# DESIGN-memory-pipeline: Memory Pipeline 工程落地方案

> **状态**：Proposed
> **范围**：v2 / 后 MVP · Phase 4-5（按 ADR-0024 §4 分阶段）
> **对应 ADR**：[ADR-0024](ADR/0024-memory-architecture.md)（Memory 架构）
> **上游 ADR**：[ADR-0021](ADR/0021-realtime-riskstream-concrete-design.md)（实时风险状态流）/ [ADR-0022](ADR/0022-evidence-chain-multimodal-interface.md)（证据链）/ [ADR-0023](ADR/0023-identity-continuity-system.md)（身份）
> **解决债务**：[TD-0024](TECH-DEBT.md)（RecentBehaviorStore eviction）/ [TD-0027](TECH-DEBT.md)（状态恢复）
> **日期**：2026-07-28

---

## 0. 文档职责边界

本文件是 **ADR-0024 的工程落地方案**，回答"改哪个文件、字段叫什么、Stage 怎么拆、测试怎么写"。

| 决策归属 | 文件 |
| --- | --- |
| 为什么需要 Memory、存什么、不存什么、三类记忆的边界、Memory Policy 的职责 | ADR-0024（决策） |
| 改哪个文件、存储格式、查询接口、落盘时机、字段名选定、Stage 拆分、测试矩阵 | **本文件（工程）** |
| Memory Conflict 具体策略 / Trust Layer confidence 计算 | 未来 ADR-00xx Memory Consistency Policy |

冲突时按此分工归位：架构决策回 ADR-0024，工程细节回本文件。

---

## 1. 背景与目标

### 1.1 ADR-0024 落地缺口

ADR-0024 已 Accepted，定义了三类记忆模型（Short-term / Episodic / Semantic）、Memory Policy 抽象、四项不变量（I1-I4）、Memory Lifecycle、Trust Layer。但 **当前代码库零行 Memory 实现**：

- `src/home_perception/` 下无 `memory/` 模块
- `RealTimeRiskEvaluator._active` 与 `RecentBehaviorStore._entries` 仍是 volatile（重启即空）
- `WarningEvent.evidence` 全链路从未填充（ADR-0022 未落地）
- 无任何 Episode Builder / Memory Policy / Snapshot 持久化代码

### 1.2 核心命题

本方案要回答四个工程问题：

1. **Memory Policy 怎么实现 + I1-I4 不变量怎么测？**
2. **Episode Builder 投影规则：哪些字段、关联键、何时触发？**
3. **Short-term Snapshot 持久化（解 TD-0027）：存什么、何时存、怎么恢复？**
4. **RecentBehaviorStore Eviction（解 TD-0024）+ 冷启动：怎么清、怎么恢复才不破坏语义？**

### 1.3 非目标（本方案不做）

- ❌ 不实现 Identity Semantic Memory（v1 `person_identity_id` 恒 None，ADR-0023）
- ❌ 不实现 Memory Conflict 解决策略（归未来 ADR-00xx）
- ❌ 不实现 Trust Layer confidence 衰减算法（归未来 ADR-00xx）
- ❌ 不实现 Agent Context Builder / LLM 推理接入（ADR-0024 §3.5 明确 Memory 不调 LLM）
- ❌ 不替换 `DecisionPolicy` / `ActionExecutor`（Memory 只读消费）
- ❌ 不修改 `EventType` 枚举 / `PerceptionEvent` schema（L1 冻结）
- ❌ 不实现 SQLite / 时序库后端（v1 内存 + JSON 文件足矣，后端可演进）
- ❌ 不接入生产 pipeline 写入路径（Stage F 起以 Shadow Mode 观察，不产 Warning）

---

## 2. 现状盘点

### 2.1 跨帧可变状态清单（Memory 接管目标）

| 状态对象 | 持有者 | 持有位置 | 主键 | 内容 | 持久化 |
| --- | --- | --- | --- | --- | --- |
| `RealTimeRiskEvaluator._active` | evaluator | `pipeline._realtime_evaluator` | `visitor_instance_id` | `Dict[str, _TrackRiskState]` | ❌ volatile |
| `RecentBehaviorStore._entries` | store | `pipeline._recent_behavior_store` | `visitor_instance_id` | `Dict[str, List[datetime]]` | ❌ volatile |
| `VisitorEventBuilder` track_id→UUID 映射 | event_builder | `pipeline.event_builder` | `track_id` | 内部映射 | ❌ volatile |
| `VisitorTracker` active tracks | tracker | `pipeline.tracker` | `track_id` | ByteTrack 内部态 | ❌ volatile |

> 前两个是 Memory Pipeline 直接接管对象；后两个不在本方案范围（归属 ADR-0006 / ByteTrack）。

### 2.2 已有事件对象（Memory Policy 输入）

| 对象 | 文件 | 主键 | 时间字段 | 已有 `to_dict()` |
| --- | --- | --- | --- | --- |
| `BehaviorState` | `analysis/behavior_state.py:50` | `visitor_instance_id` | `first_seen` / `last_seen` | ✅ |
| `RiskSignal` | `analysis/risk_signal.py:135` | `signal_id` (uuid4) | `created_at` | ✅ |
| `VisitorEvent` | `analysis/event.py:63` | `event_id` (uuid4) | `enter_time` / `leave_time` / `created_at` | ✅ |
| `WarningEvent` | `analysis/warning.py:95` | `warning_id` (uuid4) | `created_at` | ✅ |
| `ActionCommand` | `action/command.py:86` | `command_id` (uuid4) | `created_at` / `updated_at` | ✅ |
| `EvidenceItem` | —— | —— | —— | ❌ 未实现（ADR-0022 Proposed） |

### 2.3 已有测试簇（与本方案相关）

| 测试文件 | 覆盖范围 | 是否需要扩展 |
| --- | --- | --- |
| `tests/analysis/test_recent_behavior_store.py` | 滑窗 / 去重 / 引用隔离 / volatile | ✅ 需补 eviction 测试 |
| `tests/analysis/test_realtime_evaluator.py` | 状态机转移 / RAISED-CLEARED 配对 | ✅ 需补 snapshot 恢复后状态机一致性测试 |
| `tests/test_risksignal_contract.py` | RiskSignal 字段闭合性 | ❌ 不动 |
| `tests/contract/test_state_machine_contract.py` | 跨模块状态机契约 | ❌ 不动 |

### 2.4 已知债务（本方案解决）

| 债务 | 优先级 | 现状 | 本方案章节 |
| --- | --- | --- | --- |
| TD-0024 RecentBehaviorStore eviction | P1 | Open | §5.4 + §5.5 |
| TD-0027 Runtime state recovery | P3 | Deferred | §5.3 + §5.5 |

---

## 3. 工程边界与设计原则

### 3.1 边界（继承 ADR-0024 §3 + §7）

| 项 | 本方案职责 | 归属 |
| --- | --- | --- |
| MemoryPolicy ABC + 四项不变量校验 | ✅ 是 | §5.1 |
| Episode Builder 投影规则 + 字段名选定 | ✅ 是 | §5.2 |
| RuntimeSnapshot 持久化格式 + 写入时机 | ✅ 是 | §5.3 |
| RecentBehaviorStore `last_seen_at` + eviction | ✅ 是 | §5.4 |
| 冷启动恢复流程（Snapshot + Eviction 协同） | ✅ 是 | §5.5 |
| MemoryStore 内存后端 + JSON 序列化 | ✅ 是（v1） | §5.6 |
| SQLite / 时序库后端 | ❌ 否 | v2 工程方案 |
| Memory Conflict 解决策略 | ❌ 否 | 未来 ADR-00xx |
| Trust Layer confidence 计算 | ❌ 否 | 未来 ADR-00xx |
| Agent Context Builder | ❌ 否 | Phase 5 工程方案 |

### 3.2 设计原则

1. **实现中立**：不依赖任何类的私有字段（如 `_active`）；通过新增 public 方法 `snapshot()` / `restore()` 让评估器自己负责序列化。
2. **最小侵入**：Stage A-E 不修改 `process_frame()` 主循环；Stage F 才以 Shadow Mode 接入（默认关闭）。
3. **可演进**：字段名选定后写明"工程方案选定，未来可改"；存储后端抽象为 `MemoryStore` ABC，v1 内存 + JSON，v2 可换 SQLite。
4. **不破坏契约**：所有改动都是 MINOR（新增对象 / 新增可选字段），不触碰 L1 冻结。
5. **Shadow First**：接入 pipeline 时先 Shadow（只观察不写决策），soak 验证后再开 Write Mode。

---

## 4. 文件改动清单（Stage 划分）

### 4.1 Stage 概览

| Stage | 范围 | 修改文件 | 新增文件 | 解锁能力 | 阶段归属 |
| --- | --- | --- | --- | --- | --- |
| A | Memory Policy 接口 + Records + 不变量测试 | 0 | 6 | I1-I4 校验框架 | Phase 4 |
| B | Episode Builder 实现 + 投影测试 | 0 | 2 | VisitorEvent → EpisodicRecord 投影 | Phase 4 |
| C | Short-term Snapshot 持久化 + 数据结构改造 + 恢复测试 | 2（evaluator 加 `snapshot()`/`restore()` + `first_seen` + `confidence`；store 改 `BehaviorHistory` + `snapshot()`/`restore()`） | 4 | TD-0027 解 | Phase 4 |
| D | RecentBehaviorStore Eviction 算法 | 1（store 加 `evict_expired()`） | 1（测试扩展） | TD-0024 解 | Phase 4 |
| E | 冷启动恢复（Snapshot + Eviction + Confidence 协同） | 1（pipeline 启动钩子） | 2 | 重启后状态连续 | Phase 4 |
| F | Pipeline Shadow Mode 接入 | 1（pipeline 末尾挂钩） | 2 | 端到端 soak 验证 | Phase 4 |
| **G** | **Environment Semantic Aggregator**（按时间/地点/时段聚合，不依赖身份） | 1（aggregator 实现） | 2（聚合器 + 测试） | Environment Semantic Memory 可用 | Phase 5 前置 |
| **H** | **Identity Semantic Aggregator**（按 `person_identity_id` 聚合，依赖 ReID） | 1（aggregator 实现） | 2（聚合器 + 测试） | Identity Semantic Memory 可用 | Phase 4 ReID 后 |

> **Stage G/H 说明**：v1 不实现，但此处登记为占位，避免 Phase 5 Agent 接入时缺一整个阶段。Stage G 不依赖 `person_identity_id`，可在 Phase 5 前提前启用；Stage H 必须等 Phase 4 ReID 落地后才能启用（ADR-0023 约束）。具体设计归未来工程方案，本方案只登记范围。

### 4.2 详细文件清单

**Stage A — Memory Policy 接口与不变量**

新增：
- `src/home_perception/memory/__init__.py`
- `src/home_perception/memory/records.py` — `ShortTermRecord` / `EpisodicRecord` / `SemanticAggregate` dataclass
- `src/home_perception/memory/policy.py` — `MemoryPolicy` ABC + `InvariantValidator`
- `src/home_perception/memory/store.py` — `MemoryStore` ABC + `InMemoryStore` 实现
- `tests/memory/__init__.py`
- `tests/memory/test_policy_invariants.py` — I1-I4 不变量测试

**Stage B — Episode Builder 投影**

新增：
- `src/home_perception/memory/episode_builder.py` — `DefaultEpisodeBuilder` 实现
- `tests/memory/test_episode_builder.py` — 投影正确性测试

**Stage C — Short-term Snapshot 持久化**

修改：
- `src/home_perception/analysis/realtime_risk_evaluator.py` — 新增 public `snapshot()` / `restore()` 方法（不再依赖外部读 `_active`）+ `_TrackRiskState` 加 `first_seen` 字段
- `src/home_perception/analysis/recent_behavior_store.py` — `_entries` value 类型从 `List[datetime]` 改为 `BehaviorHistory`（含 `last_seen_at`），新增 `snapshot()` / `restore()` 方法

新增：
- `src/home_perception/memory/snapshot.py` — `RuntimeSnapshot` dataclass + `SnapshotStore`（JSON 文件原子写）
- `src/home_perception/memory/cold_start.py` — 冷启动恢复协调器（Stage E 用，C 阶段先建骨架）
- `tests/memory/test_snapshot_persistence.py`
- `tests/memory/test_evaluator_snapshot_roundtrip.py`

> **Stage C 集中做数据结构改造**：`_TrackRiskState.first_seen` 与 `BehaviorHistory.last_seen_at` 都在 Stage C 补齐，避免 Stage D 再动数据结构。Stage D 只新增 `evict_expired()` 算法。

**Stage D — RecentBehaviorStore Eviction**

修改：
- `src/home_perception/analysis/recent_behavior_store.py` — 新增 `evict_expired()` 方法（数据结构已在 Stage C 改造完）
- `tests/analysis/test_recent_behavior_store.py` — 补 eviction 测试用例

新增：
- `tests/analysis/test_recent_behavior_eviction.py` — 单独的 eviction soak 模拟测试

**Stage E — 冷启动恢复**

修改：
- `src/home_perception/runtime/pipeline.py` — 启动钩子调用 `ColdStartCoordinator`
- `src/home_perception/memory/cold_start.py` — 完成 `recover()` 实现

新增：
- `tests/memory/test_cold_start_recovery.py` — 冷启动恢复端到端测试

**Stage F — Pipeline Shadow Mode 接入**

修改：
- `src/home_perception/runtime/pipeline.py` — `process_frame()` 末尾挂钩 Memory Policy（gated by `memory_enabled` flag）
- `config/default.yaml` — 新增 `memory:` 配置块
- `src/home_perception/core/config.py` — 新增 `MemoryConfig` 子模型

新增：
- `tests/runtime/test_pipeline_memory_shadow.py` — Shadow Mode 集成测试

**Stage G — Environment Semantic Aggregator（占位，v1 不实现）**

新增（未来工程方案）：
- `src/home_perception/memory/semantic_environment_builder.py` — Environment 维度聚合（时间/地点/时段，不依赖身份）
- `tests/memory/test_semantic_environment_builder.py` — 聚合正确性 + 最低观测阈值（ADR-0024 §3.1.3.1）

**Stage H — Identity Semantic Aggregator（占位，v1 不实现，依赖 Phase 4 ReID）**

新增（未来工程方案）：
- `src/home_perception/memory/semantic_identity_builder.py` — 按 `person_identity_id` 聚合
- `tests/memory/test_semantic_identity_builder.py` — 聚合正确性 + 身份未确认时拒绝聚合

---

## 5. 详细设计

### 5.1 Memory Policy 接口与不变量（Stage A）

#### 5.1.1 Records dataclass（`memory/records.py`）

工程方案选定字段名（ADR-0024 §3.2.1 不固定字段名，本方案选定如下，未来可演进）：

```python
class MemoryStatus(str, Enum):
    """Memory Record 生命周期状态（§5.7 Memory Validity Version）。

    区别于 schema_version（数据结构版本）和 model_version（生成模型版本），
    memory_status 表示"这条记忆当前是否可被消费"。
    """
    ACTIVE = "active"           # 可被 Agent / 聚合消费
    DEPRECATED = "deprecated"   # 因模型升级/规则修正而降级；保留历史证据但不参与新决策
    ARCHIVED = "archived"       # 归档；只读，不参与任何消费
    INVALID = "invalid"         # 标记为无效（如发现误判）；保留可追溯但不消费


@dataclass
class ShortTermRecord:
    """Short-term Memory 记录（工作记忆）。"""
    record_id: str                       # = f"st-{visitor_instance_id}"，幂等键
    visitor_instance_id: str             # v1 主键
    phase: str                           # "none" | "active_risk"（来自 RiskPhase.value）
    raised_signal_id: Optional[str]      # ACTIVE_RISK 时必有
    raised_at: Optional[datetime]
    first_seen: datetime                 # 来自 BehaviorState.first_seen
    last_seen_at: datetime               # 上次见到时刻
    source_event_ids: List[str]          # [signal_id, ...]
    memory_status: MemoryStatus = MemoryStatus.ACTIVE  # §5.7
    schema_version: int = 1
    created_at: datetime = field(default_factory=now_dt)


@dataclass
class EpisodicRecord:
    """Episodic Memory 记录（事件记忆）。

    幂等键：record_id = f"ep-{visitor_event.event_id}"
    同一 VisitorEvent 多次投影只产生一条记录（I1）。
    """
    record_id: str                       # = f"ep-{visitor_event.event_id}"
    visitor_instance_id: str             # v1 主键
    person_identity_id: Optional[str]    # v1 恒 None（ADR-0023）
    enter_time: datetime
    leave_time: datetime
    duration_seconds: float
    risk_level: Optional[str]            # 来自 WarningEvent.risk_level；无 Warning 则 None
    recommended_action: Optional[str]
    reason_summary: List[str]
    actions: List[ActionSummary]         # ActionCommand 投影
    evidence_refs: List[EvidenceRef]     # ADR-0022 落地前为空 list
    source_event_ids: List[str]          # [visitor_event_id, warning_id, command_id, ...]
    summary: str                         # human-interpretable summary（ADR-0024 §3.2.1 强制）
    model_version: str                   # Episode Builder 版本，如 "ep-builder-v1"
    memory_status: MemoryStatus = MemoryStatus.ACTIVE  # §5.7
    corrections: List[Dict[str, Any]] = field(default_factory=list)  # I2 例外（§5.6.2）
    schema_version: int = 1
    created_at: datetime = field(default_factory=now_dt)


@dataclass
class ActionSummary:
    """ActionCommand 的 Memory 投影（不存 payload 细节）。"""
    command_type: str
    command_id: str
    status: str
    error: Optional[str]


@dataclass
class SemanticAggregate:
    """Semantic Memory 聚合记录（v1 仅 Environment 维度）。

    v1 不实现聚合逻辑；dataclass 先定义供 Stage A 测试 schema 闭合性。
    """
    aggregate_id: str                    # = f"sem-env-{dimension}-{period_key}"
    dimension: str                       # "environment"（v1 仅此一种）
    period_key: str                      # 如 "2026-07" / "2026-W30"
    episode_count: int
    statistics: Dict[str, Any]           # 时段分布 / 风险等级分布等
    confidence: float                    # [0, 1]；低于阈值不供 Agent 消费（ADR-0024 §3.1.3.1）
    source_episode_ids: List[str]        # 聚合源 Episode 列表（可追溯）
    model_version: str
    memory_status: MemoryStatus = MemoryStatus.ACTIVE  # §5.7
    schema_version: int = 1
    created_at: datetime = field(default_factory=now_dt)
```

#### 5.1.2 MemoryPolicy ABC（`memory/policy.py`）

```python
class MemoryPolicy(ABC):
    """Memory Policy 抽象 —— ADR-0024 §3.2 transformation boundary。

    不参与风险判定 / 行动决策 / LLM 推理。
    只做 ObservationStream → MemoryRecord 的确定性投影。
    """

    @abstractmethod
    def transform_short_term(
        self,
        state_snapshot: BehaviorState,
        transition: Optional[RiskSignal],
    ) -> Optional[ShortTermRecord]:
        """Short-term Memory 写入。

        触发时机：
        - 状态转移（transition 非 None）
        - 周期快照（transition None，state_snapshot 非 None）
        - 访客离场（由上层调用 transform_episode，本方法不处理）
        """

    @abstractmethod
    def project_episode(
        self,
        visitor_event: VisitorEvent,
        warnings: List[WarningEvent],
        actions: List[ActionCommand],
    ) -> Optional[EpisodicRecord]:
        """Episodic Memory 投影。

        触发时机：VisitorEvent 生成（访客离场）。
        幂等键：record_id = f"ep-{visitor_event.event_id}"。
        """

    @abstractmethod
    def aggregate_semantic(
        self,
        episodes: List[EpisodicRecord],
        dimension: str,
        period_key: str,
    ) -> Optional[SemanticAggregate]:
        """Semantic Memory 聚合（v1 仅 environment 维度）。

        v1 Stage A 不实现具体逻辑，返回 None。
        """
```

#### 5.1.3 InvariantValidator（`memory/policy.py`）

```python
class InvariantValidator:
    """ADR-0024 §3.2.3 I1-I4 不变量校验器。

    所有 MemoryPolicy 实现的产出必须经此校验。
    校验失败抛 InvariantViolationError，不入库。
    """

    def validate_short_term(self, record: ShortTermRecord) -> None:
        self._check_i1_idempotent_key(record.record_id, record.source_event_ids)
        self._check_i3_causality(record.created_at, record.source_event_ids)
        self._check_i4_explainability(record.source_event_ids)

    def validate_episodic(self, record: EpisodicRecord) -> None:
        self._check_i1_idempotent_key(record.record_id, record.source_event_ids)
        self._check_i3_causality(record.created_at, record.source_event_ids)
        self._check_i4_explainability(record.source_event_ids)
        # I2 Monotonicity 在 store.upsert() 内部检查（见 §5.6）

    def _check_i1_idempotent_key(self, record_id: str, source_event_ids: List[str]) -> None:
        """I1: record_id 必须从 source event 派生，不能用自增/随机。"""
        if not record_id.startswith(("st-", "ep-", "sem-")):
            raise InvariantViolationError(f"I1: record_id 前缀非法: {record_id}")
        if not source_event_ids:
            raise InvariantViolationError("I1: source_event_ids 不能为空")

    def _check_i3_causality(self, record_ts: datetime, source_event_ids: List[str]) -> None:
        """I3: record.created_at >= source event.timestamp。

        Stage A 实现简化版：source_event_ids 是 id 不是对象，
        完整因果校验需 InvariantValidator 持有 event_id → timestamp 索引（Stage F 接入）。
        Stage A 只校验 record_ts 非 None。
        """
        if record_ts is None:
            raise InvariantViolationError("I3: created_at 不能为 None")

    def _check_i4_explainability(self, source_event_ids: List[str]) -> None:
        """I4: EpisodicRecord 必须引用 source evidence（至少一个 source event id）。"""
        if not source_event_ids:
            raise InvariantViolationError("I4: 缺少 source_event_ids，记忆不可追溯")
```

> **I2 Monotonicity 实现位置**：不在 validator，而在 `MemoryStore.upsert_episodic()` 内部 —— 写入前查询 `record_id` 是否已存在；存在则拒绝覆写字段，只允许追加 `correction` 字段（见 §5.6.2）。

#### 5.1.4 InMemoryStore（`memory/store.py`）

```python
class MemoryStore(ABC):
    """Memory Object 存储后端抽象。"""

    @abstractmethod
    def upsert_short_term(self, record: ShortTermRecord) -> bool:
        """返回 True=新增，False=幂等命中（同 record_id 已存在，跳过）。"""

    @abstractmethod
    def upsert_episodic(self, record: EpisodicRecord) -> bool:
        """I2 Monotonicity：同 record_id 已存在时拒绝覆写非 correction 字段。"""

    @abstractmethod
    def get_episodic_by_visitor(self, visitor_instance_id: str) -> List[EpisodicRecord]:
        ...

    @abstractmethod
    def get_active_episodic(self) -> List[EpisodicRecord]:
        """只返回 memory_status=ACTIVE 的 Episode（§5.7.3 消费规则）。"""
        ...

    @abstractmethod
    def snapshot(self) -> Dict[str, Any]:
        """导出全部 Memory 内容（用于测试 / 诊断）。"""


class InMemoryStore(MemoryStore):
    """v1 内存后端：进程内 dict，重启即空。

    ⚠️ v1 持久化不对称警告（Phase 5 前必须迁移）：
    ─────────────────────────────────────
    - Short-term：JSON Snapshot 持久化（§5.3），重启可恢复
    - Episodic：内存 only，重启即丢
    - Semantic：内存 only，重启即丢

    这意味着 v1 阶段：
    - 重启后 ACTIVE_RISK 状态可恢复（Short-term Snapshot）
    - 重启后历史访问档案丢失（Episodic Memory）
    - Agent 无法回答"过去发生过什么"

    Phase 5 Agent 接入前，必须将 Episodic / Semantic 迁移到 SQLite（或等价持久化后端）。
    迁移点见 §5.1.5 MemoryStore 演进路径。
    """

    def __init__(self) -> None:
        self._short_term: Dict[str, ShortTermRecord] = {}
        self._episodic: Dict[str, EpisodicRecord] = {}

    def upsert_short_term(self, record: ShortTermRecord) -> bool:
        # 幂等：同 record_id 直接覆盖（Short-term 是当前态，可覆盖）
        is_new = record.record_id not in self._short_term
        self._short_term[record.record_id] = record
        return is_new

    def upsert_episodic(self, record: EpisodicRecord) -> bool:
        # I2 Monotonicity：已存在则拒绝覆写
        if record.record_id in self._episodic:
            existing = self._episodic[record.record_id]
            if self._fields_differ(existing, record):
                raise InvariantViolationError(
                    f"I2: EpisodicRecord {record.record_id} 已存在，禁止覆写"
                )
            return False  # 幂等命中
        self._episodic[record.record_id] = record
        return True

    def get_active_episodic(self) -> List[EpisodicRecord]:
        return [ep for ep in self._episodic.values()
                if ep.memory_status == MemoryStatus.ACTIVE]
```

#### 5.1.5 MemoryStore 演进路径（v1 → v2）

| 版本 | Short-term | Episodic | Semantic | 触发条件 |
| --- | --- | --- | --- | --- |
| v1（本方案 Stage A-F） | 内存 + JSON Snapshot | 内存 only | 内存 only | Phase 4 工程落地 |
| v2（未来工程方案） | 内存 + JSON Snapshot | **SQLite** | **SQLite** | Phase 5 Agent 接入前必须迁移 |
| v3+ | 内存 + JSON Snapshot | SQLite + 时序索引 | SQLite + 列存索引 | 长期归档 + 大规模查询 |

> **v2 SQLite 迁移的硬约束**：
> 1. Episodic Memory 是 Agent 回答"过去发生过什么"的唯一数据源；不迁移则 Agent 无历史可用
> 2. 迁移时 `MemoryStore` ABC 不变，只替换实现类（`InMemoryStore` → `SQLiteStore`）
> 3. 迁移测试必须包含 Replay Test（§6.7）：相同事件流在 v1/v2 后端产出相同 MemoryRecord

### 5.2 Episode Builder 投影规则（Stage B）

#### 5.2.1 投影流程

```
VisitorEvent (离场生成)        WarningEvent[] (该访客触发的)        ActionCommand[] (该访客触发的)
        │                              │                                    │
        └──────────────────┬───────────┴────────────────────────────────────┘
                           ▼
                   DefaultEpisodeBuilder.project_episode()
                           │
                           │  1. 关联键：visitor_instance_id（v1）
                           │  2. 时间窗：WarningEvent.created_at ∈ [enter_time, leave_time + tolerance]
                           │  3. ActionCommand.warning_id ∈ WarningEvent.warning_id 集合
                           │  4. 生成 human-interpretable summary
                           │  5. record_id = f"ep-{visitor_event.event_id}"（I1 幂等键）
                           ▼
                     EpisodicRecord
```

#### 5.2.2 字段映射表

| EpisodicRecord 字段 | 来源 | 转换规则 |
| --- | --- | --- |
| `record_id` | `VisitorEvent.event_id` | `f"ep-{event_id}"` |
| `visitor_instance_id` | `VisitorEvent.visitor_id` | 直接（v1 主键） |
| `person_identity_id` | —— | 恒 `None`（ADR-0023） |
| `enter_time` / `leave_time` / `duration_seconds` | `VisitorEvent` | 直接 |
| `risk_level` | `WarningEvent.risk_level` | 多个 Warning 取 max（HIGH > MEDIUM > LOW）；无 Warning 则 None |
| `recommended_action` | `WarningEvent.recommended_action` | 取 risk_level 最高那条的 action |
| `reason_summary` | `WarningEvent.reason_summary` | 合并去重 |
| `actions` | `ActionCommand[]` | 投影为 `ActionSummary` 列表（不存 payload） |
| `evidence_refs` | `WarningEvent.evidence` | v1 空列表（ADR-0022 未落地）；v2 接 EvidenceItem |
| `source_event_ids` | 三个事件 | `[visitor_event_id] + [warning_id, ...] + [command_id, ...]` |
| `summary` | 派生 | 见 §5.2.3 生成规则 |
| `model_version` | 常量 | `"ep-builder-v1"` |

#### 5.2.3 human-interpretable summary 生成规则

ADR-0024 §3.2.1 强制 Memory Object 必须含 human-interpretable summary。本方案选定字段名 `summary`（不固定，未来可改为 `narrative` / `explanation`）。

生成模板（确定性，不调 LLM）：

```
{enter_time_local HH:MM}-{leave_time_local HH:MM} 访问（停留 {duration_minutes} 分钟）
{risk_phrase}{action_phrase}
```

示例：
- 无风险：`"14:32-14:35 访问（停留 3 分钟），未触发风险。"`
- 有风险：`"18:32-18:44 访问（停留 12 分钟），风险等级 HIGH，已通知家属。"`
- 多风险：`"22:10-22:25 访问（停留 15 分钟），风险等级 HIGH（异常停留 / 高风险接近），已通知家属 + 升级社区。"`

> **工程约束**：summary 生成是纯函数，不依赖外部状态；同一 Episode 输入永远产出同一 summary（I1 幂等性）。

#### 5.2.4 关联规则

| 关联 | 主键 | 时间窗 |
| --- | --- | --- |
| VisitorEvent ↔ WarningEvent | `visitor_instance_id`（隐含：WarningEvent.trigger_events[].visitor_id） | `WarningEvent.created_at ∈ [enter_time, leave_time + 60s]` |
| WarningEvent ↔ ActionCommand | `warning_id` | `ActionCommand.warning_id == WarningEvent.warning_id` |

> **容差 60s**：WarningEvent 可能在访客离场后短暂延迟生成（pipeline 处理延迟）；超过 60s 不关联，避免串号。

#### 5.2.5 DefaultEpisodeBuilder 实现骨架

```python
class DefaultEpisodeBuilder(MemoryPolicy):
    """v1 Episode Builder 实现。"""

    MODEL_VERSION = "ep-builder-v1"
    ACTION_TOLERANCE_SECONDS = 60.0

    def project_episode(
        self,
        visitor_event: VisitorEvent,
        warnings: List[WarningEvent],
        actions: List[ActionCommand],
    ) -> Optional[EpisodicRecord]:
        # 1. 关联 WarningEvent（按 visitor_instance_id + 时间窗）
        related_warnings = self._filter_warnings(visitor_event, warnings)
        # 2. 关联 ActionCommand（按 warning_id）
        related_actions = self._filter_actions(related_warnings, actions)
        # 3. 聚合 risk_level（max wins，ADR-0010）
        risk_level, action = self._pick_max_risk(related_warnings)
        # 4. 生成 summary
        summary = self._build_summary(visitor_event, risk_level, action, related_actions)
        # 5. 构造 record
        return EpisodicRecord(
            record_id=f"ep-{visitor_event.event_id}",
            visitor_instance_id=str(visitor_event.visitor_id),
            person_identity_id=None,  # v1 恒 None
            enter_time=visitor_event.enter_time,
            leave_time=visitor_event.leave_time,
            duration_seconds=visitor_event.duration_seconds,
            risk_level=risk_level,
            recommended_action=action,
            reason_summary=self._merge_reasons(related_warnings),
            actions=[self._to_action_summary(c) for c in related_actions],
            evidence_refs=[],  # v1 空，ADR-0022 落地后接
            source_event_ids=self._collect_source_ids(visitor_event, related_warnings, related_actions),
            summary=summary,
            model_version=self.MODEL_VERSION,
        )
```

### 5.3 Short-term Snapshot 持久化（Stage C，解 TD-0027）

#### 5.3.1 Snapshot 内容（reconstructable only）

ADR-0024 §3.7 原则：**Snapshot stores reconstructable state, not derived metrics**。

| 字段 | 是否持久化 | 理由 |
| --- | --- | --- |
| `visitor_instance_id` | ✅ | 主键，无法重算 |
| `phase` (RiskPhase) | ✅ | 状态机态，无法重算 |
| `raised_signal_id` | ✅ | ACTIVE_RISK 时必填，用于 CLEARED 回填 |
| `raised_at` | ✅ | RAISED 时刻，无法重算 |
| `first_seen` | ✅ | 访问起点，无法重算 |
| `last_seen_at` | ✅ | 上次见到时刻，用于离场判定 |
| `dwell_seconds` | ❌ | `=(now - first_seen).total_seconds()` 可重算 |
| `is_odd_hour` | ❌ | `=f(now)` 可重算 |
| `track_id` | ❌ | ByteTrack 内部态，重启后无意义 |
| `proximity_score` | ❌ | 恒 0.0（Stage B 占位） |

#### 5.3.2 Snapshot dataclass（`memory/snapshot.py`）

```python
@dataclass
class ActiveTrackSnapshot:
    """单主体风险状态机快照（reconstructable only）。"""
    visitor_instance_id: str
    phase: str                           # RiskPhase.value
    raised_signal_id: Optional[str]
    raised_at: Optional[datetime]
    first_seen: datetime
    last_seen_at: datetime


@dataclass
class RecentBehaviorSnapshot:
    """RecentBehaviorStore 单 visitor 快照。"""
    visitor_instance_id: str
    enter_times: List[datetime]          # 窗口内进入时刻列表
    last_seen_at: datetime


@dataclass
class RuntimeSnapshot:
    """运行时整体快照（写入 JSON 文件）。"""
    snapshot_id: str                     # uuid4，每次写入新生成
    snapshot_at: datetime                # 写入时刻（UTC）
    schema_version: int = 1
    active_tracks: List[ActiveTrackSnapshot] = field(default_factory=list)
    recent_behavior: List[RecentBehaviorSnapshot] = field(default_factory=list)
    # 不含 BehaviorState derived 字段，重启后由 BehaviorBuilder 重算
```

#### 5.3.3 RealTimeRiskEvaluator 公开方法（修改 `realtime_risk_evaluator.py`）

```python
class RealTimeRiskEvaluator:
    # ... 既有代码不变 ...

    def snapshot(self, now: datetime) -> List[ActiveTrackSnapshot]:
        """导出当前 _active 状态为可持久化快照（reconstructable only）。

        公开方法，不暴露 _active 私有字段。
        """
        return [
            ActiveTrackSnapshot(
                visitor_instance_id=vid,
                phase=state.phase.value,
                raised_signal_id=state.raised_signal_id,
                raised_at=state.raised_at,
                first_seen=state.first_seen,  # 需 _TrackRiskState 增加 first_seen 字段
                last_seen_at=now,
            )
            for vid, state in self._active.items()
        ]

    def restore(self, snapshots: List[ActiveTrackSnapshot]) -> None:
        """从快照恢复 _active 状态。

        语义：清空当前 _active，按 snapshots 重建。
        用于进程重启后的状态恢复（TD-0027）。
        """
        self._active.clear()
        for snap in snapshots:
            self._active[snap.visitor_instance_id] = _TrackRiskState(
                phase=RiskPhase(snap.phase),
                raised_signal_id=snap.raised_signal_id,
                raised_at=snap.raised_at,
                first_seen=snap.first_seen,  # 新增字段
                last_track_id=None,         # track_id 不持久化，重启后未知
            )
```

> **`_TrackRiskState` 字段补充**：当前 dataclass 缺 `first_seen` 字段，需在 Stage C 补上。这是对私有 dataclass 的扩展，不破坏 L1/L2 契约。

#### 5.3.4 RecentBehaviorStore 公开方法（修改 `recent_behavior_store.py`）

> **数据结构改造同步在 Stage C 完成**：`_entries` value 类型从 `List[datetime]` 改为 `BehaviorHistory`（含 `last_seen_at`）。Stage D 的 `evict_expired()` 算法基于此结构，不再单独改数据结构。

```python
class RecentBehaviorStore:
    # ... 既有 update() 逻辑改造为操作 BehaviorHistory（见 §5.4.2）...

    def snapshot(self) -> List[RecentBehaviorSnapshot]:
        """导出当前 _entries 为可持久化快照。"""
        return [
            RecentBehaviorSnapshot(
                visitor_instance_id=vid,
                enter_times=list(history.enter_times),
                last_seen_at=history.last_seen_at,
            )
            for vid, history in self._entries.items()
        ]

    def restore(self, snapshots: List[RecentBehaviorSnapshot], now: datetime) -> None:
        """从快照恢复 _entries。

        冷启动恢复时只恢复 active visitor（last_seen_at 在窗口内），
        避免 TD-0024 旧条目累积问题重现。
        """
        self._entries.clear()
        for snap in snapshots:
            # 冷启动过滤：只恢复近期见过的 visitor
            # 具体窗口策略见 §5.5
            self._entries[snap.visitor_instance_id] = BehaviorHistory(
                enter_times=list(snap.enter_times),
                last_seen_at=snap.last_seen_at,
            )
```

#### 5.3.5 SnapshotStore（`memory/snapshot.py`）

```python
class SnapshotStore:
    """JSON 文件原子写的 Snapshot 持久化后端。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._tmp_path = path.with_suffix(".tmp")

    def save(self, snapshot: RuntimeSnapshot) -> None:
        """原子写：先写 .tmp，再 rename。防 crash 时半写。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_at": snapshot.snapshot_at.isoformat(),
            "schema_version": snapshot.schema_version,
            "active_tracks": [asdict(s) for s in snapshot.active_tracks],
            "recent_behavior": [self._serialize_recent(s) for s in snapshot.recent_behavior],
        }
        with open(self._tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(self._tmp_path, self._path)  # Windows/Linux 均原子

    def load(self) -> Optional[RuntimeSnapshot]:
        """读 snapshot；不存在 / 解析失败返回 None（视为冷启动）。"""
        if not self._path.exists():
            return None
        try:
            with open(self._path, encoding="utf-8") as f:
                payload = json.load(f)
            return self._deserialize(payload)
        except (json.JSONDecodeError, KeyError, ValueError):
            # 损坏的 snapshot 视为冷启动，不阻塞启动
            return None
```

#### 5.3.6 写入时机

| 时机 | 触发 | 频率 |
| --- | --- | --- |
| 周期快照 | pipeline 定时（默认 30s） | 30s ± 5s |
| 事件触发 | RAISED / CLEARED / 访客离场 | immediate |
| 优雅退出 | SIGTERM / atexit 钩子 | 一次最终 flush |

> **不写高频快照**：每帧写会拖垮边缘 CPU；30s 周期 + 事件触发已足够（最坏丢失 30s 状态，可接受）。

### 5.4 RecentBehaviorStore Eviction（Stage D，解 TD-0024）

#### 5.4.1 数据结构（已在 Stage C 完成，此处为引用）

```python
@dataclass
class BehaviorHistory:
    """单 visitor 的近期行为账本（替换原 `List[datetime]`）。"""
    enter_times: List[datetime]          # 滑窗内进入时刻
    last_seen_at: datetime               # 上次见到该 visitor 的时刻（新增）

class RecentBehaviorStore:
    def __init__(self) -> None:
        self._entries: Dict[str, BehaviorHistory] = {}  # value 类型从 List[datetime] 改为 BehaviorHistory
```

> **数据结构归属**：`BehaviorHistory` 与 `last_seen_at` 字段在 Stage C 已引入（§5.3.4），此处仅作引用。Stage D 只新增 `evict_expired()` 算法。
>
> **向后兼容性**：`_entries` 是私有字段，外部只通过 `update()` / `snapshot()` 访问；类型变更不破坏 L2 接口。

#### 5.4.2 update() 改造（已在 Stage C 完成）

```python
def update(self, visitor_instance_id, enter_time, now, window_seconds):
    # ... 既有校验不变 ...

    history = self._entries.get(visitor_instance_id)
    if history is None:
        history = BehaviorHistory(enter_times=[], last_seen_at=now)
        self._entries[visitor_instance_id] = history

    # 去重 + 追加
    if enter_time not in history.enter_times:
        history.enter_times.append(enter_time)
    # 更新 last_seen_at（每次 update 都刷新）
    history.last_seen_at = now

    # 滑窗清理（既有逻辑）
    cutoff = now - timedelta(seconds=window_seconds)
    in_window = [t for t in history.enter_times if t >= cutoff]
    if in_window:
        history.enter_times = in_window
    else:
        self._entries.pop(visitor_instance_id, None)

    return types.MappingProxyType({"visits_in_window": len(in_window)})
```

#### 5.4.3 evict_expired() 方法（新增）

```python
def evict_expired(self, now: datetime, retention_seconds: float) -> int:
    """清理超过 retention 未再出现的 visitor 条目。

    与滑窗语义不同：
    - 滑窗（window_seconds）：控制"访问次数"统计窗口，如最近 1h 内访问几次
    - retention（retention_seconds）：控制"该 visitor 整体保留多久"，如离场后保留 1h

    返回被清理的条目数。
    """
    if retention_seconds < 0:
        raise ValueError(f"retention_seconds 必须 >= 0，收到 {retention_seconds}")
    cutoff = now - timedelta(seconds=retention_seconds)
    expired = [vid for vid, h in self._entries.items() if h.last_seen_at < cutoff]
    for vid in expired:
        self._entries.pop(vid, None)
    return len(expired)
```

#### 5.4.4 调用时机

| 调用方 | 时机 | 频率 |
| --- | --- | --- |
| `pipeline.process_frame()` | 每帧末尾 | 每 N 帧（默认 60 帧 ≈ 7.5s @ 8fps） |
| `ColdStartCoordinator.recover()` | 启动恢复后 | 一次（清理恢复时引入的过期条目） |

> **不解耦到独立后台线程**：边缘 CPU 多线程会增加 GIL 争用；每 60 帧内联调用 `evict_expired()` 单次复杂度 O(N)，N 受 retention 上限约束（典型 < 100），延迟 < 1ms。

#### 5.4.5 配置（`MemoryConfig`）

```python
@dataclass
class MemoryConfig:
    enabled: bool = False                # Shadow Mode 开关（Stage F）
    snapshot_path: str = "data/memory/snapshot.json"
    snapshot_interval_seconds: float = 30.0
    snapshot_fresh_threshold_seconds: float = 30.0   # FRESH/STALE 分界（§5.5.0）
    snapshot_ttl_seconds: float = 300.0   # STALE/DISCARD 分界（超过则冷启动）
    recent_behavior_retention_seconds: float = 3600.0  # 1h
    eviction_interval_frames: int = 60
    cold_start_stale_confidence: float = 0.5  # STALE 档 confidence 值
```

### 5.5 冷启动恢复（Stage E）

#### 5.5.0 Cold Start Policy（核心：Confidence 分档）

冷启动恢复不能只判断"恢复 or 不恢复"，必须区分**恢复后的可信度**。原因：

```
场景：设备断电
  18:30 visitor A 进入 ACTIVE_RISK
  18:32 断电（snapshot 最后写入 18:30:00）
  18:35 重启
```

恢复时无法判断：A 是否仍在画面中？风险是否仍存在？只能等待新帧重新检测。但 `_active` 状态是否恢复，影响下游：

- **完全恢复（high confidence）**：评估器认为 A 仍 ACTIVE_RISK，新 CLEARED 可回填原 `raised_signal_id`
- **降级恢复（low confidence）**：评估器恢复状态但标注"待重新确认"，下游 Agent 不据此发警报
- **丢弃（discard）**：评估器从零开始，下次检测到 A 时视作新访客

**Cold Start Confidence 三档策略**：

| 档位 | snapshot_age | 行为 | confidence 字段 | 适用场景 |
| --- | --- | --- | --- | --- |
| **FRESH** | `< 30s` | 完全恢复 ACTIVE_RISK 状态；下游可继续基于此决策 | `1.0` | 短暂断电 / 进程崩溃重启 |
| **STALE** | `30s ~ 5min`（含 snapshot_ttl） | 恢复状态但降级 confidence；下游 Agent 据此发"待确认"提示而非警报 | `0.5` | 较长停机；状态可能已变 |
| **DISCARD** | `> 5min` 或 snapshot 缺失/损坏 | 冷启动；评估器 `reset()`；下次检测视作新访客 | `0.0` | 长时间停机；状态不可信 |

> **阈值依据**：
> - `30s`：等于一个完整评估周期（`eval_interval_frames=60` × 8fps ≈ 7.5s）的 4 倍，覆盖短暂 GC pause / 进程崩溃重启场景
> - `5min`：与 Short-term Memory 生命周期对齐（ADR-0024 §3.1.1 "访客离场后短期保留 5 分钟"），超过 5min 状态本身就应被清除
> - 阈值可通过 `MemoryConfig` 配置覆盖

#### 5.5.1 流程

```
进程启动
   │
   ▼
ColdStartCoordinator.recover(now)
   │
   ├─ 1. SnapshotStore.load()
   │     ├─ 不存在 / 损坏 → DISCARD（步骤 4）
   │     └─ 存在 → 步骤 2
   │
   ├─ 2. 时效分档
   │     age = now - snapshot.snapshot_at
   │     ├─ age > snapshot_ttl_seconds (5min) → DISCARD（步骤 4）
   │     ├─ snapshot_fresh_threshold (30s) < age <= ttl → STALE（步骤 3b）
   │     └─ age <= snapshot_fresh_threshold → FRESH（步骤 3a）
   │
   ├─ 3a. FRESH 恢复（confidence=1.0）
   │     ├─ RealTimeRiskEvaluator.restore(snapshot.active_tracks)
   │     ├─ RecentBehaviorStore.restore(filtered_recent_behavior)
   │     │     过滤：只恢复 last_seen_at >= now - retention 的 visitor
   │     ├─ RecentBehaviorStore.evict_expired(now, retention)
   │     └─ 标记所有恢复的 _TrackRiskState.confidence = 1.0
   │
   ├─ 3b. STALE 恢复（confidence=0.5）
   │     ├─ 同 3a，但标记 confidence = 0.5
   │     ├─ 不主动发 WarningEvent（待新帧重新确认）
   │     └─ 下游 Agent 据 confidence 决定是否提示"待确认"
   │
   └─ 4. DISCARD（confidence=0.0，冷启动）
         ├─ RealTimeRiskEvaluator.reset()
         ├─ RecentBehaviorStore.reset()
         └─ 记录冷启动日志：reason=...
```

#### 5.5.2 ColdStartCoordinator（`memory/cold_start.py`）

```python
class ColdStartConfidence(str, Enum):
    FRESH = "fresh"      # age <= 30s，完全恢复
    STALE = "stale"      # 30s < age <= 5min，降级恢复
    DISCARD = "discard"  # age > 5min 或缺失/损坏，冷启动


@dataclass
class RecoveryResult:
    recovered: bool                      # True=从 snapshot 恢复（FRESH 或 STALE），False=冷启动（DISCARD）
    confidence: ColdStartConfidence      # 恢复可信度档位
    reason: str                          # "snapshot_loaded_fresh" / "snapshot_loaded_stale" / "snapshot_missing" / "snapshot_stale" / "snapshot_corrupted"
    restored_tracks: int
    restored_visitors: int
    snapshot_age_seconds: Optional[float]


class ColdStartCoordinator:
    """冷启动恢复协调器 —— Snapshot + Eviction + Confidence 协同。"""

    def __init__(
        self,
        snapshot_store: SnapshotStore,
        evaluator: RealTimeRiskEvaluator,
        recent_store: RecentBehaviorStore,
        config: MemoryConfig,
    ) -> None:
        self._snapshot_store = snapshot_store
        self._evaluator = evaluator
        self._recent_store = recent_store
        self._config = config

    def recover(self, now: datetime) -> RecoveryResult:
        snapshot = self._snapshot_store.load()
        if snapshot is None:
            self._cold_start()
            return RecoveryResult(False, ColdStartConfidence.DISCARD,
                                  "snapshot_missing", 0, 0, None)

        age = (now - snapshot.snapshot_at).total_seconds()

        # 分档
        if age > self._config.snapshot_ttl_seconds:
            self._cold_start()
            return RecoveryResult(False, ColdStartConfidence.DISCARD,
                                  "snapshot_stale", 0, 0, age)

        if age <= self._config.snapshot_fresh_threshold_seconds:
            confidence = ColdStartConfidence.FRESH
            reason = "snapshot_loaded_fresh"
        else:
            confidence = ColdStartConfidence.STALE
            reason = "snapshot_loaded_stale"

        # 恢复 evaluator（带 confidence 标记）
        self._evaluator.restore(snapshot.active_tracks, confidence=confidence.value)

        # 恢复 recent_behavior（过滤过期 visitor）
        retention = self._config.recent_behavior_retention_seconds
        cutoff = now - timedelta(seconds=retention)
        filtered = [
            s for s in snapshot.recent_behavior
            if s.last_seen_at >= cutoff
        ]
        self._recent_store.restore(filtered, now)

        # 双保险：evict 一次
        self._recent_store.evict_expired(now, retention)

        return RecoveryResult(
            recovered=True,
            confidence=confidence,
            reason=reason,
            restored_tracks=len(snapshot.active_tracks),
            restored_visitors=len(filtered),
            snapshot_age_seconds=age,
        )

    def _cold_start(self) -> None:
        self._evaluator.reset()
        self._recent_store.reset()
```

> **`_TrackRiskState` 增加 `confidence` 字段**：Stage C 数据结构改造时同步加入（默认 1.0）。FRESH 恢复置 1.0；STALE 恢复置 0.5；新检测主体置 1.0（不继承历史 confidence）。下游 Agent / DecisionPolicy 可读取此字段决定是否发警报。

#### 5.5.3 Snapshot + Eviction + Confidence 协同

| 场景 | Snapshot age | Confidence | ACTIVE_RISK 行为 | 下游决策 |
| --- | --- | --- | --- | --- |
| 短暂断电（< 30s） | FRESH | 1.0 | 完全恢复，新 CLEARED 可回填原 `raised_signal_id` | 可继续基于此发警报 |
| 较长停机（30s ~ 5min） | STALE | 0.5 | 恢复但降级，等新帧重新确认 | 不发警报，Agent 可发"待确认"提示 |
| 长时间停机（> 5min） | DISCARD | 0.0 | 丢弃，从零开始 | 无历史依赖，纯新检测 |
| Snapshot 缺失 | N/A | 0.0 | 冷启动 | 同上 |
| Snapshot 损坏 | N/A | 0.0 | 冷启动 | 同上 |
| 恢复后短时间又重启 | 视 age 而定 | 视 age 而定 | 加载最新 snapshot 重新分档 | 视档位 |

> **关键设计**：
> 1. 恢复时**只恢复 active visitor**（last_seen_at 在 retention 内），不恢复 inactive visitor —— 避免 TD-0024 旧条目累积重现
> 2. **STALE 档不主动发 Warning**：confidence=0.5 的恢复状态需要新帧重新确认才升级为 confidence=1.0；避免"重启即警报"误判
> 3. **confidence 单调上升**：STALE(0.5) → 新帧检测到同一 visitor → confidence 升至 1.0；不允许下降（避免抖动）

#### 5.5.4 Pipeline 启动钩子（修改 `runtime/pipeline.py`）

```python
class PerceptionPipeline:
    def __init__(self, ..., memory_config: Optional[MemoryConfig] = None):
        # ... 既有代码 ...
        self._memory_config = memory_config
        self._cold_start_coordinator: Optional[ColdStartCoordinator] = None
        self._snapshot_store: Optional[SnapshotStore] = None

        if memory_config and memory_config.enabled:
            self._snapshot_store = SnapshotStore(Path(memory_config.snapshot_path))
            self._cold_start_coordinator = ColdStartCoordinator(
                self._snapshot_store,
                self._realtime_evaluator,
                self._recent_behavior_store,
                memory_config,
            )
            # 启动时立即恢复
            result = self._cold_start_coordinator.recover(now_dt())
            logger.info("cold_start_recovery", result=asdict(result))

    def process_frame(self, frame, frame_index, now):
        # ... 既有逻辑 ...

        # Stage F：Memory Shadow Mode（gated by memory_enabled）
        if self._memory_config and self._memory_config.enabled:
            self._memory_shadow_step(behavior_states, risk_signals, events, now)

        # Stage C：周期 snapshot
        if self._snapshot_store and self._should_snapshot(now):
            self._write_snapshot(now)

        return FrameResult(...)

    def _should_snapshot(self, now: datetime) -> bool:
        if self._last_snapshot_at is None:
            return True
        return (now - self._last_snapshot_at).total_seconds() >= self._memory_config.snapshot_interval_seconds

    def _write_snapshot(self, now: datetime) -> None:
        active = self._realtime_evaluator.snapshot(now)
        recent = self._recent_behavior_store.snapshot()
        snapshot = RuntimeSnapshot(
            snapshot_id=str(uuid4()),
            snapshot_at=now,
            active_tracks=active,
            recent_behavior=recent,
        )
        self._snapshot_store.save(snapshot)
        self._last_snapshot_at = now

    def shutdown(self) -> None:
        """优雅退出：flush 最终 snapshot。"""
        if self._snapshot_store:
            self._write_snapshot(now_dt())
```

### 5.6 MemoryStore Monotonicity（I2 实现细节）

#### 5.6.1 I2 校验时机

I2 Monotonicity 不在 `InvariantValidator`，而在 `MemoryStore.upsert_episodic()` 内部。原因：I2 需要查询 store 现有记录，validator 是无状态的，无法独立完成。

#### 5.6.2 correction 字段追加规则

`EpisodicRecord.corrections` 字段已在 §5.1.1 定义，此处说明 upsert 逻辑：

```python
class InMemoryStore:
    def upsert_episodic(self, record: EpisodicRecord) -> bool:
        if record.record_id not in self._episodic:
            self._episodic[record.record_id] = record
            return True

        existing = self._episodic[record.record_id]
        # 允许追加 corrections（I2 例外）
        if record.corrections:
            existing.corrections.extend(record.corrections)
            # 同时允许 memory_status 转移（§5.7.2）
            if record.memory_status != existing.memory_status:
                existing.memory_status = record.memory_status
            return False
        # 拒绝覆写其他字段
        if self._fields_differ(existing, record):
            raise InvariantViolationError(
                f"I2: EpisodicRecord {record.record_id} 已存在，禁止覆写非 correction 字段"
            )
        return False  # 幂等命中
```

> **correction 字段是 I2 的唯一例外**：ADR-0024 §3.2.3 I2 明确"可追加 correction / annotation 字段（不改原字段，只附加修正说明）"。

### 5.7 Memory Status & Validity Version（新增）

#### 5.7.1 三种 Version 概念区分

Memory 涉及三种"版本"概念，工程实现时必须区分：

| 字段 | 语义 | 用途 | 变更触发 |
| --- | --- | --- | --- |
| `schema_version` | 数据结构版本（如 `1`） | 反序列化兼容；schema 迁移 | Records dataclass 字段增删 |
| `model_version` | 生成模型版本（如 `"ep-builder-v1"`） | 溯源"这条记忆由哪个版本的算法生成" | Episode Builder / Aggregator 算法升级 |
| `memory_status` | 当前是否可被消费（如 `ACTIVE`） | 控制 Agent / 聚合是否引用此条记忆 | 模型升级发现旧记忆误判 / 合规归档 / 主动作废 |

> **关键区分**：
> - `schema_version=1` 不代表记忆"有效"，只代表"数据结构是 v1"
> - `model_version="ep-builder-v1"` 不代表记忆"当前可用"，只代表"由 v1 算法生成"
> - `memory_status=DEPRECATED` 表示"这条记忆保留可追溯，但不参与新决策"

#### 5.7.2 memory_status 状态机

```
                ┌─────────────────────────────┐
                │                             │
                ▼                             │
新 MemoryRecord 产生                          │
        │                                     │
        ▼                                     │
    ACTIVE ──────► DEPRECATED ──────► ARCHIVED
        │              │
        │              │
        ▼              ▼
    INVALID       INVALID
                   (合并到归档)
```

| 转移 | 触发 | 谁执行 | I2 兼容性 |
| --- | --- | --- | --- |
| ACTIVE → DEPRECATED | 模型升级发现旧 Episode 误判 | 工具脚本（手动 / 半自动） | ✅ 仅改 `memory_status` 字段，不动其他字段；追加 `corrections` 说明原因 |
| ACTIVE → INVALID | 发现明显误判（如检测错位） | 工具脚本 | ✅ 同上 |
| DEPRECATED → ARCHIVED | 合规归档（如超过保留期） | 工具脚本 / 自动 | ✅ 同上 |
| ACTIVE → ARCHIVED | 直接归档（无降级过程） | 合规要求 | ✅ 同上 |
| 任意 → ACTIVE | ❌ 禁止 | —— | ❌ 违反 I2 Monotonicity（不允许"复活"已降级的记忆） |

> **I2 兼容性**：状态转移只改 `memory_status` + 追加 `corrections`，不修改原字段（如 `risk_level` / `summary`）。这样保留历史证据，可审计"为什么这条记忆被降级"。

#### 5.7.3 消费规则

下游消费者（Agent / Semantic Aggregator）的过滤规则：

```python
def consumable_episodes(store: MemoryStore) -> List[EpisodicRecord]:
    """只返回可消费的 Episode（ACTIVE）。"""
    return [
        ep for ep in store.get_all_episodic()
        if ep.memory_status == MemoryStatus.ACTIVE
    ]
```

| memory_status | Agent 可引用 | Semantic 聚合可包含 | 工具脚本可查 |
| --- | --- | --- | --- |
| ACTIVE | ✅ | ✅ | ✅ |
| DEPRECATED | ❌ | ❌ | ✅（带降级标注） |
| ARCHIVED | ❌ | ❌ | ✅（只读） |
| INVALID | ❌ | ❌ | ✅（带作废原因） |

#### 5.7.4 v1 工程方案不实现的内容

| 项 | v1 | 归属 |
| --- | --- | --- |
| `memory_status` 字段定义 + 默认 ACTIVE | ✅ Stage A | 本方案 |
| 消费时按 `memory_status` 过滤 | ✅ Stage A（InMemoryStore 提供 `get_active_episodic()`） | 本方案 |
| 状态转移工具脚本 | ❌ | 未来 Memory Consistency ADR + 工具方案 |
| 自动降级算法（如 confidence 衰减触发 DEPRECATED） | ❌ | 未来 Memory Consistency ADR |
| 合规归档策略 | ❌ | 未来合规 ADR |

> **设计意图**：v1 先把字段和过滤规则建好，让未来模型升级时**有地方标注降级**，而不是只能删除。删除会丢失历史证据；降级 + corrections 保留可追溯性。

---

## 6. 测试矩阵

### 6.1 Stage A — 不变量测试（`tests/memory/test_policy_invariants.py`）

| 用例 | 不变量 | 验证 |
| --- | --- | --- |
| `test_i1_same_event_id_produces_one_record` | I1 | 同 `event_id` 投影 3 次，store 中只有 1 条 |
| `test_i1_record_id_must_derive_from_source` | I1 | `record_id` 不以 `ep-` 开头时拒绝 |
| `test_i2_existing_episode_no_overwrite` | I2 | 已存在的 EpisodicRecord 覆写字段时抛 `InvariantViolationError` |
| `test_i2_correction_append_allowed` | I2 | 追加 `corrections` 字段不抛异常 |
| `test_i3_created_at_none_rejected` | I3 | `created_at=None` 时拒绝（Stage A 简化版） |
| `test_i4_no_source_event_ids_rejected` | I4 | `source_event_ids=[]` 时拒绝 |

### 6.2 Stage B — 投影正确性测试（`tests/memory/test_episode_builder.py`）

| 用例 | 验证 |
| --- | --- |
| `test_project_no_warning` | 无 Warning 的 VisitorEvent → `risk_level=None` |
| `test_project_single_warning` | 单 Warning → 字段正确映射 |
| `test_project_multiple_warnings_max_wins` | 多 Warning → `risk_level=max`（HIGH > MEDIUM > LOW） |
| `test_project_actions_filtered_by_warning_id` | ActionCommand 按 `warning_id` 关联，不串号 |
| `test_project_warning_outside_time_window_ignored` | 超过 60s 容差的 Warning 不关联 |
| `test_project_summary_deterministic` | 同输入多次投影，summary 一致 |
| `test_project_idempotent_record_id` | 同 `event_id` 投影 2 次，`record_id` 相同 |
| `test_project_person_identity_id_always_none_v1` | v1 恒 None |

### 6.3 Stage C — Snapshot 持久化测试（`tests/memory/test_snapshot_persistence.py`）

| 用例 | 验证 |
| --- | --- |
| `test_snapshot_save_load_roundtrip` | save → load 字段无损 |
| `test_snapshot_atomic_write` | 写入中途 crash（模拟）不破坏既有文件 |
| `test_snapshot_load_missing_file_returns_none` | 文件不存在 → None |
| `test_snapshot_load_corrupted_json_returns_none` | JSON 损坏 → None（不抛异常） |
| `test_evaluator_snapshot_excludes_derived_fields` | `dwell_seconds` / `is_odd_hour` 不在 snapshot |
| `test_evaluator_restore_reconstructs_active` | restore 后 `_active` 与原状态一致 |
| `test_evaluator_restore_clears_existing_first` | restore 前的 `_active` 被清空 |

### 6.4 Stage D — Eviction 测试（`tests/analysis/test_recent_behavior_eviction.py`）

| 用例 | 验证 |
| --- | --- |
| `test_evict_expired_removes_old_entries` | `last_seen_at < cutoff` 的条目被清 |
| `test_evict_expired_keeps_recent_entries` | `last_seen_at >= cutoff` 的条目保留 |
| `test_evict_expired_returns_count` | 返回被清理条目数 |
| `test_evict_expired_empty_store` | 空 store 不抛异常，返回 0 |
| `test_evict_soak_1000_visitors` | 模拟 1000 个 visitor 依次进入，evict 后 `_entries` 不超过上限 |
| `test_last_seen_at_updated_on_every_update` | 同 visitor 多次 update，`last_seen_at` 始终刷新 |
| `test_visits_in_window_unchanged_after_eviction` | eviction 不破坏滑窗计数语义 |

### 6.5 Stage E — 冷启动恢复测试（`tests/memory/test_cold_start_recovery.py`）

| 用例 | 验证 |
| --- | --- |
| `test_recover_fresh_snapshot_confidence_1` | age < 30s → confidence=FRESH(1.0)，状态完全恢复 |
| `test_recover_stale_snapshot_confidence_0_5` | 30s < age < 5min → confidence=STALE(0.5)，状态恢复但降级 |
| `test_recover_stale_no_warning_emitted` | STALE 恢复后不主动发 WarningEvent（待新帧确认） |
| `test_recover_stale_then_new_frame_upgrades_confidence` | STALE 恢复后新帧检测到同一 visitor → confidence 升至 1.0 |
| `test_recover_discard_snapshot_triggers_cold_start` | 5min+ snapshot → confidence=DISCARD(0.0)，冷启动 |
| `test_recover_from_missing_snapshot_triggers_cold_start` | 无 snapshot → DISCARD |
| `test_recover_from_corrupted_snapshot_triggers_cold_start` | 损坏 snapshot → DISCARD，不抛异常 |
| `test_recover_filters_inactive_visitors` | 恢复时只恢复 active visitor，inactive 不恢复 |
| `test_recover_then_evict_no_accumulation` | 恢复后 evict，`_entries` 不超过 retention 上限 |
| `test_recover_fresh_preserves_active_risk_state` | FRESH 恢复后 `ACTIVE_RISK` 状态保留（TD-0027 验收） |
| `test_recover_fresh_preserves_raised_signal_id` | FRESH 恢复后 `raised_signal_id` 保留（CLEARED 可回填） |
| `test_recover_stale_preserves_state_but_marks_low_confidence` | STALE 恢复后状态保留但 confidence=0.5 |
| `test_recover_log_recorded` | 返回 `RecoveryResult` 字段完整（含 confidence） |

### 6.6 Stage F — Pipeline Shadow Mode 测试（`tests/runtime/test_pipeline_memory_shadow.py`）

| 用例 | 验证 |
| --- | --- |
| `test_shadow_mode_does_not_produce_warnings` | Shadow 开启时 `WarningEvent` 计数与关闭时一致 |
| `test_shadow_mode_writes_snapshot` | 30s 周期到达时写 snapshot 文件 |
| `test_pipeline_shutdown_flushes_snapshot` | `shutdown()` 调用后 snapshot 文件存在 |
| `test_pipeline_recover_on_init` | 构造时调用 `ColdStartCoordinator.recover()` |

### 6.7 Memory Replay Test（跨 Stage，`tests/memory/test_memory_replay.py`）

> **Memory 系统的核心测试**：相同事件流回放必须产出相同的 MemoryRecord。这是 I1 幂等性的端到端验证，也是 v1→v2 后端迁移时的回归基线。

#### 6.7.1 测试设计

```
Event Log（固定输入）
  ├─ VisitorEvent[1..N]
  ├─ WarningEvent[1..M]
  └─ ActionCommand[1..K]
        │
        ▼
  Episode Builder（确定性投影）
        │
        ▼
  MemoryStore（v1 InMemoryStore）
        │
        ▼
  MemoryRecord[]（实际产出）
        │
        ├─ 与 baseline.json 比对（字段级深度相等）
        └─ 与第二次回放产出比对（I1 幂等性）
```

#### 6.7.2 测试用例

| 用例 | 验证 |
| --- | --- |
| `test_replay_same_event_log_produces_same_memory` | 同一事件流回放 2 次，MemoryStore 中 EpisodicRecord 完全相同（字段级） |
| `test_replay_idempotent_no_duplicate_records` | 回放 3 次，record_count 不变（I1） |
| `test_replay_baseline_snapshot_match` | 回放产出与 `tests/fixtures/memory_baseline.json` 深度相等 |
| `test_replay_order_independent_for_disjoint_visitors` | 两个不相关 visitor 的事件交错输入 vs 顺序输入，产出相同 |
| `test_replay_order_dependent_for_same_visitor` | 同一 visitor 的事件乱序输入时，按 `enter_time` 排序后产出与有序输入相同 |
| `test_replay_with_warning_retry` | 上游重试导致 WarningEvent 重复投递，MemoryStore 中只有 1 条 EpisodicRecord |
| `test_replay_after_cold_start` | 回放 → 写 Snapshot → 重置 store → 从 Snapshot 恢复 → 继续回放 → 产出与连续回放一致 |
| `test_replay_v1_v2_backend_equivalence` | v2 SQLite 后端落地后：相同事件流在 v1 InMemoryStore 和 v2 SQLiteStore 产出深度相等 |

#### 6.7.3 Baseline 维护

- `tests/fixtures/memory_baseline.json`：固定事件流 + 期望产出的 EpisodicRecord[] 快照
- 生成方式：首次运行时自动生成；后续 Episode Builder 算法升级时，需 Owner 审核后更新
- 更新流程：跑 `pytest tests/memory/test_memory_replay.py --update-baseline` → 人工 diff → commit

#### 6.7.4 Replay Test 的硬约束

> **任何 Episode Builder / MemoryPolicy 实现变更必须通过 Replay Test**。
>
> 如果 Replay Test 失败但新行为是预期的（如算法升级改善投影质量），必须：
> 1. 在 PR 描述中说明"为什么产出变化"
> 2. 更新 `memory_baseline.json` 并附 diff
> 3. Owner 审核确认
>
> 否则视为回归，PR 不予合并。

### 6.8 Soak Test 验收（重跑 R3，对齐 TD-0024 / TD-0027 验收标准）

| 指标 | 修复前基线 | 验收阈值 |
| --- | --- | --- |
| `store_entries` after 2h | 2 → 308（线性增长） | < 50（稳定不增长） |
| p50 延迟漂移 | +13ms/h | < +2ms/h |
| `visits_in_window` 计数 | 正确 | 与修复前一致（不破坏语义） |
| 重启后 `ACTIVE_RISK` 保留 | ❌ 丢失 | ✅ 保留（TD-0027 解） |
| 重启后 `raised_signal_id` 保留 | ❌ 丢失 | ✅ 保留（CLEARED 可回填） |
| Snapshot 5min stale 后冷启动 | N/A | ✅ 视为冷启动 |

> **复用既有 Soak Test 脚本**：`scripts/soak_test_realtime_r3.py`，新增 `--memory-enabled` flag 开启 Shadow Mode 跑 2h。

---

## 7. Migration Plan

### 7.1 Stage 执行顺序

```
Stage A (接口 + 不变量测试)
    │
    ▼  ← 可独立 merge，不接 pipeline
Stage B (Episode Builder)
    │
    ▼  ← 可独立 merge，不接 pipeline
Stage C (Snapshot 持久化 + 数据结构改造)
    │   ← 修改 RealTimeRiskEvaluator / RecentBehaviorStore（加 public 方法 + first_seen + confidence）
    ▼
Stage D (Eviction)
    │   ← 修改 RecentBehaviorStore（加 evict_expired，数据结构已在 C 改造）
    ▼
Stage E (冷启动恢复 + Confidence 分档)
    │   ← 依赖 C + D；修改 pipeline 启动钩子
    ▼
Stage F (Pipeline Shadow Mode)
    │   ← 默认关闭；soak 验证后开 Write Mode
    ▼
Stage G (Environment Semantic)         ← Phase 5 前置；不依赖身份
    │
    ▼
Stage H (Identity Semantic)            ← Phase 4 ReID 后；依赖 person_identity_id
```

每个 Stage 独立 PR，独立 review，独立 merge。Stage A-B 不修改既有代码；Stage C-E 修改既有代码但保持向后兼容（新方法不影响既有调用）；Stage F 默认关闭，opt-in 启用；Stage G-H 是未来阶段占位，v1 不实现。

### 7.2 兼容性策略

| 改动 | 兼容性 | 策略 |
| --- | --- | --- |
| 新增 `memory/` 模块 | 100% 兼容 | 纯新增 |
| `RealTimeRiskEvaluator.snapshot()/restore()` | 100% 兼容 | 新增 public 方法，不动既有方法 |
| `_TrackRiskState` 加 `first_seen` 字段 | 100% 兼容 | dataclass 加字段有默认值 |
| `RecentBehaviorStore._entries` value 类型变更 | 内部不兼容 | 私有字段，外部不感知；`update()` 签名不变 |
| `pipeline` 启动钩子 | 100% 兼容 | `memory_config=None` 时不触发 |
| `EpisodicRecord.corrections` 字段 | 100% 兼容 | 默认空 list |

### 7.3 回滚预案

| Stage | 回滚方式 |
| --- | --- |
| A-B | 删除 `memory/` 目录 + 测试 |
| C | 删除 `snapshot()/restore()` 方法 + `snapshot.py`；还原 `_TrackRiskState` 删 `first_seen`；还原 `_entries` value 类型为 `List[datetime]`，删 `BehaviorHistory` |
| D | 删除 `evict_expired()` 方法（数据结构在 Stage C 已建，回滚 D 不影响 C） |
| E | 删除 `cold_start.py` + 还原 pipeline 启动钩子 |
| F | `memory.enabled=False`（配置回滚，不删代码） |

> **Snapshot 文件回滚**：删除 `data/memory/snapshot.json` 即等效于冷启动。

---

## 8. Development Phase（开发阶段 · Slice 切分）

> **视角区分**：§4 / §7 是 **Stage 视角**（按架构层切分，回答"改哪些文件"）；本节是 **Slice 视角**（按开发节奏切分，回答"先做什么、后做什么、每个 Slice 验收什么"）。两者互补，不冲突。Slice 与 Stage 的映射见 §8.2。

### 8.1 Step 0：Scope Freeze（冻结范围）

进入开发前先做一次 Scope Freeze，避免陷入架构建设。

**当前版本（v1 MVP）不实现**：

| 不实现项 | 归属 | 理由 |
| --- | --- | --- |
| ❌ Identity Semantic Memory | Phase 4 ReID 后 | v1 `person_identity_id` 恒 None（ADR-0023） |
| ❌ Agent Memory Context Builder | Phase 5 | Memory 只读消费，Agent 接入归 Phase 5 |
| ❌ LLM Summary | 未来 ADR | Memory Policy 不调 LLM（ADR-0024 §3.2.2） |
| ❌ 分布式 Memory Service | —— | 本模块是 Home 端单进程，无分布式需求 |
| ❌ 向量数据库 / 时序库 | v3+ | v1 内存 + JSON 足矣（§5.1.5） |
| ❌ 复杂知识图谱 | —— | Semantic Memory 是聚合统计，非知识图谱 |
| ❌ Memory Conflict 自动解决 | 未来 ADR-00xx | v1 只预留字段，不实现策略 |
| ❌ Trust Layer confidence 衰减 | 未来 ADR-00xx | v1 只在 Cold Start 用 confidence 分档 |

**当前目标**：

> 完成 **Short-term + Episodic Memory MVP**：能从 Raw Event 流产出可查询的 MemoryRecord，并支持进程重启后的状态恢复。

---

### 8.2 Slice 与 Stage 映射

| Slice | 名称 | 对应 Stage | 主要交付 | 解锁能力 |
| --- | --- | --- | --- | --- |
| Slice 1 | Memory Core 基础模型 | Stage A（部分） | Records dataclass + 序列化 | 领域对象就位 |
| Slice 2 | Short-term Memory | Stage A（部分）+ Stage F（Shadow） | `MemoryPolicy.transform_short_term` + 写入触发 | 实时状态→工作记忆 |
| Slice 3 | Snapshot Recovery | Stage C + Stage E | Snapshot 持久化 + 冷启动恢复 | TD-0027 解 |
| Slice 4 | Episode Builder | Stage B | VisitorEvent → EpisodicRecord 投影 | 事件→记忆转换 |
| Slice 5 | Episodic Storage | Stage A（InMemoryStore）+ Stage F | 事件→记忆→查询链路 | 记忆可查询 |
| Slice 6 | Memory Evaluation | 跨 Stage | 压缩效果 + 信息保留 + 一致性验证 | MVP 验收 |

> **执行顺序**：Slice 1 → 2 → 3 → 4 → 5 → 6。每个 Slice 独立 PR，独立验收。Slice 1-2 不修改既有代码；Slice 3 起修改 `RealTimeRiskEvaluator` / `RecentBehaviorStore` / `pipeline`，但保持向后兼容。

---

### 8.3 Slice 1：Memory Core 基础模型

**目标**：建立 Memory 的领域对象，不连接存储。

**文件结构**：

```
src/home_perception/memory/
├── __init__.py
├── records.py          # ShortTermRecord / EpisodicRecord / SemanticAggregate / MemoryStatus
└── policy.py           # MemoryPolicy ABC（接口定义，不实现）
```

**只定义对象**：

```python
# records.py
class MemoryStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    INVALID = "invalid"

@dataclass
class ShortTermRecord:
    record_id: str
    visitor_instance_id: str
    phase: str
    # ... 完整字段见 §5.1.1

@dataclass
class EpisodicRecord:
    record_id: str
    visitor_instance_id: str
    person_identity_id: Optional[str]  # v1 恒 None
    # ... 完整字段见 §5.1.1
```

**不做**：
- ❌ 不连数据库 / 文件
- ❌ 不实现 `MemoryPolicy` 的具体逻辑（只定义 ABC）
- ❌ 不接入 pipeline

**验收标准**：

| 用例 | 验证 |
| --- | --- |
| 对象创建 | `ShortTermRecord(...)` / `EpisodicRecord(...)` 可构造 |
| 序列化往返 | `asdict(record)` → JSON → 反序列化 → 字段无损 |
| `MemoryStatus` 默认值 | 新建 record 默认 `ACTIVE` |
| `schema_version` 默认值 | 新建 record 默认 `1` |
| `model_version` 必填 | `EpisodicRecord.model_version` 不能为空 |
| `record_id` 前缀约束 | `st-` / `ep-` / `sem-` 前缀校验 |

---

### 8.4 Slice 2：Short-term Memory

**目标**：实现 `MemoryPolicy.transform_short_term()`，把 BehaviorState + RiskSignal 投影为 ShortTermRecord。

**对应 ADR**：

> ADR-0024 §3.1.1：StateSnapshot + TransitionEvent → Short-term Memory

**流程**：

```
BehaviorState（当前状态快照）
       │
       │
RiskSignal（状态跃迁事件）
       │
       ▼
MemoryPolicy.transform_short_term()
       │
       ▼
ShortTermRecord
```

**写入触发规则**（不是每秒写，而是状态跃迁才写）：

| 触发 | 写入内容 | 幂等键 |
| --- | --- | --- |
| `NONE → ACTIVE_RISK`（RAISED） | 新建 ShortTermRecord，phase=active_risk | `st-{visitor_instance_id}` |
| `ACTIVE_RISK → NONE`（CLEARED） | 更新 record，phase=none | 同上 |
| 周期快照（每 30s） | 覆写当前 record | 同上 |
| 访客离场 | 转 EpisodicRecord（Slice 4） | `ep-{event_id}` |

**不做**：
- ❌ 不逐帧写（`dwell_seconds` 每帧变化，不落盘）
- ❌ 不持久化（Slice 3 才接 Snapshot）

**验收标准**：

| 用例 | 验证 |
| --- | --- |
| 输入 `RiskSignal(RAISED)` | 产出 `ShortTermRecord(phase=active_risk)` |
| 输入 `RiskSignal(CLEARED)` | 更新 record，`phase=none` |
| 同一 `visitor_instance_id` 多次 RAISED | `record_id` 一致（幂等） |
| 周期快照触发 | 覆写当前 record，不新增 |
| 无跃迁时不写 | 只传 BehaviorState 无 RiskSignal → 周期快照才写 |
| I1 幂等 | 同 signal 重复投递 3 次，store 中只有 1 条 |
| I4 可解释 | `source_event_ids` 非空 |

---

### 8.5 Slice 3：Snapshot Recovery

**目标**：解 TD-0027，实现进程重启后的状态恢复。

**流程**：

```
运行状态
   ↓
Snapshot（JSON 原子写，§5.3）
   ↓
程序重启
   ↓
ColdStartCoordinator.recover()（§5.5）
   ↓
Restore（按 confidence 分档）
   ↓
继续运行
```

**恢复什么 / 不恢复什么**（ADR-0024 §3.7 原则：reconstructable only）：

| 字段 | 恢复 | 理由 |
| --- | --- | --- |
| `risk_phase` | ✅ | 状态机态，无法重算 |
| `raised_signal_id` | ✅ | CLEARED 回填依赖 |
| `raised_at` | ✅ | RAISED 时刻 |
| `first_seen` | ✅ | 访问起点 |
| `last_seen_at` | ✅ | 离场判定 |
| `dwell_seconds` | ❌ | `=(now - first_seen)` 可重算 |
| `risk_score` | ❌ | 派生指标 |
| `track_id` | ❌ | ByteTrack 内部态，重启后无意义 |

**Cold Start Confidence 分档**（见 §5.5.0）：

| 档位 | snapshot_age | 行为 | confidence |
| --- | --- | --- | --- |
| FRESH | < 30s | 完全恢复，下游可继续决策 | 1.0 |
| STALE | 30s ~ 5min | 降级恢复，不发警报，待新帧确认 | 0.5 |
| DISCARD | > 5min / 缺失 / 损坏 | 冷启动，评估器 reset() | 0.0 |

**验收标准**：

| 用例 | 验证 |
| --- | --- |
| Snapshot save → load 往返 | 字段无损 |
| FRESH 恢复 | `ACTIVE_RISK` 状态保留，`raised_signal_id` 保留 |
| STALE 恢复 | 状态保留但 `confidence=0.5`，不发 Warning |
| STALE → 新帧升级 | 新帧检测到同一 visitor → confidence 升至 1.0 |
| DISCARD 恢复 | 评估器 `reset()`，从零开始 |
| Snapshot 损坏 | 视为冷启动，不抛异常 |
| Snapshot 缺失 | 视为冷启动 |
| 恢复后 evict | `_entries` 不超过 retention 上限 |
| 恢复只恢复 active visitor | inactive visitor 不恢复（避免 TD-0024 重现） |

---

### 8.6 Slice 4：Episode Builder

**目标**：实现 `DefaultEpisodeBuilder.project_episode()`，把业务事件流投影为 EpisodicRecord。这是 Memory 架构里**最关键的一层**。

**对应 ADR**：

> ADR-0024 §3.2.1：Episode Builder 是 Event Projection 层，Memory 不直接理解业务对象。

**流程**：

```
VisitorEvent（访客离场生成）
WarningEvent[]（该访客触发的）
ActionCommand[]（该访客触发的）
          ↓
DefaultEpisodeBuilder.project_episode()
          ↓
EpisodicRecord（一次完整访问的记忆）
```

**关联规则**（见 §5.2.4）：

| 关联 | 主键 | 时间窗 |
| --- | --- | --- |
| VisitorEvent ↔ WarningEvent | `visitor_instance_id` | `WarningEvent.created_at ∈ [enter_time, leave_time + 60s]` |
| WarningEvent ↔ ActionCommand | `warning_id` | `ActionCommand.warning_id == WarningEvent.warning_id` |

**示例**：

输入（一个完整访问周期）：

```
enter（访客进入）
risk（风险升高）
warning（通知家属）
leave（访客离开）
```

输出：

```
1 EpisodicRecord{
    enter_time, leave_time, duration,
    risk_level, recommended_action,
    actions: [通知家属],
    summary: "18:32-18:44 访问（停留 12 分钟），风险等级 HIGH，已通知家属。",
    source_event_ids: [visitor_event_id, warning_id, command_id]
}
```

**不做**：
- ❌ 不调 LLM 生成 summary（纯函数模板，见 §5.2.3）
- ❌ 不接 EvidenceItem（ADR-0022 未落地，`evidence_refs=[]`）
- ❌ 不实现 Semantic 聚合（Slice 6 只验证，不聚合）

**验收标准**：

| 用例 | 验证 |
| --- | --- |
| 一个访问周期 → 1 Episode | 输入 enter+risk+warning+leave，输出 1 条 EpisodicRecord |
| 无 Warning 的访问 | `risk_level=None`，summary 含"未触发风险" |
| 多 Warning 取 max | HIGH > MEDIUM > LOW |
| ActionCommand 按 warning_id 关联 | 不串号 |
| 超时 Warning 不关联 | 超过 60s 容差不关联 |
| summary 确定性 | 同输入多次投影，summary 一致 |
| 幂等 | 同 `event_id` 投影 2 次，`record_id` 相同 |
| `person_identity_id` 恒 None | v1 约束 |

---

### 8.7 Slice 5：Episodic Storage

**目标**：接通存储，验证"事件 → 记忆 → 查询"完整链路。

**v1 存储选择**：`InMemoryStore`（内存）+ JSON Snapshot（仅 Short-term）。**重点不是性能，是链路验证**。

> ⚠️ **v1 持久化不对称警告**（见 §5.1.4）：
> - Short-term：JSON 持久化，重启可恢复
> - Episodic：内存 only，重启即丢
>
> Phase 5 Agent 接入前必须迁移 Episodic 到 SQLite（§5.1.5）。

**链路验证**：

```
VisitorEvent + WarningEvent + ActionCommand
    ↓
Episode Builder（Slice 4）
    ↓
EpisodicRecord
    ↓
InMemoryStore.upsert_episodic()
    ↓
store.get_episodic_by_visitor(visitor_id)
    ↓
返回 EpisodicRecord[]（可查询）
```

**I2 Monotonicity 校验**（见 §5.6）：

| 场景 | 行为 |
| --- | --- |
| 新 record_id | 插入 |
| 已存在 record_id + 字段相同 | 幂等命中，不报错 |
| 已存在 record_id + 字段不同 | 抛 `InvariantViolationError` |
| 已存在 record_id + 追加 corrections | 允许（I2 例外） |

**验收标准**：

| 用例 | 验证 |
| --- | --- |
| 事件 → 记忆 → 查询 | 投影后 `get_episodic_by_visitor` 返回正确记录 |
| 同 visitor 多次访问 | 返回多条 EpisodicRecord，按时间排序 |
| I2 幂等 | 同 record_id 重复 upsert 不新增 |
| I2 禁止覆写 | 已存在 record 字段不同时抛异常 |
| `get_active_episodic` | 只返回 `memory_status=ACTIVE` 的记录 |
| DEPRECATED 记录不返回 | `get_active_episodic` 过滤掉 |
| corrections 追加 | 已存在 record 追加 corrections 不报错 |

---

### 8.8 Slice 6：Memory Evaluation

**目标**：验证 Memory 系统的有效性。Memory 不是写完就结束，需要量化验证。

#### 8.8.1 压缩效果（Compression Ratio）

**原始**：

```
10000 BehaviorState（每帧一条，dwell_seconds 逐帧变化）
```

**应该变成**：

```
1 EpisodicRecord（一次完整访问的记忆）
```

**验收**：

| 指标 | 阈值 |
| --- | --- |
| 压缩比（原始状态数 : EpisodicRecord 数） | ≥ 100:1（典型 10000:1） |
| ShortTermRecord 数 / 同期活跃 visitor 数 | ≈ 1:1（每 visitor 一条工作记忆） |

#### 8.8.2 信息保留（Information Retention）

Agent 未来需要回答的问题，Memory 不能丢：

| Agent 问题 | EpisodicRecord 字段 | 是否保留 |
| --- | --- | --- |
| 什么时候？ | `enter_time` / `leave_time` | ✅ |
| 谁？ | `visitor_instance_id`（v1）/ `person_identity_id`（v2） | ✅ |
| 发生什么？ | `summary` | ✅ |
| 风险？ | `risk_level` / `reason_summary` | ✅ |
| 处理？ | `actions` / `recommended_action` | ✅ |
| 证据？ | `evidence_refs`（v2）/ `source_event_ids`（v1） | ✅（v1 用 source_event_ids） |
| 模型版本？ | `model_version` | ✅ |
| 是否可信？ | `memory_status` / `confidence` | ✅ |

**验收**：构造一个完整访问周期，断言 EpisodicRecord 包含上述所有字段非空（`evidence_refs` v1 允许空 list，但 `source_event_ids` 必须非空）。

#### 8.8.3 一致性（Consistency / Replay Test）

**核心要求**：同样输入，必须产生同样 Memory。

**验证方式**：Memory Replay Test（见 §6.7）。

| 用例 | 验证 |
| --- | --- |
| 同一事件流回放 2 次 | EpisodicRecord 字段级深度相等 |
| 回放 3 次 | record_count 不变（I1 幂等） |
| 与 baseline.json 比对 | 深度相等 |
| 上游 retry 导致重复投递 | MemoryStore 中只有 1 条 |
| 冷启动后继续回放 | 产出与连续回放一致 |

**验收**：`pytest tests/memory/test_memory_replay.py` 全绿。

---

### 8.9 最终开发闭环

```
Raw Event Stream
    │
    │  (VisitorEvent / WarningEvent / ActionCommand / RiskSignal)
    ▼
Behavior System（ADR-0021）
    │
    │  (BehaviorState / RiskSignal)
    ▼
Memory Policy（transform + project）
    │
    ├──────────────────┐
    │                  │
    ▼                  ▼
Short-term Memory   Episode Builder
    │                  │
    │                  ▼
    │              Episodic Memory
    │                  │
    ▼                  ▼
Snapshot Store    Memory Store（InMemory + JSON）
    │                  │
    │                  ▼
    │              Query API
    │                  │
    ▼                  ▼
Cold Start Recovery  Agent（Phase 5）
    │                  │
    │                  ▼
    │              Semantic Memory（Phase 5+）
    │
    ▼
Evaluation（压缩 / 保留 / 一致性）
```

> **闭环约束**：
> 1. Memory Policy 只读消费，不反向写入状态（ADR-0024 §3.2.2）
> 2. Episode Builder 是唯一的事件→记忆转换入口（ADR-0024 §3.2.1）
> 3. Snapshot 只存 reconstructable state，不存 derived metrics（ADR-0024 §3.7）
> 4. Replay Test 是任何 Memory 变更的回归基线（§6.7.4）
> 5. v1 持久化不对称：Short-term 可恢复，Episodic 重启即丢（Phase 5 前必须迁移 SQLite）

---

## 9. 风险与开放问题

### 9.1 已识别风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Snapshot 写入与状态机并发竞争 | crash 时点 `_active` 可能半修改 | 原子写（.tmp + rename）；Snapshot 是 best-effort，丢失走冷启动 |
| 恢复的 `raised_signal_id` 与新 CLEARED 不配对 | CLEARED 找不到原 RAISED | 恢复后 evaluator 内部状态机继续工作，新 CLEARED 用恢复的 `raised_signal_id` 回填 |
| `track_id` 重启后变化导致 `last_track_id` 失效 | 仅日志受影响（docstring 已说 last_track_id 不参与判定） | 不持久化 `last_track_id`，恢复后置 None |
| Stage F Shadow Mode 性能开销 | 每帧投影 + 30s snapshot | Shadow 默认关闭；soak 验证开销 < 5% 再开 Write Mode |
| ADR-0022 EvidenceItem 未落地 | I4 `evidence_refs` 暂时为空 | I4 仍可通过 `source_event_ids` 满足；ADR-0022 落地后接 `evidence_refs` |

### 9.2 开放问题（留待后续 ADR / 工程方案）

| 问题 | 归属 | 紧迫性 |
| --- | --- | --- |
| Memory Conflict Resolution 具体策略 | 未来 ADR-00xx Memory Consistency Policy | 中（Phase 5 前） |
| Trust Layer confidence 计算 / 衰减算法 | 未来 ADR-00xx Memory Consistency Policy | 中（Phase 5 前） |
| **Episodic / Semantic SQLite 迁移** | v2 工程方案 | **高（Phase 5 Agent 接入前必须完成，否则 Agent 无历史可用）** |
| Agent Context Builder 接入 | Phase 5 工程方案 | 中 |
| Identity Semantic Memory 启用条件 | Phase 4 ReID 工程方案 | 低 |
| Stage G/H 详细设计 | 未来工程方案 | 低（占位已登记） |
| memory_status 状态转移工具脚本 | 未来 Memory Consistency ADR + 工具方案 | 低（v1 字段已预留） |
| 自动降级算法（confidence 衰减触发 DEPRECATED） | 未来 Memory Consistency ADR | 低 |

---

## 10. 与既有 ADR / 债务的关系

### 10.1 ADR 关系

| ADR | 关系 | 本方案对应章节 |
| --- | --- | --- |
| ADR-0024（Memory 架构） | 本方案是其工程落地 | §5.1-5.6 全部 |
| ADR-0021（实时风险流） | Snapshot 接管其 `_active` / `_entries` 持久化 | §5.3 / §5.4 |
| ADR-0022（证据链） | I4 `evidence_refs` 待其落地后接通 | §5.2.2 / §9.1 |
| ADR-0023（身份） | v1 `person_identity_id` 恒 None 约束 | §5.2.2 |
| ADR-0010（WarningEvent 决策） | Episode Builder 消费 WarningEvent 但不参与决策 | §5.2 |
| ADR-0014（三级冻结） | 全部 MINOR（新增对象 / 新增可选字段），不破 L1/L2 | §3.2 |

### 10.2 债务关系

| 债务 | 本方案章节 | 验收标准对齐 |
| --- | --- | --- |
| TD-0024 RecentBehaviorStore eviction | §5.4 | §6.4 + §6.7 |
| TD-0027 Runtime state recovery | §5.3 + §5.5 | §6.5 + §6.7 |

---

## 11. 附录

### 11.1 配置示例（`config/default.yaml` 新增块）

```yaml
memory:
  enabled: false                                      # Stage F Shadow Mode 开关
  snapshot_path: "data/memory/snapshot.json"
  snapshot_interval_seconds: 30.0
  snapshot_fresh_threshold_seconds: 30.0              # FRESH/STALE 分界（§5.5.0）
  snapshot_ttl_seconds: 300.0                         # STALE/DISCARD 分界（5min）
  recent_behavior_retention_seconds: 3600.0           # 1h
  eviction_interval_frames: 60                        # 每 60 帧 evict 一次
  cold_start_stale_confidence: 0.5                    # STALE 档 confidence
```

### 11.2 字段名选定记录

ADR-0024 §3.2.1 不固定字段名，由工程方案选定。本方案选定如下，未来可演进：

| 字段 | 选定名 | 备选（不采用） | 理由 |
| --- | --- | --- | --- |
| 人类可读摘要 | `summary` | `narrative` / `explanation` / `evidence_chain` | 短、语义清晰、与既有 `reason_summary` 风格一致 |
| 修正追加字段 | `corrections` | `annotations` / `amendments` | 直观表达"修正"语义，与 I2 Monotonicity 例外条款对齐 |
| 记录唯一键 | `record_id` | `memory_id` / `id` | 与 `signal_id` / `event_id` / `warning_id` 命名风格一致 |
| 快照时刻 | `snapshot_at` | `taken_at` / `captured_at` | 与 `created_at` 风格一致，但区分"快照时刻"与"记录创建时刻" |
| 记忆生命周期状态 | `memory_status` | `validity` / `lifecycle_state` | 与 `RiskPhase` / `BehaviorPhase` 命名风格区分（避免与业务状态机混淆）；语义聚焦"是否可消费" |
| 冷启动可信度 | `confidence` | `trust_score` / `recovery_confidence` | 与 ADR-0024 §3.9 Trust Layer 字段名对齐；本字段是 Trust Layer 的运行时投影 |

### 11.3 术语表

| 术语 | 定义 |
| --- | --- |
| **Short-term Memory** | 工作记忆，分钟级，访客离场后短期保留（ADR-0024 §3.1.1） |
| **Episodic Memory** | 事件记忆，天/月级，长期保留（ADR-0024 §3.1.2） |
| **Semantic Memory** | 模式记忆，月/年级，聚合产生（ADR-0024 §3.1.3） |
| **Memory Policy** | 转换边界，ObservationStream → MemoryRecord（ADR-0024 §3.2） |
| **Episode Builder** | Event Projection 层，业务事件 → Memory Object（ADR-0024 §3.2.1） |
| **Snapshot** | 运行时态持久化，reconstructable only（ADR-0024 §3.7） |
| **Cold Start** | 进程启动时无可用 Snapshot，状态从零开始 |
| **Eviction** | RecentBehaviorStore 主动清理过期 visitor 条目（TD-0024） |
| **Shadow Mode** | Memory Policy 接入 pipeline 但不产 Warning，只观察（Stage F） |
