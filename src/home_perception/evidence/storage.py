"""证据存储后端：本地文件系统（默认）或对象存储（COS，增强版）。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EvidenceStorage(ABC):
    @abstractmethod
    def save(self, data: bytes, name: str) -> str:
        """保存字节数据，返回可引用 URI。"""
        ...


class LocalStorage(EvidenceStorage):
    def __init__(self, root: str = "data/evidence"):
        self.root = root

    def save(self, data: bytes, name: str) -> str:
        # TODO(Phase 1): 写入 self.root/日期/name，返回相对路径
        raise NotImplementedError("Phase 1: 本地落盘存储")
