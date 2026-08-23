# Layer 2 · Pre-Freeze Review（manifest v2 冻结前审查）

> 日期：2026-08-23
> 状态：**待 Owner 裁决**（本审查通过 + Q1~Q5 听审完成后，方冻结 manifest v2 并跑正式 Qualification Gate）
> 审查对象：`dataset/_canonical/audio_semantic/tier2_qualification/` 22 条主干候选（#299）
> 触发指令：Owner 2026-08-23——Gate 规格 §4.1 拆分为三口径后，冻结前先做 Pre-Freeze Review，
> 只检查五件事；同时定版产品语义：**telephone_persistent ≠ telephone scam，= 一个可靠的电话交互证据**。
> 关联文档：Gate 规格 v2 §4.1（三口径）· 候选池报告（#299，§2 清单/§6 缺口/§7 听审清单）

---

## 0. 结论速览

| # | 检查项 | 结论 |
| --- | --- | --- |
| 1 | telephone ×9 是否覆盖 signal + narrowband speech 两子域 | **CONDITIONAL**：signaling 子域覆盖充分（7 条×3 形态）；narrowband speech 仅 1 条且单一来源，须补第 2 条（备选已核） |
| 2 | 非电话 hard-negative 对 alarm/music/appliance/ambient 的覆盖 | **PASS（带 1 缺口）**：四类全覆盖且按混淆维度核对到位；唯 ambient 缺第 3 条 |
| 3 | license / provenance 闭合 | **PASS**：22/22 三要素齐备、单渠道可追溯、NC=0、未知许可=0 |
| 4 | 标签本身可疑的样本 | **2 条硬可疑**（sitting_in_a_room 内容存疑 / Patrick Lalor 年龄不确定），均已在听审清单挂起；另有 1 条软边界登记（classroom 弱人声） |
| 5 | Qualification 指标是否需要按 signaling/speech 拆分 | **必要（已执行）**：LBJ 反例证明两子域的 TEL 语义不在同一标签空间；规格 §4.1 已改 v2 |

**总体判定：候选池结构健康，无系统性缺陷。** 冻结 manifest v2 的前置条件收敛为：
①听审 Q1~Q5 落地；②补 narrowband-speech 第 2 条；③（可选）补 ambient 第 3 条或接受降配额。

---

## 1. 检查一：telephone ×9 子域覆盖

### 1.1 signaling 子域（7 条现役）——充分

| 形态 | 现役 | 多样性核对 |
| --- | --- | --- |
| ringtone ×3 | 英式机械铃（Model 500）/ 德式电子铃（System 55）/ 南斯拉夫机械铃（Iskra ETA80） | 机械+电子双制式 ✅ 三国电话制式 ✅ 响停 cadence 各异 ✅ |
| dial-tone ×2 | US（350+440Hz，CC0）/ DE System 55 | 双国制式 ✅ 含 CC0 许可样本 ✅ |
| busy-tone ×2 | 老美标（480+620Hz 断续）/ UK-AU | 双制式 ✅ 断续节奏差异 ✅ |

对 Tier0 候选域（窄带平稳持续音）的对位性：dial/busy tone 是纯音对稳态信号——正是 Tier0
最擅长捕获、也最难与家电 hum 区分的形态，正样本在此形态上密度充足。

### 1.2 narrowband-speech 子域（1 条现役）——不足，须补

- 现役仅 LBJ-FORD 1963 一条，且与备选同源（均为 DPLA/LBJ 图书馆 PD 电话录音）；
- 该子域是 v2 规格 B 口径的唯一对象，样本量 1 无法支撑任何统计口径（连「登记分布」都勉强）；
- **处置建议**：纳入 `Telephone conversation 616, LBJ and JOHN MCCORMACK, 12-20-1963`
  （PD，同渠道已检索命中）作为第 2 条。接受同源性的理由：B 口径第一轮走
  「speech soft + 听检 hard」双层判定而非统计阈值，来源多样性可在 Layer3 现场语料再补。
- 备选（如需跨源）：FDR-Cordell Hull 电话录音（DPLA, PD）；Clinton 系列（DPLA, PD，
  年代更近、音质更好但信道特征可能偏离老年家庭固话场景）。

### 1.3 配额核算

7 signaling + 1 narrowband = 8；补 1 条 narrowband 后 = **9，恰好满足契约配额**，无需动 signaling。

---

## 2. 检查二：非电话 hard-negative 覆盖

按「与 telephone 信令/人声的混淆维度」逐项核对（这是 hard-negative 的定义方式——不是任意负样本，而是结构上最容易穿透 Tier0/Tier1 的负样本）：

| 混淆维度 | 威胁对象 | 对位负样本 | 判定 |
| --- | --- | --- | --- |
| 窄带稳态纯音/ hum | dial-tone / busy-tone | AC hum（Gravity Sound）/ 冰箱内嗡鸣 | ✅ 2 条，频谱形态对位 |
| 周期性 cadence 循环 | ringtone | 洗衣机中段程序（周期机械声） | ✅ 1 条；偏弱但可接受（ringtone 与机械周期在 rate 维度已被 #296 证明不可分，Tier1 靠语义） |
| 扫频/瞬态高声压 | busy-tone / ringtone | Motorsirene / Air Raid Siren / Smoke alarm | ✅ 3 条（Feuerwehralarm 削波 Q4 待裁决，替补池已备） |
| 纯音序列音乐 | busy_signal（基频对结构同构） | Satie 钢琴 / 电子乐 / 国歌合唱 | ✅ 3 条——baseline 中合成纯音音乐 FP=100% 的直接对位，本轮全部零误标 |
| 人声 vs 电话人声 | narrowband-speech（B 口径） | Nixon 磁带对话（含磁带噪声，最接近电话信道质感）/ 国会演讲 / 老年候选人 | ✅ 3 条，其中 Nixon conversation 是关键难例 |
| 室内底噪 | dial/busy tone 低电平段 | classroom / sitting in a room | ⚠️ 2/3，缺 1 条待 Q5 裁决 |

**结论**：五大类 14 条负样本全部对位到具体混淆维度，无凑数样本；唯一缺口是 ambient 第 3 条。
normal_speech 类额外承担 C/D 双口径职责（既验 FP 又验 speech 保真），样本量 3 条满足第一轮规模。

---

## 3. 检查三：license / provenance 闭合

| 核对项 | 结果 |
| --- | --- |
| 三要素齐备（source_url + license_id + author） | **22/22**（manifest jsonl 字段完整；入库快照为候选池报告 §2 表格） |
| 渠道可追溯性 | 全部 Wikimedia Commons 单渠道；Special:FilePath 可复下载（报告 §1.2 记录绕行方案） |
| NC 许可混入 | 0 |
| 未知/缺失许可 | 0 |
| 登录墙素材 | 0 |
| CC BY / CC BY-SA attribution 义务 | 6 条（CC BY-SA 3.0×3、CC BY 4.0×2、CC BY 3.0×1）——manifest v2 冻结时生成 `ATTRIBUTION.md` 随 dataset 目录落盘（本地留存，不入库） |

**结论：闭合。** 无任何一条存在 provenance 断链或许可灰区。

---

## 4. 检查四：标签可疑样本

| 样本 | 可疑点 | 定级 | 处置 |
| --- | --- | --- | --- |
| ambient__room-tone-sitting__Sitting_in_a_room | top1=`music` .637——疑为环境音乐作品而非室内底噪 | **硬可疑** | 听审清单 Q2 挂起；若否决，ambient 降至 1/3，缺口扩大（见 §2） |
| normal_speech__elderly-candidate__Patrick_Lalor_Ladywell_reminiscence | 元数据未载说话人年龄，「elderly」标签无法从 provenance 证实 | **硬可疑** | 听审清单 Q3 挂起；若否决则 elderly 子形态空缺（monologue-adult/conversation 不受影响） |
| ambient__room-tone-classroom__Ambient_classroom_mono | 含低度人声（教室底噪固有） | 软边界 | **保留并登记**：ambient 类定义即「室内底噪（可含弱人声）」，与 normal_speech 的类边界靠能量占比区分；不构成标签错误 |
| alarm__security-beeper__Motorsirene_Feuerwehralarm | 数字削波 1.22%（A1 层面，非标签层面） | 硬可疑（A1） | 听审清单 Q4 挂起；替补池已备（Toy siren / Siren.ogg / Civil-defense-siren-waver） |
| normal_speech__monologue-adult__Kathy_Manning_speech | 会场混响底噪 | 干净 | monologue-adult 标签成立，speech .995 高置信佐证 |

**结论**：无静默可疑样本——全部可疑点均已显式挂起到听审清单；软边界 1 条有明确类定义依据。

---

## 5. 检查五：指标拆分必要性（结论：必要，已执行）

三条独立证据链：

1. **反例实证**：LBJ 真实电话录音 ground truth = narrowband speech，YAMNet `speech` .953
   且 top-10 无 TEL 标签——AudioSet 的 telephone 标签空间只覆盖信令音色；
   若沿用 v1 单一 recall ≥90% 口径，这条**模型完全正确**的样本将被计为漏报；
2. **baseline 教训同构**：#297 §5.2-① 已证明「ground truth 失真会被误诊为模型失败」
   ——v1 口径在 narrowband-speech 上重演同一结构性错误；
3. **产品语义要求**：telephone_persistent 定版为「电话交互证据」，其两条证据路径
   ——信令（有人在打电话）与人声（正在通话）——声学本质不同，判据必然不同；
   统一阈值会把证据问题扭曲成单模型能力问题。

**已落地**：Gate 规格 §4.1 v2 三口径（A Signaling recall ≥90% / B Speech 双层判定 /
C Non-telephone FP =0%）+ 流程插入本 Pre-Freeze Review 关卡（规格 §6 v2）。

---

## 6. 冻结 manifest v2 的前置条件清单（收敛后）

| # | 条件 | 状态 |
| --- | --- | --- |
| 1 | 听审 Q1（LBJ 保留 + B 口径判据走向确认） | 待 Owner |
| 2 | 听审 Q2（sitting_in_a_room 去留）→ 影响 ambient 缺口大小 | 待 Owner |
| 3 | 听审 Q3（Patrick Lalor elderly 标签） | 待 Owner |
| 4 | 听审 Q4（Feuerwehralarm 豁免 or 替补） | 待 Owner |
| 5 | 听审 Q5（narrowband #2 补齐 + ambient #3 策略） | 待 Owner |
| 6 | 补采执行（依 Q4/Q5 裁决结果；narrowband #2 建议 McCormack 录音） | Agent，裁决后即时 |
| 7 | 补采条目过 A1+A4 初筛 → 生成 `_candidates_manifest_v2.jsonl`（含 notes + ATTRIBUTION.md） | Agent |
| 8 | 正式跑 Qualification Gate §4.1 v2 三口径 → 首份判定报告 | Agent |

## 7. 变更记录

| 时间 | 事项 |
| --- | --- |
| 2026-08-23 | Owner 下达三口径拆分 + Pre-Freeze Review 指令；本审查完成五项检查，前置条件收敛为 Q1~Q5 + 补采两项 |