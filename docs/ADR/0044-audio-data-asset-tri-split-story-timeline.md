# ADR-0044: 音频数据资产三层解耦与 StoryTimeline 单一真相源契约

- 状态：Proposed
- 日期：2026-08-23
- 决策者：Owner（六项决策 2026-08-23 拍板，本文档形式化）
- 相关：ADR-0026（音频感知链）/ ADR-0032（场景仿真层）/ ADR-0042（Evidence Strength）/
  `docs/reports/TIER1-QUALIFICATION-GATE-RUN1-2026-08-23.md`（RUN1 + B-hard 决策 b）/
  `docs/reports/LAYER2-PRE-FREEZE-REVIEW-2026-08-23.md` / PR #299/#300/#301

## 背景（Context）

Tier1 Semantic Qualification RUN1 达成 CONDITIONAL PASS 后，Owner 对 9 条 Layer2
telephone 样本的资产审查暴露出一个此前被掩盖的结构问题：**三类不同目标的数据被混在
同一套资产里**——

```text
① 模型资格验证   电话/铃声/忙音/警报/音乐/家电 → YAMNet/Tier1 语义分类是否可信
② Runtime 能力验证 AudioPerceptionEvent → RiskSignal → DecisionInput → Warning → Action → 工程链路是否通
③ Product Story  接通电话→双向对话→持续交互→视觉上下文→多模态证据→风险解释→行动闭环 → 产品故事是否成立
```

Layer2 的 24 条候选属于 ①（其中 telephone 类内部又混装三种叙事属性迥异的素材：
双人真实对话 / 单人信道 speech / 信令音）；Gate F/G 证明了 ②；真正缺失的是 ③。
把 dial/busy tone 硬塞进 Live UI 会产生"AI 检测到 telephone → 然后呢？"的叙事断裂，
甚至诱导出「busy tone 包装为老人正在接听」的语义造假。

同时存在一个流程风险：若让 Browser Product Acceptance 等待两级 Runtime 管道
（ADR-0026 Tier0/Tier1 的 `persistent_narrowband_candidate` 实施设计尚未落 ADR）
完成，产品验证会被工程实现反向锁死——即「为了让 E2E 通过而反过来设计 Runtime」。

## 决策（Decision）

Owner 六项决策（2026-08-23）：

| # | 决策 | 结论 |
| --- | --- | --- |
| D1 | B-hard 处置 | **选 b：登记为 limitation，不升格 PASS**。RUN1 保持 `B = CONDITIONAL PASS; hard-layer limitation = registered, non-blocking for current Layer2 qualification scope`。理由：LBJ×2 已证明「电话通话语境」（语义真实性），但窄带频响+线路失真未逐条确认（信道真实性），两者不得混同宣告 |
| D2 | Fixture 执行路径 | **允许声明式回放**，不要求现在走真实两级管道 |
| D3 | Fixture 真相源 | **StoryTimeline 为唯一真相源**，不绑定具体 Runtime 产生方式；时间轴固定，事件怎么产生是次级属性 |
| D4 | Runtime provenance | Fixture 必须显式标记 `synthetic_replay / runtime_generated / real_sensor` 三态 |
| D5 | Benign fixture | **必须与 risk fixture 同时建立**——benign case 验证核心产品命题：「识别到电话 ≠ 识别到诈骗风险」（telephone_persistent + 正常双向对话 + 无可疑视觉证据 → MONITOR、不升级、不通知家属） |
| D6 | Browser Acceptance | 先以 synthetic_replay 验证 UI/叙事/时间故事；两级真实 Runtime 完成后用**同一 oracle** 重跑 runtime_generated；real_sensor 为 Final Acceptance |

配套决策：

- **数据资产目录物理分离**（详见规格文档迁移映射表）：
  `dataset/_canonical/audio_semantic/{qualification/, product_story/}`；
  qualification 资产完成使命后保持冻结，不再承担 Demo 职责；
- **Story Contract 固定，Runtime 是实现者**——两级管道实施 ADR 的职责是成为
  StoryTimeline 的 `runtime_generated` 执行路径提供者，而非反过来；
- **telephone_persistent ≠ 电话响了**：Fixture 时间轴中 signaling（t=0 接通/振铃）
  与 telephone_persistent evidence（持续交互语义成立）是两个独立事件节点。

## 动机（Rationale）

1. **实测证据**：RUN1 三口径（A 100% / C 0% / D 100%）+ Gate F/G E2E 过闸证明 ①②
   层能力已就绪；瓶颈唯一地位于 ③ 叙事构造层。继续扩大音频搜集边际收益趋零。
2. **测试资产分层原则**：模型资格 fixture 与端到端产品验收 fixture 本就是不同层级，
   混用导致「明明快完成了怎么又不行」的假性倒退感。
3. **解耦收益**：未来换模型 / 调 Tier0 / 升级 YAMNet 不再反复拖 UI 回归；
   StoryTimeline schema 三态 provenance 让 Layer2→Layer3 平滑演进。
4. **边界纪律**：benign/risk 双 fixture 把「识别到电话≠诈骗风险」（ADR-0001 边界铁律
   的产品面）变成可回归验证的命题，而非口号。

## 后果（Consequences)

正面：

- 产品叙事可立即闭环（Browser Acceptance 不被 Runtime 实现阻塞）；
- qualification 数据使命清晰收口（RUN1 冻结），不再被期待讲产品故事；
- 同一 oracle 三态重跑 = 从 Demo 到现场验收的可比性基线。

负面 / 技术债：

- 需新建 product_story dataset 与 StoryTimeline schema（构造成本，但规模刻意最小化：
  一条 risk + 一条 benign 优先于数量）；
- 历史报告中 `tier1/tier2_qualification` 路径引用因目录迁移失效，以迁移映射表衔接
  （规格文档 §5）；
- B-hard 限制永久登记于 RUN1 报告，后续若需解除须补信道真实性逐条证据。

后续动作：

1. Agent：产出 `TELEPHONE-RISK-STORY-FIXTURE-CONTRACT-2026-08-23.md` 规格（本 ADR 配套）；
2. Agent：按契约构造首对 benign/risk fixture（synthetic_replay）→ Browser Acceptance RUN1；
3. Agent：两级管道实施 ADR（作为 runtime_generated 提供者）；
4. Owner：MONITOR ceiling 解除拍板（依据 RUN1 §4 材料）。

## 替代方案（Alternatives)

| 方案 | 否决原因 |
| --- | --- |
| 继续用 qualification 样本硬凑 Demo | dial/busy tone 无法支撑风险叙事；强行包装构成语义造假，违背 ADR-0001 |
| 寻找真实诈骗录音做 Demo | 敏感内容获取/伦理/license 不可行且无必要——需要的是真实语义结构，不是真实诈骗话术（合成拼接即可承载） |
| 仅逻辑分类、不物理分目录 | 污染风险重演（qualification 目录继续吸收 demo 资产）；物理分离是防复发的结构保证 |
| Browser Acceptance 等待两级管道完成 | 「E2E 反向设计 Runtime」锁死；产品验证被工程排期绑架 |