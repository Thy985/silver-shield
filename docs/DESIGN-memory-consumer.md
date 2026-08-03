# DESIGN-memory-consumer.md · Memory Consumer Layer 工程落地方案

- **状态**：Partial（C-0~C-6 已合；C-6=Reasoning Engine 接入，本 PR 待合并；**决策增强 = Phase 2 推后**，不在此 PR 范围）
- **日期**：2026-08-02（C-0~C-5）/ 2026-08-03（C-6）
- **承接**：ADR-0025（Memory Consumer Architecture，Accepted）
- **前置 ADR**：ADR-0024（Memory 架构，定义"存储过去"）/ ADR-0021（实时风险流）/ ADR-0023（身份连续性）/ ADR-0010（DecisionPolicy）/ ADR-0022（Evidence Chain）

> **文档职责边界**：本 DESIGN 是 ADR-0025 的**工程落地方案**，把"架构与契约边界"翻译成**可实现的结构、字段、Slice 拆分与测试策略**。凡 ADR-0025 标为"开放问题 / 工程方案"的（如检索算法、聚合窗口具体值、序列化格式），本 DESIGN 给**默认实现选择 + 预留扩展点**，但不做最终算法选型辩论（那归各自工程迭代）。推理引擎（Reasoning Engine）本身不在本方案实现范围。

---

## 0. 总览

### 0.1 目标

把 ADR-0025 定义的 **Memory Consumer Layer** 落成代码：在 Memory（ADR-0024）与未来 Reasoning 之间插入一个**只读、单向、不决策**的消费层，让"记忆反哺理解"第一次有可落地的工程载体。

### 0.2 与 ADR 的关系

| 维度 | 归属 |
| --- | --- |
| 为什么需要消费层、组件职责、硬边界、执行模型 | ADR-0025（架构） |
| Memory Object / `MemoryQuery.compose_context` / Trust Layer / Invariants | ADR-0024（被消费） |
| 当前事件（BehaviorState / VisitorEvent / RiskSignal） | ADR-0021 |
| `person_identity_id` 主键 | ADR-0023 |
| 决策中心 / `DecisionRequest` 落点 | ADR-0010 |
| `evidence_refs` 引用 | ADR-0022 |

### 0.3 本方案范围

- ✅ 实现四组件（Retrieval / Aggregation / Context Builder / Reasoning Interface 交付边界）+ 编排 + 触发接入（模式 B，修订见 §4.1）。
- ✅ 三数据契约（`ReasoningInput` / `ReasoningResult` / `DecisionRequest`）的字段具体化。
- ✅ 不变量 C1–C5 的测试覆盖。
- ✅ **C-6 实现规则参考推理引擎**（`RuleBasedReasoningEngine`，消费 `ReasoningInput` →
  产出 `ReasoningResult`）：只读、确定性、非分数、非决策（见 §4.3 / Errata C-6）。
- ✅ C-6 把 `ReasoningResult` 经 `FrameResult.reasoning_results` 做 **Shadow 观测**（默认关闭）。
- ⚠️ **决策增强（Context → Inference → Decision 闭环）刻意推后到 Phase 2**（ADR-0010
  单一决策中心边界）：C-6 的 `ReasoningResult` **不**被喂回 `DecisionPolicy`，只暴露、不回流。
  是否用其增强决策归 Phase 2 独立评审，不在本 PR。
- ❌ 不实现**真实**推理算法（规则 v2 / LLM v2 / Agent）——那是 Phase 5 Reasoning ADR 的事；
  C-6 仅落地一个确定性的**规则参考推理**默认实现（可被未来 `ReasoningEngine` 替换）。
- ❌ 不实现写侧 Semantic Aggregator（Stage G/H，ADR-0024 推迟）——Aggregation 只做读侧组合。
- ❌ 不引入 LLM。

---

### 0.4 数据来源与回放闭环（Memory Dataset / Episode Replay Layer）

> **本方案此前的头号工程风险**（Owner review 指出）：组件设计正确，但"Consumer 吃什么真实 Memory 数据、如何产生、如何验证 Memory 真改变了理解"没有定义。本节约束这一缺口——这是验收前必须先解决的前提。

**Consumer 的真实数据来源**：`ReasoningInput.historical_context` 里的 `EpisodicRecord` **不是凭空造的**，而是由 ADR-0024 的整条写入链路产生的：

```
CCTV 视频
  │  Perception Pipeline（检测 / 跟踪 / 行为 / 风险）
  ▼
BehaviorState / RiskSignal
  │  MemoryHook（PR#94，已合）
  ▼
Episode Builder（ADR-0024 Slice 1-3：抽象成 EpisodicRecord）
  │
  ▼
Memory Store（Short-term / Episodic / Semantic）
  │  MemoryQuery.compose_context（ADR-0024 Slice C，已合 #91）
  ▼
Memory Dataset / Episode Replay Layer   ← 本方案明确的"消费者数据源"边界
  │
  ▼
MemoryConsumer.Retrieval → Aggregation → Context Builder → ReasoningInput
```

- **Memory Dataset** = Memory Store 中已落库、可查询的 `EpisodicRecord` 集合（经 `MemoryQuery.compose_context` 暴露）。
- **Episode Replay Layer** = 一个回放工具：把**录制的真实 CCTV 样本**喂进完整 Perception Pipeline → Memory 写入链路，**重产** `EpisodicRecord`，使 Consumer 验证端到端可跑、可复现。它不造数据，只"重放真实视频行为"。

**验证三阶段（禁止纯 Mock 作为主验证）**：

| 阶段 | 数据 | 用途 | 能否证明"Memory 反哺理解" |
| --- | --- | --- | --- |
| A · 纯 Mock（仅开发期） | 手写 `EpisodicRecord` JSON | C-0~C-2 接口联调、提速 | ❌ 只能证明 Consumer 代码能跑 |
| B · 录制 CCTV 回放（主验证） | 真实 CCTV → Pipeline → Memory | C-3 起 + 集成 + Shadow | ✅ 证明 CCTV→Memory 链路有价值、Consumer 真读到历史 |
| C · 由回放派生的 Case（见新文档） | 从 B 的真实回放整理出的结构化 case | 回归 / 验收 | ✅ 每个 case 针对一类"Memory 价值" |

> Mock 只用于 C-0~C-2（接口 / 开发快）；**C-3 及之后必须用 B/C 的真实 Memory 数据**。否则会写出"很漂亮但无真实记忆证明"的 Consumer。

**配套文档**：验证数据（case 设计、fixture 结构、回放执行）单独定义于 **`DESIGN-memory-replay-dataset.md`**（本 PR 一同新增）。

### 0.5 工程推进顺序（M0–M5，最高优先 = 数据闭环）

不要直接 C-0~C-5 平推。按"先打通数据闭环，再写消费代码"的顺序：

| 阶段 | 对应 Slice | 目标 | 验收 |
| --- | --- | --- | --- |
| **M0 · 数据闭环** | （先于 C-1） | 建立 `memory_replay_dataset` + Episode Replay Layer，能重放已有 CCTV 跑通 Memory → Consumer | 真实 CCTV → Memory → 至少一个 `ReasoningInput` 产出（见 `DESIGN-memory-replay-dataset.md`） |
| **M1 · Consumer Skeleton** | C-0 | `contracts.py` + `interfaces.py` + `exceptions.py` | 类型可构造、ABC 不可实例化、ruff 通过 |
| **M2 · Retrieval** | C-1 | 基于 `MemoryStore.get_episodic_by_visitor` 的规则召回，吃真实 Memory 数据 | C3 确定性、召回真实 `EpisodicRecord` |
| **M3 · Aggregation** | C-2 | 读侧聚合 + 置信度分级 | 三档均产 `VisitorProfile`、模式发现正确 |
| **M4 · Context** | C-3 | 组装 `ReasoningInput` | C1 无 score、C5 溯源 |
| **M5 · Shadow Mode** | C-4 / C-5 | `MemoryConsumerHook` 接入 pipeline，门控 + 非阻塞 | Consumer 不影响主链路；记录 old decision vs consumer context 差异 |

> M0 优先级最高：没有真实 Memory 数据，后续 C-1~C-5 只是"能跑的空壳"。M5 的 Shadow Mode 重点是"Consumer 不影响主链路"，输出如 `old decision: LOW / consumer context: 发现历史重复访问模式 / difference: 记录`。

---

## 1. 模块结构（承接 ADR-0025 §3.8）

```
src/home_perception/memory/consumer/
   __init__.py
   interfaces.py      # MemoryConsumer / Retrieval / Aggregation / ContextBuilder ABC
   contracts.py       # ReasoningInput / ReasoningResult / DecisionRequest dataclasses
   orchestrator.py    # MemoryConsumer.consume()：按序驱动 Retrieval→Aggregation→ContextBuilder
   retrieval.py       # RuleBasedRetrieval 默认实现（只召回）
   aggregation.py     # RuleBasedAggregation 默认实现（只计算）
   context.py         # ReasoningInput 组装（只组装）
   reasoning.py       # RuleBasedReasoningEngine 默认参考推理（C-6，消费 ReasoningInput → ReasoningResult）
   triggers.py        # MemoryConsumerHook：模式 B 触发接入（门控 + 容错，含 maybe_reason 推理接入）
   exceptions.py      # ConsumerError / RetrievalError / AggregationError / BelowThresholdError
tests/home_perception/memory/consumer/
   test_contracts.py
   test_retrieval.py
   test_aggregation.py
   test_context.py
   test_orchestrator.py
   test_invariants.py     # C1–C5 不变量
   test_triggers.py
```

> 落点原则同 ADR-0025 §3.8：Consumer 放在 `memory/` 包内，存储侧（policy / episode_builder / store / query）只写，消费侧（consumer/）只读。Reasoning Engine（未来）在 `memory/consumer` 之外消费 `ReasoningInput`。

---

## 2. 数据契约字段具体化（承接 ADR-0025 §3.5）

### 2.1 `ReasoningInput`（Consumer 输出，Context Builder 产物）

| 字段 | 类型 | 来源 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `current_event` | `CurrentEvent` | ADR-0021 运行时对象 | ✅ | 当前触发事件（RiskSignal / VisitorEvent） |
| `historical_context` | `list[EpisodicRecord]` | Retrieval 召回 | ✅ | 按相关性排序的相关历史原始记录（ADR-0024） |
| `visitor_profile` | `VisitorProfile \| None` | Aggregation 计算 | 否 | 访客长期画像；低于观测阈值时为 `None` 或标 `confidence=low`；**必带 `identity_confirmed: bool`**（v1 常 `False`，对齐 ADR-0023） |
| `risk_pattern` | `RiskPattern \| None` | Aggregation 计算 | 否 | 风险模式（非分数） |
| `evidence_refs` | `list[EvidenceRef]` | ADR-0022 | ✅（可空列表） | 证据引用，保证可审计 |
| `previous_actions` | `list[ActionRecord]` | ADR-0011 动作历史 | ✅（可空列表） | 该访客/模式既往被派遣的动作 |
| `conflicts` | `list[ConflictFlag]` | Aggregation / ContextBuilder 标记 | ✅（可空列表） | 历史与当前的冲突（ADR-0025 §3.6） |

**硬约束（C1）**：`ReasoningInput` **不得**含 `risk_score` / `decision` / `warning` / 任何可被直接喂给 Decision 的判定字段。

> ⚠ **身份确认标记（对齐 ADR-0023）**：`VisitorProfile` 必须含 `identity_confirmed: bool` 字段；v1 临时画像该字段恒为 `False`（未确认身份），Reasoning 不得把临时画像当作真实身份画像使用。这是 C1/C5 之外的强制约束。

### 2.2 `ReasoningResult`（Reasoning Engine 输出 —— C-6 已实现）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `findings` | `tuple[str, ...]` | ✅ | 推理发现（人类可读，如"访客 X 历史到访 5 次，夜间到访占比 100%"） |
| `explanation` | `str` | ✅ | 可解释说明（继承 ADR-0024 Trust Layer） |
| `suggested_action_hint` | `str \| None` | 否 | **非绑定**建议，词汇同 `WarningEvent.recommended_action`：`MONITOR` / `NOTIFY_FAMILY` / `ESCALATE_COMMUNITY`；仅供观测 / 未来决策增强参考，**非决策** |
| `source_refs` | `tuple[SourceRef, ...]` | ✅（可空） | 每项发现回溯到 `ReasoningInput` 的具体字段（`SourceRef.source` = 字段名，`ref` = 该字段内具体 id/键，`detail` = 人类可读说明） |

**硬约束（C1，ADR-0010 单一决策中心）**：`ReasoningResult` dataclass **不存在** `risk_score` /
`score` / `decision` / `warning` / `recommended_action` 字段（与 `ReasoningInput` 同款铁律，
由 `test_reasoning.py::TestContractC1NoScore` 以字段白名单断言兜底）。`suggested_action_hint`
只是提示，最终是否采用由 ADR-0010 DecisionPolicy 决定；**C-6 阶段该 hint 未被喂回决策**
（仅经 `FrameResult.reasoning_results` Shadow 暴露）。

> `SourceRef`（`source: str` / `ref: str | None` / `detail: str | None`）是 C-6 新增的溯源
> 引用类型，供 ADR-0024 I4 可解释性 / C5 溯源：每条 finding 都能追到"它从 ReasoningInput
> 的哪个字段、哪个具体对象来"。

### 2.3 `DecisionRequest`（Decision 层输入 —— 由 DecisionPolicy 派生）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `current_risk_signal` | `RiskSignal` | 当前风险信号（ADR-0021） |
| `reasoning_result` | `ReasoningResult \| None` | Reasoning Engine 产出（可为空：推理引擎缺位时走原决策路径） |
| `reasoning_input_ref` | `SourceRef` | 指向本次 `ReasoningInput`，保证决策可溯源到历史上下文 |

**硬约束**：`DecisionRequest` **不由 Consumer / Reasoning 构造**，只由 ADR-0010 DecisionPolicy 从 `ReasoningResult` + 当前 `RiskSignal` 派生。

### 2.4 复用类型

- `EpisodicRecord` / `EvidenceRef` / `ConflictFlag`：ADR-0024 已有，直接引用。
- `CurrentEvent`：`VisitorEvent` 或 `RiskSignal`（ADR-0021）。
- `ActionRecord`：ADR-0011 ActionCommand 历史投影。

---

## 3. 组件设计

### 3.1 Retrieval（只召回）

**职责**：给定 `CurrentEvent`，从 Memory 召回排序后的相关历史原始记录。**绝不**计算异常判定、绝不聚合。

**默认策略（规则召回）**：
1. 身份键：`person_identity_id`（ADR-0023 落地后）或 `visitor_instance_id`（v1 临时画像，标注"未确认身份"）。
2. 时间窗：近 N 天（默认 30d，见 §7 O2）+ 同时间段偏好（如"同属晚 18–22 点"）。
3. 事件类型相似度：同 `VisitorEvent` 类型 / 同源 `RiskSignal` 跃迁类型。
4. 时空特征：同 `device_id` / 同门口区域。

> ⚠ **隐私边界（device_id）**：`device_id` 仅用作召回**排序键**（同设备优先），**绝不**进入 `ReasoningInput` 任何字段；若需向 Reasoning 传递空间信息，须脱敏为 `area_code`（如 `door` / `living_room`），不得携带可定位住户布局的原始 `device_id`（违反家庭隐私与 ADR-0024 §3.2 隐私约束）。

**实现落点**：直接复用 ADR-0024 的 `MemoryStore.get_episodic_by_visitor`（原始 Episode 查询基元，已合 #91 / #102），在其之上包一层 `RuleBasedRetrieval`（默认规则召回实现），不另起召回实现。`MemoryQuery.compose_context` 仅用于解释型上下文消费（人类可读 dict），**不是**召回原语——若强行用其 dict 反解 `EpisodicRecord`，会破坏 C3 确定性 / C5 溯源（ADR-0024 I4）。

**输出**：`list[EpisodicRecord]`，**确定性排序**（C3）：v1 规则召回**不使用任何 `similarity_score`**（向量分数属未来 `VectorRetrieval`，见 O1），排序键为命中强度，顺序固定为（C3 确定性）：① `risk_category_match`（risk_signal 当前事件命中含 risk_level 的记录；visitor_event 命中 risk_level 为 None 的记录——属 heuristic proxy，非语义相似度）② `same_time_band`（记录 enter hour 与当前事件 hour 环形距离 <= same_time_band_hours）③ `recency`（enter_time desc，距当前越近越前）④ `record_id asc`（最终 tiebreak，保证完全确定）。注：原 `identity_match` 排序键已移除——召回已按 `visitor_instance_id` 完成身份过滤，再排身份键为退化键。保证同输入两次召回顺序一致（回放 / 审计一致）。召回结果的数据来源见 §0.4（Memory Dataset / Episode Replay Layer）。

**复用接口签名**：
```python
class Retrieval(ABC):
    @abstractmethod
    def retrieve(self, current_event: CurrentEvent) -> list[EpisodicRecord]: ...
```

### 3.2 Aggregation（只计算）

**职责**：把 Retrieval 交付的原始记录聚合成长期模式视图。**绝不**内部再调 Retrieval；**绝不**回答"是否异常"的最终结论。

**两层产物**：
- `VisitorProfile`：`visit_count`、`visit_frequency`、`dwell_distribution`、`abnormal_time_ratio`、`repeat_pattern`、`prior_actions`。
- `RiskPattern`：`recurring_event_types`、`time_risk_signature`、`escalation_history`（**非分数**，是模式描述）。

**读侧组合（承接 ADR-0025 §3.3）**：
- 优先 join 已物化的 `SemanticAggregate`（ADR-0024 Stage G/H 若已落地）；
- 否则在请求期从召回记录做读侧组合；**窗口边界（100 条 / 30d）由 C-1 `RetrievalConfig`（`max_records` / `lookback_days`）在召回阶段施加**，本组件信任已边界化输入、**不重复裁剪**（见 Errata 分歧点 2 / C-1 review #7；窗口常量归属 `RetrievalConfig`，不写在聚合内）。

**置信度分级（取代硬性门槛，继承 ADR-0024 §3.1.3）**：
银龄盾场景下诈骗 / 踩点常"第一次出现"，硬门槛（`>=30` 且 `>=7d` 才进 `ReasoningInput`）会削弱早期发现能力。改为按样本量分级、但**始终进入** `ReasoningInput`（由 Reasoning 自行按 `confidence` 降权）：

- `cold_start`（episodes 0–5）：`confidence = low`，仅给最薄画像（如 `visit_count` 与原始 `night_visit_ratio`）；
- `weak_pattern`（5–30）：`confidence = medium`；
- `stable_pattern`（30+）：`confidence = high`。

所有档位都产出 `VisitorProfile`（可为稀疏字段），**绝不因样本少而隐藏历史**——C1 仍成立（无 score）；`confidence` 只是供 Reasoning 参考的元信息，不是判定，不得被 Consumer 翻译成分数或决策。

**临时画像（v1）**：`person_identity_id = None` 时按 `visitor_instance_id` 建临时画像并标注"未确认身份"（ADR-0024 §3.3）。

**接口签名**：
```python
class Aggregation(ABC):
    @abstractmethod
    def aggregate(self, records: list[EpisodicRecord]) -> tuple[VisitorProfile | None, RiskPattern | None]: ...
```

### 3.3 Context Builder（只组装）

**职责**：把前三步结果拼成 `ReasoningInput`。**绝不**做召回或聚合。

**组装逻辑**：
1. 取 `current_event`（触发事件）。
2. 取 Retrieval 结果 → `historical_context`。
3. 取 Aggregation 结果 → `visitor_profile` / `risk_pattern`（可能为 `None`）。
4. 取 ADR-0022 `evidence_refs`、ADR-0011 `previous_actions`。
5. 收集冲突标记 → `conflicts`（历史与当前矛盾时，见 §3.6）。
6. 校验 C1（无 score 字段）、C5（每项历史带 `source_event_ids`）。

**接口签名**：
```python
class ContextBuilder(ABC):
    @abstractmethod
    def build(self, current_event, records, profile, pattern,
              evidence_refs, previous_actions, conflicts) -> ReasoningInput: ...
```

### 3.4 Reasoning Interface（交付边界）

`ReasoningInterface` 仅指 `MemoryConsumer.provide(ReasoningInput)` 这一交付契约：**不承载推理逻辑**。

```python
# 交付边界（Consumer 侧）
def provide(self, ctx: ReasoningInput) -> ReasoningInput:
    """透传 ReasoningInput 给 Reasoning Engine；不含任何推理。"""
    return ctx

# Reasoning Engine 签名（未来组件，本方案只定义）
class ReasoningEngine(ABC):
    @abstractmethod
    def infer(self, ctx: ReasoningInput) -> ReasoningResult: ...

# ⚠ 仅签名示意：DecisionRequest 实际由 ADR-0010 DecisionPolicy 模块构造，不归 Consumer / Reasoning（见 §2.3 硬约束）。
# 此处列出只为展示链路 Context → Inference → Decision；实现期不得把 to_decision_request 放进 consumer/ 包，否则触发 ADR-0010 "唯一决策中心"边界违反。
def to_decision_request(self, signal: RiskSignal, result: ReasoningResult | None) -> DecisionRequest: ...
```

---

## 4. 编排与触发（承接 ADR-0025 §3.10）

### 4.1 触发模型：Phase 1 = 模式 B（修订：MEDIUM+ 或存在历史时触发）

**接入点**：新增独立接入点 `runtime/memory_consumer_hook.py`（`MemoryConsumerHook`），与 ADR-0024 的 `MemoryHook`（PR#94）**并列、不耦合**；两者 metrics 语义独立，Consumer 的 metrics **不得混入** MemoryHook 的 metrics（避免将来埋点 / 告警语义漂移）。触发条件（Phase 1，修订自 ADR-0025 §3.10）：

- `RiskSignal.level in {MEDIUM, HIGH}`（含 ADR-0010 落入 `ESCALATE_COMMUNITY` 等高优先动作）；**或**
- 当前 `VisitorEvent` 命中已有历史（`prior_episode_count > 0`，即"已知 / 重复访客再现"）。

> 仅 HIGH 会沦为"事后解释系统"：风险已高才查历史，只能解释"为什么高"。放宽到 MEDIUM 与"熟悉面孔再现"，Consumer 才能在"历史模式 → 提前理解 → 辅助发现风险"上发挥价值。

**位置**：`runtime/pipeline.py`，与 `MemoryHook` 并列。门控开关 `memory.consumer_enabled`（默认 `False`，遵循 ADR-0024 "默认关闭、v1 不产"的演进纪律）。

**非阻塞**：Consumer 调用**不得阻塞**实时风险主链路。参考 `MemoryHook` 的容错语义：fire-and-forget + 超时 + 异常隔离（Consumer 失败只记日志、不影响主链路）。

**模式 C（未来）**：后台周期任务（如每日凌晨）跑 Aggregation 生成 / 刷新 `SemanticAggregate`（接 ADR-0024 Stage G/H）。由独立 scheduler 触发，**不在实时链路**。

### 4.2 `MemoryConsumer.consume` 流程（伪代码）

```python
class MemoryConsumer:
    def __init__(self, retrieval, aggregation, context_builder):
        self.retrieval = retrieval
        self.aggregation = aggregation
        self.context_builder = context_builder

    def consume(self, current_event: CurrentEvent) -> ReasoningInput:
        records = self.retrieval.retrieve(current_event)          # 只召回
        profile, pattern = self.aggregation.aggregate(records)   # 只计算
        conflicts = self._collect_conflicts(current_event, records)  # 标记不解决
        ctx = self.context_builder.build(
            current_event, records, profile, pattern,
            evidence_refs=self._evidence_of(records),
            previous_actions=self._prior_actions(records),
            conflicts=conflicts,
        )                                                          # 只组装
        return ctx                                                # 交付 ReasoningInput
```

**生命周期不变量（承接 ADR-0025 §3.10）**：`consume` 仅在触发时物化 `ReasoningInput`；**无跨请求状态**（无会话、无"当前画像"缓存），保证 C2（只读）+ 无副作用 + 可复现。

### 4.3 Reasoning Engine 接入（C-6，默认关闭）

**接入点**：`runtime/memory_consumer_hook.py` 的 `MemoryConsumerHook.maybe_reason(input)`，
与 `maybe_consume` 并列。`pipeline.process_frame` 在访客循环内、`maybe_consume` 产出
`ReasoningInput` 后调用 `maybe_reason`，把结果收集进 `FrameResult.reasoning_results`。

**开关**：`memory.reasoning_enabled`（默认 `false`）——仅当 `consumer_enabled` **且**
`reasoning_enabled` 同时为真时，`from_settings` 才构造 `RuleBasedReasoningEngine` 并注入
Hook。缺 `MemoryStore` / 缺 `consumer` 时降级为关闭并记 warning（不崩、不半开）。关闭时
`maybe_reason` 立即返回 `None`，零运行时开销。

**硬边界（ADR-0010 单一决策中心 / ADR-0025 C1）**：
- `ReasoningResult` **不**被喂回 `DecisionPolicy`——本阶段只经 `FrameResult.reasoning_results`
  做 Shadow 观测（Dashboard / 审计展示"记忆如何反哺理解"），不产 Warning、不改 Risk Score。
- `RuleBasedReasoningEngine.infer` 是纯函数：只读 `ReasoningInput`、只产参考结论、不写任何
  外部状态（C2）；同输入两次产出字段级一致（C3）。
- `suggested_action_hint` 仅把已观测模式翻译成与 `DecisionPolicy` 同词汇的**非绑定**提示
  （`MONITOR` / `NOTIFY_FAMILY` / `ESCALATE_COMMUNITY`），绝不提升或设定风险等级。

**非阻塞**：`maybe_reason` 与 `maybe_consume` 同款容错语义——推理抛任何异常（含
`ReasoningError`）只计 `consumer_errors` + 日志，返回 `None`，绝不中断实时风险主链路。
指标独立记录在 Hook 的 `ConsumerMetrics`（`consumer_reasoned` 计数成功推理次数）。

> 决策增强（Context → Inference → Decision 闭环）**不在本 PR**：那是 ADR-0025 Phase 2，
> 触碰 ADR-0010 `DecisionPolicy` 的注入点（`DecisionContext.extra` 已预留）。C-6 只把
> "推理产物"暴露出来，为 Phase 2 提供可观测的 seam。

---

## 5. Slices / 里程碑（DoD）

| Slice | 内容 | DoD |
| --- | --- | --- |
| **C-0** | `contracts.py`（三数据类型）+ `interfaces.py`（四 ABC）+ `exceptions.py` | 类型可构造；ABC 不可实例化；`node --check` 不适用，ruff 通过 |
| **C-1** | `retrieval.py`（`RuleBasedRetrieval`）：基于 `MemoryQuery.compose_context` 的默认规则召回 | 召回单测：返回 `EpisodicRecord` 列表；C3 确定性（同输入两次顺序一致）；**O1 规则排序键命中强度稳定**（identity_match→event_type_match→time_distance asc） |
| **C-2** | `aggregation.py`：读侧聚合 + 置信度分级（cold_start / weak_pattern / stable_pattern） | 聚合单测：三档均产出 `VisitorProfile` 且进 `ReasoningInput`；`confidence` 随样本量升档（cold_start→weak_pattern→stable_pattern）；C1 无 score；**信任 Retrieval 已边界化输入（100 条 / 30d 由 `RetrievalConfig` 施加），不重复裁剪窗口**（越界记录已在召回阶段过滤）；混合访客输入显式抛 `AggregationError`；升级模式仅当唯一非空行为标记 >= 2 |
| **C-3** | `context.py`：组装 `ReasoningInput` | 组装单测：C1 无 score 字段、C5 每项历史带 `source_event_ids` |
| **C-4** | `orchestrator.py` + `triggers.py`（`MemoryConsumerHook`，新文件，与 `MemoryHook` 并列）：`MemoryConsumer.consume` 接入 pipeline（模式 B 门控） | ✅ 实现完成（PR#106 待合并）：集成单测覆盖三档 `RiskSignal.level`（LOW=不触发 / MEDIUM=触发 / HIGH=触发）+ 已知访客首现（`prior_episode_count>0`）即便 LOW 也触发 + `consumer_enabled=False` 不触发 + **调用次序**（consume 必须在 record 之前）+ **VisitorEvent→CurrentEvent 投影**（`risk_level`=max wins、`markers`=`behavior:` 同口径）+ 消费侧只读零泄漏 |
| **C-5** | 不变量 C1–C5 全量 + replay 风格一致性 + 跨层调用禁令测试 | ✅ 已合（PR#107）：`tests/memory/consumer/test_invariants.py` 全绿（15 例）；**跨层调用禁令**以 monkeypatch 验证 `Aggregation.aggregate` 执行期间不调 `Retrieval.retrieve`、`ContextBuilder.build` 执行期间不调 `Retrieval`/`Aggregation`、`consume` 严格各调一次；**replay 一致性**用 `tests/fixtures/memory_replay` 三 case 断言同输入同输出 + 各 case 关键 Memory 价值信号；C1 结构性字段白名单 + C2 store 快照不变/不写 + C4 只标记不解决 + C5 `source_event_ids` 透传 |
| **C-6** | Reasoning Engine 接入（`RuleBasedReasoningEngine` + `maybe_reason` + `FrameResult.reasoning_results` + `reasoning_enabled` 开关） | ✅ 实现完成（本 PR 待合并）：`reasoning.py` 落 `RuleBasedReasoningEngine`（只读、确定性、非分数、非决策）；`MemoryConsumerHook.maybe_reason` 隔离推理失败；`pipeline` 经 `FrameResult.reasoning_results` Shadow 暴露；`MemoryConfig.reasoning_enabled` 默认关闭。**决策增强（Context→Inference→Decision 闭环）刻意推后到 Phase 2**（ADR-0010 边界），`ReasoningResult` 不喂回 `DecisionPolicy`；`tests/memory/consumer/test_reasoning.py` 覆盖 C1 无 score / C2 只读 / C3 确定性 / hint 对齐 |

每个 Slice 经 `ruff check src tests` + `pytest tests/` 门禁，零回归。工程推进顺序与 M0–M5 阶段映射见 §0.5：**M0（Memory Replay Dataset）优先级最高，须先于 C-1 完成**——没有真实 Memory 数据，C-1~C-5 只是"能跑的空壳"。

---

## 6. 测试策略

| 不变量（Consumer C1–C5，与 ADR-0024 存储侧 I1–I4 **区分**；C1 ≠ I1） | 测试方法 |
| --- | --- |
| **C1 无分数变异** | 构造 `ReasoningInput`，断言不含 `risk_score` / `decision` / `warning` 字段（dataclass 字段白名单校验） |
| **C2 只读** | `consume` 前后 `MemoryStore` 写入计数 = 0（`short_term_count()` 等只读口不变） |
| **C3 确定性** | 同 Memory 状态 + 同 `current_event` 两次 `retrieve`，断言返回顺序一致 |
| **C3' 临时画像确定性**（2.2） | 同 `visitor_profile`（`person_identity_id=None`，按 `visitor_instance_id` 建临时画像）两次 `consume`，断言 `VisitorProfile` 字段一致——仅依赖检索结果，不依赖 wall-clock / 当前时间戳（防破坏 C3 确定性） |
| **C4 冲突透明** | **正向**：构造"历史低风险 vs 本次高风险" fixture，断言 `conflicts` 非空且新旧并存；**反向**：构造"历史与当前一致" fixture，断言 `conflicts == []`（防 false positive） |
| **C5 可解释** | 构造无 `source_event_ids` 的历史项，断言被拒绝或标 `missing_source` |
| **跨层调用禁令** | monkeypatch `Retrieval.retrieve`，在 `Aggregation.aggregate` 中断言其未被调用（验证单向管道的直接方法调用边界；**间接触发——回调 / 全局状态 / 共享状态——需配合 ADR-0024 I1 实现层验证**） |
| **Replay 一致性** | 用 `DESIGN-memory-replay-dataset.md` 定义的 case（case_001 重复访客 / case_002 行为升级 / case_003 冲突透明）做回放，断言同输入同输出 |
| **Baseline 区分** ⚠（4.3） | Consumer 回放 baseline = `tests/fixtures/memory_consumer_baseline.json`（消费侧 `ReasoningInput`，待 M0 生成）；**不得**复用 ADR-0024 Slice 6 的 `tests/fixtures/memory_baseline.json`（写入侧 `EpisodicRecord`）。二者概念不同，混用会导致「读 EpisodicRecord 却断言 ReasoningInput」的逻辑错乱 |

---

## 7. 仍属开放的工程决策（承接 ADR-0025 O1–O4）

| 编号 | 决策 | 本方案默认选择 + 扩展点 |
| --- | --- | --- |
| O1 | 相关性排序算法（向量 / 规则 / 混合） | **默认规则召回**（`RuleBasedRetrieval`，§3.1）；`RuleBasedRetrieval` 留 `VectorRetrieval` 扩展点，向量召回未来接入 |
| O2 | 聚合窗口（100? 200?）+ 时间窗（30d? 90d?） | 窗口边界 **100 条 / 30d 由 C-1 `RetrievalConfig`（`max_records` / `lookback_days`）在召回阶段施加**；Aggregation 信任已边界化输入、不重复裁剪（§3.2）；常量提 `consumer/config.py` 可配 |
| O3 | 序列化格式 | `pydantic.BaseModel` + `model_dump()` → JSON；契约稳定即可 |
| O4 | `person_identity_id=None` 时临时画像生命周期 | 按 `visitor_instance_id` 建临时画像并标"未确认身份"；真实身份归 ADR-0023 |

---

## 8. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 召回 / 聚合成本 | 模式 B（修订）把消费绑定到 MEDIUM+ 或已知访客再现等少量事件（§4.1），边缘 CPU 可控；非触发期零开销 |
| Reasoning Engine 缺位（Phase 1 只有 Consumer） | `ReasoningInput` 产出后可由 `MemoryConsumerHook` 记录 / 审计；`DecisionRequest.reasoning_result` 允许为 `None`，走原决策路径，不强制消费 |
| 与 ADR-0024 Memory 演进耦合 | Consumer 只读、依赖 `MemoryQuery.compose_context` 抽象；Memory 内部演进不影响 Consumer 契约 |
| 小样本误判 | 分级 `confidence` 始终进 `ReasoningInput`，由 Reasoning 按 `confidence` 降权；`cold_start` 档显式标 `low` 提示早期样本稀疏（§3.2） |

---

## 9. 后续

- **Phase 2 决策增强（Context → Inference → Decision 闭环）**：本 PR（C-6）刻意只做 Shadow
  观测，`ReasoningResult` 未喂回 `DecisionPolicy`。下一步在 ADR-0010 `DecisionContext.extra`
  注入点接入（v2 增强），需 Owner 评审决策边界，不破坏单决策中心。
- Reasoning Engine **真实**算法（规则 v2 / LLM v2 / Agent）落地时，直接替换 `ReasoningEngine`
  实现、消费 `ReasoningInput`、产出 `ReasoningResult`，归其独立 ADR（Phase 5）；C-6 的
  `RuleBasedReasoningEngine` 仅为确定性参考推理默认实现。
- 模式 C（离线 Semantic Profile 预计算）接 ADR-0024 Stage G/H。
- 冲突解决策略（覆盖 / 衰减 / 版本化）归未来 Memory Consistency Policy ADR。


## Errata（2026-08-02，C-1 实施反修）

本 DESIGN 在 C-1 落地时被真实代码约束修正如下，避免「让代码迁就 ADR 理想模型」：

1. **§0.5 / §3.1 数据源修正**：Retrieval 召回原语从 `MemoryQuery.compose_context` 改为 `MemoryStore.get_episodic_by_visitor`。
   - 原因：`compose_context()` 返回人类可读 `dict`（解释视图），不是 `EpisodicRecord` 列表；若用它反解记录会破坏 C3 确定性 / C5 溯源（ADR-0024 I4）。
   - 规则：`compose_context` 仅用于解释型上下文消费，不作为召回原语。
2. **§3.1 排序键修正**：
   - 移除退化的 `identity_match`（召回已按 `visitor_instance_id` 过滤，再排身份键无意义）；
   - `event_type_match` 更名为 `risk_category_match`，并显式标注为 heuristic proxy（risk_signal↔含 risk_level 记录 / visitor_event↔risk_level 为 None 记录），非语义相似度；
   - 最终确定性排序 = `risk_category_match` → `same_time_band` → `recency` → `record_id asc`。
3. **§3.1 device_id 处理**：v1 `EpisodicRecord` 无 `device_id` 字段，`device_id` 仅作为 `RuleBasedRetrieval` 的保留参数（no-op），绝不进入 `ReasoningInput`。不伪实现、不污染 ADR-0024 schema。
4. **O1 VectorRetrieval 定位**：VectorRetrieval 是延迟扩展点，**不是** C-1 之后的直接下一步。正确推进路径：C-1 规则召回 → C-2 聚合 → C-3 ReasoningContext → 真实 Reasoning 消费 → 评估瓶颈 → 再决定是否引入向量召回。避免技术驱动。

## Errata（2026-08-02，C-2 实施反修）

C-2 落地时被真实契约约束修正如下（避免「让代码迁就 ADR 理想模型」）：

1. **VisitorProfile 真实字段 != DESIGN §3.2 描述**：§3.2 列出 `visit_frequency` /
   `dwell_distribution` / `abnormal_time_ratio` / `repeat_pattern` / `prior_actions`，
   但 C-0 冻结契约 `VisitorProfile` 实际只有 `visitor_instance_id` / `visit_count` /
   `night_visit_ratio` / `confidence` / `identity_confirmed` / `first_seen` /
   `last_seen`。`RuleBasedAggregation` 严格按真实契约实现（扩展契约属 BREAKING，需
   Owner 评审，不在本 Slice）。
2. **窗口边界由 Retrieval 施加，Aggregation 不重复裁剪**：§3.2 描述"默认窗口 100 条 /
   30d"为聚合的有界轻量聚合；但 C-1 `RetrievalConfig`（`max_records=100` /
   `lookback_days=30`）已在召回阶段完成边界化。遵循 C-1 review #7（窗口用
   `RetrievalConfig`，不写死在聚合），`Aggregation.aggregate` 信任已边界化输入，
   `AggregationConfig` 只暴露置信度阈值 / 夜间窗 / `min_records_for_pattern`，不重复
   施加 100/30d。
3. **previous_actions / evidence_refs / conflicts 不属 Aggregation**：`Aggregation`
   接口签名仅 `(records) -> (profile, pattern)`，无 `current_event`；`conflicts`
   需要当前事件（-> C-3 ContextBuilder），`previous_actions` / `evidence_refs` 可由
   `records` 派生但属组装阶段（-> C-3）。C-2 只产出 `VisitorProfile` / `RiskPattern`。

### Errata（2026-08-02，C-3 实施反修）

C-3 落地时与 §3.3 叙述存在一处权责分歧，以**已合入的 `interfaces.py` 代码为准**（同
C-1 / C-2 的「代码权威」原则）：

1. **`conflicts` / `evidence_refs` / `previous_actions` 是 `build` 的入参，不是
   ContextBuilder 内部步骤**。已合入的 `ContextBuilder.build` 接口签名把它们列为入参；
   冲突检测与证据 / 动作派生由 **C-4 编排器**（`MemoryConsumer.consume`，见 §4.2 伪代码
   的 `self._collect_conflicts` / `_evidence_of` / `_prior_actions`）计算后传入。
   §3.3 第 4–5 步「取 evidence_refs / 收集 conflicts」是按数据流描述的消费来源，
   实现上归 C-4，C-3 `RuleBasedContextBuilder` 仅做**透传组装**（C2 只读、不检测）。
2. **C3 确定性排序由 ContextBuilder 负责**：`historical_context` 在 `build` 内按
   `(enter_time, record_id)` 确定性排序后转 `tuple`，与 Retrieval（C-1）的召回排序解耦，
   保证同输入两次产出顺序一致（审计 / 回放一致）。
3. **C5 `source_event_ids` 由 `EpisodicRecord` 契约保证**：`EpisodicRecord.__post_init__`
   强制 `source_event_ids` 非空（ADR-0024 I4）；C-3 不重复校验，仅靠单测断言透传后
   每条历史记录仍携带 `source_event_ids`。

### Errata（2026-08-02，C-4 实施反修）

C-4 落地时与 §3.4 / §4 叙述存在多处权责与命名分歧，以**已合入代码为准**（同 C-1/C-2/C-3
「代码权威」原则）：

1. **冲突检测 / 证据 / 动作派生归编排器（`orchestrator.py`），非 `ContextBuilder`**：
   §3.3 / §4.2 伪代码把 `self._collect_conflicts` / `_evidence_of` / `_prior_actions`
   列在 `MemoryConsumer.consume` 流程里，与 C-3 Errata #1 一致——`ContextBuilder.build`
   只做透传组装（C2 只读、不检测）。`RuleBasedMemoryConsumer` 在 `consume` 内先调
   Retrieval → Aggregation → 自行派生 `conflicts` / `evidence_refs` / `previous_actions`
   后，再传给 `ContextBuilder.build` 组装 `ReasoningInput`。
2. **运行时接入点文件命名为 `runtime/memory_consumer_hook.py`（非 `triggers.py`）**：
   §1 模块结构曾写 `triggers.py`，实际落地文件为 `runtime/memory_consumer_hook.py`，
   含 `MemoryConsumerHook`（与 ADR-0024 `MemoryHook` 并列、不耦合）。`MemoryConsumer`
   抽象基类仍居 `consumer/__init__.py`（继承 C-0 `interfaces.py` 的 `MemoryConsumer`
   Protocol/ABC）；`RuleBasedMemoryConsumer` 在 `consumer/orchestrator.py`。
3. **`MemoryConsumerHook` 拥有独立 `ConsumerMetrics`，不混入 `PipelineMetrics`**：
   §4.1 已声明两者 metrics 语义独立，落地为 `ConsumerMetrics`（含 `consumer_evaluated` /
   `consumer_triggered` / `consumer_errors` / `last_error` 等），挂在 hook 实例上，
   与 `MemoryHook.metrics`（`PipelineMetrics`）完全隔离，不污染 ADR-0024 埋点。
4. **触发配置为 `ConsumerTriggerConfig(enabled_levels, trigger_on_known_visitor)`**：
   §4.1 模式 B 的"MEDIUM+ 或存在历史（已知访客再现）"由 `ConsumerTriggerConfig`
   承载——`enabled_levels=("MEDIUM","HIGH")`（LOW 不触发）+ `trigger_on_known_visitor=True`
   （`prior_episode_count>0` 即便 LOW 也触发）。缺 `MemoryStore` 时降级为纯 level 判定
   （已知访客分支不可用，不误触）。配置校验：拒绝未知 level、拒绝全关（无任何触发条件）。
5. **`RiskSignal` 无 `level` 字段 → 改用 `CurrentEvent.risk_level` 做模式 B 判定**：
   ADR-0021 的 `RiskSignal` 不含 `level`（只有 `signal_id` / `visitor_instance_id` /
   `risk_category` 等）；运行期门控的"风险档位"取自 `CurrentEvent.risk_level`
   （`∈ {LOW, MEDIUM, HIGH, None}`），由 `VisitorEvent→CurrentEvent` 投影派生（见 #6）。
6. **`VisitorEvent → CurrentEvent` 投影是口径统一关键**：`VisitorEvent` 无 `risk_level`
   / `markers`（ADR-0007 事实层只记 `visitor_id` / `enter` / `leave` 等）。pipeline 在
   调用 `MemoryConsumerHook.maybe_consume` 前，用该访客当帧的 `WarningEvent` 列表投影：
   `risk_level = max(w.risk_level for w in warnings)`（同 `DefaultEpisodeBuilder._pick_max_risk`），
   `markers = [behavior: 前缀 from extract_behavior_markers(warnings)]`。这保证"当前事件"
   与"历史 `EpisodicRecord`"用同一 `behavior:` 约定口径，避免"当前 vs 历史"恒判行为突变
   （`behavior_shift` false positive）。投影逻辑落在 `PerceptionPipeline._to_current_event`
   （static method），`extract_behavior_markers` 经 `SupportsReasonSummary` Protocol 同时
   适配 `EpisodicRecord` 与 `WarningEvent`（不引入 `memory.records` 导入环）。
7. **调用次序硬约束：consume 必须在 record 之前**：`MemoryConsumerHook.maybe_consume`
   在 `process_frame` 的访客循环内、`MemoryHook.record` **之前**执行。若颠倒，当前事件会
   先被写入 episode 再被消费，导致"已知访客再现"误判（`prior_episode_count` 从 0 变 1）。
   该不变量由 `tests/runtime/test_pipeline_memory_consumer.py::TestCallOrder` 以
   "消费时刻 `get_episodic_by_visitor(vid)` 计数恒为 0"作可观测证据（次序回归会从 0→1）。
8. **`consumer_enabled` 默认 `False`，三级开关缺 `MemoryStore` 静默降级**：
   `MemoryConfig.consumer_enabled=False`（golden 默认关闭）；`from_settings` 仅当
   `settings.memory.consumer_enabled and memory_store is not None` 时才构造
   `RuleBasedMemoryConsumer` 并置 `consumer_enabled=True`；两者任一缺失则降级为关闭并
   记 warning，**不抛、不半开**。测试用 `Settings.load().memory.consumer_enabled is False`
   守住默认。

### Errata（2026-08-03，C-5 实施反修）

C-5 落地以**代码权威**为准，相对 §5/§6 叙述有一处刻意偏差需记录，避免后续读者误判为缺口：

1. **replay 一致性测试不与 M0 `expected_reasoning_input.json` 做字节级比对**：
   §6 写"用 case 做回放，断言同输入同输出"，字面可理解为与 fixture 的 `expected`
   oracle 精确相等。但该 oracle 由 M0 `ProvisionalContextAssembler`（`replay_layer.py`）
   生成，其冲突逻辑**仅 behavior_shift**（正常→异常）；而正式 `RuleBasedMemoryConsumer`
   （C-1..C-4）额外标记 `risk_escalation`（当前风险等级严格高于历史最高）。两者冲突集
   不同属**预期差异**（生产更严格），故 C-5 的 replay 测试改为断言两件事：
   (a) **同输入同输出**（同一 case 两次 `consume` 产物 `to_dict()` 逐字段相等，C3 确定性）；
   (b) **每个 case 证明其"Memory 改变了理解"的关键信号**（case_001 `visitor_profile`
   `visit_count=5`/`night_visit_ratio=1.0`；case_002 `risk_pattern.tags` 含
   `escalating_behavior`；case_003 `conflicts` 非空且含 `behavior_shift`，新旧并存）。
   若未来要用生产 consumer 生成权威 baseline，应在 `tests/fixtures/` 下另存
   `memory_consumer_baseline.json`（DESIGN §4.3 已规划但 M0 未生成），而非复用 M0 的
   provisional oracle。

2. **跨层调用禁令以 monkeypatch 直接方法调用边界落地（§6「跨层调用禁令」行）**：
   组件互不调用是架构硬边界，仅靠注释易在重构时丢失。C-5 用 `monkeypatch` 把
   `RuleBasedRetrieval.retrieve` / `RuleBasedAggregation.aggregate` 包成计数间谍，在
   `RuleBasedAggregation.aggregate` 与 `RuleBasedContextBuilder.build` 执行期间断言对方
   计数不增加 → 若有人在聚合/组装内私加一次 retrieve/aggregate，断言立即红（可变异验证）。
   同时正向控制断言 `consume` 严格按 `retrieve → aggregate → build` 各调一次。

3. **C1 提升至数据结构铁律（补 §3.9 契约层）**：
   除 `test_orchestrator.py` 的行为级 C1（产物字段白名单）外，C-5 新增契约级断言——
   `ReasoningInput` dataclass 的字段集本身不含 `risk_score`/`score`/`decision`/`warning`/
   `recommended_action`（共 7 字段固定）。即便未来有人误加字段，结构断言直接红，把
   "Consumer 不决策"从运行期产物提升为类型结构约束。

4. **C2 新增"store 写方法不被调用"断言**：除 `test_orchestrator.py` 的记录逐字段不变
   外，C-5 以 monkeypatch 把 `InMemoryStore.upsert_episodic` 替换为计数器，断言 `consume`
   全程不触发任何 store 写（只读铁律的双向覆盖：输入不变 + 无新增写入）。

### Errata（2026-08-03，C-6 实施反修）

C-6（Reasoning Engine 接入）落地以**代码权威**为准，相对 §2.2 / §3.4 / §4 叙述有一处
刻意偏差与一处范围收窄需记录，避免后续读者误判为缺口：

1. **`ReasoningResult.findings` 从 `list[Finding]` 收敛为 `tuple[str, ...]`**：§2.2 原计划
   用结构化 `Finding` 对象承载发现，但 Phase 1 规则参考推理的发现本质是"人类可读陈述 +
   一条 `SourceRef` 溯源"，用 `tuple[str]`（findings）+ `tuple[SourceRef]`（source_refs）
   同序并列即足够，无需引入 `Finding` 类（避免 BREAKING 扩展契约）。`SourceRef`
   （`source` / `ref` / `detail`）单独承担"溯源到 ReasoningInput 字段"的职责，与 findings
   一一对应（顺序一致）。若未来 LLM 推理需要更丰富结构，再在 Phase 5 演进 `Finding`，不影响
   本契约的稳定性。
2. **`suggested_action_hint` 类型从 `ActionType` 收敛为 `str`**（词汇白名单
   `RECOMMENDED_ACTION_HINTS = MONITOR / NOTIFY_FAMILY / ESCALATE_COMMUNITY`，与
   `WarningEvent.recommended_action` / `ActionCommand` 路由同源）：ADR-0025 原文写
   `ActionType | None`，但本项目并无 `ActionType` 枚举（action 词汇以 `str` 常量存在于
   `action/command.py` / `warning.py`）。为不引入重复枚举、保证与决策层词汇单一事实源，C-6
   直接用 `str` + 白名单校验。`ReasoningResult.__post_init__` 拒绝非法词汇（防污染单一决策
   中心词汇）。
3. **决策增强（Context → Inference → Decision）不在 C-6 范围**：§0.3 / §9 已明确 Phase 2
   才接。`RuleBasedReasoningEngine` 的 `infer` **绝不**提升或设定风险等级；`suggested_action_hint`
   仅把"已观测事实"用一致词汇复述。C-6 把 `ReasoningResult` 经 `FrameResult.reasoning_results`
   Shadow 暴露，**不**调用 `DecisionEngine`、不构造 `DecisionRequest`（§2.3 的 `DecisionRequest`
   仍由 ADR-0010 `DecisionPolicy` 派生，本 PR 不实现）。接入点已预留在 `DecisionContext.extra`。
4. **`maybe_reason` 复用 `maybe_consume` 的非阻塞语义与指标**：`MemoryConsumerHook` 新增
   `consumer_reasoned` 指标并扩展模块级 `CONSUMER_LOG_FIELDS`（单一事实源）；推理失败只计
   `consumer_errors` + 日志、返回 `None`，与消费失败同款隔离。测试用本地 `CONSUMER_LOG_FIELDS`
   元组须与模块常量同步（新增指标时改一处即可）。
5. **`reasoning_enabled` 是 `consumer_enabled` 的下游子开关**：`from_settings` 仅当
   `consumer_enabled` 且 `memory_store` 已就位（历史可读）时才构造 `RuleBasedReasoningEngine`；
   仅开 `reasoning_enabled` 而 `consumer_enabled=false` 会静默降级并记 warning（不抛、不半开），
   与 C-4 / C-5 的"依赖链降级"纪律一致。
