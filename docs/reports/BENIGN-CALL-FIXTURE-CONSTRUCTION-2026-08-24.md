# Benign 正常通话音轨构造报告（SSOT v3.2 收尾路径 · 步骤 A）

- **日期**：2026-08-24
- **授权链**：`docs/reports/DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md`（v3.2）§执行路径步骤 A；F-1 冻结方案经 Owner 拍板
- **产物**：`dataset/_canonical/audio_semantic/product_story/benign_normal_call/audio/normal_call_fixture.wav`
- **可复现**：`python scripts/build_benign_call_fixture.py build`（构造 + provenance + 自动三层校验一体）

---

## 1. 结论摘要

| 项 | 结果 |
| --- | --- |
| 资产 | `normal_call_fixture.wav`，20.300s @ 16kHz/mono/PCM16 |
| 叙事 | 响铃(0~1.05s) → 自然静默 → 接听(1.8~2.3s) → 正常通话(2.3~20.3s) → 挂断(fade-out) |
| **C1 校验** | ✅ **PASS**（全段 / 分段 / pipeline 三层黑名单零命中；宽阈值 0.03 审计零出现） |
| StoryTimeline 标签 | `telephone_conversation_start` @ t≈2.3s（**不使用** `bidirectional_speech_start`，判定依据见 §5） |
| 遗留登记 | Tier0 P2 塌缩在本素材上的量化新证据（§4），不阻塞素材合格判定 |

## 2. 选材依据

### 2.1 Signaling：Germany System 55（三候选能量轮廓实测对比）

| 候选 | 时长 | 结构特征 | 判定 |
| --- | --- | --- | --- |
| Model_500_Telephone_British_ring | 14.0s | burst 间 RMS 谷值 ~0.14（max 的 26%），**无真静默期**（混响底噪） | ❌ 无法在自然静默内切割 |
| Iskra_ETA80_ringing | 14.0s | 有真静默但带 1.0s 前导空白，电平偏低（max=0.176） | ❌ 需增益补偿，引入处理痕迹 |
| **Ring_tone_Germany_System_55** | 14.0s | t=0 即响铃（1.0s burst）→ 真零静默期；电平 0.689 与语音段匹配 | ✅ **采用** |

切割点由 RMS 能量检测定位在第一 burst 结束后 +0.8s（自然静默期深部），叙事为「铃响一声、停顿、拿起听筒」，不产生铃声截断 artifact。

### 2.2 通话段：LBJ_FORD candidates 版（18.0s）

- 取 **candidates 版本**而非 raw（647.9s）：该 18s 正是 TIER1-RUN1 qualification 的实测对象（top speech ≥ .95、零 TEL/distress 标签），C1 校验证据可与既有 qualification 证据直接对照；
- 总时长 20.3s ≠ mix.wav 的 30s 是**有意决策**：素材真实完整性优先于规格对称——不循环拼接通话段制造非自然重复（F-1 教训「label 对了 ≠ acoustic semantics 对了」的延伸）；benign 场景 `loop=false`，时长非契约项。

## 3. C1 校验实测证据（三层）

### 3.1 YAMNet 语义层（threshold=0.1，class_map 修复版已显式加载）

| 范围 | top 标签（score） | 黑名单 |
| --- | --- | --- |
| 全段 | speech=0.882 | 零 |
| ring 段 (0~1.8s) | alarm=0.565, telephone=0.413, telephone_ring=0.209, siren=0.162, dial_tone=0.160 | 零 |
| speech 段 (2.3s~末尾) | speech=0.952 | 零 |
| 宽阈值审计 (0.03, top_k=30) | — | **黑名单类零出现** |

正面期望均命中：ring 段 telephone/telephone_ring ✓；speech 段 speech ✓。
注：alarm/siren 为 Germany 铃声金属高频的已知 AudioSet 归类现象，非风险语义。

### 3.2 AudioPipeline 全链路层

8 条事件：1×`audio_voice_raised`（ring 段，Tier0 对铃声能量的误报）+ 7×`audio_distress_cry`。
**7 条 distress_cry 的 Tier1 `scored_labels` 全部纯净**（speech 0.81~0.99 / radio 0.195），无任何 crying/distress/scream/shout 佐证。

## 4. 关键发现：Tier0 P2 塌缩缺陷的量化新证据

**现象**：Tier1 完全关闭（tagger=None）时，Tier0 Energy 规则层仍独立产出同分布的 7 条 `audio_distress_cry`，且事件自带 `distress` 规则标签——证明 distress_cry 来自 **Tier0 特征规则塌缩**（已知 P2：tremor 重定义缺陷），而非 YAMNet 误报或素材问题。

**方法论修正**：初版校验脚本以 `ev.labels`（Tier0 规则标签 ∪ Tier1 标签并集）判 Tier1 佐证导致误 FAIL。契约上纯 Tier1 字段是 `scored_labels`（pipeline.py 注记：「seg.labels 与 ev.labels 语义层不同」）。已修正——**此区分对本仓库所有音频验收断言具有普适参考价值**。

**影响面评估**（按冻结契约推演，未改任何 Runtime 代码）：

1. Decision 链不受影响：硬门控 #2（policy 未升级前 audio→risk 链不接通）+ ADR-0042（MONITOR ceiling）；
2. DOM 音频事件表会显示 distress_cry 行（perception 层如实渲染）→ **D2 视觉验收时 benign 场景会出现"哭诉"字样事件条目**，属产品观感问题，是否提前修复 P2 由 Owner 决策（P2 原挂账不阻塞主线）；
3. 六元组 `expected_audio_evidence` 应写**素材语义真相**（normal call，零 distress）：Runtime 实际产出偏离 truth = P2 缺陷被验收逻辑链按设计暴露，而非反向迁就 bug 改 fixture。

## 5. StoryTimeline 标签判定

**判定：`telephone_conversation_start` @ t≈2.3s**

依据 F-1 命名规则（v3.2 §拍板记录）：仅当素材存在两方轮流说话的可证证据才使用 `bidirectional_speech_start`。LBJ_FORD 为历史电话录音的**单侧信道记录**，VAD 检出的 8 个语音段为同一说话人的语句间隙（TIER1-RUN1 定性：单方窄带人声），无第二说话人证据。时间戳取接听后首个 VAD 语音段起点（t=2.30s，与 provenance `boundaries.answer_silence_end_s` 对齐）。

## 6. 下一步

- **步骤 B**：risk mix.wav 六元组实测三方对照（可复用本脚本的分层校验模式；注意 case_b_mix 同样会暴露 Tier0 塌缩，处置口径与本报告 §4 一致）;
- **步骤 C**：成对冻结——`product_story_benign.yaml` 音源替换为本资产（synthetic → file），F-2/F-3 落地；
- P2 塌缩修复优先级升级与否 → 提请 Owner 决策（本素材可作为其回归测试样本：修复成功当以「benign 素材零 distress_cry 误报」为验收判据之一）。

## 附：产物清单

```
dataset/_canonical/audio_semantic/product_story/benign_normal_call/audio/
├── normal_call_fixture.wav   # 20.300s, 16kHz/mono/PCM16
└── provenance.json           # 配方/边界/license/复现命令
scripts/build_benign_call_fixture.py  # 构造+校验一体（ruff clean；build|verify 子命令）
```