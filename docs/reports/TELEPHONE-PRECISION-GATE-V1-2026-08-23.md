# Telephone Persistent Precision Gate · 数据矩阵定版与 V1 首跑

- **日期**:2026-08-23
- **性质**:Runtime/Data Gate（零 Policy 变更、零 src 变更）;合成素材 gitignore 内不入库
- **Owner 指令**:建立 hard-negative 数据集覆盖「最容易误报成电话」的边界;先让 Gate 证明 P1 候选(VAD absolute energy floor / telephone minimum duration)确为主误报来源,不要凭感觉修;达标后才重跑 Gate I
- **守护命题**:`telephone_persistent alone → MONITOR`,不得升级;「电话存在 ≠ 风险存在」

---

## 1. 结论速览

| 指标 | V1 实测 | 说明 |
| --- | --- | --- |
| `telephone_precision` | **61.54%**(8 TP / 5 FP) | 未达 90% 提议线 |
| `telephone_recall`(素材级) | **80%**(8/10) | 3 个 MISS 全部是「tel+人声」形态 |
| `false_telephone_rate` | **10.20%**(5/49 非 none 事件) | |
| `false_telephone_per_asset` | `N2_ambient×1, N4_normal_quiet×3, HN1×1` | |

**P1 归因(Gate 核心产出)**:

| P1 候选 | 归因结果 | 决策建议 |
| --- | --- | --- |
| **telephone minimum duration** | 拦截 **4/5** FP(全部为 0.14~0.2s 微段) | ✅ **有数据支撑,P1 可做** |
| **VAD absolute energy floor** | 拦截 **0/5** FP;且与正样本召回冲突(P3_tel_quiet rms=0.036 正常召回中,而 ambient rms=0.061 无法与之用能量区分) | ❌ **不建议按原样实施**——ambient FP 需第三机制 |

---

## 2. 数据矩阵定版(Batch A,17 资产)

> 配方原则:核心不是量,而是覆盖当前判据(`narrow=True & rate≈0 & tremor≥0.60`)的每个可击穿维度。
> 全部由现有分层素材 + ffmpeg 后处理产出(配方即规格,可再生成);Batch B(Azure TTS 多说话人/情绪对话/老人声)见 §7 待凭证。

### POSITIVE(4)
| 资产 | 配方 | 覆盖维度 |
| --- | --- | --- |
| P1_tel_baseline | telephone_persistent_16k 原样 | 基线 |
| P2_tel_long_30s | stream_loop ×2 → 30s | long duration |
| P3_tel_quiet | volume -12dB(rms 0.036) | 低电平召回下限 |
| P4_tel_10s | 裁剪 10s | short duration |

### NEGATIVE(7,零 tel 期望)
| 资产 | 配方 | 覆盖维度 |
| --- | --- | --- |
| N1_voice_normal | 原样 | normal speech ★盯防 |
| N2_ambient | 原样 | 底噪 ★盯防 |
| N3_micro_events | 原样 | 瞬态 ★盯防 |
| N4_normal_quiet | voice_normal -10dB | very quiet speech |
| **N5_normal_narrowband** | highpass300+lowpass3400 | ★telephone-like narrowband speech(最强判据对抗) |
| **N6_normal_short_narrowband** | N5 再裁 0.8s | ★short narrowband speech |
| N7_normal_leading_silence | anullsrc 5s + normal concat | 正常语音+长静默 |

### HARD NEGATIVE(6)
| 资产 | 配方 | 覆盖维度 |
| --- | --- | --- |
| HN1_tel_normal_full | tel(-6dB)+normal(0dB) 同起 | ★tel+normal_speech(决定性 negative control) |
| HN2_tel_normal_partial | tel 全程+normal 延迟 6s | 渐进交谈形态 |
| HN3_tel_ambient | tel(-4)+ambient(-10) | tel+环境噪声 |
| HN4_tel_micro | tel(-4)+micro(-8) | tel+瞬态 |
| HN5_tel_far_end | tel(-6)+far_end(0) | TV/background speech 近似(far_end 层复用) |
| **HN6_tel_narrowband_normal** | tel(-6)+窄带化 normal(0) | ★★最强组合对抗 |

### 事件正确性口径
`TP` = 含 tel 素材 && 事件时长 ≥1.0s && 与 tel 活跃窗口重叠 ≥50%;含 tel 素材的其余 tel 事件(微段等)= `FP`;不含 tel 素材产出的任何 tel 事件 = `FP`。

---

## 3. V1 结果矩阵

| 资产 | 组 | TP | FP_tel | other | 判定 |
| --- | --- | --- | --- | --- | --- |
| P1_tel_baseline | positive | 1 | 0 | 0 | ✅ |
| P2_tel_long_30s | positive | 2 | 0 | 0 | ✅ |
| P3_tel_quiet | positive | 1 | 0 | 0 | ✅(rms 0.036 保持召回——floor 定标约束) |
| P4_tel_10s | positive | 1 | 0 | 0 | ✅ |
| N1_voice_normal | negative | 0 | 0 | 9 cry | LEAK(P2 塌缩,非 tel) |
| **N2_ambient** | negative | 0 | **1** | 0 | **LEAK(15s 底噪 → tel;P1 两项均不可拦截)** |
| N3_micro_events | negative | 0 | 0 | 2 cry | LEAK(P2) |
| **N4_normal_quiet** | negative | 0 | **3** | 3 | **LEAK(三个 0.14~0.2s 微段 → tel)** |
| N5_normal_narrowband | negative | 0 | 0 | 11 cry | LEAK(击穿方向全是 cry,**不是 tel**——见 §4.3) |
| N6_normal_short_narrowband | negative | 0 | 0 | 1 cry | LEAK(P2) |
| N7_normal_leading_silence | negative | 0 | 0 | 8 cry | LEAK(P2;静默未改变塌缩形态) |
| **HN1_tel_normal_full** | hardneg | 0 | 1 | 1 | **MISS(锚点区间被 cry 假阳性覆盖)** |
| **HN2_tel_normal_partial** | hardneg | 0 | 0 | 0 | **MISS(锚点完全丢失)** |
| HN3_tel_ambient | hardneg | 1 | 0 | 0 | ✅ |
| HN4_tel_micro | hardneg | 1 | 0 | 0 | ✅ |
| HN5_tel_far_end | hardneg | 1 | 0 | 0 | ✅ |
| **HN6_tel_narrowband_normal** | hardneg | 0 | 0 | 1 | **MISS(主区间 none——窄带人声叠加同样使锚点丢失)** |

端到端升级安全:PR #292 已实证默认配置下 18/18 全 MONITOR(ceiling 结构保证),本轮不重复。

## 4. 关键发现

### 4.1 P1 归因:两项候选一真一假(回应 Owner「不要凭感觉修」)

5 个 FP 的逐事件归因:

| 来源 | 时长 | rms | energy floor(<0.03)可拦? | min duration(<1.0s)可拦? |
| --- | --- | --- | --- | --- |
| N4 微段 ×3 | 0.14~0.2s | 0.035~0.039 | ❌(rms 高于阈值) | ✅ |
| HN1 微段 | 0.2s | 0.110 | ❌ | ✅ |
| **N2_ambient** | **15.0s** | **0.061** | ❌(**0.061>0.03**) | ❌(15s 远超时长) |

- **min duration:确证为主因**,单项即可把 precision 从 61.54% 提到 **88.89%**(8/(8+1));
- **energy floor:对本次全部 FP 无效**,且存在原理性冲突——它要拦的 ambient(rms 0.061)比要保持召回的 quiet-tel(rms 0.036)能量更高,任何介于两者之间的阈值都不存在。**ambient FP 需要第三机制**(候选:谱平坦度/窄带纯度/音节结构检验——底噪是宽谱平稳噪声,铃音是人造窄带音,频谱形状可分),属新机制设计,不在 P1 内。

### 4.2 recall 损失 100% 聚焦于缺陷 B(与 PR #292 结论互证并扩展)

3 个 MISS(HN1/HN2/HN6)全部是「tel + 人声」形态:
- HN1/HN2:人声抬升 rate → telephone 分支(rate<0.8)失效;
- HN6 新证据:**即使 normal 被窄带化(narrow 条件稳满足),混合段 rate 抬升仍使锚点丢失**,且该形态下滑向 none 而非 cry——缺陷 B 与电平/频谱配比无关,是 `rate≈0` 维度的结构性盲区。
- 反之,不含人声的形态(HN3/HN4/HN5/P1-P4)全部 PASS——**锚点判据只在「通话中有人说话」时失效,而这恰是诈骗场景的典型形态**。

### 4.3 N5 意外发现:窄带化人声的击穿方向是 crying,不是 telephone

Owner 盯防清单中的「telephone-like narrowband normal speech → telephone_persistent」**未发生**:11 段窄带化人声 0 个 tel、11 个 cry。原因:narrow 条件虽被击穿(高频被滤除),但正常语速 rate≥0.8 使 telephone 分支仍然安全——**塌缩吸收了全部击穿流量**。这再次印证:crying 分支(P2/tremor 修复)承担着「narrow 失守后的泄洪道」角色;在其修复前,narrow 相关收紧动作会把更多流量推入 cry,两个修复存在耦合顺序。

### 4.4 P2(cry 塌缩)现状基线量化

34 个 cry 假阳性事件(N1×9/N3×2/N4×3/N5×11/N6×1/N7×8),全部来自 perception-only 的 distress_cry,不阻塞本 Gate,但作为 tremor 修复(P2)的回归基线记录在案。

## 5. 达标差距与决策依据

提议达标线(供 Owner 确认):negative 组零 tel 事件;hard-negative 组无微段 FP 且锚点检出;precision ≥90%;素材级 recall =100%。

| 动作 | 数据支撑 | 对指标的预期影响 |
| --- | --- | --- |
| P1-a:min duration(≥1.0s 起步,最终值验收定标) | 拦 4/5 FP | precision 61.54%→**88.89%**;negative 组仅剩 ambient 1 例 |
| 第三机制(ambient 频谱结构判别) | 新机制,需设计+ADR | precision →100% 的最后一步 |
| 缺陷 B 方向拍板(判据重构/语义收窄/结合) | 机制设计,Owner 决策 | recall 80%→100% 的唯一路径 |
| P2:tremor 重定义 | 34 cry 基线 | 不影响本 Gate 指标,恢复语义纯度 |

**Gate 判定:V1 未达标(61.54%<90%,recall 80%<100%)→ Gate I 参数冻结维持挂起。**

## 6. 建议的执行序列

```text
① P1-a(min duration)落地——已有 Gate 数据支撑,Task Contract 见 PR #292 §6(范围收窄为仅此一项)
        ↓
② Gate v2 重跑:precision 预期 88.89%(剩 ambient 单点)
        ↓
③ ambient 第三机制设计提案(谱结构判别)→ Owner 审 → 落地 → Gate v3(precision→100%)
        ↓
④ 缺陷 B 拍板(与 far_end corroborating 角色联动,Evidence Matrix §3.4)
        ↓
⑤ recall 达标 → Batch B(Azure TTS 多说话人/情绪对话)扩充边界覆盖
        ↓
⑥ Gate H v2 → Gate I v2(N×T×temporal_window 重估)→ 冻结 Decision Contract
```

## 7. Batch B(Azure TTS)就绪状态

定位遵 Owner 定调:**可控合成测试数据生成器,非真实世界替代品;承担规则压力测试,不承担真实性证明**。三层验证路径(Layer1 合成控制集 → Layer2 公开许可真实语音 → Layer3 真实电话场景)已登记。

- 待 Owner 提供 Azure Speech 资源凭证(经 `.env` 注入,`AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION`,仓库不落库);
- 就绪后生成:男/女/老年音色 normal conversation(10s/30s)、多说话人对话、emotional-but-non-risk 对话——各再经 ffmpeg 窄带化/降噪/混噪派生对抗变体;
- 生成脚本将复用本 Gate 的合成与取证框架(配方即规格)。

## 8. 卫生说明

合成素材位于 `dataset/_canonical/audio_mix/telephone_risk/precision_gate/`(gitignore 覆盖,配方即规格可再生成);脚本 `_precision_gate.py`、`_gate_result.json` 为一次性工具随 PR 清理。本报告为唯一入库产物。