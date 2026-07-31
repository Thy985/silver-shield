# Memory Integration Closure — 设计文档

> **阶段定位**：银龄盾「System × Memory 外部闭环」阶段
> **依赖**：ADR-0024（Memory 架构）、`docs/DESIGN-memory-pipeline.md`（Memory 内部设计）、`tests/runtime/test_memory_e2e_closed_loop.py`（已落地的 E2E 内部闭环验收）
> **状态**：设计稿 v3（评审反馈迭代中 → 拆 Slice 实现；Slice C 已在 PR #91 先行）
> **约定**：本文档中的 `file:line` 均指 `main` 当前代码，用于锚定"现状已满足 / 待补"的判断。
> **v2 修订**：① 多模态接口泛化**移出本阶段**（只出未来契约文档，不改当前代码）；② 新增 **Product Closure（用户价值验收）** 作为第一优先；③ 真实数据改用最小 committed fixture，不引入 CAVIAR。
> **v3 修订（评审反馈）**：① **Detector 不是 Memory Closure 的核心验收条件**——验收目标是"真实系统事件能否进入 Memory"，不是"YOLO 准不准"；测试明确两级：**Contract E2E（CI，cached detection 驱动整链，torch-free 可重放）** vs **Production Demo（真机 camera→YOLO→tracker，人工验证）**，模型升级只动 Demo、不拖垮 Memory 测试。② **Slice 优先级重排：B → C → A → D**（B 先证"系统存在" → C 证"价值" → A 代码整理 → D 文档冻结）；A 仅代码组织、非价值交付，降到最后。③ **compose_context 冻结 V0**：仅 `{Current Status, Episode History, Evidence, Action}`，明确禁止 Inference/Prediction/Recommendation，防止 Memory 侵入 Reasoning。

---

## 0. 一句话定位

**Memory 内部闭环已完成**（ADR-0024 Slice 1–6 + Stage F Shadow Mode + E2E 4 类 7 用例）。本阶段不做新的 Memory 功能（Semantic 聚合 Stage G/H、Audio、接口重构均不在范围内），而是证明：

> **Memory 作为系统基础设施，已真正融入银龄盾整体架构，并且能产生可审计的用户价值。**

把验证从「Memory 模块内部正确」提升到「整个风险链路经过 Memory 后仍然正确，且未来系统能消费 Memory 回答用户的问题」。

符号化：

```
内部闭环（已完成）           外部闭环（本阶段）
Vision/Audio  ─┐            Vision/Audio  ─┐
              ├─ Memory ──→               ├─ Risk ──→ Decision ──→ Action ──→ Memory ⇄ 未来解释/恢复
Risk ─────────┘            (外挂模块)                    ↑___________________|
```

外部闭环的本质：**Memory 不再是旁路外挂，而是链路中可被"写入—读出—解释"的一环**；而"解释"这一步，本阶段用一份**无需 Agent / 无需 LLM** 的 `compose_context()` JSON 来证明。

---

## 1. 为什么需要 Integration Closure

现状（内部闭环完成后）：

```
Vision
  → BehaviorState → RiskStateMachine → RiskSignal → Memory
```

Memory 已经很好（record 完整、不变量强、失败隔离、冷启动恢复、回放稳定）。但系统**尚未证明两件事**：

1. **链路真实可运行**：一个真实风险事件发生后，Memory 是否从真实生产数据（而非 `MemoryPolicy(input=test_data)`）沉淀了信息？
2. **产生用户价值**：未来系统/用户问"昨天为什么报警"，Memory 是否留下了**足够且可消费**的信息来回答？

用户场景（Integration Closure 的验收锚点）：

```
18:30  陌生访客出现
18:35  停留异常
18:40  风险升级
18:45  离开
```

当前 Memory 能生成：

```
Episode: 陌生访客异常停留 15 分钟 / 风险 HIGH / 已通知家属
```

完整系统还应能回答第二天的问题——**"昨天为什么晚上报警？"**：

```
因为：
  18:30 检测到陌生访客
证据：
  - 门口停留 15 分钟
  - 非常规访问时间
  - 历史环境模式异常
风险：HIGH
处理：通知家属
历史：过去 7 天类似事件 2 次
```

这就是 **Integration Closure**：感知 → 理解 → 决策 → 行动 → 沉淀 → 解释/恢复，全链路真实可运行，且**沉淀下来的东西能被消费出价值**。

---

## 2. 当前集成状态核实（代码事实，关键）

> 本节是设计的事实基础——先核对代码现状，再谈"补什么"，避免凭空设计。

### 2.1 Memory Hook 在 runtime 的接线（已实现）

`src/home_perception/runtime/pipeline.py`：

- **接入点**：`process_frame` 在 `for ev in events` 循环内，对每个 `VisitorEvent` 调 `_record_episode`，受 `episodic_shadow` flag 门控（pipeline.py L564–568）。
- **容错隔离**：`_record_episode`（pipeline.py L491–532）
  - 投影异常 → `metrics.errors += 1` + 记日志，跳过本 episode；
  - `InvariantViolationError`（I2 单调冲突）→ 仅 `log.warning`，**不计入 errors**（不崩溃主链路）；
  - 落库未知异常 → `errors += 1` + 记日志。
- **Shadow Mode 契约**：只投影、不接决策、不产 Warning（`episodic_shadow=True` 不改变流水线任何历史行为）。

### 2.2 Episode 触发时机 = Visitor Leave（**已实现，与用户设计一致**）

关键发现：`MemoryPolicy.project_episode` 的 docstring 写"触发时机：VisitorEvent 生成（访客离场）"，但**代码本身不按事件类型过滤**（episode_builder.py L90–135，直接读 `enter_time/leave_time/duration_seconds`）。真正决定时机的，是 `event_builder` 何时产出 `VisitorEvent`：

`src/home_perception/analysis/event_builder.py`：

- **`update()` 仅在 track 从 `active` → `left`（访客离场）时生成 `VisitorEvent`**（L116–147）；
- **不**为 enter / dwell / 风险升降生成事件；
- `_build_event`（L176–190）校验 `enter_time`/`leave_time` 必存在才构造。

**结论**：当前 Episode 触发时机**就是「Visitor Leave」，而非「RiskSignal CLEARED」**——与 Lifecycle Closure 设计完全一致。本阶段只需**测试固化**这一行为（见 Slice B 场景 4），无需改代码。

> 设计含义：风险解除 ≠ 访问结束。例：陌生访客 → 风险下降 → 继续聊天 → 离开，Episode 应记录**完整访问**（聚合 enter→leave 全窗口内的 max risk + 全部 action）。当前 `_filter_warnings` 时间窗 `[enter, leave+60s]` + `_pick_max_risk`（episode_builder.py L151–184 / L242–258）已支持此语义。

### 2.3 失败隔离（已实现 + 已测）

- Memory Store 异常 → 主链路（Camera/Risk/Warning）不受影响（pipeline.py L520–531）。
- 已有 E2E 覆盖：`test_memory_episode_build_failure_is_isolated` / `test_memory_store_invariant_violation_is_isolated`（test_memory_e2e_closed_loop.py，E2E-4）。

### 2.4 冷启动恢复（已实现 + 已测）

- `SnapshotStore` 持久化 reconstructable 字段，`ColdStartCoordinator.recover()` 在 `__init__` 重建（TD-0027）。
- 已有 E2E 覆盖：`test_restart_recovers_risk_state_and_recomputes_dwell`（E2E-2）。
- 关键不变量：快照**不**持久化 `dwell_seconds` / `risk_score`，重启后由 `now - first_seen` 重算。

### 2.5 决策–Memory 边界（已实现 + 已测）

- `episodic_shadow=True` 只记录、不接 `DecisionPolicy`、不产 `WarningEvent`。
- 已有 E2E 覆盖：`test_memory_on_is_true_bypass_no_risk_change`（E2E-4）——memory 开/关，warnings/risk_signals/commands/behavior_states 逐帧一致。

### 2.6 现状 vs 待补（差距表）

| # | 用户提出的能力 | 代码现状 | 本阶段动作 |
|---|---|---|---|
| 1 | 数据流闭环（真实生产数据） | E2E 用 `StubDetector` 绕过 detection/tracking | **待补**：走真实 detector→tracker→event_builder（用最小 fixture + 缓存检测，Slice B 场景 1） |
| 2 | 生命周期闭环（Episode=Visitor Leave） | **已实现**（event_builder 仅离场产事件） | 仅测试固化（Slice B 场景 4） |
| 3 | 决策–Memory 边界（ON/OFF 一致） | **已实现 + E2E-4 已测** | 扩大到系统级（Slice B/C） |
| 4 | Agent 消费接口准备 | `EpisodicRecord` 含 time_range/risk/actions/evidence，但**无组合查询层** | **待补**：`compose_context()` 原型（Slice C，兼作 Product Closure） |
| 5 | 多模态扩展接口就绪 | `MemoryPolicy.project_episode(visitor_event: VisitorEvent, …)` **强耦合视觉** | **本阶段不改代码**；只出未来契约文档 `docs/DESIGN-observation-contract.md`（Slice D） |

> **重要纪律（v2 新增）**：#5 的"接口泛化"**明确移出本阶段**。原因——当前链路 `VisitorEvent → Episode → Memory` 已稳定，为未来 Audio 提前改 `MemoryPolicy` ABC / `EpisodeBuilder` / 既有测试，属于"为未来重构现在稳定部分"，风险高、收益晚。**本阶段定义未来接口 ≠ 现在重构接口**。

---

## 3. 六大 Closure 问题与对策

### 3.1 Data Flow Closure（数据流闭环）
**问**：Memory 消费的数据是真实生产的，还是 `MemoryPolicy(input=test_data)`？
**答（设计）**：至少 1 个走 **detector→tracker→event_builder→rule→decision→memory** 的场景（非 StubDetector）。**但验收目标必须说清：Memory Closure 验证的是"真实系统事件能否进入 Memory"，而不是"检测器准不准"。** detector 只是链路上一个**可替换**环节；它的精度由 Detection 团队 / 模型迭代负责，不应成为 Memory 测试挂不挂的开关。

**两级测试策略（v3 新增，关键）**：
- **Contract E2E（CI 跑，Memory 的主验收）**：用**缓存检测结果**驱动 `tracker → event_builder → rule → decision → memory`，跳过 YOLO 推理。验收：缓存检测 → 风险事件 → EpisodicRecord，且 `source_event_ids` 能追溯到真实 visitor/warning/action。**torch-free、确定性、可重放**——这是 Memory Closure 的硬验收，与模型无关。
- **Production Demo（真机 / 人工验证，不进 CI）**：`camera → YOLO → tracker → …` 完整路径，由人在真机 / 演示环境跑通，**验证"整条生产链路真实可运行"**，但不作为 CI 门禁。
- **为什么这样分**：否则模型一升级（YOLO 换版 / 调参），Memory 测试跟着全挂——痛苦且毫无意义。Memory 只关心"事件有没有进来"，不关心"框得准不准"。

真实输入的最小形态见 §3.7。

### 3.2 Lifecycle Closure（生命周期闭环）
**问**：Episode 何时生成？
**答（已确认）**：Visitor Leave（event_builder 仅离场产事件）。需固化场景：风险下降后继续聊天再离开 → episode 仍记完整访问（不截断在风险解除点）。

### 3.3 Decision–Memory 边界验证
**问**：Memory 会不会腐化为隐形决策源（"这人以前危险所以报警"）？
**答（设计）**：在系统级复测 `Memory OFF` vs `Memory ON` 决策输出逐字段一致，唯一差异是多出 `MemoryRecord`。沿用 E2E-4 思路，放大到完整系统（含实时旁路 + 决策接入）。常态化回归守护此边界。

### 3.4 Agent 消费接口准备（schema 就绪性）
**问**：Memory 产出是否适合 Agent？Agent 需要的是上下文，不是 JSON 堆。
**答（设计）**：验证 `EpisodicRecord` schema 能否支撑 Agent 所需四件套——当前状态 / 历史事件 / 证据链 / 行动记录。本阶段设计轻量组合查询接口（非 Agent 实现），例如 `MemoryQuery.compose_context(visitor, window)`。只验证 schema 足够，不实现推理。

### 3.5 多模态扩展接口（**未来契约文档，本阶段不改代码**）
**问**：视觉与音频是否汇到同一个 Memory？
**答（v2 修订）**：目标一致——Memory 只关心"发生了什么值得记住"，不关心来自摄像头还是麦克风。但**当前 `project_episode(visitor_event: VisitorEvent)` 强耦合视觉，是稳定代码，本阶段不动**。本阶段只做一件事：

> 新增 **`docs/DESIGN-observation-contract.md`**——描述未来模态无关的 `Observation` 协议：
> ```
> Observation {
>   modality:   vision | audio | text
>   timestamp
>   subject     # 访客/说话人实例
>   evidence    # 模态特有的证据（框/关键词/文本片段）
> }
> ```
> 并约定"下一阶段 Multimodal Evidence Fusion 如何把 `Observation` 收敛成可喂给 `project_episode` 的事件"。**当前代码保持 `VisitorEvent` 不变、不新增 Audio 实现、不触碰 `EpisodicRecord` 不变量（I1–I4）。**

### 3.6 Product Closure（用户价值验收）★ 本阶段最高优先
**问**：银龄盾最终不是卖 Memory。Memory 到底有没有产生价值？
**答（设计）**：用一个**无需 Agent、无需 LLM** 的 `compose_context()` 调用，证明 Memory 能回答用户最朴素的问题——"昨天为什么报警？"。这是 Integration Closure 的**价值收口**，比 schema 测试更重要。

验收场景（"为什么报警"）：

```
输入：compose_context(visitor=陌生访客, window=过去 7 天)
输出 JSON：
{
  "current_status": "CLEARED",
  "reason": "18:30 检测到陌生访客，停留 15 分钟，非常规时间访问",
  "evidence": [
    "门口停留 15 分钟（> long_duration 阈值）",
    "非常规访问时间（odd_hour）",
    "风险规则 high_risk_approach 触发"
  ],
  "handling": "18:40 通知家属（ESCALATE_COMMUNITY）",
  "history": "过去 7 天类似事件 2 次"
}
```

判定标准：该 JSON 能**由 Memory 存储的真实 EpisodicRecord + WarningEvent 组合生成**，且字段来源可追溯到具体 record/warning（信息不靠硬编码，靠 Memory 沉淀）。这就是"Memory 真的产生价值"的硬证明。

**compose_context V0 边界（v3 冻结，防范围膨胀）**：`compose_context()` 只输出**四类、且全部可由 Memory 存储的真实记录组合生成**：
- **Current Status**（当前 / 回放时间点状态）
- **Episode History**（窗口内事件汇总）
- **Evidence**（证据链，可溯源到 record/warning）
- **Action**（处理 / 行动记录）

**明确禁止**以下范畴进入 V0（否则 Memory 会越过边界、侵入 Reasoning）：
- ❌ Inference（推断用户意图 / 画像）
- ❌ Prediction（风险预测 / 趋势外推）
- ❌ Recommendation（建议家属怎么做 / 下一步动作）

用户画像、行为趋势、风险预测、诈骗模式、家属关系、环境变化等——**全部不在 V0**。若未来确实需要，须单独立项 Memory v2 / Agent Reasoning，走新 ADR，不在此处膨胀。

### 3.7 真实数据来源（最小 fixture，不引入 CAVIAR）
**问**：Data Flow Closure 要真实 detector 流程，数据从哪来？
**答（设计）**：**不引入大体积 CAVIAR 帧**（untracked 大文件）。优先级方案：

- **首选**：新增 committed 小 fixture `tests/fixtures/video/stranger_visit_short.mp4`（几十秒即可，**目标不是检测精度**，而是跑通 `video → detector → tracker → event → memory`）。
- **更省 CI**：用「视频 + 已验证检测缓存」组合——`tests/fixtures/video/stranger_visit_short.mp4` + `tests/fixtures/detections/stranger_visit_short.detections.json`（预先跑过 YOLO+ByteTrack 的结果）。CI 用缓存检测结果驱动 tracker/event_builder，**避免模型推理拖死 CI**；真机/本地再跑完整 detect 路径验证。

> 这条与 §3.1 一致：**Memory 验收目标是"链路真实可运行 + 事件进入 Memory"，不是"检测更准"**。fixture 小、可提交、torch-free 也能跑通整链；cached detection 构成 Contract E2E（CI），完整 `camera→YOLO→tracker` 路径留作 Production Demo（真机 / 人工，不进 CI）。

---

## 4. 四个 Slice（实现顺序：B → C → A → D）

> 命名空间 `Slice A–D` 区别于 ADR-0024 内部的 `Slice 1–6`（Memory 内部）。
> **v3 实现顺序调整**：原 A（Runtime Integration）优先级过高——它功能已基本存在（Stage F 的 `_record_episode`），只是代码组织问题、**非价值交付**。故重排为 **B → C → A → D**：
> - **B 第一**：真实闭环，**证明系统存在**（链路真实跑通、事件进 Memory）；
> - **C 第二**：Product Closure，**证明价值**（Memory 能答"为什么报警"）；
> - **A 第三**：代码整理（Memory Hook 结构化），**最后做**，避免"重构很舒服但产品没前进"；
> - **D 第四**：文档冻结。
> 注：C 已在 PR #91 先行实现（用户拍板"保持回放语义"），故当前实际剩余顺序 **B → A → D**。

### Slice B — Closed Loop Scenario Test（闭环场景测试）★ 实现第一优先

系统级场景（torch-free 优先；真实输入走两级测试，见 §3.1 / §3.7）：

| 场景 | 输入 | 输出 | 验收重点 |
|---|---|---|---|
| **场景 1 陌生访客踩点** | enter→dwell→leave（**真实 detector 路径**：最小 fixture `stranger_visit_short.mp4` + 缓存检测；CI 用 cached detection，真机用完整 YOLO） | WarningEvent + EpisodeRecord | 事件 ID 一致（`source_event_ids` 引用 warning/action）；链路真实跑通；**验证"事件进入 Memory"，不验证检测精度** |
| **场景 2 重启恢复** | ACTIVE_RISK → 程序关闭 → 启动 | 恢复状态 | 风险状态连续（沿用 E2E-2，本场景用真实输入复刻） |
| **场景 3 异常失败隔离** | Memory Store 挂 | Camera/Risk/Warning 正常 + error log | 主链不受影响（沿用 E2E-4，系统级复刻） |
| **场景 4 风险下降仍记完整访问** | 陌生访客→风险降→继续聊天→离开 | 1 条完整 Episode | **固化 Lifecycle Closure**：episode 不截断在风险解除点，聚合全窗口 max risk + 全部 action |

**注（Data Flow Closure 真实输入 / 两级测试）**：场景 1 在 CI 用 **cached detection** 驱动整链（Contract E2E，torch-free、确定性、可重放）；完整 `camera→YOLO→tracker` 路径留作 **Production Demo**（真机 / 人工，不进 CI）。fixture（`stranger_visit_short.mp4` + `stranger_visit_short.detections.json`）进 `tests/fixtures/`（小体积、可提交）。此 fixture 在 Slice B 开工时建立。

### Slice C — Memory Evaluation + Product Closure（评估 + 用户价值验收）
（已在 PR #91 实现 `MemoryQuery.compose_context`，用户拍板"保持回放语义"；此处保留设计为冻结边界）

在 ADR-0024 Slice 6 评估基础上扩展，并把 **Product Closure（§3.6）作为硬交付**：

1. **信息损失评估（系统级）**：Raw Event → 1 条 Episode，关键字段保持：`visitor / time / risk / action / evidence`；补"跨阶段信息溯源"——从 Episode 反查 WarningEvent / ActionCommand。
2. **Replay 稳定性（版本矩阵）**：不同 Memory 版本同输入结果一致。
3. **用户价值验收 / Product Closure（核心）**：`MemoryQuery.compose_context(visitor, window)` 产出 §3.6 的 JSON（**V0 边界**，仅 Current Status + Episode History + Evidence + Action）。
   **验收（不可妥协）**：JSON 必须由真实存储的 `EpisodicRecord` + `WarningEvent` 组合生成，字段可溯源、可重放。

> **V0 边界（防膨胀）**：见 §3.6。compose_context 不输出 Inference / Prediction / Recommendation。

### Slice A — Memory Runtime Integration（运行时集成结构化）★ 实现第三优先（代码整理，非价值交付）

**目标**：Memory 在 runtime 中从"内联 `if episodic_shadow: _record_episode(...)`"整理为清晰的 **Memory Hook** 结构（Stage F 已能跑，本 Slice 仅结构化）：

```
Observation Stream
      │
  Risk Pipeline（detector→tracker→event_builder→rule→decision→action）
      │
  Memory Hook（门控：episodic_shadow / memory.enabled）
      │
  Memory Policy（project_episode，签名不变）
      │
  Memory Store
```

**落点**：
- `pipeline.py` 抽出 `MemoryHook` 封装（当前内联逻辑原样搬迁，0 行为变化）；
- 明确接线契约（输入 `VisitorEvent + warnings + actions`，输出 `episodes_recorded` / `errors`）。
- **纪律**：`MemoryPolicy.project_episode` / `EpisodicRecord` / `VisitorEvent` 签名一律不动。

**验收**：
- `memory.enabled=False` 完全不影响主链；
- Memory 异常不影响风险判断（沿用 E2E-4）；
- latency 增量可控（旁路 O(n)）。

**说明**：功能已基本存在（Stage F），重点是结构整理 + 文档化接线契约，**不是新功能、不改接口、不交付价值**——故排到最后。

### Slice D — Documentation Freeze（文档冻结，不重构接口）

形成（落在 `docs/`，与 `DESIGN-memory-pipeline.md` 并列）：

- `MEMORY_ARCHITECTURE.md`：架构图 + 生命周期（Visitor Enter→Active→Risk Raised→Warning→Action→Leave→Episode Closed）+ 接线契约（门控、输入/输出、失败隔离语义）。
- `MEMORY_OPERATION_GUIDE.md`：开关（`memory.enabled` / `episodic_shadow`）、冷启动恢复操作、失败隔离语义、已知限制、Decision–Memory 边界守护说明。
- `MEMORY_TEST_REPORT.md`：测试结果汇总（E2E 4 类 + Slice B/C 新增）、回放稳定性、信息损失评估、**Product Closure 验收样例输出**。
- **`DESIGN-observation-contract.md`（未来契约文档，v2 核心修订）**：描述未来模态无关 `Observation` 协议与 Multimodal Evidence Fusion 接入方式。**明确标注"本阶段不改动 `VisitorEvent` / `MemoryPolicy` / `EpisodeBuilder` / `EpisodicRecord`"**，作为下一阶段的契约起点，而非现在的重构任务。
- ADR-0024 标注「Integration Closure 完成」。

> **纪律**：Slice D 产出的全是**文档**，不包含任何代码改动；接口泛化（如有必要）留待 Multimodal 阶段按此契约实施。

---

## 5. Definition of Done（完成标准）

| 维度 | 标准 | 现状 |
|---|---|---|
| 架构 | Memory 接入 runtime、不影响主链、数据流闭环 | 部分已满足（运行时接入✅ / 数据流闭环真实输入⚠️待 Slice B） |
| 测试 | ≥3 端到端场景 + crash recovery + replay 稳定 + failure isolation | 部分已测（E2E 已含；补真实输入场景 + 场景 4） |
| **产品价值 ★** | **能答"为什么今天报警"**：`compose_context()` 输出 `current_status + reason + evidence + handling + history`，且字段可溯源、可重放 | **⚠️ 需 Slice C（最高优先）** |
| 文档 | ADR 冻结 + 工程方案 + 测试报告 + 未来契约文档 | ⚠️ 需 Slice D（纯文档，不重构接口） |

**完成标志**：以上全绿，且 `git` 走正常 PR 流程合入（`branch + commit + gh pr create + review + merge`，沙箱已恢复正常）。

---

## 6. 后续路线（不做 Agent）

```
Memory Integration Closure  ← 本阶段（含 Product Closure，不含接口重构）
        ↓
Multimodal Evidence Fusion   （视觉+音频证据融合；按 Slice D 的 observation-contract 契约接入）
        ↓
Audio Pipeline
        ↓
Agent Context Layer          （真正消费 Slice C 的 MemoryQuery.compose_context）
        ↓
Agent Reasoning
```

**顺序铁律**：先证明"系统记住过去"（本阶段），再证明"系统理解更多证据"（Multimodal/Audio），最后才"系统解释与协助决策"（Agent）。**本阶段不做 Agent、不重构多模态接口。**

---

## 7. 风险与开放问题

1. **真实输入 fixture**：Data Flow Closure 依赖走真实 detector 的场景，但**不引入 CAVIAR**。方案：`tests/fixtures/video/stranger_visit_short.mp4`（几十秒）+ 可选 `detections.json` 缓存（§3.7）。fixture 体积与内容在 Slice B 开工时定。
2. **多模态接口泛化已移出本阶段**：`MemoryPolicy.project_episode(visitor_event: VisitorEvent, …)` 强耦合视觉，但当前链路稳定，**本阶段不动**。只在 Slice D 出未来契约文档。若 Multimodal 阶段确需重构，按该契约 + 新 ADR 走 Owner 评审，不现在动。
3. **真实视频场景测试耗时/torch 依赖**：CI 合约（torch-free）外，用缓存检测（§3.7）规避模型推理；真机/本地再跑完整 detect 路径。参考既有 `tests/runtime` torch-free 约定 + `scripts/e2e_validate_demo.py` 真实集成路径。
4. **Memory 腐化守护**：Decision–Memory 边界需常态化回归（E2E-4 思路），防止 Memory 静默变成决策源。
5. **Product Closure 的"可溯源"断言**：Slice C 须保证 `compose_context()` 输出每字段能反查到 store 中的具体 `EpisodicRecord` / `WarningEvent`，避免"为了好看而硬编码"——这是 Product Closure 不被架空的关键。
6. **compose_context 范围膨胀（v3 冻结 V0）**：`compose_context()` 严禁滑入 Inference / Prediction / Recommendation（用户画像、行为趋势、风险预测、诈骗模式等）。一旦开始加"推断 / 预测 / 建议"，Memory 就侵入 Reasoning，背离"纯记忆 + 可消费"定位。任何超出 V0 四件套（Current Status / Episode History / Evidence / Action）的字段，须走新 ADR 单独立项，不在此处膨胀。

---

## 8. 参考

- `docs/ADR/0024-memory-architecture.md` — Memory 架构（Slice 1–6 + Stage F）
- `docs/DESIGN-memory-pipeline.md` — Memory 内部设计
- `tests/runtime/test_memory_e2e_closed_loop.py` — 已落地的 E2E 内部闭环（4 类 7 用例，torch-free）
- `src/home_perception/runtime/pipeline.py` — Memory Hook 接线（L491–532, L564–568）
- `src/home_perception/analysis/event_builder.py` — VisitorEvent 仅离场生成（L116–147）
- `src/home_perception/memory/episode_builder.py` — `project_episode`（L90–135）
- `src/home_perception/memory/policy.py` — `MemoryPolicy` ABC（L99–118，`project_episode` 当前耦合 `VisitorEvent`，本阶段不动）
- `scripts/e2e_validate_demo.py` — 真实 WS/HTTP 集成验证路径（参考）
