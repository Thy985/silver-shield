"""Memory Consumer Layer（ADR-0025）。

本包是 Memory 消费侧（只读），与存储侧（``memory/policy`` / ``episode_builder`` /
``store`` / ``query``）同域但分层。当前含：

- ``contracts``：C-0 数据契约（``ReasoningInput`` / ``VisitorProfile`` / ``RiskPattern`` /
  ``ConflictFlag`` / ``CurrentEvent`` / ``ActionRecord``）。
- ``replay_dataset`` / ``replay_layer``：M0 Episode Replay Layer（数据闭环验证）。

Retrieval / Aggregation / ContextBuilder / Orchestrator（M1 / C-1..C-3）与
``MemoryConsumerHook`` 触发接入（C-4）后续补充。
"""

from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    ReasoningInput,
    RiskPattern,
    VisitorProfile,
)
from home_perception.memory.consumer.replay_dataset import (
    MemoryReplayDataset,
    ReplayCase,
)
from home_perception.memory.consumer.replay_layer import (
    EpisodeReplayLayer,
    ProvisionalContextAssembler,
)

__all__ = [
    "ActionRecord",
    "ConflictFlag",
    "CurrentEvent",
    "EpisodeReplayLayer",
    "MemoryReplayDataset",
    "ProvisionalContextAssembler",
    "ReasoningInput",
    "ReplayCase",
    "RiskPattern",
    "VisitorProfile",
]
