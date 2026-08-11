"""ADR-0034 Phase B.2.1 · 跨模态关联 F5 validator（基础层）。

> 本文件只验证 Phase B.2 的**基础层**（不含音频驱动，音频驱动属 B.2.2）：
>   - ``CrossModalExpectation`` 契约（fail-closed 枚举校验 + 集合语义）；
>   - F5 stage 在 ``validate()`` 中产出（全 AND 的一环）；
>   - ``_check_cross_modal`` 结构化逻辑（min_links / expected_linked_modalities /
>     required_relationships）；
>   - t8：声明 ``cross_modal`` 期望却零关联边 → F5 不通过（而非静默通过）；
>   - 接线：``cross_modal_enabled=True`` 时 ``ctx.cross_modal_runtime`` 真注入、
>     ``_collect`` 从 runtime 读回真实 ``cross_modal_links``。
>
> 零行为变化铁律：``cross_modal_enabled=False`` 时与原 Phase A 行为完全一致
> （runtime 恒 None、links 恒空、F5 stage 仅 trivially 通过）。
>
> 运行环境：闭环需要 cv2（依赖 ``_assemble`` 的运行时重链），须在装有 cv2 的解释器下跑
> （如系统 Py3.14）；ruff 仍跑在托管 venv（py3.13）。依赖延迟导入。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from home_perception.core.event import EvidenceModality
from home_perception.memory.cross_modal_link import CrossModalLink, CrossModalRelationship

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
ELDERLY_PATH = INTEGRATION_DIR / "adr0034_elderly_dwell.yaml"


# ============================================================================
# 助手
# ============================================================================


def _run_with_config(path, config):
    """加载 fixture → 用给定 config 跑通闭环 → validate。"""
    from home_perception.integration.loop.context import IntegrationContext
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(path)
    runner = IntegrationRunner(config=config)
    ctx = IntegrationContext.build(config)
    result = runner.run(scn, context=ctx)
    validation = IntegrationValidator().validate(result, scn)
    return scn, result, validation, ctx


def _make_link(eid_a: str, eid_b: str, rel: CrossModalRelationship) -> CrossModalLink:
    """构造一条真实 CrossModalLink（确定性，供单元化 F5 逻辑验证）。"""
    return CrossModalLink(
        link_id=f"link-{eid_a}-{eid_b}",
        episode_ids=[eid_a, eid_b],
        relationship=rel,
        time_overlap=(
            datetime(2026, 7, 19, 18, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 19, 18, 5, 0, tzinfo=UTC),
        ),
        confidence=0.5,
        created_at=datetime(2026, 7, 19, 18, 5, 0, tzinfo=UTC),
    )


class _DummyEpisode:
    """鸭式 episode：仅暴露 F5 结构化断言所需的 record_id / modalities。"""

    def __init__(self, record_id: str, modalities: list[EvidenceModality]) -> None:
        self.record_id = record_id
        self.modalities = modalities


# ============================================================================
# 契约 fail-closed
# ============================================================================


def test_b2_contract_forbid_unknown():
    """CrossModalExpectation 写未知键 → ValidationError（extra=forbid）。"""
    from home_perception.validation.contracts import CrossModalExpectation

    with pytest.raises(ValidationError):
        CrossModalExpectation(bogus_key=1)  # type: ignore[arg-type]


def test_b2_contract_invalid_modality():
    """expected_linked_modalities 含非法模态 → 拒绝（fail-closed）。"""
    from home_perception.validation.contracts import CrossModalExpectation

    with pytest.raises(ValidationError):
        CrossModalExpectation(expected_linked_modalities=["smell"])


def test_b2_contract_invalid_relationship():
    """required_relationships 含非法关系 → 拒绝（fail-closed）。"""
    from home_perception.validation.contracts import CrossModalExpectation

    with pytest.raises(ValidationError):
        CrossModalExpectation(required_relationships=["causes"])


def test_b2_contract_duplicate_rejected():
    """集合语义字段含重复项 → 拒绝（D4 禁止精确计数，重复项多半笔误）。"""
    from home_perception.validation.contracts import CrossModalExpectation

    with pytest.raises(ValidationError):
        CrossModalExpectation(expected_linked_modalities=["vision", "vision"])
    with pytest.raises(ValidationError):
        CrossModalExpectation(required_relationships=["supports", "supports"])


# ============================================================================
# F5 stage 在 validate() 中产出
# ============================================================================


def test_b2_f5_stage_emitted():
    """validate() 必须产出名为 cross_modal 的 F5 stage（全 AND 的一环）。"""
    from home_perception.integration.loop.context import IntegrationRunnerConfig

    _, _, validation, _ = _run_with_config(ELDERLY_PATH, IntegrationRunnerConfig())
    names = [s.name for s in validation.stages]
    assert "cross_modal" in names, f"validate() 未产出 cross_modal stage；stages={names}"
    cm = next(s for s in validation.stages if s.name == "cross_modal")
    # 未声明 cross_modal 期望时 trivially 通过（零行为变化）
    assert cm.passed is True
    assert cm.failure_code is None


# ============================================================================
# 接线：cross_modal_enabled=True 时 runtime 真注入、_collect 读回
# ============================================================================


def test_b2_cross_modal_enabled_wires_runtime():
    """cross_modal_enabled=True → ctx.cross_modal_runtime 真注入、_collect 读回 links。"""
    from home_perception.integration.loop.context import IntegrationRunnerConfig

    cfg = IntegrationRunnerConfig(cross_modal_enabled=True)
    _, result, _, ctx = _run_with_config(ELDERLY_PATH, cfg)

    assert ctx.cross_modal_runtime is not None, (
        "cross_modal_enabled=True 但 ctx.cross_modal_runtime 仍 None（未注入）"
    )
    assert hasattr(ctx.cross_modal_runtime, "all_links")
    # _collect 从 runtime 只读读回；elderly 单 vision episode → 无重叠 → 0 边，
    # 但"读回通道"必须接通（结果等于 runtime 当前全部边）
    assert isinstance(result.cross_modal_links, tuple)
    assert result.cross_modal_links == tuple(ctx.cross_modal_runtime.all_links())


def test_b2_cross_modal_disabled_keeps_runtime_none():
    """cross_modal_enabled=False（默认）→ runtime 恒 None、links 恒空（零行为变化）。"""
    from home_perception.integration.loop.context import IntegrationRunnerConfig

    cfg = IntegrationRunnerConfig(cross_modal_enabled=False)
    _, result, _, ctx = _run_with_config(ELDERLY_PATH, cfg)
    assert ctx.cross_modal_runtime is None
    assert result.cross_modal_links == ()


# ============================================================================
# F5 结构化断言逻辑（单元化，鸭式构造 links/episodes）
# ============================================================================


def _f5_result(links, episodes):
    return SimpleNamespace(cross_modal_links=list(links), episodes=list(episodes))


def test_b2_f5_min_links_pass():
    """至少一条关联边 + min_links=1 → F5 通过。"""
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.contracts import CrossModalExpectation

    link = _make_link("ep-vis", "ep-aud", CrossModalRelationship.SUPPORTS)
    res = _f5_result([link], [])
    stage = IntegrationValidator._check_cross_modal(
        res, _suite(cross_modal=CrossModalExpectation(min_links=1))
    )
    assert stage.passed is True, stage.detail
    assert stage.name == "cross_modal"


def test_b2_f5_min_links_fail():
    """零关联边 + min_links=1 → F5 不通过（下界未达）。"""
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.contracts import CrossModalExpectation

    res = _f5_result([], [])
    stage = IntegrationValidator._check_cross_modal(
        res, _suite(cross_modal=CrossModalExpectation(min_links=1))
    )
    assert stage.passed is False
    assert stage.failure_code == "F5"
    assert "下界" in stage.detail


def test_b2_f5_linked_modalities_pass():
    """link 连接 vision+audio 两 episode，期望 [vision,audio] → 覆盖命中 → 通过。"""
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.contracts import CrossModalExpectation

    link = _make_link("ep-vis", "ep-aud", CrossModalRelationship.SUPPORTS)
    eps = [
        _DummyEpisode("ep-vis", [EvidenceModality.VISION]),
        _DummyEpisode("ep-aud", [EvidenceModality.AUDIO]),
    ]
    res = _f5_result([link], eps)
    stage = IntegrationValidator._check_cross_modal(
        res,
        _suite(cross_modal=CrossModalExpectation(expected_linked_modalities=["vision", "audio"])),
    )
    assert stage.passed is True, stage.detail


def test_b2_f5_linked_modalities_fail():
    """link 仅连接 vision+audio，却期望含 identity → 未覆盖 → F5 不通过。"""
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.contracts import CrossModalExpectation

    link = _make_link("ep-vis", "ep-aud", CrossModalRelationship.SUPPORTS)
    eps = [
        _DummyEpisode("ep-vis", [EvidenceModality.VISION]),
        _DummyEpisode("ep-aud", [EvidenceModality.AUDIO]),
    ]
    res = _f5_result([link], eps)
    stage = IntegrationValidator._check_cross_modal(
        res,
        _suite(cross_modal=CrossModalExpectation(expected_linked_modalities=["identity"])),
    )
    assert stage.passed is False
    assert stage.failure_code == "F5"
    assert "模态" in stage.detail


def test_b2_f5_relationship_pass():
    """link 关系=supports，期望 [supports] → 命中 → 通过。"""
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.contracts import CrossModalExpectation

    link = _make_link("ep-vis", "ep-aud", CrossModalRelationship.SUPPORTS)
    res = _f5_result([link], [])
    stage = IntegrationValidator._check_cross_modal(
        res, _suite(cross_modal=CrossModalExpectation(required_relationships=["supports"]))
    )
    assert stage.passed is True, stage.detail


def test_b2_f5_relationship_fail():
    """link 关系=supports，却期望 [co_occurs] → 未命中 → F5 不通过。"""
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.contracts import CrossModalExpectation

    link = _make_link("ep-vis", "ep-aud", CrossModalRelationship.SUPPORTS)
    res = _f5_result([link], [])
    stage = IntegrationValidator._check_cross_modal(
        res, _suite(cross_modal=CrossModalExpectation(required_relationships=["co_occurs"]))
    )
    assert stage.passed is False
    assert stage.failure_code == "F5"
    assert "关系" in stage.detail


# ============================================================================
# t8：声明 cross_modal 期望却零关联边 → F5 不通过（而非静默通过）
# ============================================================================


def test_b2_f5_t8_expectation_but_no_links():
    """t8：声明 cross_modal 期望但闭环未启用跨模态（零关联边）→ F5 不通过。

    复现路径：用默认 config（cross_modal_enabled=False，runtime 恒 None 不建边）跑
    elderly，并把场景的 integration 套件声明 cross_modal=min_links=1。validator 必须在
    F5 处判不通过——绝不能因为"没写期望就跳过"而让这种"声称要验证却拿不出边"的情形逃逸。
    """
    from home_perception.integration.loop.context import IntegrationRunnerConfig
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.contracts import (
        CrossModalExpectation,
        IntegrationExpectationSuite,
    )
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(ELDERLY_PATH)
    # 声明 cross_modal 期望但闭环未启用跨模态 → 必然零边
    scn = scn.model_copy(
        update={"integration": IntegrationExpectationSuite(cross_modal=CrossModalExpectation(min_links=1))}
    )
    runner = IntegrationRunner(config=IntegrationRunnerConfig())
    result = runner.run(scn)  # 默认 config：cross_modal_enabled=False
    validation = IntegrationValidator().validate(result, scn)

    assert validation.ok is False, "t8：声明 cross_modal 期望却零边，应整体不通过"
    assert "F5" in validation.failure_codes(), (
        f"t8：失败码应包含 F5，实际 {validation.failure_codes()}"
    )
    cm = next(s for s in validation.stages if s.name == "cross_modal")
    assert cm.passed is False
    assert "下界" in cm.detail


# ============================================================================
# 私助
# ============================================================================


def _suite(**kwargs):
    from home_perception.validation.contracts import IntegrationExpectationSuite

    return IntegrationExpectationSuite(**kwargs)
