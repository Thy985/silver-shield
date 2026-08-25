"""ADR-0036 VM-13 Phase B（#510 验证）· Live 音频依赖倒置接缝测试。

验证网关通过 DI 钩子（``live_audio_builder`` 模块级 + ``set_live_audio_events`` /
``_feed_live_audio`` 实例方法）注入真实音频事件，**不 import home_perception.audio**
（守 ADR-0015 §5 冻结边界），由组装层（scripts/run_demo.py）构建 AudioPipeline 后注入。

覆盖（SSOT v4.0 T2 更新：投递规则从「frame_index==k → 第 k 条」演进为
「case_time 驱动 + 末帧 flush 兜底」——事件按其时间轴节奏流入，Owner
「可观察的时间过程」裁决）：
- ``set_live_audio_events`` 校验（非 list/tuple → TypeError；存字典列表；按 ts 排序）。
- ``_feed_live_audio`` 按 ``timestamp <= case_time`` 投递（确定性、每条仅喂一次、
  未到期不喂、末帧 flush 兜底）。
- 摄入后 ``ProjectionAccumulator`` 投影出 REAL_SENSOR 音频节点（audio_evidence 非空）。
- fail-closed：单条音频命中 forbidden 字段 → 记日志跳过，绝不阻断、绝不抛。
- loop 重放边界：游标/投影累积器/delta 基线重置（新一轮逐条涌现语义）。
- ``create_app`` 经 ``live_audio_builder`` 模块钩子注入（依赖倒置接缝端到端）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from silver_demo.gateway import DemoGateway, create_app
from silver_demo.scenarios import ScenarioConfig


def _bare_gateway_with_scenario() -> DemoGateway:
    """构造未装配网关 + 最小 ScenarioConfig（仅喂音频投影所需 scenario_id）。"""
    gw = DemoGateway.create_for_test()
    gw.scenario = ScenarioConfig(
        scenario_id="sess-gw-seam",
        source="s",
        start_time=datetime.now(UTC),
    )
    return gw


def _audio_ev(timestamp: float, kind: str, *, event_id: str | None = None) -> dict:
    d = {
        "timestamp": timestamp,
        "kind": kind,
        "score": 0.8,
        "confidence": 0.9,
        "source_segment_ids": ["seg-1"],
        "labels": ["raised"],
    }
    if event_id:
        d["event_id"] = event_id
    return d


# ----------------------------------------------------------------------
# set_live_audio_events 校验 + 状态
# ----------------------------------------------------------------------


def test_set_live_audio_events_validation():
    gw = _bare_gateway_with_scenario()
    with pytest.raises(TypeError):
        gw.set_live_audio_events("not-a-list")
    gw.set_live_audio_events([_audio_ev(1700000000.0, "audio_voice_raised")])
    assert len(gw._live_audio_events) == 1


# ----------------------------------------------------------------------
# _feed_live_audio 确定性投递（SSOT v4.0 T2：timestamp <= case_time → 喂入）
# ----------------------------------------------------------------------


def test_feed_live_audio_injects_into_accumulator():
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(0.5, "audio_voice_raised", event_id="e1"),
        _audio_ev(1.5, "audio_telephone_persistent", event_id="e2"),
    ])
    acc = gw._ensure_live_accumulator()
    # 模拟 run_loop：case_time 推进到 0.5 喂第 1 条，推进到 1.5 喂第 2 条
    gw._frame_index = 0
    gw._last_case_time = 0.5
    gw._feed_live_audio(acc)
    gw._frame_index = 1
    gw._last_case_time = 1.5
    gw._feed_live_audio(acc)
    scn = acc.to_evidence_projection()["scenarios"][0]
    evidence = scn["audio_evidence"]
    assert len(evidence) == 2
    assert all(n["provenance_kind"] == "REAL_SENSOR" for n in evidence)
    assert evidence[0]["event_id"] == "e1"
    assert evidence[1]["event_id"] == "e2"
    assert evidence[0]["ref"] == "live://audio/1"


def test_feed_live_audio_monotonic_loop_feeds_each_once():
    """模拟 run_loop 单调 case_time 推进：每条事件恰喂一次（确定性投递，无重复/遗漏）。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(0.5, "audio_voice_raised", event_id="e1"),
        _audio_ev(1.5, "audio_telephone_persistent", event_id="e2"),
    ])
    acc = gw._ensure_live_accumulator()
    for i in range(4):
        gw._frame_index = i
        gw._last_case_time = 0.5 * (i + 1)
        gw._feed_live_audio(acc)
        # 同一 case_time 重复调用（同帧多次 diff）也不得重复喂
        gw._feed_live_audio(acc)
    evidence = acc.to_evidence_projection()["scenarios"][0]["audio_evidence"]
    assert len(evidence) == 2
    assert evidence[0]["event_id"] == "e1"
    assert evidence[1]["event_id"] == "e2"


def test_feed_live_audio_waits_for_event_time():
    """未到期事件不得提前喂入（时间轴节奏语义：case_time 未达 timestamp 不投递）。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(10.0, "audio_voice_raised", event_id="late"),
    ])
    acc = gw._ensure_live_accumulator()
    for i in range(8):
        gw._frame_index = i
        gw._last_case_time = 1.0 * (i + 1)  # 推进到 8s，仍 < 10s
        gw._feed_live_audio(acc)
    assert acc.to_evidence_projection()["scenarios"][0]["audio_evidence"] == ()
    gw._last_case_time = 10.0  # 到期
    gw._feed_live_audio(acc)
    evidence = acc.to_evidence_projection()["scenarios"][0]["audio_evidence"]
    assert len(evidence) == 1 and evidence[0]["event_id"] == "late"


def test_feed_live_audio_final_frame_flushes_remaining():
    """场景末帧兜底：未到期剩余事件在末帧全部投出（AU-08 全量可见契约兜底）。"""
    gw = _bare_gateway_with_scenario()
    gw.n_frames = 4
    gw.set_live_audio_events([
        _audio_ev(100.0, "audio_voice_raised", event_id="beyond"),
    ])
    acc = gw._ensure_live_accumulator()
    gw._frame_index = 3  # 末帧（n_frames-1）
    gw._last_case_time = 0.5  # 远小于 ts=100
    gw._feed_live_audio(acc)
    evidence = acc.to_evidence_projection()["scenarios"][0]["audio_evidence"]
    assert len(evidence) == 1 and evidence[0]["event_id"] == "beyond"


def test_set_live_audio_events_sorts_by_timestamp():
    """注入列表按 timestamp 稳定排序（时间轴投递要求有序流）。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(9.0, "audio_speech_rapid", event_id="b"),
        _audio_ev(1.0, "audio_voice_raised", event_id="a"),
    ])
    ids = [
        (e.to_dict() if hasattr(e, "to_dict") else e)["event_id"]
        for e in gw._live_audio_events
    ]
    assert ids == ["a", "b"]


def test_set_live_audio_events_normalizes_epoch_timeline():
    """epoch 绝对秒流归一化为首事件原点（synthetic_replay fixture 兼容 · SSOT v4.0）。

    fixture 携带 epoch 秒（如 1756036800.0）时与 case_time（相对秒）永不相交，
    事件将积压到末帧 flush——归一化后按原间隔相对节奏投递；事件数据本身不被改动。
    """
    gw = _bare_gateway_with_scenario()
    base = 1_756_036_800.0
    gw.set_live_audio_events([
        _audio_ev(base, "audio_telephone_persistent", event_id="e1"),
        _audio_ev(base + 4.0, "audio_voice_raised", event_id="e2"),
    ])
    assert gw._audio_feed_timeline == [0.0, 4.0]
    # 事件数据保真：timestamp 原值不动（VM-9）
    assert gw._live_audio_events[0]["timestamp"] == base


def test_epoch_normalized_stream_feeds_by_case_time():
    """epoch 归一化流在 case_time 推进下正常逐条投递。"""
    gw = _bare_gateway_with_scenario()
    base = 1_756_036_800.0
    gw.set_live_audio_events([
        _audio_ev(base, "audio_telephone_persistent", event_id="e1"),
        _audio_ev(base + 2.5, "audio_speech_rapid", event_id="e2"),
    ])
    acc = gw._ensure_live_accumulator()
    gw._frame_index = 0
    gw._last_case_time = 1.0
    gw._feed_live_audio(acc)
    scn = acc.to_evidence_projection()["scenarios"][0]
    assert [n["event_id"] for n in scn["audio_evidence"]] == ["e1"]
    gw._frame_index = 1
    gw._last_case_time = 3.0
    gw._feed_live_audio(acc)
    scn = acc.to_evidence_projection()["scenarios"][0]
    assert [n["event_id"] for n in scn["audio_evidence"]] == ["e1", "e2"]


def test_feed_live_audio_out_of_range_safe():
    """游标耗尽后不喂、不抛；到期事件喂对应内容。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([_audio_ev(0.5, "audio_voice_raised", event_id="e1")])
    acc = gw._ensure_live_accumulator()
    gw._frame_index = 0
    gw._last_case_time = 0.5
    gw._feed_live_audio(acc)             # 喂第 1 条
    assert len(acc.to_evidence_projection()["scenarios"][0]["audio_evidence"]) == 1
    gw._frame_index = 1                  # 游标已耗尽（cursor==len）
    gw._last_case_time = 99.0
    gw._feed_live_audio(acc)
    assert len(acc.to_evidence_projection()["scenarios"][0]["audio_evidence"]) == 1


def test_feed_live_audio_fail_closed_on_bad_event():
    """单条音频命中 forbidden 字段 → 记日志跳过，绝不抛（VM-5 / 探针铁律）。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        {**_audio_ev(0.5, "audio_voice_raised"), "verdict": "FRAUD"},
    ])
    acc = gw._ensure_live_accumulator()
    gw._frame_index = 0
    gw._last_case_time = 1.0
    gw._feed_live_audio(acc)             # 不得抛异常
    assert acc.to_evidence_projection()["scenarios"][0]["audio_evidence"] == ()


# ----------------------------------------------------------------------
# create_app 经 live_audio_builder 模块钩子注入（依赖倒置接缝端到端）
# ----------------------------------------------------------------------


def test_create_app_injects_live_audio_via_builder(monkeypatch):
    """create_app 调 live_audio_builder(hp_settings, scenario) → 注入网关 _live_audio_events。"""
    import silver_demo.gateway as gw_mod

    captured: dict = {}

    def fake_builder(hp_settings, scenario):
        captured["scenario_id"] = getattr(scenario, "scenario_id", None)
        return [_audio_ev(1700000000.0, "audio_voice_raised", event_id="e1")]

    monkeypatch.setattr(gw_mod, "live_audio_builder", fake_builder)
    # create_app 在模块顶层已 ``from .scenarios import load_scenario``，故需打桩
    # ``silver_demo.gateway.load_scenario``（而非 scenarios 模块本身）以隔离文件 IO。
    monkeypatch.setattr(
        gw_mod, "load_scenario",
        lambda p: ScenarioConfig(
            scenario_id="sess-builder", source="s", start_time=datetime.now(UTC),
        ),
    )

    from silver_demo.config import DemoSettings
    settings = DemoSettings.from_env()
    app = create_app(settings)
    gw = app.state.gateway
    assert len(gw._live_audio_events) == 1
    assert gw._live_audio_events[0]["kind"] == "audio_voice_raised"
    assert gw._live_audio_events[0]["event_id"] == "e1"
    assert captured["scenario_id"] == "sess-builder"


# ----------------------------------------------------------------------
# ADR-0042 运行时接线：_runtime_audio_events（Runtime 判定通道，
# 与 _feed_live_audio 投影通道并行，同一确定性投递规则）
# ----------------------------------------------------------------------


def _pipeline_with_settings():
    """最小真实 pipeline（from_settings 仅装配组件，音频通道不触发 detect）。"""
    from home_perception.core.config import Settings
    from home_perception.runtime import PerceptionPipeline

    class _Det:
        pass

    return PerceptionPipeline.from_settings(Settings(), detector=_Det())


def test_runtime_audio_events_deterministic_delivery():
    """frame_index==k → 第 k 条；dict 经 pipeline.adapt_runtime_audio 转换为实例元组。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(1700000000.0, "audio_voice_raised", event_id="e1"),
        _audio_ev(1700000001.0, "audio_telephone_persistent", event_id="e2"),
    ])
    gw.pipeline = _pipeline_with_settings()
    out0 = gw._runtime_audio_events(0)
    out1 = gw._runtime_audio_events(1)
    assert [e.event_id for e in out0] == ["e1"]
    assert [e.event_id for e in out1] == ["e2"]
    # 转换产物为 AudioPerceptionEvent 实例（duck 断言，守冻结边界不做类型 import 断言）
    assert all(hasattr(e, "kind") and hasattr(e, "score") for e in (*out0, *out1))


def test_runtime_audio_events_edge_cases_safe():
    """负索引 / 越界 / pipeline 未装配 → 空元组（不抛、失败隔离）。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([_audio_ev(1700000000.0, "audio_voice_raised", event_id="e1")])
    assert gw._runtime_audio_events(-1) == ()
    assert gw._runtime_audio_events(5) == ()
    gw.pipeline = None
    assert gw._runtime_audio_events(0) == ()
    gw.pipeline = _pipeline_with_settings()
    assert gw._runtime_audio_events(0)[0].event_id == "e1"


def test_runtime_audio_events_fail_closed_on_bad_event():
    """非法事件（forbidden 字段）→ 转换失败返回空元组（绝不抛、不阻断帧循环）。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        {**_audio_ev(1700000000.0, "audio_voice_raised"), "verdict": "FRAUD"},
    ])
    gw.pipeline = _pipeline_with_settings()
    assert gw._runtime_audio_events(0) == ()


# ----------------------------------------------------------------------
# SSOT v4.0 T2 fix 回归：switch_source / reset 后音频游标重置
# （根因：switch_source 重置 _live_accumulator=None 但未重置 _audio_feed_cursor
#  → 新 accumulator 永远收不到音频事件，D0 AU-10 reset 后 audio-table 无行涌现）
# ----------------------------------------------------------------------


def test_switch_source_resets_audio_feed_cursor():
    """switch_source / reset 后 _audio_feed_cursor 必须归零，否则新 accumulator 收不到音频。

    回归场景：首轮所有事件投递完毕（cursor==n），switch_source 重置 accumulator
    但未重置 cursor → _feed_live_audio 检查 cursor>=n 直接 return → 新 accumulator
    的 audio_evidence 恒空。修复后 cursor 同步归零，事件重新逐条投递。
    """
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(0.5, "audio_telephone_persistent", event_id="e1"),
        _audio_ev(1.5, "audio_voice_raised", event_id="e2"),
    ])
    # 首轮：投递所有事件
    acc1 = gw._ensure_live_accumulator()
    for i in range(4):
        gw._frame_index = i
        gw._last_case_time = 0.5 * (i + 1)
        gw._feed_live_audio(acc1)
    assert len(acc1.to_evidence_projection()["scenarios"][0]["audio_evidence"]) == 2
    assert gw._audio_feed_cursor == 2  # 全部投完

    # switch_source 重置（模拟：accumulator=None + cursor=0）
    gw._live_accumulator = None
    gw._audio_feed_cursor = 0  # 修复行：缺此行即复现 bug

    # 新轮：事件应重新投递
    acc2 = gw._ensure_live_accumulator()
    assert acc2 is not acc1  # 新 accumulator
    for i in range(4):
        gw._frame_index = i
        gw._last_case_time = 0.5 * (i + 1)
        gw._feed_live_audio(acc2)
    evidence = acc2.to_evidence_projection()["scenarios"][0]["audio_evidence"]
    assert len(evidence) == 2, (
        f"switch_source 后新 accumulator 应收到全部音频事件，实际 {len(evidence)} 条"
        "（_audio_feed_cursor 未随 accumulator 重置即复现 bug）"
    )
    assert [e["event_id"] for e in evidence] == ["e1", "e2"]


def test_switch_source_without_cursor_reset_reproduces_bug():
    """反向验证：不重置 cursor 时新 accumulator 收不到音频（bug 复现锚点）。

    此测试断言 bug 行为，确保修复（cursor 归零）是必要的——若有人误删
    switch_source 中的 _audio_feed_cursor=0，此测试会 FAIL 提醒恢复。
    """
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(0.5, "audio_telephone_persistent", event_id="e1"),
    ])
    acc1 = gw._ensure_live_accumulator()
    gw._frame_index = 0
    gw._last_case_time = 1.0
    gw._feed_live_audio(acc1)
    assert len(acc1.to_evidence_projection()["scenarios"][0]["audio_evidence"]) == 1
    assert gw._audio_feed_cursor == 1

    # 模拟 bug：只重置 accumulator，不重置 cursor
    gw._live_accumulator = None
    # 故意不重置 _audio_feed_cursor（bug 行为）

    acc2 = gw._ensure_live_accumulator()
    gw._frame_index = 0
    gw._last_case_time = 1.0
    gw._feed_live_audio(acc2)
    # bug 行为：新 accumulator 收不到任何音频
    assert len(acc2.to_evidence_projection()["scenarios"][0]["audio_evidence"]) == 0
