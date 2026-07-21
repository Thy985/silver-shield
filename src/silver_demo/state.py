"""DemoStateStore — 进程内反馈闭环状态（ADR-0015 §2.5）。

第一版仅内存 dict，无数据库 / 无登录 / 无权限。演示重启即重置。

状态流转（与 ADR-0015 §2.5 一致）：
    pending → family_handled → community_done

严格规则：
- **不回写** ``WarningEvent`` / ``ActionCommand``（冻结对象只读消费）。
- 状态翻转只发生在本 Store 内。
- 按 ``warning_id`` 幂等映射（同一 warning 多次上行只记一条）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional


# 合法状态（与 ADR-0015 §2.5 一致）
VALID_STATUSES = ("pending", "family_handled", "community_done")

# 合法操作者
VALID_OPERATORS = ("family", "community")

# 状态翻转规则（单向流转，不可逆）
TRANSITIONS: Dict[str, frozenset] = {
    "pending": frozenset({"family_handled"}),
    "family_handled": frozenset({"community_done"}),
    "community_done": frozenset(),  # 终态
}


class DemoStateStore:
    """进程内反馈闭环状态存储。

    线程安全：所有读写经 ``asyncio.Lock`` 保护（网关在事件循环内调用）。
    单演示连接即可，不做多用户/跨会话同步（ADR-0015 §6 明确不做）。
    """

    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, warning_id: str, status: str = "pending", operator: str = "") -> Dict[str, Any]:
        """按 warning_id 幂等插入或更新状态。

        - 首次见到的 warning_id → 初始化为 pending。
        - 已存在的 warning_id → 校验翻转合法性后更新。
        - 非法翻转 → 抛 ValueError（不静默接受，便于发现前端 bug）。

        Returns:
            更新后的状态 dict ``{"warning_id", "status", "operator"}``。
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"status 必须是 {VALID_STATUSES} 之一，收到 {status!r}")

        async with self._lock:
            entry = self._state.get(warning_id)
            if entry is None:
                # 首次：初始化为 pending（业务意义：新 warning 默认未处理）
                entry = {"warning_id": warning_id, "status": "pending", "operator": operator}
                self._state[warning_id] = entry
                return dict(entry)

            # 已存在：校验翻转
            cur = entry["status"]
            if status == cur:
                # 幂等：相同状态重复上行，只更新 operator
                entry["operator"] = operator or entry["operator"]
                return dict(entry)
            if status not in TRANSITIONS.get(cur, frozenset()):
                raise ValueError(
                    f"warning_id={warning_id!r} 状态不能从 {cur!r} 翻转到 {status!r}；"
                    f"允许的下一状态：{sorted(TRANSITIONS.get(cur, frozenset()))}"
                )
            entry["status"] = status
            entry["operator"] = operator or entry["operator"]
            return dict(entry)

    async def get(self, warning_id: str) -> Optional[Dict[str, Any]]:
        """读取单条状态；不存在返回 None。"""
        async with self._lock:
            entry = self._state.get(warning_id)
            return dict(entry) if entry else None

    async def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """返回全量状态快照（供 Dashboard 行动闭环区展示）。"""
        async with self._lock:
            return {wid: dict(e) for wid, e in self._state.items()}

    async def clear(self) -> None:
        """清空所有状态（演示重启场景用；正常退出无需调用，进程即重置）。"""
        async with self._lock:
            self._state.clear()
