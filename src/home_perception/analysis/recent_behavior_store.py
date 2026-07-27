"""近期行为账本（RecentBehaviorStore）— ADR-0021 State Layer 跨访问统计（Migration Stage A）。

> **ADR-0021 = 实时风险状态流。** 本模块维护**跨访问**的近期行为账本，是 `BehaviorState`
> （纯当前生命周期态）之外、供 `RealtimeContext.recent_behavior` 使用的**历史统计**来源。
> Stage A 边界：只加类型 + 契约测试，不接入 pipeline。

**职责（ADR-0021 §3.2 纯实时边界）**：
`visits_in_window` 描述"这个访客近期来过几次"，属于跨生命周期统计，语义上归 Memory / History，
**不能**塞进 `BehaviorState`（否则状态对象背负两个时间尺度）。故独立成 `RecentBehaviorStore`。

**关键属性**：
- **滑窗**：`update()` 按时间窗口 `[now - window_seconds, now]` 统计进入次数（含当前进行中这次）。
- **track_key = visitor_instance_id**：用稳定 UUID 主键，而非会复用的 `track_id`（防串号，见 ADR-0006）。
- **只读返回**：`update()` 产出**新构造**的只读 Mapping（`types.MappingProxyType`），绝不透传内部 list 引用；
  调用方改动返回值不影响 store 下一帧产出（引用隔离）。
- **volatile**：进程重启即空（无持久化），与 `BehaviorState` 同为 Working Memory。
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping

from ..common.timeutil import require_utc


class RecentBehaviorStore:
    """跨访问近期行为账本（滑窗计数，只读返回，volatile）。

    内部状态：`Dict[visitor_instance_id, List[enter_time]]`。
    每次 `update()` 记录一次进入并统计窗口内访问次数，返回 `{"visits_in_window": N}`。
    """

    def __init__(self) -> None:
        # visitor_instance_id -> 进入时刻列表（UTC datetime）
        self._entries: Dict[str, List[datetime]] = {}

    def update(
        self,
        visitor_instance_id: str,
        enter_time: datetime,
        now: datetime,
        window_seconds: float,
    ) -> Mapping[str, Any]:
        """记录一次进入并统计窗口内访问次数，返回只读 `{"visits_in_window": N}`。

        参数：
        - `visitor_instance_id`：稳定主键（非 track_id）
        - `enter_time`：本次进入时刻（datetime UTC）
        - `now`：当前时刻（datetime UTC），窗口右界
        - `window_seconds`：窗口长度（float 秒）

        语义：
        - 同一 `enter_time` 重复调用（同帧重处理）不会重复计数（去重）。
        - 返回值是新构造的 `MappingProxyType`，引用隔离；调用方无法改动内部状态。
        - 窗口外的旧进入记录会被清理（防无界增长），但不影响当前窗口计数。
        """
        if not visitor_instance_id or not str(visitor_instance_id).strip():
            raise ValueError("visitor_instance_id 不能为空")
        if window_seconds < 0:
            raise ValueError(f"window_seconds 必须 >= 0，收到 {window_seconds}")
        require_utc(enter_time, "enter_time")
        require_utc(now, "now")
        if enter_time > now:
            raise ValueError(f"enter_time ({enter_time}) 不能晚于 now ({now})")

        bucket = self._entries.setdefault(visitor_instance_id, [])
        if enter_time not in bucket:
            bucket.append(enter_time)

        cutoff = now - timedelta(seconds=window_seconds)
        in_window = [t for t in bucket if t >= cutoff]
        # 清理窗口外旧记录：窗口内非空则覆写，为空则删除键（防空键累积无意义内存）
        if in_window:
            self._entries[visitor_instance_id] = in_window
        else:
            self._entries.pop(visitor_instance_id, None)

        return types.MappingProxyType({"visits_in_window": len(in_window)})

    def snapshot(
        self,
        visitor_instance_id: str,
        now: datetime,
        window_seconds: float,
    ) -> Mapping[str, Any]:
        """不记录、只查询窗口内访问次数（只读返回，引用隔离）。

        与 `update()` 区别：不写入新进入记录，仅按现有账本统计。
        """
        if window_seconds < 0:
            raise ValueError(f"window_seconds 必须 >= 0，收到 {window_seconds}")
        require_utc(now, "now")
        bucket = self._entries.get(visitor_instance_id, [])
        cutoff = now - timedelta(seconds=window_seconds)
        in_window = [t for t in bucket if t >= cutoff]
        return types.MappingProxyType({"visits_in_window": len(in_window)})

    def reset(self) -> None:
        """清空账本（volatile 语义：模拟重启丢弃）。"""
        self._entries.clear()

    @property
    def is_empty(self) -> bool:
        """账本是否为空（volatile 重启即空）。"""
        return not any(self._entries.values())
