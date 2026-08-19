# Memory 架构说明（Memory Architecture）

> **定位**：银龄盾「System × Memory 外部闭环」阶段（Integration Closure）的**文档冻结**产出之一。
> **阶段**：ADR-0024 Memory 内部闭环已完成（Slice 1–6 + Stage F Shadow Mode + E2E 4 类）。
> 本文档只描述**现有代码事实**，不含任何接口重构；接口泛化见 `docs/DESIGN-observation-contract.md`（未来契约）。
> **代码锚点**：所有 `file:line` 指 `main` 当前（合并 PR #94 后）代码。

---

## 0. 一句话定位

Memory 是风险链路的**旁路（Shadow Mode）**：它记录真实生产事件中"值得记住"的信息，但**不接决策、不产 Warning、不反向写入状态**。本阶段（Integration Closure）已证明三件事：

1. **链路真实可运行**（Slice B）：真实检测缓存 → tracker → event_builder → rule → decision → memory，事件确实进入 Memory。
2. **产生用户价值**（Slice C）：`MemoryQuery.compose_context()` 能由真实沉淀的 `EpisodicRecord` 组合出"昨天为什么报警"的可审计 JSON。
3. **结构清晰、可单测**（Slice A）：`MemoryHook` 把影子写入逻辑从 `PerceptionPipeline` 抽出为独立接线点。

---

## 1. 模块地图（Memory 子系统）

`src/home_perception/memory/` 包（领域层，**不连存储引擎、不接 pipeline**）：

| 文件 | 职责 | 关键导出 |
|---|---|---|
| `records.py` | 三类记忆领域对象 + 枚举 + 不变量校验（`__post_init__` 强制 I1–I4） | `ShortTermRecord` / `EpisodicRecord` / `SemanticAggregate` / `MemoryStatus` / `VisitorPresenceStatus` / `ActionSummary` / `EvidenceRef` |
| `policy.py` | `MemoryPolicy` ABC（transformation boundary，非决策模块） | `MemoryPolicy` |
| `short_term_policy.py` | `DefaultShortTermPolicy`：`transform_short_term` 实现（Slice 2） | `DefaultShortTermPolicy` |
| `episode_builder.py` | `DefaultEpisodeBuilder`：`project_episode` 投影（Slice 4） | `DefaultEpisodeBuilder` |
| `store.py` | `MemoryStore` / `InMemoryStore`（v1 内存 + JSON 序列化）+ `InvariantViolationError`（Slice 5） | `MemoryStore` / `InMemoryStore` / `InvariantViolationError` |
| `snapshot.py` | `SnapshotStore`：reconstructable 字段持久化（Slice 3） | `SnapshotStore` / `RuntimeSnapshot` |
| `cold_start.py` | `ColdStartCoordinator`：重启恢复（TD-0027，Slice 3） | `ColdStartCoordinator` |
| `query.py` | `MemoryQuery.compose_context`：组合查询（Product Closure，Slice C） | `MemoryQuery` |
| `__init__.py` | 包级导出白名单 | 见下 |

包级导出（`memory/__init__.py`）：

```python
# 消费层 / runtime 只应 import 这些名字
DefaultEpisodeBuilder, InMemoryStore, MemoryStore, InvariantViolationError,
MemoryQuery, VisitorPresenceStatus, DefaultShortTermPolicy, MemoryPolicy,
ShortTermRecord, EpisodicRecord, SemanticAggregate, MemoryStatus, ActionSummary, EvidenceRef
```

运行时接线点：`src/home_perception/runtime/memory_hook.py`（`MemoryHook`，Slice A 抽出）。

---

## 2. Runtime 接线图

`PerceptionPipeline`（`runtime/pipeline.py`）在 `process_frame` 的每个 `VisitorEvent` 处接 `MemoryHook`：

```
Observation Stream (camera / cached detection)
      │
      ▼
Risk Pipeline
  detector → tracker → event_builder → rule → RiskStateMachine
      │                                  │
      │                                  ▼
      │                            WarningEvent ──→ ActionExecutor ──→ ActionCommand
      │                                  │
      ▼                                  ▼
  Memory Hook（门控：episodic_shadow / memory.enabled）
      │  MemoryHook.record(VisitorEvent, warnings, actions)
      ▼
  DefaultEpisodeBuilder.project_episode(visitor_event, warnings, actions)
      │  纯函数投影 → EpisodicRecord
      ▼
  InMemoryStore.upsert_episodic(record)   ← 仅 Shadow Mode 落库，不接决策、不产 Warning
```

接线代码锚点：

- 导入：`runtime/pipeline.py:68` `from .memory_hook import MemoryHook`
- 构造：`runtime/pipeline.py:316-321` `self._memory_hook = MemoryHook(self._episode_builder, self._memory_store, self._episodic_shadow, self.metrics)`
- 调用：`runtime/pipeline.py:539-540` `if self._memory_hook.enabled: self._memory_hook.record(ev, ev_warnings, cmds)`
- 装配门控：`runtime/pipeline.py:414-423`（`from_settings` 中 `memory.enabled` 联动 `episodic_shadow`）

### 2.1 门控（Gating）：两级开关

`core/config.py:348-378` 的 `MemoryConfig`：

| 字段 | 默认 | 语义 |
|---|---|---|
| `enabled` | `false` | Memory 子系统总开关（含 Slice 3 Snapshot Recovery）。**默认关闭**。 |
| `episodic_shadow` | `false` | Stage F Episodic Memory 影子写入子开关。**默认关闭，v1 不产 Warning**。 |

激活真值表（`runtime/pipeline.py:414-428`）：

| `memory.enabled` | `memory.episodic_shadow` | 行为 |
|---|---|---|
| false | false | 全关；零运行时开销；行为与基线逐字段一致。 |
| **true** | false | 仅 Snapshot Recovery（冷启动恢复）激活；**不落 Episode**。 |
| true | **true** | 构造 `InMemoryStore` + `DefaultEpisodeBuilder`，每次访客离场投影落库（Shadow Mode）。 |
| false | true | 静默无效（`pipeline.episodic_shadow_requires_memory` 告警，影子未激活）。 |

> 联动约束：`memory.enabled=true` 会连带开启 realtime 旁路装配（`runtime/pipeline.py:387 / :401-405`），因为 Snapshot / Episode 持久化需要实时旁路组件就位。

---

## 3. 访客生命周期（Lifecycle Closure）

Episode 触发时机 = **Visitor Leave**（`analysis/event_builder.py` 仅在 track 从 `active → left` 时生成 `VisitorEvent`，见设计稿 §2.2）。Lifecycle 闭环如下：

```
Enter        访客进入视野（track 出现）
  │
Active       持续在场（ShortTermRecord 逐帧覆写，record_id=st-{visitor_instance_id}）
  │
Risk Raised  RAISED 信号（dwell abnormal / high_risk_approach）→ ShortTermRecord.phase=active_risk
  │
Warning      产出 WarningEvent（含 risk_level / recommended_action / reason_summary）
  │
Action       ActionExecutor 产出 ActionCommand（NOTIFY_FAMILY / ESCALATE_COMMUNITY …）
  │
Leave        访客离场（track left）→ event_builder 产出 1 个 VisitorEvent（本访问唯一事件）
  │
Episode      MemoryHook.record → project_episode → 1 条 EpisodicRecord（覆盖 enter→leave 全窗口）
             聚合全窗口 max risk + 全部 action，不截断在风险解除点（设计稿 §2.2 含义）
  │
Closed       EpisodicRecord 入 InMemoryStore（ACTIVE）。本次访问记忆 Closed。
```

> **固化纪律（Slice B 场景 4）**：一次访问不论中途风险是否回落，只产 1 条 Episode（聚合全窗口 max risk + 全部 action）。原始 fixture 为"恒定在场 + 离场"，验证的是**投影机制**（全窗口投影而非单帧快照）；"风险降→继续聊天→离开"相位留作后续更丰富 fixture（测试已诚实标注此边界，见 `test_memory_closure_slice_b.py:126-129`）。

---

## 4. 接线契约（Wiring Contract）

`MemoryHook`（`runtime/memory_hook.py`）契约：

| 维度 | 契约 |
|---|---|
| **门控** | 仅 `enabled`（即 `episodic_shadow`）为真时 `process_frame` 才调 `record`（`pipeline.py:539`）。`MemoryHook.enabled` 是 `@property` 只读（`memory_hook.py:49-52`）。 |
| **输入** | `record(ev: VisitorEvent, warnings: List[WarningEvent], actions: List[Any])`（`memory_hook.py:54-59`）。三参数来自当次访客离场事件关联的全部 warning / action。 |
| **输出（副作用）** | 经共享 `metrics`（`PipelineMetrics`）：`metrics.episodes_recorded += 1`（成功落库） / `metrics.errors += 1`（投影或落库异常）。不修改主链路任何对象。 |
| **失败隔离** | 见 §4.1。 |

### 4.1 失败隔离语义（Failure Isolation）

`record()` 五分支（`memory_hook.py:70-95`）：

1. `episode_builder is None or memory_store is None` → 直接 return（未接 Memory，无副作用）。
2. **投影异常**（`project_episode` 抛任意 `Exception`）→ `metrics.errors += 1` + `log.exception` + return。主链路（Camera/Risk/Warning/Action）照常。
3. **落库 `InvariantViolationError`（I2 单调冲突）** → 仅 `log.warning`（防御性告警），**不计入 `errors`**（不崩溃流水线，episode 静默丢弃）。
4. **落库其他异常** → `metrics.errors += 1` + `log.exception` + return。主链路不受影响。
5. 成功 `upsert_episodic` → `metrics.episodes_recorded += 1`。

> 设计纪律（AGENTS.md §2.5）：**记忆写入失败绝不崩溃主风险链路**。

### 4.2 输出契约边界（V0 冻结，Slice C）

`MemoryQuery.compose_context()`（`memory/query.py:41-120`）输出 7 字段，**严禁** Inference / Prediction / Recommendation：

| 字段 | 类型 | 来源（可溯源） |
|---|---|---|
| `visitor_instance_id` | str | 回显入参 |
| `current_status` | `VisitorPresenceStatus` | 窗口内 episode 与 `as_of` 比较（`_current_status`，`query.py:122-139`） |
| `reason` | str | 窗口内最高风险 episode 的 `enter_time/duration_seconds/risk_level` + 非常规时间嗅探（`_compose_reason`，`query.py:142-157`） |
| `evidence` | List[str] | 该 episode 的 `reason_summary`（空则 `summary` 兜底，`_compose_evidence`，`query.py:160-164`） |
| `handling` | str | 该 episode 的 `recommended_action` + 全部 `ActionSummary` 投影（`_compose_handling`，`query.py:167-178`） |
| `history` | str | 窗口内事件计数文本（`_history_text`，`query.py:181-187`） |
| `source_record_ids` | List[str] | 贡献 episode 的 `record_id` 列表（可溯源） |

> 完整 V0 边界见 `docs/DESIGN-memory-integration-closure.md` §3.6。
> **P3 已知脆弱点**：`reason` 中"非常规访问时间"来自对 `reason_summary` 文本嗅探（`query.py:151-156`，代码自带 `TODO(review #4)`），与分析层规则摘要文案强耦合，措辞一改即静默失效。未来应把该标记沉淀为 `EpisodicRecord` 结构化字段（tags / rule_ids），由 query 端直接读取。

---

## 5. 领域对象（Domain Objects）

三类记忆模型（`records.py`），字段名由工程方案选定（ADR-0024 §3.2.1，未来可演进）。

### 5.1 枚举

- `MemoryStatus`（ACTIVE / DEPRECATED / ARCHIVED / INVALID，§5.7）：表示"记忆是否可被消费"，**单向状态机（I2 单调性）**。⚠️ 与 `VisitorPresenceStatus` 语义完全不同，命名近似不可混用（review #5）。
- `VisitorPresenceStatus`（IN_PROGRESS / CLEARED / NO_RECORD）：回放/历史时间点语义的"访客在场/风险视图"。**非实时在场**（真实数据流 episode 仅离场后写入，`leave_time` 恒为过去，实时查询恒为 CLEARED）。实时在场应读 `ShortTermRecord.phase == "active_risk"`。

### 5.2 ShortTermRecord（工作记忆，分钟级）

幂等键 `st-{visitor_instance_id}`（每 visitor 一条，新状态覆写旧状态）。字段（`SHORT_TERM_RECORD_DICT_KEYS`，`records.py:260-272`）：

`record_id` / `visitor_instance_id` / `phase`（"none"|"active_risk"）/ `raised_signal_id` / `raised_at` / `first_seen`（snapshot 持久化）/ `last_seen_at` / `source_event_ids`（I4 非空）/ `memory_status`（默认 ACTIVE）/ `schema_version`（默认 1）/ `created_at`（UTC）。

### 5.3 EpisodicRecord（事件记忆，天/月级）

幂等键 `ep-{visitor_event.event_id}`（同一 VisitorEvent 多次投影只产 1 条，I1）。字段（`EPISODIC_RECORD_DICT_KEYS`，`records.py:399-418`）：

`record_id` / `visitor_instance_id` / `person_identity_id`（**v1 恒 `None`，ADR-0023**）/ `enter_time` / `leave_time` / `duration_seconds` / `risk_level`（无 Warning 则 `None`）/ `recommended_action`（取最高 risk 那条 action）/ `reason_summary`（WarningEvent.reason_summary 合并去重）/ `actions`（List[ActionSummary]）/ `evidence_refs`（**v1 恒空 list，ADR-0022 未落地**）/ `source_event_ids`（[visitor_event_id, warning_id, ...]，I4 非空）/ `summary`（human-interpretable 必填）/ `model_version`（必填非空）/ `memory_status`（默认 ACTIVE）/ `corrections`（I2 例外追加，不改原字段）/ `schema_version`（默认 1）/ `created_at`（UTC）。

### 5.4 SemanticAggregate（模式记忆，月/年级）

`sem-` 前缀（`SEMANTIC_RECORD_DICT_KEYS`）。**v1 仅 schema 占位**：`dimension` / `period_key` / `episode_count` / `statistics` / `confidence` / `source_episode_ids`。`aggregate_semantic` 最低阈值（`policy.py:132-137`）：`minimum_episodes ≥ 30` + `minimum_time_window ≥ 7 天` + `minimum_confidence` 达标才输出，否则返回 `None`。Stage G（Environment）/ Stage H（Identity）才填充。

---

## 6. Decision–Memory 边界守护（Boundary Guard）

Memory **绝不**腐化为隐形决策源（"这人以前危险所以报警"）。结构性保证（ADR-0024 §3.2）：

- `MemoryPolicy` 是 transformation boundary，**不**参与风险判定（决策中心是 `DecisionPolicy`，ADR-0010）、**不**参与行动（执行中心是 `ActionExecutor`，ADR-0011）、**不**修改 `BehaviorState`/`RiskSignal`、不调 LLM。
- Shadow Mode 只记录、不接决策、不产 `WarningEvent`。
- 常态化回归（E2E-4 思路，`test_memory_e2e_closed_loop.py`）：`memory.enabled` 开/关，同一帧序列下 `warnings` / `risk_signals` / `commands` / `behavior_states` **逐帧一致**。

> 该边界是 Integration Closure 的核心不变量，任何未来 Agent Reasoning / Memory v2 都不得突破（见设计稿 §7 风险项 #4）。

---

## 7. 相关文档

- `docs/ADR/0024-memory-architecture.md` — Memory 架构（Slice 1–6 + Stage F + Integration Closure 标注）
- `docs/design/memory/DESIGN-memory-pipeline.md` — Memory 内部设计
- `docs/DESIGN-memory-integration-closure.md` — 外部闭环设计（Slice B/C/A/D 规格）
- `docs/MEMORY_OPERATION_GUIDE.md` — 运行 / 运维操作手册
- `docs/MEMORY_TEST_REPORT.md` — 测试报告与验收样例
- `docs/DESIGN-observation-contract.md` — 未来模态无关 `Observation` 契约（本阶段不改代码）
