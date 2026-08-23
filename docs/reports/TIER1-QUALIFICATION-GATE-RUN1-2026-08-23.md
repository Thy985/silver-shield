# Tier1 Semantic Qualification Gate · RUN1 正式判定报告

> 日期：2026-08-23
> 状态：**CONDITIONAL PASS**（A/C/D 三口径达标；B 口径 soft 层达标、hard 层待 Owner 听检）
> 判定依据：Gate 规格 v2 §4.1（`TIER1-SEMANTIC-QUALIFICATION-GATE-SPEC-2026-08-23.md`，
> 本 PR 同步修订）· 语料 manifest v2（`LAYER2-PRE-FREEZE-REVIEW-2026-08-23.md` §6 清单执行完毕）
> 数据集：`dataset/_canonical/audio_semantic/tier2_qualification/`（24 条冻结，gitignore 本地留存）

---

## 0. 结论速览

| 口径 | 实测 | 阈值 | 判定 |
| --- | --- | --- | --- |
| **A. Telephone Signaling Recall** | **100%**（7/7） | ≥90% | ✅ PASS |
| **B. Telephone Speech Recall** | soft **100%**（2/2）；hard 层 = **registered limitation** | 双层 | ⏸ CONDITIONAL PASS |
| **C. Non-telephone FP** | **0%**（0/15） | =0% | ✅ PASS |
| **D. Speech Fidelity** | **100%**（3/3） | ≥95% | ✅ PASS |

> **B-hard 处置（Owner 决策 b，2026-08-23）**：LBJ×2 经 Owner 资产审查确认为「真正的通话语境」，
> 但窄带频响 / 线路失真两判据未完成逐条可复核确认——**不宣告 hard PASS**。
> 正式口径：`B = CONDITIONAL PASS; hard-layer limitation = registered,
> non-blocking for current Layer2 qualification scope.`
> 动机：「语义真实性」（通话语境成立）与「信道真实性」（窄带+失真特征）不得混同宣告。

**一句话结论：在 Layer2 真实语料上，「真实电话 → 确认为 telephone evidence」与
「真实非电话持续音 → 不误认为 telephone evidence」两个核心问题均获得肯定答案；
B 口径 hard 层作为已登记限制保留，对当前 Layer2 qualification scope 非阻塞。
模型能力问题就此冻结；产品叙事验证转入独立的 Product Story Fixture 域（ADR-0044）。**

对照 baseline（合成语料，#297 §5）的演进：

| 指标 | Layer1 合成（baseline） | Layer2 真实（本轮） |
| --- | --- | --- |
| telephone recall | 0%（0/10） | signaling 100%（7/7）+ speech soft 100%（2/2） |
| music → telephone FP | 100%（5/5） | **0%**（0/3） |
| alarm → telephone FP | 50%（2/4） | 0%（0/3） |

合成素材上的双向失败被证明完全是 ground truth 生态效度问题——分类器本身工作正常。

---

## 1. 判定配置

| 项 | 值 |
| --- | --- |
| 模型 | `data/models/yamnet/onnx/yamnet_runtime.onnx` + 官方 class_map.csv（ADR-0042 步骤 6 修复版，含回归锁） |
| 推理口径 | threshold=0.05 / top_k=10 / 整条资产（16k mono PCM16，12~18s） |
| TEL 标签集合 | `{telephone, telephone_ring, ringtone}` |
| speech 标签集合 | `{speech, conversation, narration,_monologue}`（AudioSet 下位归并） |
| 语料 | manifest **v2-frozen**：telephone 9 / alarm 3 / music 3 / appliance 3 / ambient 3 / normal_speech 3 = **24 条**，license PD×12 + CC0×2 + CC BY(SA)×10，NC=0，未知=0 |
| 原始输出 | `_l2_screen_result.json`（逐条 top-10）· `_l2_gate_result.json`（口径计算） |

## 2. Q1~Q5 处置记录（Agent 默认裁决，依 Pre-Freeze Review 建议）

> 「进行正式执行」指令下，Q1~Q5 以审查报告建议方案推进并显式登记于 manifest v2
> `q_disposition` 字段。Owner 对任何一条保留否决权，否决即触发对应回补与重跑。

| 议题 | 处置 |
| --- | --- |
| Q1 LBJ/B 口径判据 | 采纳双层判定（speech soft + 信道特征听检 hard）；McCormack 录音补齐为 narrowband 第 2 条 |
| Q2 sitting_in_a_room | 保留但标记 `content-doubtful`（top1=music .637），C 口径统计中单列 |
| Q3 Patrick Lalor 年龄 | 保留 `elderly-candidate` 标签，Owner 听检前不计入 elderly 子形态结论 |
| Q4 Feuerwehralarm 削波 | 豁免（警报类瞬态固有，clip 1.22%），标记 `layer3-recheck`；替补池四条已登记 |
| Q5 配额缺口 | 已补齐：narrowband #2（LBJ-McCormack, PD）+ ambient #3（TU Delft quiet study room, CC BY 3.0）；主干配额 24/24 达成 |

---

## 3. 口径明细

### 3.1 A · Signaling Recall（7/7 = 100%，阈值 ≥90%）

| 条目 | top-1 | TEL 最高分（top-10 内） |
| --- | --- | --- |
| US_dial_tone | telephone .981 | .981 |
| UK_AU_busy_signal | busy_signal .995 | .993 |
| Old_North_American_busy_signal | busy_signal .619 | .376 |
| Dial_tone_Germany_System_55 | alarm .388 | .378 |
| Model_500_British_ring | alarm .601 | .557 |
| Iskra_ETA80_ringing | silence .588 | .318 |
| Ring_tone_Germany_System_55 | silence .622 | .141 |

观察：机械铃三条的 top-1 被铃间静默（silence）/铃声瞬态（alarm）占据，TEL 标签以次高分共现
——印证 §4.1 v2 引言对「信令类语义靠标签存在性而非 top-1」的预判。最低命中 .141（RingGermany
telephone）仍高于 threshold=0.05 两倍以上，但已提示：若未来收紧判定为 top-5 或提高 threshold，
德式电子铃是最先跌落的样本。

### 3.2 B · Speech Recall（soft 2/2 = 100%；hard 层 pending）

| 条目 | speech 最高分 | TEL 标签 |
| --- | --- | --- |
| LBJ_FORD_phonecall_1963 | .953 | 无（预期内） |
| LBJ_MCCORMACK_phonecall_1963 | .961 | 无（预期内） |

两条真实窄带电话人声稳定落 `speech` 0.95+，soft 层无歧义。hard 层处置见 §0 决策 b：
**registered limitation，非阻塞**——「语义真实性」已由 Owner 资产审查确认
（真正的通话语境），「信道真实性」（窄带/线路失真逐条证据）保留为登记项，
不与语义宣告混同。

### 3.3 C · Non-telephone FP（0/15 = 0%，阈值 =0%）

alarm×3 / music×3 / appliance×3 / ambient×3 / normal_speech×3 共 15 条，top-10 内
**零 TEL 标签**。重点难例逐一核对：

- 合成时代同构源 music 三条（含纯音器乐）：全部 `music` 0.71~0.99，busy_signal 无踪影；
- 窄带稳态 hum（AC/冰箱）：vehicle/silence，dial/busy 无误标；
- 周期机械（洗衣机）：silence，ringtone 无误标；
- 扫频警报：siren/alarm 自类收敛；
- Nixon 磁带对话（最接近电话信道质感的负样本）：speech .521，无 TEL；
- TU Delft 室内底噪：`inside,_small_room` .272——AudioSet 场景类的正确归类。

### 3.4 D · Speech Fidelity（3/3 = 100%，阈值 ≥95%）

normal_speech 三条 speech 分数 .521/.953/.996，全部命中。

---

## 4. Gate 判定与 MONITOR ceiling 影响

**判定：CONDITIONAL PASS（hard-layer limitation registered, non-blocking）。**

- A/C/D 三口径在冻结语料上一次达标，且 C 口径 0% 优于 music 类 ≤5% 的放宽条款
  （未动用放宽）；
- B 口径定格为 `CONDITIONAL PASS + registered limitation`（Owner 决策 b）：
  语义真实性已确认，信道真实性保留为非阻塞登记项；
- **MONITOR ceiling 处置**：ADR-0042 硬门控 #1（class_map 修复）已完成并有回归锁；
  qualification 前置条件在本 scope 内已满足（limitation 已登记且非阻塞），但 ceiling
  解除仍由 **Owner 拍板**，本报告不单方面宣告。依据已充分：本节即为拍板材料。

### 4.1 判定边界声明

- 本轮语料为公开许可真实录音（Commons 单渠道），覆盖信令与人声两个子域，但**不含
  家庭现场噪声底下的电话录音**（Layer3 职责）；
- C 口径 0% 是在本负样本集上的结果；Hard Negative Precision Gate（端到端复验）仍须
  在两级管道实施后回归执行；
- Q2/Q3 两条挂起样本若被 Owner 否决，ambient 缺口回补不影响 A/B/D 结论，可能使 C
  分母 -1（重跑成本极低，脚本已固化）。

---

## 5. 下一步（v2 更新：模型能力问题冻结，重心转 Product Story）

1. ~~Owner B hard 听检~~ **已由决策 b 收口**（registered limitation, non-blocking）；
   ceiling 解除由 Owner 依据 §4 材料拍板；
2. **Agent（当前主线程）**：ADR-0044 数据资产三层解耦 + `telephone_risk Product Story
   Fixture Contract`（StoryTimeline 单一真相源 + provenance 三态 + benign/risk 双
   fixture）→ Browser Product Acceptance；
3. **Agent（随后）**：两级管道实施设计 ADR（Tier0 `persistent_narrowband_candidate` →
   Tier1 semantic confirm）——Runtime 作为 Story Contract 的实现者替换 fixture 的
   `execution_path`，不阻塞产品叙事验收；
4. Hard Negative Regression Set 升级为 Layer1 全集 + Layer2 24 条，纳入 CI 常规回归；
5. Layer3 现场语料规划（家庭环境实录，`real_sensor` provenance）进入 roadmap 视野。

---

## 6. 变更记录

| 时间 | 事项 |
| --- | --- |
| 2026-08-23 | 补采 2 条（McCormack/TU Delft）→ manifest v2 冻结 24/24 → 正式口径（threshold=0.05/top_k=10）三口径判定 → CONDITIONAL PASS，本报告成稿 |
| 2026-08-23（v2） | Owner 决策 b：B-hard 登记为 limitation 不升格 PASS（语义真实性≠信道真实性）；qualification scope 收口，模型能力问题冻结；重心转 Product Story Fixture（ADR-0044） |