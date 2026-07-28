"""Snapshot 持久化（ADR-0024 Slice 3 · Stage C，解 TD-0027）。

> **ADR-0024 = Memory 架构。** 本模块实现运行时状态的 JSON 持久化 + 原子读回，
> 供进程重启后的冷启动恢复（Stage E）消费。
>
> **Snapshot stores reconstructable state, not derived metrics**（ADR-0024 §3.7）：
> 只存无法重算的字段（visitor_instance_id / phase / raised_signal_id / raised_at /
> first_seen / last_seen_at），不存派生指标（dwell_seconds / is_odd_hour / track_id /
> proximity_score / risk_score）。

**原子写**：先写 ``<path>.tmp``，再 ``os.replace`` 到目标路径——crash 时最多残留
半写的 .tmp，不会破坏既有 ``<path>`` 文件。

**冷启动语义**：``load()`` 对「文件缺失 / JSON 损坏 / schema 不符」一律返回 ``None``，
调用方据此走冷启动（reset），不抛异常、不阻塞启动。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ActiveTrackSnapshot:
    """单主体风险状态机快照（reconstructable only）。"""

    visitor_instance_id: str
    phase: str  # RiskPhase.value
    raised_signal_id: Optional[str]
    raised_at: Optional[datetime]
    first_seen: datetime
    last_seen_at: datetime


@dataclass
class RecentBehaviorSnapshot:
    """RecentBehaviorStore 单 visitor 快照。"""

    visitor_instance_id: str
    enter_times: List[datetime]  # 窗口内进入时刻列表
    last_seen_at: datetime


@dataclass
class RuntimeSnapshot:
    """运行时整体快照（写入 JSON 文件）。"""

    snapshot_id: str  # uuid4，每次写入新生成
    snapshot_at: datetime  # 写入时刻（UTC）
    schema_version: int = 1
    active_tracks: List[ActiveTrackSnapshot] = field(default_factory=list)
    recent_behavior: List[RecentBehaviorSnapshot] = field(default_factory=list)
    # 不含 BehaviorState derived 字段，重启后由 BehaviorBuilder 重算


def _parse_dt(value: str) -> datetime:
    """解析 ISO 8601 时间戳（兼容 +00:00 与 Z）。"""
    return datetime.fromisoformat(value)


class SnapshotStore:
    """JSON 文件原子写的 Snapshot 持久化后端。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._tmp_path = self._path.with_suffix(".tmp")

    def save(self, snapshot: RuntimeSnapshot) -> None:
        """原子写：先写 .tmp，再 replace。防 crash 时半写破坏既有文件。

        datetime 字段显式转 ISO 字符串（``asdict`` 不会递归序列化 datetime，
        直接 ``json.dump`` 会抛 ``TypeError``）。
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_at": snapshot.snapshot_at.isoformat(),
            "schema_version": snapshot.schema_version,
            "active_tracks": [
                {
                    "visitor_instance_id": s.visitor_instance_id,
                    "phase": s.phase,
                    "raised_signal_id": s.raised_signal_id,
                    "raised_at": s.raised_at.isoformat() if s.raised_at else None,
                    "first_seen": s.first_seen.isoformat(),
                    "last_seen_at": s.last_seen_at.isoformat(),
                }
                for s in snapshot.active_tracks
            ],
            "recent_behavior": [
                {
                    "visitor_instance_id": s.visitor_instance_id,
                    "enter_times": [t.isoformat() for t in s.enter_times],
                    "last_seen_at": s.last_seen_at.isoformat(),
                }
                for s in snapshot.recent_behavior
            ],
        }
        with open(self._tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(self._tmp_path, self._path)  # Windows/Linux 均原子

    def load(self) -> Optional[RuntimeSnapshot]:
        """读 snapshot；不存在 / 解析失败返回 None（视为冷启动）。"""
        if not self._path.exists():
            return None
        try:
            with open(self._path, encoding="utf-8") as f:
                payload = json.load(f)
            return self._deserialize(payload)
        except (json.JSONDecodeError, KeyError, ValueError):
            # 损坏的 snapshot 视为冷启动，不阻塞启动
            return None

    def _deserialize(self, payload: Dict[str, Any]) -> RuntimeSnapshot:
        active_tracks = [
            ActiveTrackSnapshot(
                visitor_instance_id=s["visitor_instance_id"],
                phase=s["phase"],
                raised_signal_id=s.get("raised_signal_id"),
                raised_at=_parse_dt(s["raised_at"]) if s.get("raised_at") else None,
                first_seen=_parse_dt(s["first_seen"]),
                last_seen_at=_parse_dt(s["last_seen_at"]),
            )
            for s in payload.get("active_tracks", [])
        ]
        recent_behavior = [
            RecentBehaviorSnapshot(
                visitor_instance_id=s["visitor_instance_id"],
                enter_times=[_parse_dt(t) for t in s.get("enter_times", [])],
                last_seen_at=_parse_dt(s["last_seen_at"]),
            )
            for s in payload.get("recent_behavior", [])
        ]
        return RuntimeSnapshot(
            snapshot_id=payload["snapshot_id"],
            snapshot_at=_parse_dt(payload["snapshot_at"]),
            schema_version=payload.get("schema_version", 1),
            active_tracks=active_tracks,
            recent_behavior=recent_behavior,
        )


__all__ = [
    "ActiveTrackSnapshot",
    "RecentBehaviorSnapshot",
    "RuntimeSnapshot",
    "SnapshotStore",
]
