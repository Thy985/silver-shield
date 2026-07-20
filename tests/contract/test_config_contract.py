"""Config Contract（ADR-0014 前置 #5）— 锁配置校验，拒绝非法值（配置攻击）。

用户点名："long_duration_seconds: -100 或 NaN，系统不能启动或者必须明确报错，不能静默运行。"

配置攻击防护直接在 pydantic 模型层落地（core/config.py），保持 Mock / 真实实现共用同一校验。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from home_perception.core.config import RuleConfig, TrackingConfig


@pytest.mark.parametrize("bad", [-100.0, 0.0, float("nan")])
def test_rule_config_rejects_nonpositive_or_nan_long_duration(bad: float) -> None:
    with pytest.raises(ValidationError):
        RuleConfig(long_duration_seconds=bad)


@pytest.mark.parametrize("bad", [-100.0, 0.0, float("nan")])
def test_rule_config_rejects_nonpositive_or_nan_cooldown(bad: float) -> None:
    with pytest.raises(ValidationError):
        RuleConfig(cooldown_seconds=bad)


@pytest.mark.parametrize("bad", [-100.0, 0.0, float("nan")])
def test_rule_config_rejects_nonpositive_or_nan_reset_gap(bad: float) -> None:
    with pytest.raises(ValidationError):
        RuleConfig(reset_gap_seconds=bad)


@pytest.mark.parametrize("bad", [-100.0, 0.0, float("nan")])
def test_rule_config_rejects_nonpositive_or_nan_frequency_window(bad: float) -> None:
    with pytest.raises(ValidationError):
        RuleConfig(frequency_window_s=bad)


@pytest.mark.parametrize("bad", [-1, 0])
def test_rule_config_rejects_nonpositive_repeat_count(bad: int) -> None:
    with pytest.raises(ValidationError):
        RuleConfig(repeat_visit_count=bad)


def test_rule_config_accepts_valid() -> None:
    cfg = RuleConfig(long_duration_seconds=120.0, repeat_visit_count=2)
    assert cfg.long_duration_seconds == 120.0
    assert cfg.repeat_visit_count == 2


def test_tracking_config_rejects_nonpositive_absence_gap() -> None:
    with pytest.raises(ValidationError):
        TrackingConfig(absence_gap_s=-1.0)
    with pytest.raises(ValidationError):
        TrackingConfig(absence_gap_s=float("nan"))
