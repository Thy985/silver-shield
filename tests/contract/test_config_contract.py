"""Config Contract（ADR-0014 前置 #5）— 锁配置校验，拒绝非法值（配置攻击）。

用户点名："long_duration_seconds: -100 或 NaN，系统不能启动或者必须明确报错，不能静默运行。"

覆盖三类配置攻击防护（用户建议扩展）：
- 类型约束：float 字段收到字符串（如 cooldown_seconds: "abc"）→ 启动失败；
- 范围约束：rule_weights 各值必须落在 [0, 1]（如 2.5 → 拒绝）；
- 枚举约束：ingestion.protocol 必须是已知 source（rtsp/hls），未知 → 拒绝。

配置攻击防护直接在 pydantic 模型层落地（core/config.py），保持 Mock / 真实实现共用同一校验。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from home_perception.core.config import IngestionConfig, RuleConfig, TrackingConfig


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


def test_rule_config_defaults_are_valid() -> None:
    """默认构造必须全绿：所有受校验字段（阈值/计数/权重）的默认值本身合法。

    守护"未来有人把默认值改成非法值（如 cooldown_seconds: float = -1.0）"——
    因这些字段在默认构造时不会被显式传入，只有默认构造成功才能证明默认值合法。
    """
    cfg = RuleConfig()  # 若任一默认值非法，validator 会在此抛 ValidationError
    for field in (
        "long_duration_seconds",
        "cooldown_seconds",
        "reset_gap_seconds",
        "frequency_window_s",
    ):
        assert getattr(cfg, field) > 0
    assert cfg.repeat_visit_count > 0
    assert all(0.0 <= w <= 1.0 for w in cfg.rule_weights.values())


def test_rule_config_rejects_bool_repeat_count() -> None:
    # 类型防护（用户建议）：bool 是 int 子类，不得被静默当作 1/0
    with pytest.raises(ValidationError):
        RuleConfig(repeat_visit_count=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_weight", [2.5, -0.1, float("nan")])
def test_rule_config_rejects_out_of_range_weights(bad_weight: float) -> None:
    # 范围约束（用户建议）：权重必须在 [0, 1]，否则规则命中强度语义被破坏
    with pytest.raises(ValidationError):
        RuleConfig(rule_weights={"LongDurationRule": bad_weight})


def test_rule_config_rejects_type_error_string_for_float() -> None:
    # 类型约束（用户建议）：float 字段收到字符串必须启动失败，不得静默 coerce
    with pytest.raises(ValidationError):
        RuleConfig(cooldown_seconds="abc")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_protocol", ["camera_xxx", "rtmp", "onvif", ""])
def test_ingestion_rejects_unknown_protocol(bad_protocol: str) -> None:
    # 枚举约束（用户建议）：未知 source 不应静默接受
    with pytest.raises(ValidationError):
        IngestionConfig(protocol=bad_protocol)


def test_tracking_config_rejects_nonpositive_absence_gap() -> None:
    with pytest.raises(ValidationError):
        TrackingConfig(absence_gap_s=-1.0)
    with pytest.raises(ValidationError):
        TrackingConfig(absence_gap_s=float("nan"))
