"""Memory Consumer 配置（C-1 / C-2 / C-4，DESIGN §7 O2）。

把召回窗口 / 上限 / 时段带宽 / 触发门槛提为可配常量，便于后续实验调整（如诈骗
周期可能需更长 lookback，老人行为可能需季节窗口，灰度期可能需收紧或放宽触发），
不写死在逻辑里。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievalConfig:
    """规则召回可配置参数（C-1 / DESIGN §7 O2）。"""

    lookback_days: int = 30
    max_records: int = 100
    same_time_band_hours: int = 3

    def __post_init__(self) -> None:
        if self.lookback_days <= 0:
            raise ValueError("RetrievalConfig.lookback_days 必须 > 0")
        if self.max_records <= 0:
            raise ValueError("RetrievalConfig.max_records 必须 > 0")
        if self.same_time_band_hours < 0:
            raise ValueError("RetrievalConfig.same_time_band_hours 必须 >= 0")


@dataclass
class AggregationConfig:
    """规则聚合可配置参数（C-2 / DESIGN §3.2 O2）。

    置信度阈值与夜间窗提为可配常量，便于后续实验调整（如诈骗周期可能需更长
    观察窗、老人行为可能需季节窗口），不写死在逻辑里。
    """

    cold_start_threshold: int = 5      # n < 此值 -> cold_start
    weak_pattern_threshold: int = 30   # cold_start_threshold <= n < 此值 -> weak_pattern；否则 stable
    night_start_hour: int = 22          # 夜间起点（含）
    night_end_hour: int = 6            # 夜间终点（不含，环形）
    min_records_for_pattern: int = 2    # 低于此样本数不产出 RiskPattern

    def __post_init__(self) -> None:
        if self.cold_start_threshold <= 0:
            raise ValueError("AggregationConfig.cold_start_threshold 必须 > 0")
        if self.weak_pattern_threshold <= self.cold_start_threshold:
            raise ValueError(
                "AggregationConfig.weak_pattern_threshold 必须 > cold_start_threshold"
            )
        if not (0 <= self.night_end_hour < self.night_start_hour <= 23):
            raise ValueError(
                "AggregationConfig 夜间窗必须 0 <= night_end_hour < night_start_hour <= 23"
            )
        if self.min_records_for_pattern < 1:
            raise ValueError("AggregationConfig.min_records_for_pattern 必须 >= 1")


@dataclass
class ConsumerTriggerConfig:
    """模式 B 触发门控可配参数（C-4 / ADR-0025 §3.10 / DESIGN §4.1）。

    Phase 1 触发条件 = ``risk_level ∈ enabled_levels`` **或**
    （``trigger_on_known_visitor`` 且该访客既往 episode 数 > 0）。

    「已知访客再现」是刻意放宽（ADR-0025 §3.10 修订）：若只在 HIGH 触发，Consumer
    会沦为事后解释系统——风险已经升起才去理解历史，拿不到「提前理解」的价值。
    灰度期可通过本配置收紧（如 ``enabled_levels=("HIGH",)``、
    ``trigger_on_known_visitor=False``）观察触发率与误触率。
    """

    enabled_levels: tuple[str, ...] = ("MEDIUM", "HIGH")
    trigger_on_known_visitor: bool = True

    def __post_init__(self) -> None:
        allowed = ("LOW", "MEDIUM", "HIGH")
        for level in self.enabled_levels:
            if level not in allowed:
                raise ValueError(
                    f"ConsumerTriggerConfig.enabled_levels 只能取 {allowed}，收到 {level!r}"
                )
        if not self.enabled_levels and not self.trigger_on_known_visitor:
            raise ValueError(
                "ConsumerTriggerConfig 至少需保留一种触发条件："
                "enabled_levels 非空 或 trigger_on_known_visitor=True"
                "（两者同时关闭等价于永不触发，应改用 memory.consumer_enabled=false）"
            )


__all__ = ["AggregationConfig", "ConsumerTriggerConfig", "RetrievalConfig"]
