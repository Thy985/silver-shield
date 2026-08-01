# Memory 测试报告（Memory Test Report）

> **定位**：Integration Closure（Slice D，文档冻结）产出。汇总 Memory 子系统全部测试、**回放稳定性**、**信息损失评估**、**Product Closure 验收样例输出**。
> **纪律**：纯文档，无代码改动。所有结论锚定 `main` 当前测试文件（`tests/memory/*`、`tests/runtime/test_memory_e2e_closed_loop.py`、`tests/runtime/test_memory_closure_slice_b.py`）。
> 测试分级约定：模块内（torch-free）+ E2E 系统级（torch-free，CI 每 PR）+ Production Demo（真机/人工，不进 CI）。

---

## 0. 测试总览

| 层级 | 文件 | 范围 | 进 CI |
|---|---|---|---|
| 模块内·压缩/保留 | `tests/memory/test_memory_evaluation.py` | Slice 6（§8.8.1/§8.8.2） | ✅ |
| 模块内·回放一致性 | `tests/memory/test_memory_replay.py` | Slice 6（§8.8.3）+ baseline | ✅ |
| 系统级·E2E 4 类 | `tests/runtime/test_memory_e2e_closed_loop.py` | 内部闭环系统级验收（E2E-1~4） | ✅ |
| 系统级·Slice B | `tests/runtime/test_memory_closure_slice_b.py` | 外部闭环·真实链路（场景 1/2/3/4） | ✅（Contract E2E） |
| 真实演示 | `scripts/e2e_validate_demo.py` / 真机 | Production Demo（不进 CI） | ❌（人工） |

> 所有 Memory 测试 **torch-free**：用 `SteppingStubDetector` / `CachedDetectionDetector`（重放检测缓存 schema 的最小合成 fixture）驱动整链，CI 不含 YOLO 推理。

---

## 1. 模块内测试（Slice 6）

### 1.1 压缩效果（Compression Ratio，§8.8.1）

`test_memory_evaluation.py`：

| 用例 | 断言 | 结果口径 |
|---|---|---|
| `test_compression_ratio_meets_threshold` | 5 访客 × 2000 帧 = 10000 原始观测 → 实际保留 **10 条**（短期每 visitor 1 + 长期每访问 1），比率 **1000:1 ≥ 100:1** | 用 store **实际记录数**做分母（非假设 1 条） |
| `test_memory_size_is_independent_of_frame_count` | 帧数 10/100/1000 放大 100 倍，记录数**不变**（O(活跃 visitor) 非 O(帧数)） | 真正不变量：存储规模与帧数无关 |
| `test_short_term_one_record_per_active_visitor` | ShortTermRecord 数 / 同期活跃 visitor 数 = **1:1** | 经公共 `store.short_term_count()`，不碰私有结构（v2 迁 SQLite 无感） |

**结论**：Memory 不逐帧落盘（ADR-0024 §3.1.1），存储规模 O(visitor)，压缩比远超 100:1 阈值。

### 1.2 信息保留（Information Retention，§8.8.2）

| 用例 | 覆盖字段 |
|---|---|
| `test_information_retention_all_required_fields` | `enter_time`/`leave_time`/`visitor_instance_id`/`summary`/`risk_level`/`reason_summary`/`actions`/`recommended_action`/`source_event_ids`(I4 非空)/`model_version`/`memory_status`；`evidence_refs == []`（v1 诚实锁定） |
| `test_information_retention_no_risk_visit_still_complete` | 无风险访问：`risk_level`/`recommended_action`/`reason_summary`/`actions` 成组为 `None/[]`，但时间/谁/summary/证据仍完整（Agent 可答"何时来过"） |

**结论**：完整访问周期内 EpisodicRecord 含 Agent 未来需要的全部字段；风险字段成组为空（不出现"风险等级 None"半成品）。

### 1.3 回放一致性（Replay / Consistency，§8.8.3）

`test_memory_replay.py`：

- **固定 ID 约定**：`VisitorEvent.event_id` / `WarningEvent.warning_id` / `ActionCommand.command_id` 默认 UUID4（随机）→ 易致 baseline 不稳定，故本文件**全部事件用显式固定 ID**（§6.7.3）。
- **baseline 维护**：`tests/fixtures/memory_baseline.json`；首次运行或 `MEMORY_UPDATE_BASELINE=1` 生成，后续算法升级时人工 diff 确认更新，否则视为回归（§6.7.4 硬约束）。
- 跨顺序穷举（`itertools.permutations`）：warning/action 投递顺序重排，回放仍一致（I1/§6.7.2）。

---

## 2. 系统级 E2E 4 类（`test_memory_e2e_closed_loop.py`）

torch-free，进 CI 每 PR。设计铁律：Memory 是旁路，开启后风险决策与关闭时逐字段一致；异常不崩主链。用 `ManualClock` + `SteppingStubDetector` 以少量帧模拟长时段（如 32 帧 × 30s = 15 分钟）。

| 类别 | 用例 | 验收要点 |
|---|---|---|
| **E2E-1 生命周期** | `test_full_risk_event_lifecycle_produces_episode` | 18:30 enter → 18:35 RAISED → 18:45 leave → 1 条 EpisodicRecord；`duration≈15min`(840–960s)、`risk=HIGH`、`recommended_action=ESCALATE_COMMUNITY`；`reason_summary/actions/source_event_ids` 完整；生命周期确实产过 RAISED |
| **E2E-2 重启恢复** | `test_restart_recovers_risk_state_and_recomputes_dwell` | `visitor_instance_id`/`risk_phase`/`first_seen`/`raised_at` 恢复；`dwell_seconds` 重算（snapshot 不含）；旧 `risk_score` 不恢复；FRESH 档（age<30s）恢复 |
| **E2E-3 回放稳定** | `test_same_observation_stream_yields_identical_memory` | 同 Observation Stream 跑两趟 → 规范化（UUID 归一 + `created_at=NORMALIZED`）后 `to_dict` 逐字段相等、字节稳定；`risk=HIGH`、`duration≥600`、有 `source_event_ids` |
| **E2E-4 运行时接线** | `test_memory_on_is_true_bypass_no_risk_change` | 开/关 memory，同一帧序列 `warnings`/`risk_signals`/`commands`/`behavior_states` 逐帧一致；影子仍产 1 episode |
| | `test_memory_episode_build_failure_is_isolated` | 投影抛异常 → 主链照常（RAISED/Warning 仍产），`episodes_recorded=0`、`errors=1` |
| | `test_memory_store_invariant_violation_is_isolated` | 落库 I2 冲突 → 主链无碍，`episodes_recorded=0`、`errors=0`（防御性告警不计 error） |
| | `test_memory_on_no_latency_regression` | 宽松守护：Memory 开启不引入 O(n) 延迟爆炸（`t_on < t_off*10 + 0.05`） |

---

## 3. Slice B 外部闭环（`test_memory_closure_slice_b.py`）

用 `CachedDetectionDetector` 重放最小合成 fixture `tests/fixtures/detections/stranger_visit_short.detections.json`（**诚实标注 synthetic**：bbox/confidence 恒定，仅保真检测缓存 schema；不冒充真实 YOLO 输出）。走真实 `tracker→event_builder→rule→decision→memory` 代码路径。

| 场景 | 用例 | 验收要点 |
|---|---|---|
| **场景 1 Contract E2E** | `test_cached_detection_enters_memory_and_is_traceable` | 真实风险链路产 RAISED；离场落 1 episode；`source_event_ids[0]==VisitorEvent.event_id`（可溯源）；`duration≈15min`、`risk=HIGH`、`action=ESCALATE_COMMUNITY`；跨切片收口：`compose_context` 能答"为什么报警" |
| **场景 4 Lifecycle** | `test_episode_spans_full_visit_and_aggregates_max_risk` | 覆盖完整在场窗口（`duration≥600`）；不截断在风险解除点；保留全窗口 max risk（HIGH）；聚合全部 action。诚实边界：本 fixture 无"风险回落相位"，固化的是机制 |
| **场景 3 失败隔离** | `test_episode_build_failure_is_isolated` | 投影抛异常 → 主链照常，`episodes_recorded=0`、`errors=1` |
| | `test_memory_store_invariant_violation_is_isolated` | 落库 I2 冲突 → 主链无碍，`errors=0` |
| **场景 2 重启恢复** | `test_restart_recovers_risk_state` | 同 E2E-2 真机路径复刻：`visitor_instance_id`/`risk_phase`/`first_seen`/`raised_at` 恢复；`dwell` 重算 |

---

## 4. Slice C Product Closure（`MemoryQuery.compose_context`）

`memory/query.py` 输出 7 字段（V0 边界，§3.6）。Slice B 场景 1 已跨切片收口验证：`compose_context(visitor, enter, leave, as_of=leave)` 返回非空 `reason`/`evidence`/`handling` 且 `current_status∈{IN_PROGRESS, CLEARED}`。

### 4.1 验收样例输出（来自设计稿 §3.6，可由真实 EpisodicRecord 组合生成）

```json
{
  "visitor_instance_id": "visitor-abc-123",
  "current_status": "CLEARED",
  "reason": "18:30 访客进入；停留 15 分钟；风险等级 HIGH；非常规访问时间",
  "evidence": [
    "门口停留 15 分钟（> long_duration 阈值）",
    "非常规访问时间（odd_hour）",
    "风险规则 high_risk_approach 触发"
  ],
  "handling": "ESCALATE_COMMUNITY；动作: SEND_FAMILY_MESSAGE@cmd-01(DONE); ESCALATE_COMMUNITY@cmd-02(DONE)",
  "history": "过去 7 天事件 1 次",
  "source_record_ids": ["ep-visitor-abc-123-<event_id>"]
}
```

**可溯源断言（不可妥协）**：每个业务字段均来自 store 中具体 `EpisodicRecord` 的真实字段——
- `reason` ← `enter_time`/`duration_seconds`/`risk_level` + `reason_summary` 嗅探；
- `evidence` ← `reason_summary`（空则 `summary`）；
- `handling` ← `recommended_action` + `actions[].command_type/command_id/status`；
- `source_record_ids` ← 贡献 episode 的 `record_id`。

> ⚠️ **P3 风险**：`reason` 中"非常规访问时间"靠文本嗅探（`query.py:151-156`，`TODO(review #4)`）。Product Closure 的"可溯源"断言必须覆盖 `reason` 字段本身（设计稿 §7 风险项 #5/#7）。

---

## 5. 回放稳定性（Replay Stability）

- **模块内（§8.8.3）**：固定 ID + baseline 快照，算法升级需人工 diff；跨投递顺序穷举一致。
- **系统级（E2E-3）**：同 Observation Stream 两趟完整 pipeline → 规范化（UUID 归一 + `created_at` 归一）后 `to_dict` 逐字段相等、字节稳定。证明 Memory 输出**确定性、可审计、Agent 输入稳定**。
- **规范化语义**：两次回放唯一差异是 UUID 类每趟随机标识符 + `created_at` 墙钟；内容（时间/风险/原因/动作/来源关联结构）必然一致。

---

## 6. 信息损失评估（Information Loss）

Raw 事件 → 1 条 Episode 的关键字段保持（Slice C §1）：

| 维度 | 保留 | 溯源 |
|---|---|---|
| 谁 | `visitor_instance_id`（v1） | — |
| 何时 | `enter_time`/`leave_time`/`duration_seconds` | — |
| 风险 | `risk_level`/`reason_summary` | ← WarningEvent |
| 处理 | `recommended_action`/`actions[]` | ← ActionCommand |
| 证据 | `source_event_ids`（I4 非空） | ← VisitorEvent/Warning/Action id |
| 可信 | `memory_status`/`model_version` | — |

**损失边界（诚实）**：
- `evidence_refs` v1 恒空（ADR-0022 未落地）→ 证据靠 `source_event_ids` 文本承载，非结构化证据项；
- `person_identity_id` v1 恒 None（ADR-0023）→ 跨会话不关联；
- 逐帧细节（bbox/轨迹/分数）**不进 Memory**（按设计压缩，非损失）；
- `dwell_seconds`/`risk_score` 等易变态不进 Snapshot（重启重算）。

---

## 7. 两级测试总结

| 测试 | 验什么 | 不验什么 |
|---|---|---|
| **Contract E2E（CI）** | 真实检测缓存 → tracker → event → memory；事件进入 Memory；可溯源 | 检测精度（YOLO 准不准） |
| **Production Demo（真机）** | 完整 `camera→YOLO→tracker` 整链真实可运行 | 不作为 CI 门禁 |

> Memory 验收目标是"链路真实可运行 + 事件进 Memory"，**不是**"框得准不准"。模型升级只动 Demo，不拖垮 Memory 测试（设计稿 §3.1）。

---

## 8. 结论

- ✅ 压缩比 ≥100:1（实测 1000:1），存储 O(visitor)；
- ✅ 信息保留完整（风险/处理/证据字段成组一致，无半成品）；
- ✅ 回放稳定（模块内 baseline + 系统级规范化逐字段相等）；
- ✅ 失败隔离（投影/落库异常 `errors++`，I2 冲突不计 error，主链不崩）；
- ✅ 边界守护（开/关 memory，决策逐帧一致）；
- ✅ Product Closure（`compose_context` 产出可审计、可溯源的"为什么报警" JSON）。
