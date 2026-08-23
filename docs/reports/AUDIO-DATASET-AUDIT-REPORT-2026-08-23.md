# Audio Dataset Audit Report · telephone_risk 数据资产与 Tier0 映射审计

- **日期**：2026-08-23
- **性质**：数据资产 / 标签语义 / Tier0 规则映射的只读取证审计（**零代码变更**）
- **上游输入**：Gate I 报告 §5「类别塌缩」取证（58 个 distress_cry 100% Tier0 直出、fallback=0、voice_normal_16k 单独产出 9 个 distress_cry）
- **Owner 指令**：区分三类问题——(1) 音频文件选错；(2) manifest/场景标签定义错；(3) Tier0/Tier1 错误映射。逐资产建立三元组审计表，专项归因 case_a，闭合两个问题后 Gate I 的 N/T/window 参数才值得冻结
- **红线遵守**：本报告不修改任何 `src/` / 配置；Gate H/I 数据不反向改写事实架构；全部修复建议仅列选项，决策权在 Owner

---

## 1. 结论速览（两个问题的答案）

### 问题 A：数据本身有没有选错？

**基本没有。** 素材内容与其声学标签语义一致（正常说话就是正常说话、电话铃音就是持续窄带音、case_a 的 LOW 真值与其音频内容相符）。存在 **2 个次要的数据侧瑕疵**（非塌缩原因）：

| # | 瑕疵 | 证据 | 影响 |
| --- | --- | --- | --- |
| A-1 | manifest 声明 `wav (32-bit float)` 与实际不符 | `dataset/_analysis` 与各 manifest 声明 float32；实测多数资产为 PCM16（24k/16k），仅 48k 分层原料与 `case_a_mix.wav` 为 float32 | provenance 记录错误，不影响判级 |
| A-2 | `voice_stressed_16k.wav` 疑似 = `voice_normal_16k.wav` 同文本 + 音量衰减 ~20% | 两文件前 5 段的 speech_rate / tremor / f0_mean / highband_ratio **完全一致**（如段 2.66-3.40 双方均 rate=2.7 / tremor=0.936 / f0=222.2 / hi=0.0031），仅 rms 整体低约 20%（0.147→0.114、0.099→0.077） | "stress 表现力不足"：stress 层没有独立声学变体，下游无法把 stress 与 normal 区分开 |

### 问题 B：数据没错的话，Runtime 为什么把正常语音压成 distress_cry？

**Tier0 哭诉三条件（`narrow & rate≥1.5 & tremor≥0.60`）的交叠域覆盖了几乎所有连续语音**——不是数据触发了规则，而是规则天然命中一切语音：

| 条件 | 设计意图 | 实测行为 | 结论 |
| --- | --- | --- | --- |
| `tremor ≥ 0.60`（features.py `_pitch_and_tremor`） | 哭腔慢颤调制深度 | 实现为整段包络**全局峰谷比** `(max-min)/(max+min)`；任何带自然停顿/音节间隙的语音都 ≈0.68~0.999。85 段中仅 <0.35s 超短段或 rms≈0.01 的微弱瞬态低于 0.60 | **特征语义错位**，对语音恒真 |
| `speech_rate ≥ 1.5` | 有自然音节流 | 正常语速实测 2.0~5.56 syll/s，恒过阈 | 恒真 |
| `narrow`（highband_ratio < 0.05 @ cutoff 3400Hz） | 电话砖墙带限 | TTS 男声高频占比普遍 0.003~0.047，41 个语音段中 36 段 narrow=True；唯一稳定逃逸的是 far_end（hi=0.083）等宽带素材 | 判据圈入过宽 |

三条全过后进入 crying 分支；前置的 telephone 分支要求 `rate<0.8`（只有 AGC 抹平的持续音满足）、raised 分支要求 `rms≥0.30`（本数据集无高声素材），均无法拦截。叠加 `conf=self.t.cry_confidence` 的 **0.6 硬编码置信度**（rule.py:95），Tier0 单点定级、Tier1 从不触发——与 Gate I "scored_labels 空" 的观察互为印证。

---

## 2. 审计方法

特征级取证脚本（一次性工具，已按仓库卫生约定用后即删）：对 14 个核心音频资产复刻 `AudioPipeline.run` 同款调用面——`AudioDetector.detect(LoadedAudio)` → 逐 VAD 段 `AudioFeatureExtractor.extract` → `AudioRule.evaluate`（默认 `RuleThresholds()`），输出六特征值 + crying 三条件布尔矩阵 + 最终判级。

- **样本量**：14 资产 → 85 个 VAD 段
- **口径**：与真实 pipeline 一致（同 VAD 后端 EnergyVadBackend + merge_gap_ms=300、同特征提取器、同默认阈值）；`vad_ratio` 取 detection 真实值（该参数未参与 evaluate 判级逻辑）
- **人工回听替代说明**：本审计以「manifest/intended 标签 × 特征矩阵一致性」代替人耳回听（无音频播放环境）；所有"listening judgment"结论均基于特征值与标签语义的比对，已在三元组表中标注依据

---

## 3. 数据三元组总表

> 格式：`file → intended semantic label → provenance/duration → Tier0 输出分布 → listening judgment`
> provenance 依据 manifest 与实际解码格式核对；duration 为有效语音跨度。

### 3.1 tts_raw 四相位纯 TTS 原料（最强证据源——与 mix 组合无关）

| file | intended label | provenance / duration | Tier0 输出分布 | judgment |
| --- | --- | --- | --- | --- |
| seg1_normal.wav | NORMAL 相位说话声 | PCM16/24kHz, 6.76s（manifest 声明 float32 不符，A-1） | **distress_cry×5**, none×1 | ✗ 内容确为正常陈述语（rate 2.2~5.0, f0≈200-260Hz），被大面积压成哭诉 |
| seg2_attention.wav | ATTENTION 相位（提醒注意） | PCM16/24kHz, 2.43s | **distress_cry×3** | ✗ 同上 |
| seg3_arousal.wav | AROUSAL 相位（情绪唤起） | PCM16/24kHz, 3.02s | distress_cry×2, **speech_rapid×1** | △ rapid 一段勉强贴合 arousal 语义（am_rate=5.56 过 rapid 阈），但其余仍误入 cry |
| seg4_stress.wav | STRESS 相位（紧张） | PCM16/24kHz, 1.22s | none×1, **telephone_persistent×1** | ✗ stress 短段 rate=0 被 AGC 效应压成「持续通话」——反向误判 |

### 3.2 分层素材（Acoustic phenomenon 层）

| file | intended label | provenance / duration | Tier0 输出分布 | judgment |
| --- | --- | --- | --- | --- |
| voice_normal_16k.wav | 正常说话层 | PCM16/16kHz, ≈12s 有效语音 | **distress_cry×9**, none×2 | ✗ 正常语音被压成哭诉（问题 B 主证据） |
| voice_stressed_16k.wav | 紧张说话层 | PCM16/16kHz, ≈13s | **distress_cry×11**, speech_rapid×1 | △ stress→cry 语义上可辩护（Owner 预判"可能合理"），但见 A-2：其声学与 normal 几乎相同，这些 cry 并非"识别出紧张"，而是规则对任何语音的默认输出 |
| telephone_persistent_16k.wav | 持续通话窄带音层 | PCM16/16kHz, 单一长段 2.12-15.00 | telephone_persistent×1 | ✓ 全数据集**唯一完全正确**的判级（rate=0 命中 telephone 分支） |
| ambient_living_room_16k.wav | 房间环境底噪 | PCM16/16kHz, 整段 0.00-15.00 | **telephone_persistent×1** | ✗ 新发现：底噪（rms=0.061）被能量 VAD 当成语音长段，rate=0 + narrow → 「持续通话」误报 |
| far_end_speech_16k.wav | 远端带限语音存在感 | PCM16/16kHz, 单一长段 2.02-15.00 | none×1 | △ 无事件可接受（该层非事件语义）；注意它是**唯一被 narrow 正确排除**的语音类素材（hi=0.083 宽带）——crying/tremor 对它其实也全过（rate=1.77, trem=0.791），全靠宽带逃逸 |
| micro_events_16k.wav | 衣物/身体摩擦瞬态 | PCM16/16kHz, 两段 6.30-6.80 / 10.22-10.80 | **distress_cry×2** | ✗ 新发现：非谐波摩擦瞬态（hi≈0.000-0.003, f0 打满 500Hz 上限截断）被压成哭诉 |

### 3.3 场景组合层（Scenario-level evidence composition 层）

| file | intended label | provenance / duration | Tier0 输出分布 | judgment |
| --- | --- | --- | --- | --- |
| case_a_mix.wav | case_a 场景混音（manifest 真值 LOW / visit_pending_verify） | float32/48kHz mix, ≈12s | **distress_cry×9**, telephone×1, none×1 | ✗ 全部 cry 逐段继承自 voice_normal 误判（§4 归因） |
| tr_case_a_embed.wav | case_a MP4 内嵌音轨（端到端实际入口） | PCM16/16kHz 自 mp4 抽取 | 与 case_a_mix 完全同构（9 cry + 1 tel + 1 none） | ✗ 同上 |
| case_b_mix.wav | case_b 场景混音（manifest 真值 RISK_SIGNAL） | float32/48kHz mix, ≈13.4s | **distress_cry×9**, none×1 | △ 数量上支撑了下游 N 累积触发，但语义错误：这 9 个 cry 来自 normal/stressed 语音被压成哭诉，而非真实哭诉证据 |
| tr_case_b_embed.wav | case_b MP4 内嵌音轨 | PCM16/16kHz | 与 case_b_mix 同构（9 cry + 1 none） | △ 同上 |

**汇总**：85 段中 distress_cry×68（80%）、telephone_persistent×5、speech_rapid×2、none×10。真正"正确"的判级只有 telephone_persistent 层 1 段。

---

## 4. case_a 专项归因（Owner 重点）

**问：case_a_mix 为什么被判成 distress_cry？**

**答：它的 distress_cry 全部继承自 voice_normal_16k 层的规则误判，与场景组合无关。** 逐段对照：

| case_a_mix 段 (s) | voice_normal 对应段 (s) | 特征对比 | 判级 |
| --- | --- | --- | --- |
| 0.56-0.82 | 0.56-0.82 | rate 3.85=3.85, tremor 0.530≈0.53, hi 0.0044≈0.0043 | 双方 none（短段 tremor 未过阈） |
| 1.26-1.66 | 1.26-1.66 | rate 5.0=5.0, tremor 0.780≈0.787 | 双方 distress_cry |
| 1.96-2.22 | 1.96-2.22 | rate 3.85=3.85, tremor 0.685≈0.693 | 双方 distress_cry |
| 2.66-3.40 | 2.66-3.40 | rate 2.7=2.7, tremor 0.907≈0.936 | 双方 distress_cry |
| 3.92-5.70 | 3.92-4.82 + 5.34-5.72 | mix 底噪抬升使 VAD merge_gap 把两段桥接为一段 | 双方 distress_cry |
| 6.16-6.84 | 6.16-6.82 | 同源 | 双方 distress_cry |
| **8.00-8.14** | （无对应段） | rms=0.013 微弱瞬态（micro_events/ambient 层贡献） | **case_a 独有 telephone_persistent 误报** |
| 9.06-9.34 / 9.76-10.16 / 10.46-10.72 / 11.14-11.90 | 9.06-9.32(none) / 9.76-10.16 / 10.46-10.72 / 11.16-11.90 | 同源 | cry/cry/cry/cry（首段双方 none） |

三个要点：
1. **段边界逐一吻合**（9/11 段完全一致），特征值差异 <3%（mix 电平缩放所致）——case_a 的 cry 不是"组合效应"，是 voice_normal 层误判的原样透传；
2. **8.00-8.14 的 telephone_persistent** 是 mix 独有的第二类误报来源：极低能量瞬态过 VAD 后 `rate=0 & narrow` 命中电话分支——这正是 Gate I 中 case_a "telephone-only false raise" 的微观机制；
3. **case_a 的 LOW 真值没有错**：其音频内容（正常说话 + 底噪 + 微摩擦）与 LOW 语义一致；错在规则把内容压成了 9 个 distress_cry。

---

## 5. 附带发现（超出两问范围但影响契约）

| # | 发现 | 证据 | 影响 |
| --- | --- | --- | --- |
| F-1 | **ambient 底噪 → telephone_persistent** | 整段 15.00s、rms=0.061、rate=0、hi=0.014 | 能量 VAD 无绝对能量下限 + telephone 分支无最短持续时间校验 → 任何安静房间的底噪都可产出「持续通话」事件。Gate I 的 NOTIFY/RAISE 流水线里 telephone 类事件的纯净性存疑 |
| F-2 | **micro_events → distress_cry** | hi≈0.000-0.003、f0 恒打满 500Hz（f0_range 上限截断，f0 提取失效）、tremor 0.62-0.80 | 非语音宽带瞬态同样落入 crying 交叠域 |
| F-3 | **seg4_stress → telephone_persistent** | rate=0.0（0.28s 短段 AGC 抹平） | stress 素材反向误报为电话 |
| F-4 | **f0_mean 大量取离散截断值** | 200.0 / 500.0 / 160.0 高频出现；500=f0_range 上限 | f0 提取粒度粗（自相关滞后分辨率），cry 分支虽未直接消费 f0，但未来任何依赖 f0 的增强都会受制于此 |
| F-5 | **confidence=0.6 硬编码**（rule.py:95 `conf=self.t.cry_confidence`） | Gate I 已证 scored_labels 全空 | Tier0→Tier1 升级链路结构性死路；MONITOR ceiling 门控（ADR-0042）因此仍是必要保护 |
| F-6 | **voice_stressed ≈ voice_normal − 20% 电平**（A-2 详述） | 前 5 段四特征完全一致 | stress 维度在数据层不可辨识 |

---

## 6. 修复建议分层（全部留 Owner 决策，本审计零代码变更）

### 6.1 特征实现层（属机制变更，需 Owner 授权后方可动工）

1. **tremor 重定义**：从「整段包络全局峰谷比」改为真正的慢颤调制深度度量（候选：包络 1~4 Hz 带内能量占比、包络过阈值率、局部峰谷比的分段统计量）。这是 crying 交叠域失控的第一根因。
2. **narrow 判据收紧**：`highband_ratio<0.05` 单条件不足以表达「电话带限」；候选补充：绝对高带能量下限、最小段持续时间（≥2s）、或谐波性检验（电话铃音 vs 语音的可分维度）。这同时缓解 F-1/F-2。
3. **VAD 绝对能量下限**：对 rms<某阈值（如 0.02~0.03）的段直接跳过，消除 8.00-8.14 类瞬态误报与 ambient 底噪长段（F-1）。
4. **telephone 分支加最短持续时间约束**：0.28s 的 seg4 段不应构成「持续」通话（F-3）。
5. **f0 提取精化**（低优先级，F-4）：为后续增强铺路。

### 6.2 数据集层（dataset 动作，不改 Runtime）

1. 修正 manifest 的采样格式声明（A-1：float32 → 实际 PCM16，或统一重产为声明格式）。
2. 重生成 `voice_stressed_*` 为真正的 stress 声学变体（提高基频抖动/速率/能量起伏），使 stress 维度可辨识（A-2/F-6）。
3. 若 micro_events 意图作为 negative 对照保留，需确认其在修复后的特征下不再命中（当前它命中 crying 属规则缺陷而非数据缺陷，但修复验证时应把它列入回归清单）。

### 6.3 流程层

1. **MONITOR ceiling 维持**：在 6.1 落地并用本数据集回归验证之前，ADR-0042 的 class_map/MONITOR 门控不可解除（F-5 是结构性理由）。
2. **回归基准**：本次 85 段特征矩阵可作为规则修复后的对照基线（预期目标态：seg1/seg2/voice_normal → none 或 speech_rapid；telephone_persistent 保持正确；far_end/micro/ambient → none）。

---

## 7. 与 Gate I 参数冻结的关系（回应 Owner 前提）

Owner 前提：「只有两个问题闭合后，Gate I 的 N/T/window 参数才值得冻结」。本审计闭合结果：

- 问题 A 闭合：数据无根本性选错（2 个次要瑕疵已定位，均可独立修）；
- 问题 B 闭合：塌缩根因锁定为 Tier0 三条件交叠域 + tremor 特征语义错位（§1 表 + §3 矩阵 + §4 归因），**不是**数据触发；
- 因此 **N/T/window 参数的冻结可以推进，但必须附带两条注释**：
  1. Gate I 矩阵中的 distress_cry 计数本质上是「连续语音通过率」，不是哭诉检出率——参数在当前规则下的鲁棒性区间只在当前规则语义内有效；
  2. 若 Owner 批准 6.1 特征/规则修复，修复落地后须用同一批资产重跑 Gate I 七配置矩阵复核 N/T/window 选择（预期 false_raise 结构会显著变化：case_a 的 9 cry 应归零）。

---

## 8. 审计产物与卫生

- 取证脚本与中间 JSON（`_audio_audit.py` / `_audit_features.json`）为一次性工具，随本 PR 清理，不入库；
- `data/cache/gate_h/*.wav`（审计重建的转码副本）与 `data/cache/audit/` 均在 gitignore 覆盖范围内，本地留存供 Owner 复核，不入库；
- 本报告为唯一入库产物。