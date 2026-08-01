"""事件发布：按 docs/06_api_contract.md 信封上报 VisitorEvent。

默认 MQTT；中心不可达时本地环形缓冲，恢复后补发（风险 T7）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..analysis.perception import PerceptionEvent


class Publisher(ABC):
    @abstractmethod
    def publish(self, event: PerceptionEvent) -> None: ...


class MQTTPublisher(Publisher):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 1883,
        topic: str = "silvershield/home/{device_id}/events",
        buffer_enabled: bool = True,
        max_items: int = 200,
    ):
        self.host = host
        self.port = port
        self.topic = topic
        self.buffer_enabled = buffer_enabled
        self.max_items = max_items

    def publish(self, event: PerceptionEvent) -> None:
        # TODO(Phase 1): 组装 Envelope（schema_version/source/device_id/events），
        # paho.mqtt 发布到 topic.format(device_id=event.device_id)；
        # 失败则写入本地环形缓冲，恢复后补发。
        raise NotImplementedError("Phase 1: 按 06_api_contract 上报 + 离线缓冲")
