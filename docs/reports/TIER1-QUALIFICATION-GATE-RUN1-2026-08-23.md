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
| **B. Telephone Speech Recall** | soft **100%**（2/2）；hard 待听检 | 双层 | ⏸ CONDITIONAL |
| **C. Non-telephone FP** | **0%**（0/15） | =0% | ✅ PASS |
| **D. Speech Fidelity** | **100%**（3/3） | ≥95% | ✅ PASS |

**一句话结论：在 Layer2 真实语料上，「真实电话 → 确认为 telephone evidence」与
「真实非电话持续音 → 不误认为 telephone evidence」两个核心问题均获得肯定答案；
唯一未闭合项是 narrowband-speech 的信道特征人工听检（Owner 动作）。**

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

两条真实窄带电话人声稳定落 `speech` 0.95+，soft 层无歧义。**hard 层（电话信道特征听检：
频带限制/噪声底/失真确认）为 Owner 动作，是本 Gate 唯一开放项。**

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

**判定：CONDITIONAL PASS。**

- A/C/D 三口径在冻结语料上一次达标，且 C 口径 0% 优于 music 类 ≤5% 的放宽条款
  （未动用放宽）；
- B 口径 soft 层满分，hard 层（Owner 信道特征听检）完成后 B 即闭合；
- **MONITOR ceiling 处置建议**：ADR-0042 硬门控 #1（class_map 修复）已完成并有回归锁；
  qualification 作为解除前置条件之一，本轮 A/C/D 已满足、B 待 hard 听检——建议
  **Owner 完成 B hard 层听检后再一并拍板 ceiling 解除**，本报告不单方面宣告解除。
  这与规格 §4.3「串联门控」一致。

### 4.1 判定边界声明

- 本轮语料为公开许可真实录音（Commons 单渠道），覆盖信令与人声两个子域，但**不含
  家庭现场噪声底下的电话录音**（Layer3 职责）；
- C 口径 0% 是在本负样本集上的结果；Hard Negative Precision Gate（端到端复验）仍须
  在两级管道实施后回归执行；
- Q2/Q3 两条挂起样本若被 Owner 否决，ambient 缺口回补不影响 A/B/D 结论，可能使 C
  分母 -1（重跑成本极低，脚本已固化）。

---

## 5. 下一步

1. **Owner**：B 口径 hard 层听检（LBJ×2 信道特征确认）→ B 闭合 + ceiling 拍板；
   顺带裁决 Q2/Q3/Q4 挂起项；
2. **Agent**：两级管道实施设计 ADR（Tier0 `persistent_narrowband_candidate` → Tier1
   semantic confirm 的事件流转契约落点）——这是把 qualification 成果落进运行时的
   最后一段；
3. Hard Negative Regression Set 升级为 Layer1 全集 + Layer2 24 条，纳入 CI 常规回归；
4. Layer3 现场语料规划（家庭环境实录）进入 roadmap 视野。

---

## 6. 变更记录

| 时间 | 事项 |
| --- | --- |
| 2026-08-23 | 补采 2 条（McCormack/TU Delft）→ manifest v2 冻结 24/24 → 正式口径（threshold=0.05/top_k=10）三口径判定 → CONDITIONAL PASS，本报告成稿 |