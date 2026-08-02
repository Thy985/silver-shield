# ADR-0025: Memory Consumer Architecture · 让记忆反哺理解

- **状态**：Accepted（2026-08-02，Owner 评审通过）
- **日期**：2026-08-02
- **范围**：v2 / 后 Memory Integration Closure 的 **Memory Consumer Layer** 设计；回答"记忆如何被消费以反哺理解"，定义介于 Memory 与 Reasoning 之间的消费层：四大组件（Retrieval / Aggregation / Context Builder / Reasoning Interface）的**严格单向管道与单一职责**、**三个数据契约**（ReasoningInput / ReasoningResult / DecisionRequest）、**硬边界**（不直接决策、不直接改 Risk Score），以及 **Consumer 执行模型（触发时机，§3.10）**。
- **决策者**：Owner
- **相关**：
  - ADR-0024（Memory 架构，定义"存储过去"）
  - ADR-0021 §7.1（实时风险流，Memory 的来源）
  - ADR-0023（身份连续性，主键）
  - ADR-0010（DecisionPolicy 唯一决策中心）
  - ADR-0022（Evidence Chain，Trust 来源）
  - ADR-0024 §3.8（Memory Consistency Policy，冲突解决边界）

> **文档职责边界**：本 ADR 只回答 **"为什么需要 Memory Consumer、它由哪些组件构成、组件职责与契约边界是什么、它如何与 Reasoning/Decision 解耦"**——即**消费层架构与契约边界**。"改哪个文件、检索算法、聚合存储格式、ReasoningInput 字段名"等**实现细节**归工程落地方案（本 ADR 不写）。冲突的具体解决策略归未来的 Memory Consistency Policy ADR。

---

## 1. 背景（Context）

### 1.1 关键节点：生命周期第一次闭合

Memory Integration Closure（PR#91 / #93 / #94 / #95 / #96，均已合 `main`）完成后，银龄盾第一次具备完整生命周期：

```
感知(Perceive) → 理解(Understand) → 行动(Act) → 记忆(Memory) → 回溯解释(Explain)
```

- 之前阶段（ADR-0021 实时风险 + ADR-0024 Memory + Closure）主要在建设：**系统有没有能力感知和记忆**。
- Memory（ADR-0024）回答了"**存储过去**"：三类记忆模型 + Memory Policy + Episode Builder + 外部闭环。
- 但"记忆"目前是**只写不读**的——它进入 Memory、能被审计，却**没有反哺"理解"**。

### 1.2 核心问题：如何利用过去

下一阶段要解决的不是"存得下"，而是"**记忆如何反过来增强理解**"。具体缺口：

| 缺口 | 现状 | 需要 |
| --- | --- | --- |
| 历史相关性 | 当前事件孤立判定（ADR-0010 只看当前帧） | 当前事件能找到"历史上相似 / 同一访客"的事件 |
| 长期模式 | 只有单条 EpisodicRecord，无聚合视图 | 多次访问 → 访客画像 + 风险模式 |
| 上下文供给 | Reasoning 层（未来 Agent / 规则 v2）无结构化历史输入 | 给上层一份"当前事件 + 历史上下文 + 证据 + 既往动作"的包 |
| 接口边界 | Memory 与 Reasoning 之间无契约层 | 明确"消费层不是决策层"的硬边界 |

### 1.3 为什么不能让 Reasoning 直接读 Memory

- 直接读会绕过 Episode Builder 抽象（ADR-0024 §3.2.1），让 Reasoning 耦合 Memory Object schema，未来 Memory 演进会污染 Reasoning。
- 直接读容易滑向"Memory 直接改 Risk Score"（违反 ADR-0010 + ADR-0024 §3.9），形成第二决策中心与循环论证。
- 需要一个**消费者**把 Memory 翻译成 Reasoning 能安全消费的 **Context**，并守住边界。

---

## 2. 目标与非目标

### 2.1 目标

- 定义 **Memory Consumer Layer**：Memory 与 Reasoning 之间的消费层
- 定义四大组件职责与契约：**Retrieval / Aggregation / Context Builder / Reasoning Interface**
- 明确**硬边界**：Consumer 不决策、不直接改 Risk Score；流为 Consumer → Reasoning → Decision
- 让 Reasoning（未来 Agent / 规则 v2）有结构化、可审计、可回溯的历史输入
- 与 ADR-0024 的 Memory Object / Trust Layer / Invariants 对齐，不重新发明

### 2.2 非目标（本 ADR 不做）

- ❌ 不实现 Reasoning 算法（规则 v2 / LLM v2 / Agent 本身）——本 ADR 只定义接口与边界
- ❌ 不重新实现 Memory 写入（Memory Policy / Episode Builder 已定义，Consumer 只读）
- ❌ 不直接改 Risk Score / 产 WarningEvent（违反 ADR-0010）
- ❌ 不解决 Memory 冲突（覆盖 / 衰减 / 版本化）——归未来 Memory Consistency Policy ADR
- ❌ 不定义 Semantic Aggregator 的写路径（Stage G/H，ADR-0024 已推迟；Consumer 只在读侧组合）
- ❌ 不引入 LLM（v2 才做，归 Reasoning 层）

---

## 3. 决策（Decision）

### 3.1 总体架构：Memory Consumer Layer 位于 Memory 与 Reasoning 之间

```
                    ┌──────────────────────────────────────────┐
                    │            Memory (ADR-0024)              │
                    │  Short-term / Episodic / Semantic         │
                    │  MemoryQuery.compose_context (Slice C)    │
                    └───────────────────┬──────────────────────┘
                                        │ 只读查询
                                        ▼
                    ┌──────────────────────────────────────────┐
                    │        Memory Consumer Layer             │
                    │  编排：MemoryConsumer（薄 conductor）       │
                    │  1. Retrieval  (只召回：找相关历史)        │
                    │  2. Aggregation (只计算：形成长期模式)     │
                    │  3. Context Builder (只组装：上下文)      │
                    │  4. Reasoning Interface (交付 ReasoningInput)│
                    │  —— 三阶段严格单向，互不调用（§3.1.1） ——  │
                    └───────────────────┬──────────────────────┘
                                        │ ReasoningInput（纯上下文，无 score）
                                        ▼
                    ┌──────────────────────────────────────────┐
                    │            Reasoning (理解)               │
                    │  规则 v2 / LLM v2 / Agent（未来组件）     │
                    │  消费 ReasoningInput → 推理产物         │
                    └───────────────────┬──────────────────────┘
                                        │ 推理产物（解释 / 增广特征）
                                        ▼
                    ┌──────────────────────────────────────────┐
                    │            Decision (决策)               │
                    │  ADR-0010 DecisionPolicy（唯一决策中心）  │
                    └──────────────────────────────────────────┘
```

**硬边界（本 ADR 核心）**：

```
Memory Consumer  ──▶  Reasoning  ──▶  Decision
      │
      └──✗ 禁止：Memory Consumer 直接喂 Risk Score 给 Decision
```

> **Memory Consumer is a context provider, not a decision contributor.**
> Consumer 产出的是 `ReasoningInput`（当前事件 + 历史上下文 + 证据 + 既往动作），**不含任何风险分数或决策**。分数与决策只能由 Reasoning → Decision 链路产生。直接让 Memory 改 Risk Score = 第二决策中心 + 循环论证（ADR-0010 / ADR-0024 §3.9 已否决）。

### 3.1.1 严格单向管道 + 单一职责（禁止互相调用）

四组件按固定顺序串联，数据流**严格单向**，组件之间**不互相调用**：

```
Memory
  │  只读查询
  ▼
Retrieval        # 只召回（recall）：返回原始 EpisodicRecord 列表
  │
  ▼
Aggregation      # 只计算（compute）：把召回记录聚成 Visitor Profile / Risk Pattern
  │
  ▼
Context Builder  # 只组装（assemble）：拼出 ReasoningInput
  │
  ▼
Reasoning        # 外部：消费 ReasoningInput → ReasoningResult
```

**单一职责铁律**：

- **Retrieval 只召回，不计算**：返回的是"相关历史原始记录"（如 100 条 `EpisodicRecord`），**绝不**返回已聚合的画像 / 模式 / 任何形式的预计算结论。是否"异常"不是 Retrieval 回答的——它只负责"把相关的历史找出来"。
- **Aggregation 只计算，不复述召回**：输入来自 Retrieval 的产物，产出 Visitor Profile / Risk Pattern；**绝不**内部再调 Retrieval（召回是上游给的，不是自己查的）。
- **Context Builder 只组装**：把 Aggregation 产物 + 当前事件 + 证据 + 既往动作拼成 `ReasoningInput`；**绝不**做召回或聚合。
- **三者互不可见**：Retrieval / Aggregation / Context Builder 之间**没有方法调用关系**，只通过数据传递串联。编排由 `MemoryConsumer`（薄 conductor）按序驱动。

**"最近一个月这个访客有没有异常？"归谁？**

——这条查询本身是**业务问题**，由 `MemoryConsumer` 编排入口承接：它向 Retrieval 要"该访客近一个月记录"，Retrieval 召回原始记录（不判异常），Aggregation 计算画像 / 模式（判"是否偏离常态"），Context Builder 组装成 `ReasoningInput`。**没有任何单一组件独自回答该问题，也没有组件跨层调用**。

### 3.2 组件 1：Retrieval（找到相关历史）

**职责**：给定当前事件，从 Memory 中检索排序后的相关历史。

```
当前事件 (Current Event)
   │
   │  Retrieval（组件；默认实现 RuleBasedRetrieval）
   ▼
相关历史事件 (Ranked Relevant History)
```

**检索维度**（实现由工程方案定，本 ADR 只定契约）：

- 身份键：`person_identity_id`（Phase 4 后）或 `visitor_instance_id`（v1 临时画像，标注"未确认身份"，遵循 ADR-0024 §3.3）
- 时间窗：近期 N 天 / 同时间段（如"同属晚 18–22 点"）
- 事件类型相似度：同类 `VisitorEvent` 类型 / 同类 `RiskSignal` 跃迁
- 时空特征：同设备 / 同门口区域 / 相似停留模式

  > ⚠ **隐私边界**：`device_id` 仅参与召回**排序键**（同设备优先），**绝不**进入 `ReasoningInput` 任何字段；若需向 Reasoning 传递空间信息，须脱敏为 `area_code`（如 `door` / `living_room`），不得携带可定位住户布局的原始 `device_id`（见 DESIGN-memory-consumer.md §3.1）。

**契约**：

```
Retrieval.retrieve(current_event: CurrentEvent) -> list[EpisodicRecord]
# 返回按相关性排序的 EpisodicRecord（带 source_event_ids / evidence_refs，
# 继承 ADR-0024 I4 + Trust Layer）
```

**复用**：直接建立在 ADR-0024 Slice C 的 `MemoryQuery.compose_context` 之上（Product Closure 已合），不另起炉灶。

**V0 冻结边界（ADR-0024 §10.1）**：Consumer **只调用** `compose_context`，**不修改**其签名或返回值，确保 V0 边界不被无意打破（5.4）。

**单一职责（呼应 §3.1.1）**：Retrieval **只召回**——返回按相关性排序的原始 `EpisodicRecord` 列表，**绝不**返回聚合画像 / 风险模式 / 任何形式的"是否异常"结论。判定"异常与否"是上游 Aggregation 的职责。

### 3.3 组件 2：Aggregation（形成长期模式）

**职责**：把检索到的 Episode 聚合成长期模式视图。

```
100 Episodes (检索到的 EpisodicRecord，数量示例)
   │
   │  Aggregation（组件；默认实现 RuleBasedAggregation）
   ▼
Visitor Profile（访客画像）
   │
   ▼
Risk Pattern（风险模式）
```

**两层产物**：

- **Visitor Profile**：该访客的长期画像——来访频次、停留模式、异常时段占比、repeat 模式、既往被派遣的动作类型。
- **Risk Pattern**：从历史中提取的**风险模式**（哪类事件反复出现、时段风险、升级历史），**不是风险分数**。

**关键设计——读侧组合，不重复写路径**：

- Aggregation **优先 join 已存在的 SemanticAggregate**（ADR-0024 Stage G/H，若已物化）；
- SemanticAggregate 未物化时，Aggregation 在**请求时**从检索到的 Episodes 做**有界窗口**的轻量聚合（如近 100 条 / 近 30 天）；
- 这不替代 ADR-0024 推迟的"写侧 Semantic Aggregator"，只是 Consumer 的**查询期视图组合**。

**阈值与防误判**（继承 ADR-0024 §3.1.3）：

- "100 Episodes"是示例窗口，具体数量 / 时间窗由工程方案定；
- 必须服从 ADR-0024 最低观测阈值（≥30 episodes、≥7 天）才产出高置信模式，否则标注 `confidence=low` 且不供 Reasoning 消费；
- v1 `person_identity_id=None` 时，Profile 按 `visitor_instance_id` 建临时画像并标注"未确认身份"（`VisitorProfile.identity_confirmed = False`，对齐 ADR-0023）；Reasoning 不得将临时画像当作真实身份画像使用。

**单一职责（呼应 §3.1.1）**：Aggregation **只计算**——输入是 Retrieval 交付的原始记录，产出 Visitor Profile / Risk Pattern；**绝不**内部再调 Retrieval 做召回（召回是上游给的）。它也**不回答**"该访客是否异常"的最终结论，只产出可观测的模式视图（结论归 Reasoning）。

### 3.4 组件 3：Context Builder（提供给上层）

**职责**：把前三步结果组装成一份结构化、可供 Reasoning 消费的上层上下文包。

```
Current Event
   + Historical Context (Retrieval 结果)
   + Evidence (ADR-0022 EvidenceItem 引用)
   + Previous Actions (该访客/模式既往被派遣的动作)
        │
        │  ContextBuilder
        ▼
   ReasoningInput（单一结构化对象）
```

**ReasoningInput 契约（抽象，字段名由工程方案定）**：

```
ReasoningInput {
    current_event: CurrentEvent                # 当前事件（ADR-0021 对象）
    historical_context: list[EpisodicRecord]   # 检索到的相关历史
    visitor_profile: VisitorProfile            # 聚合出的访客画像（可为空/低置信）；必带 identity_confirmed: bool（v1 常 False，对齐 ADR-0023）
    risk_pattern: RiskPattern                  # 聚合出的风险模式（非分数）
    evidence_refs: list[EvidenceRef]           # 证据引用（ADR-0022）
    previous_actions: list[ActionRecord]       # 既往动作（供 Reasoning 参考"上次怎么处理"）
    conflicts: list[ConflictFlag]              # 历史与当前的冲突标记（见 §3.6）
}
```

**关键约束**：

- `ReasoningInput` **不含 risk_score / decision / warning**——它是纯上下文。
- 所有历史项必须携带 `source_event_ids` / `evidence_refs`（继承 ADR-0024 I4 + Trust Layer），保证 Reasoning 可回溯。

### 3.5 组件 4：Reasoning Interface 与数据契约（注意：不是决策）

`Reasoning Interface` 只是**交付边界**——它不承载推理逻辑，也不该承载太多。本 ADR 把契约落为三个**显式数据类型**，形成 `Context → Inference → Decision` 的清晰链路：

```
MemoryConsumer
   │ provide(ReasoningInput)              # 交付上下文（纯上下文，无 score）
   ▼
ReasoningEngine  (未来独立组件，归其 ADR，Phase 5)
   │ infer(ReasoningInput) -> ReasoningResult
   ▼
DecisionPolicy    (ADR-0010 唯一决策中心)
   │ consume(ReasoningResult) -> DecisionRequest -> 决策
```

**三个数据契约**：

- **`ReasoningInput`**（Consumer 输出，即 Context Builder 产物）：字段定义以 **§3.4** 为单一事实来源，此处不重复罗列。要点回顾：纯上下文、不含 `risk_score` / `decision` / `warning`（C1），每个历史项携带 `source_event_ids` / `evidence_refs`（C5）。

- **`ReasoningResult`**（Reasoning Engine 输出）：

  ```
  ReasoningResult {
      findings: list[Finding]                    # 推理发现（如"该访客近 30 天夜间到访占比异常升高"）
      explanation: str                            # 可解释说明（继承 ADR-0024 Trust Layer）
      suggested_action_hint: ActionType | None   # 非绑定建议，仅供 Decision 参考，非决策
      source_refs: list[SourceRef]               # 回溯到 ReasoningInput 的字段
  }
  ```

  注意：`ReasoningResult` **不是分数、不是决策**；`suggested_action_hint` 只是提示，最终是否采用由 ADR-0010 DecisionPolicy 决定。

- **`DecisionRequest`**（Decision 层输入）：由 DecisionPolicy 从 `ReasoningResult` + 当前 RiskSignal 派生，是 ADR-0010 决策中心的正式入参。Consumer / Reasoning **不构造** `DecisionRequest`。

**边界澄清**：

- `ReasoningInterface` 仅指 `MemoryConsumer.provide(ReasoningInput)` 这一交付契约；推理引擎与决策中心是**未来独立组件**，归各自 ADR（Phase 5 / ADR-0010）。
- Consumer 不知道 Reasoning 内部怎么用 `ReasoningInput`；它只保证交付的上下文是结构化的、可审计的、不含分数。

### 3.6 冲突透明（接力 ADR-0024 §3.8）

ADR-0024 §3.8 把冲突解决策略（覆盖 / 衰减 / 版本化）推迟到未来 Memory Consistency Policy ADR。Consumer 在此只做**冲突透明**：

- 当检索 / 聚合发现历史与当前事件矛盾（如"历史低风险" vs "本次高风险"、"通常白天" vs "本次夜间"），Consumer **不解决、不覆盖**；
- 而是把冲突作为 `ConflictFlag` 放进 `ReasoningInput.conflicts`，由 Reasoning 同时看到新旧并自行推理（呼应 ADR-0024 §3.8 C3）；
- 冲突解决策略（如何衰减旧模式、是否版本化）归未来 ADR，本 ADR 不决策。

**`ConflictFlag` 数据结构**（工程方案落地字段，示例见 `DESIGN-memory-replay-dataset.md` Case 3）：

- `type: str` —— 冲突类别（如 `behavior_shift` / `time_shift` / `identity_shift`）
- `historical: str` —— 历史侧状态描述（如 `normal` / `daytime`）
- `current: str` —— 当前侧状态描述（如 `abnormal` / `night`）
- `detail: str` —— 新旧并存的补充说明（供 Reasoning 推理，Consumer 不解决）

> 实现时 `ConflictFlag` 须为结构化的具名字段（**不可退化为单一 `type: str` 字符串**），否则 C4 验证拿不到"新旧并存"细节。

### 3.7 Consumer Invariants（不变量）

这些不变量是**架构级约束**，工程方案必须保证，测试必须覆盖：

> **C1. No Score Mutation（无分数变异）**
> `ReasoningInput` 及其任何子对象**不得包含** risk_score / decision / warning / 任何可被直接喂给 Decision 的判定。Consumer 产出纯上下文。

> **C2. Read-Only（只读消费）**
> Consumer **绝不写** Memory。Memory 只由 Memory Policy / Episode Builder 写入（ADR-0024 §3.2.2 / §3.2.1）。Consumer 只读查询。

> **C3. Determinism（确定性）**
> 给定相同的 Memory 状态 + 相同的当前事件，Retrieval 返回的相关历史排序**稳定可复现**（保证回放 / 审计一致）。

> **C4. Conflict Transparency（冲突透明）**
> 历史与当前冲突时，必须同时保留新旧并标记 `ConflictFlag`，不得静默覆盖（接力 ADR-0024 §3.8）。

> **C5. Explainability（可解释性）**
> `ReasoningInput` 中每个历史项必须携带 `source_event_ids` / `evidence_refs`（继承 ADR-0024 I4 + §3.9 Trust Layer），Reasoning 可沿其回溯到源事件与证据。

**不变量验收标准**（工程方案落地时）：

- [ ] C1 测试：断言 `ReasoningInput` 不含 score / decision 字段
- [ ] C2 测试：断言 Consumer 调用后 Memory store 写入计数为 0
- [ ] C3 测试：同输入两次 retrieve，返回顺序一致
- [ ] C4 测试：构造历史与当前冲突，断言 `conflicts` 非空且新旧并存
- [ ] C5 测试：构造无 source 引用的历史项，断言被拒绝或标记

### 3.8 模块落点（建议）

```
src/home_perception/memory/
   records.py            # (已有) Memory Object
   policy.py             # (已有) MemoryPolicy
   episode_builder.py    # (已有)
   store.py              # (已有)
   query.py              # (已有) MemoryQuery.compose_context
   consumer/             # ← 本 ADR 新增
      __init__.py
      interfaces.py      # MemoryConsumer / Retrieval / Aggregation / ContextBuilder ABC
      contracts.py       # ReasoningInput / ReasoningResult / DecisionRequest dataclasses
      orchestrator.py    # MemoryConsumer.consume()：按序驱动 Retrieval→Aggregation→ContextBuilder
      retrieval.py       # RuleBasedRetrieval 默认实现（只召回）
      aggregation.py     # RuleBasedAggregation 默认实现（只计算）
      context.py         # ReasoningInput 组装（只组装）
```

> 把 Consumer 放在 `memory/` 包内，使"记忆消费"与"记忆存储"同域但分层：存储侧（Policy / EpisodeBuilder / Store）只写，消费侧（consumer/）只读。Reasoning 引擎（未来）在 `memory/consumer` 之外消费 `ReasoningInput`，不反向依赖存储细节。具体落点工程方案可微调，但**只读边界与接口契约不变**。

### 3.9 与完整生命周期的对应

```
感知 → 理解 → 行动 → 记忆 → 回溯解释
        ▲                    │
        │   Memory Consumer   │
        └──── 反哺理解 ────────┘
```

Consumer 增强的是"**理解**"与"**回溯解释**"两步：把历史注入当前理解、让解释可引用过去。它不增强也不改变"行动 / 决策"。

---

### 3.10 Consumer 执行模型（触发时机与生命周期）

**触发时机是架构决策，不是开放问题。** Consumer 不在每个事件上实时运行；何时运行由本 ADR 钉死，以保证边缘 CPU 预算（AGENTS.md §4.1）与成本可控。

三种模式：

- **模式 A · 实时逐事件消费**：每个 RiskSignal → Consumer → Reasoning。实时性最好，但每次风险事件都要跑召回 + 聚合，在边缘 CPU 上违反帧预算；**Phase 1 不采用为默认**。
- **模式 B · 风险触发（Phase 1 选定，修订）**：触发条件 = `RiskSignal.level in {MEDIUM, HIGH}`（含 ADR-0010 决策落入 ESCALATE_COMMUNITY 等高优先动作）**或** 当前 `VisitorEvent` 命中已有历史（`prior_episode_count > 0`，即"已知 / 重复访客再现"）。这样 Consumer 在"中风险"或"熟悉面孔再次出现"时即介入，实现"提前理解"而非仅"事后解释"；仍把消费成本绑定到少量事件，边缘可控。
- **模式 C · 后台周期批处理**：如每日凌晨对全量访客跑 Aggregation 生成 / 刷新 Visitor Profile（Semantic 画像）。适合"长期模式"的物化，不要求实时；与 ADR-0024 推迟的写侧 SemanticAggregator（Stage G/H）接壤——模式 C 预计算并物化的 Profile，Retrieval 可直接 join，无需每次请求期聚合。

**Phase 1 决策（触发条件修订）**：

- 触发 = **模式 B（修订：MEDIUM+ 或存在历史时触发）**；仅 HIGH 会沦为"事后解释系统"，故放宽到 MEDIUM 与"已知访客再现"（详见 DESIGN-memory-consumer.md §4.1 / §0.5）；
- 模式 C 用于 Semantic Profile 的离线预计算（未来，接 ADR-0024 Stage G/H）；
- 模式 A 明确排除为默认（成本 + 边缘约束）。

Phase 1 数据流（修订触发条件）：

```
RiskSignal ≥ MEDIUM  │  已知访客再现(prior_episode_count>0)
   │                │
   └──── 任一成立 ───┘
   ▼
MemoryConsumer.consume(event)
   → Retrieval（召回）→ Aggregation（计算）→ Context Builder（组装）
   → ReasoningInput
   │
   ▼
ReasoningEngine.infer(ReasoningInput) -> ReasoningResult
   │
   ▼
DecisionPolicy (ADR-0010) 将 ReasoningResult 作为增广上下文并入决策
```

**生命周期不变量**：Consumer 仅在触发时物化 `ReasoningInput`；非触发期**不保留任何跨请求状态**（无会话、无"当前画像"缓存），保证 C2（只读）+ 无副作用 + 可复现。

**异常路径（守 C2 只读）**：触发期内若某组件抛异常（如 Aggregation 计算失败 / 数据损坏 / OOM），Consumer **不得写入 Memory、不得持有跨请求状态**；向上层返回 `None` 或显式 `ConsumerError`（见 DESIGN §5 C-0 `exceptions.py`），并遵循 ADR-0024 C2 只读——异常绝不沉淀临时画像到 Memory 缓存，主链路不受影响（非阻塞，见 DESIGN §4.1）。

---

## 4. 动机（Rationale）

### 4.1 为什么在 Memory 与 Reasoning 之间必须有一层 Consumer

直接让 Reasoning 读 Memory 会耦合 Memory Object schema、滑向"Memory 改分数"。Consumer 作为翻译层，把 Memory 变成安全的 `ReasoningInput`，守住 ADR-0010 / ADR-0024 §3.9。

### 4.2 为什么 Aggregation 是读侧组合而非新写路径

ADR-0024 已把写侧 Semantic Aggregator（Stage G/H）推迟。Consumer 的 Aggregation 只在查询期组合视图（join 已物化的 SemanticAggregate 或做有界轻量聚合），不重复写路径、不与 ADR-0024 冲突。

### 4.3 为什么 Context Builder 产出"包"而不是"分数"

Reasoning 需要的是"上下文"（当前事件 + 历史 + 证据 + 既往动作），不是"结论"。把结论（分数）塞进 Context 会越过 Reasoning 直接喂 Decision，破坏唯一决策中心。

### 4.4 为什么现在做（节点意义）

Memory Integration Closure 完成前做 Consumer 没有可消费的 Memory；完成后 Memory 已闭环可查，正是定义消费契约的最佳时点——在 Agent / 规则 v2 落地前先把边界钉死，避免 Reasoning 直接啃 raw Memory。

---

## 5. 后果（Consequences）

### 5.1 正面

- 守住决策边界：Memory 永不改 Risk Score，唯一决策中心不变
- 让 Reasoning 有结构化、可审计、可回溯的历史输入
- 隔离 Memory 演进：Reasoning 只依赖 `ReasoningInput`，不依赖 Memory Object 内部
- 为 Agent（Phase 5）铺路：Agent 直接消费 `ReasoningInterface`，无需回头补契约
- 复用 ADR-0024 Slice C `MemoryQuery.compose_context`，不另起炉灶

### 5.2 负面

- 新增抽象层（Consumer 4 组件 + ReasoningInput），测试 / 维护成本
- v1 价值有限：`person_identity_id=None`，Visitor Profile 是临时画像
- Aggregation 读侧聚合有查询开销（有界窗口 + 最低观测阈值缓解）

### 5.3 技术债

- 与 ADR-0024 关联：写侧 Semantic Aggregator（Stage G/H）仍待办；Consumer 读侧组合是过渡
- 冲突解决策略（ADR-0024 §3.8 C1–C4）归未来 Memory Consistency Policy ADR

---

## 6. 替代方案（Alternatives）

### 6.1 Reasoning 直接读 Memory（被否决）

绕过 Consumer → 耦合 Memory Object schema + 易滑向"Memory 改分数"（违反 ADR-0010 / ADR-0024 §3.9）。

### 6.2 Memory Consumer 直接产 Risk Score（被否决）

违反本 ADR C1 + ADR-0010 唯一决策中心 + ADR-0024 §3.9（循环论证、双决策中心）。

### 6.3 Consumer 内实现写侧 Semantic Aggregator（被否决）

与 ADR-0024 推迟的 Stage G/H 重叠，职责越界；Consumer 应为只读消费层。

### 6.4 Aggregation 不做最低观测阈值（被否决）

小样本误判（ADR-0024 §3.1.3 false pattern）；必须继承最低观测阈值。

---

## 7. 范围边界（明确不做什么）

| 项 | 是否本 ADR | 归属 |
| --- | --- | --- |
| Memory Consumer Layer 四组件职责与契约 | ✅ 是 | 本 ADR §3 |
| ReasoningInput 抽象契约（不含分数） | ✅ 是 | 本 ADR §3.4 |
| Reasoning Interface（交付接口，非引擎） | ✅ 是 | 本 ADR §3.5 |
| 硬边界 Consumer → Reasoning → Decision，禁 Memory 直改 Score | ✅ 是 | 本 ADR §3.1 |
| Consumer Invariants C1–C5 | ✅ 是 | 本 ADR §3.7 |
| 冲突透明（标记不解决） | ✅ 是 | 本 ADR §3.6 |
| 模块落点建议 | ✅ 是 | 本 ADR §3.8 |
| Reasoning 算法（规则 v2 / LLM v2 / Agent） | ❌ 否 | 未来 Reasoning ADR（Phase 5） |
| 写侧 Semantic Aggregator（Stage G/H） | ❌ 否 | ADR-0024（已推迟） |
| 冲突解决策略（覆盖 / 衰减 / 版本化 / confidence 计算） | ❌ 否 | 未来 Memory Consistency Policy ADR |
| 检索算法 / 聚合窗口具体值 / 字段名 | ❌ 否 | 工程方案 |
| LLM 解释 | ❌ 否 | v2 Reasoning 层 |
| 身份识别（ReID） | ❌ 否 | ADR-0023（Phase 4） |

---

## 8. 开放问题（Open Questions）

> 注：原 O5（Consumer 触发时机）已升级为架构决策，见 **§3.10**——Phase 1 采用**模式 B（修订：MEDIUM+ 或已知访客再现时触发）**，模式 C 用于离线 Semantic Profile 预计算，模式 A 排除为默认。

| # | 问题 | 留给 |
| --- | --- | --- |
| O1 | Retrieval 的相关性排序算法（向量 / 规则 / 混合） | 工程方案 |
| O2 | Aggregation 窗口（100? 200?）+ 时间窗（30d? 90d?） | 工程方案 + 最低观测阈值 |
| O3 | ReasoningInput 具体字段名 / 序列化格式 | 工程方案 |
| O4 | Visitor Profile 在 `person_identity_id=None` 时的生命周期 | 工程方案 + ADR-0023 |
| O5 | Reasoning 引擎本身（规则 v2 / LLM v2 / Agent）的 ADR 编号 | Phase 5 |
| O6 | 冲突解决策略（覆盖 / 衰减 / 版本化） | 未来 Memory Consistency Policy ADR |

---

## 9. 与现有 ADR 的关系

| ADR | 关系 |
| --- | --- |
| **ADR-0024**（Memory 架构） | 本 ADR 的**前提与输入**；消费其 Memory Object / `MemoryQuery.compose_context`；继承 I4 / Trust Layer / Invariants；读侧 Aggregation 不重复其推迟的写侧 Semantic Aggregator |
| **ADR-0021**（实时风险流） | 当前事件（BehaviorState / VisitorEvent / RiskSignal）是 Retrieval 的输入；Consumer 不改其状态机 |
| **ADR-0023**（身份连续性） | `person_identity_id` 是 Retrieval / Aggregation 主键；v1=None 时用 `visitor_instance_id` 临时画像 |
| **ADR-0010**（DecisionPolicy） | Consumer 不产决策、不喂分数，守住其"唯一决策中心" |
| **ADR-0022**（Evidence Chain） | Context Builder 的 `evidence_refs` 引用 EvidenceItem；Trust 可审计 |
| **ADR-0024 §3.8**（Consistency） | 冲突透明（标记不解决）接力其边界；解决策略归未来 ADR |

---

## 10. 状态

本 ADR 为 **Accepted**（2026-08-02，Owner 评审通过）。已在 `docs/ADR/README.md` 清单登记（编号 0025），并于 ADR-0024 §7 / §10.1 标注 Context Builder 已由本 ADR 承接。

工程落地方案：`docs/DESIGN-memory-consumer.md`（按 Retrieval → Aggregation → Context Builder → Reasoning Interface 拆分 Slices，已随本 ADR 一同落库）。
