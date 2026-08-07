# ADR-0030: 决策边界契约（Decision Boundary Contract）

- **Status**: Proposed（review-ready，待 Owner 冻结）
- **Date**: 2026-08-07
- **Owner**: SilverShield 技术负责人
- **Related**:
  - ADR-0010（单一决策中心）
  - ADR-0001（仅产事实不裁决）/ ADR-0002（隐私铁律）
  - ADR-0021（RiskSignal 信号层）
  - ADR-0024（Memory 架构·I4 可解释性 / I2 单调性）
  - ADR-0025（Memory Consumer 架构·C1 不决策 / C2 只读 / C3 确定性 / C5 溯源；C-6 ReasoningResult）
  - ADR-0027（音频记忆集成）/ ADR-0028（跨模态运行时接线）/ ADR-0029（跨模态检索与解释·`CrossModalContext` 进 `ReasoningInput`）
  - （规划）ADR-0031（Decision Audit Trace Contract，见 §5.1 路线图）
- **Phase**: v2 · Phase 3 → Memory 闭环 → 跨模态 Memory Graph → 跨模态可解释层 → **决策边界契约（Memory 首次进入决策的契约前提）**

---

## 0. 背景与动机（Context）

**SilverShield 的四段链路（感知 → Memory → Reasoning → Decision）在"决策"这一跳上出现了契约断层：Memory/Reasoning 的产出目前根本进不了 Decision。**

按项目范式（`MEMORY.md` 项目方向）：`Source → SegmentEvent → PerceptionEvent → RiskSignal → DecisionPolicy`，跨模态关联只在 `CrossModalEvidence` 层。代码实情：

> **引用约定（可维护性）**：本 ADR 引用代码时以**符号名 + 简短引文**为准（行号会随代码漂移，仅作辅助定位）。文中出现的行号**均截至 commit `b89bc7a`**（本 ADR 分支基点 = 当时 `origin/main`）；若行号与实际不符，以符号名与引文为准。

- **`DecisionPolicy.decide`**（抽象基类，`analysis/decision_policy.py`）签名为 `decide(perception_events: list[PerceptionEvent], ctx: DecisionContext) -> WarningEvent | None`，docstring 明写「输入：`List[PerceptionEvent]`（一次评估周期内的所有事件）」——**决策层只吃 `PerceptionEvent[]`**，对 Memory 一无所知；
- `RuleBasedMemoryConsumer` 已产出 `ReasoningInput`（含 `cross_modal_contexts`，ADR-0029），**`RuleBasedReasoningEngine.infer` 装配处**（`runtime/memory_consumer_hook.py`）已产出 `ReasoningResult`——但 `runtime/pipeline.py` 的 `reasoning_results` 字段注释白纸黑字写着 **「Shadow 观测，不接决策」**（另见同文件 Stage C/D 注释「Shadow Mode 不接决策」「默认 false，Shadow Mode 不产 Warning」）；
- **决策调用点**共三处——`PerceptionPipeline` 内两处 `self.decision_engine.evaluate(percs)` / `evaluate(rt_percs)`（`runtime/pipeline.py`），以及 `AudioSessionRecorder` 的 `self._decision_engine.evaluate(percs) if percs else None`（`runtime/audio_session_recorder.py`）——参数均为 `PerceptionEvent` 列表，无 Reasoning 维度。

> 一句话：**感知信号能直接触发告警，但「这个老人过去一月的画像 / 风险模式 / 冲突 / 跨模态合证」这些 Memory 苦心沉淀的上下文，对决策完全不可见。** 四段链路在 Decision 之前断成了"感知直连决策"和"Memory 仅供 Shadow 观测"两截。

### 0.1 为什么现在做（价值排序）

- **Memory 的价值必须流到决策**：四段架构承诺了"记忆改变理解"，若 Memory 永远不进入决策，等于只建不用——`ReasoningResult` 当前是孤儿（无消费者）；
- **契约先行、零行为风险**：先冻结 `DecisionInput` 这一**收敛载体**的形状与不变式，再谈"是否 / 如何"让 Memory 影响决策——把最敏感的"决策权威性"问题隔离在契约层讨论，实现时可分级 gate；
- **复用 ADR-0029 已落地的解释上下文**：`CrossModalContext` 已能随 `ReasoningInput` 流动，它作为"解释而非判断"的上下文天然适合在决策中作为 soft signal（不是判定）；
- **与既有隐私 / 不裁决铁律同向**：本 ADR 不引入任何新判定字段，只定义"既有四类型在决策链中的角色与边界"，把 ADR-0010 / ADR-0001 / ADR-0002 的纪律在契约层面钉死。

### 0.2 本 ADR 的边界（明确不做——**核心纪律**）

**本 ADR 是「决策边界契约」设计，不是「让 Memory 接管决策」的设计。一句话铁律：DecisionInput 是四段链路的唯一收敛点；上游三类型（RiskSignal / ReasoningInput / ReasoningResult）永远是事实与建议，绝不是决策。**

| 层 | 数据 | 职责 | 是否可决策 |
| - | - | - | - |
| 感知 | `RiskSignal` / `PerceptionEvent` | 聚合原始感知，产出 severity_hint（软分数，**非判定**） | **否**（仅触发） |
| Memory | `ReasoningInput` | Consumer 组装的记忆上下文（画像 / 模式 / 冲突 / 跨模态解释） | **否**（C1） |
| Reasoning | `ReasoningResult` | 参考推理产出 findings / explanation / **非绑定** hint | **否**（C1，hint 非决策） |
| Decision | `DecisionInput` | **唯一收敛载体**：汇聚感知 + 记忆 + 状态 + 既往决策 | **是**（ADR-0010 单一中心） |
| Decision | `WarningEvent` | 决策产物（risk_level + recommended_action） | 产出（终端） |

> **关键边界**：`RiskSignal` / `ReasoningInput` / `ReasoningResult` 不得含 `risk_score` / `decision` / `verdict` / `recommended_action`（上游守 C1）；即便 `ReasoningResult.suggested_action_hint` 与 `WarningEvent.recommended_action` 同源词表（MONITOR / NOTIFY_FAMILY / ESCALATE_COMMUNITY），它也**只是建议**，DecisionPolicy 可参考但**最终动作权威在 DecisionPolicy 自身的路由表**——hint 不能"下达"动作。

---

### 0.3 架构跃迁：从「感知系统」到「认知闭环系统」

ADR-0029 与 ADR-0030 一前一后，完成了 SilverShield 的一次架构跃迁：

- **ADR-0029** 解决「记忆如何被理解」——`Memory Graph → CrossModalLink → CrossModalContext → ReasoningInput`（解释而非判断）；
- **ADR-0030** 解决「理解如何被约束地影响行动」——`Perception → RiskSignal → Memory → Reasoning → Decision → WarningEvent`。

两者连起来，`ReasoningResult` 不再孤儿，四段链路首次闭环比：

```
             Perception
                 |
             RiskSignal
                 |
        +----------------+
        | Memory System  |
        +----------------+
                 |
        CrossModalContext
                 |
           ReasoningInput
                 |
          ReasoningResult
                 |
          DecisionInput
                 |
          DecisionPolicy
                 |
          WarningEvent
                 |
             Feedback
```

关键边界守恒：`Memory = context`、`Reasoning = suggestion`、`Decision = authority`。`DecisionInput` 是四段链路唯一收敛点（类比 Kubernetes API Server 是集群唯一控制入口、DB transaction boundary 是数据一致性入口）。

### 0.4 Decision Memory Validation History（决策记忆价值验证沿革）

ADR-0030 解决的是**架构问题**——"Memory 如何合法进入 Decision"；而"Memory 进入 Decision 后是否真的提升决策质量"是独立的**价值问题**（见 §6 切片 C）。为避免评审者产生"Memory 存在 → 所以应该影响决策"的不严谨推导，本节记录 SilverShield 已有的前置验证探索，作为本 ADR 的背景演进路线。

> 为什么我们相信 Memory 值得进入 Decision？本 ADR **不预设答案**，只冻结"未来任何验证都必须经 `DecisionInput` 唯一入口"这一边界；收益问题交给受控验证（§6 切片 C / ADR-0031）回答。

历史验证路线：

```
Phase A：视觉 Memory → Decision 可行性验证（已完成）

PerceptionEvent
        |
        v
Visual Memory
        |
        v
Reasoning Context
        |
        v
Decision Shadow Evaluation
```

目标：验证历史行为模式是否能够补充当前感知、Memory context 是否能够改变 Reasoning、Reasoning 是否具备进入 Decision 的工程价值。

当前状态：
- Phase A 已完成，证明 `Memory → Reasoning → Decision` 的技术路径可行；
- 但**尚未完成长期统计意义上的收益验证**（误报 / 漏报 / escalation 准确率 / 用户价值）；
- 后续因产品场景扩展与多模态方向调整，开发路线转向：

```
视觉 Memory
        ↓
跨模态 Memory Graph
        ↓
音频 Memory
        ↓
统一 Memory Context
```

因此 Decision Memory Validation 暂停在 A 阶段。本 ADR **不重新定义验证目标**，而冻结未来验证的唯一入口：

```
Memory / Reasoning
        |
        v
DecisionInput
        |
        v
DecisionPolicy
```

任何未来验证（视觉 Memory、音频 Memory、跨模态 Memory、LLM Reasoning）必须通过 `DecisionInput` 进入，避免各模块私接旁路决策路径——这正是本 ADR 作为 **Future Decision Experiment Boundary** 的核心价值。

---

## 1. 决策（Decision）

冻结**决策链路上四个数据契约的角色、形状与不变式**，并新增 `DecisionInput` 作为 DecisionPolicy 的**唯一、规范输入**：

1. **确认三已有契约的角色与边界**（不重新实现——它们已分别由 ADR-0021 / ADR-0025 / ADR-0029 冻结，本 ADR 仅确认其在决策链中的归属并补一处缺口）：`RiskSignal`（感知触发）、`ReasoningInput`（记忆上下文载体）、`ReasoningResult`（参考推理建议，当前孤儿）；
2. **定义 `DecisionInput`（新）**：把"感知触发（`PerceptionEvent[]`）+ 记忆上下文（`ReasoningInput`）+ 参考建议（`ReasoningResult`）+ 策略状态（`DecisionContext`）+ 既往决策（`WarningEvent`，迟滞用）"收敛为一个不可变结构体，作为 `DecisionPolicy.decide` 的唯一入参；
3. **演进 `DecisionPolicy.decide` 签名**：从 `decide(perception_events, ctx)` 演进为 `decide(input: DecisionInput)`——内部行为（路由表 / max-wins）逐字不变，即**行为兼容**；接口层面则是**调用方兼容、实现方破坏性**（见 D4）；
4. **为"Memory 进入决策"预留受控验证通道（契约先行，价值后验）**：`DecisionInput.reasoning_result` / `reasoning_input` 已就位，但**Memory 是否真正影响决策、是否真有收益**由 §6 切片 C（Controlled Validation Gate，门控，需 Owner 放行）决定——契约先冻结，行为分级开；且 Memory 影响决策的**前提是验证证明统计收益，而非契约存在**（见 §0.4 / D5）。

> 本 ADR **不实现任何模型 / 不写任何 dataclass 代码**——仅冻结契约形状与不变式，落地见 §6。

---

## 2. 决策要点（D1–D5）

### D1：四类型在决策链中的角色与归属（确认 + 补洞）

四类型并非本 ADR 全部新建，而是先确权：

| 类型 | 归属 ADR | 在决策链中的角色 | 本 ADR 动作 |
| - | - | - | - |
| `RiskSignal` | ADR-0021 | 感知层首个信号；经 `signal_adapter` 译为 `PerceptionEvent` 进决策 | **确认**：不含 risk_level/score/decision；`severity_hint∈[0,1]` 仅软感知分数 |
| `ReasoningInput` | ADR-0025（+ADR-0029 加 `cross_modal_contexts`） | Consumer→Reasoning 唯一载体；记忆上下文 | **确认**：C1 仍成立；其 `cross_modal_contexts`/`risk_pattern`/`conflicts` 是 Memory 进决策的解释来源 |
| `ReasoningResult` | ADR-0025 C-6 | Reasoning Engine 产出；**当前孤儿（Shadow only）** | **确认 + 补洞**：定义其经 `DecisionInput.reasoning_result` 进入决策的契约路径（行为接否归切片 C） |
| `DecisionInput` | **本 ADR（新）** | **唯一收敛载体**，DecisionPolicy 唯一入参 | **定义**（D2） |

**补洞点**：`ReasoningResult` 此前无消费者。本 ADR 不强迫它立即影响决策，但把它**正式接入契约图**——未来任何"让 Memory 进决策"的实现都必经 `DecisionInput.reasoning_result`，杜绝各自私接。

### D2：`DecisionInput` 契约字段（规范，非实现）

`DecisionInput` 是不可变（frozen）收敛结构体，建议字段（落地时定稿）：

| 字段 | 类型 | 含义 | 是否可空 |
| - | - | - | - |
| `trigger_events` | `tuple[PerceptionEvent, ...]` | 本评估周期内的感知触发事件（取代原 `list[PerceptionEvent]` 入参） | 否（至少含触发事件） |
| `reasoning_input` | `ReasoningInput \| None` | 完整记忆上下文（含 `cross_modal_contexts` / `risk_pattern` / `conflicts` / `visitor_profile`）——Memory 进决策的解释来源 | **是（默认 None）** |
| `reasoning_result` | `ReasoningResult \| None` | Reasoning Engine 对本输入的参考建议；推理跳过 / 失败时为 `None` | 是（默认 None） |
| `decision_context` | `DecisionContext` | 策略执行上下文（`elder_id` / `now` / `extra`），沿用既有 | 否 |
| `prior_warning` | `WarningEvent \| None` | 既往决策（迟滞 / 幂等用）；非决策真相来源 | 是（默认 None） |

> **`reasoning_input` 为何可空（Memory 可缺席原则）**：`DecisionInput` 必须能在 **Memory 未启用 / 未接线 / 检索失败** 三种情形下合法构造，否则 D4 适配层与切片 B 的"零行为变化"路径根本无法成立（`DecisionEngine.evaluate` 装配时并无 Memory 上下文可填）。因此 `reasoning_input=None` 是**一等合法状态**，语义为"本次决策无记忆上下文"，`DecisionPolicy` 此时退化为纯感知决策（与今日逐字段一致，见 §5 降级语义）。
> 推论：`DecisionPolicy` 读取 Memory 相关字段时 **MUST** 做 `None` 守卫；**禁止**把"Memory 缺席"当作风险信号（缺席即中性，不得因无记忆而抬升或降低风险——该纪律与 C6/C1 同源）。

- **冗余但显式**：`reasoning_input.current_event` 与 `trigger_events` 存在重叠（同一触发可能既在 `current_event` 也在 `trigger_events`）。保留 `trigger_events` 是因为 `DecisionPolicy` 现有路由逻辑需要 `PerceptionEvent` 的 `score` / `is_odd_hour` / `device_id` 等字段，而 `CurrentEvent` 投影刻意省略了这些——Decision 需要原始感知语义，不依赖 Consumer 的投影。
- **确定性（C3）**：`from_dict` / `to_dict` 与 `contracts.py` 同构（datetime→ISO、枚举→value、tuple→list）；`trigger_events` 按 `timestamp` 升序固定。
- **契约白名单**：引入 `DECISION_INPUT_FIELD_WHITELIST`（类比 `REASONING_INPUT_FIELD_WHITELIST`），断言 `DecisionInput` 不含 `risk_score` / `decision` / `verdict` / `recommended_action`（这些是**输出**语义，不得内嵌于输入载体）。

### D3：四类型统一不变式（契约层）

沿用并强化既有纪律，把"决策边界"提升为可测不变式：

- **C1（ADR-0010 单一决策中心）**：`RiskSignal` / `ReasoningInput` / `ReasoningResult` **不含**任何决策语义字段（`risk_score` / `decision` / `verdict` / `recommended_action` / `warning`）。`ReasoningResult.suggested_action_hint` 是**非绑定建议**：DecisionPolicy **可参考但不得将其当作决策**，且 hint **只能参与路由排序（ranking / tie-breaker），绝不能被直接赋值为 `WarningEvent.recommended_action`**——最终动作权威在 `DecisionPolicy` 路由表（见 D4/D5）。`DecisionInput` 自身也**不内嵌决策**——它是"喂给决策的输入"，`risk_level` / `action` 是其**输出** `WarningEvent` 的属性，不在本结构体。该纪律由 `test_reasoning_hint_is_never_authoritative` 钉死：在 `RuleBasedDecisionPolicy` 内部，hint 只能改变候选排序、不能替代路由表选定动作（见 D5 / Slice C）。
- **C2（DecisionInput = 收敛边界）**：四类型均为 frozen；容器用 tuple。`DecisionInput` 是**唯一**把感知 + 记忆 + 状态 + 既往决策组合起来的结构体；上游组件（Consumer / Reasoning / Perception）不得各自向 Decision 私送字段。
- **C3（确定性）**：同 `DecisionInput` → 同 `WarningEvent`（审计 / 回放一致）；`from_dict`↔`to_dict` 往返稳定；`trigger_events` 固定排序。
- **C4（跨模态不判断泄漏）**：`CrossModalContext` 仅经 `reasoning_input.cross_modal_contexts` 作为**解释上下文**进入 `DecisionInput`；`link_confidence` 是"建边置信"**非**风险分，DecisionPolicy **不得**拿它当阈值（如 `if link_confidence > 0.8: alert()`）——该禁令由契约测试钉死（§6 验收 4）。
- **C5（隐私，ADR-0002 / ADR-0025 §3.1）**：`device_id` **不得**成为**决策特征**。`reasoning_input.historical_context` 内的 `EpisodicRecord` 可能带 `device_id`（ADR-0025 已知张力），但 `DecisionPolicy` **必须忽略**它；决策的**语义结论**对"部署源身份"不变。

  **精确定义（避免不可实现的断言）**：`WarningEvent` 自身携带 `device_id`（`RuleBasedDecisionPolicy` 装配 `WarningEvent` 时透传 `candidates[0].device_id`），因此 **"两 `DecisionInput` 仅 `device_id` 不同 → `WarningEvent` 逐字段全等"是必然失败的错误表述**。本不变式收紧为**决策语义字段子集不变**：

  | 字段 | C5 归类 | 断言要求 |
  | - | - | - |
  | `risk_level` | 决策语义 | **MUST 相等** |
  | `recommended_action` | 决策语义 | **MUST 相等** |
  | `reason_summary` | 决策语义 | **MUST 相等** |
  | `perception_score` | 决策语义 | **MUST 相等** |
  | `meta["trigger_event_types"]` / `meta["policy"]` / `meta["routing_table_version"]` | 决策语义 | **MUST 相等** |
  | `device_id` | **溯源透传**（非决策特征） | 允许不同（预期随输入变化） |
  | `elder_id` / `created_at` / `meta["decided_at"]` / `trigger_events[].event_id` | 溯源 / 时序 | 不在断言范围（由 `ctx` 与输入决定） |

  契约测试 `test_decision_invariant_to_device_id` **只断言上表"决策语义"子集相等**，并显式注释"`device_id` 差异是预期的溯源透传，不构成 C5 违规"。**反向变异验证**（防止测试空转）：若 `DecisionPolicy` 被人为改为读取 `device_id` 参与路由（如 `if device_id == "cam-01": level = "HIGH"`），该测试 **MUST 变红**。
- **C6（迟滞 / 幂等 seam）**：`prior_warning` 仅用于迟滞（同 `recommended_action` 在窗口内抑制重复告警），**不是决策真相来源**——`DecisionPolicy` 仍按 `trigger_events` + `reasoning_*` 独立决策，`prior_warning` 只能"压"不能"抬"也不能"替代"。
- **C7（一级聚合约束，防 God Object）**：`DecisionInput` **只允许一级聚合**——当前 v1 直接展平 `trigger_events` / `reasoning_input` / `reasoning_result` / `decision_context` / `prior_warning` 五个字段是受控的（字段数固定、语义正交）。本 ADR **显式禁止**未来无约束地横向堆字段（如 `environment_context` / `user_preferences` / `policy_override` / `external_signal` / `model_output` 等直接挂到 `DecisionInput` 顶层）。演进路径：若字段持续增长，须先抽象为具名 Bundle（`PerceptionBundle` / `CognitionBundle` / `StateBundle`），再让 `DecisionInput(perception=..., cognition=..., state=...)` 聚合——**不允许出现 20+ 扁平字段的 God Object**。v1 不强制改结构，仅在此立防膨胀约束，供后续 ADR / 评审拦截。
- **C8（决策输出耦合一致性，Outcome Coupling）**：`WarningEvent` 的 `(risk_level, recommended_action)` **MUST** 恒为**当前生效路由表中的一条合法条目**（`LegalOutcomes(routing_table)` 成员），**禁止**两字段被独立赋值 / 独立提升。今日代码天然满足（`final_level` 与 `final_action` 均由路由表查表得出）；本不变式的意义是**约束未来**——切片 C 引入 Memory 软信号时必须按 D5 的 outcome lattice **整体提升二元组**，否则会产出 `HIGH + MONITOR`、`LOW + ESCALATE_COMMUNITY` 等路由表中不存在的自相矛盾组合，破坏 `HIGH → ESCALATE_COMMUNITY` 语义耦合并污染下游 ActionLayer / Dashboard。由 `test_decision_outcome_always_in_routing_table` 穷举钉死。

### D4：`DecisionPolicy.decide` 签名演进（**调用方兼容 / 实现方破坏性**）

> **术语精确性**：本节**不是**无条件的"向后兼容"。兼容性对两类受众截然不同，必须分开陈述，避免后续读者低估迁移成本：
>
> | 受众 | 影响 | 性质 |
> | - | - | - |
> | **调用方**（`DecisionEngine.evaluate(perception_events)` 及其下游：`PerceptionPipeline`、`AudioSessionRecorder`） | 零改动——适配层吸收签名变化 | **兼容** |
> | **实现方**（`DecisionPolicy` 抽象基类的所有子类，含 `RuleBasedDecisionPolicy` 及测试内自定义 policy / mock） | 必须同步改签名，否则 `TypeError` / 抽象方法不匹配 | **破坏性（breaking）** |
> | **测试** | 直接调 `policy.decide(events, ctx)` 的既有用例需改为装配 `DecisionInput` | **破坏性（breaking）** |

- 抽象基类签名：`decide(input: DecisionInput) -> WarningEvent | None`（替代 `decide(perception_events, ctx)`）。
- `RuleBasedDecisionPolicy.decide` 内部：`trigger_events = input.trigger_events`、`ctx = input.decision_context`，**路由表 / max-wins / reason 合并逻辑逐字不变**；`reasoning_result` / `prior_warning` 在切片 B 阶段**不消费**（保持 perception-only 决策，输出与今日逐字段一致）。
- `DecisionEngine.evaluate(perception_events)` 适配层负责装配 `DecisionInput`（`trigger_events=perception_events`、`reasoning_input=None`、`reasoning_result=None`、`decision_context`、`prior_warning=None`），使既有**调用方**零改动。该装配依赖 D2 的 `reasoning_input` **可空**（Memory 未接线时无上下文可填）——若 D2 声明非空，此路径不成立。
- **破坏性变更管理（对实现方）**：`decide` 签名变化影响**所有 `DecisionPolicy` 子类与直接调用 `decide` 的测试**；切片 B 必须在**同一 PR 内原子完成** `RuleBasedDecisionPolicy` + `DecisionEngine` + 全部 decision 测试的同步更新（不可分批合并，否则中间态 CI 必红）。验收要求"既有 decision **行为**逐字段回归"——即**行为兼容、接口破坏**。
- **迁移成本定性**：本仓 `DecisionPolicy` 实现者数量有限（主要为 `RuleBasedDecisionPolicy` + 测试替身），故破坏性可控；但**不得**因"仓内可控"而在文档上淡化为"向后兼容"——外部若已有第三方策略实现，属 MAJOR 级接口变更（ADR-0014 冻结治理口径）。

### D4.1 Slice B 迁移说明：`trigger_events` 构造期规范化（C3 契约级，非路由回归）

> **为何要单独说清**：D4 承诺"路由逻辑逐字不变、输出与今日逐字段一致"，但 `DecisionInput` 在构造期对 `trigger_events` 按 `timestamp` 做了**稳定升序规范化**（C3）。这与旧 `decide(perception_events, ctx)` **保留调用方传入顺序** 不同，属于**有意的契约级行为**，不是 Slice B 的路由回归——两者必须分开陈述，否则读者会误以为排序是回归或误以为旧顺序被保留。

- **旧接口语义**：`RuleBasedDecisionPolicy.decide(perception_events, ctx)` 直接消费调用方传入的 `perception_events` 顺序；该顺序会沿 `candidates = significant + odd_hour_events` 影响 `reason_summary` 合并次序、`WarningEvent.device_id`（`candidates[0].device_id`）以及 `trigger_events` 摘要次序。
- **新接口语义（C3）**：`DecisionInput.__post_init__` 在构造期按 `timestamp` 稳定升序重排 `trigger_events`，**与传入次序无关**。因此下游 `reason_summary` / `device_id` / `trigger` 摘要顺序由 `timestamp` 决定，**不再依赖调用方顺序**。
- **这是 C3 的契约要求，不是 Slice B 行为回归**：ADR-0030 C3 要求"同 `DecisionInput` → 同 `WarningEvent`（回放 / 审计一致）"。若保留调用方顺序，同一组事件的不同排列会产生不同 `WarningEvent`，违反 C3。`RuleBasedDecisionPolicy` 的**路由逻辑（优先级 / max-wins / reason 合并 / 阈值）逐字不变**，仅输入顺序在构造期被规范。
- **影响面与防护**：仅当调用方传入**非 timestamp 有序**的 `trigger_events` 时，新旧路径的 `reason_summary` / `device_id` 顺序可能不同；既有测试（`DecisionEngine.evaluate` 装配路径、仓内单测）传入的事件在构造期即被规范，故**既有决策测试全绿**。为固定这一可观察行为、防止排序逻辑后续被悄悄改动，新增回归测试钉死：乱序 / 多设备 / 多事件输入经 `RuleBasedDecisionPolicy.decide` 后产出**确定**的 `risk_level` / `recommended_action` / `device_id` / `reason_summary` / `trigger` 顺序（见 `tests/test_warning.py` 的 `TestRuleBasedDecisionPolicy`：`test_shuffled_input_yields_deterministic_warning` / `test_device_id_resolves_to_earliest_timestamp_candidate`）。
- **调用方约束**：**不得依赖 `trigger_events` 的传入顺序在构造后存活**——构造即规范。

### D5：Memory → Decision 受控验证门（Controlled Validation Gate，非上线能力）

这是本 ADR 最具杠杆也最敏感的一步——把 `runtime/pipeline.py` 中 `reasoning_results` 的「Shadow 观测，不接决策」限制翻转为"可受控验证接入"。**关键定位**：切片 C 是**验证能力，不是上线能力**；Memory 进入 Decision 的必要前提**不是契约存在，而是经过 Shadow Validation 证明具有统计收益**（见 §0.4）。**门控原则**：

- **（验证通过 + Owner 放行后生效）唯一、受控的 Memory 软信号使用（outcome lattice + max-only，**二元组整体提升**）**：`RuleBasedDecisionPolicy` 可读取 `reasoning_input.conflicts`（如含 `risk_escalation`：历史 LOW→当前 HIGH）与 `reasoning_result.suggested_action_hint`，但 Memory 软信号**只能取偏序 max，绝不能 replace / 降级 / 逆转**——即**只升不降**。

  **关键修正（防止产出非法组合）**：偏序格**不能建立在 `recommended_action` 单一维度上**。现状代码里 `risk_level` 与 `recommended_action` 是**强耦合**的——二者均由路由表查表得出（`final_level` = candidates 的 max level、`final_action` = chosen event 的 per-event action），因此今日输出的 `(risk_level, recommended_action)` **必然是路由表中的一条合法条目**。若 Memory 软信号只抬 `recommended_action` 而不动 `risk_level`（或反之只抬 `risk_level`），就会产出 `HIGH + MONITOR`、`LOW + ESCALATE_COMMUNITY` 这类**路由表中不存在的非法组合**，破坏 `HIGH → ESCALATE_COMMUNITY` 的语义耦合，并让下游 ActionLayer / Dashboard 面对自相矛盾的告警。

  因此本 ADR 把格定义在**决策结果二元组**上：

  ```
  合法结果集（由「当前生效的」路由表派生，非硬编码）：
      LegalOutcomes(routing_table) = { (level, action) | (level, action, _) ∈ routing_table.values() }

  以 DEFAULT_ROUTING_TABLE 为例，全序为：
      (LOW, MONITOR)  <  (LOW, NOTIFY_FAMILY)  <  (HIGH, ESCALATE_COMMUNITY)

  排序键：(LEVEL_PRIORITY[level], ACTION_RANK[action])  —— 字典序
          LEVEL_PRIORITY = {LOW:1, MEDIUM:2, HIGH:3}（沿用既有）
          ACTION_RANK    = {MONITOR:1, NOTIFY_FAMILY:2, ESCALATE_COMMUNITY:3}

  最终结果：
      final_outcome = max(lattice, perception_outcome, memory_outcome)
      (final_level, final_action) = final_outcome        # 整体取，不拆字段
  ```

  - **二元组原子性**：`final_level` 与 `final_action` **MUST** 同时来自**同一条** `LegalOutcomes` 条目，**禁止**分别独立取 max（这正是产生 `HIGH+MONITOR` 的根因）；
  - **hint 投影 + fail-closed**：`suggested_action_hint` 只是一个 action，**不是**合法 outcome，须先投影为 `LegalOutcomes` 中"`action == hint` 的**最低**档条目"再参与 max；若当前路由表中**不存在**该 action（路由表可按家庭定制注入），则该 hint **不可用、直接忽略**（fail-closed，不得臆造 level 配对）；
  - **路由表派生而非硬编码**：格必须从**运行时生效的** `routing_table` 派生——因 `RuleBasedDecisionPolicy(routing_table=...)` 支持按家庭 / 地区定制注入，硬编码三档格会在定制路由表下失效；
  - **只升不降**：感知已选 `(LOW, NOTIFY_FAMILY)` 而 Memory 历史正常时**不得降级**为 `(LOW, MONITOR)`；感知选 `(LOW, MONITOR)` 且 Memory 显式 escalation 时可升 `(LOW, NOTIFY_FAMILY)`。

  该语义由 `test_decision_action_lattice_max_only` + `test_decision_never_lowers_below_perception` + **`test_decision_outcome_always_in_routing_table`**（穷举所有 Memory 信号组合，断言输出二元组恒 ∈ `LegalOutcomes`，钉死永不出现 `HIGH+MONITOR`）+ **`test_hint_with_unknown_action_is_ignored`**（fail-closed）共同钉死。
- **不动权威性**：DecisionPolicy 仍是 ADR-0010 单一决策中心；上游 `ReasoningResult` 的 hint 永远只是"建议"，C1 守卫测试 `test_reasoning_hint_cannot_override_action` + `test_decision_never_lowers_below_perception` 钉死。
- **显式 gate**：切片 C 单独成 PR + Owner 评审放行；本 ADR 冻结契约（D2 字段已就位），但**默认 Shadow Mode、不默认开启** Memory→决策 的线上影响——其上线前提是受控验证证明统计收益，而非契约存在。

### 2.1 硬约束（ADR-0030 Invariants，契约测试钉死）

- `RiskSignal` / `ReasoningInput` / `ReasoningResult` **MUST NOT** 含 `risk_score` / `decision` / `verdict` / `recommended_action` / `warning`（C1，由各类型白名单 + 契约测试断言）；
- `DecisionInput` **MUST NOT** 内嵌决策语义字段（C1，白名单断言）；
- `CrossModalContext` 经 `DecisionInput` 进入时**只作解释上下文**，`link_confidence` **MUST NOT** 被 DecisionPolicy 用作风险阈值（C4）；
- 决策的**语义结论**对 `device_id` **MUST** 不变（C5）——即 `risk_level` / `recommended_action` / `reason_summary` / `perception_score` 相等；`WarningEvent.device_id` 作为溯源透传字段**允许**不同，不在断言范围（`test_decision_invariant_to_device_id`，见 D3-C5 字段表）；
- 同 `DecisionInput` → 同 `WarningEvent`（C3），回放 / 审计一致；
- Memory 软信号（经 `reasoning_input` / `reasoning_result`）**MUST** 按 **outcome lattice** `max-only` 影响决策（仅升不降、不改派、**二元组整体提升**），`suggested_action_hint` **MUST NOT** 被直接赋值为 `recommended_action`（D5 / C1）；
- `WarningEvent` 的 `(risk_level, recommended_action)` **MUST** 恒 ∈ 当前生效路由表的合法条目集，**MUST NOT** 出现 `HIGH + MONITOR` 这类独立提升产生的非法组合（C8，`test_decision_outcome_always_in_routing_table`）；
- `reasoning_input` / `reasoning_result` 为 `None`（Memory 未启用 / 未接线 / 检索失败）时决策 **MUST** 合法退化为纯感知，且 **MUST NOT** 把"Memory 缺席"当作风险信号（D2 Memory 可缺席原则）；
- `prior_warning` **MUST NOT** 替代独立决策（C6）；
- `DecisionInput` **MUST** 保持一级聚合、禁止无约束横向堆字段（C7，防膨胀）；
- 唯一合法的"判断"出口仍是 `DecisionPolicy` → `WarningEvent`（ADR-0010），其余一切产物都不得越界。

---

## 3. 动机（Rationale）

1. **四段链路必须闭环比**：感知直连决策 + Memory 仅供 Shadow，是当前架构的真实状态；不冻结 `DecisionInput`，"Memory 进决策"永远只能各自私接，无法审计、无法守 C1；
2. **契约先行、风险隔离**：把最敏感的"决策权威性"讨论锁进契约层——`DecisionInput` 形状冻结后，未来无论 ML 评分（v2）还是 LLM 解释（v3）策略，都复用同一收敛载体，Decision 入口稳定；
3. **复用 ADR-0029 解释上下文**：`CrossModalContext` 已是"解释而非判断"，作为 Decision 的 soft signal 天然合规，不引入新判定字段；
4. **与既有铁律同向、零新判定**：C1–C8 全部强化既有纪律（ADR-0010 / ADR-0001 / ADR-0002 / ADR-0025 / ADR-0029），无冲突、无新风险字段；其中 C7（防 God Object）/ C8（输出耦合一致性）是对**未来演进**的护栏，对今日行为零影响；
5. **分级 gate 的纪律**：契约冻结 ≠ 行为立即改变；切片 C 把"是否让 Memory 影响决策"显式留给 Owner 评审，避免 AI 擅自扩大决策权威性。

---

## 4. 后果（Consequences）

### 正面

- 决策链路首次有**显式契约边界**：四类型角色、形状、不变式被钉死，可测、可审计；
- `ReasoningResult` 孤儿状态结束（契约层面接入图），为"Memory 进决策"铺好受控通道；
- `DecisionPolicy.decide` 签名统一为单入参，未来策略可替换（ML/LLM）零改动入口；
- 隐私与不裁决铁律在契约层二次加固（C5 device_id 不变 / C1 上游无决策）。

### 负面 / 代价

- `DecisionPolicy.decide` 签名破坏性变更：所有实现 + decision 测试需同步更新（切片 B 成本）；
- 新增 `analysis/decision_contract.py`（或扩展 `decision_policy.py`）与 `DECISION_INPUT_FIELD_WHITELIST` 维护成本；
- 若开启切片 C，Decision 行为从"纯感知"变为"感知 + Memory 软信号"——需 Owner 评审接受（权威性变动）；
- `DecisionInput` 携带 `reasoning_input`（含 `EpisodicRecord.device_id`），对 DecisionPolicy 的"忽略 device_id"纪律提出更高测试要求（C5）。

### 必须承担的技术债 / 后续动作

- 切片 C 受控验证门（默认 Shadow Mode；验证证明统计收益 + Owner 放行后才可能进入 outcome lattice max-only，**二元组整体提升**见 C8）；
- `suggested_action_hint` 与 `WarningEvent.recommended_action` 同源词表的"建议 vs 决策"边界长期守护（防漂移成事实上的决策）；
- `RiskSignal` 是否需 `correlation_id` 承载跨模态关联（归 ADR-0028 后续）；
- 跨模态解释 enrich `WarningEvent.reason_summary`（切片 E，解释非判断）。

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

- **切片 C 是否随本 ADR 直接门控放行，还是仅合契约（切片 A/B）后独立 PR 决定？** 建议 A/B 先合（零行为变化），C 单独 PR + Owner 评审——把"Memory 是否值得 / 如何进入决策"作为独立**验证决策**（默认 Shadow，验证证明收益 + Owner 放行后才进入 max-only）；
- **`RiskSignal` 是否需 `correlation_id`** 承载跨模态关联（当前 `CrossModalLink` 不回写 `RiskSignal`）——归 ADR-0028 后续；
- **`DecisionInput` 是否需顶层 `visitor_instance_id` 快捷字段**（目前经 `reasoning_input.current_event.visitor_instance_id` 取）——归实现细节；
- **`prior_warning` 迟滞窗口 / 策略归属**（配置 vs 硬编码）——v1 简单窗口，未来可配；
- **`ReasoningResult.findings` 自然语言是否适合直接进 `reason_summary`**（可能需结构化提取）——归切片 E；
- **Decision 失败 / `reasoning_result=None` 的降级语义**：推理不可用时决策是否退化为纯感知——默认退化为纯感知（与今日一致），由切片 B 保证。
- **`DecisionInput` 是否需 `correlation_id` 承载审计血缘（correlation lineage）**：本 ADR 明确**现在不加**。但记录其必要性——未来「为什么这个 Decision 发生」的可解释审计链（感知 → Memory → Reasoning → Decision → Warning 全链路 `correlation_id` 串联）将依赖 correlation lineage；该契约归后续 **ADR-0031 Decision Audit Trace Contract**（见 §5.1），本 ADR 不抢答其形状。

---

## 5.1 后续 ADR 路线图（Roadmap）

ADR-0030 冻结的是"决策边界契约"，但**契约存在 ≠ Memory 应该影响决策**——"Memory 是否值得进入 Decision"是独立的价值验证问题（见 §0.4）。本 ADR 的真正价值是建立 **Future Decision Experiment Boundary**：任何 Memory（视觉 / 音频 / 跨模态 / LLM Reasoning）要影响决策，都必须经 `DecisionInput` 唯一入口，杜绝各模块私接旁路。建议时序（Owner 评审确认）：

```
ADR-0030 Accepted（决策边界契约）
      ↓
Slice A：DecisionInput 契约定义（零行为变化）
      ↓
Slice B：DecisionPolicy 签名迁移（零行为变化）
      ↓
新增 ADR-0031：Decision Audit Trace Contract（决策审计血缘）
      ↓  ┌─────────────────────────────────────┐
         │ 双轨比较需完整 trace 才能审计         │
         │ baseline(perception-only) vs          │
         │ candidate(perception+memory)          │
         └─────────────────────────────────────┘
      ↓
Slice C：Memory → Decision Controlled Validation Gate
         （默认 Shadow Mode，双轨比较；验证证明收益 +
          Owner 放行后才进入 outcome lattice max-only 模式）
```

- **ADR-0031（Decision Audit Trace Contract，建议后续独立起草）**：定义 `DecisionTrace`——
  `{ decision_id, trigger_events_refs, reasoning_context_refs, reasoning_result_ref, policy_version, chosen_action, rejected_actions, timestamp }`，
  目标是把"为什么报警"钉死为可审计事实链：**哪个感知事件触发 → 哪些 Memory 被参考 → Reasoning 给了什么建议 → DecisionPolicy 为何选此 action**。它是 **Slice C 验证门的硬性前置**——没有完整 trace 就无法做 `baseline`（perception-only）vs `candidate`（perception+memory）的可审计双轨比较；同时也为将来任何 Memory 接入提供统一可观测基座。它将 ADR-0029「可解释 Memory」直接连到 ADR-0030「决策边界」。

---

## 6. 实施切片（实施顺序，冻结后执行）+ 开发方向

> 本 ADR 只冻结契约；以下切片为**开发方向**，按"契约先行、行为分级"推进。A/B 零行为变化、可先合；C/D/E 动决策行为，门控评审。

- **Slice A（契约定义，零行为变化）**：新增 `DecisionInput` frozen 契约（字段见 D2）+ `from_dict` / `to_dict`（与 `contracts.py` 同构）；新增 `DECISION_INPUT_FIELD_WHITELIST` + 契约测试 `test_decision_input_has_no_decision_fields`（C1）、`test_decision_input_roundtrip`（C3）、`test_decision_input_backward_compatible_without_reasoning_result`（`reasoning_result` 缺省 None，护旧序列化）、**`test_decision_input_valid_without_memory`**（`reasoning_input=None` + `reasoning_result=None` 可合法构造且往返稳定——D2 Memory 可缺席原则，护 D4 适配层与切片 B 路径）。**不改变 `DecisionPolicy` 任何行为**。
- **Slice B（签名演进，行为零变化；调用方兼容 / 实现方破坏性）**：`DecisionPolicy.decide(input: DecisionInput)` 抽象签名演进；`RuleBasedDecisionPolicy.decide` 内部取 `trigger_events` / `ctx`，路由逻辑逐字不变；`DecisionEngine.evaluate` 装配 `DecisionInput`（`reasoning_input=None` / `reasoning_result=None` / `prior_warning=None`，依赖 D2 可空）保持 perception-only 决策，`WarningEvent` 输出与今日逐字段一致；**同一 PR 内原子更新全部 `DecisionPolicy` 子类与 decision 测试**（见 D4 破坏性变更管理）。
- **Slice C（Memory → Decision Controlled Validation Gate，验证能力非上线能力，门控，需 Owner 放行）**：运行时把 `memory_consumer_hook` 已产出的 `ReasoningResult` 接入 `DecisionInput.reasoning_result`（翻 `pipeline.py` 中 `reasoning_results` 的 Shadow 限制），但**默认 Shadow Mode**——不改变线上 `WarningEvent`，仅做双轨比较：`baseline`（perception-only）vs `candidate`（perception + memory），比较 false positive / false negative / escalation accuracy / explanation quality；**通过 Owner approval 后才允许进入 outcome lattice `max-only` 模式**（此时 `RuleBasedDecisionPolicy` 引入唯一受控 Memory 软信号——`conflicts` 含 `risk_escalation` 或 `hint` 等级高于感知触发时，按 **outcome lattice 整体提升 `(risk_level, recommended_action)` 二元组**，只升不降、不改派、不拆字段）。C1/C8 守卫测试 `test_decision_never_lowers_below_perception` + `test_reasoning_hint_cannot_override_action` + `test_reasoning_hint_is_never_authoritative` + `test_decision_action_lattice_max_only` + `test_decision_outcome_always_in_routing_table` + `test_hint_with_unknown_action_is_ignored`。**本切片动决策权威性，单 PR + Owner 评审；其上线的前提是验证证明统计收益，而非契约存在**。
- **Slice D（隐私 + 幂等加固）**：`prior_warning` 迟滞（同 action 窗口内抑制重复告警，可调）；C5 契约测试 `test_decision_invariant_to_device_id`（仅 `device_id` 不同的两 `DecisionInput` → **决策语义字段子集相等**：`risk_level` / `recommended_action` / `reason_summary` / `perception_score`；`WarningEvent.device_id` 溯源透传允许不同，见 D3-C5 字段表）；C4 契约测试 `test_decision_invariant_to_link_confidence`（`link_confidence` 变化不改变决策语义字段）。
- **Slice E（跨模态解释进 reason，可选）**：`reasoning_input.cross_modal_contexts` enrich `WarningEvent.reason_summary`（仅解释，如追加"视觉：老人跌倒 与 音频：撞击声 相互支撑"），不新增判定；受 ADR-0029 C6 约束。

### 验收清单（Acceptance Criteria）

1. **D1 角色确权**：`RiskSignal` / `ReasoningInput` / `ReasoningResult` 不含 `risk_score` / `decision` / `verdict` / `recommended_action`（既有白名单 + 本 ADR 确认仍绿）；`ReasoningResult` 经 `DecisionInput.reasoning_result` 正式接入契约图；
2. **D2 `DecisionInput` 字段**：含 `trigger_events` / `reasoning_input`(**可 None**) / `reasoning_result`(可 None) / `decision_context` / `prior_warning`(可 None)；`DECISION_INPUT_FIELD_WHITELIST` 不含决策语义字段（C1）。**Memory 可缺席**：`test_decision_input_valid_without_memory` 钉死 `reasoning_input=None` + `reasoning_result=None` 可合法构造且往返稳定，`test_decision_degrades_to_perception_only_without_memory` 钉死此时决策输出与纯感知路径逐字段一致；
3. **D3 不变式契约测试**：C1（无决策字段）/ C2（frozen + tuple）/ C3（`from_dict`↔`to_dict` 往返 + 同输入同输出）/ C5（`test_decision_invariant_to_device_id`，**断言范围 = D3-C5 字段表中的"决策语义"子集，`WarningEvent.device_id` 显式排除**）/ C6（`prior_warning` 不替代独立决策）全部钉死；
4. **C4 跨模态不判断**：`test_decision_invariant_to_link_confidence` 钉死 `link_confidence` 不成为决策阈值；
5. **D4 行为兼容 + 迁移完整性**：`RuleBasedDecisionPolicy.decide(input: DecisionInput)` 路由逻辑与今日逐字一致；`DecisionEngine.evaluate(perception_events)` 装配 `DecisionInput` 后输出 `WarningEvent` 与今日逐字段相同（既有 decision 测试全绿）。**接口迁移完整性**：仓内所有 `DecisionPolicy` 子类（含测试替身）已在同一 PR 内改完新签名，无遗留旧签名实现；
6. **D5 门控 + outcome lattice**：切片 C 未开启时 `reasoning_result` 被忽略、决策退化为纯感知（与今日一致）；切片 C 开启后按 **outcome lattice** `max-only`（仅升不降、不改派、**`(risk_level, recommended_action)` 二元组整体提升**），C1/C8 守卫测试 `test_decision_never_lowers_below_perception` / `test_reasoning_hint_cannot_override_action` / `test_reasoning_hint_is_never_authoritative` / `test_decision_action_lattice_max_only` / **`test_decision_outcome_always_in_routing_table`** / **`test_hint_with_unknown_action_is_ignored`** 通过；
7. `DecisionInput` **不含** `device_id` 作为决策维度（C5）；`reasoning_input.historical_context` 内 `EpisodicRecord.device_id` 存在但 `DecisionPolicy` 不读取（契约测试覆盖）。注意区分：**`WarningEvent.device_id` 是决策产物的溯源透传字段，不是决策特征**——C5 禁止的是"`device_id` 影响决策语义"，不是"`WarningEvent` 携带 `device_id`"；
8. 全量 pytest + ruff 全绿（AGENTS.md 基线，不允许回归）。

---

## 7. 修订记录（Changelog）

> **修订权属（呼应 AGENTS.md §6.3「未授权改架构决策文件」）**：本 ADR 处于 Proposed 阶段由 Owner 评审；**冻结（Accepted）后的修订由 Owner 追加新条目，AI 不修改修订记录**。

- **2026-08-07**：初稿（Proposed）。基于代码实情——`DecisionPolicy.decide` 只吃 `PerceptionEvent[]`，而 `ReasoningResult` 已产出却仅 Shadow 观测（`runtime/pipeline.py` 的 `reasoning_results`「Shadow 观测，不接决策」），冻结决策边界契约：确认 `RiskSignal` / `ReasoningInput` / `ReasoningResult` 三已有类型的角色与 C1 边界，定义新收敛载体 `DecisionInput`（触发 + 记忆 + 建议 + 状态 + 既往决策），演进 `decide` 签名为单入参，并以 C1–C6 不变式钉死"上游永远事实/建议、Decision 唯一决策中心"。开发方向按"契约先行、行为分级"分 Slice A–E（A/B 零行为变化先合；C 动决策权威性、门控 Owner 评审）。本 ADR 仅冻结契约，不实现模型。
- **2026-08-07（修订三，Proposed 阶段）**：吸收第三轮 Owner 评审（PR #155 行内评论 6 条）——本轮聚焦**契约自洽性与可实现性**，修掉两处会让实现"一写就红"的缺陷：
  1. **索引补登（阻塞性）**：`docs/ADR/README.md` 清单末条停在 0028，**0029 / 0030 均未登记**，破坏 ADR 目录完整性约定。已按 0028 行格式补登两行。
  2. **D2 `reasoning_input` 可空性（契约缺陷）**：D2 原声明"是否可空：否"，而 D4 适配层与切片 B 均写 `reasoning_input=None`——**自相矛盾，向后兼容路径根本无法构造合法 `DecisionInput`**。已改为 `ReasoningInput | None`（默认 `None`），并补 **Memory 可缺席原则**（Memory 未启用 / 未接线 / 检索失败三态均须能合法构造；`None` 语义为"无记忆上下文"、退化纯感知；**禁止把"Memory 缺席"当风险信号**），同步验收清单第 2 条 + Slice A 新增 `test_decision_input_valid_without_memory`。
  3. **C5 不变式不可实现（契约缺陷）**：原表述"两 `DecisionInput` 仅 `device_id` 不同 → **同 `WarningEvent`**"必然失败——`WarningEvent` 自身携带 `device_id`（`RuleBasedDecisionPolicy` 透传 `candidates[0].device_id`），逐字段全等断言写出来就红。已收紧为**决策语义字段子集不变**，并给出**显式断言字段表**（`risk_level` / `recommended_action` / `reason_summary` / `perception_score` / `meta` 决策项 MUST 相等；`device_id` 归"溯源透传"、显式排除；`elder_id` / `created_at` / `decided_at` 不在范围），另加**反向变异验证**要求（policy 若真读 `device_id` 参与路由，测试 MUST 变红，防空转）。同步 §2.1 / Slice D / 验收 3、7。
  4. **行号引用不稳定（可维护性）**：原文硬编码 `decision_policy.py:74` / `pipeline.py:547,875,928` / `audio_session_recorder.py:237` / `memory_consumer_hook.py:190`——事实准确但会随代码漂移。已全部改为**符号名 + 简短引文**（`DecisionPolicy.decide` / `reasoning_results` 字段注释 / 三处 `evaluate` 调用点 / `RuleBasedReasoningEngine.infer` 装配处），并在 §0 加**引用约定**：行号仅作辅助、**均截至 commit `b89bc7a`**（本分支基点），冲突时以符号与引文为准。
  5. **D4「向后兼容」措辞失真（术语准确性）**：标题称"向后兼容"、正文却承认"签名变化影响所有实现与测试"。已改为 **「调用方兼容 / 实现方破坏性」**，并加**受众影响表**（调用方 `DecisionEngine.evaluate` 及 `PerceptionPipeline` / `AudioSessionRecorder` = 兼容；`DecisionPolicy` 所有子类 + 直接调 `decide` 的测试 = breaking），明确"**行为兼容、接口破坏**"、切片 B 须**同一 PR 内原子迁移**（分批必红 CI），并定性迁移成本（仓内实现者有限故可控，但对外属 MAJOR 级，守 ADR-0014 口径）。同步 §1 点 3 / Slice B / 验收 5。
  6. **D5 lattice 会产出非法组合（设计严谨性）**：原格建立在 `recommended_action` **单一维度**上，但现状 `risk_level` 与 `recommended_action` **强耦合**（二者均由路由表查表：`final_level` = candidates max level、`final_action` = chosen event 的 per-event action），单独抬一个字段会产出 `HIGH + MONITOR` / `LOW + ESCALATE_COMMUNITY` 等**路由表中不存在的自相矛盾组合**，破坏 `HIGH → ESCALATE_COMMUNITY` 语义耦合并污染下游 ActionLayer / Dashboard。已把格重定义在**决策结果二元组**上：`LegalOutcomes(routing_table) = {(level, action)}`、排序键 `(LEVEL_PRIORITY, ACTION_RANK)` 字典序、`final_outcome = max(...)` **整体取不拆字段**；补 **hint 投影 + fail-closed**（hint 非合法 outcome，须投影为"该 action 的最低档条目"，路由表无此 action 则**忽略**，不臆造 level 配对）与**路由表派生而非硬编码**（`routing_table` 支持按家庭定制注入，硬编码三档格会在定制表下失效）。新增不变式 **C8（决策输出耦合一致性）** + `test_decision_outcome_always_in_routing_table`（穷举）/ `test_hint_with_unknown_action_is_ignored`，同步 §2.1 / Slice C / 验收 6 / §3 点 4（C1–C8）。
  7. 顺带修掉 Related 头部 **ADR-0031 重复行**。
  本轮仍**未实现任何模型**，仅修契约表述与可测性。
- **2026-08-07（修订二，Proposed 阶段）**：吸收第二轮 Owner 评审——把定位从"让 Memory 决策"收敛为"建立可审计、可实验验证、可演进的 Memory-to-Decision 架构边界（**Future Decision Experiment Boundary**）"，区分架构问题（Memory 如何合法进入 Decision）与价值问题（Memory 是否值得进入 Decision）。(1) 新增 **§0.4 Decision Memory Validation History**：记录视觉 Memory Phase A 可行性验证（已完成但未完成长期收益验证）因多模态转向暂停；冻结未来验证唯一入口 = `DecisionInput`，杜绝各模块私接旁路。(2) **Slice C 重新定位**为 Memory → Decision Controlled Validation Gate：默认 Shadow Mode、不改线上 `WarningEvent`、做 `baseline`(perception-only) vs `candidate`(perception+memory) 双轨比较，验证证明统计收益 + Owner 放行后才进入 action lattice `max-only`；不再是"上线能力"。(3) **D5 补关键句**：Memory 进入 Decision 的必要前提是 Shadow Validation 证明统计收益，而非契约存在。(4) **§5.1 路线图修正**：ADR-0031（Decision Audit Trace）是 Slice C 验证门的硬性前置（无完整 trace 无法做可审计双轨比较），而非"Slice C 前置唯一条件"的误导表述；Related 头补 ADR-0031（规划）。(5) §1 点4 / Open Questions / 技术债同步"验证优先"措辞。注：前一轮评审吸收（C7 一级聚合 / C1 hint 仅排序 / action lattice max-only / §0.3 架构跃迁 / §5.1 初版）已含于 commit `4ae9035`。本 ADR 仍未实现任何模型。
- **2026-08-07（修订一，Proposed 阶段）**：吸收 Owner 评审反馈——(1) 新增 **C7 一级聚合约束（防 God Object）**：`DecisionInput` 禁止无约束横向堆字段，演进须走具名 Bundle；(2) **C1 强化**：`suggested_action_hint` 只能参与路由排序、不可直接赋值，新增契约测试 `test_reasoning_hint_is_never_authoritative`；(3) **D5「只升不降」形式化**为 action lattice（`MONITOR < NOTIFY_FAMILY < ESCALATE_COMMUNITY`）+ `max-only` 语义，新增 `test_decision_action_lattice_max_only`；(4) Open Questions 补 correlation lineage 标注（现在不加，归 ADR-0031）；(5) 新增 **§5.1 路线图**锚定后续 **ADR-0031 Decision Audit Trace Contract**（Slice C 前置可观测性）；(6) 补 §0.3 架构跃迁图。仍未实现任何模型。
