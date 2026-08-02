# DESIGN-memory-consumer.md · Memory Consumer Layer 工程落地方案

- **状态**：Draft（随 ADR-0025 Accepted 一同落库，待实现）
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

- ✅ 实现四组件（Retrieval / Aggregation / Context Builder / Reasoning Interface 交付边界）+ 编排 + 触发接入（模式 B）。
- ✅ 三数据契约（`ReasoningInput` / `ReasoningResult` / `DecisionRequest`）的字段具体化。
- ✅ 不变量 C1–C5 的测试覆盖。
- ❌ 不实现推理算法（规则 v2 / LLM v2 / Agent）——那是 Phase 5 Reasoning ADR 的事；本方案只定义 `ReasoningEngine.infer` 的**签名**。
- ❌ 不实现写侧 Semantic Aggregator（Stage G/H，ADR-0024 推迟）——Aggregation 只做读侧组合。
- ❌ 不引入 LLM。

---

## 1. 模块结构（承接 ADR-0025 §3.8）

```
src/home_perception/memory/consumer/
   __init__.py
   interfaces.py      # MemoryConsumer / Retrieval / Aggregation / ContextBuilder ABC
   contracts.py       # ReasoningInput / ReasoningResult / DecisionRequest dataclasses
   orchestrator.py    # MemoryConsumer.consume()：按序驱动 Retrieval→Aggregation→ContextBuilder
   retrieval.py       # RetrievalStrategy 默认实现（只召回）
   aggregation.py     # AggregationStrategy 默认实现（只计算）
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
| `visitor_profile` | `VisitorProfile \| None` | Aggregation 计算 | 否 | 访客长期画像；低于观测阈值时为 `None` 或标 `confidence=low` |
| `risk_pattern` | `RiskPattern \| None` | Aggregation 计算 | 否 | 风险模式（非分数） |
| `evidence_refs` | `list[EvidenceRef]` | ADR-0022 | ✅（可空列表） | 证据引用，保证可审计 |
| `previous_actions` | `list[ActionRecord]` | ADR-0011 动作历史 | ✅（可空列表） | 该访客/模式既往被派遣的动作 |
| `conflicts` | `list[ConflictFlag]` | Aggregation / ContextBuilder 标记 | ✅（可空列表） | 历史与当前的冲突（ADR-0025 §3.6） |

**硬约束（C1）**：`ReasoningInput` **不得**含 `risk_score` / `decision` / `warning` / 任何可被直接喂给 Decision 的判定字段。

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

**实现落点**：直接复用 ADR-0024 Slice C 的 `MemoryQuery.compose_context`（Product Closure，已合 #91），在其之上包一层 `RetrievalStrategy`，不另起召回实现。

**输出**：`list[EpisodicRecord]`，**确定性排序**（C3）：主排序键 `timestamp desc`，副键 `similarity_score desc`，保证同输入两次召回顺序一致（回放/审计一致）。

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
- 否则在请求期从召回记录做**有界轻量聚合**（默认窗口 100 条 / 30d）。

**最低观测阈值（继承 ADR-0024 §3.1.3）**：
- 仅当 `episodes >= 30` 且 `span_days >= 7` 时产出高置信 `VisitorProfile` / `RiskPattern`；
- 否则 `confidence = low`，且**不进入** `ReasoningInput`（避免 false pattern）；或进入但显式标 `low`，由 Reasoning 自行降权。

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

# DecisionPolicy 接入（ADR-0010，本方案只定义派生）
def to_decision_request(self, signal: RiskSignal, result: ReasoningResult | None) -> DecisionRequest: ...
```

---

## 4. 编排与触发（承接 ADR-0025 §3.10）

### 4.1 触发模型：Phase 1 = 模式 B（HIGH 触发）

**接入点**：类比 ADR-0024 的 `MemoryHook`（PR#94）。新增 `MemoryConsumerHook`（或扩展 `MemoryHook`），在 pipeline 检测到 `RiskSignal.level >= HIGH`（或 DecisionPolicy 落入 `ESCALATE_COMMUNITY` 等高优先动作）时触发 `MemoryConsumer.consume(event)`。

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
| **C-1** | `retrieval.py`：基于 `MemoryQuery.compose_context` 的默认规则召回 | 召回单测：返回 `EpisodicRecord` 列表；C3 确定性（同输入两次顺序一致） |
| **C-2** | `aggregation.py`：读侧聚合 + 最低观测阈值门控 | 聚合单测：≥30/≥7d 出高置信；低于阈值标 `low` 且不进 `ReasoningInput`；O1 排序稳定 |
| **C-3** | `context.py`：组装 `ReasoningInput` | 组装单测：C1 无 score 字段、C5 每项历史带 `source_event_ids` |
| **C-4** | `orchestrator.py` + `triggers.py`：`MemoryConsumer.consume` + `MemoryConsumerHook` 接入 pipeline（模式 B 门控） | 集成单测：HIGH 触发→`ReasoningInput` 产出；`consumer_enabled=False` 时不触发 |
| **C-5** | 不变量 C1–C5 全量 + replay 风格一致性 + 跨层调用禁令测试 | `test_invariants.py` 全绿；monkeypatch 验证 Aggregation 不调 Retrieval |

每个 Slice 经 `ruff check src tests` + `pytest tests/` 门禁，零回归。

---

## 6. 测试策略

| 不变量 | 测试方法 |
| --- | --- |
| **C1 无分数变异** | 构造 `ReasoningInput`，断言不含 `risk_score` / `decision` / `warning` 字段（dataclass 字段白名单校验） |
| **C2 只读** | `consume` 前后 `MemoryStore` 写入计数 = 0（`short_term_count()` 等只读口不变） |
| **C3 确定性** | 同 Memory 状态 + 同 `current_event` 两次 `retrieve`，断言返回顺序一致 |
| **C4 冲突透明** | 构造"历史低风险 vs 本次高风险" fixture，断言 `conflicts` 非空且新旧并存 |
| **C5 可解释** | 构造无 `source_event_ids` 的历史项，断言被拒绝或标 `missing_source` |
| **跨层调用禁令** | monkeypatch `Retrieval.retrieve`，在 `Aggregation.aggregate` 中断言其未被调用（验证单向管道） |
| **Replay 一致性** | 用 ADR-0024 风格 fixture（类比 `tests/fixtures/memory_baseline.json`）做回放，断言同输入同输出 |

---

## 7. 仍属开放的工程决策（承接 ADR-0025 O1–O4）

| 编号 | 决策 | 本方案默认选择 + 扩展点 |
| --- | --- | --- |
| O1 | 相关性排序算法（向量 / 规则 / 混合） | **默认规则召回**（§3.1）；`RetrievalStrategy` 留 `VectorRetrievalStrategy` 扩展点，向量召回未来接入 |
| O2 | 聚合窗口（100? 200?）+ 时间窗（30d? 90d?） | 默认 **100 条 / 30d**，服从最低观测阈值（§3.2）；常量提 `consumer/config.py` 可配 |
| O3 | 序列化格式 | `pydantic.BaseModel` + `model_dump()` → JSON；契约稳定即可 |
| O4 | `person_identity_id=None` 时临时画像生命周期 | 按 `visitor_instance_id` 建临时画像并标"未确认身份"；真实身份归 ADR-0023 |

---

## 8. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 召回 / 聚合成本 | 模式 B 把消费绑定到少量 HIGH 事件（§4.1），边缘 CPU 可控；非触发期零开销 |
| Reasoning Engine 缺位（Phase 1 只有 Consumer） | `ReasoningInput` 产出后可由 `MemoryConsumerHook` 记录 / 审计；`DecisionRequest.reasoning_result` 允许为 `None`，走原决策路径，不强制消费 |
| 与 ADR-0024 Memory 演进耦合 | Consumer 只读、依赖 `MemoryQuery.compose_context` 抽象；Memory 内部演进不影响 Consumer 契约 |
| 小样本误判 | 最低观测阈值（§3.2）+ `confidence=low` 不入 `ReasoningInput` |

---

## 9. 后续

- Reasoning Engine（规则 v2 / LLM v2 / Agent）落地时，直接消费 `ReasoningInput`、产出 `ReasoningResult`，归其独立 ADR（Phase 5）。
- 模式 C（离线 Semantic Profile 预计算）接 ADR-0024 Stage G/H。
- 冲突解决策略（覆盖 / 衰减 / 版本化）归未来 Memory Consistency Policy ADR。
