"""ADR-0034 Phase A 闭环集成验证 —— 契约测试（t1–t13）。

> 本文件只验证**接线 / 契约 / 失败归类 / 脱敏 / 确定性**，不引入任何新的决策或感知行为
> （零行为变化铁律）。每个用例对应 ADR-0034 实现计划 §4 的测试表，命名 `test_tNN_*`。

运行环境：闭环需要 cv2（依赖 `home_perception.integration.loop.runner._assemble` 的
运行时重链），因此本文件须在装有 cv2 的解释器下跑（如 `ss_home` 环境）；ruff 仍跑在
托管 venv（py3.13）。

依赖延迟导入：仅在用例内部 import 运行时 / 验证链，避免加载即拉起重链。
"""

from __future__ import annotations

import importlib.util
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from home_perception.action.sink import InMemoryActionRecorder

REPO_ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = (
    REPO_ROOT
    / "src"
    / "home_perception"
    / "validation"
    / "fixtures"
    / "scenarios"
    / "integration"
)
ALARM_PATH = INTEGRATION_DIR / "adr0034_alarm.yaml"
BENIGN_PATH = INTEGRATION_DIR / "adr0034_benign.yaml"


# ============================================================================
# 助手：加载 AST 契约助手（与 tests/validation 现有用例同款，避免 sys.path 污染）
# ============================================================================


def _load_ast_contract():
    """按文件路径加载 ``tests/validation/_ast_contract.py`` 的助手函数。"""
    path = REPO_ROOT / "tests" / "validation" / "_ast_contract.py"
    spec = importlib.util.spec_from_file_location("_ast_contract", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================================
# 助手：跑通最小闭环
# ============================================================================


def _run(path: Path):
    """加载 fixture → IntegrationRunner.run → IntegrationValidator.validate。"""
    from home_perception.integration.loop.context import IntegrationRunnerConfig
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(path)
    runner = IntegrationRunner(config=IntegrationRunnerConfig())
    result = runner.run(scn)
    validation = IntegrationValidator().validate(result, scn)
    return scn, result, validation


def _run_with_sink(path: Path, sink):
    """注入自定义 ``action_sink`` 后跑通闭环（用于故障注入 / 隔离测试）。"""
    from home_perception.integration.loop.context import (
        IntegrationContext,
        IntegrationRunnerConfig,
    )
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(path)
    runner = IntegrationRunner(config=IntegrationRunnerConfig())
    ctx = IntegrationContext.build(IntegrationRunnerConfig())
    ctx = replace(ctx, action_sink=sink)
    result = runner.run(scn, context=ctx)
    validation = IntegrationValidator().validate(result, scn)
    return scn, result, validation


# ============================================================================
# 辅助探针（故障注入 / 隔离）
# ============================================================================


class _FaultySink:
    """记录时**抛异常**的探针（模拟观测接缝损坏；生产必须吞掉）。"""

    def record(self, command) -> None:
        raise RuntimeError("probe intentionally broken")

    def flush(self) -> None:
        return None

    def commands(self):
        return ()


class _DroppingSink:
    """静默丢弃命令的探针（模拟 ActionSink Drop；用于触发 F6 通道 A 失配）。"""

    def record(self, command) -> None:
        return None

    def flush(self) -> None:
        return None

    def commands(self):
        return ()


def _build_executor(sink):
    """构造一个独立的 ``ActionExecutor``（D3 探针注入点，用于 t4 零行为变化）。"""
    from home_perception.action.dispatcher import ActionDispatcher, DispatcherConfig
    from home_perception.action.executor import ActionExecutor
    from home_perception.action.notifier import MockNotifier
    from home_perception.action.publisher import MockPublisher

    return ActionExecutor(
        dispatcher=ActionDispatcher(DispatcherConfig()),
        publisher=MockPublisher(),
        notifier=MockNotifier(),
        max_retries=1,
        sink=sink,
    )


# ============================================================================
# t1 · 确定性（同 seed 两次运行逐字节一致）
# ============================================================================


def test_t1_canonical_determinism(tmp_path):
    from home_perception.integration.loop.report import IntegrationReport

    for path in (ALARM_PATH, BENIGN_PATH):
        rep1 = IntegrationReport.build(*_run(path)[1:])
        rep2 = IntegrationReport.build(*_run(path)[1:])
        p1 = tmp_path / f"{path.stem}.1.json"
        p2 = tmp_path / f"{path.stem}.2.json"
        rep1.write_canonical_report(p1)
        rep2.write_canonical_report(p2)
        assert p1.read_bytes() == p2.read_bytes(), f"canonical 报告应对 {path.name} 确定性"


# ============================================================================
# t2 · 未接线（T2 边界：生产 src 不得反向依赖 integration.loop 评估包）
# ============================================================================


def test_t2_production_does_not_import_loop_package():
    ast = _load_ast_contract()

    target = "home_perception.integration.loop"
    src_root = REPO_ROOT / "src" / "home_perception"
    importers: list[Path] = []
    for py in src_root.rglob("*.py"):
        if "integration/loop" in str(py).replace("\\", "/"):
            continue  # 评估包自身引用自身合法
        src_text = py.read_text(encoding="utf-8")
        mods = ast.imported_modules(src_text)
        if any(m == target or m.startswith(target + ".") for m in mods):
            importers.append(py)

    for p in importers:
        rel = p.relative_to(REPO_ROOT).as_posix()
        assert rel.startswith(("scripts/", "tests/")), (
            f"生产模块 {rel} 不应导入 {target}（违反 T2 边界）"
        )


# ============================================================================
# t3 · 复用（F1 复用 build_scenario_score，不重写感知判据）
# ============================================================================


def test_t3_reuses_scenario_score():
    from home_perception.integration.loop.report import IntegrationReport

    _, result, validation = _run(ALARM_PATH)
    assert validation.score is not None
    assert validation.score.validation_ok is True
    perception = next(s for s in validation.stages if s.name == "perception")
    assert perception.passed is True  # F1 由 score.validation_ok 驱动

    rep = IntegrationReport.build(result, validation)
    # 报告直接复用 score.to_dict()，不另起炉灶
    assert rep.perception_score == validation.score.to_dict()


# ============================================================================
# t4 · 零行为变化 + 失败隔离（D3 探针注入点）
# ============================================================================


def _sample_warning():
    """构造一个 CREATED 态、会派发 ``LOG_ONLY`` 命令的告警（MONITOR → LOG_ONLY）。"""
    from home_perception.analysis.warning import WarningEvent

    return WarningEvent(
        elder_id="elder_001",
        device_id="home_entry_01",
        risk_level="LOW",
        recommended_action="MONITOR",
        trigger_events=[{"event_type": "abnormal_dwell", "risk_level": "LOW"}],
        reason_summary=["凌晨长停留"],
    )


def test_t4a_sink_injection_zero_behavior_change():
    """``ActionExecutor(sink=None)`` 与注入 ``InMemoryActionRecorder`` 的派发结果逐字一致。"""
    # 必须用**两个独立**的 CREATED 态告警：首个 executor 会把告警翻到 CONFIRMED，
    # 复用同一对象会让第二个 executor 撞上 CONFIRMED→PENDING 非法翻转。
    w_none = _sample_warning()
    w_rec = _sample_warning()
    ex_none = _build_executor(None)
    ex_rec = _build_executor(InMemoryActionRecorder())

    cmds_none = ex_none.execute(w_none)
    cmds_rec = ex_rec.execute(w_rec)

    # 探针注入绝不能改变"派发了哪些命令"（类型集合与数量）；UUID 逐次不同故不比 id
    assert {c.command_type for c in cmds_none} == {c.command_type for c in cmds_rec}
    assert len(cmds_none) == len(cmds_rec)


def test_t4b_sink_failure_isolation():
    """探针抛异常时，生产派发不受影响、``execute`` 不得外抛。"""
    w = _sample_warning()
    ex = _build_executor(_FaultySink())
    cmds = ex.execute(w)  # 必须不抛
    assert len(cmds) >= 1  # 生产命令照常下发


# ============================================================================
# t5 · 脱敏（落盘守卫 + 键名硬约束）
# ============================================================================


def test_t5_report_serialization_avoid_forbidden_keys():
    from home_perception.integration.loop.report import IntegrationReport

    _, result, validation = _run(ALARM_PATH)
    rep = IntegrationReport.build(result, validation)
    d = rep.to_dict()

    # ScenarioScore 必须挂在 perception_score 键下，绝不能出现裸 "score"
    assert "score" not in d
    assert "perception_score" in d
    assert isinstance(d["perception_score"], dict)
    # stage 序列化为**列表**，stage 名作为值而非 dict 键（"decision" 作键会被守卫拒绝）
    assert "decision" not in d
    assert isinstance(d["stages"], list)
    assert all(isinstance(s, dict) and "name" in s for s in d["stages"])
    # canonical 必须剔除 detail（失败分支内嵌 uuid4）
    cd = rep.canonical_dict()
    assert all("detail" not in s for s in cd["stages"])


def test_t5_write_refuses_undesensitized_provenance(tmp_path):
    from home_perception.analysis.decision_sink import DesensitizationError
    from home_perception.integration.loop.report import IntegrationReport

    _, result, validation = _run(ALARM_PATH)

    # 脱敏守卫活在**完整** ``to_dict``（``write_report``）上：``canonical_dict`` 刻意剔除
    # provenance（可复现优先），故守卫只在 full report 扫描 provenance 的禁止键。
    bad = IntegrationReport.build(result, validation, provenance={"risk_score": 0.9})
    with pytest.raises(DesensitizationError):
        bad.write_report(tmp_path / "bad1.json")

    bad2 = IntegrationReport.build(result, validation, provenance={"decision": "x"})
    with pytest.raises(DesensitizationError):
        bad2.write_report(tmp_path / "bad2.json")

    # 合法 provenance（无禁止键）应当允许落盘（full report 也通过守卫）
    good = IntegrationReport.build(result, validation, provenance={"code_version": "abc"})
    good.write_report(tmp_path / "ok.json")
    assert (tmp_path / "ok.json").exists()


# ============================================================================
# t6 · 最小闭环（告警真发生 / 良性不误发）
# ============================================================================


def test_t6_alarm_minimal_closed_loop():
    _, result, validation = _run(ALARM_PATH)
    assert validation.ok is True, str(validation)
    assert validation.failure_codes() == ()
    # 闭环确实贯通：有告警、有命令、有落库
    assert result.warnings
    assert result.commands
    assert result.episodes


def test_t6_benign_no_false_trigger():
    _, result, validation = _run(BENIGN_PATH)
    assert validation.ok is True, str(validation)
    assert validation.failure_codes() == ()
    # 良性：上游无事件 → 不告警、不派发（"不误发"用例）
    assert not result.warnings
    assert not result.commands


# ============================================================================
# t11 · 签名冻结（run / validate 不得接收已装配 pipeline 等形参）
# ============================================================================


def test_t11_run_signature_frozen():
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator

    params = inspect.signature(IntegrationRunner.run).parameters
    assert set(params) - {"self"} == {"scenario", "context"}
    for forbidden in ("pipeline", "recorder", "store", "memory_store"):
        assert forbidden not in params, f"run() 不得含形参 {forbidden}"

    vparams = inspect.signature(IntegrationValidator.validate).parameters
    assert set(vparams) - {"self"} == {"result", "scenario"}


# ============================================================================
# t12 · 失败归类（F1–F6 映射 + 顺序 + 行为落地）
# ============================================================================


def test_t12_classify_failure_mapping():
    from home_perception.integration.loop.validator import classify_failure

    mapping = {
        "perception": "F1",
        "decision": "F2",
        "notification": "F3",
        "memory": "F4",
        "cross_modal": "F5",
        "observability": "F6",
    }
    for stage, code in mapping.items():
        assert classify_failure(stage) == code
    # 未知 stage 兜底 F6（fail-closed，绝不崩）
    assert classify_failure("completely_unknown") == "F6"


def test_t12_failure_codes_ordered_and_behavioral():
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import (
        FAILURE_CODES,
        IntegrationValidator,
    )
    from home_perception.validation.contracts import (
        ActionExpectation,
        DecisionExpectation,
        IntegrationExpectationSuite,
        MemoryExpectation,
        PerceptionExpectation,
    )

    scn, _, _ = _run(BENIGN_PATH)
    # 良性场景塞入必败期望，制造 F1/F2/F3/F4 多失败
    suite = IntegrationExpectationSuite(
        perception=PerceptionExpectation(min_perception_events=1),  # 良性 0 事件 → F1
        decision=DecisionExpectation(outcome="WARN"),  # 无 WARN trace → F2
        action=ActionExpectation(
            expected_notification=True, expected_command_types=["LOG_ONLY"]
        ),  # 无命令 → F3
        memory=MemoryExpectation(min_records=2),  # 0 落库 → F4
    )
    scn2 = scn.model_copy(update={"integration": suite})
    runner = IntegrationRunner()
    res = runner.run(scn2)
    val = IntegrationValidator().validate(res, scn2)

    codes = val.failure_codes()
    # 顺序严格按 F1..F6 固定序
    assert codes == tuple(c for c in FAILURE_CODES if c in codes)
    assert {"F1", "F2", "F3", "F4"}.issubset(set(codes))
    assert val.ok is False


# ============================================================================
# t13 · 可观测交叉校验（F6 三通道；探针 Drop 必被捕获）
# ============================================================================


def test_t13_observability_drop_detected():
    # 注入静默丢弃的 sink → sink_commands 空 ≠ FrameResult.commands → 通道 A 失配
    scn, result, _ = _run_with_sink(ALARM_PATH, _DroppingSink())

    # 生产照常派发（FrameResult.commands 不依赖 sink）
    assert result.commands
    # 但旁路探针什么都没采到
    assert not result.sink_commands

    from home_perception.integration.loop.validator import IntegrationValidator

    val = IntegrationValidator().validate(result, scn)
    assert val.ok is False
    assert "F6" in val.failure_codes()
    obs = next(s for s in val.stages if s.name == "observability")
    assert obs.passed is False
    assert obs.failure_code == "F6"
    # F6 severity 恒 blocking，永不可降级
    assert obs.severity == "blocking"
