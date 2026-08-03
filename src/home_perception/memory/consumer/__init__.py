"""Memory Consumer Layer（ADR-0025）。

本包是 Memory 消费侧（只读），与存储侧（``memory/policy`` / ``episode_builder`` /
``store`` / ``query``）同域但分层。当前含：

- ``contracts``：C-0 数据契约（ReasoningInput / VisitorProfile / RiskPattern /
  ConflictFlag / CurrentEvent / ActionRecord）。
- ``interfaces``：C-0 组件接口（Retrieval / Aggregation / ContextBuilder /
  MemoryConsumer 四个 ABC）。
- ``exceptions``：C-0 分层异常。
- ``retrieval`` / ``config``：C-1 ``RuleBasedRetrieval`` 默认规则召回 + 可配参数。
- ``aggregation``：C-2 ``RuleBasedAggregation`` 默认读侧聚合。
- ``context``：C-3 ``RuleBasedContextBuilder`` 默认组装器。
- ``orchestrator``：C-4 ``RuleBasedMemoryConsumer`` 默认编排器（单向驱动三组件，
  并派生 evidence / previous_actions / conflicts）。
- ``reasoning``：C-6 ``RuleBasedReasoningEngine`` 默认推理引擎（消费 ``ReasoningInput``
  → 产出 ``ReasoningResult``，**只读、非决策、非分数**）。
- ``conventions``：记录语义约定基元（``behavior:`` 标记解析 / 风险等级序），供上述
  组件共用，避免同一约定多处内联漂移。
- ``replay_dataset`` / ``replay_layer``：M0 Episode Replay Layer（数据闭环验证）。

C-0..C-6 默认实现均已落地。运行期接线点在 ``runtime/memory_consumer_hook.py``
（``MemoryConsumerHook``，模式 B 门控，**含 maybe_reason 推理接入**，默认关闭）——
它属 runtime 层，刻意不在本包导出，与写侧 ``runtime/memory_hook.py`` 对称。
"""

from home_perception.memory.consumer.aggregation import RuleBasedAggregation
from home_perception.memory.consumer.config import (
    AggregationConfig,
    ConsumerTriggerConfig,
    RetrievalConfig,
)
from home_perception.memory.consumer.context import RuleBasedContextBuilder
from home_perception.memory.consumer.contracts import (
    ActionRecord,
    ConflictFlag,
    CurrentEvent,
    RECOMMENDED_ACTION_HINTS,
    ReasoningInput,
    ReasoningResult,
    RiskPattern,
    SourceRef,
    VisitorProfile,
)
from home_perception.memory.consumer.conventions import (
    BEHAVIOR_MARKER_PREFIX,
    extract_behavior_markers,
    max_risk_level,
    risk_rank,
)
from home_perception.memory.consumer.exceptions import (
    AggregationError,
    BelowThresholdError,
    ConsumerError,
    ContextBuildError,
    ReasoningError,
    RetrievalError,
)
from home_perception.memory.consumer.interfaces import (
    Aggregation,
    ContextBuilder,
    MemoryConsumer,
    ReasoningEngine,
    Retrieval,
)
from home_perception.memory.consumer.orchestrator import RuleBasedMemoryConsumer
from home_perception.memory.consumer.replay_dataset import (
    MemoryReplayDataset,
    ReplayCase,
)
from home_perception.memory.consumer.replay_layer import (
    EpisodeReplayLayer,
    ProvisionalContextAssembler,
)
from home_perception.memory.consumer.reasoning import RuleBasedReasoningEngine
from home_perception.memory.consumer.retrieval import RuleBasedRetrieval

__all__ = [
    "BEHAVIOR_MARKER_PREFIX",
    "ActionRecord",
    "Aggregation",
    "AggregationConfig",
    "AggregationError",
    "BelowThresholdError",
    "ConflictFlag",
    "ConsumerError",
    "ConsumerTriggerConfig",
    "ContextBuildError",
    "ContextBuilder",
    "CurrentEvent",
    "EpisodeReplayLayer",
    "MemoryConsumer",
    "MemoryReplayDataset",
    "ProvisionalContextAssembler",
    "RECOMMENDED_ACTION_HINTS",
    "ReasoningEngine",
    "ReasoningError",
    "ReasoningInput",
    "ReasoningResult",
    "ReplayCase",
    "Retrieval",
    "RetrievalConfig",
    "RetrievalError",
    "RiskPattern",
    "RuleBasedAggregation",
    "RuleBasedContextBuilder",
    "RuleBasedMemoryConsumer",
    "RuleBasedReasoningEngine",
    "RuleBasedRetrieval",
    "SourceRef",
    "VisitorProfile",
    "extract_behavior_markers",
    "max_risk_level",
    "risk_rank",
]
