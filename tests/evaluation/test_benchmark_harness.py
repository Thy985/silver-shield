"""ADR-0033 Phase 1 契约测试：harness 编排 + 指纹归因 + 零行为变化 + 脱敏。

T1 确定性（同输入同代码 → 报告逐字节一致）
T2 零行为变化（evaluation 不进入 demo/gateway/runtime，仅 D3 叶子接缝）
T3 复用不重写（harness 委派 ADR-0032 三组件，自身不 import generator/renderer）
T4 指纹归因（缺字段 fail-closed；任一成分变更即改变指纹）
T5 脱敏（落盘前经 ADR-0031 assert_desensitized 守卫，无原始媒体/PII）

变异测试：M1（改 code_version → 指纹必变）、M2（断言非永真）。
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from home_perception.action.dispatcher import ActionDispatcher, DispatcherConfig
from home_perception.action.executor import ActionExecutor
from home_perception.action.notifier import MockNotifier
from home_perception.action.publisher import MockPublisher
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.decision_sink import assert_desensitized
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.rule_engine import RuleEngine
from home_perception.detection.tracker import VisitorTracker
from home_perception.evaluation.harness import (
    BenchmarkHarness,
    BenchmarkProvenanceError,
    compute_harness_fingerprint,
    default_model_fingerprint,
)
from home_perception.runtime.pipeline import PerceptionPipeline
from home_perception.validation import (
    ScenarioCompiler,
    ScenarioRunner,
    ScenarioValidator,
    load_scenario,
)
from home_perception.validation.scenario.compiler import SyntheticInput

FIX = (
    pathlib.Path(__import__("home_perception.validation", fromlist=["__file__"]).__file__).parent
    / "fixtures"
    / "scenarios"
)

START = datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC)

# 固定指纹成分，隔离环境噪声，专测 harness 自身确定性
_FIXED = {
    "code_version": "deadbeef",
    "policy_fingerprint": "pol:rule-based:v1",
    "model_fingerprint": default_model_fingerprint("detections"),
    "runtime_dependencies": {"numpy": "2.4.2", "opencv": "4.13.0", "torch": "n/a"},
}


class _Clock:
    """可推进的确定性时钟（与 ADR-0032 端到端测试同范式）。"""

    def __init__(self, start: datetime, interval_s: float = 0.5):
        self._t = start
        self.interval_s = interval_s

    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self._t

    def tick(self, dt: float | None = None) -> None:
        self._t = self._t + timedelta(seconds=dt if dt is not None else self.interval_s)


def _build_pipeline(synth: SyntheticInput) -> PerceptionPipeline:
    """torch-free pipeline（每个场景用其专属 detector），镜像 ADR-0032 端到端装配。"""
    clock = _Clock(START)
    tracker = VisitorTracker(now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="scenario", now_provider=clock)
    feature_extractor = FeatureExtractor(frequency_window_s=60.0)
    rule_engine = RuleEngine(device_id="home_entry_01", location="入户门", now_provider=clock)
    decision_engine = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(), now_provider=clock
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    publisher = MockPublisher()
    notifier = MockNotifier()
    executor = ActionExecutor(dispatcher, publisher, notifier, max_retries=1)
    return PerceptionPipeline(
        detector=synth.detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=feature_extractor,
        rule_engine=rule_engine,
        decision_engine=decision_engine,
        executor=executor,
        now_provider=clock,
        frame_interval_s=0.5,
    )


def _load_benchmark_scenarios():
    out = []
    for p in sorted((FIX / "benchmark").glob("*.yaml")):
        out.append(load_scenario(p))
    return out


# ============================================================================
# T1 确定性：同输入同代码 → 报告逐字节一致
# ============================================================================


def test_adr0033_t1_deterministic_report():
    scenarios = _load_benchmark_scenarios()
    harness = BenchmarkHarness()

    r1 = harness.run(
        scenarios,
        _build_pipeline,
        scenario_set_id="adr0033-phase1",
        **_FIXED,
    )
    r2 = harness.run(
        scenarios,
        _build_pipeline,
        scenario_set_id="adr0033-phase1",
        **_FIXED,
    )

    # 指纹逐字节一致（M2 非永真：固定成分下确定性是可验证的强约束）
    assert r1.harness_fingerprint == r2.harness_fingerprint
    # canonical JSON 逐字节一致（canonical 剔除 generated_at，可按场景序重排后比对）
    assert json.dumps(r1.canonical_dict(), sort_keys=True) == json.dumps(
        r2.canonical_dict(), sort_keys=True
    )
    # 离散指标聚合稳定
    assert (r1.tp, r1.tn, r1.fn, r1.fp) == (r2.tp, r2.tn, r2.fn, r2.fp)


# ============================================================================
# T2 零行为变化：evaluation 不进入 demo/gateway/runtime（仅 D3 叶子接缝允许）
# ============================================================================


def test_adr0033_t2_evaluation_not_wired_into_production():
    root = pathlib.Path(__file__).resolve().parents[2]
    # 允许：evaluation 自身 + validation/scenario/scenario.py（D3 叶子接缝）
    #       + scripts/run_benchmark.py（手动入口，非运行时生产路径）
    allowed_suffixes = (
        "src/home_perception/evaluation/",
        "src/home_perception/validation/scenario/scenario.py",
        "scripts/run_benchmark.py",
    )
    needle = (
        "home_perception.evaluation",
        "home_perception import evaluation",
        "from home_perception.evaluation",
    )
    bad = []
    for p in (root / "src").rglob("*.py"):
        rel = str(p.relative_to(root)).replace("\\", "/")
        if any(rel.startswith(s) for s in allowed_suffixes):
            continue
        text = p.read_text(encoding="utf-8")
        if any(n in text for n in needle):
            bad.append(rel)
    assert bad == [], f"evaluation 泄漏到生产模块（违反 ADR-0033 D8）：{bad}"


# ============================================================================
# T3 复用不重写：harness 委派 ADR-0032 三组件，自身不 import generator/renderer
# ============================================================================


def test_adr0033_t3_reuses_adr0032_components():
    import home_perception.evaluation.harness as hmod

    src = pathlib.Path(hmod.__file__).read_text(encoding="utf-8")
    # 不得重写 generator / renderer（复用 ADR-0032 既有产物）
    assert "render_frames" not in src
    assert "scenario_generator" not in src
    assert "generator.render" not in src

    # 委派：注入 fake 组件，断言 run 真正调用它们而非自带实现
    calls = {"compile": 0, "run": 0, "validate": 0}

    class _FakeCompiler:
        def compile(self, scenario, mode=None):
            calls["compile"] += 1
            return ScenarioCompiler().compile(scenario, mode)

    class _FakeRunner:
        def run(self, synth, pipeline, frame_interval_s=0.5):
            calls["run"] += 1
            return ScenarioRunner().run(synth, pipeline, frame_interval_s=frame_interval_s)

    class _FakeValidator:
        def validate(self, run_result, scenario):
            calls["validate"] += 1
            return ScenarioValidator().validate(run_result, scenario)

    scenarios = _load_benchmark_scenarios()
    harness = BenchmarkHarness(
        compiler=_FakeCompiler(), runner=_FakeRunner(), validator=_FakeValidator()
    )
    harness.run(scenarios, _build_pipeline, scenario_set_id="adr0033-phase1", **_FIXED)

    # run 内对代表场景额外预编译一次以提取 generator_fingerprint → compile 调用 N+1 次
    assert calls["compile"] == len(scenarios) + 1
    assert calls["run"] == len(scenarios)
    assert calls["validate"] == len(scenarios)


# ============================================================================
# T4 指纹归因：缺字段 fail-closed；任一成分变更即改变指纹（M1 变异）
# ============================================================================


def test_adr0033_t4_fingerprint_missing_field_fails_closed():
    base = {
        "scenario_set_id": "s",
        "code_version": "v",
        "generator_fingerprint": "g",
        "policy_fingerprint": "p",
        "model_fingerprint": default_model_fingerprint("detections"),
        "runtime_dependencies": {"numpy": "1"},
    }
    # 每个候选字段缺省/空都须 fail-closed
    for key in list(base.keys()):
        bad = dict(base)
        bad[key] = ""  # 空字符串视为缺失
        with pytest.raises(BenchmarkProvenanceError):
            compute_harness_fingerprint(**bad)


def test_adr0033_t4_fingerprint_attribution_sensitivity():
    base = {
        "scenario_set_id": "s",
        "code_version": "v",
        "generator_fingerprint": "g",
        "policy_fingerprint": "p",
        "model_fingerprint": default_model_fingerprint("detections"),
        "runtime_dependencies": {"numpy": "1"},
    }
    ref = compute_harness_fingerprint(**base)

    # M1 变异：变更任一成分 → 指纹必须改变（证明归因真实而非常量）
    mutations = {
        "code_version": "v2",
        "generator_fingerprint": "g2",
        "policy_fingerprint": "p2",
        "scenario_set_id": "s2",
        "runtime_dependencies": {"numpy": "2"},
    }
    for field, val in mutations.items():
        mutated = dict(base)
        mutated[field] = val
        assert compute_harness_fingerprint(**mutated) != ref, (
            f"指纹对 {field} 变更无响应（归因失效）"
        )

    # 完全相同输入 → 相同指纹（与 M1 配对，证明非随机）
    assert compute_harness_fingerprint(**base) == ref


# ============================================================================
# T5 脱敏：落盘前经 ADR-0031 assert_desensitized 守卫
# ============================================================================


def test_adr0033_t5_report_desensitized():
    scenarios = _load_benchmark_scenarios()
    harness = BenchmarkHarness()
    report = harness.run(
        scenarios,
        _build_pipeline,
        scenario_set_id="adr0033-phase1",
        **_FIXED,
    )

    # 复用 ADR-0031 脱敏守卫（子串扫描敏感键 + bytes fail-closed）
    assert_desensitized(report.to_dict())
    assert_desensitized(report.canonical_dict())

    # 报告不得携带原始媒体/路径痕迹
    blob = json.dumps(report.to_dict(), ensure_ascii=False).lower()
    for denied in ("bgr", "uint8", ".mp4", ".png", "frame_data", "base64"):
        assert denied not in blob, f"报告含原始媒体痕迹 {denied!r}"

    # 逐场景 score 不得含 media 通道
    for sc in report.scores:
        assert "frames" not in sc.to_dict()
