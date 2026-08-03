# DESIGN-memory-evaluation.md — Memory Value Evaluation (E-1)

> **状态**：Implementation Ready（2026-08-03 Owner 两轮评审通过；E-1A 可开发，E-1B 待数据治理）
> **归属**：ADR-0025 Phase 1 收口后的 Evaluation Gate；位于「Consumer 接入」与「Phase 2 决策增强」之间
> **作者**：silver-shield / Memory Consumer 工作组
> **最后修订**：2026-08-03（两轮 Owner 评审：6 项增强 + 3 项冻结点，见 Appendix A / B）

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
  (a) 其 `source_refs` 至少一条指向历史（`historical_context` 的 `source_event_ids`，C5 透传）；
  (b) 命中 `GroundTruthRecord.expected_pattern`（§5）。
  即 `ValidFindingGain = (findings(M) \ findings(B)) ∩ HistoricalGrounded ∩ ExpectedPattern`，且 `|ValidFindingGain| ≥ 1`。
  **仅奖励「有历史依据且命中预期模式」的新增发现**，杜绝「话多式」信息膨胀（Baseline 给 1 条、Memory 给 7 条但无一条历史锚定 → 不通过）。
- **Q2 — 历史引用（强）**：`ReasoningResult(M).source_refs` 至少引用一条来自 `historical_context` 的 `source_event_ids`（C5 透传），即解释指向具体历史事件。
- **Q3 — Pattern Grounding（最强，替代原「关键词命中」）**：从文本匹配升级为**结构字段引用**。解释文本必须引用 `ReasoningInput` 中**仅历史侧才存在**的字段值，且这些值在 `current_event` 中不可得：
  - 具体数值：`night_visit_frequency=5` → 解释出现「过去 30 天出现 5 次夜间访问」；
  - 历史实体：某 `visitor_id` / 某 `episode_id`（非当前事件）；
  - 历史语义：`repeat_visit` / `behavior_escalation` / `conflict` 等模式标签，且这些标签**定义在历史字段**而非当前事件。
  - **反例（不通过）**：仅复读「该访客存在历史行为模式」「历史正常」等无锚点文本（假 grounding，false improvement）。

> Q3 接近论文实验中的 **evidence grounded reasoning evaluation**：要求 `Memory evidence → finding/source_refs → explanation` 形成可追溯链，证明 Memory 真被消费，而非「看到字段即输出套话」。

### 4.2 False Positive（误报，不恶化约束）

语义严重度排序：`MONITOR < NOTIFY_FAMILY < ESCALATE_COMMUNITY`。

- 基准 case：`case_001`（规律夜间访客，正确 = 温和 `MONITOR`）。
- 约束：`severity(ReasoningResult(M).suggested_action_hint) ≤ severity(ReasoningResult(B).suggested_action_hint)`。Memory 臂不得因引入历史而错误抬高 hint。
- 本指标是**不恶化上限**约束，不以「越低越好」计；FP 在 Memory Value Score 中权重最低（见 §8），因安全系统漏报成本 > 误报成本。

### 4.3 False Negative（漏报，核心指标）

Memory 的价值在「提前形成模式」。银龄盾场景诈骗风险通常是**行为序列异常**而非单事件异常。

- 基准 case：`case_002`（行为升级）/ `case_003`（冲突透明）。
- 定义 `FN`：该给出的 escalation / pattern finding 或高风险 hint **缺失**，或虽给出但**未满足 `required_evidence` 证据锚定**，即为一次漏报。Ground Truth 见 §5（`expected_pattern` + `required_evidence` **双约束**：pattern detection + evidence grounding）。
- 约束：`FN_M < FN_B`，理想 `FN_M = 0`。
- **FN 是四指标中权重最高项**（见 §8），反映「漏报比误报更危险」。

### 4.4 Early Detection（提前发现，含时间戳 Lead Time）

把每个 replay case 沿时间轴展开为**时序 step 序列**，定义「检测事件」为：首个 step 满足 `suggested_action_hint ∈ {ESCALATE_COMMUNITY, NOTIFY_FAMILY}` **或** findings 中出现 escalation 类条目。

- **Step delta（辅助）**：`Δstep = E_step(B) − E_step(M)`，`Δstep ≥ 0` 表示 Memory 臂不晚于 Baseline。
- **Lead Time（主指标）**：以**时间戳**为权威单位（现实系统 step 数量不稳定）。
  ```
  LeadTime = timestamp(B_detection) − timestamp(M_detection)
  ```
  语义：
  - `LeadTime > 0` → Memory 臂更早检测（提前 LeadTime）
  - `LeadTime = 0` → 两臂同时检测（持平）
  - `LeadTime < 0` → Memory 臂更晚检测（退化）
  例：`B=15:00, M=14:30` → `+30min`（提前）；`B=15:00, M=15:20` → `−20min`（退化）。

---

## 5. Ground Truth（结构化正确答案）

为消除「每人理解不同」的歧义，新增 `GroundTruthRecord`，每个 case 一份，由评审定义、与 replay fixture 同仓存放。

```json
{
  "case_id": "case_002",
  "category": "behavior_escalation",
  "expected_pattern": ["repeat_visit", "behavior_escalation"],
  "required_evidence": ["episode_id_001", "repeat_visit_count"],
  "acceptable_hint": ["NOTIFY_FAMILY", "ESCALATE_COMMUNITY"],
  "expected_detection_step": 7,
  "expected_detection_ts": "2026-08-03T02:47:00+08:00"
}
```

| 字段 | 含义 |
|------|------|
| `case_id` | 关联 replay fixture |
| `category` | E-1B 分层用 |
| `expected_pattern` | 期望 Memory 臂识别出的模式标签（FN 的 pattern 判定） |
| `required_evidence` | **期望被 `source_refs` / explanation 引用的历史证据锚点**（episode id / 计数类字段名等）；FN 还需命中此约束，形成 pattern detection + evidence grounding 双判据，杜绝「无依据的口号式发现」 |
| `acceptable_hint` | 可接受 hint 白名单（必须 ∈ `RECOMMENDED_ACTION_HINTS`） |
| `expected_detection_step` / `_ts` | 期望首次检测的 step 与时间戳（Early Detection 对照；可选） |

> Ground Truth 只用于**评测 Memory 臂**（FN / Early Detection 对照）；Baseline 臂作对照基线，不要求命中。

---

## 6. 数据集（E-1A 机制验证 / E-1B 价值证明）

### 6.1 E-1A（3 case，harness 的「unit test」，Implementation Ready）

- 范围：case_001 / case_002 / case_003（复用 M0 replay fixtures）。
- 目标：验证 harness 端到端跑通、四指标可计算、A/B 变量隔离正确。
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

### 8.2 Memory Value Score（加权复合，报告用，**非 gate**）

```
Memory Value Score = 0.40 × FN_term
                  + 0.30 × EarlyDetection_term
                  + 0.20 × Explanation_term
                  + 0.10 × FP_term
```

权重依据：安全系统 **漏报成本 > 误报成本**，故 FN（40%）与 Early Detection（30%）主导，Explanation（20%）次之，FP（10%）最末。
- `FN_term`：FN 降低比例越高越接近 1；`EarlyDetection_term`：`LeadTime(M)` 越早越接近 1；
- `Explanation_term`：Q1(Grounded)∧Q2∧Q3 命中比例；`FP_term`：不恶化为 1，恶化按严重度折扣。

> **Score ≥ 阈值（待 E-1B 标定）即判定「Memory 确实提高理解」，仅用于报告与横向比较不同 Memory 方案。**

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
2. False Positive：不恶化约束满足（`severity(M) ≤ severity(B)`）；
3. False Negative：`FN_M < FN_B`，理想 `FN_M = 0`；
4. Early Detection：`E_step(M) ≤ E_step(B)` 且 `LeadTime(M) ≥ 0`（Memory 臂不晚于 Baseline；`>0` 提前，`=0` 持平，`<0` 退化不通过）。

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
| E-1a | `ab_runner` + `metrics` 纯函数（四指标可计算） | C-6 已合 | **可开发** |
| E-1b | `test_e1_ab.py` + `report`（含 §8 统计占位） | E-1a | 可开发 |
| E-1c | 时序 step 展开 + `LeadTime` 时间戳；E-1A 3 case 校准 | E-1b | 可开发 |
| E-1d | `ground_truth.py` + `GroundTruthRecord` 加载；E-1B 数据集采集/标注（独立数据治理任务） | E-1c | E-1B 待治理 |

实现走分支 + `gh pr` + 文件集复核（仓库铁律）；`ab_runner`/`metrics` 纯函数进 CI。
E-1A 产出：`e1_report.json` + `e1_report.md`。

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
