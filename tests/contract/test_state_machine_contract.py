"""State Machine Contract（ADR-0014 Level 1/2）— 锁状态机合法翻转，禁止非法跳变。

只测"系统承诺的状态转移规则"。攻击性测试的目标之一：非法状态跳变必须被拒绝，
不得静默翻状态污染下游。

- WarningEvent.status：决策生命周期（CREATED→PENDING→CONFIRMED→RESOLVED/REJECTED）
- ActionCommand.status：执行层内部状态机（独立，不污染 WarningEvent.status）
"""
from __future__ import annotations

import pytest

from home_perception.action.command import (
    COMMAND_STATUSES,
    WARNING_TRANSITIONS,
    assert_transition_warning,
    can_transition_warning,
)

# WarningEvent 合法转移表（ADR-0010 / action/command.py WARNING_TRANSITIONS）
LEGAL_WARNING_TRANSITIONS = {
    "CREATED": {"PENDING", "REJECTED"},
    "PENDING": {"CONFIRMED", "RESOLVED", "REJECTED"},
    "CONFIRMED": {"RESOLVED", "REJECTED"},
    "RESOLVED": set(),  # 终态
    "REJECTED": set(),  # 终态
}


def test_warning_transition_table_frozen():
    """转移表本身冻结，作为契约基线。"""
    assert WARNING_TRANSITIONS == LEGAL_WARNING_TRANSITIONS


@pytest.mark.parametrize(
    "frm,to,ok",
    [
        ("CREATED", "PENDING", True),
        ("CREATED", "CONFIRMED", False),  # 禁止：CREATED→CONFIRMED
        ("CREATED", "RESOLVED", False),  # 禁止：CREATED→RESOLVED（用户点名攻击）
        ("CREATED", "REJECTED", True),
        ("PENDING", "CONFIRMED", True),
        ("PENDING", "RESOLVED", True),
        ("PENDING", "REJECTED", True),
        ("CONFIRMED", "RESOLVED", True),
        ("CONFIRMED", "REJECTED", True),
        ("RESOLVED", "PENDING", False),  # 终态不可回退
        ("REJECTED", "PENDING", False),  # 终态不可回退
        ("RESOLVED", "CONFIRMED", False),
    ],
)
def test_warning_transition_rules(frm: str, to: str, ok: bool) -> None:
    """合法跳变允许；非法跳变拒绝（assert_transition_warning 抛 ValueError）。"""
    assert can_transition_warning(frm, to) is ok
    if not ok:
        with pytest.raises(ValueError):
            assert_transition_warning(frm, to)


def test_action_command_status_is_internal_only():
    """ActionCommand.status 是执行层内部状态机，独立于 WarningEvent.status。

    两端状态机互不污染（ADR-0011 边界）。
    """
    assert set(COMMAND_STATUSES) == {
        "PENDING",
        "DONE",
        "FAILED",
        "RETRYING",
        "GIVEN_UP",
    }
