"""Memory Policy 抽象（ADR-0024 §3.2 transformation boundary）。

> **Slice 1 范围**：只定义 ABC 接口，不实现具体逻辑。
> DefaultEpisodeBuilder 实现见 Slice 4（Stage B）。

**核心约束**（ADR-0024 §3.2.2）：

> Memory Policy is a transformation boundary, not a decision module.

- 不参与风险判定（`DecisionPolicy` 是唯一决策中心，ADR-0010）
- 不参与行动决策（`ActionExecutor` 是唯一执行中心，ADR-0011）
- 不修改 `BehaviorState` / `RiskSignal`（Memory 只读消费，不反向写入状态）
- 不直接调用 LLM（Agent 是消费者，Memory Policy 只做确定性投影）

**输入输出契约**（ADR-0024 §3.2.2）：

```
MemoryPolicy.transform_short_term(
    state_snapshot: BehaviorState,
    transition: Optional[RiskSignal],
) -> Optional[ShortTermRecord]

MemoryPolicy.project_episode(
    visitor_event: VisitorEvent,
    warnings: List[WarningEvent],
    actions: List[ActionCommand],
) -> Optional[EpisodicRecord]

MemoryPolicy.aggregate_semantic(
    episodes: List[EpisodicRecord],
    dimension: str,
    period_key: str,
) -> Optional[SemanticAggregate]
```

**不变量**（ADR-0024 §3.2.3，由 InvariantValidator 在 Slice 2+ 强制）：
- I1 Idempotency：同一 observation event 重复发送只产生同一条 MemoryRecord
- I2 Monotonicity：Memory history 不能重写过去已有的 Episode
- I3 Causality：MemoryRecord.timestamp >= source event.timestamp
- I4 Explainability：每条 EpisodicRecord 必须引用 source evidence
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # 避免运行时循环 import；接口签名只在类型检查时需要这些类
    from ..analysis.behavior_state import BehaviorState
    from ..analysis.event import VisitorEvent
    from ..analysis.risk_signal import RiskSignal
    from ..analysis.warning import WarningEvent
    from ..action.command import ActionCommand

from .records import EpisodicRecord, SemanticAggregate, ShortTermRecord


class MemoryPolicy(ABC):
    """Memory Policy 抽象 —— ADR-0024 §3.2 transformation boundary。

    子类（如 `DefaultEpisodeBuilder`，Slice 4 实现）必须实现以下三个方法。
    所有产出必须经 `InvariantValidator`（Slice 2 引入）校验后才能入库。

    **不做什么**（结构性保证）：
    - 不持有可变状态（无状态转换器，输入 → 输出纯函数语义）
    - 不调 LLM / 不调外部 API
    - 不修改输入对象（BehaviorState / RiskSignal / VisitorEvent 等只读）
    - 不产出 WarningEvent / ActionCommand（决策归 DecisionPolicy，执行归 ActionExecutor）
    """

    @abstractmethod
    def transform_short_term(
        self,
        state_snapshot: "BehaviorState",
        transition: Optional["RiskSignal"],
    ) -> Optional[ShortTermRecord]:
        """Short-term Memory 写入（ADR-0024 §3.1.1）。

        触发时机：
        - 状态转移（transition 非 None）：RAISED / CLEARED
        - 周期快照（transition None，state_snapshot 非 None）
        - 访客离场（由上层调用 project_episode，本方法不处理）

        幂等键：`record_id = f"st-{visitor_instance_id}"`

        返回 None 的场景：
        - state_snapshot 与 transition 同时为 None（无写入触发）
        - visitor_instance_id 缺失（无法构造幂等键）
        - 其他子类自定义跳过条件
        """

    @abstractmethod
    def project_episode(
        self,
        visitor_event: "VisitorEvent",
        warnings: List["WarningEvent"],
        actions: List["ActionCommand"],
    ) -> Optional[EpisodicRecord]:
        """Episodic Memory 投影（ADR-0024 §3.2.1 Episode Builder）。

        触发时机：VisitorEvent 生成（访客离场）。
        幂等键：`record_id = f"ep-{visitor_event.event_id}"`。

        关联规则（DESIGN §5.2.4）：
        - VisitorEvent ↔ WarningEvent：visitor_instance_id + 时间窗（enter ~ leave+60s）
        - WarningEvent ↔ ActionCommand：warning_id

        返回 None 的场景：
        - visitor_event 缺失关键字段
        - 子类自定义跳过条件
        """

    @abstractmethod
    def aggregate_semantic(
        self,
        episodes: List[EpisodicRecord],
        dimension: str,
        period_key: str,
    ) -> Optional[SemanticAggregate]:
        """Semantic Memory 聚合（ADR-0024 §3.1.3）。

        v1 Slice 1 不实现具体逻辑，子类可返回 None。
        Stage G（Environment）+ Stage H（Identity）才填充。

        最低观测阈值约束（ADR-0024 §3.1.3.1，防 false pattern）：
        - minimum_episodes ≥ 30
        - minimum_time_window ≥ 7 天
        - minimum_confidence 达标才输出

        未达阈值时返回 None（不产出 SemanticAggregate）。
        """
