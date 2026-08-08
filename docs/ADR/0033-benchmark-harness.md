# ADR-0033: 基准测试框架（Benchmark Harness · 仿真场景的回归打分与门控）

- **Status**: Accepted（2026-08-09 Owner 审核通过冻结；设计定稿进入实施）
- **Date**: 2026-08-08
- **Owner**: SilverShield 技术负责人
- **Related**:
  - ADR-0032（场景仿真层 · 本 ADR 消费其产出的 `Scenario` / `ScenarioCompiler` / `ScenarioRunner` / `ScenarioValidator` / `generator.fingerprint`）
  - ADR-0031（决策审计血缘契约 · 本 ADR 复用 `DecisionABRun` 守恒范式、可选采集 `DecisionTrace`、附 trace 走其 `prune_jsonl` / `assert_desensitized`）
  - ADR-0030（决策边界契约 · `DecisionInput` 收敛载体；本 ADR 不拥有决策级 A/B）
  - ADR-0028 D6（跨模态运行时接线 · 声明式 scenario 直注 Memory Graph，与 ADR-0032 正交；本 ADR 未来可串联二者做端到端基准）
  - ADR-0014（三级冻结治理 · L2 Interface 可替换帧源 / 检测器接缝；本 ADR 经既有接缝注入 pipeline）
  - AGENTS.md §3（模块边界铁律 · 仅产标签事件）/ §6.3（架构决策文件 Owner 专属）
- **Phase**: v2 · Phase 3 → 决策边界契约（ADR-0030）→ 决策审计血缘（ADR-0031）→ 场景仿真层（ADR-0032）→ **Benchmark Harness（ADR-0033，本 ADR）**

---

## 0. 背景与动机（Context）

> **引用约定（可维护性）**：本 ADR 引用代码以**符号名 + 简短引文**为准（行号会漂移，仅作辅助定位）。文中行号均截至本 ADR 起草时（`main` 含 ADR-0032 全切片 + ADR-0031 Slice E，commit `0d6554a`）。若行号与实际不符，以符号名与引文为准。

ADR-0032 落地了"可复现、隐私安全、确定性"的**场景仿真层**：声明式 `Scenario` → 两通道（`detections` / `frames`）→ 喂整条感知 pipeline → `ScenarioValidator` 对照 `expects` 产出 `ValidationResult`（含"期望 vs 实际"差异）。它解决了"能不能喂"，但**没解决"喂完之后怎么衡量、怎么防回归"**——这正是本 ADR 的边界。

### 0.1 今天没有"版本变好还是变差"的机器答案

安全系统最致命的两个失败模式（ADR-0031 §0 已点明）是"**为什么报警**"和"**为什么没报警**"。ADR-0031 让**决策层**的漏报/误报首次可观测（`DecisionABRun` 的 `outcome.kind` 配对 + 六条守恒）；但**整条感知 pipeline**（frame → detector → tracker → event → decision → warning）在"一组已知场景上"的行为变化，今天仍无人持续盯防：

- 改了 `RuleEngine` 一条规则 → 哪些场景从"正常"变成"误报"？无人算；
- 升级了 `generator` 渲染基元（矩形→圆+矩形）→ 哪些 `ScenarioValidator` 结果漂了？无人算；
- 重构了 `tracker` → 哪个 revisit 场景的重复来访判定退化了？无人算。

这些问题的共同答案不是"加更多测试"，而是"**我能证明这一版在 N 个已知场景上的行为，相对上一版是改善还是退化**"——一个 **Benchmark Harness**。

### 0.2 已有两块"评估"资产，但都不覆盖"仿真场景回归"

| 现有资产                                                                                   | 测什么                                                                              | 不覆盖什么                                                                               |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `benchmark/yolo_speed.py` + `tests/test_benchmark.py`                                  | **性能**基准（FPS / 延迟 / 资源）                                                          | 行为正确性 / 漏报误报 / 跨场景聚合打分                                                              |
| `memory/evaluation/`（`ab_runner` / `metrics` / `report` / `ground_truth` / `temporal`） | **Memory/Reasoning 层** A/B 价值评估（E-1：Q1–Q3 / FP / FN / Early Detection，Shadow 模式） | 感知链路（frame→event→warning）在仿真场景上的回归；用的是 `MemoryReplayDataset` 而非 ADR-0032 `Scenario` |

二者都是**成熟范式**，且本 ADR 要做的"场景级打分 + 回归门控 + A/B 守恒"与它们**同构**——本 ADR 应当**对称复用**其结构（`ABRun` / `CaseEvaluation` / `HardGateSummary` / `E1Report` 的 `to_dict` / `render_markdown` / `write_report` 模式），而非另起炉灶。

### 0.3 ADR-0031 已把"漏报/误报可观测"做成范式，本 ADR 把它下沉到 pipeline 级

ADR-0031 D7 的 `DecisionABRun` 让决策层的 `SUPPRESS×WARN`（漏报）/ `WARN×SUPPRESS`（误报）首次可观测。本 ADR 把**同一种混淆矩阵**下沉到**仿真场景级**：一个 `Scenario` 的 `benchmark.expected_alarm`（显式声明的安全评价标签，与 ADR-0032 `expects` 语义分离）推导出"期望报警 / 期望不报警"标签，运行结果 `RunResult.warnings` 推导出"实际报警 / 实际不报警"标签，二者配对即场景级 TP/FN/FP/TN——使**整条 pipeline 的漏报率（suppression_rate）/ 误报率（false_alarm_rate）首次可观测、可聚合、可回归**。这是 ADR-0031 思想在"输入可复现"前提下的自然延伸。

### 0.4 本 ADR 在 AI 验证基础设施闭环中的位置

```
Scenario Simulation (ADR-0032)   ← 可复现、隐私安全输入源（本 ADR 的上游，已合 main）
        |
        v
Perception Pipeline              ← frame → detector → tracker → event → decision → warning
        |
        v
Benchmark Harness (ADR-0033)     ← 本 ADR：这一版在已知场景上的行为，相对上一版改善还是退化？
        |  （场景级混淆矩阵 + 回归门控 + 代码版本 A/B）
        v
Regression / Improvement
```

与 `memory/evaluation/`（Memory 层 E-1 评估）并列、**互不重叠**：E-1 评"Memory 有没有用"（Shadow，输入是 `MemoryReplayDataset`），本 ADR 评"感知 pipeline 在仿真场景上的行为有没有退化"（输入是 ADR-0032 `Scenario`，可跑真实 detector opt-in）。

### 0.5 现状小结

| 需求                              | 今天能否满足                                                         |
| ------------------------------- | -------------------------------------------------------------- |
| 用 ADR-0032 场景批量跑 pipeline 并聚合打分 | **不能**（没有编排/聚合层，ADR-0032 `ScenarioRunner` 明确"不含聚合，归 ADR-0033"） |
| 场景级漏报/误报可观测、可聚合                 | **不能**（ADR-0031 只在决策层）                                         |
| 跨版本回归门控（"这版不能比上版差"）             | **不能**（无基线对照、无阈值）                                              |
| 代码版本 A/B（唯一变量=代码）               | **不能**（ADR-0031 `DecisionABRun` 唯一变量=Memory，非代码版本）             |
| 分数变化可归因于 渲染/策略/代码/模型/场景集/环境      | **不能**（无 harness 级指纹）                                          |

---

## 1. 决策（Decision）

### D1 · 单一 `evaluation/` 包，复用而非重写；明确三块评估资产的边界

新建 **`src/home_perception/evaluation/`**（与 `validation/` / `analysis/` / `memory/` 平级的**核心**包），作为感知级 Benchmark Harness。它与既有两块评估资产的边界**铁律**：

| 资产                        | 层级                             | 输入                        | 评什么                                            | 关系                                                                                          |
| ------------------------- | ------------------------------ | ------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `benchmark/yolo_speed.py` | 性能                             | 合成帧 + Detector            | FPS / 延迟 / 资源                                  | 本 ADR **不碰**性能；若未来要把"场景跑真实 YOLO 的 FPS"纳入，是扩展而非重写                                            |
| `memory/evaluation/`      | Memory / Reasoning（E-1，Shadow） | `MemoryReplayDataset`     | Memory 有没有用（Q1–Q3 / FP / FN / Early Detection） | 本 ADR **不碰** Memory 价值评估；仅**对称复用其工程范式**（`ABRun` / `HardGateSummary` / `E1Report` 的序列化/渲染模式） |
| **`evaluation/`（本 ADR）**  | **感知 pipeline（仿真场景回归）**        | **ADR-0032 `Scenario` 集** | **pipeline 行为有没有退化**                           | 消费 ADR-0032 + 可选 ADR-0031 trace                                                             |

> **落点理由**：ADR-0032 D5 早已预留"`evaluation/`（perception 级）与音频侧对称消费"；`memory/evaluation/` 已是同级存在，故本 ADR 用**顶层 `evaluation/`**（感知级），与 `memory/evaluation/`（Memory 级）**并列、命名区分、不互相 import 实现**（仅共享"评估"这一抽象概念，结构上对称）。`evaluation/` 是核心包，由 `scripts/run_benchmark.py` 与 `tests/` 引用，**绝不**进 demo/gateway 运行时（守 D8 零行为变化）。

### D2 · 仅编排 ADR-0032 三组件，不重新生成、不重新校验事件 Schema

Benchmark Harness **只做编排与聚合**，绝不重复 ADR-0032 已落的职责：

```
ScenarioCompiler(YAML) → SyntheticInput
        → ScenarioRunner.run(SyntheticInput, pipeline) → RunResult
        → ScenarioValidator.validate(RunResult, Scenario) → ValidationResult   # 复用，不重写
        → ScenarioScore（本 ADR 新增：把 ValidationResult 转成可聚合打分）
        → BenchmarkReport（本 ADR 新增：跨场景聚合）
```

- 不调 `generator` / `renderer`（ADR-0032 的职责）；
- 不重新校验 `PerceptionEvent` / `WarningEvent` schema（ADR-0032 T4 已由 generator 保证；本 ADR 只**观测** pipeline 输出，不修改）；
- 不碰 `RuleEngine` / `DecisionPolicy` 行为（只**读取**其输出事件与 `policy.fingerprint`）。

> 即：Harness 是 ADR-0032 的**消费者**，不是它的合作者——与 ADR-0032 D3「生产 pipeline 不知道 generator 存在」同一哲学。

### D3 · 场景级混淆矩阵（TP/FN/FP/TN + 漏报率/误报率），把 ADR-0031 思想下沉到 pipeline 级

**语义分离（核心）**：ADR-0032 的 `expects` 负责"**验证场景输出**"（Schema 契约校验，归 `ScenarioValidator`）；ADR-0033 引入独立的 `benchmark` 字段负责"**定义安全评价标签**"（归 Benchmark Harness 的混淆矩阵）。二者**同处 `scenario.yaml`、但语义正交、互不推导**——`benchmark.expected_alarm` 由场景作者**显式声明**的安全意图，而**不是**从 `expects` 推出来的。这避免了"测试期望 ≠ 安全评价标签"的混淆：例如 `expects.emitted_event_types=[visitor_detected]` 只是"应输出该事件"，不必然要求 warning；`min_risk_level=low` 也未必等于"需要报警"。

`BenchmarkExpectation`（ADR-0033 新增，落到 ADR-0032 `Scenario` 的**可选** `benchmark` 字段；向后兼容缺省 `None`，**ADR-0032 `ScenarioValidator` 不消费此字段**——验证 ≠ 评价，职责分离）：

```python
class RiskLevel(str, Enum): ...   # 复用既有

class BenchmarkExpectation(BaseModel):
    expected_alarm: bool                 # 该场景是否期望触发告警（安全评价标签，显式声明）
    severity: RiskLevel | None = None    # 期望告警级别（可选，用于分层度量）
    note: str | None = None              # 人类可读理由（审计血缘）
```

```yaml
# scenario.yaml —— 两种语义清晰分家
expect:                 # ADR-0032：验证场景输出（不决定 benchmark 标签）
  events:
    - visitor_detected
benchmark:              # ADR-0033：安全评价标签（不依赖 expect 推导）
  expected_alarm: true
  severity: medium
  note: "陌生访客晚间出现，期望升级社区告警"
```

对每个 `Scenario`，**期望标签** `expected_label` 直接来自 `benchmark`（**不再从 `expects` 推**）：
- `scenario.benchmark is not None` 且 `benchmark.expected_alarm == True` → `"alert"`；
- `scenario.benchmark is not None` 且 `benchmark.expected_alarm == False` → `"no_alert"`；
- `scenario.benchmark is None` → **未标注场景**：不计入混淆矩阵（排除出 TP/FN/FP/TN 聚合），仅在 `BenchmarkReport.unlabeled_scenario_ids` 中记录，供 `mean_event_recall` 等**纯验证**指标使用（验证 ≠ 评价，互不污染）。

**实际标签** `actual_label` 从 `RunResult`（`scenario/runner/runner.py`：含 `warnings` / `risk_levels`）推导。**当前实现（Phase 1，可接受）**：`actual_label = bool(RunResult.warnings)`（`warnings` 非空 → `"alert"`，否则 → `"no_alert"`）。**扩展点（未来）**：`warning` 的产生未必等于"应当报警"——例如 `low_confidence` 提示或测试/调试期 `warning` 不应计入误报。故 `actual_label` 预留 `warning_policy.evaluate(warnings)` 接缝（策略化判定哪些 warning 构成"报警"）；Phase 1 不实现该策略，仅以 `bool(warnings)` 作为显式、可替换的默认实现（`evaluation/metrics.py` 的 `scenario_actual_label` 纯函数签名即为此留口）。

二者配对即场景级混淆矩阵：

| 配对                              | 含义                                          |
| ------------------------------- | ------------------------------------------- |
| `alert × alert`（TP）             | 期望报警且实际报警 ✅                                 |
| `no_alert × no_alert`（TN）       | 期望不报警且实际不报警 ✅                               |
| `alert × no_alert`（**FN / 漏报**） | 期望报警却抑制了 ❌（对应 ADR-0031 `SUPPRESS×WARN` 语义）  |
| `no_alert × alert`（**FP / 误报**） | 期望不报警却误报了 ❌（对应 ADR-0031 `WARN×SUPPRESS` 语义） |

聚合指标（纯函数，复用 ADR-0031 的"漏报/误报可观测"语言）：

- `suppression_rate = FN / (FN + TP)`（期望报警却漏报的比例）；
- `false_alarm_rate = FP / (FP + TN)`（期望不报警却误报的比例）；
- `precision` / `recall` / `F1`（以 `alert` 为阳性类）；
- `mean_event_recall`（ADR-0032 `ValidationResult` 的"期望事件类型被产出多少"的跨场景均值——**验证**指标，不依赖 `benchmark` 标签，未标注场景也参与）；
- `mean_risk_shortfall`（期望 `expects.min_risk_level` 序值 − 实际 `max(risk_levels)` 序值，负值=达标或超额——**验证**指标，沿用 ADR-0032 `ValidationResult`，与 benchmark 标签无关）。

> **与 ADR-0031 的关系（关键澄清）**：ADR-0031 的 `DecisionABRun` 在**单条决策**上做 Memory A/B 的混淆可观测；本 ADR 在**整条 pipeline × N 个仿真场景**上做**行为回归**的混淆可观测。两者**同思想、不同轴**：ADR-0031 轴=Memory 有无；本 ADR 轴=代码版本（可切模型权重轴，见 D6）。本 ADR **不拥有**决策级 A/B（baseline vs candidate 在 `DecisionInput` 上），那仍归 ADR-0030/0031；本 ADR 只**可选采集** ADR-0031 的 `DecisionTrace` 以**丰富**场景级标签（见 D6 / §3 非目标 4）。

### D4 · 可复现血缘指纹：分数变化可归因到 (code, scenario_set, environment) 三元组

`BenchmarkReport` **MUST** 携带 `harness_fingerprint` 与 `provenance`，使"分数变了"能区分是渲染变了 / 策略变了 / 代码变了 / 模型权重变了 / 跑的案例集变了 / 运行环境变了。

**最小可复现单位不是"代码"，而是三元组 `(code, scenario_set, environment)`**：
- `code` = `code_version`（git 短哈希）+ `model_fingerprint`（detector/tracker/event-extractor 权重与版本，**非 git 管理**，如 YOLO `model.pt` 升级不改变 git sha 却改变行为）；
- `scenario_set` = `scenario_set_id`（本次 benchmark 跑的是哪一批场景）；
- `environment` = `runtime_dependencies`（numpy / opencv / torch 等锁版本）。

指纹组成（`harness_fingerprint = sha256(canonical_json({...}))`）：

- `scenario_set_id`：本次 `BenchmarkRun` 携带的场景集标识（如 `registry_tag` 或 suite id）；**标识"我测的是哪批案例"**——同一 `code` 跑不同 `scenario_set` 会得到不同 report，指纹必须表达这一点；
- `code_version`：git 短哈希（报告生成时注入，fail-closed：取不到即报错，不静默）；
- `generator_fingerprint`：复用 ADR-0032 `validation/fingerprint.compute_fingerprint`，区分渲染产物版本；
- `policy_fingerprint`：复用 ADR-0031 `analysis/decision_trace.compute_policy_fingerprint`，区分决策策略配置；
- `model_fingerprint`：本 ADR 新增，**组件级指纹**，覆盖非 git 管理的模型资产：
  ```python
  model_fingerprint = {
      "detector": sha256(weight_binary) or detector_version,   # YOLO model.pt 等权重哈希；无模型时为版本串
      "tracker": tracker_version,
      "event_extractor": event_extractor_fingerprint,
  }
  ```
  （detector 默认走 ADR-0032 `detections` 通道零模型；一旦启用 `frames` + 真实 detector（ADR-0032 T7 opt-in），`model_fingerprint.detector` 即取权重哈希，确保"同代码、不同权重"能被指纹与 A/B 守恒捕获）；
- `runtime_dependencies`：本次运行的锁版本集合（替代原 `numpy_version` / `opencv_version` 散字段），如 `{numpy, opencv, torch}` 的精确版本号；环境漂移（如 opencv 升级）立即反映在指纹里。

> 与 ADR-0032 D7 / ADR-0031 D5 同一思想：**产物必须可溯源到生成它的确切代码、配置、模型与场景集**。否则"分数降了"无法回答"是 renderer 升级了、模型权重换了、还是我改坏了逻辑、还是跑的案例集变了"。指纹由纯函数计算，写时 fail-closed（缺字段即报错）。

### D5 · 门控与复合分（**Phase 3 才引入**；Phase 1/2 只报告离散指标，不产出单一加权分）

> **分阶段纪律（防范围膨胀 · Owner 2026-08-09 决议）**：本 ADR 实施严格分三阶段（见 §6）。**Phase 1（最小闭环）绝不引入 Hard Gate、绝不引入单一 `BenchmarkScore` 加权分、绝不接 CI**——此时场景数据分布尚未积累，任何阈值/权重都是空谈。Phase 1 只把**离散、可解释**的指标报告出来（precision / recall / F1 / FN / FP / suppression_rate / false_alarm_rate + 每场景混淆）。只有当 Phase 2 积累出 old-vs-new 回归基线、Phase 3 才引入门控与复合分。

**Phase 3 门控原则（mirror `memory/evaluation` 的"判定顺序铁律"，届时实现）**：

1. **Hard Gate（先于一切）**：全部场景 MUST 通过 `ScenarioValidator`（ADR-0032 `ValidationResult.ok`）**且**达到 `BenchmarkThresholds`（见 D7）阈值，否则整体不通过；
2. **复合分（仅报告、非门控、显式 experimental）**：即便 Phase 3 引入 `BenchmarkScore`，也只是 `precision` / `recall` / `F1` / `suppression_rate` / `false_alarm_rate` / `mean_event_recall` 的**加权**汇总，**仅供报告与横向比较，绝不替代 Hard Gate**；且**阈值未标定前 `calibrated=False`，不得据此宣称"pipeline 变差"**。

> **不要过早数学化（Owner 2026-08-09 明确）**：安全指标**非线性**——在安全 / 医疗 / 金融领域，`FN +1%` 与 `FP +1%` 的危害价值**极不对称**（FN penalty >>> FP penalty）。因此 **Phase 1 只报告离散指标、不产出单一分**；`BenchmarkScore` 加权和即便在 Phase 3 出现也必须标注 `calibrated=False` / experimental，且**不得用于门控判定**。空集视为**不通过**（无证据 ≠ 通过），与 `summarize_hard_gate` 一致。

### D6 · 代码版本 / 模型权重 A/B（`BenchmarkABRun` + `assert_conserved`，唯一变量=`vary` 轴）

为回答"这一版 vs 上一版"，本 ADR 提供与 ADR-0031 `DecisionABRun` **对称**但**不同轴**的载体：

```
BenchmarkABRun:
    scenario_set_id: str          # 同一场景集（baseline / candidate 用同一批 Scenario）
    report_baseline: BenchmarkReport
    report_candidate: BenchmarkReport
    vary: Literal["code_version", "model_fingerprint"] = "code_version"  # 声明本次 A/B 比较的"唯一变量"轴
    assert_conserved()            # 除 vary 轴外，其余轴全部守恒
```

`assert_conserved`（守恒集合，mirror ADR-0031 D7，但把"唯一变量"泛化为 `vary` 轴）：

1. 两臂 `scenario_set_id` 相同（跑的是同一批场景）；
2. 两臂 `generator_fingerprint` 相同（渲染产物一致，唯一允许差异不在渲染）；
3. 两臂 `policy_fingerprint` 相同（决策策略一致，唯一允许差异不在策略）；
4. 两臂 `runtime_dependencies` 相同（基线一致，唯一允许差异不在数值库/环境）；
5. 两臂 `model_fingerprint` 相同（**模型权重/版本一致，唯一允许差异不在 detector/tracker 权重**；这是 ADR-0031 没有、本 ADR 独有的轴，防止"同代码、不同 YOLO 权重"被误判为纯代码回归）；
6. 两臂 `vary` 轴字段**必须不同**（baseline ≠ candidate 在该轴上，否则"无差异"结论可能是装配 bug 伪装）；
7. 两臂场景数 / 场景顺序相同（聚合口径一致，否则 `suppression_rate` 不可比）。

任一违反即抛 `BenchmarkABConservationError`（显式异常，非 `assert`，免 `-O` 关闭）。默认 `vary="code_version"`（语义：同一模型/环境/场景集，不同代码）；显式 `vary="model_fingerprint"` 用于"同代码、升级模型权重"的回归对比（此时要求 `code_version` 守恒）。`BenchmarkDiff` 由两臂 `BenchmarkReport` 派生：逐指标 Δ + 退化场景清单（`scenario_id` 集合）。

> **与 ADR-0031 的轴区分（铁律）**：`DecisionABRun` 唯一变量=**Memory**（同一 `DecisionInput`，有无历史上下文）；`BenchmarkABRun` 默认唯一变量=**代码版本**（同一 `Scenario` 集，不同代码），可显式切换为 **模型权重**轴（同一代码，不同 detector/tracker 权重）。二者守恒形状同源、语义正交，互不替代。

### D7 · 基线可回放（**Phase 2 引入**）：提交 `BenchmarkReport` 基线到仓库，回归 delta 对照基线

> Phase 1 不提交基线（尚无数据分布，基线无意义）；Phase 2 才引入可回放基线做 old-vs-new 对照。

- `evaluation/fixtures/baselines/<scenario_set_id>.json`：提交**上次良好**的 `BenchmarkReport`（确定性：因完整 `harness_fingerprint` 锁定——含 `scenario_set_id` / `code_version` / `generator_fingerprint` / `policy_fingerprint` / `model_fingerprint` / `runtime_dependencies`，同输入同代码同模型同环境必同值）；
- `evaluate_gate(report, thresholds, baseline=None)`：
  - 无 baseline → 仅做 Hard Gate + 阈值（绝对门控）；
  - 有 baseline → 额外算 `BenchmarkDiff`，对照 `thresholds.max_regression_delta`（如 `suppression_rate` 增幅 ≤ 0.02 才算通过）；
- **基线更新是显式、Owner 评审动作**：当某一 PR **有意**改善分数（如新增场景、修正规则），须在同一 PR 内更新基线 JSON 并说明，禁止"顺手"刷新掩盖退化（CI 对基线 JSON 变更要求 PR 描述注明 `benchmark-baseline-bump`）。

`BenchmarkThresholds`（门控阈值契约）：

```python
@dataclass(frozen=True)
class BenchmarkThresholds:
    min_pass_rate: float = 1.0          # 全部场景须通过 ScenarioValidator
    max_suppression_rate: float = 0.0   # 漏报率上限（安全系统默认零容忍）
    max_false_alarm_rate: float = 0.05  # 误报率上限（可配置，默认宽松）
    max_mean_risk_shortfall: float = 0.0
    max_regression_delta: float | None = None  # 有 baseline 时生效；None=不对照
```

### D8 · 零生产行为变化 + 全程脱敏

- **零行为变化**：`evaluation/` 仅被 `scripts/run_benchmark.py` 与 `tests/` 引用，**绝不**进 `silver_demo` / `gateway` / `runtime` 运行时；不接任何运行时 hook；pipeline 行为逐字节不变（Harness 只 `process_frame` + 观测输出）。它不在 ADR-0015 §5 冻结白名单扫描范围（是核心包，且仅被 scripts/tests 引用，与 demo 互不 import）。
- **脱敏**：`BenchmarkReport` / `BenchmarkScore` / 所有指标**只含** `scenario_id` / 事件类型 / `risk_level` / 指纹——**绝不含原始帧、PII、设备 ID、家庭 ID、用户标识**（与 ADR-0032 T2 / ADR-0031 S2 同一隐私边界）。若启用"可选采集 `DecisionTrace`"（D6 / §3 非目标 4），trace 须先经 ADR-0031 Slice E 的 `prune_jsonl` / `assert_desensitized` 脱敏后再附，Harness 侧不得保存任何未脱敏原始媒体引用。

---

## 2. 不变式（Invariants，契约测试钉死）

| #       | 不变式                                                                                                                                                                                                                                 | 契约测试                                                 |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| **T1**  | **确定性**：同 `scenario_set_id` + 同 `code_version` + 同 `generator_fingerprint` + 同 `policy_fingerprint` + 同 `model_fingerprint`（默认零模型通道哈希） + 同 `runtime_dependencies`（numpy/opencv/torch 锁版本）→ `BenchmarkReport` 逐指标值**逐字节一致**（跨进程复跑）；CI 在锁版本基线下断言（沿用 ADR-0032 T1） | `test_adr0033_t1_deterministic_report`               |
| **T2**  | **零生产行为变化**：`evaluation/` 不进任何运行时路径；`tests/` 全仓无回归；`git grep` 证明 `silver_demo` / `gateway` / `runtime` 不 import `home_perception.evaluation`                                                                                        | `test_adr0033_t2_not_wired_into_runtime`             |
| **T3**  | **复用不重写**：Harness 不调用 `generator` / `renderer`，不重新校验事件 schema；`ScenarioScore` 仅由 ADR-0032 `ValidationResult` + `RunResult` 派生                                                                                                       | `test_adr0033_t3_reuses_validation_not_regenerates`  |
| **T4**  | **血缘可归因**：`BenchmarkReport` MUST 携带 `harness_fingerprint` 且由 `scenario_set_id` + `code_version` + `generator_fingerprint` + `policy_fingerprint` + `model_fingerprint` + `runtime_dependencies` 组成；缺字段即报错（fail-closed）                                                                | `test_adr0033_t4_report_carries_harness_fingerprint` |
| **T5**  | **脱敏**：报告 / 指标 / 附 trace 均不含原始媒体 / PII / 设备 / 家庭 / 用户标识；若附 trace 须过 `assert_desensitized`（复用 ADR-0031）                                                                                                                              | `test_adr0033_t5_report_is_desensitized`             |
| **T6**  | **场景级混淆可观测**：`scenario_confusion` 正确产出 TP/FN/FP/TN，`suppression_rate`/`false_alarm_rate` 公式与语义一致（FN=期望报警却漏报；FP=期望不报警却误报）                                                                                                            | `test_adr0033_t6_confusion_matrix_correct`           |
| **T7**  | **Gate 先于 Score**：`evaluate_gate` 先判 Hard Gate（全场景通过 + 阈值），复合 `BenchmarkScore` 仅用于报告、不进入门控判定；空集视为不通过                                                                                                                                | `test_adr0033_t7_hard_gate_before_score`             |
| **T8**  | **A/B 唯一变量守恒**：`BenchmarkABRun.assert_conserved` 守恒集合全满足（默认 `vary=code_version`、可切 `model_fingerprint` 轴），任一违反抛 `BenchmarkABConservationError`；与 ADR-0031 `DecisionABRun` 守恒形状同源、语义正交（本 ADR 轴=代码版本/模型权重 ≠ Memory）                                                              | `test_adr0033_t8_ab_conservation`                    |
| **T9**  | **单一职责**：`metrics` / `harness` / `report` / `gate` / `ab_runner` 从设计起分离；Harness 只编排、metrics 只算、report 只渲染、gate 只判阈值、ab_runner 只比两臂；任一组件不内嵌其余职责（God Object 防御，mirror ADR-0032 T9）                                                    | `test_adr0033_t9_components_single_responsibility`   |
| **T10** | **基线可回放**：提交的基线 `BenchmarkReport` JSON 可由同输入同代码复现；`evaluate_gate` 在有 baseline 时正确算出 `BenchmarkDiff` 并对照 `max_regression_delta`；基线 JSON 变更须 PR 注明 `benchmark-baseline-bump`（CI 校验）                                                   | `test_adr0033_t10_baseline_replayable`               |

> **契约测试命名约定**：全部 10 条不变式测试名带 `adr0033` 前缀（`test_adr0033_t{N}_*`），测试文件以 `test_benchmark_` 命名，避免与既有 `tests/test_benchmark.py`（性能基准）在 pytest 命名空间冲突、便于 CI 日志溯源。

---

## 3. 范围与非目标（Scope / Non-Goals）

**在范围内（分阶段）**：`evaluation/` 包，分三阶段落地——**Phase 1**：`schema` / `metrics`（含 `ScenarioScore`）/ `report`（离散指标）/ `harness` + `scripts/run_benchmark.py`；**Phase 2**：`ab_runner`（`BenchmarkABRun` / `BenchmarkDiff`）+ 基线 JSON 提交与回归 delta；**Phase 3**：`gate`（`BenchmarkThresholds` / `evaluate_gate`）+ CI 集成 + 基线 bump 治理。全量：`BenchmarkReport` / `ScenarioScore` / `BenchmarkABRun` / `BenchmarkDiff`；与 ADR-0032 三组件编排；代码版本/模型权重 A/B 守恒；基线提交与回归 delta；脱敏。**Phase 1 不产出 `BenchmarkScore` 单一加权分、不引入 Hard Gate、不接 CI**（见 §6 分阶段纪律）。

**明确不做**：

1. ❌ **不重新生成/渲染场景**：那是 ADR-0032 `generator` / `renderer` 的职责，本 ADR 只消费 `SyntheticInput`。
2. ❌ **不重新校验事件 Schema**：ADR-0032 T4 已由 generator 保证；本 ADR 只观测 pipeline 输出。
3. ❌ **不改 `RuleEngine` / `DecisionPolicy` 行为**：只读取其输出与 `policy.fingerprint`。
4. ❌ **不拥有决策级 A/B**（baseline vs candidate 在 `DecisionInput` 上）：那归 ADR-0030/0031；本 ADR 仅**可选采集** ADR-0031 `DecisionTrace` 以**丰富**场景级标签（如把场景级 FN 进一步下钻到决策级 `SUPPRESS` 原因），但**不重新实现**决策 A/B 机制。
5. ❌ **不做性能基准**：FPS / 延迟归 `benchmark/yolo_speed.py`；本 ADR 关注行为正确性回归。
6. ❌ **不做 Memory 价值评估**：那归 `memory/evaluation/`（E-1）；本 ADR 输入是 ADR-0032 `Scenario`，不消费 `MemoryReplayDataset`。
7. ❌ **不做端到端"场景同时驱动感知层 + Memory 层"编排**：那是 ADR-0028 D6 与 ADR-0032 的串联，归未来编排 ADR 或 ADR-0033 后续切片；本 ADR 只消费感知层输出。
8. ❌ **不入库原始媒体 / 不做实时流**：报告仅存 `scenario_id` / 类型 / 等级 / 指纹，遵循 AGENTS.md §6.1。
9. ❌ **不引新重依赖**：仅用已存在的 `opencv-python` / `numpy` / `pydantic` / `pyyaml` / `pytest`；不新增 torch 外依赖。
10. ❌ **不产出未标定的 `BenchmarkScore` 单一加权分**：Phase 1 只报告离散指标（precision / recall / F1 / FN / FP + 率）；即便 Phase 3 引入复合分也必须 `calibrated=False` / experimental，且**不得用于任何门控判定**（安全指标非线性，FN 与 FP 危害价值不对称，过早数学化会误导）。

---

## 4. 后果与备选方案（Consequences / Alternatives）

**正面**：

- 感知 pipeline 首次获得"版本变好还是变差"的**机器答案**，闭合 ADR-0032 §0.6 的 AI 验证基础设施闭环；
- 把 ADR-0031 的"漏报/误报可观测"**下沉到整条 pipeline 级**（场景级混淆矩阵），安全系统的两个最致命失败模式在**输入可复现**前提下首次可聚合、可回归；
- 与 `memory/evaluation/` 对称复用工程范式（`ABRun` / `HardGateSummary` / `E1Report` 序列化/渲染模式），降低维护成本、团队心智一致；
- `harness_fingerprint` 使"分数变化"可归因于 渲染/策略/代码，杜绝"顺手刷新基线掩盖退化"；
- 把分散的 pipeline 冒烟测试收敛为受契约测试守护的正式评估组件（消除技术债）。

**代价**：

- 新增 `evaluation/` 包 + 一层编排/聚合/门控逻辑 + 契约测试；
- 基线 JSON 需要治理（提交/更新/bump 审查），增加一点流程成本；
- 复合 `BenchmarkScore` 在阈值标定前只是"报告数字"，不能据此宣称 pipeline 变差（须配 Hard Gate + Owner 评审）。

**备选方案（已否决）**：

| 方案                                                 | 否决理由                                                                                      |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 在每个 `Scenario` 上直接断言 `ValidationResult.ok`，不做跨场景聚合 | 能验"单场景对不对"，不能答"这一版整体变好还是变差"（无聚合、无基线、无回归门控），正是本 ADR 要补的空白                                  |
| 复用 `memory/evaluation/` 直接喂 `Scenario`             | 输入契约不同（E-1 吃 `MemoryReplayDataset`，本 ADR 吃 `Scenario`），强行复用会污染 E-1 语义；并列对称、命名区分更清晰        |
| 在 `benchmark/yolo_speed.py` 上加行为断言                 | 那是性能基准，关注点不同（FPS vs 正确性），混在一起会破坏其"纯性能"契约                                                  |
| 决策级 A/B 顺便覆盖 pipeline 回归                           | ADR-0031 `DecisionABRun` 唯一变量=Memory，无法表达"代码版本"轴；本 ADR 的 `BenchmarkABRun` 轴=代码版本，二者正交不可替代 |
| 直接用 `pytest` 断言分数阈值（无 Harness 组件）                  | 阈值散落测试、无基线对照、无指纹归因、无报告；不可 CI 化、不可复跑、不可解释                                                  |

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

- **复合分权重如何标定**：`BenchmarkScore` 的 `precision` / `recall` / `F1` / `suppression_rate` / `false_alarm_rate` / `mean_event_recall` 加权（mirror `memory/evaluation` 的 `BASE_WEIGHTS`）在阈值标定前 `calibrated=False`；权重与 `max_regression_delta` 的合理值留待首批场景集跑出分布后由 Owner 拍板（类似 E-1B 标定）。
- **`frames` 通道跑真实 detector 的成本边界**：ADR-0032 T7 规定真实 YOLO 为 opt-in；本 ADR 默认用 `detections` 通道（零模型）做回归主集，`frames` + 真实 detector 作为可选"链路保真"子集（在 Phase 1 `harness.py` 内 opt-in），其 CI 时长预算留待 Phase 1 harness 实现时与 CI 配置确定。
- **场景级 FN 下钻到决策级原因**：是否把场景级 FN 进一步关联到 ADR-0031 `DecisionTrace` 的 `SuppressReason`（如 `no_trigger_events` / `all_suppressed_normal` / `unroutable_event_type`）？本 ADR 仅预留"可选采集 trace"接缝，具体下钻留待后续切片或编排 ADR。
- **端到端编排**：ADR-0032 场景同时驱动感知层 + Memory 层（ADR-0028 D6）的串联如何纳入基准，归未来编排 ADR，本 ADR 不定义。

---

## 6. 实施切片（Phases）—— 严格三阶段，防范围膨胀

> **Owner 2026-08-09 决议：分阶段纪律**。设计虽完整（D1–D8），但**实施**严格分三阶段，**每阶段可独立合入、独立验收、零行为变化**。严禁 Phase 1 就把"企业级评估平台"一次做全——在还没有 ~10 个真实场景、未积累数据分布前，阈值与权重都是空谈。每个 Phase 的"不做"清单与"做"清单同样重要。

### Phase 1 · 最小闭环（先合，零行为变化，无需 Owner 门控评审）

**目标**：跑通 `Scenario → Harness → ScenarioScore → BenchmarkReport` 的最小链路，证明"仿真场景能被批量打分并产出可复现的离散指标报告"。

**做（MUST）**：
- `evaluation/schema.py`：`BenchmarkExpectation` 模型 + ADR-0032 `Scenario` 可选 `benchmark` 字段增量契约（向后兼容、`ScenarioValidator` 不消费）；
- `evaluation/metrics.py`：纯函数 `scenario_outcome_label` / `scenario_actual_label`（当前 = `bool(warnings)`，预留 `warning_policy.evaluate` 扩展点）/ `scenario_confusion` / `event_recall` / `event_precision` / `risk_shortfall` + `ScenarioScore` dataclass；
- `evaluation/report.py`：`BenchmarkReport` dataclass + `to_dict` / `render_markdown` / `write_report`，**仅聚合离散指标**（precision / recall / F1 / FN / FP / suppression_rate / false_alarm_rate + 每场景混淆 + `unlabeled_scenario_ids`），携带 `harness_fingerprint`（D4 三元组）；**不产出单一 `BenchmarkScore` 加权分**；
- `evaluation/harness.py`：`BenchmarkHarness.run(...)` 编排 ADR-0032 `ScenarioCompiler` → `ScenarioRunner.run` → `ScenarioValidator.validate` → `ScenarioScore` → `BenchmarkReport`；抽取 `scenario_set_id` + `model_fingerprint` 注入指纹；
- `scripts/run_benchmark.py`：手动/CI 可选入口（**不接线 demo/gateway**）。

**不做（MUST NOT）**：❌ Hard Gate ❌ `BenchmarkThresholds` ❌ `BenchmarkScore` 加权分 ❌ `BenchmarkABRun` ❌ 基线 JSON ❌ CI gate ❌ Owner approval 流程。Phase 1 的报告**给人读、人工判断**，不进任何自动门禁。

**验收**：T1（确定性）/ T2（零行为变化）/ T3（复用不重写）/ T4（指纹归因）/ T5（脱敏）/ T6（混淆可观测）；在 2–3 个 ADR-0032 场景上端到端跑通。

### Phase 2 · 回归能力（独立 PR，Owner 评审）

**目标**：回答"这一版 vs 上一版"——引入 old-vs-new 回归对比与可回放基线。

**做（MUST）**：
- `evaluation/ab_runner.py`：`BenchmarkABRun` + `assert_conserved`（D6 守恒集合）+ `BenchmarkDiff`（逐指标 Δ + 退化场景清单）；
- `evaluation/fixtures/baselines/<scenario_set_id>.json`：提交**上次良好**的 `BenchmarkReport`（确定性由完整 `harness_fingerprint` 锁定）；
- `evaluate_gate(report, baseline)` 的**轻量回归对照**：在已有基线上算 `BenchmarkDiff`、对照 `max_regression_delta`（此阶段为"报告性"对照，非 CI 强制门禁）。

**不做（MUST NOT）**：❌ Hard Gate 准入门禁 ❌ CI 非零退出 ❌ 复合分门控。Phase 2 只做"我能看到退化多少"，不做"退化就拦下"。

**验收**：T8（A/B 守恒）/ T10（基线可回放）+ 在两次相邻提交上跑出 `BenchmarkDiff`。

### Phase 3 · 生产门控（独立 PR，Owner 评审，门控评审）

**目标**：把 Benchmark 接入工程护栏——CI 集成、Hard Gate、Owner 审批基线 bump。

**做（MUST）**：
- `evaluation/gate.py`：`BenchmarkThresholds` + `evaluate_gate(report, thresholds, baseline=None) -> GateResult`（D5 Hard Gate 先于复合分）；
- CI 集成：纳入既有 `ruff check src tests`；新增 `tests/benchmark_*` 门控测试作为 CI gate（Hard Gate 失败 → 非零退出）；
- 基线治理：基线 JSON 变更须 PR 注明 `benchmark-baseline-bump`（CI 校验），Owner 评审。

**验收**：T7（Gate 先于 Score）+ CI 门禁端到端验证。

> **三阶段铁律**：Phase 1 合入后，Harness 即可日常产出报告；Phase 2 让人看见回归幅度；Phase 3 才让机器在 CI 拦下退化。**任何"提前把 Phase 2/3 内容塞进 Phase 1"的冲动都违反本 ADR 的分阶段纪律。**

### 验收清单（Acceptance Criteria，按阶段达成）

> Phase 1 达成 1/2/3/4/6/8/9/10；Phase 2 达成 7；Phase 3 达成 5。

1. **D1 边界清晰**：`evaluation/` 与 `memory/evaluation/` / `benchmark/yolo_speed.py` 命名区分、互不 import 实现；Harness 只消费 ADR-0032 产物；
2. **D2 复用不重写**：T3 通过；Harness 不调 generator/renderer、不重新校验事件 schema；
3. **D3 混淆可观测**：T6 通过；场景级 TP/FN/FP/TN + `suppression_rate` / `false_alarm_rate` 公式与语义正确；
4. **D4 指纹归因**：T4 通过；`BenchmarkReport` 携带 `(code, scenario_set, environment)` 三元组指纹（含 `scenario_set_id` / `model_fingerprint` / `runtime_dependencies`），缺字段即报错；
5. **D5 Gate 先于 Score（Phase 3）**：T7 通过；Hard Gate 先于复合分，空集视为不通过；复合分 `calibrated=False`、不用于门控；
6. **D6 A/B 守恒（Phase 2）**：T8 通过；`BenchmarkABRun.assert_conserved` 守恒集合全满足（默认 vary=代码版本、可切模型权重轴），与 ADR-0031 正交；
7. **D7 基线可回放（Phase 2）**：T10 通过；基线 JSON 可复现、回归 delta 对照 `max_regression_delta`、bump 须 PR 注明；
8. **D8 零行为 + 脱敏**：T2 / T5 通过；`evaluation/` 不进运行时、`git grep` 证明 demo/gateway/runtime 不 import；报告不含原始媒体/PII/设备/家庭/用户标识；附 trace 过 `assert_desensitized`；
9. **边界铁律**：全量 `ruff check src tests` + `pytest` 全绿（AGENTS.md 基线，无回归）；
10. **非目标守住**：无新重依赖、不重生成、不改规则行为、不做性能基准、不做 Memory 价值评估、不产出未标定 `BenchmarkScore` 单一加权分（§3 非目标逐条确认）。

---

## 7. 修订记录（Changelog）

> **修订权属（呼应 AGENTS.md §6.3）**：本 ADR 处于 Proposed 阶段由 Owner 评审；**冻结（Accepted）后的修订由 Owner 追加新条目，AI 不修改修订记录**。

- **2026-08-08**：初稿（Proposed）。承接 ADR-0032 落地的"可复现、隐私安全、确定性"场景仿真层，建立**感知级 Benchmark Harness**（`src/home_perception/evaluation/`，与 `memory/evaluation/` 并列、命名区分、对称复用其工程范式）：(1) **D1** 单一 `evaluation/` 包，明确与 `benchmark/yolo_speed.py`（性能）/ `memory/evaluation/`（Memory 价值 E-1）三块评估资产的边界；(2) **D2** 仅编排 ADR-0032 `ScenarioCompiler`/`ScenarioRunner`/`ScenarioValidator`，不重新生成、不重新校验事件 Schema；(3) **D3** 把 ADR-0031 的"漏报/误报可观测"**下沉到场景级混淆矩阵**（TP/FN/FP/TN + `suppression_rate`/`false_alarm_rate`），首次让整条 pipeline 在仿真场景上的漏报/误报可聚合、可回归；(4) **D4** `harness_fingerprint`（复用 ADR-0032 `generator.fingerprint` + ADR-0031 `policy.fingerprint` + `code_version`）使分数变化可归因；(5) **D5** Hard Gate 先于 Score（mirror `memory/evaluation`）；(6) **D6** `BenchmarkABRun` + `assert_conserved` 六条守恒，**唯一变量=代码版本**（与 ADR-0031 `DecisionABRun` 唯一变量=Memory 对称但不同轴）；(7) **D7** 提交 `BenchmarkReport` 基线 JSON 到 `evaluation/fixtures/baselines/`、回归 delta 对照 `max_regression_delta`、bump 须 Owner 评审；(8) **D8** 零生产行为变化（`evaluation/` 仅被 `scripts/run_benchmark.py` 与 `tests/` 引用，不进 demo/gateway 运行时）+ 全程脱敏（报告不含原始媒体/PII/设备/家庭/用户标识，附 trace 过 ADR-0031 `prune_jsonl`/`assert_desensitized`）。T1–T10 不变式钉死确定性/零行为变化/复用不重写/指纹归因/脱敏/混淆可观测/Gate先于Score/A/B守恒/单一职责/基线可回放。实施分 Slices A–D（A/B 零行为变化先合；C/D 门控评审、单独 PR + Owner 评审）。非目标：不重生成/不重校验Schema/不改规则行为/不拥有决策级A/B/不做性能基准/不做Memory价值评估/不做端到端编排/不入库原始媒体/不引新重依赖。本 ADR 闭合 ADR-0032 §0.6 的 AI 验证基础设施闭环（Simulation→Pipeline→Trace→Benchmark）。
- **2026-08-09**：Owner 评审 refinements（三点修正）：(1) **D4 指纹升级为 `(code, scenario_set, environment)` 三元组**——新增 `scenario_set_id`（标识"测的是哪批案例"）与 `model_fingerprint`（detector/tracker/event-extractor 权重哈希，覆盖非 git 管理的 YOLO `model.pt` 等），原 `numpy_version`/`opencv_version` 散字段合并为 `runtime_dependencies`；(2) **D3 引入 `BenchmarkExpectation` 语义分离**——`benchmark.expected_alarm` 由场景作者显式声明、不再从 `expects`（`emitted_event_types`/`min_risk_level`）粗糙推导，解决"测试期望 ≠ 安全评价标签"；`benchmark` 作为 ADR-0032 `Scenario` 的可选字段（向后兼容、`ScenarioValidator` 不消费），`benchmark=None` 的场景排除出混淆矩阵；(3) **D6 A/B 守恒扩展 `model_fingerprint` 为守恒轴**——`assert_conserved` 新增"模型权重/版本守恒"约束（防"同代码、不同 YOLO 权重"被误判为纯代码回归），并引入 `vary` 轴（默认 `code_version`、可切 `model_fingerprint`），守恒由六条扩展为七条。T1/T4/T8 与验收清单同步更新。仍 Proposed，待 Owner 冻结。
- **2026-08-09（续）**：Owner 评审——**分阶段实施纪律（防范围膨胀）**。(1) **实施切片由 A–D 重组为三阶段 Phase 1/2/3**：Phase 1（最小闭环）只做 `Scenario → Harness → ScenarioScore → BenchmarkReport`，支持 TP/FN/FP/TN + report JSON + fingerprint，**明确不做** Hard Gate / `BenchmarkScore` 加权分 / `BenchmarkABRun` / 基线 JSON / CI gate / Owner approval；Phase 2 才加 `BenchmarkABRun` + `BenchmarkDiff` + 基线做 old-vs-new 对照；Phase 3 才接入 CI + Hard Gate + 基线 bump 治理。理由：尚无 ~10 真实场景与数据分布前，阈值/权重都是空谈，避免"未积累场景已设计完企业级评估平台"。(2) **D5 门控与复合分明确推迟到 Phase 3**；**D7 基线推迟到 Phase 2**。(3) **不要过早数学化（point 10）**：安全指标非线性（FN penalty >>> FP penalty），Phase 1 只报告离散指标 precision/recall/F1/FN/FP + 率，**不产出单一 `BenchmarkScore` 加权分**；即便 Phase 3 引入复合分也必须 `calibrated=False` / experimental 且不得用于门控（沿用既有 `calibrated=False` 规避）。(4) **`actual_label` 扩展点（point 9）**：当前 = `bool(warnings)`（可接受），但预留 `warning_policy.evaluate(warnings)` 接缝——未来 `low_confidence` / 调试期 warning 不应计入误报，Phase 1 不实现该策略。§3 非目标新增第 10 条（不产出未标定单一加权分）；§6 重写为三阶段；验收清单按阶段标注（Phase 1 达成 1/2/3/4/6/8/9/10，Phase 2 达成 7，Phase 3 达成 5）。仍 Proposed，待 Owner 冻结。
- **2026-08-09（续2）**：**Owner 审核通过，ADR 冻结为 Accepted（设计定稿）**。进入实施阶段：首个可合入切片为 **Phase 1（最小闭环，零行为变化、无需门控评审）**；Phase 2（回归能力）/ Phase 3（生产门控）各自独立 PR + Owner 评审。自本条目起修订权属归 Owner（AGENTS.md §6.3），AI 不再修改修订记录。
