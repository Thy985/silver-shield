"""决策边界契约（ADR-0030 · Slice A · `DecisionInput`）。

> **`DecisionInput` 是四段链路（感知 → Memory → Reasoning → Decision）的唯一收敛载体。**
> 它把「感知触发 + 记忆上下文 + 参考建议 + 策略状态 + 既往决策」收敛为一个不可变
> 结构体，作为 `DecisionPolicy.decide` 的唯一入参（签名演进见 Slice B）。

边界铁律（ADR-0030 §0.2 / ADR-0010 单一决策中心）：

- 上游三类型（`RiskSignal` / `ReasoningInput` / `ReasoningResult`）永远是**事实与建议**，
  绝不是决策；`DecisionInput` 自身**也不内嵌决策**——`risk_level` / `recommended_action`
  是其**输出** `WarningEvent` 的属性，不在本结构体（C1，`DECISION_INPUT_FORBIDDEN_FIELDS`
  + `DECISION_INPUT_FIELD_WHITELIST` 于导入期 fail-closed 断言）。
- 本模块**不改变 `DecisionPolicy` 任何行为**（Slice A 零行为变化）。

实现约束（两处循环导入，按仓库既有惯例规避）：

1. `analysis/` → `memory/`：`memory/__init__.py` 会 import `cold_start`，后者顶层
   `from ..analysis.realtime_risk_evaluator import ...`。若本模块顶层 import
   `memory.consumer.contracts` 即形成环。故 `ReasoningInput` / `ReasoningResult`
   仅作 **`TYPE_CHECKING` 注解**（`from __future__ import annotations` 下注解是字符串，
   运行期不求值），反序列化所需的真实类在 `from_dict` 内**惰性导入**——与
   `analysis/realtime_risk_evaluator.py`、`analysis/recent_behavior_store.py` 同一范式。
2. `decision_contract` ↔ `decision_policy`：本模块运行期需要 `DecisionContext`，故正向
   import；反向（`decision_policy` 需要 `DecisionInput` 注解）由对方以 `TYPE_CHECKING`
   处理，不成环。`decision_engine` → `decision_contract` → `decision_policy` 为单向链。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .decision_policy import DecisionContext
from .perception import PerceptionEvent
from .warning import WarningEvent

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查，运行期不导入（见模块 docstring §1）
    from ..memory.consumer.contracts import ReasoningInput, ReasoningResult


# ============================================================================
# 契约白名单 / 禁止字段（C1 + C7，导入期 fail-closed）
# ============================================================================

# C1（ADR-0010 单一决策中心）：决策语义字段是**输出**（WarningEvent）的属性，
# 绝不得内嵌进「喂给决策的输入」。与 tests/memory/consumer/_c1.py 的
# CONSUMER_FORBIDDEN_FIELDS 同源，另加 WarningEvent 侧的决策产物字段。
DECISION_INPUT_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "risk_score",
        "score",
        "decision",
        "verdict",
        "warning",
        "risk_level",
        "recommended_action",
    }
)

# C7（一级聚合约束，防 God Object）：`DecisionInput` 只允许这 5 个语义正交的字段。
# 未来若字段持续增长，须先抽象为具名 Bundle（PerceptionBundle / CognitionBundle /
# StateBundle）再聚合，**不允许**演化成 20+ 扁平字段的 God Object（ADR-0030 C7）。
# 任何新增字段都会让下方 `_assert_contract_shape()` 在**导入期**直接抛错，
# 强制走 ADR / 评审流程。
DECISION_INPUT_FIELD_WHITELIST: frozenset[str] = frozenset(
    {
        "trigger_events",
        "decision_context",
        "reasoning_input",
        "reasoning_result",
        "prior_warning",
    }
)


# ============================================================================
# 内部序列化辅助
# ============================================================================
#
# `PerceptionEvent` / `WarningEvent` / `DecisionContext` 目前只有 `to_dict()`，
# 没有 `from_dict()`。Slice A 刻意**不**去给这三个既有类型加方法：
#   - `WarningEvent` 属 ADR-0015 冻结边界（silver_demo 白名单类型），
#   - Slice A 的承诺是「只新增一个契约模块、不改既有行为」。
# 故反序列化逻辑收敛为本模块私有 helper；未来若其他模块也需要，再按 ADR 提升为
# 公共 `from_dict`（届时本处 helper 应改为委托，避免两套口径漂移）。


def _perception_from_dict(data: dict[str, Any]) -> PerceptionEvent:
    """`PerceptionEvent.to_dict()` 的逆。"""
    return PerceptionEvent(
        device_id=data["device_id"],
        event_type=data["event_type"],
        score=data["score"],
        visitor_id=data["visitor_id"],
        source_video=data.get("source_video", ""),
        timestamp=data["timestamp"],
        track_id=data.get("track_id"),
        bbox=data.get("bbox"),
        location=data.get("location"),
        repeat_count=data.get("repeat_count"),
        is_odd_hour=data.get("is_odd_hour", False),
        evidence=list(data.get("evidence", [])),
        meta=dict(data.get("meta", {})),
        created_at=datetime.fromisoformat(data["created_at"]),
    )


def _perception_to_dict(event: PerceptionEvent) -> dict[str, Any]:
    """`PerceptionEvent.to_dict()` + `source_video`。

    既有 `to_dict()` 未包含 `source_video`（它是构造必填项），直接往返会丢字段。
    本 helper 补齐，保证 C3 往返稳定。
    """
    payload = event.to_dict()
    payload["source_video"] = event.source_video
    return payload


def _warning_from_dict(data: dict[str, Any]) -> WarningEvent:
    """`WarningEvent.to_dict()` 的逆。"""
    return WarningEvent(
        elder_id=data["elder_id"],
        device_id=data["device_id"],
        risk_level=data["risk_level"],
        recommended_action=data["recommended_action"],
        trigger_events=list(data["trigger_events"]),
        reason_summary=list(data["reason_summary"]),
        warning_id=data["warning_id"],
        status=data.get("status", "CREATED"),
        perception_score=data.get("perception_score", 0.0),
        evidence=list(data.get("evidence", [])),
        meta=dict(data.get("meta", {})),
        created_at=datetime.fromisoformat(data["created_at"]),
    )


def _context_to_dict(ctx: DecisionContext) -> dict[str, Any]:
    return {
        "elder_id": ctx.elder_id,
        "now": ctx.now.isoformat(),
        "extra": dict(ctx.extra),
    }


def _context_from_dict(data: dict[str, Any]) -> DecisionContext:
    return DecisionContext(
        elder_id=data["elder_id"],
        now=datetime.fromisoformat(data["now"]),
        extra=dict(data.get("extra", {})),
    )


# ============================================================================
# DecisionInput —— 决策链唯一收敛载体（ADR-0030 D2）
# ============================================================================


@dataclass(frozen=True)
class DecisionInput:
    """`DecisionPolicy.decide` 的唯一、规范输入（ADR-0030 D2）。

    字段（**声明顺序与 ADR-0030 D2 表格不同**：Python dataclass 要求无默认值字段
    先于有默认值字段，故 `decision_context` 上提至第二位；语义与 D2 表逐字段一致）：

    - ``trigger_events``：本评估周期内的感知触发事件（取代原 ``list[PerceptionEvent]``
      入参）。**不可为 None**，但**允许为空元组**——`DecisionPolicy` 抽象基类 docstring
      明确「返回 None 的典型情况：空列表」，若此处强制非空会与该既有契约冲突并使
      ``DecisionEngine.evaluate([])`` 由「返回 None」变为「抛异常」（行为回归）。
    - ``decision_context``：策略执行上下文（``elder_id`` / ``now`` / ``extra``），沿用既有。
    - ``reasoning_input``：完整记忆上下文（含 ``cross_modal_contexts`` / ``risk_pattern`` /
      ``conflicts`` / ``visitor_profile``）——Memory 进决策的解释来源；**可为 None**。
    - ``reasoning_result``：Reasoning Engine 的参考建议；推理跳过 / 失败时为 ``None``。
    - ``prior_warning``：既往决策（迟滞 / 幂等用，ADR-0030 C6）；**非决策真相来源**。

    **Memory 可缺席原则（ADR-0030 D2）**：``reasoning_input`` / ``reasoning_result``
    在 **Memory 未启用 / 未接线 / 检索失败** 三态下均须能合法构造，``None`` 语义为
    「本次决策无记忆上下文」，`DecisionPolicy` 此时退化为纯感知决策（与今日逐字段一致）。
    推论：读取 Memory 字段前 **MUST** 做 ``None`` 守卫；**禁止**把「Memory 缺席」当作
    风险信号（缺席即中性，不得因无记忆而抬升或降低风险）。

    确定性（C3）：``trigger_events`` 在构造时按 ``timestamp`` 升序**规范化**（稳定排序，
    同 timestamp 保留传入相对次序），保证「同一组事件的不同排列 → 同一 DecisionInput
    → 同一 WarningEvent」，供回放 / 审计一致。
    """

    trigger_events: tuple[PerceptionEvent, ...]
    decision_context: DecisionContext
    reasoning_input: ReasoningInput | None = None
    reasoning_result: ReasoningResult | None = None
    prior_warning: WarningEvent | None = field(default=None)

    def __post_init__(self) -> None:
        if self.trigger_events is None:
            raise ValueError(
                "DecisionInput.trigger_events 不能为 None（无触发事件请传空元组 ()）"
            )
        if not isinstance(self.trigger_events, tuple):
            raise TypeError(
                "DecisionInput.trigger_events 必须是 tuple（C2 不可变容器），"
                f"收到 {type(self.trigger_events).__name__}"
            )
        for i, ev in enumerate(self.trigger_events):
            if not isinstance(ev, PerceptionEvent):
                raise TypeError(
                    f"DecisionInput.trigger_events[{i}] 必须是 PerceptionEvent，"
                    f"收到 {type(ev).__name__}"
                )
        if not isinstance(self.decision_context, DecisionContext):
            raise TypeError(
                "DecisionInput.decision_context 必须是 DecisionContext，"
                f"收到 {type(self.decision_context).__name__}"
            )

        # C3 规范化：按 timestamp 升序（稳定排序 —— 同 timestamp 保留传入相对次序）
        object.__setattr__(
            self,
            "trigger_events",
            tuple(sorted(self.trigger_events, key=lambda ev: ev.timestamp)),
        )

    # ------------------------------------------------------------------
    # 序列化（C3：与 memory/consumer/contracts.py 同构 —— datetime→ISO、tuple→list）
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_events": [_perception_to_dict(ev) for ev in self.trigger_events],
            "decision_context": _context_to_dict(self.decision_context),
            "reasoning_input": self.reasoning_input.to_dict()
            if self.reasoning_input is not None
            else None,
            "reasoning_result": self.reasoning_result.to_dict()
            if self.reasoning_result is not None
            else None,
            "prior_warning": self.prior_warning.to_dict()
            if self.prior_warning is not None
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionInput:
        # 惰性导入：规避 analysis ↔ memory 循环（见模块 docstring §1）
        from ..memory.consumer.contracts import ReasoningInput, ReasoningResult

        raw_reasoning_input = data.get("reasoning_input")
        raw_reasoning_result = data.get("reasoning_result")
        raw_prior_warning = data.get("prior_warning")
        return cls(
            trigger_events=tuple(
                _perception_from_dict(ev) for ev in data.get("trigger_events", [])
            ),
            decision_context=_context_from_dict(data["decision_context"]),
            # 向后兼容：Slice A 之前 / Memory 未接线时落库的 payload 缺这些键 → None
            reasoning_input=ReasoningInput.from_dict(raw_reasoning_input)
            if raw_reasoning_input is not None
            else None,
            reasoning_result=ReasoningResult.from_dict(raw_reasoning_result)
            if raw_reasoning_result is not None
            else None,
            prior_warning=_warning_from_dict(raw_prior_warning)
            if raw_prior_warning is not None
            else None,
        )


# ============================================================================
# 导入期 fail-closed 契约守卫（C1 + C7）
# ============================================================================


def _assert_contract_shape() -> None:
    """在**导入期**钉死 `DecisionInput` 的字段形状。

    放在导入期而非 `__post_init__`：既零每实例开销，又能让任何越界改动在
    `import home_perception.analysis.decision_contract` 的瞬间就炸——决策链上
    「偷偷加一个 risk_score 字段」不可能悄悄合入。测试另有独立断言兜底
    （不依赖本函数，避免自证）。
    """
    names = {f.name for f in fields(DecisionInput)}

    forbidden = DECISION_INPUT_FORBIDDEN_FIELDS & names
    if forbidden:
        raise RuntimeError(
            f"DecisionInput 含禁止的决策语义字段 {sorted(forbidden)}；"
            "决策语义是 WarningEvent 的输出属性，不得内嵌于决策输入（ADR-0030 C1 / ADR-0010）"
        )

    if names != DECISION_INPUT_FIELD_WHITELIST:
        extra = sorted(names - DECISION_INPUT_FIELD_WHITELIST)
        missing = sorted(DECISION_INPUT_FIELD_WHITELIST - names)
        raise RuntimeError(
            "DecisionInput 字段与 DECISION_INPUT_FIELD_WHITELIST 不一致（ADR-0030 C7 防膨胀）；"
            f"多出 {extra}、缺少 {missing}。新增字段须先走 ADR 评审，"
            "字段持续增长时应抽象为具名 Bundle 而非横向堆平字段。"
        )


_assert_contract_shape()


__all__ = [
    "DECISION_INPUT_FIELD_WHITELIST",
    "DECISION_INPUT_FORBIDDEN_FIELDS",
    "DecisionInput",
]
