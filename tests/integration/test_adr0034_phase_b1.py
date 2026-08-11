"""ADR-0034 Phase B.1 · Memory 闭环深度断言（契约测试）。

> 本文件只验证 **Phase B.1 的 Memory 深度断言**：
>   - `MemoryExpectation` 新增结构化字段（expected_risk_level / expected_action_types /
>     required_modalities）的 fail-closed 契约；
>   - F4 结构化断言（在 min_records 下界之外，按"并集包含"判定）；
>   - 验收三要素（对应 ADR-0034 F4）：
>       ① memory_hook 真注入（闭环往 context 持有的 store 写）；
>       ② episodic record 真产生（真实 EpisodicRecord，形状正确）；
>       ③ action 关联存在（episode.actions[0].command_id == 已执行命令 command_id）。
>
> 零行为变化铁律：不新增任何决策/感知行为，只新增"对已发生事实的断言"。
>
> 运行环境：闭环需要 cv2（依赖 ``_assemble`` 的运行时重链），须在装有 cv2 的解释器下跑
> （如系统 Py3.14）；ruff 仍跑在托管 venv（py3.13）。依赖延迟导入。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from home_perception.memory.records import EpisodicRecord

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
ELDERLY_PATH = INTEGRATION_DIR / "adr0034_elderly_dwell.yaml"


# ============================================================================
# 助手：跑通最小闭环（含持有 ctx 的变体，用于验证 memory_hook 真注入）
# ============================================================================


def _run(path):
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


def _run_full(path):
    """同 ``_run``，但显式持有 ``IntegrationContext`` 以便断言 store 被闭环写入。

    runner 在 ``context=None`` 时会内部 ``IntegrationContext.build`` 一个全新 ctx——那会让
    我们拿不到 store 引用，无法证明"闭环写进的是 context 持有的 store"。此处**自己构建
    ctx 并传入**，跑完直接检查 ``ctx.memory_store`` 是否被注入 memory_hook 填充。
    """
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
    result = runner.run(scn, context=ctx)
    validation = IntegrationValidator().validate(result, scn)
    return scn, result, validation, ctx


# ============================================================================
# 验收三要素（对应 ADR-0034 F4）
# ============================================================================


def test_b1_memory_hook_injected():
    """① memory_hook 真注入：闭环把我们持有的 store 写进了 episode。

    ``runner.run`` 装配的 ``MemoryHook`` 必须写入 ``ctx.memory_store``；跑完后该 store
    非空、且与 ``result.episodes``（从同一 store 读回）完全一致——证明 episode 不是凭空
    造出来、而是 memory_hook 真挂进了 pipeline。
    """
    _, result, _, ctx = _run_full(ELDERLY_PATH)

    store_episodes = tuple(ctx.memory_store.all_episodic())
    assert store_episodes, "memory_hook 未注入：context 持有的 store 跑完仍为空"
    assert store_episodes == result.episodes, (
        "result.episodes 与 context store 不一致——episode 来源可疑（非 memory_hook 写入）"
    )


def test_b1_episodic_record_produced():
    """② episodic record 真产生：至少一条真实 EpisodicRecord，形状正确。"""
    _, result, _, _ = _run_full(ELDERLY_PATH)

    assert result.episodes, "闭环未产生任何 episodic record"
    ep = result.episodes[0]
    assert isinstance(ep, EpisodicRecord), f"episode 不是 EpisodicRecord，而是 {type(ep)}"
    assert ep.record_id.startswith("ep-"), f"record_id 非法：{ep.record_id}"
    # 形状与 ADR-0034 Phase B.1 验收一致：risk_level=LOW / modalities=[vision]
    assert ep.risk_level == "LOW", f"期望 risk_level=LOW，实际 {ep.risk_level}"
    mod_values = [m.value if hasattr(m, "value") else m for m in ep.modalities]
    assert mod_values == ["vision"], f"期望 modalities=[vision]，实际 {mod_values}"
    assert ep.actions, "episode 未携带任何 action 投影"


def test_b1_action_associated():
    """③ action 关联存在：episode 投影的 command_id 指向真实执行过的命令。

    与 F6 通道 C（episode 命令 ⊆ 已执行命令）互为印证：此处进一步断言"至少有一条
    action 确实引用了已执行命令的 id"——episode 不是孤立造出来的。
    """
    _, result, _, _ = _run_full(ELDERLY_PATH)

    assert result.commands, "闭环未派发任何命令"
    assert result.episodes[0].actions, "episode 无 action 投影"
    ep_action = result.episodes[0].actions[0]
    assert ep_action.command_id == str(result.commands[0].command_id), (
        f"episode action.command_id={ep_action.command_id} 与已执行命令 "
        f"command_id={result.commands[0].command_id} 不匹配——action 关联断裂"
    )


def test_b1_structured_pass_end_to_end():
    """端到端：载入声明了结构化 memory 块的 elderly_dwell fixture，整体判定通过。

    既验证 fixture 可加载（契约层 fail-closed 已放行结构化字段），也验证 F4 结构化断言
    在"声明与事实一致"时不妨碍通过。
    """
    _, _, validation = _run(ELDERLY_PATH)
    assert validation.ok is True, str(validation)
    assert validation.failure_codes() == ()

    mem = next(s for s in validation.stages if s.name == "memory")
    assert mem.passed is True
    assert mem.failure_code is None


# ============================================================================
# F4 结构化断言 · 负例（声明与事实不符必须判 F4 不通过）
# ============================================================================


def _run_with_memory_expectation(path, memory_exp):
    """把场景的 integration 块替换为仅含给定 memory 期望的 suite，跑通并判定。"""
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.contracts import IntegrationExpectationSuite
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(path)
    suite = IntegrationExpectationSuite(memory=memory_exp)
    scn2 = scn.model_copy(update={"integration": suite})
    res = IntegrationRunner().run(scn2)
    val = IntegrationValidator().validate(res, scn2)
    return res, val


def test_b1_f4_risk_level_mismatch():
    """结构错误：期望 HIGH 但 episode 实际为 LOW → F4 不通过。"""
    from home_perception.validation.contracts import MemoryExpectation

    # ALARM 路径实测 episode.risk_level=LOW，故意断言 HIGH 制造必败
    _, val = _run_with_memory_expectation(
        ALARM_PATH, MemoryExpectation(min_records=1, expected_risk_level="HIGH")
    )
    assert val.ok is False
    assert "F4" in val.failure_codes()
    mem = next(s for s in val.stages if s.name == "memory")
    assert not mem.passed
    assert "期望风险等级" in mem.detail


def test_b1_f4_action_type_mismatch():
    """结构错误：期望 SEND_FAMILY_MESSAGE 但 episode 仅含 LOG_ONLY → F4 不通过。"""
    from home_perception.validation.contracts import MemoryExpectation

    _, val = _run_with_memory_expectation(
        ALARM_PATH,
        MemoryExpectation(min_records=1, expected_action_types=["SEND_FAMILY_MESSAGE"]),
    )
    assert val.ok is False
    assert "F4" in val.failure_codes()
    mem = next(s for s in val.stages if s.name == "memory")
    assert not mem.passed
    assert "缺动作类型" in mem.detail


def test_b1_f4_modality_mismatch():
    """结构错误：期望 audio 模态但 episode 仅含 vision → F4 不通过。"""
    from home_perception.validation.contracts import MemoryExpectation

    _, val = _run_with_memory_expectation(
        ALARM_PATH, MemoryExpectation(min_records=1, required_modalities=["audio"])
    )
    assert val.ok is False
    assert "F4" in val.failure_codes()
    mem = next(s for s in val.stages if s.name == "memory")
    assert not mem.passed
    assert "缺模态" in mem.detail


# ============================================================================
# 契约层 fail-closed（加载期就拒绝非法/未知键，而非跑完才静默放过）
# ============================================================================


def test_b1_contract_forbid_unknown_key():
    """未知键必须被 extra='forbid' 拒绝（与 ADR-0034 静默丢弃命题一致）。"""
    from home_perception.validation.contracts import MemoryExpectation

    with pytest.raises(ValidationError):
        MemoryExpectation(min_records=1, bogus_unknown_field=5)


def test_b1_contract_reject_illegal_values():
    """结构化字段取值非法必须 fail-closed（加载期拒绝）。"""
    from home_perception.validation.contracts import MemoryExpectation

    illegal_cases = [
        {"expected_risk_level": "ULTRA"},             # 非 RISK_LEVELS
        {"expected_action_types": ["NOT_A_COMMAND"]},  # 非 COMMAND_TYPES
        {"required_modalities": ["smell"]},           # 非 ("vision","audio","identity")
    ]
    for case in illegal_cases:
        with pytest.raises(ValidationError):
            MemoryExpectation(min_records=1, **case)


def test_b1_contract_reject_duplicate_in_set_fields():
    """集合语义字段（D4 禁止精确计数）不得含重复项——重复项多半是笔误。"""
    from home_perception.validation.contracts import MemoryExpectation

    with pytest.raises(ValidationError):
        MemoryExpectation(min_records=1, expected_action_types=["LOG_ONLY", "LOG_ONLY"])
    with pytest.raises(ValidationError):
        MemoryExpectation(min_records=1, required_modalities=["vision", "vision"])


def test_b1_contract_accepts_valid_structured():
    """合法结构化字段通过契约校验（不被误伤）。"""
    from home_perception.validation.contracts import MemoryExpectation

    exp = MemoryExpectation(
        min_records=1,
        expected_risk_level="LOW",
        expected_action_types=["LOG_ONLY"],
        required_modalities=["vision"],
    )
    assert exp.expected_risk_level == "LOW"
    assert exp.expected_action_types == ["LOG_ONLY"]
    assert exp.required_modalities == ["vision"]
