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
- **只读返回**：`update()` / `query_window()` 产出**新构造**的只读 Mapping
  （`types.MappingProxyType`），绝不透传内部 list 引用；调用方改动返回值不影响 store 下一帧产出。
- **Snapshot hooks（ADR-0024 Slice 3 Stage C）**：新增 `snapshot()` / `restore()` /
  `evict_expired()`，供 pipeline 持久化运行时状态、进程重启后冷启动恢复（解 TD-0027）。
  内存中的 `_entries` 仍是 volatile 工作态；持久化由 pipeline 驱动（外部 JSON）。

> **命名区分**：`query_window()` 是 Stage A 既有的**查询**方法（返回 `{"visits_in_window": N}`），
> `snapshot()` 是 Stage C 新增的**持久化导出**方法（返回 `List[RecentBehaviorSnapshot]`）。
> 两者同名会冲突，故查询方法更名为 `query_window`（签名不变，历史测试同步改名）。
"""
from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Mapping

from ..common.timeutil import require_utc

if TYPE_CHECKING:
    from ..memory.snapshot import RecentBehaviorSnapshot


@dataclass
class BehaviorHistory:
    """单 visitor 的近期行为账本（替换原 `List[datetime]`，Stage C 引入）。

    - `enter_times`：滑窗内进入时刻列表（用于窗口计数）
    - `last_seen_at`：上次见到该 visitor 的时刻（新增；用于离场判定 / 恢复过滤）
    """

    enter_times: List[datetime]
    last_seen_at: datetime


class RecentBehaviorStore:
    """跨访问近期行为账本（滑窗计数，只读返回，volatile）。

    内部状态：`Dict[visitor_instance_id, BehaviorHistory]`。
    每次 `update()` 记录一次进入并统计窗口内访问次数，返回 `{"visits_in_window": N}`。
    """

    def __init__(self) -> None:
        # visitor_instance_id -> BehaviorHistory（含 enter_times + last_seen_at）
        self._entries: Dict[str, BehaviorHistory] = {}

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
        - 每次 `update` 刷新 `last_seen_at`（供离场判定 / 恢复过滤）。
        """
        if not visitor_instance_id or not str(visitor_instance_id).strip():
            raise ValueError("visitor_instance_id 不能为空")
        if window_seconds < 0:
            raise ValueError(f"window_seconds 必须 >= 0，收到 {window_seconds}")
        require_utc(enter_time, "enter_time")
        require_utc(now, "now")
        if enter_time > now:
            raise ValueError(f"enter_time ({enter_time}) 不能晚于 now ({now})")

        history = self._entries.get(visitor_instance_id)
        if history is None:
            history = BehaviorHistory(enter_times=[], last_seen_at=now)
            self._entries[visitor_instance_id] = history

        # 去重 + 追加进入时刻
        if enter_time not in history.enter_times:
            history.enter_times.append(enter_time)
        # 每次 update 刷新 last_seen_at
        history.last_seen_at = now

        cutoff = now - timedelta(seconds=window_seconds)
        in_window = [t for t in history.enter_times if t >= cutoff]
        # 清理窗口外旧记录：窗口内非空则覆写，为空则删除键（防空键累积无意义内存）
        if in_window:
            history.enter_times = in_window
        else:
            self._entries.pop(visitor_instance_id, None)

        return types.MappingProxyType({"visits_in_window": len(in_window)})

    def query_window(
        self,
        visitor_instance_id: str,
        now: datetime,
        window_seconds: float,
    ) -> Mapping[str, Any]:
        """不记录、只查询窗口内访问次数（只读返回，引用隔离）。

        与 `update()` 区别：不写入新进入记录，仅按现有账本统计。
        （原 `snapshot()` 查询方法在 Stage C 重命名为 `query_window`，避免与持久化
        导出 `snapshot()` 同名冲突。）
        """
        if window_seconds < 0:
            raise ValueError(f"window_seconds 必须 >= 0，收到 {window_seconds}")
        require_utc(now, "now")
        history = self._entries.get(visitor_instance_id)
        bucket = history.enter_times if history is not None else []
        cutoff = now - timedelta(seconds=window_seconds)
        in_window = [t for t in bucket if t >= cutoff]
        return types.MappingProxyType({"visits_in_window": len(in_window)})

    def snapshot(self) -> List["RecentBehaviorSnapshot"]:
        """导出当前 `_entries` 为可持久化快照（ADR-0024 Slice 3 Stage C）。

        返回 `List[RecentBehaviorSnapshot]`，供 `SnapshotStore` 写入 JSON。
        只导出 reconstructable 字段（enter_times / last_seen_at），不导出派生指标。
        """
        from ..memory.snapshot import RecentBehaviorSnapshot

        return [
            RecentBehaviorSnapshot(
                visitor_instance_id=vid,
                enter_times=list(history.enter_times),
                last_seen_at=history.last_seen_at,
            )
            for vid, history in self._entries.items()
        ]

    def restore(
        self,
        snapshots: List["RecentBehaviorSnapshot"],
        now: datetime,
    ) -> None:
        """从快照恢复 `_entries`（ADR-0024 Slice 3 Stage E）。

        冷启动恢复时调用方（`ColdStartCoordinator`）已按 `last_seen_at` 过滤出
        active visitor，本方法只负责重建本地状态（不重复过滤）。
        `now` 用于防御时钟回拨：`last_seen_at` 晚于 `now` 时钳制为 `now`。
        """
        require_utc(now, "now")
        self._entries.clear()
        for snap in snapshots:
            last_seen = snap.last_seen_at if snap.last_seen_at <= now else now
            self._entries[snap.visitor_instance_id] = BehaviorHistory(
                enter_times=list(snap.enter_times),
                last_seen_at=last_seen,
            )

    def evict_expired(self, now: datetime, retention_seconds: float) -> int:
        """清理超过 retention 未再出现的 visitor 条目（Stage D，解 TD-0024）。

        与滑窗语义不同：
        - 滑窗（window_seconds）：控制"访问次数"统计窗口，如最近 1h 内访问几次
        - retention（retention_seconds）：控制"该 visitor 整体保留多久"，如离场后保留 1h

        返回被清理的条目数。
        """
        if retention_seconds < 0:
            raise ValueError(f"retention_seconds 必须 >= 0，收到 {retention_seconds}")
        require_utc(now, "now")
        cutoff = now - timedelta(seconds=retention_seconds)
        expired = [vid for vid, h in self._entries.items() if h.last_seen_at < cutoff]
        for vid in expired:
            self._entries.pop(vid, None)
        return len(expired)

    def reset(self) -> None:
        """清空账本（volatile 语义：模拟重启丢弃）。"""
        self._entries.clear()

    @property
    def is_empty(self) -> bool:
        """账本是否为空（volatile 重启即空）。"""
        return not any(self._entries.values())


__all__ = ["BehaviorHistory", "RecentBehaviorStore"]
