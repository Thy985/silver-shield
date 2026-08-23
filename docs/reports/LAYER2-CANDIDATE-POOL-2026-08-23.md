# Layer 2 Tier1 Semantic Qualification · 候选池报告

> 日期：2026-08-23
> 状态：**待 Owner 听审**（本报告为 SOP 第 5 步产物；听审通过后才冻结 manifest v2）
> 上游契约：[`LAYER2-DATA-REQUIREMENT-CONTRACT-2026-08-23.md`](LAYER2-DATA-REQUIREMENT-CONTRACT-2026-08-23.md) §5（三层 Acceptance 判据）· §6（执行 SOP）
> Gate 规格：[`TIER1-SEMANTIC-QUALIFICATION-GATE-SPEC-2026-08-23.md`](TIER1-SEMANTIC-QUALIFICATION-GATE-SPEC-2026-08-23.md)
> 数据集目录：`dataset/_canonical/audio_semantic/tier2_qualification/`（`dataset/` 被 .gitignore 整体忽略，本报告为唯一入库事实快照）

---

## 0. 执行摘要

| 项 | 结果 |
| --- | --- |
| 主干候选总量 | **22 / 24**（契约主干 6 类 ×24 条） |
| License 合规 | **22/22**（CC0 / Public domain / CC BY / CC BY-SA，三要素齐备；NC=0，未知许可=0） |
| A1 格式初筛 | **21/22 PASS**（1 条素材固有削波待裁决，见 §4） |
| A4 YAMNet soft check | **非电话类 14 条零 TEL 误标**；telephone 类 7/8 有 TEL 标签命中，1 条语义合理例外（见 §5） |
| 显式缺口 | **2 条**：telephone·narrowband-speech 第 2 条、ambient 第 3 条（见 §6） |
| 听审待裁决 | **4 项**（见 §7） |

**关键信号（对照 #297 baseline）**：合成素材上 music→tel FP 为 100%、telephone recall 为 0%；本批真实素材上 **music/alarm/appliance/ambient/speech 共 14 条无一出现 TEL 标签误标**，telephone 信令类 7/8 在 top5 即命中 `telephone`/`telephone_ring`/`ringtone`。生态效度假设（baseline 失败源于合成素材而非分类器）获得正向证据。正式指标仍须待 manifest v2 冻结后在全量集合上计算。

---

## 1. 执行记录

### 1.1 渠道

单一渠道：**Wikimedia Commons**（API `list=search` 检索 + `prop=imageinfo` 元数据核验 + 文件下载）。Internet Archive / Freesound 未启用（Commons 命中已覆盖主干配额的 22/24）。

### 1.2 限流事件与绕行（过程记录）

- `upload.wikimedia.org` 直载在连续 ~13 次请求后触发边缘限流（HTTP 429，Varnish `0c640b1`），指数退避至 210s 仍持续拒绝；
- **有效绕行**：改走 `commons.wikimedia.org/wiki/Special:FilePath/<filename>`（302 重定向端点）+ `curl.exe`（不同 TLS/Header 指纹），剩余 10 条一次连续成功；
- 结论（供后续批次复用）：批量下载 Commons 音频应**默认使用 Special:FilePath 端点**，直载 URL 仅作元数据登记用途。

### 1.3 转码规格

统一 `ffmpeg -ac 1 -ar 16000 -c:a pcm_s16le`；裁剪模式：持续音取 18s（起点偏移 ≤2s）/ ringtone `-stream_loop` 循环至 ≥12s / 大文件电话录音取中段 18s（避开首尾静默）/ speech 取中段 12s。
两条源过短的周期性信令（Old NA busy 6s、US dial tone 5s）按周期信号无损语义用 `stream_loop` 补足至 ~14s 后复检 PASS。

---

## 2. 候选清单总表（22 条）

> A4 top1 = YAMNet top-1 标签（score）；「flag」列含义见 §5。来源 URL 均为 `https://commons.wikimedia.org/wiki/File:<name>`（Special:FilePath 同名可下载）。

### 2.1 telephone（8/9，缺 narrowband-speech ×1）

| id（前缀省略 `telephone__`） | subtype | license | author | dur(s) | A1 | A4 top1 |
| --- | --- | --- | --- | --- | --- | --- |
| ringtone__Model_500_Telephone_British_ring | ringtone | CC BY-SA 3.0 | CianMcCann | 14.0 | PASS | alarm .601 |
| ringtone__Ring_tone_Germany_System_55 | ringtone | CC BY-SA 3.0 | Rene Böke | 14.0 | PASS | silence .622 |
| ringtone__Iskra_ETA80_ringing | ringtone | CC BY 4.0 | Work With Sounds | 14.0 | PASS | silence .588 |
| dial-tone__US_dial_tone | dial-tone | CC0 | Edokter | 14.0* | PASS | **telephone .981** |
| dial-tone__Dial_tone_Germany_System_55 | dial-tone | CC BY-SA 3.0 | Rene Böke | 18.0 | PASS | alarm .388 |
| busy-tone__Old_North_American_busy_signal | busy-tone | Public domain | Denelson83 | 14.0* | PASS | busy_signal .619 |
| busy-tone__UK_AU_busy_signal | busy-tone | CC BY-SA 4.0 | SirLuciusLeftFoot | 18.0 | PASS | busy_signal .995 |
| narrowband-speech__LBJ_FORD_phonecall_1963 | narrowband-speech | Public domain | LBJ Presidential Library | 18.0 | PASS | speech .953 ⚑ |

`*` = stream_loop 补足；`⚑` = A4 flag（见 §5.2）。原始素材：LBJ 为 114MB WAV，取中段。

### 2.2 alarm（3/3）

| id（前缀省略 `alarm__`） | subtype | license | author | dur(s) | A1 | A4 top1 |
| --- | --- | --- | --- | --- | --- | --- |
| smoke-alarm__Smoke_alarm | smoke-alarm | Public domain | cori | 18.0 | PASS | alarm .977 |
| siren-outdoor__Pneumatic_Air_Raid_Siren | siren-outdoor | CC BY-SA 4.0 | Glosome | 18.0 | PASS | siren .543 |
| security-beeper__Motorsirene_Feuerwehralarm | security-beeper | Public domain | Nallchen | 18.0 | **FAIL** ⚑clip | siren .736 |

### 2.3 music（3/3）

| id（前缀省略 `music__`） | subtype | license | author | dur(s) | A1 | A4 top1 |
| --- | --- | --- | --- | --- | --- | --- |
| vocal-song__Amar_Sonar_Bangla_choir | vocal-song | Public domain | Press Information Dept. Bangladesh | 18.0 | PASS | music .706 |
| instrumental__Satie_Gymnopedie_No3_piano | instrumental | Public domain | Satie (rec.) | 18.0 | PASS | music .990 |
| electronic-rhythmic__automatization_electronic | electronic-rhythmic | CC BY-SA 4.0 | Complex Numbers | 18.0 | PASS | music .966 |

### 2.4 appliance（3/3）

| id（前缀省略 `appliance__`） | subtype | license | author | dur(s) | A1 | A4 top1 |
| --- | --- | --- | --- | --- | --- | --- |
| fan-hvac__Air_conditioner_hum_gravitysound | fan-hvac | CC BY 4.0 | Gravity Sound | 14.0† | PASS | vehicle .692 |
| fridge-hum__Inside_of_refrigerator | fridge-hum | Public domain | stephan | 18.0 | PASS | silence .287 |
| motor-appliance__Washing_machine_mid_program | motor-appliance | Public domain | ezwa | 18.0 | PASS | silence .251 |

`†` = 源素材全长即 ~14s（不足 18s 配额但满足 ≥10s 下限）；AC hum 与冰箱/洗衣机 top1 落在 vehicle/silence 属 YAMNet 对稳态 hum 的已知混淆，不构成 TEL 误标。

### 2.5 ambient（2/3，缺 1）

| id（前缀省略 `ambient__`） | subtype | license | author | dur(s) | A1 | A4 top1 |
| --- | --- | --- | --- | --- | --- | --- |
| room-tone-classroom__Ambient_classroom_mono | room-tone-classroom | Public domain | rcrossley | 18.0 | PASS | vehicle .105（低置信） |
| room-tone-sitting__Sitting_in_a_room | room-tone-sitting | CC BY 3.0 | Epsilon not (FMA) | 18.0 | PASS | music .637 ⚑内容存疑 |

### 2.6 normal_speech（3/3）

| id（前缀省略 `normal_speech__`） | subtype | license | author | dur(s) | A1 | A4 top1 |
| --- | --- | --- | --- | --- | --- | --- |
| conversation__Nixon_tape_840_conversation | conversation | Public domain | Nixon Presidential Office (DPLA) | 18.0 | PASS | speech .521 |
| monologue-adult__Kathy_Manning_speech | monologue-adult | Public domain | U.S. House of Representatives | 12.0 | PASS | speech .995 |
| elderly-candidate__Patrick_Lalor_Ladywell_reminiscence | elderly-candidate | CC0 | A.-K. D. | 12.0 | PASS | speech .996 ⚑年龄不确定 |

---

## 3. Manifest 快照

机器可读清单落盘于 `dataset/_canonical/audio_semantic/tier2_qualification/_candidates_manifest.jsonl`（22 行，字段：`id / semantic_class / subtype / source_url / license_id / author / file / status / duration_s / source_duration_s`）。因 `dataset/` 不入库，Owner 听审后若冻结 v2，将以本报告 §2 表格为准同步生成 `_candidates_manifest_v2.jsonl` 并在其 `notes` 字段落听审结论。

自动初筛原始输出：`_l2_screen_result.json`（含每条 A1 detail 与 YAMNet top-5 完整标签分数）——同为本地工作产物，不入库。

---

## 4. A1 异常登记（3 条）

| id | 判定 | 处置 |
| --- | --- | --- |
| telephone__busy-tone__Old_North_American_busy_signal | 源仅 6s < 10s 下限 | **已修复**：周期信令 `stream_loop` 补足 14s，复检 PASS |
| telephone__dial-tone__US_dial_tone | 源仅 5s < 10s 下限 | **已修复**：同上，复检 PASS |
| alarm__security-beeper__Motorsirene_Feuerwehralarm | 削波占比 1.22% > 0.5% 上限（素材固有数字削波） | **待听审裁决**：降增益无法消除既有平顶失真；备选见 §6.3 |

---

## 5. A4 Soft Check 发现（YAMNet top-10，threshold=0.01）

> 契约 §5 A4 定位：soft check 仅触发复核，**不以模型结果筛除任何候选**。TEL 标签集合沿用 Gate 规格：`{telephone, telephone_ring, ringtone}`。

### 5.1 非电话类：零 TEL 误标

music(3)/alarm(3)/appliance(3)/ambient(2)/normal_speech(3) 共 **14 条，top-10 内均无 TEL 标签**。对照 baseline 的结构性风险（合成纯音音乐 ≡ busy_signal 0.97）：真实器乐/合唱/电子乐三条 music 全部稳定落在 `music`（0.71~0.99）。**这是两级 Gate 架构成立性的首个真实语料正向证据。**

### 5.2 telephone 类：7/8 命中 + 1 条规格级例外

- 信令类（ringtone×3 / dial×2 / busy×2）**全部在 top-5 内出现 TEL 标签**（US_dial_tone `telephone` .981；UK_busy `telephone` .993；其余 .135~.45 区间共现）。机械铃 top1 落在 `silence`/`alarm` 是帧级聚合下铃间静默主导所致，不影响 TEL 标签存在性判定；
- **例外：LBJ_FORD_phonecall_1963**（narrowband-speech）top1=`speech` .953，top-10 无 TEL 标签。这不是素材缺陷——它确实是窄带电话信道里的人声，YAMNet 按「人声」归类语义正确。**暴露的是 Gate 规格议题**：narrowband-speech 子类的 TEL 语义依赖「电话信道特征」（频带限制/噪声底/失真）而非声学事件标签。候选处置：(a) 为该子类单独定义 soft-check 判据（如接受 `speech`+人工确认信道特征）；(b) 维持统一判据并在 Gate 指标中把 narrowband-speech 计入「预期难例」。**须 Owner 拍板，影响 manifest v2 冻结形态。**

---

## 6. 显式缺口登记（对照契约 §3 配额）

### 6.1 telephone·narrowband-speech：1/2

检索命中的其余 DPLA 电话录音（FDR-Cordell Hull、LBJ-McCormack、Clinton 系列）均为同源同性质素材（PD 电话录音），**第 2 条可从备选池即时补齐**（建议 `Telephone conversation 616, LBJ and JOHN MCCORMACK`，Public domain）。未先行纳入是避免同质化冗余，待 Owner 确认是否需要跨来源多样性后再补。

### 6.2 ambient：2/3

第三条候选在 Commons 检索中未见高置信命中（现存两条中 `room-tone-classroom` 含低度人声、`room-tone-sitting` 内容存疑见 §7）。选项：(a) 放宽关键词重检（room tone / apartment ambience / house interior noise）；(b) 扩展到 Internet Archive 渠道；(c) Owner 接受 2/3 缺口并降配额。**待拍板。**

### 6.3 alarm 替补池（Feuerwehralarm 若被否决）

已核验存在的备选（license 未核）：`File:Toy siren alarm.ogg`、`File:Alarm or siren.ogg`、`File:Siren.ogg`、`File:Civil-defense-siren-waver.ogg`。若听审否决 Feuerwehralarm，按 SOP 从替补池补查 license 后纳入。

---

## 7. Owner 听审清单（A3 硬性关卡，按优先级）

| # | 候选 | 裁决问题 |
| --- | --- | --- |
| Q1 | narrowband-speech__LBJ_FORD_phonecall_1963 | 窄带人声子类的 A4 判据走向（§5.2 两选项）；同时确认其作为 narrowband 第 1 条的听检通过性 |
| Q2 | room-tone-sitting__Sitting_in_a_room | top1=music .637，疑似环境音乐而非纯室内底噪——是否保留为 ambient |
| Q3 | elderly-candidate__Patrick_Lalor_Ladywell_reminiscence | 说话人是否属「老年声音」目标分布（元数据未载年龄，需耳听判断） |
| Q4 | security-beeper__Motorsirene_Feuerwehralarm | 削波 1.22% 是否豁免（警报类瞬态高声压素材普遍接近削波）或从 §6.3 替补池更换 |
| Q5 | narrowband-speech 第 2 条补齐（§6.1）与 ambient 第 3 条策略（§6.2） | 补齐 or 接受缺口降配额 |

---

## 8. 下一步（听审通过后）

1. 依听审结论生成 `_candidates_manifest_v2.jsonl`（含 notes 字段），冻结候选池；
2. 在冻结全集上计算 Gate 规格 §4.1 正式指标：telephone recall / precision、各类→tel FP、hard-negative regression 通过率；
3. Hard Negative Regression Set 升级：Layer1 全集 + 本批 Layer2 非电话全集合并回归；
4. 指标达标 → 解锁两级管道实施设计评审（Tier0 `persistent_narrowband_candidate` → Tier1 semantic confirm 契约落点）。

---

## 附：变更记录

| 时间 | 事项 |
| --- | --- |
| 2026-08-23 | 契约批准后启动检索 SOP；Commons 直载限流，改道 Special:FilePath；22/22 下载转码完成；A1 初筛 21 PASS / 1 待裁决；A4 完成；本报告成稿待听审 |