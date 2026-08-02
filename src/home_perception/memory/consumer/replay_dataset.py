"""Memory Replay Dataset 加载器（M0，DESIGN-memory-replay-dataset.md §2）。

从 ``tests/fixtures/memory_replay/`` 加载 case，反序列化为类型化对象，供
``EpisodeReplayLayer`` 与测试消费。本模块只做 I/O + 反序列化，不含组装逻辑。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from home_perception.memory.consumer.contracts import (
    CurrentEvent,
    ReasoningInput,
)
from home_perception.memory.records import EpisodicRecord


@dataclass
class ReplayCase:
    """一个回放 case：历史 EpisodicRecord + 当前事件 + 期望 ReasoningInput（oracle）。"""

    name: str
    history: list[EpisodicRecord]
    current_event: CurrentEvent
    expected: ReasoningInput


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class MemoryReplayDataset:
    """加载 ``tests/fixtures/memory_replay/`` 下的回放 case。"""

    def __init__(self, root: str) -> None:
        self._root = root

    def case_names(self) -> list[str]:
        """返回所有 case 目录名（按字母序，确定性）。"""
        if not os.path.isdir(self._root):
            return []
        names = [
            d
            for d in os.listdir(self._root)
            if d.startswith("case_") and os.path.isdir(os.path.join(self._root, d))
        ]
        return sorted(names)

    def load(self, case_name: str) -> ReplayCase:
        case_dir = os.path.join(self._root, case_name)
        if not os.path.isdir(case_dir):
            raise FileNotFoundError(f"回放 case 不存在: {case_dir}")

        history_raw = _load_json(os.path.join(case_dir, "history.json"))
        current_raw = _load_json(os.path.join(case_dir, "current.json"))
        expected_raw = _load_json(os.path.join(case_dir, "expected_reasoning_input.json"))

        history = [EpisodicRecord.from_dict(d) for d in history_raw]
        current_event = CurrentEvent.from_dict(current_raw)
        expected = ReasoningInput.from_dict(expected_raw)

        return ReplayCase(
            name=case_name,
            history=history,
            current_event=current_event,
            expected=expected,
        )

    def load_all(self) -> list[ReplayCase]:
        return [self.load(name) for name in self.case_names()]


__all__ = ["MemoryReplayDataset", "ReplayCase"]
