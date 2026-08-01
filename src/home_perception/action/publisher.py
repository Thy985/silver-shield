"""MQTT Publisher 协议与 Mock 实现（P0-9 · 行动层）。

> **P0-9 = 行动层。** `MQTTPublisher` 是行动层**唯一**接触外部传输的接口。
> MVP 用 `MockPublisher`（写本地 JSONL）演示；P1 接真实 MQTT broker。
>
> 严格**不**在 Publisher 里做：
> - 业务逻辑（重试 / 幂等 / 状态翻转 → ActionExecutor 责任）
> - 序列化（payload 已是 dict，Publisher 不变 schema）
> - 重连（v2 真实 broker 才需要）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class MQTTPublisher(Protocol):
    """MQTT 发布协议（v2 真实 broker 实现此协议即可替换 Mock）。

    接口约束（ADR-0011 Decision 1）：
    - 输入：topic（str）、payload（dict，已序列化安全）
    - 输出：bool（True = 发送成功；False = 发送失败，调用方应重试）
    - **不**抛异常：失败用 bool 而非 raise（行动层 try/except 太重）
    - **不**做幂等（幂等是 ActionExecutor 责任，按 warning_id 去重）
    - **不**做重试（重试是 ActionExecutor 责任，Publisher 只管"这一次能否成功"）
    """

    def publish(self, topic: str, payload: dict[str, Any]) -> bool:
        """发布一条消息到 topic。

        Args:
            topic: MQTT topic 字符串（如 `silvershield/home/{device_id}/warning`）
            payload: 消息内容（dict 格式，调用方保证可序列化）

        Returns:
            bool: True 成功 / False 失败（调用方按需重试）
        """
        ...


class MockPublisher:
    """Mock MQTT Publisher：写本地 JSONL 文件（每行一条 JSON 消息）。

    用法（测试 / Demo）：
        pub = MockPublisher(output_path="var/mock_mqtt.jsonl")
        pub.publish("test/topic", {"foo": "bar"})
        # var/mock_mqtt.jsonl 末行 + {"topic": "test/topic", "payload": {...}, "ts": "..."}
        assert pub.published[0]["topic"] == "test/topic"

    失败模拟：
        pub.fail_next = True
        assert pub.publish(...) is False

    断言（测试用）：
        assert pub.publish_count == 1
    """

    def __init__(self, output_path: str | None = None):
        """output_path=None 时只内存收集不落盘（纯单测用）。"""
        self.output_path = Path(output_path) if output_path else None
        self.published: list[dict[str, Any]] = []
        self.fail_next: bool = False
        self._closed = False

    def publish(self, topic: str, payload: dict[str, Any]) -> bool:
        """Mock 实现：失败由 fail_next 触发；成功追加到 published + 落盘（若 output_path 设置）。"""
        if self.fail_next:
            self.fail_next = False  # 一次性失败标记
            return False

        record = {
            "topic": topic,
            "payload": payload,
            "ts": datetime.now(UTC).isoformat(),
        }
        self.published.append(record)

        if self.output_path is not None:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with self.output_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True

    @property
    def publish_count(self) -> int:
        return len(self.published)

    def reset(self) -> None:
        """清空内存（落盘文件保留）。"""
        self.published.clear()
        self.fail_next = False
