# Gate H · Data Calibration 报告（2026-08-23）

> **性质**：只做真实数据分析，不改产品策略。本报告不含任何 `src/` / 配置变更；
> 所有结论供 Owner 决断 ADR-0041（Temporal Window）与 ADR-0042（N/T/M 参数）回填。
>
> **采集方式**：真实推理链全量采集——音频侧 energy VAD + Tier0 特征规则 +
> Tier1 YAMNet ONNX（`yamnet_runtime.onnx`）；视觉侧 YOLO11n CPU（imgsz 416，
> ByteTrack）+ realtime evaluator 生产阈值。非标注真值统计。
>
> **分析态边界**（仅采集脚本内存中设置，产品配置零改动）：evaluator 以
> `ceiling_monitor_only=False` + `raise_min_count=1` 运行以获取完整判级分布与
> RAISED 时间线；生产默认 MONITOR ceiling 不受影响。

## 1. 数据资产与口径

| 项 | 值 |
| --- | --- |
| 音频资产池 | 31 个 wav：`dataset/_canonical/audio{,_mix}/**` + `*_with_audio.mp4` 内嵌音轨抽取 + golden case_b_mix |
| 视觉×音频配对 | 10 组（telephone_risk ×3 / stranger_visit / repeated_visit ×3 / evidence_insufficient ×3） |
| 有效 ΔT 贡献配对 | 5 组（其余组音频侧零声学事件——脚步/门铃/环境音不构成 Tier0 感知事件，属数据本性非缺陷） |
| 已知解码修复 | manifest 声明的 32-bit float WAV（format 3）不被标准 wave 解码器支持 → ffmpeg 转 PCM16 后入池（内容无损，仅容器格式转换） |

## 2. 五大分布

### 2.1 AudioKind distribution（n=102）

| Kind | 计数 | 占比 | 主要来源 |
| --- | --- | --- | --- |
| `audio_distress_cry` | 89 | 87.3% | voice_stressed 全相位 + case_mix 压力段（Tier0 特征规则直出） |
| `audio_telephone_persistent` | 11 | 10.8% | telephone_persistent.wav + case mixes（窄带+持续语音） |
| `audio_speech_rapid` | 2 | 2.0% | 高语速段 |

### 2.2 Score distribution（[0,1]）

| 口径 | n | min | P50 | P75 | P90 | P95 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| overall | 102 | 0.511 | 0.843 | 0.960 | 0.985 | 1.000 | 1.000 |
| `distress_cry` | 89 | 0.527 | 0.843 | 0.955 | 0.980 | 1.000 | 1.000 |
| `telephone_persistent` | 11 | 0.559 | 0.949 | 0.985 | 0.994 | 0.996 | 0.996 |
| `speech_rapid` | 2 | 0.511 | — | — | — | — | 0.511 |

### 2.3 Confidence distribution（⚠️ 双源异质，见 §5 局限）

| 口径 | n | min | P50 | P75 | P90 | P95 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| overall | 102 | 0.600 | 0.600 | 0.600 | 0.815 | 0.892 | 0.902 |
| `distress_cry` | 89 | 0.600 | **0.600（恒定）** | | | | 0.600 |
| `telephone_persistent` | 11 | 0.746 | 0.882 | 0.895 | 0.898 | 0.899 | 0.899 |
| `speech_rapid` | 2 | 0.902 | — | — | — | — | 0.902 |

> **关键发现**：`distress_cry` 的 confidence 恒等于 0.6——它是 Tier0 特征规则的
> 工程常数输出，不是统计量。confidence 维度的阈值校准只对
> `telephone_persistent`（YAMNet/Tier1 参与的路径）有意义。

### 2.4 Vision↔Audio Δt distribution（RAISED 信号级，|Δt| 秒）

双口径：

| 口径 | n | min | P50 | P75 | P90 | P95 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Native**（manifest 对齐原位） | 7 | 0.00 | 1.26 | 2.40 | 3.47 | 8.00 | 8.00 |
| **Phase-prior**（±{0.5,1,2}s 相位先验扩样） | 49 | 0.00 | 2.00 | 3.40 | 7.00 | 8.50 | 10.00 |

窗口覆盖率（audio RAISED 在窗内能找到 vision RAISED 的比例）：

| 窗口 | Native 覆盖率 | Phase-prior 覆盖率（offset=0） |
| --- | --- | --- |
| w = 0.5s | 16.7% | 16.7% |
| w = 1.0s | 16.7% | 16.7% |
| w = 2.0s | **58.3%** | 58.3% |
| w > 2.0s | （超出 ADR-0041 候选空间） | phase-prior 全池 P50=2.0s → 上限档仅覆盖约半数相位 |

原始时间线（native）：vision RAISED 集中在素材开头（人物入场即时 RAISE，
t≈0–2.4s）；audio RAISED 主事件 `distress_cry` 在 case 系列的 t≈1.26s
（voice_stressed 相位起点），case_a 另有 t=8.0s 离群事件。

### 2.5 Session duration distribution（音频资产，n=31）

| min | P50 | P75 | P90 | P95 | max |
| --- | --- | --- | --- | --- | --- |
| 6.0s | 15.0s | 15.0s | 15.0s | 15.0s | 33.5s |

主体为 15s 标准片段（telephone_risk 系列）；evidence_insufficient mix 为 6–7s，
stranger_visit 最长 33.5s。

### 2.6 附加：同 kind 事件到达间隔（ADR-0042 T 维度直接依据）

`distress_cry`（n=78）：min 0.68 / **P50 0.92** / P75 1.52 / **P90 2.36** /
**P95 2.90** / max 3.92（秒）

## 3. ADR-0041 窗口选档数据映射（决策留 Owner）

ADR-0041 候选档位：same frame / ≤0.5s / ≤1.0s / ≤2.0s。数据映射：

| 候选档 | native 覆盖 | 说明 |
| --- | --- | --- |
| same frame | 14%（1/7，act_a 同刻共现） | 仅当音画真同步录制时可达 |
| ≤0.5s | 17% | — |
| ≤1.0s | 17% | — |
| **≤2.0s（上限档）** | **58%** | 罩住 Δt≈1.26s 的 distress_cry 主事件簇 |

三条可选路线及代价（**本报告不替 Owner 选择**）：

1. **维持候选上限 2.0s**：保守，只关联紧密对；58% 覆盖意味着约四成真实
   共现事件在窗口外静默错过（漏关联 ≠ 误报，符合「宁缺勿滥」方向）。
2. **扩充候选档位（如 ≤5s / ≤8s）**：需修订 ADR-0041（Owner 专属）；
   phase-prior 口径 P50=2.0s / P75=3.4s 表明扩到 5s 可覆盖约 70%+。
   代价：弱关联对的幻觉合并风险上升，且 NEAR_WINDOW 线性衰减在长窗下
   区分度下降。
3. **判定 demo 数据不具代表性，窗口继续悬空**：本数据集为合成/TTS 混音
   素材，音画相位由制作决定而非物理共时；真实家庭部署的事件相位差可能
   显著不同。窗口 None 期间 NEAR_WINDOW 结构性不可用（SAME_FRAME 不受影响），
   Gate G 已验证该降级路径。

## 4. ADR-0042 N/T/M 参数依据（数据→建议区间，决策留 Owner）

| 参数 | 数据依据 | 可支撑的建议区间 |
| --- | --- | --- |
| **N**（raise_min_count，持续性维度） | distress_cry 到达间隔 P50=0.92s：T=5s 窗口内典型可见 3–5 个同 kind 事件 | N ∈ {2, 3}；N=1 已被 Gate G 验收态使用但无抗噪持续性 |
| **T**（raise_window_s，同类窗口） | 到达间隔 P95=2.90s / max=3.92s → T<3s 会拆散 P95 双事件 | **T ≈ 4–5s**（罩住 P95+ 并留余量）；T=15s（整段）则退化为全会话计数 |
| **M**（notify_min_kinds，多样性维度） | 当前数据仅 3 种 kind 且 speech_rapid n=2 | **数据不足，无法校准**——建议保持 None（结构性不可达）直至多 kind 并发样本积累 |
| monitor_score_threshold | score 分布高度饱和（P50=0.84，max=1.0）；底部 10% ≈ <0.53 | 0.60 为温和起点（滤除明显弱段）；≥0.85 将砍掉一半以上事件需谨慎 |
| monitor_confidence_threshold | confidence 双源异质（§2.3）：distress_cry 恒 0.6 是工程常数 | **对该维度设阈值会整体误伤特征规则直出的 kind**；若启用应仅约束 Tier1 路径（≥0.75） |

## 5. 局限性声明（防误读）

1. **样本量**：Δt native 口径仅 n=7（有效声学配对 5 组）；分位数用于量级判断，
   不具备精确分布推断力。
2. **数据代表性**：golden/dataset 为受控制作的演示素材（TTS + DSP 混音），
   音画相位由制作脚本决定；「真实家庭」部署的 Δt 分布可能系统性不同——
   这正是 §3 路线 3 存在的理由。
3. **视觉 RAISED 结构单一**：每素材单次播放仅触发 1–2 次（入场即时 visit 类），
   无 dwell 渐进/重复来访等多样形态。
4. **confidence 恒定值**：distress_cry 的 0.6 为特征规则工程常数（§2.3），
   任何把 0.6 当作「模型置信度分布」使用的读法都是错的。
5. **float WAV 转码**：10 个资产经 ffmpeg PCM16 容器转换（采样值无损），
   解码路径与生产 16k PCM 一致性已由 _16k 版本交叉验证（同资产事件产出一致）。
6. **相位偏移先验**（±0.5–2s）是分析扩样手段而非观测事实，仅用于 §3 路线 2
   的敏感性参考；native 口径才是 manifest 真值对齐下的保守数字。

## 6. 交付物与复现

- 采集脚本：一次性工具（未入库）；核心逻辑 = `AudioPipeline.from_audio_config`
  + `RealTimeAudioRiskEvaluator.observe(case_time=event.timestamp)` +
  `PerceptionPipeline.process_frame`（YOLO CPU @4fps）+ 相位偏移扫描
- 原始统计 JSON：本地 `_gate_h_data.json`（含逐事件 kind/score/confidence/
  timestamp、逐配对时间线与偏移表；按仓库纪律不入库，Owner 需查可现场重跑）