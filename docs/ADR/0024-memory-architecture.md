# ADR-0024: Memory 架构 · 三类记忆模型与 Memory Policy

- **状态**：Accepted
- **日期**：2026-07-27（Proposed）→ 2026-07-28（Accepted）
- **范围**：v2 / 后 MVP 的 **Memory 架构设计**；回答"什么信息值得跨生命周期保留"以及"如何从 Raw State 提炼 Memory"。本 ADR 是 ADR-0021 §7.1 预留的 Memory ADR，是 Roadmap Phase 4-5（身份系统化 / Agent）的基础设施。当前 MVP 不实现。
- **决策者**：Owner
- **相关**：
  - ADR-0021 §7.1（预留 Memory ADR，定义状态连续性来源）
  - ADR-0023（身份连续性，`person_identity_id` 是 Memory 主键）
  - ADR-0010（WarningEvent 决策架构，Episodic Memory 来源）
  - ADR-0014（三级冻结治理，Memory 不入 L1/L2/L3 冻结）
  - `docs/TECH-DEBT.md` TD-0024（RecentBehaviorStore eviction）/ TD-0027（状态恢复）

> **文档职责边界**：本 ADR 只回答 **"为什么需要 Memory、存什么、不存什么、三类记忆的边界、Memory Policy 的职责"**——即**决策与契约边界**。"改哪个文件、存储格式、查询接口、落盘时机"等**实现细节**归未来的工程落地方案（`docs/DESIGN-memory-pipeline.md`，本 ADR 不写）。冲突时按此分工归位。

---

## 1. 背景（Context）

### 1.1 ADR-0021 留的缺口

ADR-0021 §7.1 明确：

> 本 ADR **仅定义状态连续性的「来源」**——即产出哪些对象、各自的时间语义。它**不定义 Memory 如何存储**：`BehaviorState` / `VisitorEvent` / `RiskSignal` 各自的**持久化粒度、保留时长、淘汰与摘要策略，均由独立的 Memory ADR 决策**。

ADR-0021 留了一张方向表：

| 对象 | 时间语义 | 候选归宿（方向） |
| --- | --- | --- |
| `BehaviorState` | 当前正在发生（volatile） | 倾向 Short-term / Working Memory |
| `VisitorEvent` | 过去已完成 | 倾向 Long-term Episodic Memory |
| `RiskFeature` | 过去的统计 | 倾向 Long-term Memory 的模式/频率特征 |
| `RiskSignal` | 此刻的跃迁 | 倾向 Agent Observation Input |

本 ADR 把"方向"落为**决策**。

### 1.2 核心问题：不是"要不要存"，而是"什么值得跨生命周期保留"

Memory 不是简单"存历史"。如果把所有对象无差别落盘，会产生：

- **易变态爆炸**：`BehaviorState.dwell_seconds` 每帧变化（10:00=100 → 10:01=160 → 10:05=350），逐帧落盘 = 海量冗余
- **状态与记忆混淆**：`BehaviorState` 是"现在正在发生什么"，不是"过去发生了什么"；两者时间语义不同
- **查询无效**：Agent 问"为什么今天风险高"，不需要"18:30:01 dwell=1, 18:30:02 dwell=2..."，需要"18:30-18:45 异常停留 15 分钟"

**核心判断**：

> **状态 ≠ 记忆。**
>
> - 状态回答"此刻是什么"（`dwell_seconds=350`）
> - 记忆回答"过去发生了什么值得记住"（"18:30-18:45 异常停留 15 分钟"）
>
> Memory 的职责是从状态流中**提炼**可跨生命周期使用的记忆，不是无差别落盘。

### 1.3 触发本 ADR 的具体问题

| 问题 | 来源 | 当前处理 |
| --- | --- | --- |
| 进程重启后 `ACTIVE_RISK` 状态丢失 | TD-0027 | Deferred（本 ADR） |
| `RecentBehaviorStore._entries` 旧条目累积 | TD-0024 | Open（eviction 优化） |
| Agent 回答"为什么今天风险高"需要什么输入 | Roadmap Phase 5 | 无（本 ADR 定义） |
| 跨天/跨会话访客画像需要什么数据 | Roadmap Phase 4 | 无（本 ADR 定义） |

---

## 2. 目标与非目标

### 2.1 目标

- 定义 **三类记忆模型**（Short-term / Episodic / Semantic），明确各自的来源、生命周期、用途
- 定义 **Memory Policy** 抽象：从 Raw State → Memory Summary 的提炼规则
- 明确 Memory 的**主键策略**（与 ADR-0023 `person_identity_id` 的关系）
- 明确 Memory 与现有对象的**边界**（不替换 `BehaviorState` / `RecentBehaviorStore`）
- 为 Agent（Phase 5）和身份系统（Phase 4）提供数据基础

### 2.2 非目标（本 ADR 不做）

- **不定义存储格式**（SQLite / Parquet / JSON / 时序库 —— 属工程方案）
- **不定义查询接口**（REST / GraphQL / 内存查询 —— 属工程方案）
- **不实现任何 Memory 模块**（本 ADR 是架构决策，实现归 Phase 4-5 工程方案）
- **不替换 `RecentBehaviorStore`**（它是 Working Memory 的滑窗统计，本 ADR 定义它如何在 Memory 体系中定位）
- **不定义 Agent 推理逻辑**（Agent 是 Memory 的消费者，本 ADR 只定义 Memory 产出什么）
- **不破坏 ADR-0014 三级冻结**（Memory 是新增旁路，不入 L1/L2/L3 冻结）

---

## 3. 决策（Decision）

### 3.1 三类记忆模型（核心）

Memory 分为三类，按**时间尺度**与**抽象层级**划分：

```
              时间尺度          抽象层级
              ──────           ──────
Short-term    分钟级            Raw State（低抽象）
Episodic      天/月级           Episode Summary（中抽象）
Semantic      月/年级           Pattern（高抽象）
```

> **对象层级澄清**：`BehaviorState` / `RealTimeRiskEvaluator` / `RiskSignal` 不是同级对象，而是：
> ```
> BehaviorState（当前状态快照）
>     │
>     ▼
> RealTimeRiskEvaluator（状态机评估，产出运行时态）
>     │
>     ▼
> RiskSignal（transition event，跃迁事件，不是状态）
> ```
> 因此 Short-term Memory 的输入必须**按类型区分语义**：StateSnapshot（状态快照）vs RiskStateMachineRuntimeState（状态机运行时态）vs TransitionEvent（跃迁事件），三者不能混为一谈。本 ADR 只定义输入类型，不依赖任何类的内部实现（见 §3.1.1 实现中立原则）。

#### 3.1.1 Short-term Memory（工作记忆）

| 字段 | 值 |
| --- | --- |
| **输入** | **StateSnapshot**（当前状态，由 `BehaviorState` 承载）+ **TransitionEvent**（状态变化，由 `RiskSignal` 承载） |
| **时间尺度** | 分钟级（当前访问生命周期 + 短期缓存） |
| **生命周期** | 访客离场后短期保留（如 5 分钟），过期清除 |
| **用途** | Agent "现在发生什么"的输入；进程重启后的状态恢复（TD-0027） |
| **存储** | 进程内（volatile）+ 可选短期持久化（用于重启恢复） |

**输入类型定义**（架构层抽象，不依赖具体实现）：

| 输入类型 | 语义 | 当前承载对象 |
| --- | --- | --- |
| StateSnapshot | 当前状态快照（低抽象） | `BehaviorState` |
| TransitionEvent | 状态跃迁事件（触发写入） | `RiskSignal` |
| RiskStateMachineRuntimeState | 风险状态机运行时态（如"哪个 track 当前处于 ACTIVE_RISK"） | 由 ADR-0021 `RealTimeRiskEvaluator` 产出，**本 ADR 不指定内部数据结构** |

> **实现中立原则**：本 ADR 不依赖任何类的私有字段（如 `_active` dict）。未来 `RealTimeRiskEvaluator` 重构为 `StateStore` / Actor Model / 其他形式时，只要仍产出上述三类输入，本 ADR 不失效。具体内部数据结构属工程实现，由工程方案决定。

**内容示例**：

```
当前门口:
  visitor_x
  持续停留: 350s
  风险: ACTIVE_RISK
  最近跃迁: dwell abnormal RAISED @ 18:35（transition event）
```

**关键约束**：Short-term Memory **不逐帧落盘**。`BehaviorState.dwell_seconds` 每帧变化，落盘 = 爆炸。Short-term Memory 只在以下时机写入：

- 状态转移（`NONE → ACTIVE_RISK` / `ACTIVE_RISK → NONE`，由 TransitionEvent 触发）
- 周期快照（如每 30 秒一次，用于重启恢复，见 §3.7 Snapshot 原则）
- 访客离场（生命周期结束，转为 Episodic Memory）

**TransitionEvent 的定位**：TransitionEvent 是**跃迁事件**，不是状态。它作为 Short-term Memory 的 **写入触发信号**，不作为"当前状态"存入。当前状态由 StateSnapshot 承载，TransitionEvent 只记录"何时发生了什么跃迁"。

#### 3.1.2 Episodic Memory（事件记忆）

| 字段 | 值 |
| --- | --- |
| **来源** | `VisitorEvent` / `WarningEvent` / `ActionCommand` → 经 **Episode Builder** 提炼 |
| **时间尺度** | 天/月级 |
| **生命周期** | 长期保留（如 90 天），过期归档或删除 |
| **用途** | 历史访问档案；Agent "过去发生过什么"的输入；Semantic Memory 的原料 |
| **存储** | 持久化（SQLite / 文件） |

**内容示例**：

```
2026-07-27
  访客: unknown（person_identity_id=None）
  时间: 18:32-18:44
  行为: 停留 12 分钟
  风险: high_risk_approach
  处理: 通知家属（已确认）
  证据: evidence_items[...]
```

**关键约束**：Episodic Memory 存的是**事件摘要**，不是原始帧/原始状态。一次访问 = 一条 Episodic 记录（不是 1000 条 `BehaviorState` 快照）。

**Episode Builder 的必要性**（详见 §3.2.1）：Memory 不应直接理解业务对象（`WarningEvent` / `ActionCommand`），否则未来业务对象字段变化会污染 Memory。必须经 Episode Builder 把原始事件转换为 Memory Object。

#### 3.1.3 Semantic Memory（模式记忆）

Semantic Memory 分为两类，**启用条件不同**：

##### 3.1.3.1 Environment Semantic Memory（环境模式记忆）

| 字段 | 值 |
| --- | --- |
| **来源** | Episodic Memory 聚合（按时间/地点/时段，不按身份） |
| **时间尺度** | 月/年级 |
| **生命周期** | 长期保留，周期性更新（如每日/每周聚合） |
| **用途** | 环境模式识别；Agent "这个家庭/这个时段的模式是什么"的输入 |
| **v1 是否启用** | **可由场景需求决定**（不依赖 `person_identity_id`），但**必须满足最低观测阈值**（见下） |
| **存储** | 持久化（键值/文档库） |

**内容示例**：

```
这个家庭过去 30 天:
  晚上 18-22 点陌生访客概率: 高
  周末异常访问: 比工作日 +40%
  平均每日访客数: 3.2
  风险趋势: 上升
```

**聚合维度**：时间 / 时段 / 地点（设备）—— **不按身份聚合**，因此 v1 即可启用。

**最低观测阈值约束**（防 false pattern）：

> **Environment Semantic Memory requires minimum observation threshold.**

环境模式在数据量不足时容易产生 false pattern（如"一天来了两个快递员 → 系统认为晚上陌生访问风险升高"）。必须满足最低阈值才产出聚合：

| 阈值类型 | 建议值（具体由工程方案定） | 作用 |
| --- | --- | --- |
| minimum_episodes | ≥ 30 条 Episodic 记录 | 防小样本误判 |
| minimum_time_window | ≥ 7 天观测 | 防短期偶发被当成模式 |
| minimum_confidence | 聚合置信度达标才输出 | 防低置信度模式污染 Agent |

**未达阈值时的行为**：不产出 SemanticAggregate，或产出但标注 `confidence=low` 且不供 Agent 消费。具体策略由工程方案决定。

##### 3.1.3.2 Identity Semantic Memory（身份模式记忆）

| 字段 | 值 |
| --- | --- |
| **来源** | Episodic Memory 按 `person_identity_id` 聚合 |
| **时间尺度** | 月/年级 |
| **生命周期** | 长期保留，周期性更新 |
| **用途** | 个人模式识别；Agent "这个人过去的模式是什么"的输入 |
| **v1 是否启用** | ❌ **不启用**（v1 `person_identity_id` 恒为 None，ADR-0023） |
| **存储** | 持久化（按 `person_identity_id` 索引） |

**内容示例**：

```
该访客（person_identity_id=xxx）过去 30 天:
  来访次数: 8
  异常时段占比: 80%
  风险等级分布: LOW 5 / MID 2 / HIGH 1
  最近一次访问: 2026-07-25 19:30
```

**v1 约束**（来自 ADR-0023）：`person_identity_id` 恒为 `None`，无法跨会话关联。强行在 v1 建 Identity Semantic Memory 会把 `visitor_instance_id` 当 `person_identity_id`，违反"不冒充身份"约束。

**Semantic Memory 总体约束**：Semantic Memory **不是原始事件的堆叠**，而是**聚合后的模式**。它由 Episodic Memory 经聚合产生，不直接由 Real-time Stream 写入。

### 3.2 Memory Policy（Raw State → Memory Summary）

**核心抽象**：Memory 不是直接存对象，而是经 **Memory Policy** 提炼后存摘要。

```
Raw State / Raw Event          Memory Policy              Memory Object（跨生命周期）
─────────────────         ──────────────              ────────────────
BehaviorState.dwell=350        │                          │
BehaviorState.dwell=351   ───▶ │ 提炼规则 ───▶             │ Short-term:
BehaviorState.dwell=352        │  - 状态转移才写入          │   "18:30-18:45
...                            │  - 周期快照               │    异常停留 15min"
RiskSignal(RAISED)             │  - 访客离场转 Episodic     │
RiskSignal(CLEARED)            │                          │ Episodic:
                               │                          │   "2026-07-27
VisitorEvent                   │                          │    高风险访问"
WarningEvent                   │
ActionCommand                  │                          │ Semantic:
                               │                          │   "过去 30 天
                               │                          │    8 次陌生访客"
```

#### 3.2.1 Episode Builder（Event Projection 层）

**核心判断**：Memory **不应直接理解业务对象**（`VisitorEvent` / `WarningEvent` / `ActionCommand`），否则：

- 业务对象字段变化会污染 Memory（向后兼容噩梦）
- Memory 变成业务对象的 dump 场所，失去抽象
- 不同业务对象的字段含义重叠/冲突

**Episode Builder 的职责**：把原始事件流（`VisitorEvent` + `WarningEvent` + `ActionCommand`）**投影**（project）为 Memory 自有的 **Memory Object**。

```
Raw Event Stream            Episode Builder           Episodic Memory
───────────────         ───────────────────         ────────────────
VisitorEvent              ┌─────────────────┐
WarningEvent      ─────▶  │ Episode Builder │  ────▶  MemoryObject{
ActionCommand             │  - 关联同一访问  │          visitor: ...,
                          │  - 提取关键信息  │          time_range: ...,
                          │  - 丢弃冗余字段  │          risk: ...,
                          │  - 生成摘要     │          actions: [...],
                          └─────────────────┘          evidence: [...],
                                                       summary: "..."
                                                     }
```

**Memory Object 的特征**：

- **Memory 自有 schema**：不依赖 `WarningEvent` / `ActionCommand` 的字段定义
- **human-interpretable summary**：Memory Object **必须包含人类可读的事件摘要**（如"陌生访客异常停留 12 分钟，已通知家属"），用于 Agent 解释
- **向后兼容**：业务对象字段变化时，只需调整 Episode Builder 的投影规则，Memory Object schema 不变

> **实现中立**：本 ADR 只约束"Memory Object 必须包含 human-interpretable summary"，**不固定字段名**。具体字段名（`narrative` / `summary` / `explanation` / `evidence_chain` 等）由工程方案决定，未来可演进。

#### 3.2.2 Memory Policy 输入输出契约

Memory Policy 是**转换边界**（transformation boundary），不是决策模块，不是 pipeline stage，也不是独立服务。

**契约定义**：

```
MemoryPolicy.transform(
    observation_stream: ObservationStream  # 输入：状态/事件流
) -> MemoryRecord                          # 输出：Memory Object
```

**输入**（ObservationStream）：

| 类型 | 语义 | 当前承载对象 |
| --- | --- | --- |
| StateSnapshot | 当前状态快照（低抽象） | `BehaviorState` |
| RiskStateMachineRuntimeState | 风险状态机运行时态 | 由 `RealTimeRiskEvaluator` 产出（本 ADR 不指定内部结构） |
| TransitionEvent | 跃迁事件（触发写入） | `RiskSignal` |
| RawEvent | 业务事件（待投影） | `VisitorEvent` / `WarningEvent` / `ActionCommand` |

**输出**（MemoryRecord）：

| 类型 | 写入目标 | 触发时机 |
| --- | --- | --- |
| ShortTermRecord | Short-term Memory | 状态转移 / 周期快照 |
| EpisodicRecord | Episodic Memory | 访客离场（Episode Builder 投影） |
| SemanticAggregate | Semantic Memory | 周期性聚合（每日/每周） |

**Memory Policy 不做什么**：

- 不参与风险判定（`DecisionPolicy` 是唯一决策中心，ADR-0010）
- 不参与行动决策（`ActionExecutor` 是唯一执行中心，ADR-0011）
- 不修改 `BehaviorState` / `RiskSignal`（Memory 只读消费，不反向写入状态）
- 不直接调用 LLM（Agent 是消费者，Memory Policy 只做确定性投影）

**关键约束**：

> **Memory Policy is a transformation boundary, not a decision module.**
>
> 它把状态/事件流转换为 Memory Object，不产出决策、不修改上游状态、不调用推理。所有"判断"留在 `DecisionPolicy` / `Agent`。

#### 3.2.3 Memory Policy Invariants（不变量）

Memory Policy 必须满足以下四条不变量。这些不变量是**架构级约束**，工程方案必须保证，测试必须覆盖。

> **I1. Idempotency（幂等性）**
>
> **同一个 observation event 重复发送，只能产生同一条 MemoryRecord，不能产生多条。**
>
> ```
> RiskSignal(RAISED, event_id=X) 发送 1 次 → EpisodicRecord(id=derive(X))
> RiskSignal(RAISED, event_id=X) 发送 2 次 → EpisodicRecord(id=derive(X))（同一记录，不新增）
> RiskSignal(RAISED, event_id=X) 发送 3 次 → EpisodicRecord(id=derive(X))（同一记录，不新增）
> ```
>
> **实现要求**：MemoryRecord 的唯一键必须从 source event 的稳定标识派生（如 `event_id` / `signal_id`），不能用自增 ID 或随机 UUID。重复投递时 upsert，不 insert。
>
> **为何重要**：upstream（pipeline / MQTT）可能重试或重放；没有幂等性会导致同一事件被记录多次，污染 Semantic 聚合。

> **I2. Monotonicity（单调性）**
>
> **Memory history 不能重写过去已有的 Episode。**
>
> ```
> ✅ 允许：追加新 Episode / 聚合新 Semantic
> ❌ 禁止：修改已存在的 EpisodicRecord 的字段（如 retroactively 改 risk_level）
> ❌ 禁止：删除已发生的 Episode（除非合规删除，归 ADR-00xx 合规 ADR）
> ```
>
> **例外**：可追加 `correction` / `annotation` 字段（不改原字段，只附加修正说明），具体由工程方案定。
>
> **为何重要**：Agent 解释依赖历史一致性；重写过去会让"昨天为什么 HIGH"的答案不稳定，破坏可解释性。

> **I3. Causality（因果性）**
>
> **MemoryRecord 的 timestamp 必须 >= source event 的 timestamp。**
>
> ```
> VisitorEvent(ts=T1) → EpisodicRecord(ts=T2)
> 约束：T2 >= T1
> ```
>
> **为何重要**：允许 Memory 时间戳早于源事件会破坏因果链，导致 Agent 无法回答"这个记忆基于哪个事件"。

> **I4. Explainability（可解释性）**
>
> **每条 EpisodicRecord 必须引用 source evidence（至少一个 source event id 或 evidence reference）。**
>
> ```
> EpisodicRecord {
>   source_event_ids: [visitor_event_id, warning_event_id, ...],
>   evidence_refs: [evidence_item_id, ...],  # 见 §3.9 Memory Trust Layer
>   ...
> }
> ```
>
> **为何重要**：老人安全场景下，Memory 会影响家属通知/社区干预；没有 source evidence 的 Memory 是黑盒，Agent 回答"为什么认为异常"会变成"Memory 说的"——不可接受。每条记忆必须可追溯到源事件和证据。

**不变量验收标准**（工程方案落地时）：

- [ ] I1 测试：同一 event_id 重复投递 3 次，Memory 中只有 1 条记录
- [ ] I2 测试：写入 Episode 后尝试修改 risk_level，断言失败或只追加 correction
- [ ] I3 测试：构造 MemoryRecord(ts < source_event.ts)，断言拒绝
- [ ] I4 测试：构造无 source_event_ids 的 EpisodicRecord，断言拒绝

### 3.3 主键策略（与 ADR-0023 的关系）

Memory 的主键是 `person_identity_id`（ADR-0023），但 v1 它恒为 `None`。

**分层主键策略**：

| 记忆类型 | v1 主键（`person_identity_id=None`） | v2+ 主键（Phase 4 ReID 后） |
| --- | --- | --- |
| Short-term | `visitor_instance_id`（会话内稳定） | `person_identity_id`（跨会话稳定） |
| Episodic | `visitor_instance_id` + 时间戳 | `person_identity_id` + 时间戳 |
| Environment Semantic | 时间 / 时段 / 设备（不按身份） | 同 v1 |
| Identity Semantic | **不建**（v1 无身份，无法跨会话聚合） | `person_identity_id` 聚合 |

**v1 约束**（来自 ADR-0023）：

> 任何代码不得把 `visitor_instance_id` 当作 `person_identity_id` 使用；Memory/Profile 在 `person_identity_id` 为 None 时按 `visitor_instance_id` 建临时画像，标注"未确认身份"。

**后果**：

- v1 有 Short-term + Episodic + **Environment Semantic**（环境模式不依赖身份）
- v1 **无 Identity Semantic**（Phase 4 后启用）
- v1 的 Episodic Memory 按 `visitor_instance_id` 索引，Phase 4 后回填 `person_identity_id` 关联

### 3.4 与现有对象的关系（边界）

| 现有对象 | 是否变 Memory | 关系 |
| --- | --- | --- |
| `BehaviorState` | ❌ 不变 | Memory 的**输入来源**（StateSnapshot），不是 Memory 本身 |
| `RecentBehaviorStore` | ❌ 不变 | 属 **Short-term Memory 的滑窗统计实现**（TD-0024 优化后归位） |
| `VisitorEvent` | ❌ 不变 | Memory 的**输入来源**（RawEvent）；Episodic Memory 由 Episode Builder 投影 |
| `WarningEvent` | ❌ 不变 | Memory 的**输入来源**（RawEvent）；Episode Builder 投影为 Memory Object |
| `RiskSignal` | ❌ 不变 | Memory 的**输入来源**（TransitionEvent）；触发 Short-term 写入，不作为"状态"存入 |

**关键边界**：Memory 是**新增旁路**，不替换任何现有对象。现有对象继续按 ADR-0021 / ADR-0010 定义工作；Memory 在它们旁边**只读消费**，提炼摘要。

### 3.5 演进路径

```
Phase 1（当前，ADR-0021 已交付）
  BehaviorState / RiskSignal / VisitorEvent / WarningEvent
  ────────────────────────────────────────────────────────
  Memory: 无（只有 RecentBehaviorStore 滑窗统计）

Phase 4（身份系统化，ADR-0023）
  + IdentityResolver 产出真实 person_identity_id
  + Short-term Memory 落地（含 Snapshot 恢复，TD-0027 解决）
  + Episodic Memory 落地（Episode Builder 投影）
  + Environment Semantic Memory 可选启用（不依赖身份）
  ────────────────────────────────────────────────────────
  Memory: Short-term + Episodic + Environment Semantic
    - 进程重启状态恢复（TD-0027 解决）
    - 历史访问档案
    - 环境/时段模式

Phase 5（Agent）
  + Agent 消费 Memory 回答"为什么"
  + Identity Semantic Memory 启用（需 Phase 4 的 person_identity_id）
  ────────────────────────────────────────────────────────
  Memory: Short-term + Episodic + Environment Semantic + Identity Semantic
    - Agent: "当前状态异常(BehaviorState) + 历史模式匹配(Semantic) + 规则" → 解释
```

### 3.6 Memory Lifecycle（记忆生命周期状态机）

与 ADR-0021 的 `NONE → ACTIVE_RISK → NONE` 对应，Memory 也有自己的生命周期状态机。Memory Record 的状态流转：

> **关键约束**：**Memory Lifecycle is data lifecycle, not business state machine.**
>
> Memory Lifecycle 描述的是**一条 Memory Record 从产生到淘汰的数据流转**（类似 TTL / retention policy），**不是**业务状态机。它：
> - ❌ 不驱动业务决策（决策由 ADR-0010 DecisionPolicy 负责）
> - ❌ 不产出 RiskSignal / WarningEvent（信号由 ADR-0021 状态机负责）
> - ❌ 不被 Agent 直接消费（Agent 消费 Memory 内容，不消费 Lifecycle 状态）
> - ✅ 只描述"这条记录何时产生、何时转换、何时淘汰"
>
> Memory Lifecycle 与 ADR-0021 状态机是**两个独立的状态机**：ADR-0021 描述"风险业务的态"，Memory Lifecycle 描述"数据记录的态"。两者通过**事件触发**关联（如访客离场触发 Episode Closed），但状态机本身不耦合。

```
                   ┌──────────────────────────────────────────┐
                   │                                          │
                   ▼                                          │
┌──────────────┐  transition   ┌──────────────────┐  leave   │  ┌──────────────────┐
│              │  event        │                  │ ───────▶ │  │                  │
│  Raw State   │ ───────────▶  │ Active Working   │          │  │  Episode Closed  │
│  (observed)  │               │  Memory          │          │  │  (Episodic)      │
│              │               │                  │          │  │                  │
└──────────────┘               └──────────────────┘          │  └──────────────────┘
        │                             │                      │          │
        │                             │ periodic snapshot    │          │ periodic
        │                             │ (for recovery)       │          │ aggregation
        │                             ▼                      │          ▼
        │                      ┌──────────────┐              │  ┌──────────────────┐
        │                      │              │              │  │                  │
        └─────────────────────▶│  Short-term  │              │  │  Semantic        │
                               │  Snapshot    │              │  │  Aggregate       │
                               │              │              │  │                  │
                               └──────────────┘              │  └──────────────────┘
                                     │                       │          │
                                     │ expire (e.g. 5min     │          │ expire
                                     │   after leave)        │          │ (e.g. 90 days)
                                     ▼                       │          ▼
                               ┌──────────────┐              │  ┌──────────────────┐
                               │              │              │  │                  │
                               │  Discarded   │              │  │  Archived/       │
                               │              │              │  │  Forgotten       │
                               └──────────────┘              │  └──────────────────┘
                                                             │
                                  restart ──── recover ───────┘
```

**状态定义**：

| 状态 | 含义 | 触发转移 |
| --- | --- | --- |
| Raw State | 观测到但未写入 Memory | `RiskSignal` transition event → Active Working Memory |
| Active Working Memory | 当前访问生命周期内的 Short-term 记忆 | 访客离场 → Episode Closed |
| Short-term Snapshot | 周期快照（用于重启恢复） | expire（离场后 5 分钟）→ Discarded |
| Episode Closed | Episodic Memory 的一条记录 | periodic aggregation → Semantic Aggregate |
| Semantic Aggregate | Semantic Memory 的一条聚合 | expire（90 天）→ Archived/Forgotten |
| Discarded | 已删除 | （终态） |
| Archived/Forgotten | 归档或遗忘 | （终态） |

**与 ADR-0021 的对应关系**：

| ADR-0021 状态机 | Memory Lifecycle |
| --- | --- |
| `NONE → ACTIVE_RISK`（RAISED） | Raw State → Active Working Memory（写入 Short-term） |
| `ACTIVE_RISK → NONE`（CLEARED） | （不影响 Memory Lifecycle，由访客离场触发 Episode Closed） |
| 访客进入（track created） | 开始 Raw State 观测 |
| 访客离场（track left） | Active Working Memory → Episode Closed |

**关键约束**：

- Memory Lifecycle **不耦合** ADR-0021 状态机：RAISED/CLEARED 只触发 Short-term 写入，不直接驱动 Episode Closed
- Episode Closed 由**访客离场**（`VisitorEvent`）触发，不是由 `RiskSignal` CLEARED 触发（CLEARED 可能发生在访客仍在场时，如风险降级）
- Semantic Aggregate 由**周期性聚合**触发（如每日 cron），不是实时

### 3.7 Snapshot 原则（TD-0027 状态恢复）

Short-term Memory 的周期快照用于进程重启状态恢复（TD-0027）。快照的核心原则：

> **Snapshot stores reconstructable state, not derived metrics.**

**存什么**（reconstructable state）：

| 字段 | 例子 | 原因 |
| --- | --- | --- |
| `track_id` | 5 | 标识，不可推导 |
| `visitor_instance_id` | `uuid-xxx` | 标识，不可推导 |
| `risk_phase` | `ACTIVE_RISK` | 状态机态，不可推导 |
| `raised_at` | `2026-07-27T18:35:00Z` | 跃迁时刻，不可推导 |
| `enter_time` | `2026-07-27T18:30:00Z` | 进入时刻，不可推导 |
| `last_seen_frame` | 12345 | 最后观测帧，用于断点续传 |

**不存什么**（derived metrics）：

| 字段 | 例子 | 原因 |
| --- | --- | --- |
| `dwell_seconds` | 350 | 可由 `now - enter_time` 推导；存了会和当前时间冲突 |
| `risk_score` | 0.72 | 可由 `RiskFeature` 重新计算 |
| `visits_in_window` | 3 | 可由 `RecentBehaviorStore` 重新统计 |

**为何不存 derived metrics**：

```
恢复旧状态（dwell_seconds=350）
        +
当前时间（now = enter_time + 600s）
        =
状态冲突（dwell_seconds 应为 600，不是 350）
```

存 derived metrics 会导致恢复后状态与当前时间矛盾。Snapshot 只存不可推导的标识和时刻，恢复后由 pipeline 重新计算 derived metrics。

**验收标准**（TD-0027 修复时）：

- [ ] Snapshot 只含 reconstructable state，不含 derived metrics
- [ ] 进程重启后，从 Snapshot 恢复 `risk_phase` / `raised_at` / `enter_time`
- [ ] 恢复后 `dwell_seconds` 由 `now - enter_time` 重新计算，不从 Snapshot 读取
- [ ] 新增测试：模拟进程重启，验证恢复后状态与连续运行一致（除 derived metrics 外）

### 3.8 Memory Conflict Resolution（冲突处理边界）

> **本节只定义冲突的"存在性"与"处理边界"，不定义具体冲突解决策略**——后者归未来 **ADR-00xx Memory Consistency Policy**。

#### 冲突场景

未来 Agent 一定会遇到 Memory 冲突：

| 场景 | 旧 Memory | 新 Episode | 冲突 |
| --- | --- | --- | --- |
| 时段模式 | "该访客通常晚上出现" | "今天下午出现" | 时段不符 |
| 风险趋势 | "历史风险低" | "本次风险高" | 风险等级跳跃 |
| 访问频率 | "过去 30 天 2 次" | "本周已 5 次" | 频率突变 |

#### 本 ADR 确定的边界

1. **冲突是客观存在的**：本 ADR 不假装 Memory 永远自洽；新 Episode 可能与旧 Semantic 矛盾
2. **冲突不由 Memory Policy 自动解决**：Memory Policy 是 transformation boundary（§3.2.2），不做冲突判定
3. **冲突不由 DecisionPolicy 解决**：DecisionPolicy（ADR-0010）只看当前帧，不看 Memory 历史
4. **冲突处理归未来 ADR**：具体策略（覆盖 / 衰减 / 版本化 / contradiction handling）由 **ADR-00xx Memory Consistency Policy** 定义

#### 未来 ADR-00xx 需回答的问题

| # | 问题 | 候选方向 |
| --- | --- | --- |
| C1 | 旧 Semantic 被新 Episode 矛盾时，覆盖还是衰减？ | 衰减（旧模式 confidence 随时间下降）+ 版本化（保留历史版本） |
| C2 | Memory 是否有 confidence 字段？ | 是（与 §3.9 Trust Layer 协同） |
| C3 | Agent 如何消费冲突的 Memory？ | 同时提供新旧 + 标注冲突，由 Agent 推理 |
| C4 | 合规删除与 Monotonicity（I2）冲突时如何处理？ | 合规删除优先（合规 ADR 定义例外） |

#### v1 约束

v1 无 Semantic Memory（除可选的 Environment Semantic），冲突场景有限。v1 的 Episodic Memory 按 §3.2.3 I2 Monotonicity 只追加不重写，不涉及冲突。**冲突处理的复杂度归 Phase 4-5**。

### 3.9 Memory Trust Layer（可信度层）

> **核心判断**：老人安全场景下，Memory 会影响家属通知 / 社区干预 / Agent 解释。**Memory 本身必须可信，不能是黑盒。**

#### 问题：没有 Trust Layer 的风险

```
Agent: "为什么认为这个人异常？"
Memory: "因为历史记录显示异常"
Agent: "历史记录从哪来？"
Memory: "..."
```

→ **黑盒**。家属/社区无法追溯，Agent 解释不可信。

#### Trust Layer 的组成

每条 EpisodicRecord 必须携带 Trust Metadata：

| 字段 | 语义 | 示例 |
| --- | --- | --- |
| `confidence` | 这条记忆的可信度 | 0.82 |
| `source` | 数据来源标识 | `camera_01` / `device_002` |
| `evidence_refs` | 引用的证据项 ID（链接 ADR-0022 EvidenceItem） | `[ev_001, ev_002]` |
| `source_event_ids` | 源事件 ID（满足 I4 Explainability） | `[visitor_event_id, warning_event_id]` |
| `model_version` | 产出该记忆的模型/规则版本 | `yolo-v8n` / `rule-engine-v1.2` |
| `created_at` | 记忆产生时刻（满足 I3 Causality） | `2026-07-27T18:45:00Z` |

> **实现中立**：本 ADR 只约束"必须携带上述信息"，不固定字段名/格式。具体 schema 由工程方案定。

#### 与 ADR-0022 Evidence Chain 的连接

```
MemoryRecord (EpisodicRecord)
      │
      │ evidence_refs
      │
      ▼
EvidenceReference ──────▶ ADR-0022 EvidenceItem
      │                      │
      │                      │ 类型化证据（视频片段/音频/规则触发）
      │                      │
      ▼                      ▼
Trust Layer             Evidence Chain（可审计）
```

**连接关系**：

- ADR-0022 定义 `EvidenceItem`（类型化证据）和 `EvidenceAggregator`（只整理不重推）
- 本 ADR 的 EpisodicRecord 通过 `evidence_refs` 引用 `EvidenceItem`
- Agent 解释时可沿 `MemoryRecord → evidence_refs → EvidenceItem` 检索原始证据

#### Trust Layer 不做什么

- ❌ **不重新推理**：Trust Layer 只记录可信度元数据，不重新跑模型/规则（ADR-0022 EvidenceAggregator 同样原则）
- ❌ **不修改 Memory 内容**：Trust Layer 是附加元数据，不改 EpisodicRecord 本身（与 I2 Monotonicity 协同）
- ❌ **不驱动决策**：DecisionPolicy 不读 Trust Layer；Agent 读 Trust Layer 仅用于解释，不用于提高/降低风险等级

#### 为何不把 confidence 放进 DecisionPolicy

```
❌ 错误：DecisionPolicy 读取 Memory.confidence 调整风险等级
   → Memory 变成第二决策中心（违反 ADR-0010）
   → 循环论证（"高风险因为历史高风险"）

✅ 正确：DecisionPolicy 只看当前帧的 RiskFeature
         Agent 读 Memory + Trust Layer 用于解释
         Memory 不影响风险等级，只影响解释质量
```

#### v1 约束

v1 的 EpisodicMemory 落地时（Phase 4）必须同时落地 Trust Layer 基础字段（`confidence` / `source` / `evidence_refs` / `source_event_ids`）。`model_version` 在 Phase 5（Agent 消费）前必须完善。

---

## 4. 动机（Rationale）

### 4.1 为什么是三类而不是一层

单层 Memory（如"把所有 VisitorEvent 存数据库"）的问题：

- **易变态爆炸**：`BehaviorState` 逐帧落盘 = 海量冗余
- **查询无效**：Agent 需要"18:30-18:45 异常停留"，不需要"18:30:01 dwell=1, 18:30:02 dwell=2..."
- **时间尺度混淆**：当前状态（秒级）与历史模式（月级）混在一起，查询/淘汰策略无法统一

三类记忆按**时间尺度**与**抽象层级**划分，各自有明确的淘汰策略和查询模式。

### 4.2 为什么需要 Memory Policy

没有 Memory Policy 的问题：

- 状态直接落盘 = 易变态爆炸
- 事件直接堆叠 = 无法回答"模式是什么"
- Memory 变成"数据垃圾场"而非"可查询的知识"

Memory Policy 是**从状态到记忆的提炼层**，确保 Memory 存的是**值得跨生命周期保留的信息**，不是原始状态的堆叠。

### 4.3 为什么 Semantic Memory 拆分为 Environment / Identity 两类

原设计把 Semantic Memory 整体推迟到 Phase 4（依赖 `person_identity_id`）。但 Semantic Memory 实际有两类聚合维度：

- **Identity Semantic**：按 `person_identity_id` 聚合（"这个人过去 30 天来了 8 次"）—— 依赖身份，v1 不启用
- **Environment Semantic**：按时间/时段/设备聚合（"这个家庭晚上 18-22 点陌生访客概率高"）—— **不依赖身份**，v1 可启用

把 Semantic Memory 整体绑定身份会过度限制 v1 的能力：环境模式不依赖身份，强行绑定会让 v1 损失"环境/时段模式"这一可用能力。

拆分后，v1 可按场景需求启用 Environment Semantic，Identity Semantic 等 Phase 4。

### 4.4 为什么需要 Episode Builder

没有 Episode Builder 的问题：

- `WarningEvent` 字段变化（如 `reason` 从字符串变对象）会污染 Memory schema
- Memory 变成业务对象的 dump 场所，失去抽象
- Agent 无法消费"原始事件堆叠"，需要"事件叙述"

Episode Builder 是 **Event Projection 层**，把业务事件投影为 Memory 自有的 Memory Object（含 human-interpretable summary），隔离业务对象变化。

### 4.5 为什么 Memory 不参与决策

ADR-0010 确立"DecisionPolicy 是唯一决策中心"。Memory 参与决策会导致：

- 双决策中心漂移（DecisionPolicy vs Memory 历史）
- 决策不可解释（"为什么 HIGH？因为 Memory 说是高风险"——循环论证）

Memory 只读消费，为 Agent（Phase 5）提供输入，不直接驱动 WarningEvent。

### 4.6 为什么 Snapshot 不存 derived metrics

存 `dwell_seconds` 等 derived metrics 会导致恢复后状态与当前时间矛盾（详见 §3.7）。Snapshot 只存不可推导的标识和时刻（`track_id` / `risk_phase` / `raised_at` / `enter_time`），derived metrics 由 pipeline 在恢复后重新计算。

---

## 5. 后果（Consequences）

### 5.1 正面

- **状态与记忆分离**：`BehaviorState` 继续按 ADR-0021 工作，Memory 在旁边只读消费，零回归风险
- **为 Agent 铺路**：Phase 5 Agent 可直接消费三类记忆回答"为什么"，无需回头补数据基础设施
- **解决 TD-0027**：Short-term Memory 的 Snapshot 原则（§3.7）支持进程重启状态恢复
- **归位 TD-0024**：`RecentBehaviorStore` 明确定位为 Short-term Memory 的滑窗统计实现，eviction 优化有明确归宿
- **演进友好**：v1 可做 Short-term + Episodic + Environment Semantic，Phase 4 加 Identity Semantic，Phase 5 加 Agent，每步增量不重写
- **业务解耦**：Episode Builder 隔离业务对象变化，Memory Object schema 稳定

### 5.2 负面

- **新增抽象层**：Memory Policy + Episode Builder 是新抽象，需额外的测试/维护成本
- **v1 价值有限**：v1 无 Identity Semantic（缺 `person_identity_id`），Agent 暂时无法消费个人模式
- **存储成本**：Episodic Memory 长期保留（90 天）会产生存储压力（虽每条记录是摘要而非原始帧）
- **Schema 演进**：Memory Object schema 需独立版本管理（Episode Builder 投影规则可能随业务对象变化而调整）

### 5.3 技术债

| 项 | 关系 |
| --- | --- |
| TD-0024（RecentBehaviorStore eviction） | 本 ADR 归位：`RecentBehaviorStore` = Short-term Memory 滑窗实现，eviction 优化是 Short-term Memory 的清理策略 |
| TD-0027（状态恢复） | 本 ADR 解决方向：Short-term Memory 周期快照 + 重启恢复 |
| 新 TD（本 ADR 引入） | Memory Policy 的具体提炼规则（状态转移/周期快照/生命周期转换）需在工程方案中定义，本 ADR 只定义抽象 |

---

## 6. 替代方案（Alternatives）

### 6.1 单层 Memory（被否决）

**方案**：把所有对象（`BehaviorState` / `VisitorEvent` / `RiskSignal`）无差别存数据库。

**否决原因**：
- 易变态爆炸（`dwell_seconds` 逐帧落盘）
- 时间尺度混淆（秒级状态与月级模式混在一起）
- 查询无效（Agent 需要"事件摘要"，不需要"原始状态流"）
- 违反"状态 ≠ 记忆"核心判断

### 6.2 Memory 直接参与决策（被否决）

**方案**：Memory 存历史风险，`DecisionPolicy` 查询 Memory 调整决策（如"这个人历史高风险，本次也 HIGH"）。

**否决原因**：
- 违反 ADR-0010"DecisionPolicy 是唯一决策中心"
- 双决策中心漂移（DecisionPolicy vs Memory）
- 决策不可解释（循环论证）
- Memory 应为 Agent（Phase 5）提供输入，不直接驱动 WarningEvent

### 6.3 Semantic Memory 在 v1 强建（被否决）

**方案**：v1 用 `visitor_instance_id` 聚合建 Semantic Memory。

**否决原因**：
- 违反 ADR-0023"不冒充身份"约束
- `visitor_instance_id` 是会话级，跨会话不关联，聚合无意义
- 会产生错误的模式（如把 5 次访问的 5 个 `visitor_instance_id` 当成 5 个不同的人）

### 6.4 Memory Policy 作为独立服务（被否决）

**方案**：Memory Policy 独立部署，经 API 调用。

**否决原因**：
- 过度工程化（v2 边缘部署，单进程足够）
- 增加网络延迟和故障点
- Memory Policy 是 pipeline 内的提炼层，不是独立服务

---

## 7. 范围边界（明确不做什么）

| 项 | 是否本 ADR 职责 | 归属 |
| --- | --- | --- |
| 三类记忆模型（Short-term / Episodic / Semantic） | ✅ 是 | 本 ADR §3.1 |
| Semantic 拆分为 Environment / Identity | ✅ 是 | 本 ADR §3.1.3 |
| Memory Policy 抽象（transformation boundary） | ✅ 是 | 本 ADR §3.2 |
| Episode Builder（Event Projection 层） | ✅ 是 | 本 ADR §3.2.1 |
| Memory Policy 输入输出契约 | ✅ 是 | 本 ADR §3.2.2 |
| Memory Policy Invariants（I1-I4） | ✅ 是 | 本 ADR §3.2.3 |
| 主键策略 | ✅ 是 | 本 ADR §3.3 |
| Memory Lifecycle 状态机（数据生命周期） | ✅ 是 | 本 ADR §3.6 |
| Snapshot 原则（reconstructable vs derived） | ✅ 是 | 本 ADR §3.7 |
| Memory Conflict Resolution 边界（存在性 + 归属） | ✅ 是（只定义边界） | 本 ADR §3.8 |
| Memory Trust Layer 存在性 + 组成 | ✅ 是 | 本 ADR §3.9 |
| Memory Conflict 具体解决策略（覆盖/衰减/版本化） | ❌ 否 | 未来 ADR-00xx Memory Consistency Policy |
| Trust Layer confidence 计算/衰减算法 | ❌ 否 | 未来 ADR-00xx Memory Consistency Policy |
| 存储格式（SQLite/Parquet/...） | ❌ 否 | 工程方案 |
| 查询接口（REST/GraphQL/...） | ❌ 否 | 工程方案 |
| Context Builder（消费层上下文组装契约） | ❌ 否（契约由 ADR-0025 定义） | ADR-0025 |
| Agent 推理逻辑（规则 v2 / LLM v2 / Agent 本身） | ❌ 否 | Phase 5 ADR |
| ReID 算法 | ❌ 否 | Phase 4 ADR-0023 |
| Memory Policy 的具体提炼规则（状态转移触发条件/快照频率/聚合粒度） | ❌ 否（本 ADR 只定义抽象） | 工程方案 |
| Episode Builder 的具体投影规则（字段映射/summary 模板） | ❌ 否（本 ADR 只定义必要性） | 工程方案 |
| Memory Object schema 字段定义 | ❌ 否 | 工程方案 |
| 数据合规/隐私（数据保留/删除/审计） | ❌ 否（需独立 ADR） | 未来合规 ADR |

---

## 8. 开放问题（Open Questions）

以下问题本 ADR 不决策，留给工程方案或后续 ADR：

| # | 问题 | 留给 |
| --- | --- | --- |
| O1 | Short-term Memory 周期快照的频率（30s? 60s?） | 工程方案 |
| O2 | Episodic Memory 的保留时长（90 天? 180 天?） | 工程方案 + 合规 ADR |
| O3 | Memory Policy 的状态转移写入触发条件（仅 RAISED/CLEARED? 还是含 BehaviorPhase 变化?） | 工程方案 |
| O4 | Environment Semantic Memory 的聚合粒度（日? 周? 月?）+ 是否在 v1 启用 | 工程方案 |
| O5 | 进程重启后 Short-term Memory 如何恢复（从 Snapshot? 从 Episodic 回溯?） | TD-0027 + 工程方案 |
| O6 | Memory 是否需要 export 给中心服务（跨设备共享） | 未来跨设备 ADR |
| O7 | 数据合规（老人隐私 / 数据删除权 / 审计要求） | 未来合规 ADR |
| O8 | Episode Builder 的 summary 字段生成规则（模板? LLM? 混合?） | 工程方案 |
| O9 | Memory Object schema 的版本管理策略 | 工程方案 |
| O10 | Memory Conflict 具体策略（覆盖/衰减/版本化/contradiction handling） | 未来 ADR-00xx Memory Consistency Policy |
| O11 | Trust Layer confidence 计算/衰减算法 | 未来 ADR-00xx Memory Consistency Policy |
| O12 | Invariants I1-I4 的具体实现机制（upsert 策略/时间戳校验位置/evidence 引用校验） | 工程方案 |

---

## 9. 与现有 ADR 的关系

| ADR | 关系 |
| --- | --- |
| **ADR-0021**（实时风险流） | 本 ADR 是 §7.1 预留的 Memory ADR；消费 ADR-0021 产出的 `BehaviorState` / `RiskSignal` |
| **ADR-0023**（身份连续性） | `person_identity_id` 是 Memory 的主键；v1 为 None 限制了 Identity Semantic Memory |
| **ADR-0010**（WarningEvent 决策） | `WarningEvent` 是 Episodic Memory 的来源；Memory 不参与决策（§3.9 Trust Layer 不驱动 DecisionPolicy） |
| **ADR-0014**（三级冻结） | Memory 是新增旁路，不入 L1/L2/L3 冻结 |
| **ADR-0022**（证据链） | EpisodicRecord 通过 `evidence_refs` 引用 `EvidenceItem`；Trust Layer（§3.9）连接 Evidence Chain 实现可审计 |
| **未来 ADR-00xx**（Memory Consistency Policy） | 本 ADR §3.8 定义冲突处理边界；具体策略（覆盖/衰减/版本化）+ confidence 计算/衰减算法归未来 ADR |

---

## 10. 状态

本 ADR 为 **Accepted**（2026-07-28），已在 `docs/ADR/README.md` 清单登记。

工程落地方案：`docs/DESIGN-memory-pipeline.md`（按 Stage A–H 拆分）。

### 10.1 实施进度（Slices 1–6 + Stage F + Integration Closure 已合入 main）

> 所有 Slice 均经 `ruff` + `pytest` 门禁。Stage F（Pipeline Shadow Mode 影子写入）已接线：
> `runtime/pipeline.py` 在 `memory.enabled + memory.episodic_shadow` 同时为真时，把每次访客离场
> 投影为 `EpisodicRecord` 写入 `InMemoryStore`（**默认关闭，v1 不产 Warning**）。PR 编号见 `docs/08_roadmap.md` §8.5。

| Slice | 对应 Stage | 内容 | PR | 状态 |
| --- | --- | --- | --- | --- |
| Slice 1 | Stage A（基础） | `records.py`（ShortTermRecord / EpisodicRecord / SemanticAggregate）+ `MemoryPolicy` ABC + 不变量 I1–I4 校验 | #77 / #78 | ✅ 已合并 |
| Slice 2 | Stage A（投影） | `DefaultShortTermPolicy` 实现 `transform_short_term` | #79 / #80（+ #81 文档修正） | ✅ 已合并 |
| Slice 3 | Stage C + D + E | `snapshot.py` / `cold_start.py`（Snapshot 持久化 + 冷启动恢复，解 TD-0027） | #82 | ✅ 已合并 |
| Slice 4 | Stage B | `episode_builder.py`：`DefaultEpisodeBuilder` 实现 `project_episode`（VisitorEvent + WarningEvent + ActionCommand → EpisodicRecord） | #83 | ✅ 已合并 |
| Slice 5 | §5.6 | `store.py`：`MemoryStore` / `InMemoryStore`（Episodic 持久化后端，v1 内存 + JSON 序列化）+ `InvariantViolationError` | #84 | ✅ 已合并 |
| Stage F | Pipeline Shadow Mode | `runtime/pipeline.py`：`InMemoryStore` + `DefaultEpisodeBuilder` 影子写入（每访客离场 → `EpisodicRecord` 入 store）；`memory.episodic_shadow` 开关默认关闭；`DefaultEpisodeBuilder` 包级导出 | #87 | ✅ 已合并 |
| Slice 6 | §8.8 | Memory Evaluation（验证切片，**不实现** Semantic 聚合）：压缩比 ≥100:1（`test_memory_evaluation.py`）+ 信息保留字段校验 + Replay Test（`test_memory_replay.py` §6.7，baseline `tests/fixtures/memory_baseline.json`）。附带修复 `DefaultEpisodeBuilder` 确定性缺陷（均由新增用例暴露）：① 关联 warning 按 `created_at` 排序、关联 action 按 `command_id` 排序（**两处对称**），保证上游乱序投递 warning/action 时回放仍一致（I1/§6.7.2）；② 关联 warning/action 按 `warning_id`/`command_id` 去重——上游重试会使 `source_event_ids` 变长，令重投 record 与首投字段不等而触发 I2 违规（Shadow Mode 下 episode 被静默丢弃）。另为 `MemoryStore` 增补公共只读计数口 `short_term_count()`（评估用例不再依赖后端私有结构，v2 迁 SQLite 无感） | #88 | ✅ 已合并 |
| — | 清理 | 移除未使用的 `TYPE_CHECKING` 导入 | #85 | ✅ 已合并 |
| **Integration Closure · Slice B** | 外部闭环·真实链路 | `test_memory_closure_slice_b.py`：Contract E2E（cached detection 驱动整链）+ 重启恢复 + 失败隔离 + Lifecycle Closure（场景 1/2/3/4） | #93 | ✅ 已合并 |
| **Integration Closure · Slice C** | 外部闭环·用户价值 | `memory/query.py`：`MemoryQuery.compose_context`（Product Closure，V0 边界冻结） | #91 | ✅ 已合并 |
| **Integration Closure · Slice A** | 外部闭环·代码整理 | `runtime/memory_hook.py`：抽出 `MemoryHook`（0 行为变化，门控/容错/metrics 语义不变） | #94 | ✅ 已合并 |
| **Integration Closure · Slice D** | 外部闭环·文档冻结 | 4 份文档（`MEMORY_ARCHITECTURE.md` / `MEMORY_OPERATION_GUIDE.md` / `MEMORY_TEST_REPORT.md` / `DESIGN-observation-contract.md`）+ 本 ADR 标注 | #95 | ✅ 已合并 |

> **消费层承接（ADR-0025）**：本 ADR 定义"存储过去"，Memory 如何被消费以反哺理解（Retrieval / Aggregation / Context Builder / Reasoning Interface + 执行模型）已由 **ADR-0025** 定义。本 ADR 不再覆盖消费层契约；推理逻辑本身仍归 Phase 5 Reasoning ADR。

**Integration Closure 完成标志**：System × Memory 外部闭环全部收口——
B（真实链路闭环，证明系统存在）→ C（Product Closure，证明价值）→ A（MemoryHook 代码整理）→ D（文档冻结，纯文档不重构接口）。
Memory 已真正融入整体架构：真实事件进入 Memory、能消费出可审计用户价值、结构清晰可单测。后续路线：Multimodal Evidence Fusion → Audio → Agent（见 `docs/DESIGN-memory-integration-closure.md` §6）。

**待办（未实现，v1 范围外）**：
- Slice 6（Memory Evaluation）已完成：仅量化验收 Memory 系统有效性（压缩/保留/一致性），不新增存储或聚合功能
- Stage G：Environment Semantic Aggregator（占位，v1 不实现）
- Stage H：Identity Semantic Aggregator（依赖 Phase 4 ReID，v1 不实现）
- 多模态接口泛化（Observation 契约）→ 留待 Multimodal 阶段按 `docs/DESIGN-observation-contract.md` + 新 ADR 实施（本阶段只出契约文档，不改 `VisitorEvent`/`MemoryPolicy`/`EpisodeBuilder`/`EpisodicRecord`）
