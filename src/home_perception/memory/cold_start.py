"""冷启动恢复（ADR-0024 Slice 3 · Stage E）。

> **ADR-0024 = Memory 架构。** 本模块是进程重启后的状态恢复协调器，解 TD-0027。
> 依赖 Stage C 的 `SnapshotStore` 与 `RealTimeRiskEvaluator` / `RecentBehaviorStore`。

**Cold Start Confidence 三档**（工程方案 §5.5.0）：

| 档位   | snapshot_age            | 行为                         | confidence |
| ------ | ----------------------- | ---------------------------- | ---------- |
| FRESH  | `< fresh_threshold`     | 完全恢复；下游可继续决策     | 1.0        |
| STALE  | `fresh < age <= ttl`    | 降级恢复；待新帧确认，不发警报 | 0.5      |
| DISCARD| `> ttl` / 缺失 / 损坏   | 冷启动；evaluator/store reset | 0.0      |

**关键设计**：
1. 恢复时**只恢复 active visitor**（`last_seen_at` 在 retention 内），不恢复 inactive
   visitor —— 避免 TD-0024 旧条目累积重现。
2. **STALE 档不主动发 Warning**：confidence=0.5 的恢复状态需新帧重新确认才升级为 1.0；
   避免"重启即警报"误判。
3. **confidence 单调上升**：STALE(0.5) → 新帧检测到同一 visitor → 评估器置 1.0；不允许下降。
4. 恢复后 `evict_expired()` 双保险一次，清理残留过期条目。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum

from ..analysis.realtime_risk_evaluator import RealTimeRiskEvaluator
from ..analysis.recent_behavior_store import RecentBehaviorStore
from ..core.config import MemoryConfig
from .snapshot import SnapshotStore


class ColdStartConfidence(str, Enum):
    """冷启动恢复可信度档位。"""

    FRESH = "fresh"  # age <= fresh_threshold，完全恢复
    STALE = "stale"  # fresh < age <= ttl，降级恢复
    DISCARD = "discard"  # age > ttl 或缺失/损坏，冷启动


@dataclass
class RecoveryResult:
    """冷启动恢复结果（供调用方/Pipeline 记录与监控）。"""

    recovered: bool  # True=从 snapshot 恢复（FRESH/STALE），False=冷启动（DISCARD）
    confidence: ColdStartConfidence  # 恢复可信度档位
    reason: str  # snapshot_loaded_fresh / snapshot_loaded_stale / snapshot_missing
    restored_tracks: int
    restored_visitors: int
    snapshot_age_seconds: float | None

    def as_log_fields(self) -> dict:
        """展开为 structlog 友好的扁平字段。"""
        d = asdict(self)
        d["confidence"] = self.confidence.value
        return d


class ColdStartCoordinator:
    """冷启动恢复协调器 —— Snapshot + Eviction + Confidence 协同。

    调用 ``recover(now)`` 完成一次恢复：load → 时效分档 → 恢复 / 冷启动。
    幂等：多次调用安全（每次都从 load 结果重建）。
    """

    def __init__(
        self,
        snapshot_store: SnapshotStore,
        evaluator: RealTimeRiskEvaluator,
        recent_store: RecentBehaviorStore,
        config: MemoryConfig,
    ) -> None:
        self._snapshot_store = snapshot_store
        self._evaluator = evaluator
        self._recent_store = recent_store
        self._config = config

    def recover(self, now: datetime) -> RecoveryResult:
        """进程启动时调用：尝试从 snapshot 恢复运行时状态。"""
        snapshot = self._snapshot_store.load()
        if snapshot is None:
            self._cold_start()
            return RecoveryResult(
                recovered=False,
                confidence=ColdStartConfidence.DISCARD,
                reason="snapshot_missing",
                restored_tracks=0,
                restored_visitors=0,
                snapshot_age_seconds=None,
            )

        age = (now - snapshot.snapshot_at).total_seconds()

        # 分档
        if age > self._config.snapshot_ttl_seconds:
            self._cold_start()
            return RecoveryResult(
                recovered=False,
                confidence=ColdStartConfidence.DISCARD,
                reason="snapshot_stale",
                restored_tracks=0,
                restored_visitors=0,
                snapshot_age_seconds=age,
            )

        if age <= self._config.snapshot_fresh_threshold_seconds:
            confidence = ColdStartConfidence.FRESH
            reason = "snapshot_loaded_fresh"
            conf_value = 1.0
        else:
            confidence = ColdStartConfidence.STALE
            reason = "snapshot_loaded_stale"
            conf_value = self._config.cold_start_stale_confidence

        # 恢复 evaluator（带 confidence 标记）
        self._evaluator.restore(snapshot.active_tracks, confidence=conf_value)

        # 恢复 recent_behavior（只恢复 active visitor：last_seen_at 在 retention 内）
        retention = self._config.recent_behavior_retention_seconds
        cutoff = now - timedelta(seconds=retention)
        filtered = [s for s in snapshot.recent_behavior if s.last_seen_at >= cutoff]
        self._recent_store.restore(filtered, now)

        # 双保险：evict 一次（清理恢复时可能引入的过期条目）
        self._recent_store.evict_expired(now, retention)

        return RecoveryResult(
            recovered=True,
            confidence=confidence,
            reason=reason,
            restored_tracks=len(snapshot.active_tracks),
            restored_visitors=len(filtered),
            snapshot_age_seconds=age,
        )

    def _cold_start(self) -> None:
        """冷启动：清空评估器与账本，从零开始（不补发 CLEARED）。"""
        self._evaluator.reset()
        self._recent_store.reset()


__all__ = ["ColdStartConfidence", "ColdStartCoordinator", "RecoveryResult"]
