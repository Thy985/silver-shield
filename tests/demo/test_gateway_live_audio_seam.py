"""ADR-0036 VM-13 Phase B（#510 验证）· Live 音频依赖倒置接缝测试。

验证网关通过 DI 钩子（``live_audio_builder`` 模块级 + ``set_live_audio_events`` /
``_feed_live_audio`` 实例方法）注入真实音频事件，**不 import home_perception.audio**
（守 ADR-0015 §5 冻结边界），由组装层（scripts/run_demo.py）构建 AudioPipeline 后注入。

覆盖：
- ``set_live_audio_events`` 校验（非 list/tuple → TypeError；存字典列表）。
- ``_feed_live_audio`` 按 frame_index==k 喂第 k 条音频事件（确定性、每条仅喂一次、越界不喂）。
- 摄入后 ``ProjectionAccumulator`` 投影出 REAL_SENSOR 音频节点（audio_evidence 非空）。
- fail-closed：单条音频命中 forbidden 字段 → 记日志跳过，绝不阻断、绝不抛。
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
# _feed_live_audio 确定性投递（frame_index==k → 第 k 条）
# ----------------------------------------------------------------------


def test_feed_live_audio_injects_into_accumulator():
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(1700000000.0, "audio_voice_raised", event_id="e1"),
        _audio_ev(1700000001.0, "audio_telephone_persistent", event_id="e2"),
    ])
    acc = gw._ensure_live_accumulator()
    # 模拟 run_loop：frame_index==0 喂第 0 条，frame_index==1 喂第 1 条
    gw._frame_index = 0
    gw._feed_live_audio(acc)
    gw._frame_index = 1
    gw._feed_live_audio(acc)
    scn = acc.to_evidence_projection()["scenarios"][0]
    evidence = scn["audio_evidence"]
    assert len(evidence) == 2
    assert all(n["provenance_kind"] == "REAL_SENSOR" for n in evidence)
    assert evidence[0]["event_id"] == "e1"
    assert evidence[1]["event_id"] == "e2"
    assert evidence[0]["ref"] == "live://audio/1"


def test_feed_live_audio_monotonic_loop_feeds_each_once():
    """模拟 run_loop 单调 frame_index：每条事件恰喂一次（确定性投递，无重复/遗漏）。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        _audio_ev(1700000000.0, "audio_voice_raised", event_id="e1"),
        _audio_ev(1700000001.0, "audio_telephone_persistent", event_id="e2"),
    ])
    acc = gw._ensure_live_accumulator()
    for i in range(2):
        gw._frame_index = i
        gw._feed_live_audio(acc)
    evidence = acc.to_evidence_projection()["scenarios"][0]["audio_evidence"]
    assert len(evidence) == 2
    assert evidence[0]["event_id"] == "e1"
    assert evidence[1]["event_id"] == "e2"


def test_feed_live_audio_out_of_range_safe():
    """frame_index 越界（>=事件数）时不喂、不抛；合法索引喂对应事件。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([_audio_ev(1700000000.0, "audio_voice_raised", event_id="e1")])
    acc = gw._ensure_live_accumulator()
    gw._frame_index = 0
    gw._feed_live_audio(acc)             # 喂第 0 条
    assert len(acc.to_evidence_projection()["scenarios"][0]["audio_evidence"]) == 1
    gw._frame_index = 1                  # 越界（仅 1 条事件）
    gw._feed_live_audio(acc)
    assert len(acc.to_evidence_projection()["scenarios"][0]["audio_evidence"]) == 1


def test_feed_live_audio_fail_closed_on_bad_event():
    """单条音频命中 forbidden 字段 → 记日志跳过，绝不抛（VM-5 / 探针铁律）。"""
    gw = _bare_gateway_with_scenario()
    gw.set_live_audio_events([
        {**_audio_ev(1700000000.0, "audio_voice_raised"), "verdict": "FRAUD"},
    ])
    acc = gw._ensure_live_accumulator()
    gw._frame_index = 0
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
