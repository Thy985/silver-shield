# Golden Case 接 Live：数据有效性诊断报告

> **方法**：实际跑通 Live runtime，用 WS 客户端收集真实事件，**用证据而非猜测**判断。
> **数据采集**：2026-08-17 · frame_index 0~485 · 两个 scenario 完整跑一遍
> **核心问题**：抽象层（5 认知骨架）已经设计好，但**接什么 case、case 是否真的能跑出预期数据**？

---

## 一、实测数据汇总（不是设计推演）

### 1.1 `delivery_courier_normal`（声称"白天正常来访"）

```yaml
# 800 条 WS 消息统计
- frame_tick: 312（每帧 1 次）
- evidence_delta: 311
- perception_delta: 163
- risk_delta: 14

# 感知事件类型（evidence_delta.perception_events）
- abnormal_dwell: 1
- visit_normal: 1
- visit_pending_verify: 1
- 其他类别: 0

# 检测类别（perception_delta.detections）
- person: 88 次
- 其他: 0

# 风险等级
- LOW: 4 个 warning（NOTIFY_FAMILY 1, MONITOR 1）
- MEDIUM/HIGH: 0

# Risk 转换序列
  f=90 transition=raised   levels=['LOW'] active_warnings=1
  f=91 transition=cleared  levels=[] active_warnings=1
  f=125 transition=None    levels=[] active_warnings=1
  f=171 transition=raised   levels=['LOW'] active_warnings=2
  ...

# Audio evidence: 0（整个 video 期间）
```

### 1.2 `cctv_surveillance_suspicious`（声称"夜间异常 → HIGH"）

```yaml
# 800 条 WS 消息统计
- frame_tick: 330
- evidence_delta: 330
- perception_delta: 131
- risk_delta: 9

# 感知事件类型
- visit_pending_verify: 1
- visit_normal: 1
- abnormal_dwell: 1

# 风险等级
- LOW: 2 个 warning（NOTIFY_FAMILY 1, MONITOR 1）
- MEDIUM/HIGH: 0   ← 实际上没有产生 HIGH 风险！

# Risk 转换
  f=82 transition=raised   levels=['LOW']
  f=83 transition=cleared
  f=194 transition=raised  levels=['LOW']
  ...

# Audio evidence: 0
```

### 1.3 实测关键结论

| 维度 | 实测结果 | 与设计预期对比 |
|------|----------|---------------|
| 风险等级封顶 | **LOW**（两个 scenario 都一样） | ❌ 严重不符——cctv_surveillance 设计意图是 HIGH |
| 5 认知之 ② "AI 看到什么" | 仅 abnormal_dwell / visit_normal / visit_pending_verify 3 类 | ⚠️ 不够丰富——"AI 看到了"叙事很单薄 |
| 5 认知之 ② 声学 | 0 audio events | ❌ 完全空——"声学状态变化"叙事无法构建 |
| 5 认知之 ③ "为什么值得关注" | 仅有 1-2 个 reason（"异常停留"、"未在白名单"） | ⚠️ 单薄——trigger chips 只有一个，强度条无数据 |
| 5 认知之 ④ ⑤ 决策 + 行动 | LOG_ONLY / NOTIFY_FAMILY，无社区工单 | ❌ 家属/社区闭环**从未真正跑出** |
| ⑥ Memory | 0 episodes（Live 不产记忆） | ✅ 设计上确实不产，但页面没显示"诚实"占位 |
| 声学状态变化 | 不存在 | ❌ telephone_risk 是这一叙事的唯一 Golden case，但未实测 |

---

## 二、当前 demo 数据**根本撑不起** 5 认知骨架

### 2.1 差距表（5 认知 × 现有数据 vs 设计目标）

| 认知 | 设计目标 | 现有数据能撑吗？ | 缺什么 |
|------|---------|-----------------|--------|
| ① 实时画面 | 视频流 + frame_tick + case_time + 当前 detection | ✅ 完全够 | — |
| ② AI 看到了什么 | 3-5 个感知事件（visual + audio） | ⚠️ 视觉够（88 person 检测），audio=0 | **音频事件** |
| ③ 为什么值得关注 | 3-4 个 trigger chips + 命中强度条 | ⚠️ 只有 1 个 chip | **多 trigger + 强度数值** |
| ④ 当前判断 | MEDIUM / HIGH 风险 + recommended_action | ❌ 封顶 LOW | **HIGH 风险触发** |
| ⑤ AI 做了什么 | 家属/社区/日志 三端任务卡 | ❌ 只有家属端触发 | **社区端闭环** |
| ⑥ 历史上下文 | 诚实占位"暂无历史" | ✅ 占位已有，但文案偏工程 | **文案优化** |

### 2.2 4 个数据维度断点

#### 断点 ①：风险等级**封顶 LOW**

**实测**：`delivery_courier_normal` 和 `cctv_surveillance_suspicious` 都只产出 LOW warning。

**根因**：
- `cctv_surveillance_suspicious` 设计的 `rule_overrides: {repeat_visit_count: 2}` + `realtime_risk.enabled: true` 意图触发 `HighRiskApproachRule`
- 但实测 perception_events 最多只有 `abnormal_dwell / visit_normal / visit_pending_verify` 三类，**没有"重复出现"事件**（visitor 没回访过）
- 触发 HighRiskApproachRule 需要 LongDuration + RepeatVisit + OddHour **同帧全中**——重复事件缺失

**这意味着**：当前 demo scenario 数据**无法**展示"系统识别 HIGH 风险 → 升级社区"。

#### 断点 ②：Audio evidence 全空

**实测**：两个 demo scenario 都**没有任何 audio events**。

**根因**：
- `config/demo/scenarios/*.yaml` 全部 6 个都没有 `audio_path` 字段（只有 `live_telephone_risk.yaml` 有，但需要 `--video` 注入）
- 即便有 audio_path，Golden Case 资产格式异构（IEEE_FLOAT 32-bit 不被支持）
- YAMNet class_names 缺失，模型退化为 stub，错认 telephone 为 distress_cry

**这意味着**：5 认知之 ② "声学状态变化"叙事**无法在 demo scenario 中展示**。

#### 断点 ③：Risk 转换闪烁

**实测**：每个 LOW warning 都触发 `raised → cleared` 1 帧切换（连续 2 帧）；active_warnings 在 cleared 时**丢失**（不保活）。

**根因**：
- `live_stream.js:715-718` setTimeout 1.2s 后清空 DOM
- `live_adapter.py:_accumulate` 的 `cur_risky = bool(self._last_risk_levels)` 是**单帧判断**——下一帧 risk_levels 空 → 立即 cleared
- 单帧 emit warning → 下一帧空 → cleared，循环

**这意味着**：UI 看不到"持续 LOW 风险"——只在 emit 那一帧有卡，下一帧消失。

#### 断点 ④：三端闭环未跑出

**实测**：所有 warning 的 `recommended_action` 只有 `LOG_ONLY` 和 `NOTIFY_FAMILY`，**没有 CREATE_COMMUNITY_TASK**。

**根因**：
- 风险等级封顶 LOW，rule_engine 不会产生 CREATE_COMMUNITY_TASK（需要 MEDIUM/HIGH）
- community_endpoint 在 `config/default.yaml:93` 配置但**未被实际触发**

**这意味着**：5 认知之 ⑤ "社区处置" 叙事**无法在 demo scenario 中展示**。

---

## 三、Golden Case 资产盘点（重新审视）

### 3.1 当前 4 个 Golden Case

| Case | 视频 | 音频 | manifest 事件声明 | AudioPerceptionEvent 5 类映射 | 当前能跑出 |
|------|------|------|-------------------|---------------------------|----------|
| **stranger_visit** | 33.3s, 1 段 | 8 wav (footsteps/doorbell/ambient) | doorbell_ring | ❌ 无映射 | 低 |
| **repeated_visit** | 3 幕各 9-10s | 10 wav (doorbell/footsteps) | **无 audio 声明** | — | 低 |
| **telephone_risk** | 15s 1 段 (raw.mp4) | 4 wav (ambient/voice_normal/voice_stressed/far_end) + 4 tts_raw | voice_stressed acoustic_progression | ❌ 无 AudioPerceptionKind 映射 | 极低 |
| **evidence_insufficient** | 3 幕 | — | — | — | 极低 |

### 3.2 关键问题

**Golden manifest 用的是"领域语义标签"（doorbell_ring / voice_stressed），不是 AudioPerceptionEvent 5 类（audio_telephone_persistent / audio_voice_raised ...）**。

两层语义之间**没有桥**：
- `doorbell_ring` 不知道映射到哪个 `AudioPerceptionKind`
- `voice_stressed` 不知道映射到 `audio_voice_raised`

这是**数据契约层面的缺口**，不是技术实现问题。

### 3.3 live_telephone_risk 当前能跑什么？

**实测路径**（根据 manifest）：
- video: `dataset/_canonical/video/telephone_risk_raw.mp4`
- audio: `data/golden/telephone_risk/audio_mix/case_b_mix.wav`（IEEE_FLOAT 32-bit，**当前不支持**）
- expected: LOW → RISK_SIGNAL（Case A → Case B）

**如果 IEEE_FLOAT 修了 + YAMNet class_names 修了**：
- 期待：case_b_mix 跑出 `audio_telephone_persistent` 事件（telephone 持续声）
- 期待：声学状态从 NORMAL → ATTENTION → AROUSAL → STRESS 4 阶段
- 期待：trigger chip 显示"电话交互"+ "声学状态变化"
- 期待：判决从 MONITOR 升级到 RISK_ESCALATION

**这是 5 认知骨架中唯一能展示 ② ④ ⑤ 三个认知的 case**。

---

## 四、5 认知骨架 × Case 可行性矩阵

| 认知 \ Case | delivery_courier | cctv_surveillance | golden_stranger_visit | golden_repeated_visit | golden_telephone_risk | golden_evidence_insufficient |
|------------|-----------------|-------------------|----------------------|----------------------|----------------------|----------------------------|
| ① 实时画面 | ✅ 视频流 OK | ✅ 视频流 OK | ✅ | ✅ 3 幕 OK | ✅ | ✅ 3 幕 |
| ② AI 看到 | ⚠️ 仅视觉 | ⚠️ 仅视觉 | ⚠️ 仅视觉 | ⚠️ 仅视觉 | ✅ 视觉+音频 | ⚠️ 仅视觉 |
| ③ 为什么 | ⚠️ 1 chip | ⚠️ 1 chip | ✅ doorbell+停留 | ✅ 跨日 pattern | ✅ 多 chip | ✅ 边界 case |
| ④ 当前判断 | LOW | LOW | LOW (设计意图 LOW) | 升级 (设计意图 NOTIFY_FAMILY) | MONITOR→RISK_ESCALATION | NOT_TRIGGERED |
| ⑤ AI 做什么 | 家属触发 | 家属触发 | 家属端 | 家属端 | 跨模态 SUPPORTS | 无 |
| ⑥ 历史 | 0 | 0 | 0 | **✅ 3 天/1 天/今天** | 0 | 0 |

**结论**：

- **golden_repeated_visit** 唯一能展示 **⑥ 历史** 叙事（"系统记得过去"）
- **golden_telephone_risk** 唯一能展示 **② 声学 + ③ 多 trigger + ④ RISK_ESCALATION** 叙事
- **golden_stranger_visit** 唯一能展示 **LOG_ONLY 决策路径**（"系统克制，不误报"）
- **golden_evidence_insufficient** 唯一能展示 **不报警** 叙事（"信息不足不升级"）

**没有一个 case 能完整支撑 5 认知骨架**——需要 **demo mode 顺序播放**多个 case。

---

## 五、关键诊断结论

### 5.1 你的问题"案例是否有效"的直接回答

| 案例 | 是否"有效"？ | 缺什么 |
|------|------------|-------|
| `delivery_courier_normal` | ⚠️ 部分有效 | 没有 audio、风险封顶 LOW，只能展示"系统在观察" |
| `cctv_surveillance_suspicious` | ❌ 设计意图失效 | rule_overrides 触发不到 HighRiskApproachRule（无 repeat_visit 事件）|
| `golden_stranger_visit` | ⚠️ 部分有效 | audio 加载失败（0 events），只能展示视觉 |
| `golden_repeated_visit` | ⚠️ 阶段性有效 | 需先有 ② memory 接入能力才能展示跨日 pattern |
| `golden_telephone_risk` | ✅ 最有价值 | 需修 IEEE_FLOAT 加载 + YAMNet class_names 才有 audio events |
| `golden_evidence_insufficient` | ✅ 设计意图清晰 | 视频+渲染已就绪，能展示"克制" |

### 5.2 关键洞察

**抽象层（5 认知骨架）已经设计好了**，但**数据流只有 ① 完全就绪**。

**②③④⑤ 的数据流**在 Golden Case 端存在但**链路未通**（audio 格式 / YAMNet 资源 / manifest 语义映射）。

**⑥ 的数据流**在 Live runtime 中**诚实不存在**（Live 不产 memory_episodes），但 Golden Case `repeated_visit` 有"跨日 prior_episodes"能力但**未接到 Live Adapter**。

### 5.3 接 Live 的瓶颈不是"加 scenario"，是"数据契约对齐"

Golden manifest 用的语义（`doorbell_ring` / `voice_stressed` / `phone_interaction`）与 Live 用的语义（`audio_telephone_persistent` / `audio_voice_raised`）**没有正式映射表**。

要么：
- **方向 A**：Live 端实现 manifest → LiveFrame 的转译层
- **方向 B**：Golden manifest 改用 AudioPerceptionEvent 5 类标准名

### 5.4 一个更深的判断

**当前 Live 页面是基于"实时 runtime 视角"设计的**——假设 detection/warning/audio 是"自然涌现"的事件流。

**但 Golden Case 的本质是"测试数据驱动"**——它不依赖 runtime 跑通 audio，**它能预先声明"在 frame X 应该有 audio_telephone_persistent 事件"**。

这两者之间有个**根本性矛盾**：

> Live runtime 可能在 frame 90 才发现"abnormal_dwell"，
> 但 Golden manifest 想说"frame 12 就有 phone_interaction"。

**这就是为什么我前面在第一份诊断说"Golden Case 接 Live 不只是技术问题"**——现在是数据哲学问题。

### 5.5 三种可能的产品化路径

| 路径 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. 纯实时** | Live 真的用 YAMNet 跑 audio，用 detector 跑 video | 真实 | audio 错认、风险封顶 LOW、5 认知不能完整跑通 |
| **B. 引导注入** | Live 接受 manifest 声明的"预定义事件"，runtime 检测只作为补充 | 叙事完整 | 需要改 Live Adapter 接受外部事件流，破坏 VM-1 |
| **C. Hybrid** | Live runtime 主跑，关键事件（声学状态、跨日 pattern）从 manifest 注入 | 平衡 | 需要精确设计"哪些注入、哪些不注入" |

**我的建议是 A + C**：先把 audio pipeline 真跑通（YAMNet 修好），telephone_risk 就能展示 5 认知骨架；其他 Golden Case 的"叙事完整性"用 manifest 文案增强（`_R._esc` 旁加 `_humanSummary`），不破坏 VM-1。

---

## 六、抽象与数据 mismatch 列表（按优先级）

### 🟠 Mismatch-A：风险等级封顶

**数据**：LOW everywhere
**抽象要求**：MEDIUM / HIGH 也要展示
**影响**：5 认知之 ④ ⑤ 无法跑通
**修复路径**：
- 选择 `golden_telephone_risk` 作为"高风险展示"案例（修复 audio 链路后）
- 临时方案：在 `_render_live_skeleton` 加注释"演示场景风险等级受 fixture 限制"

### 🟠 Mismatch-B：Audio 全空

**数据**：0 audio events
**抽象要求**：声学状态变化叙事
**影响**：5 认知之 ② 严重单薄
**修复路径**：
- 修 `source.py` 支持 IEEE_FLOAT（T2.2）
- 修 YAMNet class_names（T2.3）
- 这是阻塞 golden_telephone_risk 接入的根因

### 🟠 Mismatch-C：Risk 转换闪烁

**数据**：raised → cleared 1 帧切换
**抽象要求**：状态机稳定展示
**影响**：5 认知之 ④ 看起来像"动画"
**修复路径**：
- T1.2 修 `_applyRiskSignal` setTimeout 闪烁
- T2 修 `live_adapter` 的 `cur_risky` 判断逻辑（保活）

### 🟡 Mismatch-D：Memory 占位工程口吻

**数据**：`<div>🧠 历史记忆 · 当前案例无历史事件可供引用</div>`
**抽象要求**：诚实但不工程
**影响**：⑥ 区域
**修复路径**：文案改写为"本次通话暂无历史相关事件"

### 🟡 Mismatch-E：触发 chips 数量不足

**数据**：实测只有 1 个 chip
**抽象要求**：3-4 个 trigger chips
**影响**：5 认知之 ③ 显得空
**修复路径**：
- 这是 case 本身的局限——`cctv_surveillance_suspicious` 只有 1 个 reason
- 接入 `golden_telephone_risk` 后会有 2 个（phone_interaction + voice_stress）

### 🟢 Mismatch-F：Manifest 语义到 Live 语义映射缺失

**数据**：golden manifest 用 `doorbell_ring` / `voice_stressed` 等
**抽象要求**：需要映射到 `audio_telephone_persistent` 等 Live 5 类
**影响**：未来 Golden Case 接入需要这层桥
**修复路径**：
- 在 `live_adapter.py` 增加 `MANIFEST_TO_AUDIO_KIND` 映射表
- 或在 `telephone_risk/manifest.yaml` 改用 5 类标准名

---

## 七、给设计文档的修正

### 7.1 `DESIGN-golden-case-live-product.md` 需补充：

**§五 数据通路 → 加"Mismatch 表"**：

```
A. 风险等级封顶 → 用 golden_telephone_risk 修复 audio 链后能展示
B. Audio 全空 → 阻塞 golden_telephone_risk 接入
C. Risk 转换闪烁 → T1.2 / T2 已规划修复
D. Memory 占位工程口吻 → T1.6 改文案
E. 触发 chips 不足 → golden_telephone_risk 接入后改善
F. Manifest 语义映射缺失 → 需新增 M1 任务
```

### 7.2 `TASKS-golden-case-live-product.md` 需补充：

**新增 M 任务**（数据有效性 / 语义映射）：

- M1：Manifest 语义 → LiveFrame 5 类映射表
- M2：golden_telephone_risk 接入后端到端验证
- M3：golden_repeated_visit 三幕循环接入
- M4：golden_evidence_insufficient "克制"叙事
- M5：跨 case Demo Mode 顺序播放

### 7.3 5 认知骨架的"诚实降级"

不是每个 case 都能完整跑通 5 认知。需要为每个 case 设计"认知适配"：

| Case | ① | ② | ③ | ④ | ⑤ | ⑥ |
|------|---|---|---|---|---|---|
| delivery_courier | ✅ 实时画面 | ⚠️ 仅视觉 | ⚠️ 1 chip | LOW | 家属 | 空 |
| cctv_surveillance | ✅ | ⚠️ | ⚠️ | LOW | 家属 | 空 |
| golden_stranger_visit | ✅ | ⚠️ | ✅ doorbell+停留 | LOG_ONLY | 家属 | 空 |
| golden_repeated_visit | ✅ 3 幕 | ⚠️ | ✅ pattern | NOTIFY_FAMILY | 家属 | ✅ 跨日 |
| golden_telephone_risk | ✅ | ✅ 声学 | ✅ 多 chip | RISK_ESCALATION | 家属+跨模态 | 空 |
| golden_evidence_insufficient | ✅ 3 幕 | ⚠️ | ✅ 边界 | NOT_TRIGGERED | 无 | 空 |

**结论**：没有 single case 能完整支撑 5 认知。**Demo Mode 顺序播放**多个 case 是合理的产品化方案。

---

## 八、给 Owner 的关键建议

### 8.1 当前阶段最重要的事

**不是再加 case，是先打通 1 个 case 的完整 5 认知**。

推荐路径：
1. **修 IEEE_FLOAT + YAMNet class_names**（T2.2 + T2.3）—— 1 周
2. **接入 golden_telephone_risk 到 live**（T2.1）—— 0.5 天
3. **端到端验证 audio_telephone_persistent 事件**（T2.4）—— 0.5 天
4. **UI 适配 5 认知骨架**（T1.x）—— 1 周
5. **接入其他 Golden Case**（T3.2 + M1-M4）—— 1 周

### 8.2 不建议先做的事

- **不要做 Demo Mode 自动播放**——5 认知骨架都没跑通，自动播放只是空架子
- **不要急着接 repeated_visit**——memory 接入需要先有 Live Adapter 的 memory_episodes 通道
- **不要改 rule_overrides 期望产出 HIGH**——除非先用更大数据验证 repeat_visit 真的能产生

### 8.3 真实问题

> **抽象层（5 认知骨架）已经完整设计——但当前 6 个 scenario 都没能完整跑通它。**
>
> 不是"再加 case 就能解决"——是"必须先把 1 个 case 的 5 认知跑通，验证抽象层假设，再扩 case"。

---

*报告版本：v0.1 | 实际数据采集：2026-08-17 | 待 Owner 决策 §8.1 优先级*

---

## 九、补充：纠正之前对 telephone_risk 的过度推荐

### 9.1 之前判断的偏差

之前我说 "telephone_risk 是 5 认知骨架中唯一能展示 ② ④ ⑤ 三个认知的 case"——这个判断**有前提**（修复 audio 链），但我**没把"成本维度"和"价值维度"分开**。

**之前的真实推理路径**：
```
"② 声学是核心叙事"（错误前提）
  ↓
"只有 telephone_risk 有 audio"（事实）
  ↓
"telephone_risk 必须修好"（结论）
  ↓
"推荐 telephone_risk"
```

**这个推理的缺陷**：把"技术稀缺"误认为"产品价值"。

### 9.2 严谨 4 维度评估

| 维度 | telephone_risk | repeated_visit | stranger_visit | evidence_insufficient |
|------|---------------|----------------|----------------|------------------------|
| A. 已有数据能跑通认知数 | 1/6 | 1/6 | 1/6 | 1/6 |
| B. 修复成本 | 高（2 个外部依赖） | 中（1 个新增功能） | 低（仅 M1） | 低（仅 M1） |
| C. 修复后能跑通认知数 | 3/6（② ④ ⑤） | 2/6（⑤ ⑥） | 1/6 | 1/6 |
| D. 产品价值排序 | ③ nice-to-have | **① 核心**（跨日记忆）| ② 中（doorbell 通用）| ④ 低（边缘） |

**修正后的优先级**：
- **路径 B（M1 manifest 映射）**优先级最高：4 个 case 都受益，成本最低
- **路径 C（memory 通道）**其次：repeated_visit 是 ⑥ 唯一能跑通的 case
- **路径 A（修 audio 链 + telephone_risk）**第三：虽认知覆盖多，但成本高且依赖外部

### 9.3 "推荐 telephone_risk" 的真实适用场景

如果你只修一条路径，**该选哪一条**取决于你要回答的产品问题：

- **"产品是否利用了多模态？"** → 选 A（telephone_risk）
- **"产品是否记得过去？"** → 选 C（repeated_visit）
- **"4 个 Golden case 都能跑通自己的设计意图吗？"** → 选 B（manifest 映射）

**之前我说"推荐 telephone_risk"是因为它回答了"多模态"**——但产品化的核心问题应该是**先让 4 个 case 都跑通**（路径 B），然后再选一个 case 重点深耕。

### 9.4 修正后的决策建议

**不要先选一个 case 推荐**。而是：

1. **先做 B（M1）**：让 4 个 golden case 都能接入并跑出设计意图
2. **再选 case**：基于 B 完成后的事实数据，决定哪个 case 优先深耕

这个修正**反转了原来的"先 telephone_risk 修 audio 链"建议**。

---

*报告版本：v0.2 | 补充 §9 修正 | 待 Owner 决策*