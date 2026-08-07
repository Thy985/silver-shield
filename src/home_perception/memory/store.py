"""Memory 存储后端（ADR-0024 Slice 5 · Episodic Storage）。

> v1 存储选择：`InMemoryStore`（内存）。重点不是性能，是链路验证。
>
> ⚠️ v1 持久化不对称：Episodic 内存 only，重启即丢。
> Phase 5 Agent 接入前必须迁移到 SQLite。

**I2 Monotonicity**（§5.6）：
- 新 record_id → 插入
- 已存在 + 字段相同 → 幂等命中，不报错
- 已存在 + 字段不同 → 抛 InvariantViolationError
- 已存在 + 追加 corrections → 允许（I2 例外）
"""

from __future__ import annotations

from dataclasses import fields

from .records import (
    EpisodicRecord,
    MemoryStatus,
    ShortTermRecord,
)


class InvariantViolationError(Exception):
    """Memory 不变量违反异常。"""


class MemoryStore:
    """Memory Object 存储后端抽象（v1 仅 InMemoryStore 实现）。"""

    def upsert_short_term(self, record: ShortTermRecord) -> bool:
        raise NotImplementedError

    def upsert_episodic(self, record: EpisodicRecord) -> bool:
        raise NotImplementedError

    def get_episodic_by_visitor(self, visitor_instance_id: str) -> list[EpisodicRecord]:
        raise NotImplementedError

    def get_active_episodic(self) -> list[EpisodicRecord]:
        raise NotImplementedError

    def all_episodic(self) -> list[EpisodicRecord]:
        """全部 EpisodicRecord（ADR-0028 D5，跨模态关联扫描用）。

        供 ``CrossModalLinkRuntime`` 全量扫描建边；v1 内存实现返回全部，未来
        SQLite/远程后端须提供 LIMIT + 游标化扫描（避免 N+1 全表扫描，ADR-0028 §D5
        Performance Boundary）。episode ≥ 10_000 时须响应缩放告警契约。
        """
        raise NotImplementedError

    def short_term_count(self) -> int:
        """当前保留的 ShortTermRecord 条数（工作记忆规模）。

        公共观测口：短期记忆应为 O(活跃 visitor) 而非 O(帧数)，Slice 6 压缩比
        验收（§8.8.1）需要读取该规模。提供公共方法以免调用方触碰后端私有结构
        （v2 迁 SQLite 时实现换成 `SELECT COUNT(*)`，调用方无感）。
        """
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError


class InMemoryStore(MemoryStore):
    """v1 内存后端：进程内 dict，重启即空。"""

    def __init__(self) -> None:
        self._short_term: dict[str, ShortTermRecord] = {}
        self._episodic: dict[str, EpisodicRecord] = {}

    def upsert_short_term(self, record: ShortTermRecord) -> bool:
        is_new = record.record_id not in self._short_term
        self._short_term[record.record_id] = record
        return is_new

    def upsert_episodic(self, record: EpisodicRecord) -> bool:
        if record.record_id in self._episodic:
            existing = self._episodic[record.record_id]
            if self._fields_differ(existing, record):
                raise InvariantViolationError(
                    f"I2: EpisodicRecord {record.record_id} 已存在，禁止覆写非 correction 字段"
                )
            return False
        self._episodic[record.record_id] = record
        return True

    def get_episodic_by_visitor(self, visitor_instance_id: str) -> list[EpisodicRecord]:
        result = [
            ep for ep in self._episodic.values() if ep.visitor_instance_id == visitor_instance_id
        ]
        result.sort(key=lambda e: e.enter_time)
        return result

    def get_active_episodic(self) -> list[EpisodicRecord]:
        return [ep for ep in self._episodic.values() if ep.memory_status == MemoryStatus.ACTIVE]

    def all_episodic(self) -> list[EpisodicRecord]:
        return list(self._episodic.values())

    def short_term_count(self) -> int:
        return len(self._short_term)

    @staticmethod
    def _fields_differ(a: EpisodicRecord, b: EpisodicRecord) -> bool:
        for f in fields(a):
            name = f.name
            if name in ("created_at", "corrections"):
                continue
            if getattr(a, name) != getattr(b, name):
                return True
        return False

    def snapshot(self) -> dict:
        return {
            "short_term": [r.to_dict() for r in self._short_term.values()],
            "episodic": [r.to_dict() for r in self._episodic.values()],
        }

    def clear(self) -> None:
        self._short_term.clear()
        self._episodic.clear()


__all__ = ["InMemoryStore", "InvariantViolationError", "MemoryStore"]
