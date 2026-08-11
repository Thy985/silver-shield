"""ADR-0034 Phase C · 生产门禁（gate）验收：severity 语义 + 门禁判定 + 指纹联动。

> Phase C 的目标（用户建议语义）：把闭环验证从「测试工程」升级为「验证治理系统」——
> 门禁回答的不再是「测试过了没」，而是「闭环证据、标准版本、运行血缘全部匹配，才允许
> 合并」。severity 是这套治理的语义核心：**谁失败会拦门禁**（blocking），**谁失败只降级**
> （warning，可见但不拦）。
>
> 对应实施计划 §2.5 + t10/t17/t18/t19：
> - t10：memory=warning 失败 → ``passed=True`` + ``degraded=True`` + 报告标注；
>   decision=blocking 失败 → ``passed=False``；
> - t17：severity 只能来自配置对象（suite / severity_table）；运行时改
>   ``StageResult.severity`` 不影响 gate 判定（frozen，判定免疫篡改）；
> - t18：severity 表里写 ``observability: warning`` → 被忽略并 warn，F6 仍使整体不通过；
> - t19：把 memory 由 blocking 降为 warning → ``expectation_fingerprint`` 必变
>   （降级可追溯：改标准必留痕）。

运行环境：闭环 e2e 需要 cv2，须在装有 cv2 的解释器下跑。
"""

from __future__ import annotations

import dataclasses

import pytest

from home_perception.integration.loop.validator import StageResult

# 与 gate.py 的 EXPECTED_SEVERITIES 一致性由 t18 等值断言锁死（防止两边漂移）。
EXPECTED_SEVERITIES = ("blocking", "warning")


def _report(stages, scenario_id: str = "scn-x"):
    from home_perception.integration.loop.report import IntegrationReport

    return IntegrationReport(scenario_id=scenario_id, ok=False, stages=tuple(stages))


def _memory_suite(*, severity: str = "blocking", min_records: int = 1):
    from home_perception.validation.contracts import MemoryExpectation

    return MemoryExpectation(min_records=min_records, severity=severity)


def _suite(**kwargs):
    from home_perception.validation.contracts import IntegrationExpectationSuite

    return IntegrationExpectationSuite(**kwargs)


# ============================================================================
# t10：warning 失败降级不拦门禁 / blocking 失败拦门禁 + 报告标注
# ============================================================================


def test_c10_memory_warning_failure_is_degraded_not_blocking():
    """memory=warning 失败 → gate.passed=True + degraded=True（t10 前半）。"""
    from home_perception.integration.loop.gate import evaluate_integration_gate

    report = _report(
        [
            StageResult(name="memory", passed=False, failure_code="F4"),
            StageResult(name="observability", passed=True),
        ]
    )
    suite = _suite(memory=_memory_suite(severity="warning"))
    gate = evaluate_integration_gate(report, suite)
    assert gate.passed is True, "warning 失败不得拦门禁"
    assert gate.degraded is True, "warning 失败必须可见（degraded）"
    assert [v.name for v in gate.warning_failures()] == ["memory"]
    assert gate.blocking_failures() == ()


def test_c10_decision_blocking_failure_blocks_gate():
    """decision=blocking 失败 → gate.passed=False（t10 后半）。"""
    from home_perception.integration.loop.gate import evaluate_integration_gate

    report = _report(
        [
            StageResult(name="decision", passed=False, failure_code="F2"),
            StageResult(name="observability", passed=True),
        ]
    )
    suite = _suite(decision=None)  # 未声明子期望 → 默认 blocking（fail-closed）
    gate = evaluate_integration_gate(report, suite)
    assert gate.passed is False
    assert [v.name for v in gate.blocking_failures()] == ["decision"]
    assert gate.degraded is False


def test_c10_report_annotates_real_severity_e2e():
    """报告标注：validator 从 suite 填 StageResult.severity（真实场景，t10 标注）。"""
    from home_perception.integration.loop.context import IntegrationRunnerConfig
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(
        "src/home_perception/validation/fixtures/scenarios/integration/"
        "adr0034_cross_modal.yaml"
    )
    scn = scn.model_copy(
        update={"integration": _suite(memory=_memory_suite(severity="warning"))}
    )
    runner = IntegrationRunner(config=IntegrationRunnerConfig(cross_modal_enabled=True))
    result = runner.run(scn)
    validation = IntegrationValidator().validate(result, scn)
    severities = {s.name: s.severity for s in validation.stages}
    assert severities["memory"] == "warning"  # 从 suite 填，非恒 blocking
    assert severities["observability"] == "blocking"  # F6 恒 blocking
    assert severities["decision"] == "blocking"


# ============================================================================
# t17：severity 只来自配置对象；运行时篡改 StageResult.severity 不影响 gate
# ============================================================================


def test_c17_stage_result_is_frozen():
    """StageResult frozen：运行期不可改 severity（t17 的防线之一）。"""
    s = StageResult(name="memory", passed=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.severity = "warning"  # type: ignore[misc]


def test_c17_tampered_stage_severity_does_not_affect_gate():
    """即使绕过 frozen 篡改 report.stages[].severity，gate 判定也不受影响（t17）。"""
    from home_perception.integration.loop.gate import evaluate_integration_gate

    report = _report(
        [
            StageResult(name="memory", passed=False, failure_code="F4", severity="blocking"),
            StageResult(name="observability", passed=True),
        ]
    )
    # 篡改：把报告里的 memory severity 改成 warning（展示投影被污染）
    tampered = dataclasses.replace(report.stages[0], severity="warning")
    report = _report([tampered, report.stages[1]])
    # 但 suite 未声明 memory 子期望 → gate 只从 suite 取 → 仍 blocking
    gate = evaluate_integration_gate(report, _suite())
    assert gate.passed is False
    assert [v.name for v in gate.blocking_failures()] == ["memory"]


def test_c17_severity_from_config_table():
    """severity_table（运维覆盖）是合法配置来源；未在表里的 stage 默认 blocking。"""
    from home_perception.integration.loop.gate import (
        evaluate_integration_gate,
        stage_severity,
    )

    assert stage_severity(None, "memory", severity_table={"memory": "warning"}) == "warning"
    assert stage_severity(None, "decision", severity_table={"memory": "warning"}) == "blocking"

    report = _report(
        [
            StageResult(name="memory", passed=False, failure_code="F4"),
            StageResult(name="observability", passed=True),
        ]
    )
    gate = evaluate_integration_gate(
        report, _suite(), severity_table={"memory": "warning"}
    )
    assert gate.passed is True and gate.degraded is True


# ============================================================================
# t18：F6（observability）永不可降级
# ============================================================================


def test_c18_observability_not_downgradable():
    """severity 表写 observability: warning → 忽略 + warn，F6 仍使整体不通过（t18）。"""
    from home_perception.integration.loop.gate import evaluate_integration_gate

    report = _report(
        [
            StageResult(name="memory", passed=True),
            StageResult(name="observability", passed=False, failure_code="F6"),
        ]
    )
    gate = evaluate_integration_gate(
        report, _suite(), severity_table={"observability": "warning"}
    )
    assert gate.passed is False, "F6 永不可降级：observability 失败必须拦门禁"
    assert any("observability" in n and "永不可降级" in n for n in gate.notices), (
        f"必须记 warn notice，实际 notices={gate.notices}"
    )
    obs = next(v for v in gate.verdicts if v.name == "observability")
    assert obs.severity == "blocking"


def test_c18_severity_values_match_contracts():
    """gate 与 contracts 的 severity 合法取值等值（防止两边漂移）。"""
    from home_perception.integration.loop.gate import EXPECTED_SEVERITIES as GATE_SEV
    from home_perception.validation.contracts import (
        EXPECTED_SEVERITIES as CONTRACT_SEV,
    )

    assert GATE_SEV == CONTRACT_SEV == EXPECTED_SEVERITIES


# ============================================================================
# t19：severity 变更 → expectation_fingerprint 必变（降级可追溯）
# ============================================================================


def test_c19_severity_change_moves_fingerprint():
    """memory blocking→warning → expectation_fingerprint 必变（t19）。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
    )

    fp_blocking = compute_expectation_fingerprint(
        _suite(memory=_memory_suite(severity="blocking", min_records=2))
    )
    fp_warning = compute_expectation_fingerprint(
        _suite(memory=_memory_suite(severity="warning", min_records=2))
    )
    assert fp_blocking != fp_warning, "severity 是评价标准的一部分，变更必须留痕"


def test_c19_version_bumped_to_1_0_0():
    """SCENARIO_INTEGRATION_VERSION == 1.0.0（Phase C；severity 进 canonical 属破坏性变化）。"""
    from home_perception.integration.loop.fingerprint import (
        SCENARIO_INTEGRATION_VERSION,
    )

    assert SCENARIO_INTEGRATION_VERSION == "1.0.0"


# ============================================================================
# 契约与确定性：severity 非法值 fail-closed / gate 序列化 / 空 stage fail-closed
# ============================================================================


@pytest.mark.parametrize(
    "owner,ctor",
    [
        ("perception", lambda: __import__("home_perception.validation.contracts", fromlist=["PerceptionExpectation"]).PerceptionExpectation),
        ("memory", lambda: __import__("home_perception.validation.contracts", fromlist=["MemoryExpectation"]).MemoryExpectation),
        ("decision", lambda: __import__("home_perception.validation.contracts", fromlist=["DecisionExpectation"]).DecisionExpectation),
        ("action", lambda: __import__("home_perception.validation.contracts", fromlist=["ActionExpectation"]).ActionExpectation),
        ("cross_modal", lambda: __import__("home_perception.validation.contracts", fromlist=["CrossModalExpectation"]).CrossModalExpectation),
    ],
)
def test_c_severity_invalid_value_fail_closed(owner, ctor):
    """5 个子期望的 severity 非法值 → 加载期拒绝（fail-closed，防写错名空转）。"""
    cls = ctor()
    with pytest.raises(ValueError, match="severity"):
        cls(severity="fatal")  # type: ignore[call-arg]


def test_c_gate_result_deterministic_and_desensitized():
    """gate 结果确定性（两次 canonical 逐字节一致）+ 序列化键安全（脱敏友好）。"""
    from home_perception.integration.loop.gate import evaluate_integration_gate

    report = _report(
        [
            StageResult(name="decision", passed=False, failure_code="F2"),
            StageResult(name="observability", passed=True),
        ]
    )
    gate = evaluate_integration_gate(report, _suite())
    c1 = gate.canonical_dict()
    c2 = gate.canonical_dict()
    assert c1 == c2
    # 脱敏守卫禁止的键（decision/score/risk_score...）不得作为 JSON **键**出现；
    # "decision" 作为 verdicts[].name 的**值**是安全的。
    assert set(c1) == {"scenario_id", "passed", "degraded", "verdicts", "notices"}
    assert all(set(v) == {"name", "passed", "severity", "failure_code"} for v in c1["verdicts"])


def test_c_gate_empty_stages_fail_closed():
    """空 stage 集合 → 不通过（无证据就没有门禁结论，fail-closed）。"""
    from home_perception.integration.loop.gate import evaluate_integration_gate

    gate = evaluate_integration_gate(_report([]), _suite())
    assert gate.passed is False
    assert any("空 stage" in n for n in gate.notices)


def test_c_gate_suite_type_error():
    """suite 类型错误 → TypeError（与 fingerprint 同纪律）。"""
    from home_perception.integration.loop.gate import evaluate_integration_gate

    report = _report([StageResult(name="memory", passed=True)])
    with pytest.raises(TypeError, match="IntegrationExpectationSuite"):
        evaluate_integration_gate(report, "not-a-suite")  # type: ignore[arg-type]


def test_c_gate_severity_table_invalid_value_fail_closed():
    """severity_table 非法值 → ValueError（fail-closed，不静默采纳）。"""
    from home_perception.integration.loop.gate import evaluate_integration_gate

    report = _report([StageResult(name="memory", passed=False, failure_code="F4")])
    with pytest.raises(ValueError, match="severity_table"):
        evaluate_integration_gate(report, _suite(), severity_table={"memory": "fatal"})
