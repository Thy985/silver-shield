"""Memory Consumer Layer（ADR-0025）。

本包是 Memory 消费侧（只读），与存储侧（``memory/policy`` / ``episode_builder`` /
``store`` / ``query``）同域但分层。当前含：

- ``contracts``：C-0 数据契约（ReasoningInput / VisitorProfile / RiskPattern /
  ConflictFlag / CurrentEvent / ActionRecord）。
- ``interfaces``：C-0 组件接口（Retrieval / Aggregation / ContextBuilder /
  MemoryConsumer 四个 ABC）。
- ``exceptions``：C-0 分层异常。
- ``retrieval`` / ``config``：C-1 ``RuleBasedRetrieval`` 默认规则召回 + 可配参数。
- ``replay_dataset`` / ``replay_layer``：M0 Episode Replay Layer（数据闭环验证）。

Aggregation / ContextBuilder 的默认实现（C-2..C-3）与 MemoryConsumer 编排 +
MemoryConsumerHook 触发接入（C-4）后续补充。
"""

from home_perception.memory.consumer.config import RetrievalConfig
from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    ReasoningInput,
    RiskPattern,
    VisitorProfile,
)
from home_perception.memory.consumer.exceptions import (
    AggregationError,
    BelowThresholdError,
    ConsumerError,
    ContextBuildError,
    RetrievalError,
)
from home_perception.memory.consumer.interfaces import (
    Aggregation,
    ContextBuilder,
    MemoryConsumer,
    Retrieval,
)
from home_perception.memory.consumer.replay_dataset import (
    MemoryReplayDataset,
    ReplayCase,
)
from home_perception.memory.consumer.replay_layer import (
    EpisodeReplayLayer,
    ProvisionalContextAssembler,
)
from home_perception.memory.consumer.retrieval import RuleBasedRetrieval

__all__ = [
    "ActionRecord",
    "Aggregation",
    "AggregationError",
    "BelowThresholdError",
    "ConflictFlag",
    "ConsumerError",
    "ContextBuildError",
    "ContextBuilder",
    "CurrentEvent",
    "EpisodeReplayLayer",
    "MemoryConsumer",
    "MemoryReplayDataset",
    "ProvisionalContextAssembler",
    "ReasoningInput",
    "ReplayCase",
    "Retrieval",
    "RetrievalConfig",
    "RetrievalError",
    "RiskPattern",
    "RuleBasedRetrieval",
    "VisitorProfile",
]
