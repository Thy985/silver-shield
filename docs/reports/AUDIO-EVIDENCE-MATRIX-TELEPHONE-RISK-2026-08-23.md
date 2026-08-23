# Telephone Risk · Audio Evidence Matrix（产品风险模型先行论证）

- **日期**：2026-08-23
- **性质**：产品风险模型分析文档（提案，待 Owner 审定）；零代码变更
- **前置输入**：《AUDIO-DATASET-AUDIT-REPORT-2026-08-23.md》（塌缩根因取证）、Owner 定调：
  - telephone_risk 的产品叙事是**「疑似高风险电话交互」**，不是「哭诉检测」；
  - `telephone_persistent`=场景锚点，`distress_cry`=可选增强证据；
  - **先确定产品风险模型，再决定哪些 AudioKind 值得建立 positive dataset**——不为填数据集空白而补哭声。

---

## 1. Evidence Matrix 本体

> 列定义：
> **① 真实数据** —— 该声音事实是否真实存在于 telephone_risk 数据集（含 Tier0 实际产出情况，依据审计 85 段取证）；
> **② 产品意义** —— 在「疑似高风险电话交互」叙事下是否有真实风险含义；
> **③ 进 Policy** —— 是否应成为 Risk Policy 的升级依据（risk_signals 消费面）。
> 判定标准（③）：(a) 有真实正样本支撑；(b) 单独升级价值 > 误报代价；(c) 未被其他证据冗余覆盖。

| AudioKind / 声学事实 | ① 真实数据 | ② 产品意义 | ③ 进 Policy | 一句话依据 |
| --- | --- | --- | --- | --- |
| `audio_telephone_persistent` | ✅（唯一判级正确的 kind；但有 3 个误报源混入，见 §3.1） | **核心·场景锚点** | ✅ | 「长时间专注通话」是电话诈骗交互的结构性信号，几乎必然发生 |
| `audio_speech_rapid` | ⚠️ 仅"碰巧出现"（2 段，narrow 逃逸后的次级落点，无设计素材） | 弱辅助（情绪唤起的行为表现） | 待定 | am_rate 判据未经独立验证，且无正样本集 |
| `audio_voice_raised` | ❌ 完全不存在（全数据集 rms<0.30，历史零事件） | 辅助风险（音量骤升=冲突/紧急通用信号） | 待定 | 语义清晰但零数据零验证；若未来建 positive 集，优先级高于 cry（rms 判据简单可靠、误报低） |
| `audio_distress_cry` | ❌ 真素材为零（68 个事件 100% 为塌缩误判） | 可选增强证据 | **暂不进入** | 尚未证明链条需要它；技术门槛与边际增益不成比例（§2.1） |
| `audio_anomaly_other`（兜底） | ❌ 零触发（Gate I 已证 fallback=0） | 无独立意义（兜底类） | ❌ | MONITOR 恒封顶双保险已覆盖，保持现状 |

### 非 Kind 的声学层（素材/背景角色，同样纳入边界管理）

| 声学层 | ① 真实数据 | ② 产品意义 | ③ 进 Policy | 角色 |
| --- | --- | --- | --- | --- |
| normal_speech（voice_normal/stressed、seg1-2 相位） | ✅ 大量存在 | **中性背景**——甚至是「生活如常」的正面信号 | ❌ | negative（评测回归基线） |
| voice_stressed | ✅ 存在但与 normal 不可辨识（审计 A-2） | stress evidence（潜在） | ❌ 暂缓 | 待重生成真正 stress 变体后重评 |
| ambient（环境底噪） | ✅ 存在（被误报为 tel×1） | 无 | ❌ | negative + 回归清单项 |
| micro_events（衣物摩擦瞬态） | ✅ 存在（被误报为 cry×2） | 无 | ❌ | **hard negative**（最易击穿规则的对照组） |
| far_end_speech（远端带限语音） | ✅ 存在（宽带逃逸，判 none） | △ 意外发现：它是「电话另一端有人」的强旁证，与锚点同源 | ❌（并入锚点考量，见 §3.4） | 锚点的 corroborating 信号候选 |

### 缺失组合（比缺失素材更关键的评测缺口）

| 组合 | 状态 | 应然行为 |
| --- | --- | --- |
| **telephone_persistent + voice_normal**（老人在打电话，但无其他异常） | ❌ 数据集中不存在 | **必须 MONITOR，不升级**——这是锚点 precision 的决定性用例 |
| telephone_persistent + distress_cry | ❌ 两项素材均缺 | （若 cry 层未来建立）增强档验证用例 |
| telephone_persistent + distress_cry + vision | ❌ | 完整升级链验收用例 |

---

## 2. 核心论证：telephone_risk 目前不需要 distress_cry

### 2.1 三条理由

1. **可得性不对称**。「老人独自在家接诈骗电话」场景中，*长时间专注通话*几乎必然发生，*哭声*未必发生。锚点证据的选择天然应该偏向高频可观测事实。产品价值链 `持续通话 → 情绪/行为变化 → 人物上下文 → 升级` 的第一环和第三环都已成立，第二环（cry）不是必需路径。
2. **误报代价不对称**。tel_persistent 误报的代价是 MONITOR 多看一眼；把正常说话判成哭诉的代价是「狼来了」——污染事件流统计、消耗家庭信任。在当前特征栈连「哭诉 vs 正常语音」都无法区分（tremor 失效已实证）的情况下建 cry 正样本集，只会生产无法验证的数据。
3. **差异化不在声学情绪识别**。SilverShield 的竞争力在于时空合成（门前行为 × 通话持续性 × 时域连续性），不在于做一个高难度的哭声分类器。竞品壁垒排序上，Evidence Synthesis >> 声学情感计算。

### 2.2 因此的正确动作序列

```text
① 冻结 Policy 最小集（§4）：只认 telephone_persistent 锚点
        ↓
② 用 negative / hard-negative 集证明锚点的纯净度（precision 先行）
        ↓
③ 待定区（voice_raised / speech_rapid）按「判据可靠性」逐个补数据验证
        ↓
④ distress_cry 保持「暂不进入」：等真实需求/真实样本出现，不为填空白而造数据
```

---

## 3. 逐条补充论证

### 3.1 锚点的三个误报源必须先清除（矩阵带来的优先级重排）

审计 §6.1 将 tremor 重定义列为修复第一优先；**从 Policy 视角看应重排**——`telephone_persistent` 是唯一进 Policy 的 kind，它的 precision 就是整条 audio risk 链的 precision。直接污染它的三个误报源优先级最高：

| 误报源 | 机制 | 修复归属 |
| --- | --- | --- |
| ambient 底噪 → tel（15s 整段误报） | 能量 VAD 无绝对能量下限 + tel 分支无最短持续时间 | VAD 能量下限 + tel 最短时长校验（**P1**） |
| case_a 8.00-8.14 微瞬态 → tel | 同上（rms=0.013 瞬态过 VAD 后 rate=0） | 同上（**P1**） |
| seg4_stress 0.28s 短段 → tel | AGC 抹平致 rate=0 | 同上（**P1**） |

tremor 重定义与 crying 分支修复降级为 P2（感知层质量改进）——因为 distress_cry 已「暂不进入 Policy」，其误判不再直接影响风险链，只影响 UI 展示与 Memory 记录的语义纯度。

### 3.2 speech_rapid / voice_raised 的「待定」含义

两者产品语义都成立，但共同问题是**零正样本 + 判据未独立验证**。区别在判据可靠性预期：

- `voice_raised`：rms 判据简单、物理含义直白、误报可控——若建 positive dataset，性价比最高；
- `speech_rapid`：am_rate 与 tremor 共享包络特征栈，tremor 失效的教训提示它同样脆弱——应在特征修复后再评估。

「待定」≠ 排队等待：在 Owner 批准前，两者都不启动数据建设。

### 3.3 distress_cry 的处置细节

「暂不进入 Policy」落地为三条：

1. **数据侧**：不新建哭诉素材（Owner 拍板）；
2. **规则侧**：修复后的 crying 分支保持极保守（多条件高阈值），目标是从「默认命中」变为「几乎不触发」，消除 normal/micro→cry 的语义污染；
3. **契约侧**：`AUDIO_DISTRESS_CRY` 枚举保留（ADR-0042 五档框架容纳它），仅停留在感知层输出（UI 中文标签、Memory 记录），不进 risk_signals 升级消费面。

### 3.4 far_end_speech 的意外发现

远端人声是宽带语音（highband_ratio=0.083），当前 narrow 判据把它排除在所有 kind 之外。但产品语义上「电话另一端有人说话」正是通话存在的强旁证——它与 telephone_persistent 同源互补（近端窄带铃音/通话声 + 远端带限人声 = 更完整的「正在通话」图景）。建议：不新增 kind，将 far_end 作为锚点判定的 corroborating 特征候选记入 v2 备忘，本轮不动。

### 3.5 anomaly_other 维持现状

零触发 + 恒封顶 MONITOR 双保险（config.py:391）已自洽。矩阵确认其「兜底类不承载产品语义」定位，无需动作。

---

## 4. Risk Policy 最小集提案（MVP Audio→Risk Chain）

```text
audio_telephone_persistent          ← 唯一进 Policy 的 AudioKind（锚点）
        ↓  ADR-0041 SignalTemporalLinker（时域持续性/聚合）
        ↓  ADR-0028 CrossModalLinker（episode 级视觉合成）
RiskSignal（MONITOR ceiling 内）
        ↓  中心综合（本模块不做最终定性）
```

配套约束：

- 除锚点外的一切 AudioKind：感知层可见（UI/Memory），Policy 不可见；
- `telephone_persistent alone → MONITOR`，永不单独升级（Owner 定调，与 ADR-0042 门控一致）；
- 升级必须依赖合成证据（时域 + 视觉），单模态音频事件无升级权；
- MONITOR ceiling 解除的前提条件由「锚点 precision 验证（§2.2-②）」给出，而非参数调优。

## 5. 与既有契约的对齐检查

| 契约 | 影响 | 结论 |
| --- | --- | --- |
| ADR-0040（risk_signals 一等输入，C7 六信号硬顶） | 本矩阵约束的是「哪些 kind 值得成为 policy 升级依据」，不触碰信号通道结构 | 无冲突 |
| ADR-0041（SignalTemporalLinker） | 锚点的时域聚合正是其设计用途 | 无冲突 |
| ADR-0042（Evidence Strength 五档 + MONITOR ceiling） | ceiling 维持获得新论据：除锚点外所有 kind 均缺正样本支撑，解除无从谈起 | 维持 |
| 审计报告 §6 修复建议 | 优先级重排：VAD 能量下限 + tel 最短时长升为 P1（护锚点精度），tremor/narrow 降为 P2（感知纯度） | 修订见 §3.1 |
| Gate I 参数冻结 | 维持挂起；重跑前提更新为「锚点 precision 在 hard-negative 集上达标」 | 挂起 |

## 6. 数据集行动指南（先模型后数据的落地）

| 动作 | 对象 | 说明 |
| --- | --- | --- |
| ✅ 建 positive | telephone_persistent 变体（不同铃音/免提/时长梯度） | 唯一值得扩充的正样本 |
| ✅ 建 negative / hard-negative（服务评测，非 Policy） | voice_normal、ambient、micro_events、**tel+normal 组合** | hard-negative 组合是锚点 precision 的决定性用例 |
| ⏸ 待定区冻结 | voice_raised、speech_rapid | Owner 批准前不建数据 |
| ❌ 不建 | distress_cry | 未证明需要；等真实需求/真实样本 |
| 🔧 修数据记录 | manifest 格式声明、voice_stressed 重生成评估 | 审计 A-1/A-2 |

## 7. 待 Owner 决策点

1. 本矩阵的 Policy 最小集（§4）是否认可为 telephone_risk 的 MVP 音频风险模型；
2. 修复优先级重排（§3.1：P1=护锚点精度两项，P2=tremor/narrow 感知纯度）是否批准——若批准，即可按此开步骤①机制修复；
3. hard-negative 组合素材（tel+normal）的生成是否列入数据集动作（mix 基础设施已具备）；
4. far_end 作为锚点旁证的 v2 备忘是否登记。

---

*本矩阵为分析提案，Risk Policy 的最终定义权在 Owner。零代码变更，一次性产物已清理。*