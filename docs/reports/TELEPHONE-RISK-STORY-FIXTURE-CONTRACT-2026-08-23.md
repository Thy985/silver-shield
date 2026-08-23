# telephone_risk Product Story Fixture Contract（v1）

> 日期：2026-08-23
> 状态：**SPEC v1 — 待 Owner 审批**（ADR-0044 配套规格；本文档定义"建什么"，实现随后）
> 决策依据：ADR-0044 D1~D6（Owner 六项决策 2026-08-23）
> 上游：`TIER1-QUALIFICATION-GATE-RUN1-2026-08-23.md`（qualification 收口）·
> ADR-0032（Scenario schema，vision timeline 的生成基础设施）· ADR-0028（CrossModalLink 时间重叠硬 gate）

---

## 0. 一句话定位

```text
Qualification Dataset  → 验证模型「认识什么声音」（已收口：RUN1 CONDITIONAL PASS + limitation registered）
Product Story Dataset  → 验证系统「如何讲一个电话风险故事」（本契约）
```

StoryTimeline 是唯一真相源；事件怎么产生（provenance / execution_path）是次级属性。
**Runtime 是 Story Contract 的实现者，不是反过来。**

---

## 1. StoryTimeline Schema

每个 fixture = 一个自包含目录，核心是 `story_timeline.yaml`：

```yaml
schema_version: "1"
fixture_id: telephone_risk_multimodal_001        # 全局唯一
category: risk | benign                          # D5：双轨强制
provenance: synthetic_replay | runtime_generated | real_sensor   # D4 三态必填
duration_s: 30                                   # 叙事总时长

timeline:                                        # 时间轴 = 唯一真相源（秒，单调递增）
  - {t: 0,  event: signaling,                    source: audio, detail: "ring/dial → 线路接通"}
  - {t: 2,  event: bidirectional_speech_start,   source: audio, detail: "双人对话开始"}
  - {t: 8,  event: telephone_persistent_evidence,source: audio, detail: "持续交互语义成立 ≠ 振铃"}
  - {t: 20, event: PERSON_ENTERED,               source: vision, detail: "门前人物出现"}
  - {t: 22, event: temporal_overlap,             source: fusion, detail: "Audio+Vision 落入时间窗(ADR-0028)"}

expected:                                        # oracle 断言层
  evidence:
    - {at: 8,  kind: AUDIO_TELEPHONE_PERSISTENT}
    - {at: 20, kind: visitor_event}              # 对齐视觉链既有事件类型
  risk_transition:      {from: none, to: raised, at: 24}
  decision:             {action_hint: notify_family_pending, at: 26}
  action:               {channel: family_app, state: delivered, at: 27}
  dom_assertions:                               # Browser Acceptance oracle
    - {region: "① 视频",       expect: "人物出现画面可见"}
    - {region: "② 时间线",     expect: "telephone_persistent 与 PERSON_ENTERED 同窗显示"}
    - {region: "③ 风险解释",   expect: "含电话交互+人员出现双证据表述，无『诈骗』判定词"}
    - {region: "③.5 风险信号", expect: "RiskSignal 升级轨迹可观测"}
    - {region: "④ 行动闭环",   expect: "家属通知状态流转可见"}
    - {region: "⑥ Memory",     expect: "本次事件证据条目落库"}

provenance_meta:
  audio_assets: [清单+license 引用]              # qualification 目录素材可复用为拼接原料
  vision_source: scenario_id (ADR-0032) | detections.json
  notes: "构造方式与复跑指令"
```

### 1.1 字段纪律

| 规则 | 内容 |
| --- | --- |
| T1 时间轴单调 | `t` 严格递增；`duration_s` ≥ 最后事件 t |
| T2 语义边界 | `expected` 中禁止出现 fraud/suspect/诈骗 判定词（AGENTS §3.1 / ADR-0001）；风险解释只描述证据组合 |
| T3 provenance 必填 | 缺省即校验失败；三态定义见 §2 |
| T4 双轨强制 | `category=risk` 与 `benign` 必须成对交付（D5） |
| T5 oracle 可机检 | `dom_assertions` 每条须能被 Browser E2E 断言（文本匹配或元素状态），不可机检的写进人工验收清单并显式标记 |

---

## 2. Provenance 三态（D4）

| 态 | 含义 | 当前可用性 |
| --- | --- | --- |
| `synthetic_replay` | 音频由 qualification 素材按时间轴拼接、视觉由 ADR-0032 scenario 通道生成，事件流预录回放进 Demo runtime | ✅ 本契约首个实现目标 |
| `runtime_generated` | 同一 StoryTimeline 输入真实运行时管道（含两级 Tier0 candidate→Tier1 confirm）实时产出事件流 | ⏳ 待两级管道实施 ADR 落地后启用；**用同一套 dom_assertions 重跑** |
| `real_sensor` | 家庭现场实录对齐时间轴 | roadmap Layer3；Final Acceptance 专属 |

升级纪律：换态不换 schema、不换 oracle——三态重跑结果可直接对比。

## 3. 首批 Fixture 定义（最小集：一 risk 一 benign）

### 3.1 Risk case — `telephone_risk_multimodal_001`

采用 §1 示例时间轴。叙事：电话持续交互期间门前出现人物，多模态证据时间重叠触发
风险升级与家属通知。对应 Owner 给定的完整事件链：
`signaling → 双向语音 → telephone_persistent → PERSON_ENTERED → overlap → Combined Evidence → Risk → Decision → Action`。

### 3.2 Benign case — `call_connected_normal_001`（与 risk 同时交付，D5）

```text
t=0    signaling（接通）
t=2    双向人声（正常交谈）
t=8    telephone_persistent evidence 成立
t=30   通话结束
—— 全程无可疑视觉证据 ——
expected: risk 保持 MONITOR；无 escalation；无家属通知；
          ③ 区域解释为「检测到电话交互，未见异常」；④ 区域无行动触发
```

它验证的是刚冻结的核心产品命题：**识别到电话 ≠ 识别到诈骗风险**
（telephone_persistent = 电话交互证据，非 scam 结论——模块边界铁律的产品面回归化）。

### 3.3 构造原则

1. **要真实语义结构，不要真实诈骗内容**：双向对话由 LBJ 类 PD 电话录音承载，
   不涉及任何敏感话术录制；
2. **音频拼接来源优先复用 qualification 目录**（license 已闭合），拼接脚本记录
   `provenance_meta.audio_assets`；
3. **视觉侧走 ADR-0032 声明式 scenario**（detections 通道即可，无需照片级帧），
   与音频共用同一条时间轴种子，保证 T1 单调与可复现；
4. **质量 > 数量**：一对高质量完整案例优先于十几个半成品。

## 4. Browser Product Acceptance RUN1（验收形态）

- 输入：`synthetic_replay` 版 `telephone_risk_multimodal_001` + `call_connected_normal_001`；
- 过程：事件流回放进 Live UI 五分钟叙事；
- Oracle：两份 fixture 的 `dom_assertions` 全绿 + 六个区域（①②③③.5④⑥）人工走查表；
- 通过判据：risk case 讲出完整故事（含 ③ 解释与 ④ 行动闭环）、benign case 全程 MONITOR 不误报；
- 产出报告登记 `execution_path=synthetic_replay`，为将来 `runtime_generated` 重跑留基线。

## 5. 数据资产目录迁移映射（本地 dataset/，gitignore 不入库）

```text
dataset/_canonical/audio_semantic/
├── qualification/                  ← 原 tier1/tier2 合并升层（模型能力域，冻结）
│   ├── tier1_synthetic/            ← 原 tier1_qualification/（36 条合成 + baseline）
│   └── tier2_real/                 ← 原 tier2_qualification/（24 条 manifest v2 + ATTRIBUTION）
└── product_story/                  ← 产品叙事域（本契约管辖）
    └── telephone_risk/
        ├── telephone_risk_multimodal_001/    （待构造）
        └── call_connected_normal_001/        （待构造）
```

历史报告路径引用对照：`tier1_qualification→qualification/tier1_synthetic`、
`tier2_qualification→qualification/tier2_real`。迁移为纯 `mv`，manifest 内相对路径字段同步修正。

---

## 6. 变更记录

| 时间 | 事项 |
| --- | --- |
| 2026-08-23 | 按 ADR-0044 D1~D6 成稿：StoryTimeline schema / provenance 三态 / benign+risk 双 fixture / Browser Acceptance 形态 / 目录迁移映射 |