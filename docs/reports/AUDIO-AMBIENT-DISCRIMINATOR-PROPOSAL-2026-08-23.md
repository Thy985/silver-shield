# Ambient 第三机制设计提案：包络平稳性判别器（Envelope Stationarity Discriminator）

> 日期：2026-08-23（同日修订：追加 §7 机制资格审查）
> 状态：**REJECTED BY QUALIFICATION AUDIT — 提案经 Owner 审查流程否决，本文档存档完整证据链**
> 修订要点：① §3.1 结论表述按 Owner 审定收紧；② §7 资格审查证明 env_cv 在真实运行时路径下
> **双向失效**，§0/§4 的原始提案结论已被推翻；③ precision 收尾路径待重新拍板（§6）。
> 任务定位：Precision Gate 执行序列第 ③ 步「ambient 第三机制」的第一阶段（提案 → 资格审查）。
> 上游依据：`TELEPHONE-PRECISION-GATE-V2-2026-08-23.md`（唯一剩余 FP = N2_ambient）、
> `AUDIO-EVIDENCE-MATRIX-TELEPHONE-RISK-2026-08-23.md`（telephone_persistent = 场景锚点，非充分条件）。

---

## 0. 结论速览（资格审查修订版）

1. **谱维负结果（表述已按 Owner 审定收紧）**：对全部 Gate 资产 421 个 VAD 段的帧级 STFT
   统计显示，N2 底噪段与 TP 电话段在谱平坦度/峰能量占比/谱熵/过零率上完全重叠。
   **在当前候选谱统计及当前语料上，未发现具有足够分离度的谱维特征**——这是实验结论，
   不等于所有谱特征都不可能分离（原稿「同构/不存在任何判别器」的表述过强，已修正）。
2. **env_cv 提案已被 §7 资格审查否决**：第一轮「整条资产口径」下的分离间隙 `[0.18, 0.31]`
   是 VAD 段化伪影——真实运行时路径先切段后提特征，电话素材的 cadence 静默被 EnergyVAD
   切除，**TP 段级 env_cv（0.065~0.074）反而低于 N2 底噪段（0.180~0.183）**，方向反转；
   且周期性冰箱嗡鸣（0.253）可穿透 0.25 阈值。若实施：TP 召回 6/7 全灭 + hard negative
   照样穿透，双向失效。
3. **Owner 审查流程的价值实证**：本案例按「proposal → 资格审查 → 实施 → Gate V3 → 冻结」
   流程执行，审查环节在零生产代码变更的成本下拦下一次必然返工的实现。
4. **新暴露威胁域（§7.4）**：警报器、慢板音乐、宽带音乐等「窄带非电话持续音」在 Tier0
   特征空间与 telephone 完全同簇（rate=0.0 + narrow），是比 N2 更广的系统性 FP 来源，
   现有 min duration 无法拦截。
5. **precision 收尾路径待重新拍板（§6）**：Tier0 特征空间内暂无合格第三机制候选；
   后续选项涉及产品语义口径或 Tier1 承担，须 Owner 决策。

---

## 1. 背景与问题定义

### 1.1 问题资产

`N2_ambient.wav`（15s 合成客厅底噪，rms≈0.061）在 EnergyVAD 下被切为两个长段
（8.16s / 6.82s），两段均满足 narrow(highband_ratio<0.05) + speech_rate<0.8 → 判为
`audio_telephone_persistent`。P1-a min duration（≥1.0s）对长段无效——这是 V2 后
precision 88.89% 中唯一的剩余 FP 来源。

### 1.2 为什么 P1-a 之后不能就此收手

V2 报告已论证：energy floor 对 N2 无效且与 quiet-tel 召回原理性冲突（N2 rms 0.061 >
P3_tel_quiet rms 0.036，无可用响度阈值）。若接受 N2 为已知局限，锚点 precision 封顶
88.89%；本模块作为「风险数字孪生前端事实采集器」，锚点的信噪比直接决定后续 Policy
升级（消费 risk_signals）时中心侧对该信号的信任基础。序列③的目标即 precision 收尾到 100%。

### 1.3 本提案的方法学承诺

遵循 Owner 红线「用数据归因，不要凭感觉修」：先离线探针验证候选特征的分离度，
再提机制设计；本 PR 不含任何生产代码变更。

---

## 2. 探针方法（可复现配方）

> 探针脚本为一次性产物，不入库（`.gitignore` 已排除 `dataset/**`）；本节配方即规格，
> 素材可按既有报告 §2 配方再生成。

### 2.1 第一轮：谱维候选特征 × 全量段

- 语料：`dataset/_canonical/audio_mix/telephone_risk/` 全部 34 条 wav
  （case_a/b + hardneg×5 + precision_gate×17 + batch_b×12）；
- 段切分：项目真实 `EnergyVadBackend()`（floor=0.01, relative_ratio=0.4, min_segment_ms=150）；
- 每段用项目真实 `AudioFeatureExtractor.extract()` 复现现有判定路径
  （`AudioRule.evaluate` 确认 kind 归属），并叠加帧级 STFT 特征
  （frame=1024, hop=512, hanning）：
  | 特征 | 定义 |
  | --- | --- |
  | spec_flat | 谱平坦度 = exp(mean(log pw)) / mean(pw)，段级取中位数与均值 |
  | peak_ratio | top-5 bin 能量和 / 总能量（窄带纯度） |
  | spec_ent | 归一化谱熵 −Σp·log p / log N |
  | zcr | 过零率 |
  | lowband_ratio | ≤3400Hz 能量占比（narrow 口径对照） |
- 产出：421 个段级样本（no_hit 117 / distress_cry 209 / telephone 78 / speech_rapid 17）。

### 2.2 第二轮：时间维候选特征 × min-duration 存活长段

对 FP-N2 与全部 TP 长段（10 条资产整段）：

| 特征 | 定义 |
| --- | --- |
| env_cv | 包络变异系数 std(env)/mean(env)，env=30ms 帧 RMS 序列 |
| env_diff | mean(\|Δenv\|)/mean(env) |
| peak_freq_mad | 主谱峰频率的帧间中位绝对偏差 (Hz) |
| cent_std | 谱质心帧间标准差 (Hz) |

---

## 3. 结果

### 3.1 负结果：谱维特征全面重叠（第一轮）

聚焦决策域——`kind=audio_telephone_persistent` 命中段的分布（FP-N2 vs TP_tel）：

| 特征(段级中位数) | FP_N2 (n=2) | TP_tel (n=76) 分布 | 可分？ |
| --- | --- | --- | --- |
| spec_flat_med | 0.0061~0.0064 | [0.00001, 0.03932]，p25=0.00047 med=0.00151 | ❌ N2 落于 TP 内部（大量窄带人声 TP flat<0.006） |
| peak_ratio_med | 0.8510~0.8513 | [0.21681, 0.94563]，med=0.67111 | ❌ 重叠（B4_multi_speaker 达 0.94、case_b 0.88） |
| spec_ent_med | 0.3577~0.3589 | [0.25889, 0.70449]，med=0.48313 | ❌ 重叠 |
| zcr_med | 0.0635~0.0655 | [0.01857, 0.31281]，med=0.08798 | ❌ 重叠（B9 0.0498 低于 N2） |
| lowband_ratio_med | 0.9862~0.9863 | [0.90513, 0.99997] | ❌ 重叠 |

**根因（Owner 审定表述）**：telephone_persistent 正样本素材本身是合成持续音
（AGC 抹平包络、砖墙带限）。**在当前候选谱统计及当前语料上，未发现具有足够分离度的
谱维特征。** 此为实验结论，不构成「所有谱特征都不可能分离」的理论断言；它证伪了
V1 报告 §6 中「谱平坦度/窄带纯度」的初始候选方向，避免了一次无效实现。

### 3.2 正结果：时间维 env_cv 干净分离（第二轮 · ⚠️ 后被 §7 推翻）

> **⚠️ 修订警示**：本节分离结论是在「整条资产」口径下测得的；§7 资格审查证明，
> 在真实运行时路径（EnergyVAD 先切段 → 段级提特征）下该分离**不存在且方向反转**。
> 本节仅作证据链存档，不得引用为实施依据。

| 资产（整段） | 角色 | env_cv | cent_std_hz |
| --- | --- | --- | --- |
| N2_ambient | **FP** | **0.182** | 58.8 |
| HN3/HN3_tel_ambient (tel+ambient) | TP（最低） | 0.312 | 186.4 |
| P1/P2/P3_tel_* | TP | 0.407 | ~1111 |
| HN4_tel_micro | TP | 0.407 | 1111 |
| HN5_tel_far_end | TP | 0.440 | 1025 |
| P4_tel_10s | TP | 0.519 | 1305 |

- `env_cv`：FP 与最低 TP 之间间隙 `[0.182, 0.312]`，相对裕度 +37% / −20%；
- `cent_std_hz` 同样可分（[58.8, 186.4]）但需逐帧 STFT，计算贵一个量级 → 记为备选增强，暂不实施；
- 排除项：`peak_freq_mad` 全体为 0（合成音主峰均锁相，无区分度）；
  `env_diff` 方向反常（N2 因包络均值低反而最大，0.306 vs TP 0.031~0.070）——均不可用。

### 3.3 物理解释（⚠️ 该解释建立在整段口径上，已被 §7.4 伪影机制取代）

机械/电气底噪是无限平稳过程（包络恒定、频谱成分恒定）；通信音频即使经 AGC 平滑，
仍保有协议性或内容性起伏（铃音 cadence、通话轮替、线路噪声调制）→ 包络变异系数
系统性更低。这与 ADR-0026 设计文档「电话 AGC 抹平包络致音节率≈0」的既有观察互补：
speech_rate 看「有没有峰」，env_cv 看「整体有多平」。

---

## 4. 提案设计

> **⚠️ REJECTED — 本节设计已被 §7 资格审查否决，仅存档，禁止实施。**

### 4.1 机制定义

```
Envelope Stationarity Discriminator（包络平稳性判别器）

env_cv = std([rms(frame_i)]) / mean([rms(frame_i)])    # frame=30ms，复用 _envelope()

telephone 分支新增必要条件：features.env_cv >= thresholds.telephone_min_env_cv
建议默认阈值：telephone_min_env_cv = 0.25   # 间隙 [0.182, 0.312] 中点偏下
```

### 4.2 改动面（实施时，非本 PR）

| 文件 | 变更 |
| --- | --- |
| `src/home_perception/audio/features.py` | `AudioFeatures` 新增字段 `env_cv: float`；`extract()` 复用 `_envelope()` 计算（约 4 行）；空段返回 0.0 |
| `src/home_perception/audio/rule.py` | `RuleThresholds` 新增 `telephone_min_env_cv: float = 0.25`；telephone 分支条件追加一项（约 3 行） |
| 测试 | 新增回归锁：N2 fixture 两长段必须拒绝；P1/P2/P3/P4 召回保持；env_cv 边界值（0.249/0.251）；`test_audio_tier1.py` 既有子类覆写兼容性 |

影响域仅 telephone 分支；raised/crying/rapid 判定路径不动。契约层（事件 Schema/MQTT）
零变更——`AudioFeatures` 是内部感知对象，不入事件 payload。

### 4.3 成本

O(n) 时间、零内存峰值变化、零新依赖（numpy 向量化即可）。相对 STFT 类备选
（cent_std）便宜一个数量级，符合边缘 CPU 约束（AGENTS.md §4.1）。

### 4.4 Gate V3 验证计划（实施后）

1. 复跑 Precision Gate 全量矩阵（A 批 17 + B 批 12）：预期 precision 100%、recall 不变（8/14，
   缺陷 B 域未触碰）、false_telephone_rate 10.2% → 0%；
2. 回归组（case_a/b + hardneg×5）端到端 MONITOR PASS 保持；
3. 变异测试：telephone_min_env_cv ∈ {0.15, 0.20, 0.25, 0.30, 0.35} 敏感性扫描，
   确认 0.25 两侧均有缓冲带。

---

## 5. 风险登记与边界

> ⚠️ 本表为原提案（§4）配套登记，前提已被 §7 推翻；保留存档。其中 R2 的
> 「失败方向安全」论证在 §7.3 双向击穿后不再成立（实际结果是召回崩塌 + 穿透并存）。

| # | 风险 | 评估 | 缓解 |
| --- | --- | --- | --- |
| R1 | 小样本：FP 侧仅 N2 一条资产（两段），TP 长段 6 条 | 阈值是「当前语料上的可行解」，非泛化证明 | 阈值标注 TBD by acceptance data（对齐 ADR-0042 惯例）；Layer2/3 真实录音验收时复校 |
| R2 | 过度平稳的真电话（如无 cadence 的老式拨号音持续播放）会被拒 → 锚点漏检 | **失败方向安全**：锚点缺失仅回落 MONITOR ceiling（ADR-0042），不误升级；且 Evidence Matrix 已定调锚点是「必要证据之一而非充分条件」，far_end 等其余证据独立承担 | 接受该 trade-off 并在此显式登记 |
| R3 | 与未来 tremor 重定义（P2）的耦合 | tremor 与 env_cv 同源（包络），但 tremor 是全局峰谷比、env_cv 是二阶统计，语义不同 | P2 实施时一并复核两者相关性，不在本次范围 |
| R4 | 合成素材自证循环（正负样本皆合成） | 已知方法学局限，与 Batch B 报告 TTS 边界声明一致 | Layer2 公开许可真实语音 / Layer3 真实电话场景为最终 Acceptance |

---

## 6. 决策点与拍板记录

### 6.1 Owner 已拍板（2026-08-23，资格审查指令）

1. ✅ **方向批准**：「研究并实施 env_cv 作为第三道 Precision Guard」的方向批准执行
   ——已按流程完成资格审查（§7），结论为否决；
2. ✅ **0.25 不冻结**：暂不批准「0.25 已是生产冻结值」——资格审查后该阈值随提案一并否决，
   冻结议题不再存在；
3. ✅ **流程定调**：proposal → 机制资格审查 → 通过 → 实施最小规则 → Gate V3（precision/
   recall/hard-negative/quiet-phone/regression）→ Layer 2/3 真实录音 → 最终决定生产配置。
   本报告即流程前两环的完整存档；
4. ✅ **表述修正**：§3.1 已按 Owner 审定措辞收紧（实验结论 ≠ 理论不可能性）。

### 6.2 新增待 Owner 决策点（资格审查后）

1. **precision 收尾路径三选一**（Tier0 特征空间内暂无合格第三机制候选）：
   - **选项 A · 接受局限 + 语义口径修正**：接受 N2/警报/音乐类 FP 为 Tier0 已知局限，
     在 Evidence Matrix 中把 Tier0 telephone_persistent 锚点的实际语义登记为
     「环境存在持续窄带平稳音」（persistent narrowband tone），MONITOR ceiling 兜底不变；
     语义判别交 Tier1 YAMNet（ADR-0042 class_map 修复后）承担；
   - **选项 B · Tier1 前置**：把 precision 收尾从序列③中摘出，挂到 ADR-0042 class_map
     修复队列之后，由 Tier1 语义类承担「电话 vs 警报 vs 音乐 vs 家电」判别；
   - **选项 C · 继续 Tier0 特征挖掘**：谐波族结构 / cadence 周期检测等更强时频结构特征
     （边缘 CPU 成本更高、泛化未证，且 §3.1 已证当前候选谱统计无效，风险自担）；
2. **§7.4 新威胁域（警报/音乐）是否纳入 Gate 语料基线**：建议纳入——它们比 N2 更广，
   且全部穿透 P1-a；
3. 关联事项：缺陷 B 三选一（序列④）仍待拍板，与上述决策独立、可并行。

---

## 7. 机制资格审查（2026-08-23 修订追加 · Owner 指令执行）

### 7.1 审查问题（Owner 原话要旨）

> env_cv 是否真的代表「电话的时间结构」，还是只是这批 synthetic TP 比 ambient 更活跃？
> 至少把 TP 7 类形态与 negative 7 类全部跑一遍；尤其要专门找 hard negative：
> **high env_cv + narrow + rate≈0 + long duration**——如果这种负样本大量存在，
> env_cv 就不是充分有效的第三机制。

### 7.2 审查语料（17 条新资产 + 6 条对照）

生成配方（一次性脚本已删，本节即规格；ffmpeg/edge-tts，全部 16k mono PCM16）：

| 组 | ID | 类别 | 配方要点 |
| --- | --- | --- | --- |
| POS | TP-A1~A7 | synthetic NB / real speech phone / speakerphone / handset / tel+bg TV / quiet phone / long playback | telephone_persistent 变体；far_end 窄带化；aecho 房间感；TV 对白混入 |
| NEG | NEG-A1 | ambient | 复用 ambient_living_room |
| NEG | NEG-A2/A3 | HVAC fan 稳态/调制 | brown noise 80~700Hz；A3 加 tremolo(1.2Hz, depth 0.6) |
| NEG | NEG-A4/A5 | fridge hum 稳态/周期启停 | 100/150/200Hz 正弦族；A5 加 7s 周期音量起伏 |
| NEG | NEG-A6 | TV 远场闷化 | edge-tts 新闻对白 + lowpass 500Hz（定向 hard-negative 候选） |
| NEG | NEG-A7 | 警报器 | FM 扫频 700±75Hz @0.5Hz + lowpass 3000 |
| NEG | NEG-A8/A9 | 慢板音乐 窄带/宽带 | 440/494/392Hz 音符序列（1.5s/音）；A9 加八度泛音 |
| NEG | NEG-A10 | normal speech | voice_normal 素材 |
| CTRL | — | N1/N2/N4/P1/P3/hn3 | 既有 Gate 资产 |

### 7.3 结果：双向击穿

段级 env_cv 排序（`kind=telephone_persistent` 且 P1-a 存活 dur≥1.0 的长段）：

| env_cv | 段 | 角色 |
| --- | --- | --- |
| 0.0021 | NEG-A7 警报器 | **NEG（最低）** |
| 0.0040 | NEG-A8 慢板窄带音乐 | NEG |
| 0.0470 | NEG-A9 宽带音乐 | NEG |
| 0.0559 | NEG-A4 冰箱稳态 | NEG |
| **0.0649~0.0738** | **TP-A1/A3/A4/A5/A6/A7 全部 + CTRL P1/P3/hn3** | **TP 全体** |
| 0.1534 | NEG-A2 风扇稳态 | NEG |
| **0.1803~0.1830** | **N2 / NEG-A1 底噪** | **NEG（FP 源）** |
| 0.2210 | NEG-A5 冰箱周期 seg0 | NEG |
| **0.2532** | NEG-A5 冰箱周期 seg1 | **NEG · 击穿 0.25 阈值** |

- **POS KILL 6/7**：若按提案实施 `env_cv≥0.25`，TP-A1/A3/A4/A5/A6/A7 与对照 P1/P3/HN3
  全部被拒 → telephone 召回崩塌（仅 TP-A2 real speech phone 因走 crying 塌缩域不在统计内）；
- **HARD-NEG HIT 1 例**：NEG-A5 seg1（cv=0.2532≥0.25）穿透阈值——Owner 预判的
  hard negative 形态（narrow + rate≈0 + long duration + high env_cv）**存在**；
- **方向反转**：N2 底噪段 cv（0.18）**高于**全部 TP 段（0.067）——与第一轮整段口径
  完全相反。

### 7.4 根因：第一轮分离是 VAD 段化伪影 + 新威胁域

1. **伪影机制**：EnergyVAD（relative_ratio=0.4）按相对中位数切段。telephone_persistent
   素材的 cadence on/off 结构中，静默部分被切掉，VAD 段只剩「平稳响段」→ 段级 env_cv
   极低；而 ambient 全程过阈值形成大段，段内保留自然波动 → 段级 env_cv 反而更高。
   第一轮对整条资产算 env_cv，把 cadence 起伏计入了统计——**该口径在运行时不存在**
   （运行时先切段后提特征）。Owner 的质疑（「代表电话的时间结构，还是只是这批
   synthetic TP 更活跃」）被数据精确证实：两者都不是，是测量口径伪影。
2. **新威胁域（比 N2 更广）**：警报器（0.002）、慢板音乐（0.004）、宽带音乐（0.047）、
   冰箱稳态 hum（0.056）全部命中 telephone 长段且 P1-a 无法拦截——它们与 TP 在
   Tier0 特征空间（narrow + rate≈0）**同簇**。NEG-A6 TV 闷化对白产生 20+ 微段命中，
   全部 dur<1.0 被 P1-a 正确拦截（P1-a 在该语料上有效）。
3. **Tier0 特征空间的结构性边界**：narrow + speech_rate + tremor + am_rate（及任何
   包络/谱统计量）无法区分「人造持续音」大类内部的 电话/警报/音乐/家电hum/底噪。
   该判别需要语义级信息（Tier1 YAMNet 类别）或产品口径修正。

### 7.5 审查结论

**env_cv 不具备第三机制资格，提案撤回。** 0.25 阈值议题随提案一并关闭。
precision 收尾路径转入 §6.2 三选一决策。

---

## 8. 附：证据文件清单

- 第一轮：`_proposal_probe.py` / `_probe_analyze.py` / `_probe_time.py`（已删除）；
  数据 `_probe_result.json` / `_probe_time_result.json`（`dataset/**` 本地留存）；
- 第二轮（资格审查）：`_gen_env_cv_audit.py` / `_probe_env_cv_audit.py` / `_audit_dump.py`
  （已删除）；语料 `dataset/_canonical/audio_mix/telephone_risk/env_cv_audit/{pos,neg}/` 与
  数据 `_audit_result.json`（gitignore 内，本地留存供 Owner 复核）。