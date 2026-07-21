"""WebSocket 端点与极简广播（ADR-0015 §2.3 / §3）。

第一版单演示连接即可（ADR-0015 §6 明确不做多用户连接管理）。
设计为「最新帧覆盖旧帧」语义：广播时只保留最新一帧，避免慢客户端积压。

上行消息（Dashboard → 网关）：
    {"type": "action", "warning_id": "...", "operator": "family"|"community", "action": "..."}
    → 写入 DemoStateStore

下行消息（网关 → Dashboard）：
    {"type": "frame", "view": {...frame_result_to_view...}, "state": {...snapshot...}}
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket

from .state import DemoStateStore


class ConnectionHub:
    """极简 WebSocket 连接管理（单演示连接；第一版不做房间/多用户）。

    - ``active``：当前连接集合（通常 1 个）。
    - ``broadcast``：并发推送；任一连接异常即移除。
    - 线程安全：所有操作经 ``asyncio.Lock``。
    """

    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.active.discard(ws)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """向所有活跃连接推送 JSON 消息；失败的连接静默移除。"""
        text = json.dumps(message, ensure_ascii=False)
        dead: list[WebSocket] = []
        # 拷贝集合避免迭代中修改
        async with self._lock:
            conns = list(self.active)
        for ws in conns:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.active.discard(ws)

    async def send_to(self, ws: WebSocket, message: Dict[str, Any]) -> None:
        """向单个连接推送 JSON 消息（用于新连接首连 ``snapshot`` 等）。

        失败的连接静默移除（视为已断开）。
        """
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
        except Exception:
            async with self._lock:
                self.active.discard(ws)


async def handle_upstream(
    ws: WebSocket,
    raw: str,
    store: DemoStateStore,
) -> Optional[Dict[str, Any]]:
    """处理 Dashboard 上行的 JSON 消息。

    目前仅支持 ``type=action``：解析 warning_id / operator / action → 写入 DemoStateStore。

    Returns:
        处理结果 dict（供网关回送 ACK / 广播状态更新），或 None（非 action 消息）。
    """
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "error", "error": "invalid_json"}

    if msg.get("type") != "action":
        return None

    warning_id = msg.get("warning_id")
    operator = msg.get("operator", "")
    action = msg.get("action", "")

    if not warning_id or not isinstance(warning_id, str):
        return {"type": "error", "error": "missing_warning_id"}

    # action → 目标状态映射（与 ADR-0015 §2.5 状态机一致）
    # family 的 [认识] / [通知社区] → family_handled
    # community 的 [接受] / [完成] → community_done
    target_status: Optional[str] = None
    if operator == "family":
        target_status = "family_handled"
    elif operator == "community":
        target_status = "community_done"

    if target_status is None:
        return {"type": "error", "error": f"unknown_operator:{operator!r}"}

    try:
        updated = await store.upsert(warning_id, status=target_status, operator=operator)
    except ValueError as exc:
        return {"type": "error", "error": "invalid_transition", "detail": str(exc)}

    return {"type": "action_ack", "action": action, "updated": updated}
