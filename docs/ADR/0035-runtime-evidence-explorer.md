# ADR-0035: Runtime Evidence Explorer（运行证据探索器 · Evidence Presentation Layer）

- **Status**: Proposed（2026-08-12 起草 + 两轮 Owner 评审收紧，待 Owner 定稿）
- **Date**: 2026-08-12
- **Owner**: SilverShield 技术负责人
- **Implementation Plan**: 本 ADR 定稿后随 D1 启动补充（非冻结件）
- **Related**:
  - ADR-0031（决策审计血缘 · `DecisionTrace` 五 bundle：identity/provenance/policy/rationale/outcome + `JsonlTraceRecorder` —— **Decision Trace 视图的数据源**）
  - ADR-0024（Memory 架构 · `EpisodicRecord` 九字段投影——**Episode 视图的数据源**）
  - ADR-0027 / ADR-0028 / ADR-0029（跨模态关联 · `CrossModalLink` 边（link_id/episode_ids/relationship/time_overlap/confidence）——**Cross Modal Graph 视图的数据源**）
  - ADR-0032（场景仿真层 · `frames` 通道程序化 BGR 帧 + `detections` 通道——**D2 Replay 与 D3 程序化视频的资产**）
  - ADR-0034（闭环集成验证 · `IntegrationReport` canonical/gate/fingerprints artifact + `IntegrationRunner` 探针链路——**Fingerprint/Gate 视图的数据源 + D1 的输入契约**）
  - ADR-0014（三级冻结治理 · 只读消费、零行为变化纪律）/ AGENTS.md §6.1（模块边界）
- **Phase**: v2 · ADR-0030 → 0031 → 0032 → 0033 → 0034 → **ADR-0035（本 ADR，展示层）**

---

## 0. 背景与动机（Why）

### 0.1 瓶颈转移：从"系统是否可信"到"别人是否相信"

ADR-0034 Phase C 完成后，验证体系已经能**机器化地回答**"闭环是否按契约运转"（F1–F6 失败模型、两枚指纹、severity 门禁）。但项目面临的新瓶颈不是技术，而是**解释**：

> 银龄盾的本质不是"展示数据"，而是回答——**为什么系统认为这里有风险？**

当前所有证据资产（Scenario / Runtime Trace / Episode / Decision / Action / Cross Modal Link / Benchmark Score / Fingerprint）都以 JSON artifact 形式存在，**只有工程师能读**。面向 Owner / 评委 / 投资人 / 家属，"系统真的在工作"这一结论无法被看见、被理解、被相信。

### 0.2 关键洞察：证据链本身就是天然的时间轴叙事

与本项目"程序化视频"（ADR-0032 `frames` 通道 + ADR-0027 audio tts 确定性合成栈）完全同构，闭环运行天然生成：

```
Runtime Trace → 时间轴节点 → 状态变化 → 因果关系
```

一份 IntegrationReport 不是一堆无关 JSON，而是**一条可回放的故事线**：

```
18:00:00 Camera Frame
18:00:02 Person detected（Track ID=23）
18:00:05 abnormal_dwell event → Decision: WARN / risk=HIGH → Action: SEND_FAMILY_MESSAGE
18:00:06 Memory Episode created → CrossModal Link（vision ←supports→ audio）
```

这比传统 dashboard（只看指标）价值高得多——**它回答"为什么"**。

### 0.3 数据资产现状与来源边界（关键收紧点）

**本 ADR 不新增任何采集 / 存储 / 判定逻辑**——只消费已落盘资产做**只读展示**。但"消费哪些"必须逐节点钉死，否则 D1 实现时会出现 `timeline.append("Frame")` 式**人为拼装**，违反"零新增采集"。

| 展示节点 | 数据源（真实落盘） | 可用性 |
|---|---|---|
| Scenario 锚点 | `Scenario.meta`（scenario_id/声明） | ✅ fixture YAML |
| Frame / Detection | ADR-0032 `detections` 通道的**声明式 per-frame detections**（fixture YAML `actors.tracks`），或 IntegrationReport 的感知摘要（`artifacts.counts`） | ⚠️ **D1 只投影已有粒度**（见 D2：缺失粒度渲染为 stage 摘要节点，**禁止合成帧级细节**） |
| Event / Warning | IntegrationReport 感知摘要（`event_types` / `risk_levels`） | ✅ |
| Decision | ADR-0031 `DecisionTrace`（`TraceOutcome` / `TraceRationale` / `TracePolicy`） | ✅ |
| Action | ADR-0034 `ActionSink` 探针（`sink_commands` / `command_types`） | ✅ |
| Episode | ADR-0024 `EpisodicRecord` | ✅ |
| CrossModal Link | ADR-0027/28/29 `CrossModalLink` | ✅ |

> **规则**：渲染节点**必须来自 artifact 真实字段**；artifact 里没有的粒度（如逐帧像素细节）**不渲染**或投影为 stage 级摘要节点——绝不捏造。

### 0.4 定位声明

> **ADR-0035 建立 Runtime Evidence Explorer，作为 SilverShield 的 Evidence Presentation Layer。它消费 ADR-0031/0032/0034 产生的可信 artifact，将机器可验证的运行证据转换为人类可理解的时间轴、因果链和关系图。该层不参与运行、不参与判断、不改变系统行为。**

---

## 1. 决策（Decision）

### D1 · 形态：Evidence Presentation Layer，不是"验证资产"

采纳命名 **Runtime Evidence Explorer（运行证据探索器）**。定位是**第四层可信工程资产（Evidence Presentation Layer）**——与 `evaluation/`（验证感知质量）、`integration/`（验证闭环正确性）**层级并列但职责不同**：本层**没有验证能力**，只做"证据呈现"。语义上绝不能说"visualizer 验证了系统"。

- 命名刻意避开 `frontend/` / `dashboard/`：它不展示业务指标，只**回放与解释运行时证据**；
- 落点：`src/home_perception/visualizer/` + 入口 `scripts/run_evidence_explorer.py`。

### D2 · 数据投影契约（EvidenceProjection → EvidenceTimelineArtifact）

**visualizer 的输入不是原始 artifact 直读，而是投影契约**。新增 `EvidenceProjection`（visualizer 内部加载层）：把 IntegrationReport / DecisionTrace / Episode / Link 的原始 JSON **投影**为 `EvidenceTimelineArtifact`——时间轴节点的规范化结构：

```json
{
  "timeline": [
    {
      "timestamp": "18:00:02",
      "stage": "perception",
      "type": "Detection",
      "summary": "person detected (track=23)",
      "provenance_kind": "FIXTURE",
      "ref": "det-001"
    },
    {
      "timestamp": "18:00:05",
      "stage": "decision",
      "type": "Warning",
      "summary": "abnormal dwell",
      "provenance_kind": "SIMULATED",
      "ref": "trace-e158"
    }
  ]
}
```

**职责链**：

```
IntegrationArtifact（已落盘）
      │  投影（loader：字段映射 + 结构谓词校验，缺字段 fail-closed）
      ▼
EvidenceProjection（visualizer/schema 定义的类型实例）
      │  渲染（renderer：HTML 生成）
      ▼
自包含 HTML
```

**硬规则**：
1. **投影是唯一入口**：渲染层**只能消费 `EvidenceProjection`**，禁止直接 `json.load` 后自由拼装；
2. **ref 必填**：每个节点携带 `ref`（指向源 artifact + 记录 id，见 D8 provenance 验收），无 ref 的节点渲染层直接拒绝；
3. **缺失粒度降级**：artifact 无帧级数据时，时间轴渲染 stage 摘要节点（如 `perception stage: 3 events`），**禁止合成**帧/检测细节；
4. **provenance_kind 必填**：每节点标注 `REAL_SENSOR` / `SIMULATED` / `FIXTURE`（见 D7b）——展示层不得让观看者把合成/夹具帧误认为真实录像；未来真实设备接入时该字段自然承载 `REAL_SENSOR`，无需重构。

### D2b · Schema Evolution Fail-Closed（长期风险防护）

Evidence Layer 最大的长期风险是 **artifact schema 演化**（如 ADR-0034 的 `decision_id` 未来变成 `decision.identity.id`）。若投影层默默容忍，页面会出现 `Decision: undefined` 而无人发现。

**规则**：投影层是 **fail-closed** 的——删除/改名关键字段 → 投影必须**抛错拒绝**，绝不产出空白页面。与 ADR-0034 的 fail-closed 精神一致（缺字段 = 无法复述"这次怎么跑的"，比渲染空白更危险）。

```
旧 artifact
      │
      ▼
projection
      │
字段缺失 → FAIL-CLOSED（抛错，拒绝生成）
      │
（字段齐全）→ EvidenceTimelineArtifact
```

### D3 · 消费协议：禁 import 生产类，但保留类型安全

- **禁止** import `runtime` / `evaluation` / `integration` / `memory` 的任何生产类（AST 契约守护——visualizer 在 import 图中是死胡同叶子，循环依赖风险为零）；
- **允许** `visualizer/schema/` 定义自己的 **TypedDict / dataclass**（如 `DecisionEvidence`、`TimelineNode`、`EvidenceGraphNode`）作为投影目标类型：

```
visualizer/
    schema/
        evidence.py      # DecisionEvidence / TimelineNode / EvidenceGraph...
        gate.py          # GateVerdict / FingerprintPair
    loader.py            # IntegrationArtifact → EvidenceProjection（D2）
    renderer.py          # EvidenceProjection → HTML（D4）
    assets/              # vendored ECharts（~1MB）
```

> 理由：完全裸 JSON 消费会损失类型安全——JSON 演化后"字段改名 → 页面空白 → 没人发现"。`schema/` 让投影层有结构契约（缺字段 fail-closed），同时不触碰生产代码。

### D4 · 技术栈：分层引入，D1 零新增 Python 依赖

| 阶段 | 后端 | 前端 | 理由 |
|---|---|---|---|
| **D1 MVP** | Python stdlib 静态生成**自包含单页 HTML**（零服务器） | **ECharts 单框架**（vendored 到 `visualizer/assets/`，~1MB；graph 系列已覆盖关系图需求） | "一次运行 → 可视化"；artifact 上传后浏览器直开；零服务器运维 |
| **D2 Replay** | **FastAPI**（读 artifact + 时间轴交互/重放） | ECharts + 少量 **D3.js**（自定义动画/力导向补充） | Replay 需要交互服务；D3 只在 ECharts 力有不逮时引入 |
| **D3 Demo** | 复用 ADR-0032 `frames` 通道 + ADR-0027 audio tts 确定性合成栈 | OpenCV 渲染（已有） | 程序化视频，零新资产 |

> 决策理由：项目纪律是"不引重依赖"。D1 用静态生成 = **Python 侧零新增依赖**，前端仅 vendored 一个 JS 库；FastAPI/D3 推迟到真正需要交互的 D2，避免"D1 就背上服务器 + 双框架"。

### D5 · Evidence Graph：统一底层抽象（核心概念）

Timeline / Decision Trace / Cross Modal Graph 三个视图**共享同一底层结构**——`EvidenceGraph`：

```
节点（Node）：Scenario / Detection / Event / Decision / Action / Episode / Link
边（Edge）：  caused_by / supports / derived_from / triggered / stored_as
```

各视图是 Evidence Graph 的**投影视角**：

| 视图 | = Evidence Graph + |
|---|---|
| Scenario Replay Timeline | 时间戳排序（Evidence Graph + timestamp） |
| Decision Trace | 决策子树（Evidence Graph + decision subtree） |
| Cross Modal Graph | 关系边（Evidence Graph + relationship edges） |
| D3 程序化视频 | 动画（Evidence Graph → Animation） |

> **派生模型边界（评审收紧）**：**Evidence Graph 是展示层派生模型（presentation-layer derived model），不属于运行时领域模型，不作为 runtime 状态交换协议**。它只由 `EvidenceProjection` 从已落盘 artifact 构造，runtime 完全不知晓其存在——防止未来出现 "runtime 写 EvidenceGraph 供 visualizer 读" 的反向污染（同 D3 零 import 边界的镜像约束）。

> 意义：① 三层视图不各自造数据结构；② 未来 D2/D3 直接消费同一图结构做重放/动画；③ 每个节点天然带 provenance（D8）。

### D6 · 四视图（D1 MVP 必达）

1. **Scenario Replay Timeline（最高价值）**：调试器式垂直时间轴（基于 Evidence Graph + timestamp）——`Frame → Detection(Track ID) → Event → Decision(WARN/HIGH) → Action(SEND_FAMILY_MESSAGE) → Episode created → CrossModal Link`，每节点可点开看证据细节（含 `ref` 溯源）；
2. **Decision Trace**：`为什么报警？` 五 bundle 逐步展开——检测证据（停留 5 分钟）→ 规则（abnormal_dwell）→ 策略（elderly_scam_policy_v3）→ 决策（WARN_HIGH）→ 动作（通知家属）；
3. **Cross Modal Graph**：episode 节点 + `supports` 边（`CrossModalLink.relationship` / `time_overlap` / `confidence` 入边标注）——接近"家庭数字孪生"展示形态；
4. **Fingerprint / Gate**：`expectation_fp` / `loop_fp` / Runtime（python/numpy/opencv 版本）+ 逐 stage verdict（✓/✗ + severity）+ `passed/degraded` 结论。

### D7 · 脱敏与隐私（继承项目铁律）

展示层同样过脱敏姿态：**不渲染原始媒体路径 / 设备序列号 / 家庭 ID / 任何 PII**。投影层做**白名单字段投影**（只挑"可复现性要素 + 判定结论"），结构上装不下敏感值（同 ADR-0034 `LoopArtifactSummary` 的"结构上就装不下"思路）。

### D7b · Presentation Identity Policy（身份呈现策略）

Evidence Explorer 本质是 demo 展示，未来必然有人要求"把老人名字显示出来更真实"——**必须从结构上拒绝**。

**规则**（白名单呈现，身份一律脱敏为角色标签）：

| 允许（角色化） | 禁止（真实身份） |
|---|---|
| `Resident-A` / `Visitor-B` | 真实姓名 / 昵称 |
| `Device-01`（部署源别名） | 设备序列号 / MAC |
| `home_entry_01`（ADR-0028 部署源标识） | 家庭地址 / 门牌 |
| 手机号 / 身份证 / 社交账号 | 一律禁止 |

`provenance_kind`（D2 硬规则 4）与 Identity Policy 配套：前者防"合成当真实"，后者防"角色当真实"——两者共同守住"证据可信"底线。

### D8 · 确定性 + Evidence Provenance + Schema 兼容（对齐 canonical 精神）

- **确定性**：同 artifact 两次生成 → 输出 HTML **逐字节一致**（剔除时间戳/路径类易变字段，或固定注入）；
- **Evidence provenance**：每个渲染节点携带 `ref`（`source artifact + 记录 id`，如 `decision_trace.jsonl / decision_id=e158`），点击节点可见溯源——"只是漂亮图"不算交付；
- **Schema 兼容 fail-closed**：见 D2b——artifact 关键字段演化导致缺失时，投影**必须抛错拒绝**，不产出空白页面（验收第 10 条守护）。

### D9 · 零行为变化

`visualizer/` 只读 artifacts + 生成 HTML：不触碰生产 runtime / 验证判定 / 基线文件；`scripts/run_evidence_explorer.py` 默认退出码 0（生成即成功），供人工查看；不接任何 CI 门禁（它是"给人看"的资产，不是"拦合入"的闸）。

---

## 2. 定位（与既有资产的关系）

```
artifacts/（ADR-0031/0024/0027/0034 落盘）
      │ 只读（D2 投影契约）
      ▼
visualizer/（本 ADR：Evidence Presentation Layer · 第四层可信工程资产）
      │ stdlib 生成（D4）
      ▼
自包含 HTML（Timeline / Decision Trace / CrossModal Graph / Fingerprint-Gate）
      │（D2）FastAPI Replay →（D3）程序化视频 Demo
      ▼
Owner / 评委 / 投资人 / 家属 —— "为什么系统认为这里有风险？"
```

三层评估资产并列（职责互补、互不拥有）：`evaluation/`（感知级**验证**）→ `integration/`（闭环级**验证**）→ `visualizer/`（**呈现**，无验证能力）。

---

## 3. 非目标（Non-Goals）

- ❌ 不做实时监控前端 / 真实设备 UI（Phase E 之后的产品化，另议）；
- ❌ 不做"大而全"产品界面（React/多页路由/组件库等一概不做）；
- ❌ 不新增采集/存储/判定（零新探针、零新 schema 写入、零新 JSON 直读拼装）；
- ❌ **不捏造节点**：artifact 缺失粒度一律降级为 stage 摘要或省略，绝不合成（D2 硬规则）；
- ❌ 不引入 LLM 解释（v2 才做，AGENTS.md §6.1 禁区）；
- ❌ 不接 CI 门禁（展示层不是闸）；
- ❌ 不 import 生产/验证代码（D3 硬边界；`visualizer/schema/` 自建类型例外）；
- ❌ 不重写既有时间轴/图表组件（ECharts 配置式覆盖，不手写 D3 除非 D2 力有不逮）；
- ❌ 不把程序化视频当"真实监控录像"宣传（沿用 ADR-0015 数据真实性声明精神）。

---

## 4. 代价与备选方案

| 方案 | 代价 | 结论 |
|---|---|---|
| 产品级前端（React + 服务端） | 双语言团队成本、重依赖、背离"呈现资产"定位 | 否决：D1 只需"一次运行→可解释网页" |
| D1 即上 FastAPI + ECharts + D3 | 服务器运维、双框架、D1 交付变重 | 否决：分层引入（D4），D1 静态、D2 才上服务 |
| 只做静态 markdown 报告（现状） | 无可交互时间轴/图，解释力不足 | 否决：正是要解决"看不到" |
| 复用既有 dashboard（`silver_demo`） | 展示业务 Demo 指标，非运行证据；冻结边界（ADR-0015） | 否决：方向不同，不穿透冻结层 |
| 渲染层直接 `json.load` 自由拼装 | 字段演化 → 页面空白无人知；节点来源不可控 | 否决：必须经 D2 投影契约 + D5 Evidence Graph |

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

1. **D2 Replay 的动画语义**：时间轴动画是"逐帧步进"还是"因果链展开"？留 D2 设计；
2. **ECharts 资产 vendored vs CDN**：仓库体积 vs 离线可用性权衡（D1 建议 vendored + 可选 CDN 开关）；
3. **visualizer 是否复用于产品化**：若未来做产品 UI，visualizer 的视图组件可否抽取？留 Phase E；
4. **Evidence Graph 的边类型闭集**：`caused_by/supports/derived_from/triggered/stored_as` 是否够用，D1 落地时按真实 artifact 字段定稿（白名单，不许自由字符串）。

---

## 6. 实施切片（概要）

> **分阶段铁律**（沿用 ADR-0033/0034）：D1/D2/D3 独立 PR + Owner 评审，严禁一次做全。

| 阶段 | 目标 | 达成的决策 | 明令不做 |
|---|---|---|---|
| **D1** | Evidence Explorer MVP：一次运行 → 可视化（实现顺序：**Timeline → Decision Explanation → Graph**，由价值从高到低推进） | D1/D2/D2b/D3/D4(静态)/D5/D6/D7/D7b/D8/D9 | ❌服务器 ❌D3/D2 动画 ❌跨进程读 Memory ❌帧级合成 |
| **D2** | Replay Engine：Scenario + Trace 重放动画 | D4(FastAPI + D3 按需) + Evidence Graph → Animation | ❌程序化视频合成 ❌真实设备 |
| **D3** | Product Demo：程序化视频（比赛/投资人/用户） | 复用 ADR-0032 frames + ADR-0027 audio tts | ❌真实录像 ❌实时 |

---

## 7. 验收标准（Acceptance Criteria，D1 先行）

1. **输入契约**：`scripts/run_evidence_explorer.py --artifacts artifacts/adr0034_integration/` → 输出自包含 HTML（可浏览器直开，无服务器）；
2. **四视图全覆盖**：Timeline / Decision Trace / Cross Modal Graph / Fingerprint-Gate 各至少渲染一个真实场景数据；时间轴含 Frame→Detection→Event→Decision→Action→Episode→Link 全链节点（缺失粒度按 D2 降级，不捏造）；
3. **零 import 边界**：AST 契约测试——`visualizer/` 源码不含任何 `from home_perception.(runtime|evaluation|integration|memory)` import；`visualizer/schema/` 自建 TypedDict 除外；
4. **脱敏**：渲染产物不含原始路径 / 设备序列号 / PII（白名单投影测试）；
5. **确定性**：同 artifact 两次生成逐字节一致（t 级断言）；
6. **Evidence 完整性（新增）**：所有渲染节点必须来自 artifact 真实字段——测试注入伪造节点（如凭空 `timeline.append("Frame")`）→ 渲染拒绝/报错；**禁止 synthetic node**；
7. **Evidence provenance（新增）**：每个渲染节点可追溯——点击节点显示 `source: <artifact 文件名> / <记录 id>`；测试断言所有节点 `ref` 非空且能定位到源 artifact；
8. **D9 零行为**：既有全量 `ruff check src tests` + pytest 全绿（无回归）；`run_evidence_explorer.py` 不接 CI 门禁；
9. **文档**：`docs/ADR/README.md` 登记本 ADR；D1 PR 描述附真实渲染截图；
10. **Projection compatibility（新增）**：删除 artifact 关键字段（如 `loop_fingerprint` / `decision_id`）→ 投影**必须 fail-closed 抛错拒绝生成**，绝不产出含 `undefined` 的空白页面（D2b Schema Evolution 守护；与 ADR-0034 fail-closed 同纪律）。

---

## 8. 修订记录（Changelog）

> **修订权属（AGENTS.md §6.3）**：Proposed 阶段由 Owner 评审；冻结（Accepted）后的修订由 Owner 追加新条目。

- **2026-08-12**：初稿（Proposed）。采纳 Owner 蓝图：命名 **Runtime Evidence Explorer**（非 Frontend）；四视图；数据源全为已落盘资产零新增采集；D1 静态单页 HTML（stdlib + ECharts 单框架）→ D2 FastAPI+D3 Replay → D3 程序化视频 Demo；`visualizer/` 与 evaluation/integration 平列；纯 JSON 消费零 import 生产代码；脱敏 + 确定性 + 零行为变化。
- **2026-08-12（Owner 评审收紧一）**：五项收紧 + 两项新验收。(1) **定位修正**——`visualizer` 不是"第四层验证资产"（无验证能力），改 **Evidence Presentation Layer / 第四层可信工程资产**，新增 §0.4 定位声明；(2) **D2 数据投影契约**——新增 `EvidenceProjection` → `EvidenceTimelineArtifact` 投影层（loader 唯一入口、`ref` 必填、缺失粒度降级为 stage 摘要、**禁 synthetic node**），§0.3 逐节点钉死数据源（Frame/Detection 仅投影已有粒度）；(3) **D3 放宽**——禁 import 生产类不变，但允许 `visualizer/schema/` 自建 TypedDict（`DecisionEvidence`/`TimelineNode` 等）补类型安全，防 JSON 演化静默空白；(4) **D5 Evidence Graph 统一抽象**——节点（Scenario/Detection/Event/Decision/Action/Episode/Link）+ 边（caused_by/supports/derived_from/triggered/stored_as），Timeline/Decision Trace/Cross Modal/程序化视频均为其投影视角；(5) 验收 +2：**Evidence 完整性**（禁 synthetic node，注入伪造节点渲染必须拒绝）+ **Evidence provenance**（节点 `ref` 可溯源到源 artifact+记录 id）。仍 Proposed，待 Owner 定稿。
- **2026-08-12（Owner 评审收紧二）**：五项补充（Accepted 前）。(1) **D5 派生模型边界**——Evidence Graph 明确为 **presentation-layer derived model，不属于运行时领域模型，不作为 runtime 状态交换协议**（防 runtime→visualizer 反向污染，与 D3 零 import 镜像）；(2) **D2 `provenance_kind` 必填**——每节点标注 `REAL_SENSOR`/`SIMULATED`/`FIXTURE`，防观看者把合成/夹具帧误认为真实录像，未来真实设备接入无需重构；(3) **D2b + D8 Schema Evolution Fail-Closed**——artifact 关键字段演化缺失 → 投影必须抛错拒绝，绝不产出 `undefined` 空白页面（与 ADR-0034 fail-closed 同纪律）；(4) **D7b Presentation Identity Policy**——身份一律脱敏为角色标签（允许 `Resident-A`/`Visitor-B`/`Device-01`，禁止真实姓名/手机号/家庭地址/设备序列号），从结构上拒绝 demo 阶段"显示真实身份更真实"的突破边界诉求；(5) **验收 +1（共 10 条）**——Projection compatibility：删关键字段 → 投影必须失败。另：D1 实现顺序定为 **Timeline → Decision Explanation → Graph**（价值从高到低）。仍 Proposed，待 Owner 定稿。
