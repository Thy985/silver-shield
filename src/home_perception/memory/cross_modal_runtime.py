"""跨模态关联运行时（ADR-0028 D4）—— episode 落库后自动建边的接线层。

在 ``MemoryHook.record`` 落库成功后触发：全量扫描 ``MemoryStore`` →
``CrossModalLinker`` 产出候选边 → ``CrossModalLinkStore.add``（悬空引用校验）
写入边索引。视觉与音频两路共用 ``MemoryHook.record``，故本运行时自动覆盖两路。

**硬约束（ADR-0028）**：

- **只读 MemoryRecord（C2）**：只调 ``store.all_episodic()`` 读全量，绝不写 Episodic；
- **只写边索引**：产物是 ``CrossModalLink``（独立索引，非 MemoryRecord），写
  ``CrossModalLinkStore``；
- **失败隔离（AGENTS.md §2.5）**：建边任何异常 → 记日志 + 跳过该边，绝不向上抛
  （不影响 episode 落库主链路）；**失败不计入 ``metrics.errors``**（D4 review：
  errors 属 Memory 落库通道契约，建边是旁路增量）；
- **可选注入，零行为变化**：``MemoryHook`` 未注入本组件时，落库行为与历史逐字段一致；
- **幂等**：``link_id`` 确定性 → ``CrossModalLinkStore.add`` 同内容幂等（不重复建边）；
- **Performance Boundary（D5）**：episode ≥ 10_000 时按缩放告警契约降级跳过本轮建边
  （v1 只记日志，索引迁移机制归开放项）。
"""

from __future__ import annotations

from home_perception.common.logging import get_logger

from .cross_modal_link import (
    CrossModalLink,
    CrossModalLinker,
    CrossModalLinkStore,
)
from .records import EpisodicRecord
from .store import MemoryStore

log = get_logger(__name__)

# ADR-0028 D5 Performance Boundary：全量扫描 O(n²) 可接受的上限（硬边界，写死防遗忘）
CROSS_MODAL_SCALE_THRESHOLD = 10_000


class CrossModalLinkRuntime:
    """跨模态关联运行时：episode 落库后自动扫描建边（ADR-0028 D4）。

    只读 EpisodicRecord（C2），只写 CrossModalLinkStore（边索引，非 MemoryRecord）；
    失败隔离：建边异常只记日志，绝不阻断落库主链路（AGENTS.md §2.5），且不计入
    ``metrics.errors``（errors 属 Memory 落库通道契约）。
    """

    def __init__(
        self,
        store: MemoryStore,
        link_store: CrossModalLinkStore,
        linker: CrossModalLinker | None = None,
        *,
        min_overlap_seconds: float = 0.0,
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._link_store = link_store
        self._linker = linker or CrossModalLinker(
            min_overlap_seconds=min_overlap_seconds
        )
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def link_count(self) -> int:
        return self._link_store.link_count()

    def on_episode_recorded(self, record: EpisodicRecord) -> list[CrossModalLink]:
        """落库后触发：全量扫描 → linker → link_store.add（悬空校验）。

        返回本次**新写入**的边（空列表 = 无新关联 / 未启用 / 缩放降级）。
        失败隔离：单边 add 异常 → 记日志跳过，绝不向上抛。
        """
        if not self._enabled:
            return []
        try:
            episodes = self._store.all_episodic()
        except Exception as exc:  # noqa: BLE001  # 全量取数失败：降级跳过本轮
            log.warning(
                "cross_modal.runtime.scan_failed", error=str(exc), record_id=record.record_id
            )
            return []
        # D5 Performance Boundary：超阈值降级本轮建边（v1 只告警，迁移机制归开放项）
        if len(episodes) >= CROSS_MODAL_SCALE_THRESHOLD:
            log.warning(
                "cross_modal.runtime.scale_limit",
                n_episodes=len(episodes),
                threshold=CROSS_MODAL_SCALE_THRESHOLD,
                note="episode ≥ 10_000：触发索引迁移契约，本轮全量扫描降级跳过",
            )
            return []
        try:
            candidates = self._linker.link(episodes)
        except Exception as exc:  # noqa: BLE001  # linker 异常：降级（不崩落库链路）
            log.warning(
                "cross_modal.runtime.link_failed", error=str(exc), record_id=record.record_id
            )
            return []
        known_episode_ids = {ep.record_id for ep in episodes}
        written: list[CrossModalLink] = []
        for link in candidates:
            try:
                is_new, _ = self._link_store.add(link, known_episode_ids)
            except Exception as exc:  # noqa: BLE001  # 单边失败（含悬空引用）→ 跳过
                log.warning(
                    "cross_modal.runtime.add_failed",
                    link_id=link.link_id,
                    error=str(exc),
                )
                continue
            if is_new:
                written.append(link)
        return written


__all__ = [
    "CROSS_MODAL_SCALE_THRESHOLD",
    "CrossModalLinkRuntime",
]
