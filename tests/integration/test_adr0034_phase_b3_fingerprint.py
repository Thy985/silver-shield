"""ADR-0034 Phase B.3 · D7：闭环两枚指纹（expectation_fingerprint / loop_fingerprint）。

> B.3 的目标（用户建议语义）：定义 ``expectation_fingerprint`` 回答「用什么标准评价」
> ——把评价标准（``IntegrationExpectationSuite``）规范化哈希；``loop_fingerprint``
> 回答「这次闭环是用哪套输入 + 标准 + 装配跑的」（6 成分 fail-closed）。
>
> 对应实施计划 §2.5 + t15/t16：
> - t15：仅改期望（如 ``min_records`` 1→2）→ ``expectation_fingerprint`` 变、
>   ``loop_fingerprint`` 随之变；场景/策略未变时其余成分不变；
> - t16：任一成分缺失 → raise（fail-closed）；ADR-0033
>   ``FINGERPRINT_COMPONENT_FIELDS`` 未被修改（读取常量做等值断言），且本模块
>   **不得** import ``evaluation``（字段守恒域隔离，t16 纪律）。

运行环境：闭环 e2e 需要 cv2（``_assemble`` 运行时重链），须在装有 cv2 的解释器下跑；
ruff 仍跑在托管 venv（py3.13）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
CROSS_MODAL_PATH = INTEGRATION_DIR / "adr0034_cross_modal.yaml"
FINGERPRINT_PATH = (
    REPO_ROOT / "src" / "home_perception" / "integration" / "loop" / "fingerprint.py"
)

# 与 implementation-plan §2.5 对齐：Phase A=0.1.0 / B=0.2.0 / C=1.0.0
EXPECTED_SCENARIO_INTEGRATION_VERSION = "0.2.0"


def _suite(**kwargs):
    from home_perception.validation.contracts import IntegrationExpectationSuite

    return IntegrationExpectationSuite(**kwargs)


def _base_loop_kwargs(expectation_fp: str) -> dict:
    return {
        "harness_fp": "H" * 64,
        "policy_fp": "P" * 64,
        "sink_type": "in_memory",
        "memory_backend": "in_memory",
        "cross_modal_enabled": False,
        "expectation_fp": expectation_fp,
    }


# ============================================================================
# t15：仅改期望 → expectation_fingerprint 变、loop_fingerprint 联动、其余成分不变
# ============================================================================


def test_b3_expectation_change_moves_fingerprint_t15():
    """min_records 1→2 → expectation_fingerprint 必变（变异验证）。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
    )
    from home_perception.validation.contracts import MemoryExpectation

    fp1 = compute_expectation_fingerprint(
        _suite(memory=MemoryExpectation(min_records=1))
    )
    fp2 = compute_expectation_fingerprint(
        _suite(memory=MemoryExpectation(min_records=2))
    )
    assert fp1 != fp2, "仅改 min_records 1→2，expectation_fingerprint 必须变（t15）"
    assert len(fp1) == 64 and len(fp2) == 64


def test_b3_loop_fingerprint_linked_rest_unchanged_t15():
    """loop_fingerprint 随 expectation_fp 联动；其余 5 成分逐项不变（t15）。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
        compute_loop_fingerprint,
        loop_fingerprint_components,
    )
    from home_perception.validation.contracts import MemoryExpectation

    fp1 = compute_expectation_fingerprint(
        _suite(memory=MemoryExpectation(min_records=1))
    )
    fp2 = compute_expectation_fingerprint(
        _suite(memory=MemoryExpectation(min_records=2))
    )

    lf1 = compute_loop_fingerprint(**_base_loop_kwargs(fp1))
    lf2 = compute_loop_fingerprint(**_base_loop_kwargs(fp2))
    assert lf1 != lf2, "expectation_fp 变，loop_fingerprint 必须随之变（t15）"

    # 其余成分逐项不变（场景/策略/装配未动）
    c1 = loop_fingerprint_components(**_base_loop_kwargs(fp1))
    c2 = loop_fingerprint_components(**_base_loop_kwargs(fp2))
    assert c1["expectation_fp"] != c2["expectation_fp"]
    for key in ("harness_fp", "policy_fp", "sink_type", "memory_backend", "cross_modal_enabled"):
        assert c1[key] == c2[key], f"成分 {key} 不应随期望变更而变（t15）"


def test_b3_subexpectation_sensitive():
    """各子期望（decision / cross_modal）变更 → expectation_fingerprint 必变。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
    )
    from home_perception.validation.contracts import (
        CrossModalExpectation,
        DecisionExpectation,
    )

    base = compute_expectation_fingerprint(_suite())
    assert compute_expectation_fingerprint(
        _suite(decision=DecisionExpectation(outcome="WARN"))
    ) != base
    assert compute_expectation_fingerprint(
        _suite(cross_modal=CrossModalExpectation(min_links=2))
    ) != base


# ============================================================================
# 确定性 / None 语义 / bool 成分
# ============================================================================


def test_b3_deterministic():
    """同标准两次计算必同指纹；同成分两次 loop 必同指纹。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
        compute_loop_fingerprint,
    )
    from home_perception.validation.contracts import MemoryExpectation

    fp = compute_expectation_fingerprint(
        _suite(memory=MemoryExpectation(min_records=3))
    )
    assert fp == compute_expectation_fingerprint(
        _suite(memory=MemoryExpectation(min_records=3))
    )
    lf = compute_loop_fingerprint(**_base_loop_kwargs(fp))
    assert lf == compute_loop_fingerprint(**_base_loop_kwargs(fp))


def test_b3_none_suite_equals_empty_suite():
    """None == 空套件（validator 的 ``or IntegrationExpectationSuite()`` 同款语义）。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
    )

    assert compute_expectation_fingerprint(None) == compute_expectation_fingerprint(
        _suite()
    )


def test_b3_cross_modal_boolean_component_distinct():
    """cross_modal_enabled True/False 是两种合法配置 → loop 指纹不同。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
        compute_loop_fingerprint,
    )

    fp = compute_expectation_fingerprint(None)
    off_kwargs = _base_loop_kwargs(fp)
    on_kwargs = dict(_base_loop_kwargs(fp), cross_modal_enabled=True)
    off = compute_loop_fingerprint(**off_kwargs)
    on = compute_loop_fingerprint(**on_kwargs)
    assert off != on, "跨模态开关是装配成分，True/False 指纹必须不同"


# ============================================================================
# t16：任一成分缺失 → raise（fail-closed）
# ============================================================================


@pytest.mark.parametrize(
    "mutate,exc",
    [
        ({"harness_fp": ""}, ValueError),  # 空字符串
        ({"harness_fp": None}, TypeError),  # None（非 str）
        ({"policy_fp": ""}, ValueError),  # 空字符串
        ({"policy_fp": None}, TypeError),  # None
        ({"sink_type": ""}, ValueError),  # 空字符串
        ({"memory_backend": None}, TypeError),  # None
        ({"expectation_fp": ""}, ValueError),  # 空字符串
    ],
)
def test_b3_fail_closed_string_component_t16(mutate, exc):
    """任一字符串成分缺失（空 / None）→ raise（t16，绝不静默降级）。

    空字符串 = 值非法 → ``ValueError``；``None`` = 类型非法 → ``TypeError``。
    """
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
        compute_loop_fingerprint,
    )

    fp = compute_expectation_fingerprint(None)
    kwargs = _base_loop_kwargs(fp)
    kwargs.update(mutate)
    with pytest.raises(exc, match="fail-closed"):
        compute_loop_fingerprint(**kwargs)


@pytest.mark.parametrize("bad", [None, 1, 0, "false"])
def test_b3_fail_closed_bool_component_t16(bad):
    """cross_modal_enabled 必须是显式 bool（None/0/1/"false" 均拒绝，False 合法）。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
        compute_loop_fingerprint,
    )

    fp = compute_expectation_fingerprint(None)
    kwargs = _base_loop_kwargs(fp)
    kwargs["cross_modal_enabled"] = bad
    with pytest.raises(TypeError, match="cross_modal_enabled"):
        compute_loop_fingerprint(**kwargs)


def test_b3_fail_closed_audit_components_t16():
    """审计视图与 compute 同校验：缺成分同样 raise（两处共用语义）。"""
    from home_perception.integration.loop.fingerprint import (
        compute_expectation_fingerprint,
        loop_fingerprint_components,
    )

    fp = compute_expectation_fingerprint(None)
    kwargs = _base_loop_kwargs(fp)
    kwargs["policy_fp"] = ""
    with pytest.raises(ValueError):
        loop_fingerprint_components(**kwargs)


# ============================================================================
# t16：ADR-0033 FINGERPRINT_COMPONENT_FIELDS 守恒 + 本模块不 import evaluation
# ============================================================================


def test_b3_fingerprint_component_fields_unchanged_t16():
    """ADR-0033 FINGERPRINT_COMPONENT_FIELDS 未被修改（读取常量做等值断言）。"""
    from home_perception.evaluation.fingerprint_fields import (
        FINGERPRINT_COMPONENT_FIELDS,
    )

    assert FINGERPRINT_COMPONENT_FIELDS == (
        "scenario_set_id",
        "code_version",
        "generator_fingerprint",
        "policy_fingerprint",
        "model_fingerprint",
        "runtime_dependencies",
    ), "ADR-0033 指纹成分字段被修改（t16）"


def test_b3_fingerprint_module_does_not_import_evaluation():
    """loop/fingerprint.py 不得 import evaluation（字段守恒域隔离，t16 纪律）。

    用 AST 解析 import 语句（docstring 引用常量名属合法文档，不做字符串扫描）。
    """
    import ast

    tree = ast.parse(FINGERPRINT_PATH.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offending = [m for m in imported if "evaluation" in m]
    assert not offending, f"loop 指纹模块不得 import evaluation，发现 {offending}"


def test_b3_version_matches_plan():
    """SCENARIO_INTEGRATION_VERSION == 0.2.0（Phase B；实施计划 §2.5）。"""
    from home_perception.integration.loop.fingerprint import (
        SCENARIO_INTEGRATION_VERSION,
    )

    assert SCENARIO_INTEGRATION_VERSION == EXPECTED_SCENARIO_INTEGRATION_VERSION


# ============================================================================
# e2e：IntegrationRunResult 两枚指纹
# ============================================================================


def test_b3_runner_fills_fingerprints_e2e():
    """run() 产出非空两枚指纹：互异、与 synth 指纹互异、期望变更联动、synth 不变。"""
    from home_perception.integration.loop.context import (
        IntegrationContext,
        IntegrationRunnerConfig,
    )
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.validation.contracts import IntegrationExpectationSuite
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(CROSS_MODAL_PATH)
    cfg = IntegrationRunnerConfig(cross_modal_enabled=True)
    ctx = IntegrationContext.build(cfg)
    runner = IntegrationRunner(config=cfg)
    res = runner.run(scn, context=ctx)

    # 非空 + 互异
    assert len(res.expectation_fingerprint) == 64
    assert len(res.loop_fingerprint) == 64
    assert res.expectation_fingerprint != res.loop_fingerprint
    assert res.fingerprint not in (res.expectation_fingerprint, res.loop_fingerprint)

    # 期望变更 → 两枚指纹联动；场景输入指纹（synth）不变
    scn2 = scn.model_copy(
        update={"integration": IntegrationExpectationSuite(memory=None)}
    )
    res2 = runner.run(scn2, context=ctx)
    assert res.expectation_fingerprint != res2.expectation_fingerprint
    assert res.loop_fingerprint != res2.loop_fingerprint
    assert res.fingerprint == res2.fingerprint, "场景输入指纹不应随期望变更而变"


def test_b3_loop_fingerprint_components_audit_e2e():
    """loop_fingerprint_components 返回 6 成分且键集 == LOOP_FINGERPRINT_COMPONENT_FIELDS。"""
    from home_perception.integration.loop.fingerprint import (
        LOOP_FINGERPRINT_COMPONENT_FIELDS,
        compute_expectation_fingerprint,
        loop_fingerprint_components,
    )

    fp = compute_expectation_fingerprint(None)
    comps = loop_fingerprint_components(**_base_loop_kwargs(fp))
    assert set(comps) == set(LOOP_FINGERPRINT_COMPONENT_FIELDS)
    assert comps["cross_modal_enabled"] == "0"  # False → 审计视图 "0"
