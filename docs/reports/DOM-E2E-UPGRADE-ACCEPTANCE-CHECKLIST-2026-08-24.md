# 产品化收口路线与三层验收清单 v3.2（2026-08-24）

> **性质**：telephone_risk 产品场景收口的 SSOT 路线文档。v1/v2 的"单一 backlog"与"三层 Gate"结构经 Owner 2026-08-24 阶段重定位后整合升级。
> **硬门禁条款（不可协商，Owner 原话）**：
>
> 「DOM E2E PASS 的必要条件是"产品代码实际符合 Product Surface Contract"；不得通过缩小查询范围、改变断言目标、弱化黑名单或跳过实际可见节点获得 PASS。」
>
> **阶段定位声明**：不是"项目快做完才发现一堆没做"，而是 **Runtime 地基已打完，进入产品化收口阶段**。之前反复的根因是把 Runtime、数据 Qualification、Fixture、DOM Contract、Browser E2E、Visual Acceptance 混成了一个"完成度"；拆开后剩余工作完全明确。

---

## 0. 阶段重定位（Owner 2026-08-24 · 已逐条对照仓库事实核实）

### 0.1 层级现状判定表

| 层 | 状态 | 判断 |
|---|---|---|
| Runtime 核心链路（ADR-0039~0043 + Gate F/G/H/I） | ✅ | 基本闭环 |
| Audio → RiskSignal → Decision（audio-native） | ✅ | 架构方向正确 |
| Temporal Alignment（Gate G LinkedSignalPair） | ✅ | 真实链路可成对 |
| Anti-Hallucination（F6 + G3/G4 负例） | ✅ | 成立 |
| Tier0 Audio 边界（narrowband 不可独立判电话） | ✅ | 边界已证 |
| Tier1 Semantic（Layer2 24 条，RUN1 A/C/D PASS；B hard 待听检） | 🟢 | 接近完成 |
| Hard Negative（alarm/music/appliance/ambient/normal speech） | ✅ | 正式纳入 |
| 参数校准（Gate H/I） | 🟡 | 完成，暂不续调（依赖最终语义层） |
| Browser Infrastructure E2E（A–E 17 项） | ✅ | 通过 |
| Browser Behavioral E2E（P1–P8 36 项） | ✅ | 通过 |
| DOM Product Surface（约 12 个产品表面缺陷） | 🔴 | **当前主战场** |
| Visual Acceptance（27/36/17 全绿 ≠ 视觉 PASS） | 🔴 | 需按 D2 重做 |
| telephone_risk 真实场景素材 | 🟡 | 已确认音画同步，未做最终验收 |
| Product Story Fixture 成对性 | 🟡 | 有缺口（§2.2） |
| Memory UI | ⏸ | 后置（非本收口阻塞点） |

### 0.2 核对中发现的三处偏差（v3 修正项）

1. **Benign fixture 音频 = Risk fixture 音频（同一文件）**
   `product_story_benign.yaml:23` 与 `product_story_risk.yaml:23` 指向同一个 `product_story/telephone_risk/audio/mix.wav`。「电话存在≠诈骗存在」目前仅靠 synthetic 空 actor 实现；**「正常双向通话」的 benign 音频素材不存在**（Phase 2 缺口 F-1，§2.2）。
2. **Risk/Benign 连源类型都不同构**：benign = `synthetic`（call_connected_normal_001，loop=false），risk = `video_file`（CCTV，loop=true）。成对 fixture 需要统一源类型语义（F-2/F-3）。
3. **Runtime 两个正式挂账项不得省略**：① MONITOR ceiling 解除仍卡 B hard 听检 + Owner 拍板（TIER1-RUN1 §4）；② Tier1 两级管道实施设计 ADR 未做（TIER1-RUN1 §5.2）。二者不改变"不扩 Runtime 测试"，但必须显式挂账（§7）。

### 0.3 核心矛盾重述

```text
数据已经足够 → 但不同数据承担不同职责
    → Fixture 没把这些职责对齐
    → Browser E2E 没有严格锚定展示状态
    → DOM Product Surface 暴露工程字段
    → Vision Acceptance 才发现页面不像产品
```

结论：**不再继续找数据、不再扩 Runtime；主战场 = Fixture 对齐 + D0 产品表面修复 + D2 视觉验收。**

---

## 1. Phase 1 · 数据角色冻结（✅ 已完成：Owner 2026-08-24 拍板 F-1 方案 + demo.mp4 定位）

### 1.1 数据资产职责表（v3.1 终版，冻结后不再挪用）

| 数据资产 | 内容 | 唯一职责 | 明确不负责 |
|---|---|---|---|
| `telephone_risk_demo.mp4` | h264 1080p 31.0s + AAC 31.0s 音画同步；白发老人客厅打电话，**无门前人物** | **真实电话场景 Reality Check / 最终真实数据验证**（§4） | 不作为 Risk Fixture 主媒体源；不直接替换 media_path |
| Layer2 真实语义集 | `qualification/tier2_real/` 24 条冻结（PD×12+CC0×2+CC BY×10，NC=0），含真实 signaling 7 条 | **Tier1 Semantic Qualification** + Hard Negative 回归集 | 不做 demo 展示音源 |
| LBJ / McCormack 1963 | `tier2_real/_raw/`，111MB+182MB 真实窄带电话录音 | **真实电话信道人声 / B-hard 听检对象**；benign 通话音轨的声学基底候选 | 不等于 signaling；不做诈骗剧情配音 |
| `case_b_mix.wav`（15s golden） | 合成混音，manifest 带时序标记 | **Browser Infrastructure / Runtime E2E**（A–E/P1–P8）专用音轨 | 不做产品故事叙事基准 |
| synthetic `mix.wav`（30s canonical） | 合成混音 | **确定性 Product Story Fixture** 音轨（当前 risk/benign 错误共用，待 Phase 2 拆分） | — |
| `normal_speech` B 系列 | tier1_synthetic 内 B1_male_conv/B2_female_conv/B3_elder_long | **Benign 正常通话构造的素材基底之一** | ⚠️ 仅正常人声素材 ≠ 完整正常通话（见 F-1 构造要求） |
| alarm/music/appliance/ambient 集 | tier1_synthetic NEG-A\* 系列 + tier2_real 对应条目 | **Hard Negative 回归集** | — |

**demo.mp4 的历史澄清（防再混淆）**：早期曾抽出其 AAC 音轨跑 AudioPipeline 得 19 个事件、全部 `audio_distress_cry`——那是 YAMNet class_map 缺失 + Energy backend 类别塌缩时代的产物（RUNTIME-RISK-ROOT-CAUSE-AUDIT-2026-08-22 记录，已被 CORRECTION 报告修正），**不能据此判定其音频"不是电话"或否定素材价值**。如今 class_map 已修复并有回归锁，Reality Check（§4）正是在修复后的链路上重验这份真实素材。三个问题各自有主：
「真实电话场景是什么？」→ demo.mp4；「模型能不能识别电话语义？」→ Layer2 qualification；「浏览器产品故事能否确定性复现？」→ synthetic Product Story Fixture。

### 1.1.1 主线与 Runtime backlog 分离（冻结）

H-1~H-4 属于 **Runtime future governance backlog**，只影响未来 production policy / production semantic path，**不阻塞当前 D0/D1/D2 Product Surface 收口**——当前要验证的核心是「在已冻结的 Runtime contract 与验收态下，产品表面是否把事实讲对」。不得以"先完善 Runtime 再看页面"为由重回主线（这是前期反复绕圈的根源之一）。

### 1.2 语义三分（已实证）

```text
Telephone signaling（信令：拨号音/忙音/振铃）
    ≠ Telephone conversation（电话信道中的真实人声）
    ≠ Telephone persistent interaction（持续通话行为锚点）
```

实证：LBJ 双条 top speech .95+ 且无 TEL 标签（预期内）；signaling 7 条靠标签存在性而非 top-1 命中（TIER1-RUN1 §3.1/§3.2）。

### 1.3 禁止事项（Phase 1 红线）

- ❌ **不为 demo 好看制造「电话诈骗音频」**——产品命题是「电话存在 ≠ 诈骗存在」，benign case 与 risk case 同等重要；
- ❌ 不继续扩充 distress_cry 数据（已定 perception-only：不进 Policy、暂不建 positive、不废枚举；AUDIO-EVIDENCE-MATRIX §3.3）；
- ❌ 不在 Tier0 挖更多神奇特征（narrow + rate≈0 无法区分电话/警报/音乐/家电——hard negative 已证边界）;
- ❌ benign 缺口若需补音频，只允许以**正常通话**为语义目标，且必须按 §2.2.1 构造（signaling + 电话信道人声 + 持续段），不得用普通演讲/TTS 人声冒充"正常通话"（label 对 ≠ acoustic semantics 对），不得借机夹带风险语义。

---

## 2. Phase 2 · Product Story Fixture 成对设计

### 2.1 目标命题与 Fixture Contract（v3.1 收紧）

```text
Risk   = 电话信令 → 电话对话/持续交互 + 人物/异常视觉证据 + 时间重叠 → Combined Evidence → Risk → Decision → Action
Benign = 电话信令 → 正常双向通话     + 无异常视觉证据                → MONITOR → No Notification
```

两个一起才真正证明：**系统知道电话存在，但不会因为电话存在就认定诈骗。**

**Fixture Contract 六元组（每个 fixture 必须显式定义，防止"页面出现 Risk 卡即 PASS"）**：

验收逻辑链必须是：

```text
Fixture truth（六元组声明）
    ↓ Expected StoryTimeline
    ↓ Runtime actual events（实测）
    ↓ Projection / DOM
```

| # | 契约字段 | Risk fixture 预期 | Benign fixture 预期 |
|---|---|---|---|
| C1 | expected_audio_evidence | signaling + telephone_persistent（持续锚点）+ 时序标记 | signaling + 正常通话人声，**零 distress/risk 语义事件** |
| C2 | expected_vision_evidence | person 进入/出现 + 时间区间 | 无异常视觉证据（无 actor 或明确无人物） |
| C3 | expected_temporal_relationship | 音频×视觉时间重叠成立 | 不构成重叠升级条件 |
| C4 | expected_risk_transition | RAISED（含等级） | 恒 MONITOR / 不升级 |
| C5 | expected_decision | 对应 Decision（如 NOTIFY/ESCALATE 档） | No Notification |
| C6 | expected_action | 行动闭环任务生成 | 无行动任务 |

Browser Acceptance 的 PASS 判据因此从"页面里出现了 Risk 卡"升级为"**这个 Risk 卡确实由 Fixture 规定的证据链产生**"——六元组逐项与 Runtime 实测事件、DOM 投影三方对照。

### 2.2 当前缺口盘点（改造 backlog）

| # | 缺口 | 现状证据 | 改造方向 |
|---|---|---|---|
| F-1 | benign 音频复用 risk mix.wav | `product_story_benign.yaml:23` ≡ `product_story_risk.yaml:23` | **构造 `normal_call_fixture.wav`**（见 §2.2.1），不是简单换一段 normal_speech |
| F-2 | 视觉源不同构 | benign=synthetic / risk=CCTV video_file | 成对 fixture 统一源类型策略（同构或显式声明差异理由） |
| F-3 | loop 语义不一致 | benign loop=false / risk loop=true | 显式声明并纳入 D1 行为预期 |
| F-4 | 时长错配无契约 | 视频 60.5s vs 音频 15s/30s；`live_adapter.py:1529` TimeMapping 硬编码 60.0s | fixture 契约化：媒体时长声明 + AU-07（音频播完后 DOM 有界）|
| F-5 | C1 叙事错配（risk 用 CCTV） | vision 评审已指出 | 按 SPEC v1（待批）决策；真实视频先走 §4 Reality Check |

#### 2.2.1 F-1 构造方案（✅ Owner 已拍板 2026-08-24 · 附语义收严约束）

**教训锚定**：B 系列只是「正常人声素材」，不自动等价于「正常双向通话」；同理 **LBJ/McCormack 是真实电话信道人声，也不自动等于「双向通话」**——若录音只有一方讲话或另一方不可闻，直接命名为 bidirectional 就是再次犯「label 对了，acoustic semantics 没完全对」的错误。

**验收语义（Owner 收严版）**：

```text
telephone signaling
        +
telephone-channel speech        （电话信道质感，内容语义无关）
        +
normal conversational continuity（正常通话连续段，无 distress/stress 层）
        ↓
normal_call_fixture.wav（时长与视频对齐）
```

**StoryTimeline 命名规则（防 Fixture 自证未证明的事实）**：

| 条件 | StoryTimeline 标签 |
|---|---|
| 素材确实存在两方轮流说话（可证明） | `bidirectional_speech_start` |
| 仅单方讲话 / 另一方不可闻 | `telephone_conversation_start` |

**构造后强制校验**：跑 AudioPipeline + YAMNet 实测产出事件序列，逐项对照 §2.1 六元组 C1（预期 telephone_persistent/speech 类、**零 distress/risk 语义事件**）——校验不过不得进入 fixture。risk fixture 的 mix.wav 同样按六元组补做实测对照（历史上 case 系列混音曾有 distress_cry 塌缩污染记录，见 AUDIO-DATASET-AUDIT-REPORT-2026-08-23）。

### 2.3 Fixture 改造边界

- 配置层改动（config/demo/scenarios/**），Owner 授权后独立 PR 实施；
- F-1/F-2/F-3 可先行（不动业务代码）；F-4 涉及 TimeMapping 契约，随 D0 Step ④ 一并评估；
- F-5 依赖 §4 结论 + SPEC v1 审批，不阻塞 D0/D1/D2 主线。

---

## 3. Phase 3 · 三层 Gate 落地（v2.1 冻结内容全量继承）

### 3.1 架构（冻结）

```
Gate D0 · DOM Product Contract —— 产品表面是否符合契约
        ↓
Gate D1 · Browser Behavioral E2E —— 行为链路是否正确运转（17+36 项零改动）
        ↓
Gate D2 · Visual Product Acceptance —— 用户看到的是否像产品
```

逻辑铁律：D0 ∧ D1 全绿 ⇏ D2 PASS（2026-08-24 实证：80 项全绿 ∧ vision 判定"开发者调试工具页面"）；D0 FAIL 禁止截图；D2 FAIL 只能回产品修复。

### 3.2 D0 · Product Surface Contract（步骤 D 冻结对象）

#### 3.2.1 V 断言（视觉面，对应 A1–A6）

| ID | 断言 | 合格判据 |
|---|---|---|
| V-01 | Demo 状态面板双模隔离 | 元素带 `data-debug-only`；product mode 下经 §3.2.5 维度②判定不可见 |
| V-02 | overlay chips 双模隔离 | `ov-frame-*` / `ov-time-*` 同 V-01 |
| V-03 | 媒体源绑定行 | product mode 下 `case-video-binding` 不含内部组件名；人话形态如「视频已连接」；或整行隔离 |
| V-04 | 内部概念术语清零 | 并入 §3.2.3 黑名单统一扫描 |
| V-05 | perception 条目人话化 | 不含 `conf <数值>` / `bbox [<数值>` 工程字段；人话描述替代 |
| V-06 | Session 计时器隔离 | `ds-session-*` 同 V-01 |

#### 3.2.2 AU 断言（音频面，对应 B1–B5/B7）

| ID | 断言 | 合格判据 |
|---|---|---|
| AU-01 | audio-table 行人话化 | 不含 `score=`/`conf=`；保留五类 AudioKind→中文映射回归保护（`live_stream.js:51-55`） |
| AU-02 | 感知流条目 | 不含裸 score 数值 |
| AU-03 | 显示上限契约（三条不变量） | ① `visible_rows ≤ configured_display_limit`（N 是产品配置事实，默认 10/20 由实现自定，**不写入契约**）；② 折叠账目守恒 `collapsed_count = total_count − visible_count`；③ loop 下 DOM 节点数有界。UI 调整 N 时测试语义零改动 |
| AU-04 | Audio Health 三态状态机 | RECENT_EVENT(绿)/NO_RECENT_EVENT(橙,5s 无事件)/UNAVAILABLE(灰)；SPEC §2.4 契约补齐（真测试缺口） |
| AU-05 | 人话标签回归保护 | distress/telephone 中文标签仍正确渲染（防修复误伤） |
| AU-06 | RMS Canvas 存在性（DOM 侧拆层） | canvas 存在 + width/height>0 + 样本缓冲>0；波形合理性归 D2 |
| AU-07 | **音频播完后 DOM 有界（v3 新增）** | 音轨播放结束后感知流/audio-table 不再新增条目（防时长错配下静音期事件漂移污染） |

代码锚点：A1=`render.py:594-600`+`js:489/1184`；A2=`render.py:574`+`js:483/487/1169`；A3=`render.py:657`；A4=`render.py:678`；A5=`js:786-794`；A6=`js:1200-1208`；B1=`js:553`；B2=`js:588`；B3+B7=`js:938-944`；B4=`render.py:344-358`+`js:185-220`；B5=`js:57-96`。

#### 3.2.3 工程语义模式黑名单（冻结；只增不减，放宽须 Owner 书面批准）

```text
# ① 工程字段模式（正则）
frame@\d+  /  bbox\s*\[  /  score\s*=\s*\d  /  conf\s*=\s*\d  /  ·\s*conf\s+\d

# ② 内部组件名（字面量，大小写敏感）
LiveFrameStream / ArtifactVideoSource / Media Source Adapter / ProjectionAccumulator

# ③ 内部概念术语（字面量）
Evidence Timeline / Media Timeline / View Model / evidence_delta / perception_delta
```

禁止自然语言词进黑名单（中文页禁"帧/源/Session"等极脆弱）。

#### 3.2.4 双模规则（Product Mode / Debug Mode）

- 调试元素统一加 `data-debug-only="true"`；
- **product mode（默认）**：不可见——要么不渲染，要么满足维度②任一不可见判据；不参与 D2 截图；
- **debug mode**（显式开关如 `?debug=1`）：可见仅供排障，截图不进 D2；
- 双模是隔离手段不是豁免手段。

#### 3.2.5 D0 查询范围（两维度分离，防假绿核心）

**维度① 用户可见文本断言（主断言）**

- 扫描 `document.body.innerText` 对 §3.2.3 全部模式匹配，任一命中即 FAIL；
- 禁止只扫单元素（v1 D3 错位根源：只查 behavior-timeline 单元素，泄漏在别的元素里）；
- 已知盲区由维度②补齐：`visibility:hidden` / `opacity:0` / `aria-hidden` / off-screen / canvas 内绘制内容。

**维度② Debug 元素隔离断言（独立断言）**

- 查询 `querySelectorAll("[data-debug-only]")`，逐节点验证；满足**任一**判据即合规，全部不满足则 FAIL：

| 判据（OR） | 说明 |
|---|---|
| 不存在于 DOM | 最彻底 |
| `display === 'none'` | 最常用 |
| `visibility === 'hidden'` | 占位但不可见 |
| `opacity < 0.01` | 建议配 pointer-events:none |
| boundingClientRect 完全视口外 或 零尺寸 | off-screen |

- ⚠️ `aria-hidden` 仅影响辅助技术，**不构成视觉不可见**；
- 集合为空直接 PASS；该断言防"忘了 hidden / 藏一半"。

**canvas 特别说明**：canvas 像素不属于任何文本查询范围；存在性归 AU-06，合理性归 D2。

**实现约束（步骤 D）**：断言前必须**轮询等待目标 DOM 条件出现（带超时）**，禁止裸 sleep 作为唯一同步手段——这是"盲等假绿"教训的直接对策。

### 3.3 D1 · Browser Behavioral E2E（零改动）

17（A–E）+ 36（P1–P8）原样保留、不新增不改判定标准；唯一联动点 = 步骤 E/F 修产品后回归确认无回归。

### 3.4 D2 · Visual Product Acceptance（验收流水线，非确定性测试）

**明确禁止**：`pytest → 调模型 → assert "看起来不错"` 式伪自动化。pytest 仅限两端：①驱动 Playwright 生产六张截图；②校验报告存在性与 schema 结构（字段齐全/枚举合法/6×5=30 项齐全）。**禁止对 judge 评分结论做任何 assert**。

```text
Playwright 截图（固定六张）→ 五维 Visual Rubric → Vision Judge → 结构化报告（JSON+MD 入库 vision-eval-*）
```

五维 Rubric（冻结；只增不减）：信息层级 / 叙事完整性 / 调试元素残留 / 视觉压迫感 / 产品感。
FAIL 唯一路径：回产品修复后重走完整流水线；禁止重跑刷分 / 改 rubric / 收窄 prompt。
检查范围：六区域（视频/时间线/风险解释/风险信号/行动闭环/Memory 如场景需要）+ B6 sensor-card 样式 + AU-06-V 波形 + C1 如实记录（fixture 未切换前不一票否决）。

---

## 4. Reality Check · telephone_risk_demo.mp4（✅ Owner 已拍板：仅作 Reality Check，独立验证，不阻塞 D0）

**定位（Owner 原话锚定）**：它回答的是「我们的真实世界电话视频，在已经修复 class_map 的当前 Runtime 上，到底能产生什么」，而不是「它能不能撑起确定性 Risk Story」。已确认无门前人物 → 不能天然完成 `telephone → PERSON_ENTERED → temporal overlap → combined risk`；强行塞进 Risk Fixture 会再次把「真实素材」与「产品故事 Fixture」混成一个问题。

**独立性（v3.2 冻结）**：Reality Check 是独立验证，**不作为 Product Story Fixture 的前置依赖，不阻塞 D0**——D0 是产品表面问题，demo.mp4 是真实数据验证问题，两者并行。

正确用法：

```text
telephone_risk_demo.mp4
    ↓ AudioPipeline（AAC 31s 自带音轨）
    ↓ YAMNet / Tier1（class_map 修复版 + 回归锁）
    ↓ 真实音频事件时间线（检出什么 kind？时序？Evidence Strength 分档？）
    ↓ 视觉事件时间线（person / cell phone 检出情况）
    ↓ 对照：能否形成 Product Story？
    ↓ 产出 REALITY-CHECK 报告（docs/reports/）
```

产出三选一（供后续 fixture 决策）：
1. 音视频事件足以支撑「老人在家接电话」单模态叙事 → 可作新 fixture 基底（配合 SPEC v1 修订）；
2. 只支撑部分链路 → 作为 D2 第二场景样本 / 演示辅助素材；
3. 暴露 Runtime × 真实数据缺口 → 形成明确 gap 清单回填 roadmap（有价值结果，非失败）。

**演示入口（v3.9 · Owner 第三轮裁决：Reality Check 要成为可看的前端页面）**：

- 场景 `config/demo/scenarios/telephone_risk_reality_check.yaml` —— **REAL pipeline 全真态**：`media_path=demo.mp4`、`audio_path=dataset/telephone_risk/audio/telephone_risk_demo_16k_mono.wav`（FileAudioSource 仅支持 WAV，音轨预提取资产 16k PCM16 mono）、无 `audio_replay_path`、loop=true；
- 页面呈现 = 三段式如实声明：`视觉源: 实时推理 (REAL_RUNTIME_VIDEO)` / `音频语义源: 实时推理 (REAL_AUDIO_PIPELINE)` / `风险判定: runtime-computed`（AU-08 provenance 派生在真实态自动成立）；distress_cry 双路径降级标注（sensor 卡 + 「系统听到了什么」摘要常显「疑似哭诉求助声(当前算法判定)」，详细证据 audio-table 附 H-5 已知误识别脚注，展开 details 可见）；
- 实测复现与 G 步骤一致（audio.pipeline.done events=19 backend=energy = distress_cry×18 + speech_rapid×1；person 在场 ~24s），截图存档 `docs/reports/assets/vision-eval/reality_check_live.png`。

---

## 5. 收尾路径（v3.2 压缩版 A~I · Owner 2026-08-24 拍板）

| 步 | 动作 | 退出条件 |
|---|---|---|
| **A** | **立即完成 F-1**：构造 normal_call_fixture.wav（signaling + telephone-channel speech + normal continuity）→ AudioPipeline/YAMNet 实测 | 零 distress/risk 语义事件；C1 PASS；StoryTimeline 标签符合 §2.2.1 命名规则 |
| **B** | **risk mix.wav 六元组实测**：Fixture truth ↔ Runtime actual events ↔ Expected StoryTimeline 三方对照 | C1~C6 逐项有实测证据；塌缩污染（若有）登记并处置 |
| **C** | **Risk + Benign fixture 成对冻结**（F-1/F-2/F-3 落地） | 双向跑通 risk→RAISED / benign→MONITOR；六元组三方对照全过；同构成对 |
| **D** | **D0 Product Surface Contract 故意打红** | Owner 冻结 §3.2；新断言在当前代码上全 FAIL 且 FAIL↔缺陷 ID 一一对应；断言文件入库 |
| **E** | **修 render.py / live_stream.js 产品表面** | 测试零改动；git diff 仅 visualizer/**；每 FAIL 有对应修复 |
| **F** | **D0 PASS + D1 回归** | 维度①∧②双绿 + AU-01~AU-07 全过；17+36 无回归；ruff/pytest 全绿 |
| **G** | **Reality Check**（§4，独立验证，**与 D/E/F 并行，不阻塞 D0**） | 报告入库且三选一结论明确 |
| **H** | **Playwright 六张产品截图** | 截图重生成、md5 入库、console/page error=0（含已知豁免）；依赖 C |
| **I** | **Vision Acceptance** | 30 项无未解释 FAIL、报告入库；此后宣告 telephone_risk 产品场景收尾 |

**依赖关系（v3.2 更新）**：A→B→C 为数据线；D→E→F 为产品表面线（**不被 A/B/C 阻塞，可立即启动**）；G 独立并行；H/I 依赖 C+F。

### 5.1 执行状态（v3.7 · 2026-08-24 · A~I 全部完成 · 收口达成）

| 步 | 状态 | 关键产出 / 退出证据 |
|---|---|---|
| A | ✅ | `normal_call_fixture.wav`（20.3s @16k/mono/PCM16）C1 三层 PASS；标签 `telephone_conversation_start`@2.3s；报告 `BENIGN-CALL-FIXTURE-CONSTRUCTION-2026-08-24.md` |
| B | ✅ | mix.wav 六元组实测三方对照；**Owner 裁决：Product Story 音频事实源 = synthetic_replay**（验收链五段化，Runtime Input 两态）；P2 定性 `Tier0 semantic collapse` 入 backlog；报告 `RISK-MIX-SIXTUPLE-VERIFICATION-2026-08-24.md` |
| C | ✅ | `ScenarioConfig.audio_replay_path` + `_build_live_audio_events` replay 分支（网关/前端零改动）；risk/benign 双场景接线（audio_path=播放介质 ∥ audio_replay_path=语义事实源）；两 fixture 标签修订为 `telephone_conversation_start`；合约测试 `tests/demo/test_scenario_audio_replay_path.py` 9/9 + tests/demo 回归全绿 |
| D | ✅ | `tests/visualizer/test_dom_product_contract.py`（31 断言）打红：17 FAIL ↔ 缺陷锚点一一对应（含双源/新缺陷发现：renderer.py:788、AU-05b、AU-06 Surface 门控根因） |
| E | ✅ | 产品表面修复（render.py/renderer.py/live_stream.js/scenario_config.py 四文件）：工程内容 data-debug-only 隔离、内部术语人话化、score/conf 定性化降级 data-*、AU-06 Surface 注册、_pendingAudioRows 上限 200。**测试零改动**；30/31 |
| F | ✅ | **AU-07 Owner 裁决落地**：拆分 AU-07a（audio boundedness：replay 声明时间线结束后 audio-derived DOM evidence 不增长）/ AU-07b（visual boundedness：视觉 frame/event DOM 自身有界，live://frame/N 合法推进不计入 AU-07a），保留不豁免；F-4 选 a = fixture 时间契约对齐（新增 fixture invariant：media duration 与 observation window 一致或显式结束边界）。D0 31→**32/32 全绿**。D1 BA 单跑全绿（P6d FAIL 经甄别为跨阶段 DOM 残留 + 阶段干净日志窗口径错位——文案源头 decision_policy.py:292 为 ADR-0040 合法投影非前端编造，测试侧 carry-over 口径修正，allowlist 零改动）；BA+D0 合跑 ×2 稳定全绿（69 passed + 1 既有 skip）；ruff 干净 |
| G | ✅ | Reality Check 完成（报告 `REALITY-CHECK-TELEPHONE-RISK-DEMO-2026-08-24.md`）：demo.mp4（76.7MB/31s，室内客厅老人持手机通话叙事）三层实测——音频 ffmpeg 提轨 16k PCM16 mono → YAMNet 全段唯一标签 speech=0.781、宽阈值黑名单零出现；Pipeline distress_cry×18（t=1.28~28.56s 连续）+ speech_rapid×1（conf=0.947），每条 distress_cry Tier1 scored_labels 均 speech 0.866~0.997 → **纯真实世界素材首次完整复现 H-5 semantic collapse**（此前仅合成组）；视觉 fps=0.5 抽 16 帧 → yolo11n person 16/16（0.859~0.911 持续在场）/ cell phone 0/16，三帧目视全程无访客进入跃迁；三选一结论 = **选项 3（gap 清单回填 roadmap）**：Gap-1 = H-5 第四组独立素材（回归池扩五组）/ Gap-2 = 室内在场类视觉事件契约缺失（v2 须 Owner 立项）/ Gap-3 = yolo11n 手持手机 0 检出（反证 ADR-0038 optional_supporting）/ Gap-4 = 部署边界（室内超门前模块语义）；§4 口径精确化「无门前人物」→「无门前到访者」（持续在场 ≠ PERSON_ENTERED） |
| H | ✅ | `tests/visualizer/test_product_screenshots.py` 3 断言 ×2 稳定全绿；固定六张落盘 `docs/reports/assets/vision-eval/product_story_risk/`（冻结序 manifest + md5 + console/pageerror=0）。**新缺陷发现并修复**：Live 页 Evidence Graph `<script>` 中段裸内联在解析期先于页尾 echarts 执行 → `ReferenceError: echarts is not defined`，图从未渲染成功（render.py 原注释「details 展开时自然执行」假设不成立）；修复 = toggle-gated 内联（details 首次展开才 init，兼规避隐藏容器 0-size），D0+BA 回归零失败 |
| I | ✅ | Vision 流水线两轮完整走通：R1 发现 FAIL×5 维（图01/04）→ 三缺陷 D-I1（runtime 标识 UUID/枚举裸显）/ D-I2（behavioral(vision) 裸枚举）/ G-H1 回产品修复 → R2 重截重评 **PASS 26 / WARN 4 / FAIL 0**，30 项无未解释 FAIL 达成。报告入库 `vision-eval-product-story-risk-2026-08-24.{md,json}` + schema 校验测试 `test_vision_eval_report_schema.py` 5/5（pytest 仅校验存在性/schema，不对 judge 结论 assert）；WARN×4 登记打磨清单（W-1~W-4）不阻塞收口 |

> **收口结论重定性（v3.8 · Owner 裁决）**：本路线交付并验证的是 **Telephone Risk Product Story Simulation**（CCTV 视觉 runtime 推理 + synthetic_replay 音频语义注入 + 实时融合 + 真实浏览器 UI 的产品闭环呈现能力）→ **PASS**；**Telephone Risk Real-World Validation**（真实电话视频端到端自主感知 → 风险判断）当前 **NOT READY，不宣称 PASS**（H-5 semantic collapse + Gap-2/Gap-3 感知缺口实证）。Reality Check 定位为诚实性证据：证明系统没有掩盖真实世界缺陷，而非未完成的替代品。配套落地：Provenance 显性化（AU-08 模态级三行声明常显）+ distress_cry 产品语义降级（AU-09，Perception ≠ Truth ≠ Risk Decision）。剩余移交项：打磨清单 W-1~W-4、Runtime backlog H-1~H-5、Reality Check gap 清单。

> 数据探索收敛声明（冻结）：不再搜任何新数据。剩余唯一数据工作 = F-1 的 fixture composition（用已有资产重新编排），不是找数据。

## 6. 克制清单（暂时不做）

- ❌ 不扩充 distress_cry 数据（perception-only 定稿）；
- ❌ 不在 Tier0 挖新特征（边界已证）；
- ❌ 不扩大 Runtime E2E（最大风险已转为「链路通了但产品表面错误展示内部事实」）；
- ❌ 不动 Memory UI（⏸ 后置）；
- ❌ 不修改 ADR / 架构决策文件（Owner 专属）；fixture 业务代码改动须授权走 PR。

## 7. Runtime 挂账管理（future governance backlog · 不阻塞 Product Surface 收口）

> 依 §1.1.1：H-1~H-4 影响未来 production policy / semantic path，**不是 D0/D1/D2 的前置**。当前收口验证的是「已冻结 Runtime contract 与验收态下，产品表面是否讲对事实」。

| # | 挂账项 | 责任方 | 来源 | 时机 |
|---|---|---|---|---|
| H-1 | B 口径 hard 听检（LBJ×2 信道特征）→ B 闭合 + **MONITOR ceiling 解除拍板** | Owner | TIER1-RUN1 §4/§5.1 | 建议随 ① 一并处理 |
| H-2 | Tier1 两级管道实施设计 ADR（Tier0 persistent_narrowband_candidate → Tier1 confirm 事件流转契约） | Agent | TIER1-RUN1 §5.2 | ceiling 解除后启动 |
| H-3 | Q2/Q3/Q4 挂起语料裁决 | Owner | TIER1-RUN1 §2 | 随 H-1 |
| H-4 | confidence 双源异质契约修订（rule conf ≠ model conf） | 待 Owner 拍板 | GATE-I 报告 | Policy 升级窗口 |
| H-5 | **Tier0 semantic collapse: telephone-channel speech → distress_cry**——四组独立素材量化证据（benign fixture ×7 段、mix.wav ×11 条、case_a/b_mix ×9×9、demo.mp4 ×18 条——2026-08-24 Reality Check 纯真实素材复现），YAMNet 层全程纯净；回归判据 = 五组素材全链路零 distress_cry 误报；**修复路径（v3.8 细化）= 重定义 crying 特征（现 tremor 为整段包络峰谷粗指标，自然语音停顿/音节起伏即命中，调阈值治标不治本）+ 五类语料（normal speech / stressed speech / real distress / crying micro-event / telephone speech）真正的二分类验证**；处置矩阵（Owner 拍板）：枚举/感知输出保留 ✅、展示标注「当前算法判定·疑似」✅、参与 telephone_risk 升级 ❌（audio→risk 链未接通）、当真实哭诉事实 ❌ | Agent→Owner | BENIGN 构造报告 §4 + RISK-MIX 报告 §4/§6 + REALITY-CHECK 报告（选 C 配套） | Policy 升级窗口 |

## 8. 总体验收通过标准

- [x] §1 数据角色冻结记录在案（✅ 2026-08-24 Owner 拍板：F-1 方案 + demo.mp4 仅作 Reality Check）
- [x] 步骤 A normal_call_fixture.wav 构造 + 实测校验 C1 PASS
- [x] 步骤 B risk mix.wav 六元组三方对照通过
- [x] §2 成对 fixture 双向跑通（risk→RAISED / benign→MONITOR）+ 六元组全过
- [x] 步骤 D 打红证据在案（FAIL ↔ 缺陷 ID 对照表）
- [x] 步骤 E 产品 diff 仅含 visualizer 目录，测试零改动
- [x] 步骤 F D0 双维度全绿（可见文本 ∧ debug 隔离）+ D1 无回归 + ruff/pytest 全绿
- [x] 步骤 G Reality Check 报告入库且有明确结论（✅ 2026-08-24 三选一 = 选项 3，gap 清单回填 roadmap）
- [x] 步骤 H 六图 + md5 + console/page error=0
- [x] 步骤 I 结构化 vision 报告入库（30 项无未解释 FAIL）；pytest 未 assert judge 结论
- [x] 全程满足硬门禁条款（无缩小查询 / 无改断言目标 / 无弱化黑名单 / 无跳过可见节点）

## 9. 明确边界

- 本文档是收口路线 SSOT；ADR 决策仍归 docs/ADR（Owner 专属）；fixture 改造走独立 PR；
- 不扩张 Runtime 测试；不改既有 80 项 E2E 断言语义；
- prototypes/、凭证、模型权重等 gitignore 纪律不变。

## 10. 自检对照（AGENTS.md §9.4）

- [x] 已读 AGENTS.md 相关章节；未违反 §6 Hard Rules
- [x] 所有事实性声明均经仓库核实（TIER1-RUN1/GATE-H/GATE-I/AUDIO-EVIDENCE-MATRIX 报告 + ffprobe/wave 实测 + yaml/py/js 源码行号）
- [x] 不夹带：本文档仅路线与验收定义，不含代码改动

## 11. 变更记录

| 时间 | 事项 |
|---|---|
| 2026-08-24 | v1 初稿（单一 backlog 结构） |
| 2026-08-24 | v2 重构：三层 Gate 分离 / 工程语义模式黑名单 / 缺陷重定性（12 产品缺陷+1 真测试缺口+3 验收机制升级）/ 硬门禁条款 / Step 0~5 |
| 2026-08-24 | v2.1：查询范围两维度分离（innerText 主断言 + data-debug-only 逐节点隔离）/ AU-03 去参数化三条不变量 / D2 重定位为验收流水线（禁 assert judge 结论）/ aria-hidden 与 canvas 盲区条款 |
| 2026-08-24 | **v3 阶段重定位**：采纳 Owner「产品化收口」框架并逐条核实——①Phase 1 数据角色冻结表（Layer2 24 条/LBJ 对话素材/demo.mp4/case_b_mix/mix.wav 各归其位）+ Telephone signaling≠conversation≠persistent interaction 语义三分实证；②Phase 2 成对 fixture 设计，核实并登记四缺口（F-1 benign 音频≡risk 音频 / F-2 源类型不同构 / F-3 loop 不一致 / F-4 时长错配无契约）；③新增 AU-07（音频播完后 DOM 有界）与数据锚定轮询实现约束；④新增 Reality Check 章节（demo.mp4 先验证不替换，产出三选一）；⑤执行路径改为 ①~⑩ 十步并声明依赖（③~⑥ 不被 ② 阻塞，⑧⑩ 依赖 ②）；⑥新增 Runtime 挂账管理 H-1~H-4；⑦克制清单固化 |
| 2026-08-24 | **v3.1 执行前收紧**（Owner 三点 + 数据职责终版澄清）：①§2.1 新增 **Fixture Contract 六元组 C1~C6**（expected_audio/vision/temporal/risk_transition/decision/action），验收逻辑链钉死为 Fixture truth→Expected StoryTimeline→Runtime actual events→Projection/DOM，步骤退出条件升级为六元组三方对照，杜绝"页面出现 Risk 卡即 PASS"；②F-1 升级为 §2.2.1 normal_call_fixture.wav 构造方案——教训锚定：B 系列正常人声 ≠ 正常双向通话，label 对 ≠ acoustic semantics 对；③§1.1.1 冻结主线与 backlog 分离：H-1~H-4 属 Runtime future governance，不阻塞 D0/D1/D2；④§1.1 职责表补 demo.mp4 历史澄清（19 个 distress_cry 为 class_map 缺失时代塌缩产物，CORRECTION 报告在案）；⑤确认数据探索收敛：不再回头找 telephone_risk 音频 |
| 2026-08-24 | **v3.2 拍板落地与执行路径定稿**：①Owner 两项决策生效——F-1 构造路线批准（附语义收严：LBJ/McCormack ≠ 自动双向通话；StoryTimeline 命名规则 `bidirectional_speech_start` 仅当两方轮流说话可证明，否则 `telephone_conversation_start`）；demo.mp4 确认仅作 Reality Check、独立验证、不作为 Product Story Fixture 前置且不阻塞 D0；②§5 执行路径压缩为 A~I：数据线 A→B→C 与产品表面线 D→E→F 并行，G（Reality Check）独立并行，H/I 依赖 C+F；③§1 数据角色冻结完成标记（Phase 1 ✅）；④§8 验收标准同步 A~I 编号并勾选已完成的 §1 冻结项；⑤确认数据探索收敛终态：剩余唯一数据工作 = F-1 fixture composition，非找数据 |
| 2026-08-24 | **v3.3 双线并行执行收口（AI 按 Owner 裁决落笔）**：①步骤 B 实测触发架构裁决——Product Story 音频事实源 = **synthetic_replay**（验收链五段化：Runtime Input ∈ {REAL_AUDIO_PIPELINE, SYNTHETIC_EVENT_REPLAY}；mix.wav 降级播放介质）；P2 正式命名 Tier0 semantic collapse 入 backlog（H-5），排除「弱化断言」选项 b；②步骤 A/B/C/D/E/F 全部执行完毕（§5.1 状态表）：replay 注入通道落地（audio_replay_path）、D0 契约测试 31 断言入库打红→修复、30/31 + D1 零回归；③唯一遗留 AU-07 = F-4 时长错配契约层后果，Owner 二选一裁决后即可开工 H/I；④数据角色分层表以 RISK-MIX 报告 §6.1 为准 |
| 2026-08-24 | **v3.4 AU-07 裁决落地 + F 线收口（AI 按 Owner 裁决执行）**：①**AU-07 拆分裁决执行**——AU-07a（Audio boundedness）/ AU-07b（Runtime visual boundedness，新增 CASE_TIME_MARK_LIMIT=120 常量），保留不豁免、不 skip；F-4 选 a = fixture 时间契约对齐，新增 fixture invariant「media duration 与 observation window 一致或显式结束边界」；②D0 31→32 断言，拆分后 **32/32 全绿**；③**F 线 D1 收口**——BA 单跑 P6d FAIL 甄别闭环：根因 = 场景热切换共用页面实例下 risk 阶段 lrk-reasons 残留（benign 仅 display:none 不清空，PR-B P1-1 特性）+ 阶段干净日志窗（source_switched 后）比对口径错位；文案源头 `decision_policy.py:292`「实时风险信号: {category}({source})」为 ADR-0040 policy 合法投影，非前端编造；修复 = P6d carry-over 口径修正（benign 允许集并入 risk 阶段已验证 DOM reasons），REASON_RUNTIME_ALLOWLIST 零改动零弱化；④复验证据：BA 单跑全绿 + D0 32/32 + 合跑 ×2 稳定全绿（69 passed + 1 既有 skip）+ ruff 干净；⑤A~F 全部 ✅，§8 对应项勾选，H/I 开工 |
| 2026-08-24 | **v3.5 步骤 H 完成 + 新缺陷 G-H1 发现修复**：①`tests/visualizer/test_product_screenshots.py` 入库（3 断言：RAISED 可见窗事件驱动连拍 / 六图+manifest / console-pageerror=0）；活页面竞态对策 = RAISED 窗口内连拍且易逝的 ③/③.5 最先，禁跨测试依赖实时状态；favicon.ico 404 登记已知豁免（Chromium 自动请求，非产品缺陷）；②固定六张落盘 `docs/reports/assets/vision-eval/product_story_risk/`（冻结序 manifest + md5 当次快照）；③**新缺陷 G-H1**：Live 页 Evidence Graph/CrossModal 图 `<script>` 中段裸内联于 HTML 解析期执行、先于页尾 echarts 定义 → `ReferenceError: echarts is not defined`，两图在 Live 页从未渲染成功（原「details 展开时自然解析执行」假设与浏览器事实不符）；修复 = toggle-gated 内联（render.py `_live_gated_inline`：details 首次展开才 init，兼规避隐藏容器 0-size init）；④复验：H 套件 ×2 全绿 + D0(32) + BA(37+1s) 回归零失败 + ruff 干净；§8 H 勾选 |
| 2026-08-24 | **v3.6 步骤 I 完成 · telephone_risk 产品场景收口达成**：①Vision 流水线两轮完整走通——R1（修复前六图）PASS 11/WARN 14/FAIL 5，图01/图04 整体 FAIL；三缺陷定位并回产品修复：D-I1 = rt-card 裸显 signal_id/subject UUID 片段与 category/severity 英文枚举 + 视频区 Visual perception/VM-10 条款号泄漏（修法：UUID/数值降级 data-* 溯源属性、_LEVEL_ZH/_SIGNAL_CATEGORY_ZH 中文映射、说明文人话化）；D-I2 = behavioral(vision) 枚举裸露（修法：_REASON_ZH 确定性译文映射「实时风险信号: 行为特征（视觉）」+ BA REASON_RUNTIME_ALLOWLIST 同步，即 P5b 既有润色白名单机制，键集冻结非编造）；②R2 重截重评 **PASS 26 / WARN 4 / FAIL 0**，§8「30 项无未解释 FAIL」达成；③报告入库 `vision-eval-product-story-risk-2026-08-24.{md,json}` + schema 校验测试 `test_vision_eval_report_schema.py` 5/5（仅校验存在性/schema/计数一致性/FAIL 闭环，**不对 judge 结论 assert**）；④WARN×4 登记打磨清单 W-1~W-4（进度条占位符措辞/时间线密度/chips 裸 score 涉 AU-02 边界/闭环底注术语），不阻塞收口；⑤复验：D0(32)+BA(37+1s)+H(3)+I-schema(5) 全绿，ruff 干净。**A~F/H/I 全部 ✅，G Reality Check 并行待办** |
| 2026-08-24 | **v3.7 步骤 G 完成 · 收尾路径 A~I 全线关闭**：①Reality Check 执行完毕（报告 `REALITY-CHECK-TELEPHONE-RISK-DEMO-2026-08-24.md` 入库）——demo.mp4（76.7MB/31s，室内客厅老人持手机通话叙事）三层实测：音频 ffmpeg 提轨 16k PCM16 mono → verify_audio_fixture 三层校验，YAMNet 全段唯一标签 speech=0.781、宽阈值黑名单零出现；Pipeline 19 事件 = distress_cry×18（t=1.28~28.56s 连续）+ speech_rapid×1（conf=0.947），每条 distress_cry Tier1 scored_labels 均 speech 0.866~0.997 → **纯真实世界素材首次完整复现 H-5 semantic collapse**（此前仅有合成 mix/case_a/b 组）；视觉 ffmpeg fps=0.5 抽 16 帧 → yolo11n conf≥0.25：person 16/16（0.859~0.911 持续在场）、cell phone 0/16；三帧目视确认全程无访客进入跃迁；②§4 口径精确化：「无门前人物」→「无门前到访者」（人在场但持续在场 ≠ PERSON_ENTERED）；③**三选一结论 = 选项 3（gap 清单回填 roadmap，有价值结果非失败）**：Gap-1 = H-5 第四组独立素材（回归素材池五组：benign×7/mix×11/case_a×9/case_b×9/demo.mp4×18）；Gap-2 = 室内在场类视觉事件契约缺失（presence/activity，v2 范围须 Owner 立项）；Gap-3 = yolo11n 手持手机 0/16 检出（反向佐证 ADR-0038 phone_interaction=optional_supporting）；Gap-4 = 部署边界（室内客厅超出 Home 门前时空异常模块语义）；④§7 H-5 证据池同步（三→四组素材，回归判据改五组零误报）；⑤临时产物清理（_rc_demo_audio_16k.wav / _rc_frames/）；⑥§8 G 项 + 硬门禁总项勾选。**A~I 全部 ✅，telephone_risk 产品化收口路线关闭** |
| 2026-08-24 | **v3.8 收口结论双层重定性 · Provenance 显性化 + distress_cry 语义降级落地（Owner 两轮裁决执行）**：①**双层命名拆分**——Telephone Risk Product Story Simulation（PASS：给定事实下 Projection→Browser→Action 产品闭环呈现能力）/ Telephone Risk Real-World Validation（NOT READY 不宣称 PASS：H-5 + Gap-2/3 实证感知缺口）；Reality Check 重定位为诚实性证据，杜绝「工程验收 PASS 被误读为真实产品能力 PASS」；②**Provenance 显性化（AU-08 新增）**——取证确认 live_adapter 音频节点硬编码 REAL_SENSOR、与真实推理不可区分的混源风险；修法 = run_demo.py replay 注入携带 `provenance="SYNTHETIC_REPLAY"` 声明 → LiveAudioFrame schema 加 NotRequired provenance 透传（audio_result_to_live_audio 原实现丢弃非 schema 字段，为根因）→ `_audio_provenance_kind()` 派生 FIXTURE → render.py `_render_modality_provenance_line` 三段常显声明（视觉源: 实时推理 REAL_RUNTIME_VIDEO / 音频语义源: 合成回放 SYNTHETIC_REPLAY / 风险判定: runtime-computed；全部从 projection provenance_kind 派生非硬编码，REAL 态自动显示对应声明）；③**distress_cry 四行处置矩阵落地**——枚举/感知输出保留 ✅、展示标注「当前算法判定·疑似」✅、参与 telephone_risk 升级 ❌（audio→risk 链本就未接通，UI 把既有边界讲出来）、当真实哭诉事实 ❌；live_stream.js `_AUDIO_KIND_CAUTION` 特判：感知流 label 降级「声学特征(当前算法判定): 疑似哭腔/求助」+ 详情已知误识别声明、日志人话化同步「声学特征(疑似)」，映射表名词零改动（AU-05 兼容）；④测试：D0 32→34——AU-08 DOM 断言（含 reload 时序对策：prov-banner 服务端一次性渲染，须等音频摄入后重取页面方可读到 FIXTURE 投影）+ AU-09 静态守护（product_story fixture 无 distress_cry 无法自然触发降级路径，防特判回归删除）；⑤复验：D0(34) + BA(37+1s) + H + I-schema 全绿 + ruff(src/tests) 干净。**收口最终口径：Product Story Simulation = PASS；Real-World Telephone Risk = NOT READY 不宣称 PASS** |
| 2026-08-24 | **v3.9 Reality Check 演示入口落地 · H-5 降级标注补全服务端静态渲染路径（Owner 第三轮裁决执行）**：①**新场景 `telephone_risk_reality_check`**（`config/demo/scenarios/telephone_risk_reality_check.yaml`）= REAL pipeline 全真态演示入口：media_path=demo.mp4、audio_path=`dataset/telephone_risk/audio/telephone_risk_demo_16k_mono.wav`（音轨预提取资产，FileAudioSource 仅支持 WAV 故须落盘）、无 audio_replay_path、loop=true、start_time=2026-08-16T19:45:00+08:00；②实测复现 G 步骤结论——audio.pipeline.done events=19 backend=energy = distress_cry×18 + speech_rapid×1（REAL AudioPipeline 对 demo.mp4 音轨再次完整复现 H-5 semantic collapse），视觉真实输出 person 持续在场 ~24s + visitor track enter/leave 正常触发；modality banner REAL 态三段正确显示「视觉源: 实时推理 (REAL_RUNTIME_VIDEO) / 音频语义源: 实时推理 (REAL_AUDIO_PIPELINE) / 风险判定: runtime-computed」（AU-08 派生逻辑零改动自动成立，验证其非 Simulation 态特例）；③**服务端静态渲染路径降级补全**——探针发现 v3.8 降级仅覆盖 WS 动态流（js 特判），sensor 卡「检测到的声学类型」/首屏「系统听到了什么」（renderer `_translate_audio_kind` 静态渲染）仍裸显旧文案；修法 = renderer.py `_AUDIO_KIND_ZH['audio_distress_cry']` 改「疑似哭诉求助声(当前算法判定)」+ `_render_audio_evidence` 条件脚注 `audio-caution-note`（存在 distress_cry kind 时渲染 H-5 已知误识别声明；脚注位于「详细证据」details 折叠面板内与 audio-table 同层，展开即见——页面信息架构预期设计）；AU-09 守护扩展至 renderer.py 两处文案；④截图存档 `docs/reports/assets/vision-eval/reality_check_live.png`；⑤回归：D0+BA+I-schema+contract 合跑 **86 tests / 0 failed / 0 errors / 1 既有 skip**（test_p4_risk_memory_region，Memory API 待接入空态）+ ruff(src/tests) 干净。**Reality Check 页面闭环达成：Simulation 态与 REAL 态双场景均可在前端如实呈现** |
| 2026-08-25 | **v4.0 T-batch 收尾 + switch_source 音频游标重置缺陷修复**：①**T2 loop 重播**（上一会话实现）——gateway `_feed_live_audio` case_time 驱动投递 + epoch 归一化 + loop 回绕重置游标/accumulator/delta 基线；live_stream.js `_resetAudioSurfacesForNewLoop` 前端判重集合重置；②**T3 波形×事件标记**（上一会话实现）——renderer `_render_audio_event_locator` 静态 SVG 定位条 + AU-10 契约（定位点数 = audio-table 行数、非语义判定红线文案、语义背书词汇黑名单）；③**T5 Debug Mode**（上一会话实现）——live_stream.js `?debug=1` 时 score/conf data-* 属性显形；④**epoch 裸显缺陷修复**——synthetic_replay fixture 携带 Unix 绝对秒（1756036800.0），audio-table 行/定位条刻度直接显示工程数值；renderer.py + live_stream.js 显示层修复（epoch→会话相对秒，data-ts 属性保留原始值供排障）；新增 3+3=6 个单测覆盖 epoch/relative/unparseable 三态；⑤**switch_source 音频游标重置缺陷修复（根因）**——`/demo/reset` 调 `switch_source` 重置 `_live_accumulator=None` 但未重置 `_audio_feed_cursor` → 新 accumulator 永远收不到音频事件（D0 AU-10 reset 后 audio-table 无行涌现）；修复 = `switch_source` 加 `self._audio_feed_cursor = 0`；新增 2 个回归测试（正向：重置后事件重新投递；反向：不重置 cursor 复现 bug）；⑥**audio_lifecycle fixture 时序修复**——音频时间线有 8s 间隔（12→20s），recent window 在事件间误判 NO_RECENT_EVENT 导致 baseline 过早采集；修复 = baseline 前等音频行稳定 12s（超过最大间隔）；⑦p02/p03 文案断言同步（Case Time 标题变更后 3 处断言漂移）；⑧复验：ruff 全绿 + 目标 94 测试全绿 + D0 35/35 全绿 |
