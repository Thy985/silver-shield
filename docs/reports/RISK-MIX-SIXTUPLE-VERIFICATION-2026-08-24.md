# Risk mix.wav 六元组实测三方对照报告（SSOT v3.2 收尾路径 · 步骤 B）

- **日期**：2026-08-24
- **授权链**：`docs/reports/DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md`（v3.2）§执行路径步骤 B；契约母体 `TELEPHONE-RISK-STORY-FIXTURE-CONTRACT-2026-08-23.md`（SPEC v1）；**P2 处置与验收链定义经 Owner 裁决修订（2026-08-24，见 §1.1/§6）**
- **对象**：`dataset/_canonical/audio_semantic/product_story/telephone_risk/audio/mix.wav`（30.000s @ 16kHz/mono/PCM16；物理结构 = US_dial_tone 0~5s + LBJ_FORD raw 截段 5~30s）
- **工具**：`scripts/verify_audio_fixture.py`（三层校验，ruff clean，B/C/H 可复用）
- **复现**：`python scripts/verify_audio_fixture.py dataset/_canonical/audio_semantic/product_story/telephone_risk/audio/mix.wav --segment dial_tone:0:5 --segment lbj_speech:5:30`

---

## 1. 结论摘要

| 项 | 结果 |
| --- | --- |
| 素材层 C1（YAMNet 黑名单） | ✅ **PASS**（全段/分段/宽阈值审计零 crying/distress/scream/shout） |
| REAL_AUDIO_PIPELINE 验证态 | LBJ 区间稳定复现 Tier0 `distress_cry` 塌缩（13 条事件中 11×）——**作为 Runtime 感知缺陷登记（P2 backlog），不改变 `synthetic_replay` Product Story 的语义输入方式** |
| 定性 | 素材合格 ✅；Product Story 事实链走 `synthetic_replay`，Runtime 感知缺陷与产品故事验收**彻底切开**（§1.1） |
| 步骤 C 可推进性 | ✅ 可推进；P2 处置 Owner 已裁决选 C（§6） |

### 1.1 验收链定义修订（Owner 裁决 · 2026-08-24）

初版「三方对照」对 `synthetic_replay` 不够精确：既规定 `provenance=synthetic_replay`，
又要求 Runtime 必须经过真实（且有缺陷的）Tier0 AudioPipeline——这是逻辑矛盾。
按 Owner 裁决升级为五段链：

```text
Fixture Truth
    ↓
Expected StoryTimeline
    ↓
Runtime Input / Event Source     ← 两态：REAL_AUDIO_PIPELINE 或 SYNTHETIC_EVENT_REPLAY
    ↓
Runtime actual
    ↓
Projection / DOM
```

- 本报告 §2 实测 = **REAL_AUDIO_PIPELINE 态验证记录**（缺陷发现手段；其产出登记为 Runtime 缺陷证据）；
- Product Story 事实源 = **SYNTHETIC_EVENT_REPLAY**（声明式 AudioPerceptionEvent 注入，
  即 fixture yaml audio 通道）；mix.wav 降级为**播放介质**，不承担语义判定职责；
- SPEC §0「时间轴单一真相源，与事件产生方式解耦」由此落地为可操作定义。

## 2. C1 对照表（核心实测 · REAL_AUDIO_PIPELINE 验证态）

| 区间 | ① 素材物理真相（YAMNet 分段实测） | ② Expected StoryTimeline（fixture 注入声明） | ③ Runtime actual（REAL_AUDIO_PIPELINE 态） | 判定 |
| --- | --- | --- | --- | --- |
| dial_tone 0~5s | telephone=0.997, dial_tone=0.979, busy_signal=0.950 | `telephone_persistent` score=0.981 labels=[telephone,dial_tone] @ t=0/2/4 | 1×`telephone_persistent` t=0 tier1=[telephone=0.976, dial_tone=0.964, busy_signal=0.933] | ✅ 语义一致；时间粒度差见 D-B4 |
| LBJ 5~30s | speech=0.478（段级帧平均）；**零 distress 类** | `telephone_persistent` ×5 score=0.953 labels=[telephone,speech] @ t=8/12/20/24/28 | **10×`distress_cry`** + 1×`speech_rapid`(conf=1.0)；tier1 仅 speech/vehicle/horse 等无害标签 | ⚠️ REAL_AUDIO_PIPELINE 态塌缩（D-B1/D-B3）→ 登记 P2；**不影响 synthetic_replay 事实源** |

宽阈值(0.03)审计：黑名单类零出现。

## 3. 偏差清单（逐项归因）

| 编号 | 偏差 | 根因 | 影响面 |
| --- | --- | --- | --- |
| D-B1 | REAL_AUDIO_PIPELINE 态在 LBJ 区间产 11×distress_cry（占事件 85%），叙事应为持续通话 | **Tier0 Energy tremor 重定义塌缩（P2 挂账）**——Tier1 关闭时独立复现同分布事件 | 已登记 `Tier0 semantic collapse: telephone-channel speech → distress_cry`（§6 选 C）；不影响 synthetic_replay 事实源 |
| D-B2 | fixture 声明 LBJ 区间 score=0.953，实测 raw 截段段级 speech=0.478 | 0.953 来自 yamnet baseline 对 **candidates 18s 版**的测量；mix.wav 用 _raw **25s 不同截段**，含非语音间隙拉低帧平均 | 仅影响"用实测背书注入 score"的证据表述；注入路径不跑 YAMNet 不受影响 |
| D-B3 | runtime 出现 `speech_rapid` conf=1.0 @14.24s，不在任何声明中 | 同 D-B1（Tier0 特征规则产物） | 登记为已知 Tier0 行为 |
| D-B4 | dial_tone 区间 runtime 仅 1 枚（t=0），注入 3 枚（t=0/2/4） | VAD 将 5s 信令合为单段；synthetic_replay 与 runtime 两态时间粒度天然不同 | 符合 SPEC §2「三态重跑对比」设计意图，暴露而非缺陷 |

## 4. 与既有审计证据的交叉验证

`AUDIO-DATASET-AUDIT-REPORT-2026-08-23.md` §case 系列已记录同模式污染：
`case_a_mix.wav` distress_cry×9（normal 语音误判继承）、`case_b_mix.wav` distress_cry×9。
本次 mix.wav distress_cry×11 为 **Tier0 P2 塌缩的第三个独立素材样本**，三样本模式完全一致：
正常/受压语音段被特征规则压成哭诉、YAMNet 层全程干净。P2 缺陷的证据链现已闭合到可回归测试级别。

## 5. C2~C6 对照（引用既有证据）

| 维度 | 声明值 | 证据来源 | 状态 |
| --- | --- | --- | --- |
| C2 vision evidence | stranger_a frame40→100, abnormal_dwell | Gate F/G/I（Browser E2E 17+36 通过） | ✅ 已验 |
| C3 temporal relationship | t=20 audio SAME_FRAME vision PERSON_ENTERED；t=24/28 持续 evidence | 注入路径硬约束2 已验证；runtime 路径 TimeMapping/F-3 缺口已登记 | 🟡 双态分记 |
| C4 risk_transition | none → raised @24 | Gate H decision 链证据 | ✅ 已验 |
| C5 decision | notify_family_pending @26 | P0-11 Demo + Browser E2E | ✅ 已验 |
| C6 action | family_app delivered @27 | 同上 | ✅ 已验 |

## 6. P2 处置决策（Owner 已裁决 · 2026-08-24）

正式登记命名：

```text
Tier0 semantic collapse:
telephone-channel speech → distress_cry
```

| 选项 | 内容 | 处置 |
| --- | --- | --- |
| A | 立即修复 P2 后再收口 | ❌ 排除：目标 1（感知算法正确性）不应阻塞目标 2（产品故事收口） |
| B | 忽略 P2 / D2 断言绕开 distress_cry | ❌ 排除：「测试不检查」不改变「页面真实显示」，即假绿，与硬门禁验收纪律直接冲突 |
| **C** | **Product Story 使用 `synthetic_replay` 作为事实源（mix.wav 仅作播放介质）；P2 作为 Runtime perception backlog 独立治理** | ✅ **已采纳（Owner 推荐）** |

选 C 的配套约束：

1. mix.wav 不删除；职责收敛为 Product Story 播放介质（浏览器播放时用户确实听到「电话信令 + 电话信道人声」），**不再用于 AudioKind 分类正确性测试**；
2. Product Story 事件语义经 fixture replay 注入：`t=0 signaling → t=2 telephone_conversation_start → t=8~28 telephone_persistent ×6`，与 Vision `PERSON_ENTERED` 经 Temporal Link → Combined Evidence → Risk → Decision → Action；
3. D2 断言口径不变：synthetic_replay 输入下 DOM **不应出现** distress_cry 行，出现即 FAIL——这是更换输入源后的更严格回归，不是绕开；
4. P2 归 Runtime future governance / perception quality backlog，回归判据已齐备（benign/mix/case_a/case_b 四素材零误报）。

### 6.1 数据资产角色分层（Owner 终版）

| 数据 | 正确职责 |
| --- | --- |
| Layer2 真实电话信令 | Tier1 Qualification |
| LBJ / McCormack | telephone-channel speech 真实性 |
| `telephone_risk_demo.mp4` | Reality Check |
| `case_b_mix.wav` | Browser Infrastructure E2E |
| `mix.wav` | Product Story 播放介质 |
| replayed `AudioPerceptionEvent` / `RiskSignal` | Product Story 确定性语义 |

## 7. 下一步

- **步骤 C（成对冻结 · synthetic_replay 口径）**：
  1. 首项技术确认——demo runtime 的 audio replay 注入通道接线方式（product_story_risk.yaml 当前为 `video_file + audio_path(mix.wav)` 真实管道态，需切换/对齐为 fixture replay 态，F-2/F-3 落地）；
  2. benign/risk 两 yaml 的 `bidirectional_speech_start`@t=2 按 F-1 规则修订为 `telephone_conversation_start`；
  3. 六元组终版登记（Runtime Input 态标注为 SYNTHETIC_EVENT_REPLAY）；
- 产品表面线 **D/E/F** 并行启动（不被 B/C 阻塞）。

## 变更记录

| 时间 | 事项 |
| --- | --- |
| 2026-08-24 | 步骤 B 实测成稿：三层校验数据 + 六元组对照 + D-B1~D-B4 偏差清单 + Owner 决策点 |
| 2026-08-24 | **Owner 裁决修订**：§1.1 验收链五段化（Runtime Input 两态）；§2 表头校准为 REAL_AUDIO_PIPELINE 验证态；§6 三选一定案为 C（synthetic_replay 事实源 + P2 独立入 backlog，正式命名 Tier0 semantic collapse）；§6.1 数据角色分层终版收录 |