"""通知适配器协议与 Mock 实现（P0-9 · 行动层）。

> **P0-9 = 行动层。** `NotificationAdapter` 负责把 WarningEvent 翻译成
> 家属 / 社区可读的消息并发送。MVP 用 `MockNotifier`（写日志）演示；
> P1 接真实短信 / App push / 社区平台。

> 严格**不**在 Notifier 里做：
> - 内容生成（消息内容由 ActionDispatcher 已构造在 payload，Notifier 只负责发送）
> - 重试（→ ActionExecutor 责任）
> - 状态翻转（→ ActionExecutor 责任）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Protocol


@dataclass
class FamilyContact:
    """家属联系方式（MVP 从 devices.yaml 读；v2 从中心 RiskTwin 拉）。"""
    elder_id: str
    name: str
    phone: str
    relation: str = "family"


class NotificationAdapter(Protocol):
    """通知协议（v2 真实通道实现此协议即可替换 Mock）。

    接口约束（ADR-0011 Decision 1）：
    - 返回 bool 而非 raise（与 MQTTPublisher 一致）
    - **不**做幂等（ActionExecutor 责任）
    - **不**做重试（ActionExecutor 责任）
    """

    def notify_family(self, contact: FamilyContact, message: str) -> bool:
        """通知家属。True 成功 / False 失败。"""
        ...

    def notify_community(self, endpoint: str, task: Dict[str, Any]) -> bool:
        """通知社区（创建工单 / 推送消息）。True 成功 / False 失败。"""
        ...


class MockNotifier:
    """Mock NotificationAdapter：只记录到内存列表，不真发送。

    失败模拟：
        notifier.fail_next = True
        assert notifier.notify_family(...) is False

    断言（测试用）：
        assert notifier.family_messages[0]["contact"].phone == "+8612345"
    """

    def __init__(self):
        self.family_messages: List[Dict[str, Any]] = []
        self.community_messages: List[Dict[str, Any]] = []
        self.fail_next: bool = False

    def notify_family(self, contact: FamilyContact, message: str) -> bool:
        if self.fail_next:
            self.fail_next = False
            return False
        self.family_messages.append({
            "contact": contact,
            "message": message,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        return True

    def notify_community(self, endpoint: str, task: Dict[str, Any]) -> bool:
        if self.fail_next:
            self.fail_next = False
            return False
        self.community_messages.append({
            "endpoint": endpoint,
            "task": task,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        return True

    @property
    def family_count(self) -> int:
        return len(self.family_messages)

    @property
    def community_count(self) -> int:
        return len(self.community_messages)

    def reset(self) -> None:
        self.family_messages.clear()
        self.community_messages.clear()
        self.fail_next = False
