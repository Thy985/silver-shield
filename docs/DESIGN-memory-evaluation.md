# DESIGN-memory-evaluation.md — Memory Value Evaluation (E-1)

> **状态**：Implementation Ready（2026-08-03 Owner 评审通过；E-1A 可开发，E-1B 待数据治理；Early Detection 指标已定义、计算推迟 E-1B）
> **归属**：ADR-0025 Phase 1 收口后的 Evaluation Gate；位于「Consumer 接入」与「Phase 2 决策增强」之间
> **作者**：silver-shield / Memory Consumer 工作组
> **最后修订**：2026-08-03（三轮评审：6 增强 + 3 冻结点 + 6 项契约修订，见 Appendix A / B / C）

---

## 0. 范围、定位与分层

本设计回答一个问题，且只回答一个问题：

> **Memory 是否真的提高了「理解」（reasoning）质量？**

它是 **Phase 2 决策增强（ADR-0010 放开单点决策边界）之前的强制价值证明 gate**。
在把 `ReasoningResult` 汇入 `DecisionPolicy` 之前，必须先有可证伪证据说明「`Memory → Reasoning` 有效」。
否则直接 `Memory → Decision` 会引入巨大风险：Memory 可能只增加了系统复杂度，却没有提升理解。

**本设计不实现 Phase 2 决策增强**，不触碰 `DecisionPolicy`、不构造 `DecisionRequest`。所有观测停留在 `ReasoningResult` 层（Shadow）。

### 0.1 E-1A / E-1B 分层（关键）

| 阶段 | 状态 | 范围 | 能证明 / 不能证明 |
|------|------|------|-------------------|
| **E-1A** | **Implementation Ready（可开发）** | M0 三 case（case_001/002/003） | 证明 Memory pipeline 让 Reasoning 获得历史信息并改善**预定义案例**中的理解；**不**证明真实环境普遍提升 |
| **E-1B** | 待数据治理 | 20~50 真实 CCTV replay | 统计意义上证明 Memory 提升推理（需 GroundTruth / CI / Bootstrap / paired test） |

- E-1A 本质更接近 **regression test + mechanism validation**，不是 statistical evidence；不可过度解读为「Memory 普遍有用」。
- E-1B 的核心风险是 **Evaluation Dataset Engineering**（非代码）：需标注 event sequence / visitor identity / episode boundary / risk pattern / ground truth detection time，否则无法判定 Memory 好坏。建议作为独立数据治理子任务，先于 E-1B 实现。

---

## 1. 为什么需要 E-1：Memory 四阶段链路

ADR-0024 / ADR-0025 / E-1 构成 Agent 记忆系统的关键三段，分别证明不同命题：

| 阶段 | 文档 | 它回答的问题 |
|------|------|--------------|
| Memory Storage | ADR-0024 | `Can system remember?`（系统能记忆吗） |
| Memory Consumer | ADR-0025 | `Can system consume memory?`（系统能使用记忆吗） |
| **Memory Evaluation** | **E-1（本设计）** | **`Does memory improve intelligence?`（记忆提升智能了吗）** |
| Reasoning Decision Integration | ADR-0026（规划中） | `Does memory improve decision?`（记忆提升决策了吗） |

大量 Memory Agent 项目证明了「有 Memory」，却从未证明「Memory 有用」。E-1 补上最后、也最关键的一步。
演进路径：

```
ADR-0024  Memory Infrastructure
        ↓
ADR-0025  Memory Consumer
        ↓
DESIGN-E1 Memory Evaluation   ← 本设计
        ↓
ADR-0026  Reasoning Decision Integration（规划中）
```

---

## 2. A/B 实验协议（严格变量控制）

### 2.1 核心原则：唯一变量 = HistoricalContext 有无

两臂共享**完全相同**的：同一份 replay 数据集（M0 fixtures）、同一个 `current_event`、同一个 `RuleBasedReasoningEngine`（确定性，C-5 的 C3 不变量保证同输入同输出）。

两臂**唯一差异**在于喂给引擎的 `ReasoningInput` 是否携带历史上下文：

```
Baseline 臂（Memory=off）：
  ReasoningInput { current_event only; historical_context=[], profile=None,
                   pattern=None, conflicts=[], previous_actions=[] }
        ↓
  RuleBasedReasoningEngine.infer
        ↓
  ReasoningResult(B)

Memory 臂（Memory=on）：
  ReasoningInput { current_event + historical_context + profile + pattern
                   + conflicts + previous_actions }   ← 来自 M0 完整链路
        ↓
  同一 RuleBasedReasoningEngine.infer
        ↓
  ReasoningResult(M)
```

> 等价于机器学习控制变量实验：`X = Memory Context`，`Y = Reasoning Capability`，回答「Memory 本身的边际贡献」。
> **反例（禁止）**：Baseline 用普通规则模型、Memory 用 LLM+Memory+新 Prompt —— 提升归因不可分。

### 2.2 Baseline 臂构造方式

Baseline 臂**不是**另一套模型，而是同一引擎喂**空历史** `ReasoningInput`（复用 `MemoryConsumerHook.maybe_consume` 产出，把 `historical_context` 及其衍生字段清零，保留 `current_event` 原样），确保两臂除历史外零差异、可进 CI、可复现。

### 2.3 数据集来源

- **Memory 臂**：M0 replay dataset（case_001 规律夜间访客 / case_002 行为升级 / case_003 冲突透明），其 §5 验收断言已定义「Memory 改变了理解」。
- **Baseline 臂**：复用 `DESIGN-memory-consumer.md` §4.3 规划的消费侧 `ReasoningInput` 基线（Memory=off）。

---

## 3. 复用的数据契约与本次边界

| 契约 | 来源 | E-1 中的角色 |
|------|------|--------------|
| `ReasoningInput` | ADR-0025 C-3 | A/B 两臂输入；唯一变量在其历史字段 |
| `ReasoningResult` | ADR-0025 C-6 | A/B 两臂输出；本设计所有指标的观测对象 |
| `SourceRef` / `source_refs` | ADR-0025 C-6 | Q2 / Q1(a) 历史引用的判定依据（C5 透传） |
| `RECOMMENDED_ACTION_HINTS` | ADR-0025 C-6 | FP/FN 指标中 `suggested_action_hint` 白名单 |
| `DecisionRequest` | ADR-0025（未实现） | **本期不触碰**；Phase 2 才接入 |

**硬边界（守 ADR-0010）**：Consumer / Engine 绝不决策、绝不改 Risk Score。`suggested_action_hint` 仅作 Reasoning 层**行为观察指标**，不代表 Decision 输出（详见 §10）。

---

## 4. 四指标（结构指标，零外部依赖，可 CI）

### 4.1 Explanation Quality（解释质量）

判定 Memory 臂是否给出**有历史依据**的解释。

- **Q1 — Grounded Finding Gain（有效新增发现）**：Memory 臂相对 Baseline 臂**新增**的 finding 必须**同时**满足：
  (a) 其 `source_refs` 至少一条 `source == "historical_context"` 且 `ref` 为某条历史 `EpisodicRecord.record_id`（如 `ep-a001-d1`，前缀 `ep-`，由 C5 透传；注意引擎对历史上下文只锚定 `historical_context[0].record_id`，故 `required_evidence` 中的 record_id 必须取首条历史记录）。
  (b) 命中 `GroundTruthRecord.expected_pattern`（§5）。
  即 `ValidFindingGain = (findings(M) \ findings(B)) ∩ HistoricalGrounded ∩ ExpectedPattern`，且 `|ValidFindingGain| ≥ 1`。
  **仅奖励「有历史依据且命中预期模式」的新增发现**，杜绝「话多式」信息膨胀（Baseline 给 1 条、Memory 给 7 条但无一条历史锚定 → 不通过）。
- **Q2 — 历史引用（强）**：`ReasoningResult(M).source_refs` 至少一条 `source == "historical_context"`（其 `ref` 为历史 `record_id`，C5 透传），即解释指向具体历史事件（而非仅 `current_event`）。
- **Q3 — Pattern Grounding（最强，证据链形式，替代原「关键词命中」）**：从「扫描解释文本」升级为**结构化证据链验证**，不与 C-6 当前 `_explain()` 输出冲突（C-6 解释文本为模板化「结合该访客历史画像、识别到风险模式、召回 N 条历史记录…」，**不嵌入具体数值**）。Q3 通过条件（全部满足）：
  1. `explanation` 非空（契约保证）；
  2. 存在 ≥1 条**历史锚定** `SourceRef`（`source ∈ {visitor_profile, risk_pattern, historical_context, conflicts, previous_actions}`，即非 `current_event`）；
  3. 该历史 `SourceRef` 对应的 `findings` 条目**携带具体历史值**——如 `visitor_profile` 的 `detail` 含 `visit_count=5` / `night_visit_ratio=1.0`，或 `risk_pattern` 的 `ref` 为具体标签（如 `escalating_behavior`），或 `historical_context` 的 `ref` 为具体 `record_id`——且该历史概念在 `explanation` 中被提及（如「历史画像」「风险模式」「历史记录」「冲突」等词，而非空洞的「存在历史行为模式」）；
  4. **反例（不通过）**：仅有 `current_event` 锚点的 finding，或 explanation 仅复述「该访客存在历史行为模式」而无任何可追溯的历史 `SourceRef` / `findings` 值。
  > Q3 验证的是「Memory evidence → SourceRef → findings(值) → explanation(概念)」可追溯链，证明 Memory 真被消费；它衡量 **grounding 机制**（≥1 条历史锚定），与 Q1 衡量「新增有效发现数（Δ）」**正交，不重复计分**。若未来希望 explanation 文本本身携带数值（更强可读性），属 C-6 增强、单独跟踪，**非 E-1A 前置条件**。

> Q3 接近论文实验中的 **evidence grounded reasoning evaluation**：要求 `Memory evidence → finding/source_refs → explanation` 形成可追溯链，证明 Memory 真被消费，而非「看到字段即输出套话」。

### 4.2 False Positive（误报，不恶化约束）

**语义严重度排序（冻结映射，含 `None`）**：

| `suggested_action_hint` | severity |
| ----------------------- | -------: |
| `None`（无提示）        | 0 |
| `MONITOR`               | 1 |
| `NOTIFY_FAMILY`         | 2 |
| `ESCALATE_COMMUNITY`    | 3 |

- **判定改为对照 Ground Truth 可接受范围，而非两臂 hint 差值**：`FP = severity(hint) > severity(max(acceptable_hint))`。Baseline 与 Memory 两臂均须满足 `FP = false`（hint 不超过 `GroundTruthRecord.acceptable_hint` 上限）。
- **为何不再用 `severity(M) ≤ severity(B)`**：当前 C-6 `_hint()` 由 `current_event.risk_level` 主导（HIGH→`ESCALATE_COMMUNITY`、MEDIUM→`NOTIFY_FAMILY`），两臂 `current_event` 完全相同 → hint **恒等**，差值恒为 0，无法区分 Memory 贡献；且当 Memory 本应**新增** advisory hint（如低危重复访客由 `None`→`MONITOR`）时，「≤」会误判为恶化。对照 GT 上限更准确。
- **case_001 校准说明**：原设计写「正确 = 温和 `MONITOR`」与引擎不符——`current.json` 风险等级为 `MEDIUM` → 两臂均得 `NOTIFY_FAMILY`（severity 2）。修正：`case_001.acceptable_hint = ["MONITOR", "NOTIFY_FAMILY"]`，Memory 价值体现在 **Q1/Q2/Q3 的历史画像 grounding**，而非 hint 变化。两臂 hint 均为 `NOTIFY_FAMILY`（≤ 上限 2）→ FP 通过。
- **「无 hint」（`None`）处理**：`None = 0` 为最低严重度，FP 比较中 `None` 不算误报；FN 与 Early Detection 不以 hint 是否 `None` 判定（见 §4.3 / §4.4）。
- 本指标是**不恶化上限**约束，不以「越低越好」计；FP 在 Memory Value Score 中权重最低（见 §8），因安全系统漏报成本 > 误报成本。
- **hint 级边际贡献的局限（fixture 校准提示）**：当前 M0 三 case 的 `current.json` 风险等级为 `MEDIUM`/`HIGH`/`HIGH`，使 hint 两臂塌缩、无法体现 Memory 对 hint 的**增量**。若要在 E-1B 验证「Memory 是否改变 advisory hint」，对应 case 应使用一个**本身不触发目标 hint** 的 `current_event`（如 `risk_level = LOW/None`），使 hint 增量来自 Memory 揭示的模式（如 `escalating_behavior`/`conflicts` → `NOTIFY_FAMILY`）。此校准属 E-1B 数据治理范畴。

### 4.3 False Negative（漏报，核心指标，基于 pattern finding）

Memory 的价值在「提前形成模式」。银龄盾场景诈骗风险通常是**行为序列异常**而非单事件异常。

- **判定基于「预期模式 finding」而非 hint**（避免两臂 hint 恒等导致塌缩）：`FN = |expected_pattern 中未被 Memory 臂 findings 覆盖且经 required_evidence 锚定的条目|`。
  - Baseline 臂无历史 → 这些 pattern finding **全缺** → `FN_B = |expected_pattern|`；
  - Memory 臂补全 → `FN_M` 趋近 0。
- 单条 `expected_pattern` 视为「已检出」须满足**双约束**（§5 `GroundTruthRecord`）：
  (a) `findings` 出现该 pattern 的语义条目（如 `risk_pattern.tags` 含 `escalating_behavior`，或 `conflicts` 含 `behavior_shift` 类型）；
  (b) 该条目有**历史锚定** `SourceRef` 且命中 `required_evidence`（pattern detection + evidence grounding）。
- 约束：`FN_M < FN_B`，理想 `FN_M = 0`。
- **FN 是四指标中权重最高项**（见 §8），反映「漏报比误报更危险」。
- **注**：E-1A 中 hint 两臂恒等，故 FN **不**以 hint 缺失计（hint 缺失 ≠ 漏报）；FN 严格以 pattern finding 缺失计。

### 4.4 Early Detection（提前发现，含时间戳 Lead Time）

> **E-1A 现状（重要）**：当前 M0 三 case 每个 case 仅含**单个 `current_event` + 历史 `EpisodicRecord`**，**没有时序 step 序列**，也**没有每 step 的两臂检测结果**。因此无法从现有输入推导两个不同的检测时刻，Early Detection **无法在 E-1A 计算**。本指标**定义保留**，但**计算推迟到 E-1B**（其拥有真实 CCTV 时序回放）。E-1A 报告中该指标标记为 `N/A（data-gated → E-1B）`，**不计入 E-1A Hard Gate**。

**指标定义（供 E-1B 实现）**：把每个 replay 沿时间轴展开为**有序 step 序列**，定义「检测事件」为：首个 step 满足 `suggested_action_hint ∈ {ESCALATE_COMMUNITY, NOTIFY_FAMILY}` **或** findings 中出现 escalation / conflict 类条目。

- **Step delta（辅助）**：`Δstep = E_step(B) − E_step(M)`，`Δstep ≥ 0` 表示 Memory 臂不晚于 Baseline。
- **Lead Time（主指标，时间戳权威）**：
  ```
  LeadTime = timestamp(B_detection) − timestamp(M_detection)
  ```
  语义：
  - `LeadTime > 0` → Memory 臂更早检测（提前 LeadTime）
  - `LeadTime = 0` → 两臂同时检测（持平）
  - `LeadTime < 0` → Memory 臂更晚检测（退化）
  例：`B=15:00, M=14:30` → `+30min`（提前）；`B=15:00, M=15:20` → `−20min`（退化）。

#### 4.4.1 时序 fixture 契约（E-1B / E-1c 须实现）

为使 Early Detection 可计算，E-1B fixture 须提供有序 step 序列（与 M0 fixture 的「单当前事件」结构不同）：

```
temporal_case/
  steps.json          # 有序事件序列，每个 step：
                      #   { "step": int,
                      #     "timestamp": ISO8601,
                      #     "current_event": CurrentEvent,                 # 该 step 的当前事件
                      #     "reasoning_input": ReasoningInput | null }      # 该 step 的 Memory 臂输入（null=无记忆）
  ground_truth.json   # 含 expected_detection_step / expected_detection_ts（可选）
```

- 两臂检测时刻由**同一 harness** 对每个 step 分别构造 Baseline（清空历史）与 Memory 输入、过同一引擎得到；
- `E_step(B)` / `E_step(M)` = 各自首次检测 step；`timestamp` 取该 step 的 `current_event.occurred_at`；
- **缺失检测**（某臂始终未触发）→ 该臂 LeadTime 记为最坏（`−∞`，退化），E-1B 报告时剔除或按最坏处理（见 §8 缺失值规则）。

---

## 5. Ground Truth（结构化正确答案）

为消除「每人理解不同」的歧义，新增 `GroundTruthRecord`，每个 case 一份，由评审定义、与 replay fixture 同仓存放。

```json
{
  "case_id": "case_002",
  "category": "behavior_escalation",
  "expected_pattern": ["repeated_visit", "behavior_escalation"],
  "required_evidence": [
    "historical_context[].record_id = ep-b002-d1",
    "visitor_profile.visit_count = 3",
    "risk_pattern.tags = escalating_behavior"
  ],
  "acceptable_hint": ["NOTIFY_FAMILY", "ESCALATE_COMMUNITY"],
  "expected_detection_step": null,
  "expected_detection_ts": null
}
```

| 字段 | 含义 |
|------|------|
| `case_id` | 关联 replay fixture |
| `category` | E-1B 分层用 |
| `expected_pattern` | 期望 Memory 臂识别出的模式标签（FN 的 pattern 判定）；须 ∈ `risk_pattern.tags` 或对应 `conflicts.type` 语义 |
| `required_evidence` | **期望被 `source_refs` / `findings` 引用的历史证据锚点**，采用冻结匹配语法 `<path> = <value>`（见下）；FN 还需命中此约束，形成 pattern detection + evidence grounding 双判据，杜绝「无依据的口号式发现」 |
| `acceptable_hint` | 可接受 hint 白名单（须 ∈ `RECOMMENDED_ACTION_HINTS`，可含 `null` 表示允许无提示）；FP 上限取其中最高 severity（映射见 §4.2） |
| `expected_detection_step` / `_ts` | E-1B 用（Early Detection 对照）；E-1A 置 `null` |

**`required_evidence` 匹配语法（冻结）**：每条为 `<path> = <value>`，`<path>` 以 `ReasoningInput` 为根的点路径，`<value>` 为期望具体值：

| `<path>` | 匹配规则（对照 C-6 `SourceRef` 产出） |
|----------|----------------------------------------|
| `historical_context[].record_id` | 某 `SourceRef(source="historical_context", ref=<record_id>)`；**引擎仅锚定首条历史** `historical_context[0].record_id`，故须取首条（如 `ep-b002-d1`，前缀 `ep-`） |
| `visitor_profile.<field>`（`visit_count` / `night_visit_ratio` / …） | 某 `SourceRef(source="visitor_profile")` 且其 `detail` 含 `<field>=<value>`（引擎写 `detail="visit_count=3,night_visit_ratio=0.0,confidence=cold_start"`）；**计数字段经 `detail` 字符串绑定具体值** |
| `risk_pattern.tags` | 某 `SourceRef(source="risk_pattern", ref=<tag>)`（引擎每个 tag 一条 SourceRef） |
| `conflicts.type` | 某 `SourceRef(source="conflicts", ref=<type>)` |

- **合法锚点组合**：`SourceRef.source ∈ {visitor_profile, risk_pattern, historical_context, conflicts, previous_actions}`（**不含 `current_event`**）且 `ref` / `detail` 满足上述路径约束。
- **FN 双判据**：某 `expected_pattern` 条目「已检出」= `findings` 含其语义 **且** 至少一条 `required_evidence` 被对应 `SourceRef` 命中（pattern detection + evidence grounding）。

> Ground Truth 只用于**评测 Memory 臂**（FN / Early Detection 对照）；Baseline 臂作对照基线，不要求命中。

---

## 6. 数据集（E-1A 机制验证 / E-1B 价值证明）

### 6.1 E-1A（3 case，harness 的「unit test」，Implementation Ready）

- 范围：case_001 / case_002 / case_003（复用 M0 replay fixtures）。
- 目标：验证 harness 端到端跑通、**Explainability(Q1/Q2/Q3) / FP / FN 三指标可计算**、A/B 变量隔离正确；Early Detection 指标定义保留但 **data-gated → E-1B**（无时序数据，见 §4.4）。
- 通过即证明**机制成立**，但**不构成价值证明**（样本太小，无法排除人工设计偏差 / 规则针对性优化 / overfitting）。

### 6.2 E-1B（20~50 case，真实 CCTV replay，待数据治理）

- 来源：真实 CCTV replay（非人工合成），经 ADR-0024 写入链路产出 EpisodicRecord。
- 分层（每类约 10 例，合计 20~50）：

| 类型 | 数量 | 预期 Memory 增益 |
|------|-----:|------------------|
| 正常访客 | 10 | FP 不恶化、FN 低 |
| 重复访问 | 10 | Q3 grounding、Early Detection 正向 |
| 行为升级 | 10 | FN 降低、Early Detection 正向 |
| 冲突案例 | 10 | Q2/Q3 历史引用 |
| 未知模式 | 10 | 压力测试，避免 overfitting |

- **核心风险是 Evaluation Dataset Engineering（非代码）**：需标注 event sequence / visitor identity / episode boundary / risk pattern / ground truth detection time，否则无法判定 Memory 好坏。建议作为独立数据治理子任务，先于 E-1B 实现。
- E-1B 才有统计意义（见 §8）。

---

## 7. Harness 结构

新增 `src/home_perception/memory/evaluation/`（可复用 harness，依最终实现约定）：

```
memory/evaluation/
├── ab_runner.py        # 跑实验：构造两臂 ReasoningInput、调同一引擎、收 ReasoningResult
├── metrics.py          # 四指标纯函数（Q1 grounded / Q2 / Q3 / FP / FN / Early Detection）
├── ground_truth.py     # GroundTruthRecord 定义 + 加载（§5）
├── report.py           # 生成报告（含 §8 统计汇总）
└── fixtures/
    ├── e1a/            # 3 case（复用 M0 replay）
    └── e1b/            # 真实 CCTV replay（规划）
```

职责分离（不混）：runner 执行不评价；metrics 算指标（纯函数、可单测）；ground_truth 定义正确答案、与 replay 解耦；report 汇总指标 + 统计、生成可审阅报告。

---

## 8. 统计汇总与 Memory Value Score

### 8.1 统计汇总（E-1B 适用）

对每个指标跨 N 个 case 报告：`mean` / `std` / **95% 置信区间**（小样本用 bootstrap 或 t 区间）。
两臂对比用**配对**口径（同一 case 的 B/M 配对）：报告 `Δ = metric(M) − metric(B)` 的均值与 CI；显著性可用 Wilcoxon signed-rank（小样本、非正态友好），仅作报告、不作硬门槛。

### 8.2 Memory Value Score（加权复合，报告用，**非 gate**，E-1B 前未标定）

四 term 各自归一化到 `[0, 1]`，**独立不重复计权**：

```
Memory Value Score = 0.40 × FN_term
                  + 0.30 × EarlyDetection_term
                  + 0.20 × Explanation_term
                  + 0.10 × FP_term
```

权重依据：安全系统 **漏报成本 > 误报成本**，故 FN（40%）与 Early Detection（30%）主导，Explanation（20%）次之，FP（10%）最末。

- **`FN_term`**（输入域 = FN 计数；`clamp(x,0,1)`）：
  `FN_term = clamp( (FN_B − FN_M) / max(FN_B, 1), 0, 1 )`。
  `FN_B=0 ∧ FN_M=0` → `0`（无信息，中性）；`FN_M < FN_B` → 正向；`FN_M > FN_B` → 截 0。
- **`EarlyDetection_term`**（输入域 = LeadTime 分钟；缺失检测 → 最坏）：
  设 `W = 60`（标定窗口，分钟，E-1B 标定）；`LeadTime_min` 为 M 臂相对 B 臂提前分钟数（负=退化）。
  `EarlyDetection_term = clamp( LeadTime_min / W, 0, 1 )`；M 臂未检测（缺失）→ `0`。
- **`Explanation_term`**（输入域 = Q2∧Q3 通过比例，**不含 Q1** 以免与 FN_term 重叠）：
  每 case `e = (Q2_pass ? 0.5 : 0) + (Q3_pass ? 0.5 : 0)`；`Explanation_term = mean(e over cases)`。
- **`FP_term`**（输入域 = 严重度折扣）：
  `FP_term = 1 − max(0, severity(M_hint) − severity(upper(acceptable_hint))) / 3`；两臂均未超上限 → `1`；超出按严重度差折扣到 0。
- **缺失值**：E-1A 中 `EarlyDetection_term` 因无时序数据不参与（报告标记 `N/A`）；其余 term 按上计算。
- **聚合**：跨 N case 取 mean；E-1B 加 `std` / 95% CI（bootstrap，见 §8.1）。

> Score **仅用于报告与横向比较不同 Memory 方案**，绝不替代 §9 Hard Gate；E-1B 前阈值未标定，不得据 Score 判定「Memory 有用」。`Explanation_term` 已不含 Q1，避免与 `FN_term`（含 grounded gain 精神）重复计权。

---

## 9. 通过门槛（Hard Gate 先于 Score）

正确的判定结构是 **先 Hard Gate、后 Memory Value Score，绝不反向**：

```
Hard Gate（硬门槛，必须全过才许进 Phase 2）
|-- FN 不能恶化（≤）
|-- FP 不能严重恶化（≤）
|-- Grounding（Q3）/ Grounded Finding Gain（Q1）必须通过
        ↓
Memory Value Score（仅报告/横向比较，不替代硬门槛）
```

### E-1A（3 case）通过条件（全部满足）

1. Explanation Quality：Q1(Grounded)∧Q2∧Q3 三子判据全过（3/3）；
2. False Positive：两臂 hint 均 `≤ acceptable_hint` 上限（对照 Ground Truth，非两臂差值；映射见 §4.2）；
3. False Negative：`FN_M < FN_B`（基于 pattern finding，非 hint），理想 `FN_M = 0`；
4. Early Detection：**E-1A 不计算**（无时序数据），标记 `N/A（→ E-1B）`，**不计入 Hard Gate**（指标定义与 fixture 契约见 §4.4 / §4.4.1）。

### E-1B（20~50 case）额外要求

统计汇总显示 `Δ` 的 95% CI 整体正向（不含 0），Memory Value Score 达标定阈值。

> 即使 Score 高，任一硬门槛失败也不许进入 Phase 2 决策增强（防止复合指标隐藏单点恶化）。

---

## 10. 边界与 ADR-0010（hint 非决策）

**明确声明**：`ReasoningResult.suggested_action_hint` 是 **Reasoning 层的行为观察指标**，**不代表 Decision 输出**。真正的决策权唯一属于 `DecisionPolicy`（ADR-0010）。
本设计把 hint 用作 FP/FN 的观测代理，仅为评估「理解是否更准确」，绝不将其直接转译为动作执行。此声明 preempt ADR-0010 review 可能提出的「为什么 Reasoning 已经建议动作」之质疑。

---

## 11. Slice 拆分（实现期）

| Slice | 内容 | 依赖 | 状态 |
|-------|------|------|------|
| E-1a | `ab_runner` + `metrics` 纯函数（四指标可计算） | C-6 已合 | ✅ 已合（PR#111） |
| E-1b | `test_e1_ab.py` + `report`（含 §8 统计占位） | E-1a | ✅ 已实现（本 slice） |
| E-1c | 时序 step 展开 + `LeadTime` 时间戳；E-1A 3 case 校准 | E-1b | 可开发 |
| E-1d | `ground_truth.py` + `GroundTruthRecord` 加载；E-1B 数据集采集/标注（独立数据治理任务） | E-1c | E-1B 待治理 |

实现走分支 + `gh pr` + 文件集复核（仓库铁律）；`ab_runner`/`metrics` 纯函数进 CI。
E-1A 产出：`e1_report.json` + `e1_report.md`。

**E-1b 落地补充**（与 §7 / §8 的实现约定）：

- CLI 入口 `python -m home_perception.memory.evaluation --fixtures <dir> --out <dir>`，
  **Hard Gate 失败 → 退出码 1**，可直接作 CI gate；产出默认写 `artifacts/e1/`（已 gitignore，可再生成）。
- Early Detection 在 E-1A 为 `N/A`：该 term **从 Score 中剔除**，剩余三 term 按原比例重归一化
  （`0.40 / 0.20 / 0.10 → ÷ 0.70`），报告标记 `partial=true`、`calibrated=false`。
  **不得用 0 分冒充「无提前量」**——「未测量」与「无提前量」是两回事。
- §8.1 统计量用 **t 区间 + 内置临界值表**（零依赖、确定性、可单测）；Wilcoxon signed-rank 留待 E-1B。
- E-1A 实测：Hard Gate **3/3 通过**，但 FN 配对 Δ 的 95% CI 为 `[-0.101, 2.768]`（**跨 0**），
  如实印证 §9「CI 整体正向」只能由 E-1B 满足——小样本下 Hard Gate 通过 **≠** 统计显著。

---

## Appendix A. 第一轮评审增强（2026-08-03）

按 Owner 首轮评审落地 6 项增强（评审原文备份见 `docs/.DESIGN-memory-evaluation.review-2026-08-03.md`）：

| # | 位置 | 修改 |
|---|------|------|
| 1 | Q3（§4.1） | 关键词命中 → **Pattern Grounding**（结构字段引用） |
| 2 | FP（§4.2）+ §10 | 明确 `suggested_action_hint` **仅 Reasoning 层行为观察，非 Decision 输出** |
| 3 | Early Detection（§4.4） | 增加 **timestamp Lead Time** 为主指标，step delta 降为辅助 |
| 4 | Dataset（§6） | 分 **E-1A（3 case）/ E-1B（20~50 真实 CCTV）** 两阶段 |
| 5 | Ground Truth（§5） | 新增结构化 `GroundTruthRecord` |
| 6 | Metrics（§8） | 增加 **统计汇总** + **Memory Value Score 加权**（FN 40% / Early 30% / Expl 20% / FP 10%） |

首轮评分：架构 9/10 · 实验 8.5/10 · CI 9/10 · 科研 8/10。

## Appendix B. 第二轮评审冻结点（2026-08-03）

Owner 判定 E-1A **Implementation Ready**、E-1B 需先数据治理，并冻结 3 个修订（评审原文备份见 `docs/.DESIGN-memory-evaluation.review2-2026-08-03.md`）：

| # | 位置 | 修改 |
|---|------|------|
| 1 | Q1（§4.1） | findings 增益 → **Grounded Finding Gain**（`(M\B) ∩ HistoricalGrounded ∩ ExpectedPattern`），杜绝「话多式」膨胀 |
| 2 | GroundTruthRecord（§5）+ FN（§4.3） | 增 **`required_evidence`** 字段；FN 改为 pattern detection + evidence grounding 双约束 |
| 3 | LeadTime（§4.4）+ §9 | 明确正负方向：`>0` 提前 / `=0` 持平 / `<0` 退化 |

第二轮评分：架构边界 9.5/10 · 实验 9/10 · CI 9.5/10 · 科研 8.5/10 · 工程 9/10。
补充明确：Hard Gate（FN/FP/grounding）**先于** Memory Value Score，Score 仅报告/横向比较，绝不反向替代硬门槛（§7/§9）。

## Appendix C. 第三轮契约修订（2026-08-03，Owner 代码级审查）

Owner 对「Implementation Ready」做契约级复核，指出 6 处会使 E-1A **无法落地**的缺口；本次全部修订（均基于真实 `EpisodicRecord` / `ReasoningInput` / `SourceRef` / `RuleBasedReasoningEngine` 字段核对）：

| # | 严重度 | 位置 | 问题 | 修订 |
|---|--------|------|------|------|
| 1 | 高 | §4.4 / §4.4.1 / §9 | Early Detection 无时序数据基础（M0 单当前事件，无 step 序列/每 step 检测） | 计算**推迟 E-1B**；保留指标定义；新增 §4.4.1 时序 fixture 契约；移出 E-1A Hard Gate（标 `N/A`） |
| 2 | 高 | §5 `required_evidence` | 引用不存在字段（`episode_id_001` / `repeat_visit_count`） | 改用真实路径：`historical_context[].record_id=ep-...` / `visitor_profile.visit_count=n` / `risk_pattern.tags=tag`；冻结 `<path>=<value>` 匹配语法 + SourceRef 锚点规则 + 计数字段经 `detail` 绑定 |
| 3 | 高 | §4.1 Q3 | 要求扫描 explanation 文本，但 C-6 `_explain()` 为模板化无具体值 → 结构性失败 | 改 Q3 为**证据链验证**（SourceRef→findings(值)→explanation(概念)），不与 C-6 冲突；明确不重复计 Q1 |
| 4 | 中 | §4.2 / §4.3 / §6.1 | 三 case hint 两臂塌缩（`_hint` 由 `current_event.risk_level` 主导）；case_001「正确=MONITOR」与引擎冲突 | FP 改对照 GT 上限（非两臂差值）；FN 改基于 pattern finding（非 hint）；修正 case_001 校准；加 fixture 校准提示 |
| 5 | 中 | §8.2 | Score 各项归一化/缺失值未定义；Q1 与 Explanation_term 重复计权 | 定义 FN/Early/Expl/FP 四 term 输入域、归一化函数、缺失值处理；`Explanation_term = Q2∧Q3`（不含 Q1）；标注 E-1B 前未标定 |
| 6 | 低 | §4.2 | FP 严重度未定义 `None` 排序 | 冻结映射 `None=0, MONITOR=1, NOTIFY_FAMILY=2, ESCALATE_COMMUNITY=3`；明确 `None` 在 FP/FN/Early Detection 中的处理 |

本次修订后，E-1A 可在**现有 M0 fixtures + 当前 C-6 引擎**上直接计算 Explainability(Q1/Q2/Q3) / FP / FN 三指标；Early Detection 与 hint 级边际贡献留待 E-1B（需时序/校准数据）。
