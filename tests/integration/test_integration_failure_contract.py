"""ADR-0034 Phase C · 失败注入契约（DoD C6）：验证系统本身也需要验证。

目的：防止未来有人"放宽" Validator（如把某分支改成 ``return passed=True``）后 CI 仍然
绿色。因此对每条核心失败路径做**探针级注入**——制造"系统行为被破坏"的真实数据，断言
Validator 必须如实判失败、且失败码精确：

| 注入 | 期待 | 验证点 |
|---|---|---|
| 删除 decision_traces（有感知事件却零 trace） | F2 | Decision Drop 不静默通过 |
| 删除 commands（warning 无对应命令） | F3 | Notification Drop 不静默通过 |
| 删除 cross_modal_links（声明了期望却零关联边） | F5 | CrossModal Drop 不静默通过 |
| （t13 已覆盖 F6：dropping sink） | F6 | Observability Drop |

注入方式：真实场景跑通闭环后，用 ``dataclasses.replace`` 构造"损坏"的
``IntegrationRunResult``——比"改期望"更贴近"系统被破坏"的真实失败形态
（期望驱动只能证明 Validator 会执行期望，注入型能证明 Validator 不会放过数据缺失）。

运行环境：闭环 e2e 需要 cv2，须在装有 cv2 的解释器下跑。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from home_perception.integration.loop.runner import IntegrationRunner
from home_perception.integration.loop.validator import IntegrationValidator
from home_perception.validation.scenario import load_scenario

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
CROSS_MODAL_PATH = INTEGRATION_DIR / "adr0034_cross_modal.yaml"


def _run(path: Path, *, cross_modal: bool = False):
    """跑通真实闭环（alarm 用默认 config；cross_modal 场景须启用跨模态）。"""
    from home_perception.integration.loop.context import IntegrationRunnerConfig

    scn = load_scenario(path)
    runner = IntegrationRunner(
        config=IntegrationRunnerConfig(cross_modal_enabled=cross_modal)
    )
    result = runner.run(scn)
    return scn, result


def _validate(scn, result):
    return IntegrationValidator().validate(result, scn)


def _stage(result, name):
    return next(s for s in result.stages if s.name == name)


# ============================================================================
# F2：删除 trace（有感知事件却零决策痕迹）→ Decision Drop
# ============================================================================


def test_inject_delete_traces_f2():
    """注入：删光 decision_traces（上游事件仍在）→ F2，且绝不静默通过。"""
    scn, result = _run(ALARM_PATH)
    assert result.perception_events, "前置：alarm 场景必须产生感知事件"
    assert result.decision_traces, "前置：alarm 场景必须产生决策痕迹"

    broken = dataclasses.replace(result, decision_traces=())
    val = _validate(scn, broken)

    assert val.ok is False, "删 trace 后必须判失败（F2 Decision Drop 不得静默通过）"
    assert "F2" in val.failure_codes(), f"期待 F2，实际 {val.failure_codes()}"
    assert _stage(val, "decision").passed is False
    assert _stage(val, "decision").failure_code == "F2"


# ============================================================================
# F3：删除 command（warning 无对应命令）→ Notification Drop
# ============================================================================


def test_inject_delete_commands_f3():
    """注入：删光 commands（warnings 仍在）→ F3，且绝不静默通过。"""
    scn, result = _run(ALARM_PATH)
    assert result.warnings, "前置：alarm 场景必须产生告警"
    assert result.commands, "前置：alarm 场景必须产生命令"

    broken = dataclasses.replace(result, commands=())
    val = _validate(scn, broken)

    assert val.ok is False, "删 command 后必须判失败（F3 Notification Drop 不得静默通过）"
    assert "F3" in val.failure_codes(), f"期待 F3，实际 {val.failure_codes()}"
    assert _stage(val, "notification").passed is False
    assert _stage(val, "notification").failure_code == "F3"


# ============================================================================
# F5：删除 cross_modal_links（声明了期望却零关联边）→ CrossModal Drop
# ============================================================================


def test_inject_delete_links_f5():
    """注入：删光 cross_modal_links（cross_modal 期望仍在）→ F5，且绝不静默通过。"""
    scn, result = _run(CROSS_MODAL_PATH, cross_modal=True)
    assert scn.integration is not None and scn.integration.cross_modal is not None, (
        "前置：cross_modal fixture 必须声明 F5 期望"
    )
    assert result.cross_modal_links, "前置：启用跨模态后必须产出关联边"

    broken = dataclasses.replace(result, cross_modal_links=())
    val = _validate(scn, broken)

    assert val.ok is False, "删关联边后必须判失败（F5 CrossModal Drop 不得静默通过）"
    assert "F5" in val.failure_codes(), f"期待 F5，实际 {val.failure_codes()}"
    assert _stage(val, "cross_modal").passed is False
    assert _stage(val, "cross_modal").failure_code == "F5"


# ============================================================================
# 完整性：三枚失败码互不干扰（各注入精确命中自己的 stage）
# ============================================================================


def test_injections_hit_precise_stages():
    """三个注入各自只触发对应 stage 的失败（防止"全盘变红"掩盖真因）。"""
    cases = [
        (ALARM_PATH, False, "decision_traces", "decision", "F2"),
        (ALARM_PATH, False, "commands", "notification", "F3"),
        (CROSS_MODAL_PATH, True, "cross_modal_links", "cross_modal", "F5"),
    ]
    for path, cross_modal, field, stage_name, code in cases:
        scn, result = _run(path, cross_modal=cross_modal)
        broken = dataclasses.replace(result, **{field: ()})
        val = _validate(scn, broken)
        assert code in val.failure_codes(), (
            f"{field}=() 未触发 {code}，实际 {val.failure_codes()}"
        )
        s = _stage(val, stage_name)
        assert s.passed is False and s.failure_code == code
        # 目标 stage 失败时，其余业务 stage 不应无辜变红（F6 可能因联动失配，属预期）
        other_failed = [
            st.name
            for st in val.stages
            if st.name != stage_name and st.name != "observability" and not st.passed
        ]
        assert other_failed == [], (
            f"{field}=() 误伤了其他业务 stage：{other_failed}"
        )
