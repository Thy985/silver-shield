# ADR-0037: Generator v2 时序语义重设计 — Normal/Stress 分布分离 + Real Acoustic Calibration

- 状态：Proposed
- 日期：2026-08-19
- 决策者：Owner
- 相关：ADR-0026（音频感知链路）、P2.1 Feature Pilot Report（`reports/feature_pilot_results/comparison_report.md`）、P2.2-1 Real Acoustic Reference Profile（`reports/feature_pilot_results/real_acoustic_reference.md`）、Generator v1（`src/home_perception/data/generators/telephone_risk.py`）

---

## 背景（Context）

SilverShield Home 感知模块的音频 stress classifier 在 Synthetic → Real transfer 上存在严重 domain gap。P2.1 Feature Redesign Pilot 与 P2.2-1 Real Acoustic Reference Profile 两轮实验已将根因定位到**数据生成语义层**，特征工程层面无法解决。

### P2.1 Pilot 证据：特征工程无法解决 domain gap

P2.1 在同一份合成数据上对照了 4 种特征提取模式（A/B/C/D），结果如下（详见 `reports/feature_pilot_results/comparison_report.md`）：

| Model | Feature           | IID FPR | OOD FPR | Real FPR | Gain Corr |
|-------|-------------------|---------|---------|----------|-----------|
| A     | absolute_rms      | 0.846   | 0.920   | 1.000    | 0.550     |
| B     | delta_rms         | 1.000   | 1.000   | 1.000    | 0.500     |
| C     | multi_feature     | 1.000   | 1.000   | 1.000    | 0.500     |
| D     | normalized_rms    | 1.000   | 1.000   | 1.000    | 0.500     |

- 4 个模型 Real FPR 全部 = 1.0，未通过 < 30% 条件；
- B/C/D 的阈值卡在搜索范围下界（B=-10 dB、C=0.1、D=0.5），说明**几乎所有样本（含 normal）的特征值都 > 阈值**；
- 替代特征（B/C/D）不仅没有改善 Real transfer，反而把 IID FPR 从 0.846 拉到 1.0。

### 三个根因

**根因 1：合成 normal/stress RMS 分布重叠**

合成 IID 数据中 normal RMS `[0.0040, 0.0192]` 与 stress RMS `[0.0054, 0.0318]` 在 `[0.0054, 0.0192]` 区间严重重叠。OOD 数据同样如此（normal `[0.0015, 0.0139]`，stress `[0.0090, 0.0297]`）。任何能量阈值都无法区分。

**根因 2：合成 normal 样本设计缺陷**

Generator v1 的 normal 样本语义是"安静 baseline → 正常说话"（`telephone_risk.py` §`_render_audio`：normal 阶段 `0.025 * sin(2π·140·t)` + 低噪声，stressed 阶段 `0.10 * sin(2π·200·t)` + `base_stressed * 2.0` + 噪声）。这个设计**本身就有能量变化**——从安静到说话的 ΔRMS 自然为正，与 stress 的"说话 → 大声说话"在 ΔRMS 上无法区分（两者都是相对安静 baseline 的正能量增加）。这就是 B/C/D 阈值卡在下界的直接原因。

**根因 3：真实/合成 scale mismatch**

真实 normal RMS = 0.0635，合成 normal RMS = 0.0040–0.0318；真实 stress RMS = 0.0493，合成 stress RMS = 0.0054–0.0318。真实数据 RMS 上界约为合成的 2.0x。在合成数据上学习的阈值无法迁移到真实数据。

### P2.2-1 真实音频分析关键发现

P2.2-1 对 4 个真实音频（1 normal + 2 stress + 1 unlabeled，48kHz / 15s）做了声学特征分析（详见 `reports/feature_pilot_results/real_acoustic_reference.md`）：

| 文件 | 标签 | 全局RMS | ΔRMS(dB) | ZCR | 频谱重心(Hz) | 窗口RMS变异系数 |
|------|------|---------|----------|-----|-------------|----------------|
| `audio_voice_normal.wav` | normal | 0.0635 | -2.42 | 5051 | 1479.0 | 0.798 |
| `audio_distress_cry.wav` | stress | 0.0493 | +2.03 | 32160 | 2243.4 | 0.523 |
| `audio_voice_raised.wav` | stress | 0.0493 | +2.03 | 32160 | 2243.4 | 0.523 |

三个关键发现：

1. **ΔRMS 能区分**：normal ΔRMS = -2.42 dB（负值，能量下降），stress ΔRMS = +2.03 dB（正值，能量上升）。两者方向相反，有明确语义。
2. **ZCR 和频谱重心也能区分**：stress ZCR = 32160 vs normal ZCR = 5051（6.4x）；stress 频谱重心 = 2243 Hz vs normal = 1479 Hz（1.5x）。stress 的高频成分显著增加。
3. **绝对 RMS 反而误导**：真实 normal RMS = 0.0635 > stress RMS = 0.0493。这与合成数据上的"stress RMS > normal RMS"假设完全相反。哭声/喊声的能量分布与正常说话不同，全局 RMS 不是区分特征。

**真实 normal 的能量波动特征**：`audio_voice_normal.wav` 的分窗口 RMS 变异系数 = 0.798，ΔRMS = -2.42 dB。这表明真实正常通话本身就有能量起伏（自然说话的音量波动），但起伏是**零均值的随机波动**，不是单向转变。

### 触发本次决策的场景

P2.1 Pilot 已证明特征工程层面无效，根因在数据生成语义层。P2.2-1 已建立真实音频参考分布，提供了校准目标。现在需要在数据生成层面重新定义 NORMAL/STRESS 的 temporal semantics，使合成数据在 ΔRMS + ZCR + 频谱重心空间可分，且 scale 匹配真实数据。

---

## 决策（Decision）

总决策：**重新定义 Generator v2 的 NORMAL/STRESS 时序语义，从"全局能量阈值二分类"转向"temporal state transition detection"，并基于 P2.2-1 真实音频特征校准生成器参数。**

### 决策 1：重新定义 NORMAL 样本语义

- **旧定义（v1）**：normal = "安静 baseline → 正常说话"。从静音到正常音量有能量跃升，ΔRMS 为正。
- **新定义（v2）**：normal = "完整的正常语音过程"，含 **hard variability**（自然波动：音量 / 音调 / 语速 / 停顿 / 笑 / 咳嗽），但**没有目标性的异常状态变化**。
- **关键区别**：normal 不是"没有变化"，而是"没有目标性的异常状态变化"。自然波动是允许的甚至是必须的，但不存在"baseline → stressed"的定向转变。
- **时序特征**：normal 样本的 ΔRMS 应分布在 0 附近（-3 dB 到 +3 dB），不存在单向能量跃升。

### 决策 2：重新定义 STRESS 样本语义

- **旧定义（v1）**：stress = "安静 baseline → 提高音量/音高的说话"。与 normal 的"安静 → 说话"在 ΔRMS 上同向。
- **新定义（v2）**：stress = **temporal state transition** — "正常 baseline speech → 状态变化 → stressed-like speech"。
- **时序阶段**：`[NORMAL phase] → [TRANSITION window] → [STRESSED-LIKE phase]`。
- **关键**：stress 的定义不是"高能量"，而是"从正常状态到异常状态的**转变过程**"。全局能量高低不是判据，**转变的发生**才是判据。
- **时序特征**：stress 样本的 ΔRMS 应 > +3 dB（单向能量跃升），且有明确的 transition onset 时间点。

### 决策 3：任务从 `stress_classifier` 改为 `Acoustic State Transition Detection`

- **旧任务**：二分类 normal vs stress（基于全局特征阈值）。
- **新任务**：时序状态检测，输出状态序列 `NORMAL → ATTENTION → ELEVATED → STRESSED-LIKE`。
- 这不是二分类问题，而是 **temporal state sequence** 问题。
- 模型需要检测的是"状态转变的发生"，而非"当前是否处于 stress"。

### 决策 4：标签拆分 `generator_state` vs `target_event`

分离"生成器知道什么"和"声学上发生了什么"：

- **`generator_state`**：可控变量，记录生成器使用的参数（`stress_onset`、`energy_delta_db`、`speech_rate_factor`、`transition_duration`、`background_snr_db`、`room_rt60`、`f0_baseline`、`f0_stress` 等）。这是生成器的内部状态，用于复现和参数扫描。
- **`target_event`**：声学事件标签，核心字段 `acoustic_transition: true | false`。
  - 当 `acoustic_transition = true` 时，附带 `transition_onset_s`、`transition_duration_s`、`pre_state`、`post_state`。
  - 当 `acoustic_transition = false` 时（normal 样本），这些字段省略或为 null。

### 决策 5：时序 ground truth 格式

- **旧格式（v1）**：
  ```json
  {
    "labels": {"stress_like": true, "acoustic_change": true},
    "features": {"change_onset": 8.0}
  }
  ```
- **新格式（v2）**：时序状态序列。
  - **stress 样本**：
    ```json
    {
      "temporal_ground_truth": [
        {"start_s": 0.0, "end_s": 7.5, "state": "NORMAL", "evidence": "baseline_speech"},
        {"start_s": 7.5, "end_s": 8.5, "state": "TRANSITION", "evidence": "energy_rise_onset"},
        {"start_s": 8.5, "end_s": 15.0, "state": "STRESSED_LIKE", "evidence": "elevated_pitch_and_energy"}
      ],
      "acoustic_transition": true,
      "transition_onset_s": 7.5,
      "transition_duration_s": 1.0,
      "pre_state": "NORMAL",
      "post_state": "STRESSED_LIKE"
    }
    ```
  - **normal 样本**：
    ```json
    {
      "temporal_ground_truth": [
        {"start_s": 0.0, "end_s": 15.0, "state": "NORMAL", "evidence": "continuous_speech_with_natural_variation"}
      ],
      "acoustic_transition": false
    }
    ```

### 决策 6：基于 P2.2-1 真实音频特征校准生成器参数

- **能量 scale 校准**：合成数据 RMS 整体放大约 2.0x，使合成 RMS 范围匹配真实数据（真实 normal RMS ≈ 0.06，真实 stress RMS ≈ 0.05）。可在 Generator 的能量参数中乘以 scale factor，或在后处理阶段统一增益。
- **ΔRMS 分离**：
  - normal 样本 ΔRMS ∈ [-3, +3] dB（围绕 0 波动，零均值随机起伏）。
  - stress 样本 ΔRMS > +3 dB（单向跃升，对应真实 stress ΔRMS = +2.03 dB 的方向，并留出分离裕度）。
- **ZCR 区分**：
  - normal 样本 ZCR ≈ 5000（低频语音为主，对应真实 normal ZCR = 5051）。
  - stress 样本 ZCR > 20000（高频成分增加，对应真实 stress ZCR = 32160）。
- **频谱重心区分**：
  - normal ≈ 1500 Hz（对应真实 normal = 1479 Hz）。
  - stress > 2000 Hz（对应真实 stress = 2243 Hz）。
- **注意**：绝对 RMS 不是区分特征（真实 normal RMS > stress RMS），ΔRMS + ZCR + 频谱重心才是区分特征。生成器不得把"提高全局 RMS"作为 stress 的唯一标记。

### 决策 7：Normal 样本的 hard variability 实现

normal 样本必须包含以下自然波动（随机组合，不是全部都加）：

- **音量自然起伏**：±3 dB 内的随机波动（不是单向变化）。
- **音调自然变化**：F0 ±20 Hz 随机抖动。
- **语速变化**：0.8x 到 1.2x 随机变化。
- **停顿**：随机位置的短停顿（0.3–0.8s）。
- **轻微笑声/咳嗽**：低概率（10%）出现的短事件。

**关键约束**：这些波动是**零均值**的——不会有"从头到尾音量上升"这种单向趋势。这确保 normal 样本有真实感（不是合成感），但 ΔRMS 仍在 0 附近。真实 normal 样本的分窗口 RMS 变异系数 = 0.798，说明自然波动幅度不小，但方向是随机的而非定向的。

---

## 动机（Rationale）

### 为什么不能再在特征工程层面尝试

P2.1 实验数据已经证明：4 个模型（A/B/C/D）在相同合成数据上 Real FPR 全部 = 1.0，替代特征（B/C/D）反而把 IID FPR 从 0.846 拉到 1.0。B/C/D 的阈值卡在搜索范围下界，说明合成 normal 样本的特征值也普遍 > 阈值。这不是特征选错了，是**合成数据本身 normal/stress 不可分**。

### 为什么旧 normal 定义是错的

旧 normal 定义"安静 baseline → 正常说话"的根本错误：从安静到说话的能量变化（ΔRMS > 0）与 stress 的"说话 → 大声说话"（ΔRMS > 0）在 ΔRMS 上**同向**。无论阈值设在哪里，要么两者都判 stress，要么两者都判 normal。P2.1 Model B（ΔRMS）阈值卡在 -10 dB 下界就是直接证据。

### 为什么新 normal 定义是对的

新 normal 定义的核心洞察来自 P2.2-1：真实正常通话本身有能量起伏（变异系数 0.798），但起伏是**零均值的随机波动**，不是单向转变。真实 normal ΔRMS = -2.42 dB（负值），真实 stress ΔRMS = +2.03 dB（正值），**两者方向相反**。新定义让 normal 的 ΔRMS 围绕 0 波动（零均值随机起伏），stress 的 ΔRMS 单向跃升（> +3 dB），两者在 ΔRMS 空间可分。

### 为什么转向时序状态检测

真实 stress 的判据不是"当前帧能量高"，而是"发生了从正常到异常的转变"。真实 normal 的全局 RMS（0.0635）甚至高于 stress（0.0493），说明"当前是否处于高能量状态"不是正确的判据。时序状态检测更符合实际任务：真正需要检测的是"状态转变的发生"，而非"当前帧是否 stress"。这也能利用 transition onset 时间点做更丰富的模型训练（序列模型 / transition detection）。

### 为什么需要 generator_state vs target_event 拆分

旧格式把"生成器参数"和"声学事件标签"混在 `labels` 里（如 `stress_like`、`acoustic_change`、`telephone_persistent`），导致标签语义混乱。拆分后：
- `generator_state` 服务于复现和参数扫描（生成器内部状态）。
- `target_event` 服务于模型训练（声学事件标签）。
- 两者解耦后，可以用同一组 `target_event` 标签对应不同的 `generator_state`（不同参数生成的同一声学事件），便于做参数鲁棒性测试。

### 为什么基于 P2.2-1 校准

P2.2-1 提供了真实音频的参考分布：
- ΔRMS：normal -2.42 dB vs stress +2.03 dB（方向相反）。
- ZCR：normal 5051 vs stress 32160（6.4x）。
- 频谱重心：normal 1479 Hz vs stress 2243 Hz（1.5x）。
- scale：真实 RMS 上界约合成 2.0x。

这些是当前仅有的真实参考点。校准目标是让合成数据的这些特征分布匹配真实数据，使在合成数据上学习的阈值可迁移到真实数据。

### 为什么允许 breaking change

时序 ground truth 格式与旧格式不兼容（从 `labels`/`features` 二维结构改为 `temporal_ground_truth` 时序序列）。但当前数据集已冻结（P2.1 Pilot 已完成，模型确认不进入 Live Runtime），旧数据集可保留用于回溯对比，新数据集用新格式。breaking change 可接受。

---

## 后果（Consequences）

### 正面

- 合成 normal/stress 分布在 ΔRMS + ZCR + 频谱重心空间可分（normal ΔRMS ∈ [-3, +3] dB vs stress ΔRMS > +3 dB；normal ZCR ≈ 5000 vs stress ZCR > 20000；normal 频谱重心 ≈ 1500 Hz vs stress > 2000 Hz）。
- 合成数据 scale 匹配真实数据（RMS 整体放大 2.0x），阈值可迁移。
- 时序 ground truth 支持更丰富的模型训练（序列模型 / transition detection / onset localization）。
- normal 样本有 hard variability（零均值自然波动），减少过拟合风险，更接近真实正常通话的变异系数 0.798。
- `generator_state` vs `target_event` 拆分使标签语义清晰，便于参数鲁棒性测试。

### 负面

- 需要重写生成器（Task 43：`src/home_perception/data/generators/telephone_risk_v2.py`）。
- 需要更新 Data CI（Task 44：`scripts/data_ci_evaluator.py` 加 `temporal_label_consistency` 检查）。
- 需要重新训练模型（Task 45/46：生成 100 个 pilot 样本 + Synthetic → Real Transfer 评估）。
- 时序 ground truth 格式与旧格式不兼容（breaking change，但当前数据集已冻结，可接受）。
- 真实音频样本仍不足（仅 3 个有标签），校准精度有限。

### 技术债

- 真实音频校准数据不足：当前仅 3 个有标签真实样本（1 normal + 2 stress），无法建立统计可靠参考分布。需要 ≥ 40 个真实样本（20 normal + 20 stress）才能建立统计可靠的真实参考分布。P2.2-1 §6 已记录此需求。
- hard variability 的参数范围需要后续实验微调：±3 dB 音量起伏、F0 ±20 Hz 抖动、0.8x–1.2x 语速变化等参数是基于真实 normal 变异系数 0.798 的初步估计，可能需要根据生成样本的实际特征分布调整。
- ZCR 和频谱重心的合成控制精度待验证：合成信号的高频成分主要来自噪声和音高，能否精确匹配真实 stress 的 ZCR = 32160 和频谱重心 = 2243 Hz 需要在 Task 43 实现后用 P2.2-1 同款分析脚本验证。

---

## 替代方案（Alternatives）

### 方案 1：继续在特征工程层面尝试

P2.1 已证明无效：4 个模型 Real FPR 全部 = 1.0，替代特征（B/C/D）反而更差。根因是合成数据 normal/stress 分布重叠，特征工程无法解决。**否决**。

### 方案 2：收集更多真实数据后直接训练

真实数据量级不够（仅 3 个有标签样本），且即使收集到 40 个真实样本，合成数据本身仍有 normal/stress 分布重叠问题，合成数据仍需重设计。**否决**。

### 方案 3：域适应（Domain Adaptation）

在合成和真实之间做域对齐（如 DANN / CORAL / MMD）。但根因是合成数据本身 normal/stress 不可分，域适应只能对齐分布形状，无法让原本重叠的 normal/stress 在对齐后变得可分。**否决**。

### 方案 4：放弃合成数据，只用真实数据

真实数据量级不够（仅 3 个有标签样本），无法训练任何模型。即使收集到 40 个真实样本，也仅够小模型评估，不足以训练泛化模型。合成数据仍是主训练源。**否决**。

### 方案 5：保持旧 normal 定义但增大 stress 能量差

即 normal 仍是"安静 → 说话"，但把 stress 的能量差从 +6 dB 拉到 +12 dB 或更高。问题：
- 不解决 scale mismatch（真实/合成 RMS 仍差 2.0x）。
- 不解决 ΔRMS 语义问题（normal 的 ΔRMS 仍为正，与 stress 同向）。
- 真实 stress 的 ΔRMS 只有 +2.03 dB，合成 stress 拉到 +12 dB 会偏离真实分布，阈值仍无法迁移。
**否决**。

---

## 后续动作

| Task | 内容 | 依赖 |
|------|------|------|
| 43 | 实现 `TelephoneRiskGenerator v2`（`src/home_perception/data/generators/telephone_risk_v2.py`）：normal 含 hard variability（零均值自然波动），stress 为 temporal state transition，输出时序 ground truth | 本 ADR |
| 44 | Data CI 加 `temporal_label_consistency` 检查（`scripts/data_ci_evaluator.py`）：验证 measured acoustic change 只在指定 transition window 显著 | 43 |
| 45 | 生成 100 个 pilot 样本（Generator v2）+ 运行 Data CI | 43, 44 |
| 46 | Synthetic → Real Transfer 评估：用 Generator v2 数据训练模型，在真实音频上评估 Real FPR/Recall，判断是否通过条件（Real FPR < 30%, Real Recall ≥ 0.90） | 45 |