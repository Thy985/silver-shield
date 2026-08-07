"""Runtime E2E Contract Test（ADR-0028）—— 真实运行链装配验证。

> 目标：证明「Vision Runtime + Audio Runtime → MemoryHook → CrossModalLinkRuntime
> → 真实 Link」整条**运行时装配链**成立（P0 补真实运行链验证，非新增能力）。

与既有测试的分工：
- ``test_cross_modal_link.py`` / ``cross_modal_scenarios.yaml``（Slice C）：**单元级**——
  直接构造 ``EpisodicRecord`` 喂 ``CrossModalLinker``，验证关联规则本身；
- ``test_audio_session_recorder.py``：音频 → Memory 闭环落库（单模态）；
- 本文件：**E2E 级**——经 ``PerceptionPipeline.from_settings`` **真实装配**
  （``episodic_shadow`` 激活 → ``CrossModalLinkRuntime`` 注入 MemoryHook 单例；
  ``audio.enabled`` → AudioSessionRecorder 复用同一 hook），驱动：
  视觉访问（检测帧 → tracker → 事件 → 规则 → Warning → MemoryHook）+
  音频会话（``process_audio_session`` → D3 决策门槛 → 纯音频 episode），
  断言 ``CrossModalLinkStore`` 中的**真实 Link**（SUPPORTS / 时间窗 / 设备键）。

**不生成 / 不消费任何视频或音频二进制**——只驱动领域事件流
（ADR-0028 §0.2 闭环：重点验证装配，程序化视频 / 合成媒体不属于本 ADR）。

时间控制：``ManualClock`` + plan 驱动 detector（与 E2E ``SteppingStubDetector`` 同构），
少量帧 × 大步进模拟长时段访问；音频事件 ``timestamp`` 精确控制
``EvidenceItem.captured_at`` → 音频 episode 窗口，使时间重叠可确定断言。

铁律：Memory 是旁路（Shadow Mode）——开启后不改变任何主链路行为（对照用例）。

scenario 声明式驱动见 ``tests/fixtures/runtime_cross_modal_scenarios.yaml``。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from _helpers import ManualClock, drive

from home_perception.audio.event import (
    AudioPerceptionEvent,
    AudioPerceptionKind,
    new_event_id,
)
from home_perception.core.config import AudioConfig, MemoryConfig, RuleConfig, Settings
from home_perception.core.event import EvidenceModality
from home_perception.detection.detector import Detection, DetectionResult
from home_perception.runtime.pipeline import PerceptionPipeline

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "runtime_cross_modal_scenarios.yaml"
)
# 统一时钟基线：18 点（UTC）命中 OddHourRule（odd_hour_set=[18]）→ 视觉产 HIGH warning
_BASE = datetime(2026, 8, 7, 18, 0, 0, tzinfo=UTC)
_STEP_S = 30.0


# ===========================================================================
# scenario 加载
# ===========================================================================
def _load_scenarios() -> list[dict]:
    raw = yaml.safe_load(Path(_FIXTURE).read_text(encoding="utf-8"))
    scenarios = raw.get("scenarios", [])
    assert scenarios, "fixture 必须含至少一个 scenario"
    return scenarios


# ===========================================================================
# 驱动件（plan 驱动 detector + 音频事件工厂 + pipeline 装配）
# ===========================================================================
class RuntimeDetector:
    """按 plan 返回 Detection 列表；不自动推进时钟（由调用方经 drive 步进）。

    与 E2E ``SteppingStubDetector`` 同构（本文件独立持有，避免跨测试文件 import）。
    """

    def __init__(self, plan: list[list[Detection]]):
        self.plan = plan
        self.i = 0

    def detect(self, frame) -> DetectionResult:
        idx = min(self.i, len(self.plan) - 1)
        dets = self.plan[idx]
        self.i += 1
        return DetectionResult(
            detections=dets,
            timestamp=0.0,
            inference_ms=0.0,
            source_size=(1, 1),
            inference_size=(1, 1),
            model="runtime-e2e",
        )


def _person(track_id: int = 1) -> list[Detection]:
    return [
        Detection(
            class_id=0,
            class_name="person",
            confidence=0.9,
            bbox=[0, 0, 10, 10],
            timestamp=0.0,
            track_id=track_id,
        )
    ]


def _visual_plan(window: list[float]) -> list[list[Detection]]:
    """视觉访问帧序列：在场 ceil(窗口/step) 帧 + 离场空帧（触发 tracker 离场判定）。"""
    start, end = window
    n_present = max(1, math.ceil((end - start) / _STEP_S))
    return [_person(1) for _ in range(n_present)] + [[] for _ in range(2)]


def _audio_event_at(kind: str, offset_s: float) -> AudioPerceptionEvent:
    """音频事件：timestamp = 基线 + 会话内偏移（→ EvidenceItem.captured_at）。"""
    return AudioPerceptionEvent(
        event_id=new_event_id(),
        timestamp=_BASE.timestamp() + offset_s,
        kind=AudioPerceptionKind(kind),
        score=0.8,
        confidence=0.8,
        source_segment_ids=[f"seg-{int(offset_s)}"],
    )


def _build_pipeline(detector: RuntimeDetector, clock: ManualClock, device_id: str) -> PerceptionPipeline:
    """from_settings 真实装配：audio + memory 影子 + 规则阈值（命中 HIGH）。"""
    rule = RuleConfig(
        long_duration_seconds=60.0,
        repeat_visit_count=1,
        odd_hour_set=[18],
        frequency_window_s=1800.0,
    )
    settings = Settings(
        audio=AudioConfig(enabled=True),
        memory=MemoryConfig(enabled=True, episodic_shadow=True),
        rule=rule,
    )
    return PerceptionPipeline.from_settings(
        settings, detector=detector, device_id=device_id, now_provider=clock
    )


def _run_visual_then_audio(scenario: dict) -> tuple[PerceptionPipeline, object]:
    """统一驱动顺序：先视觉全帧（含离场），后收割音频会话。

    收割时刻 = 视觉驱动完成后的时钟值（≈ 视觉 leave）→ 音频 episode 窗口
    ``[min(captured_at, warning.created_at), max(...)]`` 的**左端**由
    ``audio.at`` 精确控制、**右端** ≈ 视觉 leave——正例（at 在窗口内）必然重叠、
    负例（at 在窗口外）必然不重叠，时间语义确定。
    """
    clock = ManualClock(base=_BASE)
    plan = _visual_plan(scenario["vision"]["window"])
    pipeline = _build_pipeline(RuntimeDetector(plan), clock, scenario["device_id"])
    drive(pipeline, clock, plan, _STEP_S)
    audio = scenario["audio"]
    summary = pipeline.process_audio_session(
        [_audio_event_at(audio["event"], float(audio["at"]))],
        audio_session_id=f"audio_{scenario['name']}",
    )
    return pipeline, summary


def _assert_scenario_contract(scenario: dict, pipeline: PerceptionPipeline, summary) -> None:
    """断言 expected.cross_modal_link（真实 CrossModalLinkStore 里的边）。"""
    expected = scenario["expected"]["cross_modal_link"]
    links = pipeline.cross_modal_links  # list[CrossModalLink] | None
    if expected["present"]:
        assert summary is not None, "正例：音频收割应返回摘要"
        assert summary.episode_recorded is True, "正例：音频应过 D3 门槛并落库"
        assert links is not None, "正例：from_settings 应装配 runtime（link 视图非 None）"
        assert len(links) == expected.get("n_links", 1), (
            f"正例应恰好 {expected.get('n_links', 1)} 条 link，收到 {len(links)}"
        )
        link = links[0]
        assert link.relationship.value == expected["relationship"], (
            f"relationship 应为 {expected['relationship']}，收到 {link.relationship.value}"
        )
        assert link.time_overlap is not None, "正例：时间窗应重叠（time_overlap 非 None）"
        assert len(link.episode_ids) == 2, "link 应关联恰好 2 条 episode"
    else:
        assert links is not None, "负例：runtime 已装配（视图非 None，只是没有边）"
        assert links == [], f"负例不应建边，收到 {len(links)} 条"


# ===========================================================================
# 1. 声明式 scenario 契约（parametrize yaml）
# ===========================================================================
class TestRuntimeE2EContract:
    """每个 scenario 走真实运行链，断言 CrossModalLinkStore 中的真实 Link。"""

    @pytest.mark.parametrize("scenario", _load_scenarios(), ids=lambda s: s["name"])
    def test_scenario(self, scenario: dict) -> None:
        pipeline, summary = _run_visual_then_audio(scenario)
        _assert_scenario_contract(scenario, pipeline, summary)

    def test_real_chain_full_assert(self) -> None:
        """正例端到端全断言：两路都真实落库 + 真实 Link 的语义细节。

        - 视觉 episode（modalities=[VISION], device_id=camera01）与
          音频 episode（modalities=[AUDIO], device_id=camera01）各落 1 条；
        - link.episode_ids 恰好覆盖这两条（SUPPORTS = 跨模态支撑）；
        - link 无分数语义：confidence ∈ (0, 1]，非风险分。
        """
        scenario = next(s for s in _load_scenarios() if s["name"] == "vision_fall_audio_distress_supports")
        pipeline, summary = _run_visual_then_audio(scenario)

        # 两路各落库 1 条（视觉访客 + 纯音频，D4 匿名）
        assert pipeline.metrics.episodes_recorded == 2
        assert summary is not None and summary.episode_recorded is True

        episodes = pipeline._memory_store.get_active_episodic()  # 既有 runtime 测试同款（快照语义）
        assert len(episodes) == 2
        by_modality = {tuple(rec.modalities): rec for rec in episodes}
        assert (EvidenceModality.VISION,) in by_modality, "视觉 episode 应带 VISION 模态"
        assert (EvidenceModality.AUDIO,) in by_modality, "纯音频 episode 应只带 AUDIO 模态"
        assert all(rec.device_id == "camera01" for rec in episodes), "两路 device_id 同源（部署源标识）"

        # 真实 Link：SUPPORTS + 覆盖两条 episode + 时间窗重叠 + 无风险分语义
        links = pipeline.cross_modal_links
        assert links is not None and len(links) == 1
        link = links[0]
        assert link.relationship.value == "supports"
        assert set(link.episode_ids) == {rec.record_id for rec in episodes}
        assert link.time_overlap is not None
        assert link.time_overlap[1] > link.time_overlap[0]
        assert 0.0 < link.confidence <= 1.0, "confidence 是关联强度（非风险分）"


# ===========================================================================
# 2. 装配对照（零行为变化 / D3 门槛）
# ===========================================================================
class TestRuntimeE2EControl:
    def test_shadow_disabled_no_runtime_zero_change(self) -> None:
        """对照：episodic_shadow 关闭 → from_settings 不注入 runtime。

        - ``cross_modal_links`` 为 None（未装配视图）；
        - 主链路照常：视觉访问仍产 Warning（Memory 是旁路，不改变风险行为）；
        - 无 episode 落库（影子关闭），自然无 link。
        """
        clock = ManualClock(base=_BASE)
        plan = _visual_plan([30.0, 930.0])
        settings = Settings(
            audio=AudioConfig(enabled=True),
            memory=MemoryConfig(enabled=True, episodic_shadow=False),
            rule=RuleConfig(long_duration_seconds=60.0, repeat_visit_count=1, odd_hour_set=[18]),
        )
        pipeline = PerceptionPipeline.from_settings(
            settings, detector=RuntimeDetector(plan), device_id="camera01", now_provider=clock
        )
        results = drive(pipeline, clock, plan, _STEP_S)

        assert sum(len(r.warnings) for r in results) > 0, "主链路照常产 Warning（旁路语义）"
        assert pipeline.cross_modal_links is None, "未装配 runtime：link 视图应为 None"
        assert pipeline.metrics.episodes_recorded == 0, "影子关闭：不落库"
        assert pipeline.process_audio_session(
            [_audio_event_at("audio_distress_cry", 300.0)]
        ) is None, "影子关闭：音频闭环未装配"

    def test_audio_no_warning_no_link(self) -> None:
        """对照（D3 决策门槛）：音频会话不过决策（无 WarningEvent）→ 不落库 → 无 link。

        证明 Memory 只记「系统已确认的风险事件」，不新增音频记忆孤岛；
        空会话是 D3 的最短负例（与 test_audio_session_recorder 的 D3 用例同语义，
        此处验证其对跨模态链路的影响：无音频 episode 自然无 SUPPORTS 边）。
        """
        scenario = next(s for s in _load_scenarios() if s["name"] == "vision_fall_audio_distress_supports")
        clock = ManualClock(base=_BASE)
        plan = _visual_plan(scenario["vision"]["window"])
        pipeline = _build_pipeline(RuntimeDetector(plan), clock, scenario["device_id"])
        drive(pipeline, clock, plan, _STEP_S)

        summary = pipeline.process_audio_session(
            [], audio_session_id="audio_empty_d3"
        )
        assert summary is not None
        assert summary.episode_recorded is False, "D3：无 WarningEvent 不落库"

        assert pipeline.metrics.episodes_recorded == 1, "仅视觉 episode 落库"
        links = pipeline.cross_modal_links
        assert links is not None and links == [], "无音频 episode → 无跨模态 link"
