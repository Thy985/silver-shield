"""Memory Consumer 配置（C-1 / C-2，DESIGN §7 O2）。

把召回窗口 / 上限 / 时段带宽提为可配常量，便于后续实验调整（如诈骗周期可能
需更长 lookback，老人行为可能需季节窗口），不写死在逻辑里。
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


__all__ = ["AggregationConfig", "RetrievalConfig"]
