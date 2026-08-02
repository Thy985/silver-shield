# DESIGN-memory-replay-dataset.md · Memory Consumer 验证数据集（Episode Replay Layer）

- **状态**：Draft（随 ADR-0025 工程化一同落库，待实现 M0）
- **日期**：2026-08-02
- **承接**：`DESIGN-memory-consumer.md` §0.4（数据来源与回放闭环）
- **前置**：ADR-0024（Memory 写入链路 / `MemoryQuery.compose_context`）/ ADR-0025（Consumer 契约）/ ADR-0021（实时风险流）/ ADR-0023（身份连续性）

> **文档职责**：定义 Memory Consumer 的**验证数据**——回答"Consumer 吃什么真实 Memory 数据、如何产生、如何验证 Memory 真改变了理解"。它是 `DESIGN-memory-consumer.md` §0.4 的落地细则，优先级高于 C-1~C-5（M0 先行）。本文件**只定义数据结构与 case 语义**，不实现回放代码（回放 harness 归 M0 实现）。

---

## 0. 为什么需要这份文档

`DESIGN-memory-consumer.md` 解决了"Consumer 怎么落代码、怎么验收"，但最大风险是**数据闭环未定义**：组件设计正确，却没说清 Consumer 消费的真实 Memory 从哪来、怎么产生、怎么证明它改变了理解。

本文件补上这一环。核心原则（Owner review）：

> 写出"很漂亮但无真实记忆证明"的 Consumer，等于没做。验证必须基于**真实 CCTV 回放产生的 Memory**，而不是手写 Mock。

---

## 1. 三阶段验证策略

| 阶段 | 数据来源 | 适用 Slice | 能否证明"Memory 反哺理解" |
| --- | --- | --- | --- |
| **A · 纯 Mock** | 手写 `EpisodicRecord` JSON | C-0 ~ C-2 | ❌ 只证明 Consumer 代码能跑 |
| **B · 录制 CCTV 回放** | 真实 CCTV → Pipeline → Memory → `EpisodicRecord` | C-3 起 + 集成 + Shadow | ✅ 端到端证明 CCTV→Memory 链路有价值 |
| **C · 派生 Case** | 从 B 的真实回放整理出的结构化 case | 回归 / 验收 | ✅ 每个 case 针对一类"Memory 价值" |

- **A 仅限开发期接口联调**：Mock 只能证明接口、提速；不可作为"Consumer 有效"的主证据。
- **B 是主验证**：用录制的真实 CCTV 样本跑完整链路，Consumer 读到的是 Pipeline 真实产出的 Memory。
- **C 是 B 的结构化沉淀**：把 B 的真实回放整理成可版本化、可断言的 case（见 §3），用于回归与验收。

---

## 2. Fixture 结构

测试 fixture 放在 `tests/fixtures/memory_replay/`，按 case 分目录（每个 case = 一组能证明某类 Memory 价值的输入 + 期望 `ReasoningInput`）：

```
tests/fixtures/memory_replay/
  case_001_repeat_visitor/        # 重复访客模式（证明 Memory 把孤立事件连成画像）
    history.json                  # 近 7 天该 visitor_instance_id 的 EpisodicRecord 列表
    current.json                  # 当前触发事件（VisitorEvent / RiskSignal）
    expected_reasoning_input.json # 期望 ReasoningInput（仅上下文，无 score）
  case_002_behavior_escalation/   # 行为升级（证明 Memory 发现 escalation 模式）
    history.json
    current.json
    expected_reasoning_input.json
  case_003_conflict_transparency/ # 冲突透明（验证 C4）
    history.json
    current.json
    expected_reasoning_input.json
  README.md                       # 每个 case 的"证明什么"说明 + 数据来源（来自哪段真实 CCTV）
```

字段约定：

- `history.json`：`list[EpisodicRecord]`，直接复用 ADR-0024 已落库的 `EpisodicRecord` schema（含 `source_event_ids` / `evidence_refs`，满足 C5）。
- `current.json`：`CurrentEvent`（`VisitorEvent` 或 `RiskSignal`）。
- `expected_reasoning_input.json`：`ReasoningInput` 快照，**硬约束不含 `risk_score` / `decision` / `warning`**（C1）；用于回放断言"同输入同输出"。

> 这些 fixture 在 **M0** 由 Episode Replay Layer 从真实 CCTV 回放**生成 / 校准**（不手工臆造）。文档此处给出结构与期望值语义；实际数值在实现期从回放结果填充并冻结为 baseline。

---

## 3. Case 设计（不要随机数据，要能证明 Memory 价值）

### Case 1 · 重复访客模式（Repeat Visitor）

**数据来源**：同一 `visitor_instance_id`（或落地后的 `person_identity_id`）在 7 天内多次夜间到访的真实回放。

**历史（history.json）**：

```
Day1: 陌生人 22:00 停留 5 分钟
Day2: 陌生人 22:00 停留 8 分钟
Day3: 陌生人 21:30 停留 6 分钟
Day5: 陌生人 22:30 停留 4 分钟
Day7: 陌生人 22:00 停留 9 分钟
```

**当前（current.json）**：Day7 之后的又一次夜间到访事件。

**无 Memory 时系统看到**：两个孤立事件（Day1 / Day2），无法关联。

**有 Memory Consumer 时 `expected_reasoning_input.json`**：

```json
{
  "current_event": { "visitor_instance_id": "A001", "type": "door_visit", "time": "22:10" },
  "historical_context": [ { "day": 1, "time": "22:00", "duration_s": 300 }, ... ],
  "visitor_profile": {
    "visit_count": 5,
    "night_visit_ratio": 1.0,
    "confidence": "stable_pattern"
  },
  "risk_pattern": null,
  "evidence_refs": [ ... ],
  "previous_actions": [ ... ],
  "conflicts": []
}
```

**证明**：Memory 把 5 次孤立事件连成 `visit_count=5 / night_visit_ratio=1.0` 的画像——系统从"看到一次"升级为"理解这是规律夜间访客"。注意输出是**画像字段 + confidence，不是 score**（C1）。

---

### Case 2 · 行为升级（Behavior Escalation）

**数据来源**：同一访客连续多日行为强度递增的真实回放。

**历史（history.json）**：

```
Day1: 门口观察（observe）
Day2: 门口停留（dwell）
Day3: 携带物品 + 频繁观察摄像头（carry + observe_camera）
```

**当前（current.json）**：Day3 事件触发。

**`expected_reasoning_input.json` 的 `risk_pattern`**：

```json
{
  "risk_pattern": {
    "tags": [ "repeated_visit", "escalating_behavior" ],
    "escalation_history": [ "observe", "dwell", "carry+observe_camera" ],
    "confidence": "weak_pattern"
  }
}
```

**证明**：Memory 发现"行为逐日升级"的模式（`escalating_behavior`），这是单看当前事件得不到的上下文。

**反模式**：绝对不能输出 `risk_score=0.8`——那是决策分数，违反 ADR-0025 C1 与"Memory 不直接改 Risk Score"边界。`risk_pattern` 是**模式描述，非分数**。

---

### Case 3 · 冲突透明（Conflict Transparency，验证 C4）

**数据来源**：历史为正常访客、当前出现异动（异常时间 + 异常行为）的真实回放。

**历史（history.json）**：长期正常访客（白天、短时、无异常行为）。

**当前（current.json）**：异常时间（深夜）+ 异常行为（反复观察摄像头）。

**`expected_reasoning_input.json` 的 `conflicts`**：

```json
{
  "conflicts": [
    {
      "type": "behavior_shift",
      "historical": "normal",
      "current": "abnormal",
      "detail": "historical day_ratio=1.0 vs current night+observe_camera"
    }
  ]
}
```

**证明（C4）**：Consumer **不解决、不覆盖**历史——把"历史正常 vs 当前异常"作为 `ConflictFlag` 同时交给 Reasoning，由 Reasoning 自行推理（呼应 ADR-0024 §3.8 / ADR-0025 §3.6）。冲突解决策略归未来 Memory Consistency Policy ADR。

---

## 4. Episode Replay Layer 执行（M0 实现）

回放 harness 的职责：把录制的真实 CCTV 样本喂进完整链路，**重产** `EpisodicRecord`，供 Consumer 在真实 Memory 上验证。

```
真实 CCTV 样本（config/demo/scenarios/* 或 data/ 录制）
   │  Perception Pipeline（检测/跟踪/行为/风险）—— 复用现有 runtime
   ▼
MemoryHook（PR#94，已合）
   │  Episode Builder（ADR-0024 Slice 1-3）
   ▼
Memory Store（落库 EpisodicRecord）
   │  MemoryQuery.compose_context（ADR-0024 Slice C）
   ▼
MemoryConsumer.Retrieval → Aggregation → Context Builder
   ▼
ReasoningInput
   │
   ▼
断言 ≈ expected_reasoning_input.json（同输入同输出，C3）
```

执行要点：

- **复用不新建**：回放直接复用现有 `PerceptionPipeline` + `MemoryHook` + `MemoryQuery.compose_context`，不另写一套"造数据"的逻辑。
- **可复现**：同一 CCTV 样本两次回放，产出的 `EpisodicRecord` 与 `ReasoningInput` 必须一致（C3 确定性）。
- **不污染生产 Memory**：回放用独立 Memory Store 实例 / 测试库，不写入生产证据或生产 Memory。

---

## 5. 验收标准（每个 case 必须证明"Memory 改变了理解"）

| Case | 验收断言（核心） |
| --- | --- |
| case_001 | 无 Memory 时当前事件被当作孤立事件；有 Consumer 时 `visitor_profile.visit_count >= 2` 且 `night_visit_ratio` 被填充——证明"关联历史"发生 |
| case_002 | `risk_pattern.tags` 含 `escalating_behavior`；且**不含**任何 `risk_score` 字段（C1） |
| case_003 | `conflicts` 非空、且同时保留 `historical` 与 `current`（C4）；Consumer 未修改历史记录 |

通用不变量（所有 case 必过）：C1 无 score / C2 只读（Memory 写入计数不变）/ C3 确定性 / C5 每项历史带 `source_event_ids`。

---

## 6. 反模式（实现期禁止）

- ❌ **纯 Mock 作为主验证**：手写 JSON 只能验证接口；C-3 起必须用真实回放（B/C 阶段）。
- ❌ **在 `ReasoningInput` 里塞分数**：`risk_score` / `decision` / `warning` 一律禁止（C1，边界见 ADR-0025 §3.9）。
- ❌ **回放写入生产 Memory / 证据**：测试隔离，避免污染。
- ❌ **凭空造 fixture**：`history.json` 必须来自真实回放或由其校准，不可随机生成"看起来合理"的数据。

---

## 7. 与工程推进顺序的关系

本文件 = `DESIGN-memory-consumer.md` §0.5 的 **M0（数据闭环）** 交付物。M0 完成判据：

1. `tests/fixtures/memory_replay/` 含 ≥3 个 case（含上述 001/002/003 或等价）；
2. Episode Replay Layer 能跑通"真实 CCTV → Memory → ReasoningInput"；
3. 至少一个 case 的回放断言通过（证明 Memory 真改变了理解）。

完成后才进入 M1（C-0 骨架）/ M2（C-1 Retrieval 吃真实数据）。
