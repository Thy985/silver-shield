"""ADR-0032 / ADR-0033 / ADR-0034 共享契约（中立子包，避免反向依赖）。

本模块承载两组**评价契约模型**：

- ``BenchmarkExpectation``（ADR-0033）：**感知级**安全评价标签——"这个场景该不该报警"；
- ``IntegrationExpectationSuite``（ADR-0034 D4）：**闭环级**集成期望——"报警之后整条链
  （Memory / Decision / Notification）该不该真落库、真发出通知"。

两者**语义分离**，互不替代。它们都语义上属于"场景评价"，但为了避免
``validation/scenario/scenario.py`` 反向 import ``evaluation`` / ``integration`` 包
（``validation`` 是二者的被依赖方，方向必须单向），统一放在中立的 ``validation.contracts``
子包。

- ``scenario.py``（validation 内部）从此处 import，方向在 validation 内、无环；
- ``evaluation`` 通过 ``evaluation.schema`` re-export 供外部使用，``evaluation`` 仍只消费
  validation，不反向被依赖。

本模块是纯数据模型（pydantic + ``action`` / ``analysis`` 的枚举常量），**不** import
``evaluation`` / ``integration`` / ``harness``，断环。

> **为什么所有模型都 ``extra="forbid"``**：ADR-0034 的核心命题是"静默丢弃 = 失败"。若
> 期望字段写错名（``min_recods: 5``）却被 pydantic 默默忽略，整套集成校验会**空转通过**
> ——这正是本 ADR 要消灭的失败模式。故新增模型一律 fail-closed 拒绝未知键。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from home_perception.action.command import COMMAND_TYPES
from home_perception.analysis.decision_trace import SuppressReason, TraceOutcomeKind
from home_perception.analysis.warning import RECOMMENDED_ACTIONS, RISK_LEVELS
from home_perception.core.event import EvidenceModality
from home_perception.memory.cross_modal_link import CROSS_MODAL_RELATIONSHIP_VALUES

# 期望里允许出现的决策 outcome / 抑制原因，直接派生自决策层枚举（单一事实源，
# 决策层新增抑制路径时此处自动跟随，不会出现"契约写死、实现漂移"）。
DECISION_OUTCOMES: tuple[str, ...] = tuple(kind.value for kind in TraceOutcomeKind)
SUPPRESS_REASON_VALUES: tuple[str, ...] = tuple(reason.value for reason in SuppressReason)

# 期望语言专属的第三取值（**不是** ``TraceOutcomeKind`` 的成员）。
#
# 为什么必须存在：``PerceptionPipeline._act_on_event`` 在规则未命中时 ``return [], [], []``
# —— 决策层**根本不被调用**，因此不会留下任何 trace。此时"没有告警"是**合法未触发**，
# 而不是 SUPPRESS（SUPPRESS 意味着决策层跑过并给出了抑制理由）。
#
# 若不提供 ``NONE``，良性场景只能靠"不声明 decision 子期望"来蒙混过关——那等于放弃对
# "不误发"的正向断言，与 ADR-0034"静默丢弃 = 失败"的命题背道而驰。
#
# 实证（2026-08-10，Phase A 落地时测得）：视觉单模态 + 默认 policy 下三条 SUPPRESS 路径
# **全部不可达**——``NO_TRIGGER_EVENTS`` 被上述短路挡住；``ALL_SUPPRESSED_NORMAL`` 需要
# "visit_normal 且非 odd_hour"，而 ``OddHourRule`` 产出的 ``visit_normal`` 恒带
# ``is_odd_hour=True``；``UNROUTABLE_EVENT_TYPE`` 需要 routing_table 外的类型，而
# ``DEFAULT_ROUTING_TABLE`` 覆盖了 ``EVENT_TYPES`` 全部 5 类。``SUPPRESS`` 取值仍保留，
# 供 Phase B 音频路径与 DecisionEngine 直接单测使用。
OUTCOME_NONE: str = "NONE"
DECISION_EXPECTATION_OUTCOMES: tuple[str, ...] = (*DECISION_OUTCOMES, OUTCOME_NONE)

# Memory 结构化断言里允许出现的证据模态（单一事实源，派生自 ``EvidenceModality``）。
MEMORY_MODALITIES: tuple[str, ...] = tuple(m.value for m in EvidenceModality)


class BenchmarkExpectation(BaseModel):
    """场景的安全评价标签（ADR-0033 D3，与 ADR-0032 ``expects`` 语义分离）。

    - ``expected_alarm``：该场景是否期望触发告警（安全评价意图，由场景作者**显式声明**）；
    - ``severity``：期望告警级别（可选，用于分层度量；必须是 ``RISK_LEVELS`` 之一）；
    - ``note``：人类可读理由（审计血缘）。
    """

    expected_alarm: bool
    severity: str | None = None
    note: str | None = None

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, v: str | None) -> str | None:
        if v is not None and v not in RISK_LEVELS:
            raise ValueError(
                f"benchmark.severity={v!r} 非法；必须为 {RISK_LEVELS}（fail-closed）"
            )
        return v


# ============================================================================
# ADR-0034 D4 · 闭环集成期望（Phase A 子集）
# ============================================================================
#
# **设计原则（D4 冻结）**：期望值**只用下界 + 结构断言，绝不用精确 `==` 计数**。
# 闭环内部的 Memory 落库数会随实现演进（一次诈骗流程可能拆成 visitor / suspicious call /
# money transfer 1~3 个 episode），精确计数会把测试绑死到某一版实现，催生"为过测而凑数"。
#
# **Phase 边界**（ADR-0034 §Phase 切片，本文件严格遵守）：
# - Phase A（已落地）：`perception` / `memory.min_records` / `decision` / `action`
# - Phase B.1（已落地）：`MemoryExpectation` 结构化字段 `expected_risk_level` /
#   `expected_action_types` / `required_modalities`（Memory 深度断言）
# - Phase B.2（本次）：`CrossModalExpectation`（F5，vision+audio 真实关联）
# - Phase C：各子期望的 `severity: Literal["blocking","warning"]` 字段
#
# 提前落 Phase C 字段 = 落一个当前无人消费的空契约，反而给"已支持"的错觉。


class _StrictModel(BaseModel):
    """新增集成契约模型的共同基类：未知键 fail-closed（见模块 docstring）。"""

    model_config = ConfigDict(extra="forbid")


class PerceptionExpectation(_StrictModel):
    """感知阶段期望（F1 判据）。

    - ``min_perception_events``：产出的 ``PerceptionEvent`` 条数**下界**（未声明 → 不校验）。
      配合 ADR-0033 ``build_scenario_score`` 的 ``outcome ∈ {TP, TN}`` 共同构成 perception
      stage 判据。
    """

    min_perception_events: int | None = None

    @field_validator("min_perception_events")
    @classmethod
    def _validate_lower_bound(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"min_perception_events 必须 >= 0，收到 {v}（下界语义）")
        return v


class MemoryExpectation(_StrictModel):
    """Memory 阶段期望（F4 判据 · Phase A 只含条数下界，Phase B.1 加结构化断言）。

    - ``min_records``：``InMemoryStore.all_episodic()`` 返回的 ``EpisodicRecord`` 条数下界。
      默认 1——只要声明了 ``memory`` 块，就至少要求"闭环真的往 Memory 里写了东西"。
    - ``expected_risk_level``：期望在**至少一条** episode 上观测到的风险等级（来自
      ``EpisodicRecord.risk_level``）。按"并集包含"判定：
      ``exp.expected_risk_level in {ep.risk_level for ep in episodes}``——D4 禁止精确计数 /
      要求全部相同，否则会判死正常的多 episode 场景。
    - ``expected_action_types``：期望在 episode 的 ``actions``（Memory 投影的
      ``ActionSummary.command_type``）中观测到的命令类型集合；按**集合**比对（不计数、
      不计顺序），``required.issubset(union_of_episode_action_types)``。元素取自
      ``COMMAND_TYPES``。
    - ``required_modalities``：期望在 episode 的 ``modalities`` 中观测到的证据模态集合；
      按集合比对，``required.issubset(union_of_modalities)``。元素取自
      ``EvidenceModality`` 的合法值（``("vision", "audio", "identity")``）。

    > 所有字段均 opt-in、互相正交；未声明不参与校验。结构化字段属 Phase B.1（Memory 深度）。
    """

    min_records: int = 1
    expected_risk_level: str | None = None
    expected_action_types: list[str] | None = None
    required_modalities: list[str] | None = None

    @field_validator("min_records")
    @classmethod
    def _validate_min_records(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"min_records 必须 >= 0，收到 {v}（下界语义）")
        return v

    @field_validator("expected_risk_level")
    @classmethod
    def _validate_risk_level(cls, v: str | None) -> str | None:
        if v is not None and v not in RISK_LEVELS:
            raise ValueError(
                f"memory.expected_risk_level={v!r} 非法；必须为 {RISK_LEVELS}（fail-closed）"
            )
        return v

    @field_validator("expected_action_types")
    @classmethod
    def _validate_action_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        illegal = [t for t in v if t not in COMMAND_TYPES]
        if illegal:
            raise ValueError(
                f"memory.expected_action_types 含非法类型 {illegal}；"
                f"必须取自 {COMMAND_TYPES}（fail-closed）"
            )
        duplicates = sorted({t for t in v if v.count(t) > 1})
        if duplicates:
            raise ValueError(
                f"memory.expected_action_types 含重复项 {duplicates}；"
                "本字段按**集合**比对（D4 禁止精确计数），重复项无法表达'期望 N 条'，"
                "多半是笔误（fail-closed）"
            )
        return v

    @field_validator("required_modalities")
    @classmethod
    def _validate_modalities(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        illegal = [m for m in v if m not in MEMORY_MODALITIES]
        if illegal:
            raise ValueError(
                f"memory.required_modalities 含非法模态 {illegal}；"
                f"必须取自 {MEMORY_MODALITIES}（fail-closed）"
            )
        duplicates = sorted({m for m in v if v.count(m) > 1})
        if duplicates:
            raise ValueError(
                f"memory.required_modalities 含重复项 {duplicates}；"
                "本字段按**集合**比对（D4 禁止精确计数），重复项多半是笔误（fail-closed）"
            )
        return v


class DecisionExpectation(_StrictModel):
    """Decision 阶段期望（F2 判据），对照 ``trace_recorder`` 采到的 ``DecisionTrace``。

    - ``outcome``：``WARN`` | ``SUPPRESS`` | ``NONE``（三者语义严格区分，见 ``OUTCOME_NONE``）：
      ``WARN`` = 决策层产出告警；``SUPPRESS`` = 决策层跑过且给出抑制理由；
      ``NONE`` = 决策层无告警产出，**且**该缺失有合法解释（无上游感知事件，或存在
      SUPPRESS trace）——无解释的缺失即 F2 Decision Drop；
    - ``risk_level``：``LOW`` | ``MEDIUM`` | ``HIGH`` —— 用于**区分 WARN_LOW 与 WARN_HIGH**
      （这正是 D4 强调"结构化区分"的原因：只断言"有告警"会让降级/升级漂移逃逸）；
    - ``recommended_action``：``MONITOR`` | ``NOTIFY_FAMILY`` | ``ESCALATE_COMMUNITY``；
    - ``reason_code``：``SUPPRESS`` 时必须取自 ``SuppressReason.value``；WARN 侧为开放项；
    - ``confidence``：映射 ``WarningEvent.perception_score``（0–1 规则命中强度，**不是**
      决策置信度——决策层不产生概率，ADR-0010）。
    """

    outcome: str | None = None
    risk_level: str | None = None
    recommended_action: str | None = None
    reason_code: str | None = None
    confidence: float | None = None

    @field_validator("outcome")
    @classmethod
    def _validate_outcome(cls, v: str | None) -> str | None:
        if v is not None and v not in DECISION_EXPECTATION_OUTCOMES:
            raise ValueError(
                f"decision.outcome={v!r} 非法；必须为 {DECISION_EXPECTATION_OUTCOMES}"
                "（fail-closed）"
            )
        return v

    @field_validator("risk_level")
    @classmethod
    def _validate_risk_level(cls, v: str | None) -> str | None:
        if v is not None and v not in RISK_LEVELS:
            raise ValueError(
                f"decision.risk_level={v!r} 非法；必须为 {RISK_LEVELS}（fail-closed）"
            )
        return v

    @field_validator("recommended_action")
    @classmethod
    def _validate_recommended_action(cls, v: str | None) -> str | None:
        if v is not None and v not in RECOMMENDED_ACTIONS:
            raise ValueError(
                f"decision.recommended_action={v!r} 非法；"
                f"必须为 {RECOMMENDED_ACTIONS}（fail-closed）"
            )
        return v

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"decision.confidence 必须在 [0, 1]，收到 {v}")
        return v

    @model_validator(mode="after")
    def _validate_outcome_coherence(self) -> DecisionExpectation:
        """"无告警"类 outcome 与判定字段互斥（对齐 ``TraceOutcome`` 的带标签联合语义）。

        ``TraceOutcome`` 规定：WARN 必带 ``risk_level``/``recommended_action``/``warning_id``，
        SUPPRESS 必带 ``suppress_reason``。因此声明 ``outcome=SUPPRESS`` 却同时期望
        ``risk_level=HIGH`` 是**自相矛盾的期望**——它永远不可能被满足，只会变成一条恒失败
        的僵尸断言。fail-closed 在**加载期**就拒绝，而不是等到跑完一整轮才报 F2。

        ``NONE`` 额外禁止 ``reason_code``：声明理由码等于要求"决策层跑过并抑制"，那应当
        直接写 ``SUPPRESS``。允许二者并存会让"合法未触发"与"显式抑制"这两种**判定路径
        完全不同**的情形混为一谈。
        """
        no_warning_outcomes = (TraceOutcomeKind.SUPPRESS.value, OUTCOME_NONE)
        if self.outcome in no_warning_outcomes:
            conflicting = [
                name
                for name in ("risk_level", "recommended_action")
                if getattr(self, name) is not None
            ]
            if conflicting:
                raise ValueError(
                    f"decision.outcome={self.outcome} 时不得声明 {conflicting}；"
                    "该 outcome 语义为'未产生告警'，无风险等级/推荐动作（fail-closed）"
                )
        if self.outcome == TraceOutcomeKind.SUPPRESS.value:
            if self.reason_code is not None and self.reason_code not in SUPPRESS_REASON_VALUES:
                raise ValueError(
                    f"decision.reason_code={self.reason_code!r} 非法；"
                    f"SUPPRESS 的 reason_code 必须取自 {SUPPRESS_REASON_VALUES}（fail-closed）"
                )
        elif self.outcome == OUTCOME_NONE and self.reason_code is not None:
            raise ValueError(
                "decision.outcome=NONE 时不得声明 reason_code；"
                "需要断言抑制理由请改用 outcome=SUPPRESS（fail-closed）"
            )
        return self


class ActionExpectation(_StrictModel):
    """Notification 阶段期望（F3 判据），对照 ``ActionSink`` 收到的 ``ActionCommand``。

    - ``expected_command_types``：元素取自 ``COMMAND_TYPES``；按**集合**比对（不计数、不计
      顺序）。显式空列表 ``[]`` 是合法且有意义的声明——"良性场景不得发出任何命令"，即
      ADR-0034 t6 的"不误发"用例。
    - ``expected_notification``：期望是否真发出通知；与 ``expected_command_types`` 非空一致。
    """

    expected_command_types: list[str] | None = None
    expected_notification: bool | None = None

    @field_validator("expected_command_types")
    @classmethod
    def _validate_command_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        illegal = [t for t in v if t not in COMMAND_TYPES]
        if illegal:
            raise ValueError(
                f"action.expected_command_types 含非法类型 {illegal}；"
                f"必须取自 {COMMAND_TYPES}（fail-closed）"
            )
        duplicates = sorted({t for t in v if v.count(t) > 1})
        if duplicates:
            raise ValueError(
                f"action.expected_command_types 含重复项 {duplicates}；"
                "本字段按**集合**比对（D4 禁止精确计数），重复项无法表达'期望 N 条'，"
                "多半是笔误（fail-closed）"
            )
        return v

    @model_validator(mode="after")
    def _validate_notification_coherence(self) -> ActionExpectation:
        """两个字段都声明时必须自洽（D4："与 expected_command_types 非空一致"）。

        只有**同时声明**才交叉校验；单独声明其一表示"另一项不参与校验"。
        """
        if self.expected_notification is None or self.expected_command_types is None:
            return self
        has_commands = len(self.expected_command_types) > 0
        if self.expected_notification != has_commands:
            raise ValueError(
                f"action.expected_notification={self.expected_notification} 与 "
                f"expected_command_types={self.expected_command_types} 不一致；"
                "期望发通知就必须列出命令类型，期望不发就必须是空列表（fail-closed）"
            )
        return self


class CrossModalExpectation(_StrictModel):
    """跨模态关联阶段期望（F5 判据 · Phase B.2）。

    断言闭环跑完后 ``CrossModalLinkRuntime`` 产出的关联边（``CrossModalLink``）满足：

    - ``min_links``：关联边条数**下界**（默认 1——只要声明了 ``cross_modal`` 块，就至少
      要求"闭环真的建出了跨模态关联"）。与 D4 纪律一致：下界而非精确计数。
    - ``expected_linked_modalities``：期望在**至少一条**关联边两端 episode 的模态并集上
      观测到的证据模态集合（如 ``["vision", "audio"]`` 表"至少有一条链接同时覆盖视觉与
      音频"）。按"并集包含"判定：``required.issubset(union_of_linked_episode_modalities)``
      ——D4 禁止精确计数 / 要求全部相同。元素取自 ``MEMORY_MODALITIES``。
    - ``required_relationships``：期望在**至少一条**关联边上观测到的关系白名单集合
      （如 ``["supports"]`` 表"跨模态支撑"）。按**集合**比对（不计数、不计顺序），
      ``required.issubset({link.relationship for link in links})``。元素取自
      ``CROSS_MODAL_RELATIONSHIP_VALUES``（``co_occurs`` / ``supports``）。

    > 所有字段均 opt-in、互相正交；未声明不参与校验。对应 ADR-0034 Phase B.2 的
    > "episode A → link → episode B 真实关联" 验收——声明了期望却没注入
    > ``cross_modal_runtime``（或根本没建出边）即 F5 不通过（t8），而非静默通过。
    """

    min_links: int = 1
    expected_linked_modalities: list[str] | None = None
    required_relationships: list[str] | None = None

    @field_validator("min_links")
    @classmethod
    def _validate_min_links(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"min_links 必须 >= 1，收到 {v}（下界语义：至少建出一条关联）")
        return v

    @field_validator("expected_linked_modalities")
    @classmethod
    def _validate_linked_modalities(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        illegal = [m for m in v if m not in MEMORY_MODALITIES]
        if illegal:
            raise ValueError(
                f"cross_modal.expected_linked_modalities 含非法模态 {illegal}；"
                f"必须取自 {MEMORY_MODALITIES}（fail-closed）"
            )
        duplicates = sorted({m for m in v if v.count(m) > 1})
        if duplicates:
            raise ValueError(
                f"cross_modal.expected_linked_modalities 含重复项 {duplicates}；"
                "本字段按**集合**比对（D4 禁止精确计数），重复项多半是笔误（fail-closed）"
            )
        return v

    @field_validator("required_relationships")
    @classmethod
    def _validate_relationships(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        illegal = [r for r in v if r not in CROSS_MODAL_RELATIONSHIP_VALUES]
        if illegal:
            raise ValueError(
                f"cross_modal.required_relationships 含非法关系 {illegal}；"
                f"必须取自 {CROSS_MODAL_RELATIONSHIP_VALUES}（fail-closed）"
            )
        duplicates = sorted({r for r in v if v.count(r) > 1})
        if duplicates:
            raise ValueError(
                f"cross_modal.required_relationships 含重复项 {duplicates}；"
                "本字段按**集合**比对（D4 禁止精确计数），重复项多半是笔误（fail-closed）"
            )
        return v


class IntegrationExpectationSuite(_StrictModel):
    """闭环集成期望顶层容器（ADR-0034 D4）。

    以**命名子期望**承载各关注点，避免单一 God Object 随阶段膨胀——未来
    ``human_feedback`` / ``privacy`` / ``security`` 直接加为**同级可选字段**，不污染现有
    结构。每个子期望**独立可选、互相正交**；未声明的字段不参与校验。

    与 ``BenchmarkExpectation`` 语义分离：后者测"感知该不该报警"，本 suite 测"报警后整条
    链该不该真落库 / 真发出通知"。

    > ``cross_modal`` 子期望（Phase B.2，F5）已落字段 ``CrossModalExpectation``；因基类
    > ``extra="forbid"``，场景 YAML 若误写未知键会**明确报错**而非静默忽略。声明了
    > ``cross_modal`` 期望却没注入 ``cross_modal_runtime``（闭环未启用跨模态 / 未建出边）
    > 即 F5 不通过（t8），而非静默通过。
    """

    perception: PerceptionExpectation | None = None
    memory: MemoryExpectation | None = None
    decision: DecisionExpectation | None = None
    action: ActionExpectation | None = None
    cross_modal: CrossModalExpectation | None = None
