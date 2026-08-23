# Layer 2 数据需求契约（冻结稿 · 待 Owner 批准）

> 日期：2026-08-23
> 状态：**CONTRACT DRAFT — 待 Owner 批准后冻结**；批准前不得启动检索。
> 任务定位：Tier1 Semantic Qualification 路线（PR #297）的数据侧前置契约——
> 冻结每一类「需要什么 / 为什么 / 最低多少 / license 要求 / acceptance 判据」，
> 批准后由 Agent 执行自动检索与整理。
> 上游依据：`TIER1-SEMANTIC-QUALIFICATION-GATE-SPEC-2026-08-23.md`
> （§4 分层判定原则、§5 baseline 三发现）、`AUDIO-EVIDENCE-MATRIX-TELEPHONE-RISK-2026-08-23.md`。

---

## 0. 结论速览

1. 主干 **6 类 × 最低 24 条**（telephone 9 / alarm 3 / music 3 / appliance 3 /
   ambient 3 / normal_speech 3，数量与覆盖项按 Owner 指令冻结）；
   `far_end_speech` 归并入 telephone·narrowband-speech 子形态，`micro_event` 降为
   P2 可选回归补充（§4 归并决策）；
2. License 政策：**CC0 首选，CC-BY/CC-BY-SA 允许（须登记三要素），NC 类待 Owner
   拍板；未知许可一律禁止**（§2.3）；
3. Acceptance 三层判据：资产级（格式+时长+license+人工听检+YAMNet soft check）→
   类级（数量+子形态覆盖）→ Gate 级（v2 冻结后跑 Qualification 正式指标）（§5）;
4. 本契约只解决 Layer2 语料准入；Qualification 通过标准仍以 #297 §4.1 为准，
   阈值批准独立进行。

---

## 1. 背景与定位

Baseline 实测（#297 §5）证明：Layer1 合成语料上 telephone recall=0%、music→tel FP=100%，
根因是合成正样本不具备生态效度——**qualification 的语义判定必须换用真实录音**。

本契约定义 Layer2 语料的准入规格。原则：

- **真实性**：真实环境录制的声音，非合成/非 MIDI 化/非纯音近似；
- **形态多样性**：每类覆盖其真实世界的主要变体（Layer1 教训：单一形态产生伪结论）；
- **可追溯**：每条素材 license 与来源三要素齐备，manifest 全量登记；
- **最小充分**：数量取「统计意义下限」，够跑 Qualification 指标即可；规模扩张留给
  Layer3 / 后续迭代，不在本契约范围。

---

## 2. 全局技术规格（所有类别强制）

### 2.1 音频格式

| 项 | 要求 |
| --- | --- |
| 入库格式 | 16 kHz / mono / PCM16 wav（与既有管道一致；源可为 44.1k/48k，统一转码） |
| 时长下限 | 持续音类 ≥15s；事件/语音类 ≥10s（详见 §3 各类）；不足者以素材内自然循环拼接（禁人工静默填充） |
| 电平 | 峰值不削波（< 0 dBFS），不做人工增益归一化（保留真实响度特征，Tier0 raised 判定依赖绝对响度） |
| 禁止项 | 二次合成处理（移调/时间拉伸/降噪过度致声学特征失真）；多轨混音成品（如带 BGM 的铃声可选，但须在 notes 标注） |

### 2.2 目录与 manifest v2

```
dataset/_canonical/audio_semantic/tier2_qualification/<semantic_class>/<id>.wav
dataset/_canonical/audio_semantic/tier2_qualification/manifest.jsonl
```

manifest 字段（v1 基础上扩展，加粗为新增必填）：
`id` / `file` / `semantic_class` / **`subtype`**（§3 子形态枚举值）/ `duration_s` /
**`source_url`** / **`license_id`**（SPDX 短标识，如 CC0-1.0、CC-BY-4.0）/
**`author`** / **`retrieved_date`** / **`capture_context`**（室内外/设备距离等，可得时填）/
`yamnet_sanity`（soft check 结果，见 §5.1）/ `notes`。

### 2.3 License 政策

| 许可 | 准入 | 条件 |
| --- | --- | --- |
| CC0-1.0 / 公共域 | ✅ 首选 | 无 |
| CC-BY 4.0 | ✅ 允许 | manifest 登记署名三要素 |
| CC-BY-SA 4.0 | ⚠️ 允许但登记 | 同上 + SA 再分发影响在 notes 备注 |
| CC-BY-NC / ND 系列 | ❓ 待 Owner 拍板 | 比赛/研究用途通常可行；若未来产品化需重评估。默认不入库，候选可先行收集隔离存放 |
| 无明确许可 / YouTube·AudioSet 抓取 / 违反站点 ToS | ❌ 一律禁止 | —— |

**红线**：任何素材缺 source_url / license_id / author 任一项即拒绝入库；
`.gitignore` 继续排除音频文件本体，入库的只有 manifest 与报告（配方可复现）。

---

## 3. 类别需求契约（主干 6 类）

### 3.0 总表（数量与覆盖项已按 Owner 指令冻结）

| 类别 | 最低样本 | 必须覆盖（subtype 枚举） | 定位 |
| --- | --- | --- | --- |
| telephone | **9** | ringtone / dial-tone / busy-tone / narrow-band speech | qualification 主指标正类 |
| alarm | **3** | siren / alarm（家庭烟雾报警器等） | confusable hard negative |
| music | **3** | 真实音乐（**禁止纯正弦合成**） | confusable hard negative |
| appliance | **3** | fan / fridge / motor | confusable hard negative |
| ambient | **3** | indoor room tone | Tier0 FP 源回归 |
| normal_speech | **3** | 真人 speech（非 TTS） | speech 保真主指标 |

### 3.1 telephone（9 条）

**为什么**：qualification 唯一正类。Baseline 证明合成"电话音"在 AudioSet 中非电话
（recall 0%）——必须以真实电话声音重建 ground truth。9 = 四个子形态的最小可信配额
（每个子形态 ≥2，最大类 ringtone 给 3），既保证形态多样性又不放大检索成本。

| subtype | 配额 | 规格 | 为什么需要 |
| --- | --- | --- | --- |
| ringtone | 3 | 机械铃/电子铃/智能手机铃各 ≥1；含 ≥2 次完整响铃循环（≥10s） | 「来电」语义锚点；三种发声机制覆盖 AudioSet Telephone bell ringing / Ringtone 类 |
| dial-tone | 2 | 拨号音持续录音 ≥15s（不同制式更好：美标 350+440Hz / 其他制式） | 对应 telephone_persistent 目标语义「持续通信音」；dial_tone 正是 baseline 中 music 误报的目标类，必须有真值锚点 |
| busy-tone | 2 | 忙音持续录音 ≥15s | 同上；baseline 中合成音乐被大量误判为 busy_signal（0.97），需真实 busy-tone 反向锚定 |
| narrowband-speech | 2 | 真实电话通话片段 ≥15s（听筒/免提均可，notes 标注） | 电话信道内人声；承接原 far_end_speech 类（归并决策 §4.1），关联缺陷 B 语境 |

### 3.2 alarm（3 条）

**为什么**：baseline 中低频慢扫警笛被判 busy_signal:0.968（alarm→tel FP 50%）；
Tier0 下 siren 也是长段 telephone 命中者。真实警报录音用于验证语义层能否分开
alarm 与 telephone。

| subtype | 配额 | 规格 | 为什么需要 |
| --- | --- | --- | --- |
| smoke-alarm | 1 | 家庭烟雾报警器报警声 ≥10s（T3 型循环最佳） | 家庭场景最高频警报；与电话铃最易混淆的家用声源之一 |
| siren-outdoor | 1 | 户外警报器/民防警笛 ≥10s | 经典窄带扫频；对应 AudioSet Siren / Civil defense siren |
| security-beeper | 1 | 防盗/门禁警笛或断续蜂鸣 ≥10s | 电子蜂鸣形态补全 |

### 3.3 music（3 条）

**为什么**：baseline 中合成纯音音乐 → tel FP 100%（纯音序列 ≡ 忙音）。真实音乐
谐波丰富、节奏连续，是与忙音可分的反例锚点。

| subtype | 配额 | 规格 | 为什么需要 |
| --- | --- | --- | --- |
| vocal-song | 1 | 含人声的歌曲片段 ≥15s | 人声+伴奏复合谱；同时校验 speech/music 标签共存行为 |
| instrumental | 1 | 器乐曲 ≥15s（钢琴/弦乐等自然乐器） | 自然谐波结构 vs 纯音的反差锚点 |
| electronic-rhythmic | 1 | 电子乐/节拍明确的段落 ≥15s | 合成器音色但成品化音乐——检验「电子≠忙音」边界 |

### 3.4 appliance（3 条）

**为什么**：NEG-A2~A5 在 Tier0 全部长段命中 telephone 且 min duration 无法拦截
（#296）；须验证语义层对家电嗡鸣的正确拒识。

| subtype | 配额 | 规格 | 为什么需要 |
| --- | --- | --- | --- |
| fan-hvac | 1 | 风扇/空调运转 ≥15s | NEG-A2/A3 的真实版；宽带平稳噪声代表 |
| fridge-hum | 1 | 冰箱压缩机运行 ≥15s（若能录到启停周期更佳） | NEG-A4/A5 真实版；工频谐波 hum 代表 |
| motor-appliance | 1 | 洗衣机/微波炉等电机运转 ≥15s | 旋转机械调制形态补全 |

### 3.5 ambient（3 条）

**为什么**：N2 底噪是 precision gate 唯一残留 FP（#295→#296 链条起点）；须有真实
房间底噪验证「安静室内 ≠ 电话」。

| subtype | 配额 | 规格 | 为什么需要 |
| --- | --- | --- | --- |
| room-tone-living | 1 | 客厅底噪 ≥15s（无人说话时段） | 对标 N2 场景 |
| room-tone-kitchen | 1 | 厨房底噪 ≥15s | 家电待机混合底噪 |
| room-tone-bedroom | 1 | 卧室底噪 ≥15s | 低电平静音场景；quiet-phone 召回的对照面 |

### 3.6 normal_speech（3 条）

**为什么**：speech 保真是 qualification 的正向对照指标（baseline 中 TTS 人声 7/7，
但须确认真人语音同样可靠）；银发场景要求覆盖老年语音。

| subtype | 配额 | 规格 | 为什么需要 |
| --- | --- | --- | --- |
| conversation | 1 | 真人日常对话 ≥10s（多人更佳） | 最常见家庭语音形态 |
| monologue-adult | 1 | 单人连续说话 ≥10s | 与对话形态对照 |
| elderly-speech | 1 | 老年人说话 ≥10s（语速慢/音高低特征自然呈现） | 场景用户画像直接相关；老年语音特征是后续 distress 判定的基础 |

---

## 4. 归并决策登记（供 Owner 确认）

### 4.1 far_end_speech 并入 telephone·narrowband-speech

理由：Owner 总表未单列该类；且 Evidence Matrix 已定调 far_end 承担「交谈证据」而非
独立锚点——其语义本质就是「电话信道内的人声」。归并后由 telephone/narrowband-speech
子形态承载，验收时额外记录其 YAMNet 是否输出 speech 共存标签（信息项，非门槛）。

### 4.2 micro_event 降为 P2 可选

理由：Owner 总表未列入；micro_event 在 baseline 中无电话误报（silence/white_noise），
不构成 confusable 风险。保留为 regression set 补充位：若后续检索中顺带获得
glass-break/knock 素材可收集（各 1 条即可），不阻塞 Gate。

---

## 5. Acceptance 判据（三层）

### 5.1 资产级（逐条准入门槛）

| # | 判据 | 性质 |
| --- | --- | --- |
| A1 | 格式合规：16k/mono/PCM16、时长达标、无削波 | 硬性 |
| A2 | license 三要素齐备（url/license/author）且政策合规（§2.3） | 硬性 |
| A3 | 人工听检：内容与标注 subtype 相符、无明显失真 | 硬性（抽检规则见 §6） |
| A4 | YAMNet sanity soft check：top-10 标签与语义不矛盾（例：ringtone 素材 top-10 含 telephone*/bell/music 任一即通过；完全无关如 vehicle 触发人工复核） | **软性**——仅触发复核，不自动否决（真实素材的模型表现正是测量对象，不得用模型筛数据） |

### 5.2 类级

- 数量 ≥ 总表最低值；subtype 覆盖齐全（每 subtype ≥ 配额）；
- 未达标类的处理：允许降配额通过但必须在报告中显式登记缺口及影响（例如某 subtype
  确无可准入素材），不得静默缺失。

### 5.3 Gate 级（qualification 正式判定前置条件）

1. 六类全部达到类级标准 → dataset v2 冻结（manifest 定版，此后增删走变更登记）；
2. v2 全量进入 Hard Negative Regression Set（负类）与正类基准集；
3. 以 #297 §4.1 指标跑正式 Qualification 报告（阈值届时另行批准）；
4. MONITOR ceiling 维持至正式判定通过。

---

## 6. 执行流程（本契约批准后启动）

```
Owner 批准契约（冻结）
    ↓
Agent 自动检索整理（SOP）
  ① 按附录 A 渠道逐类检索候选（优先 CC0）
  ② 下载 → 转码 16k/mono/PCM16 → 时长裁剪/循环拼接
  ③ 自动初筛：A1 格式检查 + A4 soft check
  ④ 产出《候选池报告》：每类候选清单 + license 表 + 初筛结果
    ↓
Owner 人工听审（36 条量级建议全量听审；A3）
    ↓
通过者写入 manifest v2 → dataset v2 冻结 → Gate 级流程（§5.3）
```

执行约束：检索阶段遇到登录墙/不可直载素材，仅登记 URL 交人工处理，不绕过站点限制；
NC 类候选单独目录隔离存放，未经 Owner 拍板不入正式 manifest。

---

## 附录 A：候选渠道预研（执行层输入，非承诺）

| 渠道 | License 政策 | 适用类别 | 可行性备注 |
| --- | --- | --- | --- |
| Freesound.org | 逐条 CC0/CC-BY 标注齐全 | 全部六类 | 最大候选池；部分下载需登录（登录墙素材转人工）；API 需 key，优先页面直载 |
| Wikimedia Commons | CC 系列齐全 | 电话铃/警笛/环境音 | 可直载、元数据规范；音频存量中等 |
| Internet Archive | 公共域/CC 混合，逐条核对 | 老式电话铃/拨号音（历史录音） | 制式电话音色的独特来源；license 需逐条确认 |
| 自录（Owner 设备） | 无版权问题 | ambient/appliance/normal_speech | 质量最高、上下文最真实；作为检索失败时的兜底方案提出 |

## 附录 B：与本仓库既有资产的衔接

- Layer1 dataset v1（36 条）保留原位，职责转为管道机制连通性验证 + 回归防退化
  （#297 §4.2），不删除；
- Hard Negative Regression Set 在 v2 冻结时升级为「Layer1 全集 + Layer2 负类全集」；
- manifest v2 向后兼容 v1 字段，旧条目不迁移。