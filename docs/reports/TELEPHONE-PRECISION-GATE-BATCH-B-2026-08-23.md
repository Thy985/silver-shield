# Telephone Persistent Precision Gate · Batch B(edge-tts 多说话人对抗矩阵)

- **日期**:2026-08-23
- **性质**:Runtime/Data Gate 扩展(零 Policy/src 变更);素材 gitignore 内不入库,配方即规格
- **前置**:Gate V1(PR #293 同分支前序报告);Owner 定调 edge-tts=可控合成测试数据生成器(免 key),承担规则压力测试,不承担真实性证明
- **Batch B 目的**:验证 V1 发现的规则漏洞是否**跨 TTS 分布成立**(而非仅现有素材特例)

---

## 1. 结论速览

| 验证命题 | Batch B 实测 | 结论 |
| --- | --- | --- |
| 缺陷 B(tel+人声形态锚点丢失)是否跨 TTS 成立 | **4/4 hard-negative 全部 MISS**(B9/B10/B11 主区间被 cry/rapid 吞掉,B12 窄带版主区间 none) | ✅ **成立,V1 非特例** |
| P2 cry 塌缩是否跨 TTS 成立 | 104 个非 none 事件中 **96 个 distress_cry**;5 个纯 negative 人声素材全部大面积塌缩 | ✅ **成立,规则本身问题** |
| P1 min duration 是否仍是误报主因 | **3/3 FP 全部为 0.16~0.22s 微段 tel,min duration 100% 覆盖** | ✅ 强化(A+B 合计 7/8) |
| narrow 击穿方向 | B6/B8 窄带化后仍零 tel 直产,流量继续滑入 crying | ✅ 与 V1 N5 一致 |

**合并判定**:Gate 整体未达标;`min duration` 的数据支撑从「单项可做」升级为「跨分布确证」;缺陷 B 从「3 形态样本」扩展到「7 形态样本」,修复优先级进一步上升。

---

## 2. 数据矩阵(Batch B,12 资产)

### 生成管线
`edge-tts 7.2.8`(微软 Edge 在线 TTS,免 key)→ mp3 → ffmpeg 转 16k PCM16 mono → ffmpeg 对抗派生/混音。

### TTS-Negative(5)
| 资产 | 配方 |
| --- | --- |
| B1_male_conv | zh-CN-YunxiNeural 男声日常对话 ~10s |
| B2_female_conv | zh-CN-XiaoxiaoNeural 女声日常对话 ~12s |
| B3_elder_long | zh-CN-YunjianNeural rate=-20% pitch=-15Hz 老年感长独白 ~29s |
| B4_multi_speaker | B1/B2 四段交替 concat(多说话人) |
| B5_emotional_nonrisk | Yunxi rate=+15% vol=+10%,兴奋正面内容(情绪化非风险) |

### TTS-Adversarial(3)
| 资产 | 配方 | 针对 |
| --- | --- | --- |
| B6_male_narrowband | B1 + highpass300/lowpass3400 | narrow 判据 |
| B7_male_quiet | B1 + volume -12dB | 能量/VAD 下界 |
| B8_multi_narrowband | B4 + 窄带化 | 多说话人×窄带复合 |

### TTS-Hard-Negative(4,tel -6dB + 人声 0dB,duration=first)
B9_tel_elder_long / B10_tel_multi_speaker(TV background speech 真实版)/ B11_tel_emotional / **B12_tel_male_narrowband(最强组合)**

## 3. 结果矩阵

| 资产 | 组 | TP | FP_tel | other | 关键观察 |
| --- | --- | --- | --- | --- | --- |
| B1_male_conv | negative | 0 | 0 | 5 cry | 塌缩 |
| B2_female_conv | negative | 0 | **1** | 7 | 微段 tel 0.16s |
| B3_elder_long | negative | 0 | 0 | 11 | 老年感(rate/pitch 降档)**不改变塌缩命运**,28s 里 11 cry |
| B4_multi_speaker | negative | 0 | 0 | 23 cry | 多说话人全面塌缩 |
| B5_emotional_nonrisk | negative | 0 | 0 | 5 cry | 兴奋语气未触发 rapid 主导,仍入 cry |
| B6_male_narrowband | adversarial | 0 | 0 | 7 cry | 窄带化→crying 泄洪(V1 N5 模式复现) |
| B7_male_quiet | adversarial | 0 | 0 | 5 cry | 低音量未产 tel(V1 N4 微段模式此音色未复现) |
| B8_multi_narrowband | adversarial | 0 | **1** | 32 cry | 47s 里唯一微段 tel 0.22s |
| B9_tel_elder_long | hardneg | 0 | 0 | 2 | **MISS:主区间 13.08s distress_cry** |
| B10_tel_multi_speaker | hardneg | 0 | 0 | 2 | **MISS:主区间 12.94s distress_cry** |
| B11_tel_emotional | hardneg | 0 | 1 | 2 | **MISS:主区间 12.92s speech_rapid**(新击穿方向:高 am_rate 人声把混合段推入 rapid 分支) |
| B12_tel_male_narrowband | hardneg | 0 | 0 | 0 | **MISS:主区间 none**(与 V1 HN6 同构) |

指标(Batch B 口径):precision 0%(无 correct TP)、recall 1/4、false_telephone_rate 2.88%、per_asset=B2/B8/B11 各 1。

> 注:precision=0% 是「人声在场时锚点召回为零」的口径体现,不是比 V1 更差——两批合并看,锚点失效模式完全一致:**只要人声与电话同场,锚点即不可靠**。

## 4. P1 归因(累计)

| FP 来源 | 时长 | 处置 |
| --- | --- | --- |
| V1:N4×3、HN1×1 | 0.14~0.2s | min duration ✅ |
| **B:B2×1、B8×1、B11×1** | 0.16~0.22s | **min duration ✅(本轮新增 3/3 全覆盖)** |
| V1:N2_ambient×1 | 15.0s | ❌ 需第三机制(谱结构判别) |

**合计:8 个 FP 中 7 个由单一 min duration 参数覆盖(87.5%),ambient 单点待第三机制。**

## 5. 对执行序列的影响(Gate V1 §6 修订)

```text
① P1-a(min duration)落地 —— 数据支撑升级为跨 TTS 分布确证(7/8 FP),Task Contract 已备(PR #292 §6)
        ↓
② Gate v2 重跑:预期 precision ≈ 8/(8+1)=88.89%(A 批)且 B 批 FP 归零,剩 ambient 单点
        ↓
③ ambient 第三机制设计提案(谱平坦度/窄带纯度)→ precision→100%
        ↓
④ 缺陷 B 拍板 —— 样本从 3 形态扩至 7 形态(HN1/HN2/HN6/B9/B10/B11/B12),
   且新增击穿方向(speech_rapid);far_end corroborating 角色联动评估
        ↓
⑤ recall 达标 → Gate H v2 → Gate I v2(N×T×temporal_window)→ 冻结 Decision Contract
```

## 6. edge-tts 使用边界(遵 Owner 定调)

- ✅ 用作:可控对抗样本生成器、规则压力测试、回归基线扩充;
- ❌ 不用作:真实世界 precision 证明(最终 Acceptance/Calibration 依赖 Layer2 公开许可真实语音与 Layer3 真实电话场景);
- 本轮价值已兑现:**证明了规则漏洞不是现有素材的特例,而是判据设计的普遍行为**——这恰是合成控制集的职责上限,后续重心应转向机制修复而非继续扩量。

## 7. 卫生说明

素材位于 `dataset/_canonical/audio_mix/telephone_risk/precision_gate_batch_b/`(gitignore 覆盖,TTS 文本+音色+参数即配方,可再生成);脚本 `_batch_b.py`、`_gateb_result.json` 为一次性工具随 PR 清理。本报告为唯一入库产物。