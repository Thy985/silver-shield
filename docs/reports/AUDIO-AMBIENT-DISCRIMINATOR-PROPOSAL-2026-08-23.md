# Ambient 第三机制设计提案：包络平稳性判别器（Envelope Stationarity Discriminator）

> 日期：2026-08-23
> 状态：**PROPOSAL — 待 Owner 审批后实施**（本 PR 仅含本文档，零代码变更）
> 任务定位：Precision Gate 执行序列第 ③ 步「ambient 第三机制」的第一阶段（提案）。
> 上游依据：`TELEPHONE-PRECISION-GATE-V2-2026-08-23.md`（唯一剩余 FP = N2_ambient）、
> `AUDIO-EVIDENCE-MATRIX-TELEPHONE-RISK-2026-08-23.md`（telephone_persistent = 场景锚点，非充分条件）。

---

## 0. 结论速览

1. **谱维判别被数据证伪**：对全部 Gate 资产 421 个 VAD 段的帧级 STFT 统计显示，
   N2 底噪段与真实电话阳性段在谱平坦度 / 峰能量占比 / 谱熵 / 过零率上**完全重叠**——
   因为 telephone_persistent 正样本本身是合成平稳音，与合成底噪在「谱形状」上同构。
   任何基于帧级谱统计的第三机制都不可行。
2. **时间维找到可分离特征**：包络变异系数 `env_cv = std(env)/mean(env)`（30ms 帧 RMS）
   在 N2（0.182）与全部 TP 长段（最低 0.312）之间存在干净间隙 `[0.18, 0.31]`。
3. **提案机制**：telephone 分支增加条件 `env_cv >= telephone_min_env_cv`（建议默认 0.25）。
   计算成本 O(n)、零新依赖、复用现有 `_envelope()`。
4. **Gate V3 预测**：precision 88.89% → 100%（N2 两段被拒）；TP 召回零损失（全部 env_cv ≥ 0.31 > 0.25）。
5. **失败方向安全论证**：该判别器的错误方向是「漏掉过度平稳的真电话」→ 锚点缺失仅回到
   MONITOR ceiling 兜底（ADR-0042），不产生风险误升级；而它消除的是污染锚点 precision 的误报。
   与 Evidence Matrix §4 升级路径图一致。

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

**根因（结构性，非调参可解）**：telephone_persistent 正样本素材本身是合成持续音
（AGC 抹平包络、砖墙带限），与合成底噪同为「人造平稳音」，帧级谱统计同构。
结论：**不存在任何基于谱形状的 Tier0 判别器能分离二者**——这同时证伪了
V1 报告 §6 中「谱平坦度/窄带纯度」的初始候选方向，避免了一次注定无效的实现。

### 3.2 正结果：时间维 env_cv 干净分离（第二轮）

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

### 3.3 物理解释

机械/电气底噪是无限平稳过程（包络恒定、频谱成分恒定）；通信音频即使经 AGC 平滑，
仍保有协议性或内容性起伏（铃音 cadence、通话轮替、线路噪声调制）→ 包络变异系数
系统性更低。这与 ADR-0026 设计文档「电话 AGC 抹平包络致音节率≈0」的既有观察互补：
speech_rate 看「有没有峰」，env_cv 看「整体有多平」。

---

## 4. 提案设计

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

| # | 风险 | 评估 | 缓解 |
| --- | --- | --- | --- |
| R1 | 小样本：FP 侧仅 N2 一条资产（两段），TP 长段 6 条 | 阈值是「当前语料上的可行解」，非泛化证明 | 阈值标注 TBD by acceptance data（对齐 ADR-0042 惯例）；Layer2/3 真实录音验收时复校 |
| R2 | 过度平稳的真电话（如无 cadence 的老式拨号音持续播放）会被拒 → 锚点漏检 | **失败方向安全**：锚点缺失仅回落 MONITOR ceiling（ADR-0042），不误升级；且 Evidence Matrix 已定调锚点是「必要证据之一而非充分条件」，far_end 等其余证据独立承担 | 接受该 trade-off 并在此显式登记 |
| R3 | 与未来 tremor 重定义（P2）的耦合 | tremor 与 env_cv 同源（包络），但 tremor 是全局峰谷比、env_cv 是二阶统计，语义不同 | P2 实施时一并复核两者相关性，不在本次范围 |
| R4 | 合成素材自证循环（正负样本皆合成） | 已知方法学局限，与 Batch B 报告 TTS 边界声明一致 | Layer2 公开许可真实语音 / Layer3 真实电话场景为最终 Acceptance |

---

## 6. 待 Owner 决策点

1. **是否批准本机制进入实施**（批准后另起 `fix/tel-env-cv-discriminator` 分支，含测试 + Gate V3 报告）；
2. **阈值默认值确认**：建议 0.25（TBD by acceptance data 标注保留）；
3. **R2 trade-off 显式接受与否**（漏掉过度平稳真电话 vs 底噪不再污染锚点）；
4. 关联事项提醒：缺陷 B 三选一（序列④）仍待拍板，与本提案相互独立、可并行推进。

---

## 7. 附：本轮证据文件清单

- `_proposal_probe.py` / `_probe_analyze.py` / `_probe_time.py`：一次性探针（已删除，不入库）；
- `_probe_result.json` / `_probe_time_result.json`：原始段级/资产级数据（`dataset/**` gitignore 内，
  本地留存供 Owner 复核）。