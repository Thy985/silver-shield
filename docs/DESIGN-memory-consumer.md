# DESIGN-memory-consumer.md · Memory Consumer Layer 工程落地方案

- **状态**：Partial（C-0/C-1/C-2 已合，C-3..C-5 待实现）
- **日期**：2026-08-02
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
- ❌ 不实现推理算法（规则 v2 / LLM v2 / Agent）——那是 Phase 5 Reasoning ADR 的事；本方案只定义 `ReasoningEngine.infer` 的**签名**。
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
   triggers.py        # MemoryConsumerHook：模式 B 触发接入（门控 + 容错）
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

### 2.2 `ReasoningResult`（Reasoning Engine 输出 —— 本方案只定义签名）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `findings` | `list[Finding]` | ✅ | 推理发现（如"该访客近 30 天夜间到访占比异常升高"） |
| `explanation` | `str` | ✅ | 可解释说明（继承 ADR-0024 Trust Layer） |
| `suggested_action_hint` | `ActionType \| None` | 否 | **非绑定**建议，仅供 Decision 参考，非决策 |
| `source_refs` | `list[SourceRef]` | ✅ | 回溯到 `ReasoningInput` 的具体字段 |

**硬约束**：`ReasoningResult` **不是分数、不是决策**；`suggested_action_hint` 只是提示，最终是否采用由 ADR-0010 DecisionPolicy 决定。

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

---

## 5. Slices / 里程碑（DoD）

| Slice | 内容 | DoD |
| --- | --- | --- |
| **C-0** | `contracts.py`（三数据类型）+ `interfaces.py`（四 ABC）+ `exceptions.py` | 类型可构造；ABC 不可实例化；`node --check` 不适用，ruff 通过 |
| **C-1** | `retrieval.py`（`RuleBasedRetrieval`）：基于 `MemoryQuery.compose_context` 的默认规则召回 | 召回单测：返回 `EpisodicRecord` 列表；C3 确定性（同输入两次顺序一致）；**O1 规则排序键命中强度稳定**（identity_match→event_type_match→time_distance asc） |
| **C-2** | `aggregation.py`：读侧聚合 + 置信度分级（cold_start / weak_pattern / stable_pattern） | 聚合单测：三档均产出 `VisitorProfile` 且进 `ReasoningInput`；`confidence` 随样本量升档（cold_start→weak_pattern→stable_pattern）；C1 无 score；**信任 Retrieval 已边界化输入（100 条 / 30d 由 `RetrievalConfig` 施加），不重复裁剪窗口**（越界记录已在召回阶段过滤）；混合访客输入显式抛 `AggregationError`；升级模式仅当唯一非空行为标记 >= 2 |
| **C-3** | `context.py`：组装 `ReasoningInput` | 组装单测：C1 无 score 字段、C5 每项历史带 `source_event_ids` |
| **C-4** | `orchestrator.py` + `triggers.py`（`MemoryConsumerHook`，新文件，与 `MemoryHook` 并列）：`MemoryConsumer.consume` 接入 pipeline（模式 B 门控） | 集成单测：**三档 `RiskSignal.level` 断言**（LOW=不触发 / MEDIUM=触发 / HIGH=触发）+ 已知访客首现（`prior_episode_count>0`）即便 LOW 也触发；`consumer_enabled=False` 时不触发 |
| **C-5** | 不变量 C1–C5 全量 + replay 风格一致性 + 跨层调用禁令测试 | `test_invariants.py` 全绿；monkeypatch 验证 Aggregation 不调 Retrieval |

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

- Reasoning Engine（规则 v2 / LLM v2 / Agent）落地时，直接消费 `ReasoningInput`、产出 `ReasoningResult`，归其独立 ADR（Phase 5）。
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
