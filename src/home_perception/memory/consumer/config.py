"""Memory Consumer 配置（C-1，DESIGN §7 O2）。

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


__all__ = ["RetrievalConfig"]
