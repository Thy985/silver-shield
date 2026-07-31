# Memory 运维操作手册（Memory Operation Guide）

> **定位**：Integration Closure（Slice D，文档冻结）产出。描述 Memory 子系统的**开关、冷启动恢复、失败隔离、已知限制、Decision–Memory 边界守护**。
> **纪律**：本文档是**运维说明**，不含任何代码改动；所有字段/默认值锚定 `main` 当前代码（`core/config.py:348-378`、`runtime/pipeline.py`、`memory/`）。
> 接口泛化（多模态）见 `docs/DESIGN-observation-contract.md`（未来契约，本阶段不改代码）。

---

## 0. 速查

| 想做的事 | 怎么做 |
|---|---|
| 完全关闭 Memory | `memory.enabled: false`（默认即关） |
| 只开快照恢复（冷启动），不落 Episode | `memory.enabled: true` + `memory.episodic_shadow: false` |
| 开启完整影子写入（落 Episode） | `memory.enabled: true` + `memory.episodic_shadow: true` |
| 验证"昨天为什么报警" | `MemoryQuery(store).compose_context(visitor_instance_id, window_start, window_end, as_of=...)` |
| 确认 Memory 没拖垮主链 | 看 `metrics.episodes_recorded` / `metrics.errors`；`errors` 异常升高说明 Memory 在失败但被隔离 |

---

## 1. 开关配置（Switches）

`core/config.py:348-378` 的 `MemoryConfig`（Pydantic，`Settings.memory` 注入）：

```yaml
memory:
  enabled: false                 # 总开关，默认关
  episodic_shadow: false         # Stage F 影子写入子开关，默认关（v1 不产 Warning）
  snapshot_path: "data/memory/snapshot.json"
  snapshot_interval_seconds: 30.0          # 周期快照间隔
  snapshot_fresh_threshold_seconds: 30.0   # FRESH/STALE 分界
  snapshot_ttl_seconds: 300.0              # STALE/DISCARD 分界（5min）
  recent_behavior_retention_seconds: 3600.0  # 恢复时只保留 last_seen_at 在 1h 内的 visitor
  eviction_interval_frames: 60            # 每 N 帧内联 evict_expired()
  cold_start_stale_confidence: 0.5         # STALE 档恢复 confidence 值
```

### 1.1 激活真值表

| `enabled` | `episodic_shadow` | 实际行为 |
|---|---|---|
| false | false | 全关；零开销；行为与基线一致。 |
| **true** | false | 仅 Snapshot Recovery（冷启动恢复）；**不落 Episode**。 |
| true | **true** | 构造 `InMemoryStore` + `DefaultEpisodeBuilder`，每次访客离场投影落库（Shadow Mode）。 |
| false | true | 静默无效（`episodic_shadow_requires_memory` 告警，影子未激活）。 |

### 1.2 联动约束（重要）

- `memory.enabled=true` 会**连带开启 realtime 旁路装配**（`runtime/pipeline.py:387`，`realtime_enabled = settings.realtime_risk.enabled or settings.memory.enabled`）。若 `realtime_risk.enabled=false` 但 `memory.enabled=true`，会打 `memory_implicitly_enables_realtime` 信息日志，自动开启实时旁路（Shadow Mode，决策仍按配置）。
- 若 `memory.enabled=true` 但 realtime 旁路组件缺失 → `memory_enabled_without_realtime` 告警，跳过 Snapshot/恢复（`pipeline.py:291-295`）。
- 若 `episodic_shadow=true` 但 store/builder 缺失 → `episodic_shadow_without_store` 告警，影子写入静默跳过（`pipeline.py:303-309`）。

> **v1 存储说明**：`InMemoryStore` 是进程内内存存储（JSON 序列化能力已具备，但 v1 默认不落盘持久化 Episode）。进程重启后 Episode 丢失；ShortTerm 冷启动恢复依赖 `snapshot_path` 的 JSON 文件。Agent 查询（Slice C）在进程生命周期内有效。v2 迁 SQLite 后 `short_term_count()` 等公共只读口无感（`test_memory_evaluation.py` 已据此断言）。

---

## 2. 冷启动恢复操作（Cold Start Recovery，TD-0027）

### 2.1 机制

- `SnapshotStore`（`memory/snapshot.py`）按 `snapshot_path` 做**原子写**（先 `.tmp` 再 `os.replace`）。
- `ColdStartCoordinator.recover()` 在 `PerceptionPipeline.__init__` 时重建活跃风险状态（`runtime/pipeline.py` Slice 3 接线）。
- 状态档分级（`snapshot_fresh_threshold_seconds` / `snapshot_ttl_seconds`）：FRESH（直接恢复）→ STALE（带 `cold_start_stale_confidence` 降 confidence 恢复）→ DISCARD（超过 TTL，冷启动）。

### 2.2 关键不变量（验收已固化）

快照**只存 reconstructable 字段**，**不存 derived metrics**：

| 持久化字段 | 不持久化字段 |
|---|---|
| `track_id` / `visitor_instance_id` | `dwell_seconds`（重启后由 `now - first_seen` 重算） |
| `risk_phase` / `raised_at` / `enter_time`(first_seen) | `risk_score`（旧值不恢复） |
| `last_seen_at` | — |

验收（`test_memory_e2e_closed_loop.py` E2E-2 / `test_memory_closure_slice_b.py` 场景 2）：快照 JSON 断言 `"dwell_seconds" not in at` 且 `"risk_score" not in at`；重启后 `first_seen` / `raised_at` / `risk_phase` 恢复，`dwell = (clock2.now() - first_seen)` 重算。

### 2.3 运维要点

- 删除/移动 `snapshot_path` 指向的文件 → 下次启动视为 DISCARD（冷启动，无恢复）。
- `snapshot_ttl_seconds` 默认 300s：程序停止超过 5 分钟再启动，活跃风险状态按 DISCARD 处理（不恢复为活跃）。如需更长保留窗，调大该值（属工程方案范畴，非本阶段改动）。

---

## 3. 失败隔离语义（Failure Isolation）

Memory 写入失败**绝不崩溃主风险链路**（AGENTS.md §2.5）。运维视角：

| 现象 | 计数 | 主链路影响 | 日志 |
|---|---|---|---|
| `project_episode` 投影抛异常 | `metrics.errors += 1` | 无（Camera/Risk/Warning/Action 照常） | `pipeline.episode_build_failed`（exception） |
| 落库抛 `InvariantViolationError`（I2 单调冲突） | **不计入 errors** | 无（episode 静默丢弃） | `pipeline.episode_invariant_violation`（warning） |
| 落库抛其他异常 | `metrics.errors += 1` | 无 | `pipeline.episode_store_failed`（exception） |
| 成功落库 1 条 | `metrics.episodes_recorded += 1` | 无 | — |

> 监控建议：盯 `metrics.errors`。若持续升高但系统不崩，说明 Memory 后端在失败（如未来 SQLite 连接异常）；主链路仍正确，但"记忆"在丢失，需排查后端而非回滚主链。

`MemoryHook.record` 前置守卫（`memory_hook.py:70`）：`episode_builder is None or memory_store is None` → 直接 return（未接 Memory，零副作用）。

---

## 4. Decision–Memory 边界守护（Boundary Guard）

**铁律**：Memory 是旁路，绝不变成隐形决策源（"这人以前危险所以报警"）。

- Shadow Mode 只记录、不接 `DecisionPolicy`、不产 `WarningEvent`。
- `memory.enabled` 开/关，同一帧序列下 `warnings`/`risk_signals`/`commands`/`behavior_states` **逐帧一致**（E2E-4 已固化）。
- 任何 Agent Reasoning / Memory v2 都**不得**让 Memory 直接驱动 `WarningEvent` 或改写风险判定（ADR-0010 唯一决策中心）。

运维红线：若发现开启 `episodic_shadow=true` 后 `warnings` / `commands` 数量或内容与关闭时不一致 → **立即视为回归**，回滚并上报（正常应为零差异）。

---

## 5. 已知限制（Known Limitations）

1. **v1 不产 Warning / 不接决策**：Shadow Mode 仅记录，落库值不参与任何实时决策（设计如此，非缺陷）。
2. **InMemoryStore 进程内存储**：进程重启 → Episode 丢失；跨进程/跨设备共享归未来 ADR（ADR-0024 O6）。Snapshot 仅恢复 ShortTerm 活跃风险状态。
3. **`evidence_refs` v1 恒空**：ADR-0022 证据链未落地，`EpisodicRecord.evidence_refs` 始终 `[]`；证据目前靠 `source_event_ids` + `reason_summary` 文本承载（验收测试已诚实锁定 `evidence_refs == []`）。
4. **`person_identity_id` v1 恒 `None`**：ADR-0023 身份连续性未落地，跨会话不关联访客（Semantic 聚合 Stage H 无主键基础）。
5. **`compose_context` 的 `reason` 脆弱嗅探（P3）**："非常规访问时间"来自对 `reason_summary` 文本嗅探（`memory/query.py:151-156`，代码自带 `TODO(review #4)`），与分析层规则摘要文案强耦合，措辞一改即静默失效。Product Closure 的"可溯源"断言**必须覆盖 `reason` 字段本身**（设计稿 §7 风险项 #5/#7）。未来应把该标记沉淀为 `EpisodicRecord` 结构化字段（tags / rule_ids）。
6. **Lifecycle "风险降→继续聊天→离开" 相位未覆盖**：当前 fixture 为"恒定在场 + 离场"，只验证投影机制（全窗口聚合）；该相位需更丰富 fixture（如多阶段 visit / 去升级策略）固化（测试已诚实标注，`test_memory_closure_slice_b.py:126-129`）。
7. **两级测试边界**：Contract E2E（CI，cached detection 驱动，torch-free）证明"事件进入 Memory"，**不验证检测精度**；完整 `camera→YOLO→tracker` 路径留作 Production Demo（真机/人工，不进 CI）。Memory 验收目标是"链路真实可运行 + 事件进 Memory"，不是"框得准不准"。

---

## 6. 快速诊断清单

- [ ] `metrics.episodes_recorded` 在访客离场后 +1？（影子写入生效）
- [ ] `metrics.errors` 持续为 0？（Memory 后端健康）
- [ ] 开/关 `episodic_shadow`，`warnings/commands` 数量是否一致？（边界守护未破）
- [ ] 重启后活跃风险状态是否恢复？（查 `snapshot_path` 文件存在 + TTL 未超）
- [ ] `compose_context` 输出 `reason/evidence/handling` 是否非空且可溯源？（Product Closure 未被架空）

---

## 7. 相关文档

- `docs/MEMORY_ARCHITECTURE.md` — 架构 / 接线契约 / 领域对象
- `docs/MEMORY_TEST_REPORT.md` — 测试矩阵与验收样例
- `docs/ADR/0024-memory-architecture.md` — Memory 架构 ADR
- `docs/DESIGN-memory-integration-closure.md` — 外部闭环设计
