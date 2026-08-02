"""Memory Consumer 在 runtime 中的接入点（ADR-0025 C-4 / DESIGN §4.1）。

与写侧 ``MemoryHook``（ADR-0024 Integration Closure Slice A）**并列而非嵌套**：

- ``MemoryHook``：访客离场 → 投影 ``EpisodicRecord`` → 写入 ``MemoryStore``（存储过去）；
- ``MemoryConsumerHook``：访客离场 → 按模式 B 门控召回历史 → 产出 ``ReasoningInput``
  （利用过去）。

硬边界（ADR-0025 §3.9）：

- **不决策**：只产 ``ReasoningInput``，不写回 Memory、不改 Risk Score、不产 Warning
  （守 ADR-0010 单一决策中心）；
- **非阻塞**：任何异常只计 ``consumer_errors`` + 日志，绝不中断实时风险主链路
  （AGENTS.md §2.5：记忆侧失败不崩溃主链路）；
- **默认关闭**：``memory.consumer_enabled`` 默认 ``false``，关闭时 ``maybe_consume``
  立即返回 ``None``，零运行时开销、零行为变化；
- **独立指标**：Consumer 指标存本 Hook 自带的 ``ConsumerMetrics``，**不**混入
  ``PipelineMetrics``——消费侧噪声不得污染主链路可观测口径。

模式 B 触发（ADR-0025 §3.10 修订 / DESIGN §4.1）：
``risk_level ∈ {MEDIUM, HIGH}`` **或** 已知访客再现（``prior_episode_count > 0``）。
放宽到"存在历史"是刻意的——只在 HIGH 触发会让 Consumer 沦为事后解释系统，拿不到
"提前理解"的价值。

⚠️ 调用次序（见 ``pipeline.process_frame``）：本 Hook 必须在 ``MemoryHook.record``
**之前**调用。若先写后读，``MemoryHook`` 刚落库的"当下"会被 Retrieval 当作"历史"
召回，导致首次来访也被判为已知访客（``prior_episode_count`` 虚增 1），且
``historical_context`` 被当前事件污染。次序即语义。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..common.logging import get_logger
from ..memory.consumer.config import ConsumerTriggerConfig
from ..memory.consumer.contracts import CurrentEvent, ReasoningInput
from ..memory.consumer.exceptions import ConsumerError
from ..memory.consumer.interfaces import MemoryConsumer
from ..memory.store import MemoryStore

log = get_logger(__name__)

# 结构化日志字段集合（单一事实源：新增消费者指标时改这里一处，
# 测试用 ``CONSUMER_LOG_FIELDS`` 引用，避免字段与断言漂移）。
CONSUMER_LOG_FIELDS: tuple[str, ...] = (
    "consumer_evaluated",
    "consumer_triggered",
    "consumer_produced",
    "consumer_errors",
)


@dataclass
class ConsumerMetrics:
    """Memory Consumer 侧独立指标（刻意不并入 ``PipelineMetrics``）。

    - ``consumer_evaluated``：开关开启且被调用的次数（门控判定前的分母）；
    - ``consumer_triggered``：通过模式 B 门控、真正进入消费的次数；
    - ``consumer_produced``：成功产出 ``ReasoningInput`` 的次数；
    - ``consumer_errors``：消费失败次数（已隔离，不影响主链路）。

    ``evaluated - triggered`` 即被门控挡下的量，是灰度期判断"模式 B 是否过窄/过宽"
    的直接观测口（ADR-0025 §3.10 修订动因）。
    """

    consumer_evaluated: int = 0
    consumer_triggered: int = 0
    consumer_produced: int = 0
    consumer_errors: int = 0

    def as_log_fields(self) -> dict[str, int]:
        """结构化日志字段（与 ``PipelineMetrics`` 同风格，便于统一采集）。

        字段集合由模块级 ``CONSUMER_LOG_FIELDS`` 单一事实源驱动，新增指标时
        只改该常量即可，无需逐个手写 dict。
        """
        return {name: getattr(self, name) for name in CONSUMER_LOG_FIELDS}


class MemoryConsumerHook:
    """Memory 消费侧接线点（Consumer Layer ↔ 主链路）。

    只做"门控 + 调用 + 失败隔离"，不含任何召回 / 聚合 / 组装逻辑（那些在
    ``RuleBasedMemoryConsumer`` 及其三组件里）。
    """

    def __init__(
        self,
        consumer: MemoryConsumer | None,
        memory_store: MemoryStore | None,
        enabled: bool,
        metrics: ConsumerMetrics | None = None,
        config: ConsumerTriggerConfig | None = None,
    ) -> None:
        self._consumer = consumer
        self._memory_store = memory_store
        self._enabled = bool(enabled)
        self.metrics = metrics or ConsumerMetrics()
        self._config = config or ConsumerTriggerConfig()

    @property
    def enabled(self) -> bool:
        """运行期门控：``memory.consumer_enabled`` 是否激活。"""
        return self._enabled

    def maybe_consume(self, current_event: CurrentEvent) -> ReasoningInput | None:
        """按模式 B 门控消费一次事件；未触发或失败均返回 ``None``。

        返回值当前**无人消费**（Reasoning Engine 尚未落地，属 ADR-0025 后续阶段）：
        本阶段等价于 Shadow Mode——只验证"能在正确时机拿到正确上下文"，产物经指标与
        日志观测，不回流主链路。返回而非丢弃，是为了让 C-5 不变量测试与未来
        Reasoning 接入可直接消费同一出口。

        ⚠️ 次序契约（不变量级）：调用方 **必须** 在 ``MemoryHook.record`` 之前调用本方法。
        先写后读会把刚落库的"当下"当作"历史"召回（首次来访被判已知访客 +
        ``historical_context`` 被自身污染）。次序即语义——见模块 docstring 与
        ``pipeline.process_frame`` 标注。任何重构不得调换二者顺序。

        Args:
            current_event: 由 runtime 投影的当前事件（见 ``pipeline`` 投影函数）。

        Returns:
            触发且成功时返回 ``ReasoningInput``；关闭 / 未触发 / 失败时返回 ``None``。
        """
        if not self._enabled or self._consumer is None:
            return None
        self.metrics.consumer_evaluated += 1
        try:
            if not self._should_trigger(current_event):
                return None
            self.metrics.consumer_triggered += 1
            result = self._consumer.consume(current_event)
        except ConsumerError as exc:
            # 已分类的消费失败：属预期内故障，warning 级 + 计数，不中断主链路
            self.metrics.consumer_errors += 1
            log.warning(
                "memory_consumer.consume_failed",
                event_id=getattr(current_event, "event_id", None),
                error=str(exc),
            )
            return None
        except Exception:
            # 未分类异常：保留 traceback 便于定位，同样不中断主链路
            self.metrics.consumer_errors += 1
            log.exception(
                "memory_consumer.consume_unexpected_error",
                event_id=getattr(current_event, "event_id", None),
            )
            return None
        self.metrics.consumer_produced += 1
        return result

    # -- 模式 B 门控 -----------------------------------------------------------
    def _should_trigger(self, current_event: CurrentEvent) -> bool:
        """``risk_level ∈ enabled_levels`` **或** 已知访客再现（ADR-0025 §3.10）。

        两个条件是"或"关系：低风险的已知访客同样值得理解（重复来访本身是信号），
        高风险的陌生访客也必须理解（首次即高危）。
        """
        if current_event.risk_level in self._config.enabled_levels:
            return True
        if self._config.trigger_on_known_visitor:
            return self._prior_episode_count(current_event.visitor_instance_id) > 0
        return False

    def _prior_episode_count(self, visitor_instance_id: str) -> int:
        """该访客**既往** episode 数（只读，C2）。

        "既往"由调用次序保证：本 Hook 在 ``MemoryHook.record`` 之前执行，故此刻
        store 中不含本次事件的记录（见模块 docstring 的次序说明）。
        """
        if self._memory_store is None:
            return 0
        return len(self._memory_store.get_episodic_by_visitor(visitor_instance_id))


__all__ = ["ConsumerMetrics", "MemoryConsumerHook"]
