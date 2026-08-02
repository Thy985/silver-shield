"""ADR-0021 Phase 1 · 实时风险 Demo 层可观测性测试。

聚焦「演示层接入」这一真正缺口（后端 Stages A–D 已由 analysis/pipeline 测试覆盖）：
bridge 是否透传 behavior_states / risk_signals → 网关是否把它们喂给聚合状态 →
聚合状态是否维护 RAISED/CLEARED 生命周期 → 场景 YAML 是否真正开启实时旁路。

全部为**单元 / 薄集成**级别，不加载 YOLO / 不读真实视频，可直接在 torch-free 环境跑。

隔离：所有场景对象用 duck-typing 的假 FrameResult（仅 to_dict），不 import 任何
home_perception 7 层内部 —— 守住 ADR-0015 冻结边界（test_freeze_boundary 同约束）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from silver_demo.bridge import frame_result_to_view
from silver_demo.gateway import DemoGateway
from silver_demo.scenarios import ScenarioConfig, load_scenario
from silver_demo.state import DemoAggregateState

REPO_ROOT = Path(__file__).resolve().parents[2]

# ----------------------------------------------------------------------
# 测试夹具：duck-typing 的假对象（只提供 to_dict，不依赖 home_perception）
# ----------------------------------------------------------------------


class _FakeToDict:
    """任意 to_dict 装载器（与 FrameResult 中 behavior_states/risk_signals 元素同契约）。"""

    def __init__(self, d: dict) -> None:
        self._d = dict(d)

    def to_dict(self) -> dict:
        return dict(self._d)


class _FakeFrameResult:
    """只暴露 bridge 读取的属性 + duck-typing to_dict 元素，不依赖真实 FrameResult 类型。"""

    def __init__(self, **kw: object) -> None:
        self.perception_events = list(kw.get("perception_events", []))  # type: ignore[arg-type]
        self.warnings = list(kw.get("warnings", []))  # type: ignore[arg-type]
        self.commands = list(kw.get("commands", []))  # type: ignore[arg-type]
        self.behavior_states = list(kw.get("behavior_states", []))  # type: ignore[arg-type]
        self.risk_signals = list(kw.get("risk_signals", []))  # type: ignore[arg-type]
        self.n_detections = int(kw.get("n_detections", 0))  # type: ignore[arg-type]
        self.n_visitor_events = int(kw.get("n_visitor_events", 0))  # type: ignore[arg-type]


def _raised(signal_id: str, **kw: object) -> dict:
    base = {
        "signal_id": signal_id,
        "subject_type": "visitor",
        "subject_id": "v1",
        "category": "HIGH_RISK_APPROACH",
        "source": "realtime_evaluator",
        "transition": "raised",
        "features": {"long_dwell": True, "repeat": True, "odd_hour": True},
        "paired_signal_id": None,
        "track_id": "t1",
        "visitor_instance_id": "vi1",
        "severity_hint": "HIGH",
        "created_at": "2026-07-19T23:40:00+00:00",
    }
    base.update(kw)
    return base


def _cleared(signal_id: str, paired_signal_id: str) -> dict:
    return {
        "signal_id": signal_id,
        "subject_type": "visitor",
        "subject_id": "v1",
        "category": "HIGH_RISK_APPROACH",
        "source": "realtime_evaluator",
        "transition": "cleared",
        "features": {},
        "paired_signal_id": paired_signal_id,
        "track_id": "t1",
        "visitor_instance_id": "vi1",
        "severity_hint": "HIGH",
        "created_at": "2026-07-19T23:41:00+00:00",
    }


# ----------------------------------------------------------------------
# 1. bridge 透传（flag 关闭 → 空；flag 开启 → 全字段）
# ----------------------------------------------------------------------


def test_bridge_includes_realtime_fields_when_empty():
    fr = _FakeFrameResult()
    view = frame_result_to_view(fr, frame_index=0, frame_base64=None, demo_time=None)
    assert view["behavior_states"] == []
    assert view["risk_signals"] == []


def test_bridge_passes_behavior_states_and_risk_signals():
    fr = _FakeFrameResult(
        behavior_states=[_FakeToDict({"track_id": "t1", "phase": "approaching"})],
        risk_signals=[_FakeToDict(_raised("sig-1"))],
    )
    view = frame_result_to_view(fr, frame_index=3, frame_base64=None, demo_time=None)
    # 元素经 to_dict 翻译为 dict
    assert view["risk_signals"] == [_raised("sig-1")]
    assert view["behavior_states"] == [{"track_id": "t1", "phase": "approaching"}]
    # 原对象未被修改（冻结边界：只读 to_dict）
    assert fr.risk_signals[0]._d["signal_id"] == "sig-1"


def test_bridge_rewrites_risk_signal_created_at_to_demo_time():
    fr = _FakeFrameResult(risk_signals=[_FakeToDict(_raised("sig-1"))])
    view = frame_result_to_view(fr, frame_index=3, frame_base64=None, demo_time="2099-01-01T00:00:00+00:00")
    assert view["risk_signals"][0]["created_at"] == "2099-01-01T00:00:00+00:00"


# ----------------------------------------------------------------------
# 2. DemoAggregateState 维护 RAISED/CLEARED 生命周期
# ----------------------------------------------------------------------


def test_aggregate_ingest_raised_then_cleared():
    agg = DemoAggregateState()
    # 亮卡
    agg.ingest([], [], [], {"family": [], "community": [], "log_only": []}, 0, 0, [_raised("sig-1")])
    snap = agg.snapshot()
    assert len(snap["risk_signals"]) == 1
    assert snap["risk_signals"][0]["signal_id"] == "sig-1"
    assert agg.meta()["active_risk_signals"] == 1

    # 熄卡：经 paired_signal_id 配对移除
    agg.ingest(
        [], [], [], {"family": [], "community": [], "log_only": []}, 1, 0,
        [_cleared("sig-1-clear", "sig-1")],
    )
    assert agg.snapshot()["risk_signals"] == []
    assert agg.meta()["active_risk_signals"] == 0


def test_aggregate_clear_resets_risk_signals():
    agg = DemoAggregateState()
    agg.ingest([], [], [], {"family": [], "community": [], "log_only": []}, 0, 0, [_raised("sig-1")])
    assert agg.meta()["active_risk_signals"] == 1
    agg.clear()
    assert agg.snapshot()["risk_signals"] == []
    assert agg.meta()["active_risk_signals"] == 0


def test_aggregate_ignores_malformed_risk_signals():
    agg = DemoAggregateState()
    agg.ingest([], [], [], {"family": [], "community": [], "log_only": []}, 0, 0, [{"no_id": True}, None, 123])
    assert agg.snapshot()["risk_signals"] == []


# ----------------------------------------------------------------------
# 3. 薄集成：bridge 输出 → gateway 喂给聚合状态（端到端语义，不跑 run_loop）
# ----------------------------------------------------------------------


def test_gateway_wiring_bridge_to_aggregate():
    """复刻 gateway.run_loop 中「view -> ingest」的接线，证明 risk_signals 一路透传。"""
    fr = _FakeFrameResult(
        perception_events=[_FakeToDict({"event_type": "high_risk_approach", "visitor_id": "v1"})],
        warnings=[],
        commands=[],
        risk_signals=[_FakeToDict(_raised("sig-9"))],
    )
    view = frame_result_to_view(fr, frame_index=7, frame_base64=None, demo_time=None)
    assert view["risk_signals"] == [_raised("sig-9")]

    agg = DemoAggregateState()
    agg.ingest(
        [],  # active_warnings
        view["perception_events"],
        view["warnings"],
        {"family": [], "community": [], "log_only": []},
        7,  # frame_index
        0,  # loop_count
        view["risk_signals"],  # 第 7 个参数 = 实时风险跃迁
    )
    assert agg.snapshot()["risk_signals"] == [_raised("sig-9")]


# ----------------------------------------------------------------------
# 4. gateway 场景级实时开关覆盖（改动 hp_settings.realtime_risk 供 from_settings 装配）
# ----------------------------------------------------------------------


def test_realtime_override_flips_settings():
    gw = DemoGateway.create_for_test()
    from home_perception.core.config import Settings
    from silver_demo.config import DemoSettings

    gw.hp_settings = Settings.load(DemoSettings.from_env().home_perception_config)
    assert gw.hp_settings.realtime_risk.enabled is False
    assert gw.hp_settings.realtime_risk.decision_enabled is False

    gw.scenario = ScenarioConfig(
        scenario_id="cctv",
        source="cctv",
        source_type="video_file",
        media_path="data/demo/CCTV_Surveillance_Final.mp4",
        start_time=datetime.now(UTC),
        realtime_risk={"enabled": True, "decision_enabled": True},
    )
    gw._apply_scenario_realtime_overrides()
    assert gw.hp_settings.realtime_risk.enabled is True
    assert gw.hp_settings.realtime_risk.decision_enabled is True


def test_realtime_override_unknown_key_is_safe():
    gw = DemoGateway.create_for_test()
    from home_perception.core.config import Settings
    from silver_demo.config import DemoSettings

    gw.hp_settings = Settings.load(DemoSettings.from_env().home_perception_config)
    gw.scenario = ScenarioConfig(
        scenario_id="cctv",
        source="cctv",
        source_type="video_file",
        media_path="data/demo/CCTV_Surveillance_Final.mp4",
        start_time=datetime.now(UTC),
        realtime_risk={"not_a_real_key": 1},
    )
    # 未知键只告警不抛异常，且不改变既有默认值
    gw._apply_scenario_realtime_overrides()
    assert gw.hp_settings.realtime_risk.enabled is False


# ----------------------------------------------------------------------
# 5. 场景 YAML 真正开启实时旁路（Task #5 落地验证）
# ----------------------------------------------------------------------


def test_cctv_scenario_enables_realtime_risk():
    p = REPO_ROOT / "config" / "demo" / "scenarios" / "cctv_surveillance_suspicious.yaml"
    sc = load_scenario(p)
    assert sc.realtime_risk == {"enabled": True, "decision_enabled": True}
    # 既有 rule_overrides 不被破坏
    assert sc.rule_overrides == {"repeat_visit_count": 2}


# ----------------------------------------------------------------------
# 6. 端到端链路：realtime 开关 → pipeline 真正装配实时组件（不 load 权重，轻量）
# ----------------------------------------------------------------------


def test_realtime_enabled_assembles_pipeline_components():
    """网关把 hp_settings.realtime_risk.enabled 置 True（场景级覆盖的等价终态）后，
    PerceptionPipeline.from_settings 必须装配 BehaviorBuilder / RecentBehaviorStore /
    RealTimeRiskEvaluator，且 _decision_enabled 随 decision_enabled 同步。

    不调 load_detector（懒加载，不触权重），仅验证装配接线 —— 与 ADR-0021 工程方案一致。
    """
    from home_perception.core.config import Settings
    from home_perception.runtime.pipeline import DemoClock, PerceptionPipeline
    from silver_demo.config import DemoSettings

    hp = Settings.load(DemoSettings.from_env().home_perception_config)
    hp.realtime_risk.enabled = True
    hp.realtime_risk.decision_enabled = True

    clock = DemoClock(start=datetime.now(UTC), interval_s=0.5)
    pipe = PerceptionPipeline.from_settings(
        hp, device_id="test", now_provider=clock, frame_interval_s=0.5
    )
    assert pipe._realtime_enabled is True
    assert pipe._realtime_evaluator is not None
    assert pipe._behavior_builder is not None
    assert pipe._recent_behavior_store is not None
    # 决策接入随 decision_enabled 同步开启（Stage D 单一决策中心）
    assert pipe._decision_enabled is True


# ----------------------------------------------------------------------
# 7. 跨场景状态泄漏回归（review 最实质回归点）
# ----------------------------------------------------------------------


def test_realtime_override_does_not_leak_across_scenarios():
    """从「开启 realtime」的场景热切到「无 realtime_risk override」的场景时，
    hp_settings.realtime_risk 必须复位为基线（enabled/decision_enabled = False）。

    否则 CCTV（enabled=true）切到 Delivery（无 override）会残留 True，导致实时旁路
    意外开启（跨场景状态泄漏）。修复点：_apply_scenario_realtime_overrides 先复位基线
    再覆盖，且 _rebuild_pipeline（switch_source 经由此）在 from_settings 前调用它。
    """
    gw = DemoGateway.create_for_test()
    from home_perception.core.config import Settings
    from silver_demo.config import DemoSettings

    gw.hp_settings = Settings.load(DemoSettings.from_env().home_perception_config)
    assert gw.hp_settings.realtime_risk.enabled is False
    assert gw.hp_settings.realtime_risk.decision_enabled is False

    # 1) CCTV 场景覆盖：开启
    gw.scenario = ScenarioConfig(
        scenario_id="cctv",
        source="cctv",
        source_type="video_file",
        media_path="data/demo/CCTV_Surveillance_Final.mp4",
        start_time=datetime.now(UTC),
        realtime_risk={"enabled": True, "decision_enabled": True},
    )
    gw._apply_scenario_realtime_overrides()
    assert gw.hp_settings.realtime_risk.enabled is True
    assert gw.hp_settings.realtime_risk.decision_enabled is True

    # 2) 热切到无 override 的场景（如 Delivery）—— 必须复位，不能残留 True
    gw.scenario = ScenarioConfig(
        scenario_id="delivery",
        source="delivery",
        source_type="video_file",
        media_path="data/demo/Delivery.mp4",
        start_time=datetime.now(UTC),
        # 注意：无 realtime_risk 字段
    )
    gw._apply_scenario_realtime_overrides()
    assert gw.hp_settings.realtime_risk.enabled is False
    assert gw.hp_settings.realtime_risk.decision_enabled is False


def test_realtime_override_whitelist_rejects_unknown_fields():
    """realtime_risk 覆盖仅接受白名单字段（enabled / decision_enabled），
    其余键（含 RealtimeRiskConfig 已有但不该经 YAML 改写的字段）必须被拒绝，
    杜绝任意字段名经 setattr 改写配置对象私有属性（review 安全风险项）。
    """
    gw = DemoGateway.create_for_test()
    from home_perception.core.config import Settings
    from silver_demo.config import DemoSettings

    gw.hp_settings = Settings.load(DemoSettings.from_env().home_perception_config)
    # eval_interval_frames 是 RealtimeRiskConfig 已有字段，但不在白名单 → 应被拒绝
    gw.scenario = ScenarioConfig(
        scenario_id="cctv",
        source="cctv",
        source_type="video_file",
        media_path="data/demo/CCTV_Surveillance_Final.mp4",
        start_time=datetime.now(UTC),
        realtime_risk={"enabled": True, "eval_interval_frames": 99},
    )
    gw._apply_scenario_realtime_overrides()
    assert gw.hp_settings.realtime_risk.enabled is True
    # 非白名单字段未被 setattr 改写（保持基线 1）
    assert gw.hp_settings.realtime_risk.eval_interval_frames == 1
