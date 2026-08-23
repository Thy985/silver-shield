# Tier1 Semantic Qualification Gate 规格 + 8 类语义 Dataset v1（含 YAMNet Baseline 实测）

> 日期：2026-08-23
> 状态：**SPEC + BASELINE — 待 Owner 审批**（本 PR 仅含文档，零代码变更）
> 任务定位：Owner 定调的方向切换——停止 Tier0 特征挖掘，转建
> 「telephone_persistent 的 Tier1 semantic precision dataset」并形成新 Gate；
> Precision Gate 重定义为两级架构。
> 上游依据：`AUDIO-AMBIENT-DISCRIMINATOR-PROPOSAL-2026-08-23.md` §6.2（三选一决策，
> 本报告即其执行：选项 C 出局，走 Tier1 路线）、ADR-0042、Gate I 报告（class_map 缺陷）。

---

## 0. 结论速览

1. **两级 Gate 架构定版**（Owner §7 形式化，见 §2）：Tier0 降级为候选生成器
   （`persistent_narrowband_candidate`），语义确认上移 Tier1，最终经 Hard Negative
   Precision Gate 才进 Risk Policy。原「Tier0 telephone_persistent → Precision Gate」单级
   设计废止。
2. **8 类语义 dataset v1 已建成**（36 条资产 + manifest，§3）：telephone / alarm / music /
   appliance / ambient / normal_speech / far_end_speech / micro_event；本轮实证击穿的
   全部负样本固化为 hard-negative regression set。
3. **YAMNet baseline 实测（§5）双向失败，且根因指向数据而非模型**：
   - telephone recall = **0%**（10 条合成"电话音"全部被判 white_noise/waterfall）——
     合成正样本在 AudioSet 语义空间中**不是电话**；
   - music → telephone FP = **100%**（合成纯音音乐与电话忙音/拨号音在 AudioSet 中本就同类，
     busy_signal score 高达 0.97）；
   - normal_speech 检测 **7/7 全对**（speech 0.74~0.99），模型对人声完全可靠。
4. **Qualification Gate 在纯 Layer1 合成语料上不可判定**：正样本生态效度不足。
   telephone 类语料必须引入 Layer2 真实录音（真实铃声/忙音/拨号音/通话录音）换血后，
   Gate 才具备判定力（§6）。
5. **对 Owner 前置判断的实证回应**：「还不能马上说 YAMNet 能解决」成立且更强——
   当前语料下 YAMNet 无法解决，但失败模式证明模型本身工作正常（speech 类完美、
   纯音类如实归类），瓶颈在 ground truth 音频的真实性。
6. MONITOR ceiling 维持不变：qualification 通过前不解除（与 ADR-0042 硬门控 #1 对齐）。

---

## 1. 背景

### 1.1 决策链回放

| 节点 | 结论 |
| --- | --- |
| env_cv 提案（#295/#296） | 资格审查否决：Tier0 特征空间内「人造持续音」大类同簇 |
| §6.2 三选一 | A 接受局限 / B Tier1 前置 / C 继续 Tier0 挖掘 |
| **Owner 定调（本报告起点）** | 不再找 Tier0 特征；建立 8 类语义 dataset + Tier1 Semantic Qualification Gate；Precision Gate 重定义为两级架构 |

### 1.2 为什么验收问题被重构得更干净

旧问题：「telephone 规则还能不能命中？」——它在 Tier0 特征空间内无法回答
（#296 已证 narrow+rate≈0 无法区分电话/警报/音乐/家电）。

新问题：「**Tier0 产生候选后，Tier1 能否把 telephone 与结构相似的非电话声音正确分开？**」
——这是语义层可判定问题，有明确的 dataset、映射表和指标。

### 1.3 前置约束（Owner 明示，本报告遵守）

不能直接假设「YAMNet 能解决」：Gate I 已暴露 class_map 缺失 → semantic label 不可信；
#296 又实证 Tier0 semantic collapse。因此 qualification 必须以数据验证，且通过前
MONITOR ceiling 不解除。

## 1.4 代码现状盘点（侦察结论）

class_map 加载链路已在 ADR-0042 步骤 6 修复并有回归锁（`tests/test_audio_class_map.py`：
fail-fast 契约 + `build_tagger` 端到端接线）。模型资产在位：

```
data/models/yamnet/yamnet_class_map.csv        (官方 521 类 CSV)
data/models/yamnet/onnx/yamnet_runtime.onnx    (动态 rank-1 输入导出, 16MB)
data/models/yamnet/onnx/yamnet.onnx            (固定 [1] 退化导出, 已被 _validate_input_shape 拒绝)
```

`YAMNET_SEMANTIC_MAP`（tagging.py:56）已覆盖 telephone/telephone_ring/ringtone/alarm/
siren/music/crying/speech 等关键场景类——qualification 所需映射表无需新增代码即可开跑。

---

## 2. 两级 Precision Gate 架构（定版）

```
┌─────────────────────────────────────────────────────────────────┐
│ Tier0 Candidate Gate                                            │
│   EnergyVAD + Prosody 规则 (narrow + rate≈0 [+ min duration])    │
│   输出：persistent_narrowband_candidate                         │
│   语义口径：「环境存在持续窄带平稳音」——非电话认定                  │
└──────────────────────────┬──────────────────────────────────────┘
                           ↓ 候选段
┌─────────────────────────────────────────────────────────────────┐
│ Tier1 Semantic Gate                                             │
│   YAMNet(class_map) → AudioSet 521 类 → 语义归并                 │
│   判定：candidate 是否为 telephone_persistent                    │
│   通过标准：§4 Qualification 映射表 + 指标                        │
└──────────────────────────┬──────────────────────────────────────┘
                           ↓ telephone_persistent（语义确认）
┌─────────────────────────────────────────────────────────────────┐
│ Hard Negative Precision Gate                                    │
│   固化回归集（§3.3）端到端复验：alarm/music/appliance/ambient      │
│   不得产出 telephone_persistent                                  │
└──────────────────────────┬──────────────────────────────────────┘
                           ↓ 可信 AudioKind
                     Risk Policy（ADR-0042 五档；ceiling 规则不变）
```

**实施边界说明**：`persistent_narrowband_candidate` 是架构意图表述。正式实施涉及
Tier0 产物语义降级（事件 kind 字段是否新增中间态 vs 内部状态标记），属感知→事件边界
变更，须先走契约评审（AGENTS.md §3.1）；本报告不预设接口设计。

---

## 3. Tier1 Semantic Qualification Dataset v1

### 3.1 结构

位置：`dataset/_canonical/audio_semantic/tier1_qualification/<semantic_class>/*.wav`
（gitignore 内，配方即规格可再生）；manifest：`manifest.jsonl`
（字段：id / file / semantic_class / source / duration_s / notes）。

| semantic_class | n | 语料来源 |
| --- | --- | --- |
| telephone | 10 | P1~P4 + TP-A1/A3~A7（persistent 通信音形态全集） |
| alarm | 4 | NEG-A7 FM 扫频警笛 + 新增：快扫 FM / 双音交替 / 低频慢扫 |
| music | 5 | NEG-A8/A9 + 新增：五声旋律 / 和弦琶音 / 短音符节奏 |
| appliance | 4 | fan 稳态/调制、fridge 稳态/周期启停（NEG-A2~A5） |
| ambient | 1 | N2 同源客厅底噪 |
| normal_speech | 7 | N1/N4/N5、B1/B2/B3 TTS 人声、voice_stressed |
| far_end_speech | 3 | HN5 tel+far_end、TP-A2 窄带化人声、far_end 原素材 |
| micro_event | 2 | N3、HN4 |

### 3.2 预期映射要求（Owner 定义的 7 行，形式化）

| ground truth class | 允许的 Tier1 语义输出 | 禁止输出 |
| --- | --- | --- |
| telephone | telephone / telephone_ring / ringtone | music, alarm*, noise 类 |
| alarm | alarm / siren | telephone* |
| music | music | telephone* |
| appliance | appliance 域标签（hum/mechanism 类透传） | telephone* |
| ambient | other / none（noise/silence 等） | telephone* |
| normal_speech | speech / none | telephone* |
| far_end_speech | speech（+ 可带 telephone 场景标签） | —— |
| micro_event | other / none | telephone* |

`telephone*` = {telephone, telephone_ring, ringtone}。

### 3.3 Hard Negative Regression Set v1（固化）

以下资产自本轮起为**固定回归集**，任何后续音频链路变更必须全量重跑：

| 来源 | 资产 | 击穿记录 |
| --- | --- | --- |
| #295 第一轮 | N2_ambient | Tier0 telephone FP（precision gate 唯一残留 FP） |
| #296 审计 | NEG-A2/A3 fan、NEG-A4/A5 fridge | Tier0 telephone FP（长段命中，min duration 无效） |
| #296 审计 | NEG-A7 siren、NEG-A8/A9 music | Tier0 telephone FP + env_cv 双向击穿证据 |
| 本轮 baseline | 上列全部 | YAMNet 层 confusable FP 见 §5（music→tel 100%） |

新增规则：今后任何新发现的 hard negative 一律进入本集合并登记击穿记录。

---

## 4. Qualification Gate 规格

### 4.1 验收指标

| 指标 | 定义 | 建议阈值（待批） |
| --- | --- | --- |
| telephone recall | telephone 类中 top-k 标签含 `telephone*` 的比例 | ≥ 90%（Layer2 语料上） |
| alarm → telephone FP | alarm 类中被标 `telephone*` 的比例 | = 0% |
| music → telephone FP | music 类中被标 `telephone*` 的比例 | = 0%（允许 music 标签共存时放宽 ≤5%） |
| appliance → telephone FP | 同理 | = 0% |
| ambient → telephone FP | 同理 | = 0% |
| speech 保真 | normal_speech 被标 speech 的比例 | ≥ 95% |
| far_end 行为 | 被标 speech 且不误报为风险升级 | 登记（far_end 承担交谈证据，非锚点） |

阈值均为建议值，须经 Owner 批准并在 Layer2 语料就绪后校准。

### 4.2 分层判定原则（baseline 教训写入规格）

| 层 | 语料 | 可判定内容 |
| --- | --- | --- |
| Layer1（合成） | 本 dataset v1 | 仅管道机制连通性（tagger 接线/标签流转/回归集防退化） |
| Layer2（公开许可真实录音） | Freesound CC0 等：真实铃声/忙音/拨号音/DTMF/家电/音乐片段 | **语义质量判定**（recall/FP 指标在此层计算） |
| Layer3（真实现场） | 家庭环境实录 | 最终 Acceptance（对齐 Batch B 报告 TTS 边界声明） |

### 4.3 与既有门控的关系

- ADR-0042 MONITOR ceiling：qualification 通过（Layer2 指标达标）是解除 ceiling 的
  前置条件之一，与硬门控 #1（class_map 修复）串联，均满足才可谈解除；
- 原 Precision Gate（V1/V2 系列）演进为 Hard Negative Precision Gate：跑法不变
  （四指标矩阵），但判定对象从「Tier0 规则输出」改为「两级串联后的最终 kind」。

---

## 5. YAMNet Baseline 实测（v1 dry-run，Layer1）

配置：`yamnet_runtime.onnx` + 官方 class_map.csv，threshold=0.05，top_k=10，
整条资产推理。

### 5.1 结果

| 指标 | 实测 |
| --- | --- |
| **telephone recall** | **0%**（0/10）——top1 全部为 white_noise/waterfall/water/rain |
| **music → telephone FP** | **100%**（5/5）——busy_signal 最高 0.978、telephone 0.969 |
| alarm → telephone FP | 50%（2/4）——低频慢扫警笛 busy_signal:0.968 + telephone:0.947 |
| appliance → telephone FP | 0%（explosion/music 类误标，但不涉电话） |
| ambient → telephone FP | 0% |
| normal_speech → speech 保真 | **100%**（7/7，score 0.74~0.99） |
| far_end_speech | 未检出 speech（top 为 vehicle/train/explosion，见 §5.2-③） |
| micro_event | silence/white_noise，无电话误报 |

### 5.2 三个关键发现

1. **合成 telephone 正样本在 AudioSet 语义空间中不是电话**。我们的 telephone_persistent
   是「窄带平稳合成持续音」，而 AudioSet 的 Telephone 类学的是真实电话铃/通信音色。
   recall=0% 是 ground truth 失真，不是分类器失灵——这正是「生态效度」问题的直接证据。
2. **合成纯音音乐 ≡ 电话忙音（语义空间真实重叠）**。busy_signal/dial_tone 本身就是
   纯音对（如美标 480+620Hz），我们用纯正弦序列合成的"音乐"在声学上就是忙音。
   music→tel FP=100% 是素材属性，不是模型缺陷。它同时解释了为什么这类素材能穿透
   Tier0 规则——它们与电话音在物理上同构。
3. **speech 类完全可靠**：7 条人声（含 TTS、窄带化）全部高分命中 speech。
   far_end 三条未检出 speech 的现象需 Layer2 复核（疑因混响重/电平低/窄带化损伤），
   暂记 open question，不影响主结论。

### 5.3 Baseline 结论

- Qualification 的**指标计算必须在 Layer2 真实语料上进行**（§4.2 分层判定）；
- Layer1 dataset 的价值定位修正为：管道机制连通性验证 + regression set 防退化；
- YAMNet 分类器本身获得正面旁证：speech 类完美、纯音类归类与 AudioSet 定义一致。

---

## 6. 下一步（待 Owner 批准后执行）

1. **telephone 类语料换血（Layer2 seed）**：引入公开许可真实录音——电话铃（机械/电子）、
   忙音、拨号音、DTMF 按键、真实通话片段；同步补齐 appliance/music/alarm 真实短样本；
2. 在换血后的 dataset 上正式跑 Qualification Gate（§4.1 指标），产出首份判定报告；
3. 两级管道实施设计（ADR 候选）：Tier0 候选化 + Tier1 语义确认的事件流转，
   含 `persistent_narrowband_candidate` 的契约落点评审；
4. Hard Negative Regression Set 纳入 CI 常规回归。

---

## 7. 附：证据文件清单

- 生成脚本 `_gen_tier1_dataset.py` / `_probe_yamnet_baseline.py`（一次性，已删除）；
- dataset：`dataset/_canonical/audio_semantic/tier1_qualification/`（36 wav + manifest.jsonl，
  gitignore 内本地留存）；
- baseline 原始输出：同目录 `_yamnet_baseline.json`（每条资产 top-10 标签+score）。