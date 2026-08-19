# 黄金案例集 · 展示层设计 v2（Golden Case Viewer）

- **状态**：规划（v2：Owner 11 点评审合入——声学语义修正 / 命题一句话 / 能力谱系 / 案例组合设计）
- **日期**：2026-08-16（v1 → v2）
- **决策者**：Owner
- **相关**：docs/design/golden-case/DESIGN-golden-scenario-set.md（数据准备 v2）/ docs/design/golden-case/GOLDEN-CASES-USAGE.md（接入清单）/ docs/design/golden-case/DESIGN-demo-v2-product-restore.md（P0 产品链）/ ADR-0036（统一 Case Viewer）

---

## 0. 核心原则（v2 凝练 · Owner 认可）

> **Case Viewer 不负责解释系统为什么"认为"某事发生，而负责把系统已经产生的 Evidence、Decision、Memory 和 Action 以与 Case 命题一致的方式可视化。**

配套两条硬约束：
1. **先冻结命题，再冻结展示**：每个 Case 先冻结"到底要证明什么"，再冻结"允许 EvidenceProjection 展示什么"——**Viewer 不得反向驱动业务语义**；
2. **展示层不生成证据（VM-9）**：Viewer 是"证据解释器"，不是第二套推理系统。Decision Trace 的事实与 Viewer 展示是**同一事实的两个视图**。

---

## 1. 统一展示框架 + 案例组合导航（v2 新增）

### 1.1 单 case 布局

```
┌─────────────────────────────────────────────────────────┐
│ Case Header                                              │
│   · Case 名 + 命题一句话（This case demonstrates: …）      │
│   · 能力徽章（能力谱系投影）                              │
├─────────────────────────────────────────────────────────┤
│ Case Video（主轴 · media_alignment 对齐）                 │
├─────────────────────────────────────────────────────────┤
│ 当前风险 · 为什么 · 系统行动                               │
├─────────────────────────────────────────────────────────┤
│ 案例专属组件（按 case 命题注入，见 §3）                    │
├─────────────────────────────────────────────────────────┤
│ Evidence Timeline（统一时间轴 · 每关键 Evidence 可反向    │
│   定位到 Case Time：点击 → 视频跳转 + 音频播放 + 高亮）     │
└─────────────────────────────────────────────────────────┘
```

### 1.2 通用设计语言：任何关键 Evidence 都能反向定位到 Case Time

```
Fact ──→ Timestamp ──→ Video/Audio
```

- **门铃（stranger_visit）**：14.2s 高亮 → 点击视频跳 14.2s；
- **声学变化（telephone_risk）**：08.4s 声学指标变化 → 点击播放 08.4s；
- **决策/处置（repeated/high_risk）**：决策节点/Resolution 节点 → 点击定位对应时刻。

这是整个 Viewer 建立信任的通用语言，不是单 case 装饰。

---

## 2. 六 Case 产品命题 + 命题一句话（Case Header 组件）

| Case | 命题一句话（Case Header 展示） | 真正证明的能力（能力谱系） |
| --- | --- | --- |
| **benign** | "系统能识别正常环境并解释为什么没有触发。" | 系统能够"不做事"（TN with reason） |
| **stranger_visit** | "系统能从弱视觉事件中发现值得关注的异常。" | 系统能够发现弱异常 |
| **repeated_visit** | "系统能使用历史事件改变当前判断。" | 系统能够使用历史 |
| **telephone_risk** | "系统能用声学变化补充视觉证据，而不是仅凭电话事件误报。" | 系统能够融合新模态证据 |
| **high_risk** | "系统发现风险后能够完成通知与处置闭环。" | 系统能够完成行动闭环 |
| **ambiguous** | "系统面对不完整证据时保持克制，不将不确定性强行升级成风险。" | 系统能够处理不确定性而不过度报警 |

> **v2 关键**：六个 Case 是**能力谱系**，不是"风险越来越高"的单轴风险基准（见 §5）。Viewer 视觉语言避免设计成 LOW→MEDIUM→HIGH 的单调风险条。

---

## 3. 案例专属组件（v2 修正）

### 3.1 Memory Timeline（记忆时间线 · repeated_visit · 最高价值）

```
3 days ago ────── 1 day ago ────── today
   ep_001           ep_002          ep_003
  NORMAL          MONITOR        NOTIFY_FAMILY
        └──────── memory_ref: [ep_001, ep_002] ────────┘
        Decision Trace: uses ep_001 · uses ep_002
```

- 从"系统说自己有记忆"（`Memory: 3 episodes` 无证明力）→ **"系统能证明自己使用了过去的信息"**；
- 与 CI 断言（Decision Trace 引用历史 Episode，G0-3 `historical_record_ids`）**同源**，两视图同一事实。

### 3.2 Acoustic Change 可视化（telephone_risk · v2 声学语义修正）

**v2 修正**：`NORMAL→ATTENTION→AROUSAL→STRESS` 与 `voice_stress_score` 是**解释层状态**，不是原始声学事实；Viewer 不得自行把物理指标推导成 `STRESS`。

**事实层（EvidenceProjection 承载）**：
```json
{
  "acoustics": {
    "vad_ratio": 0.81,
    "rms": 0.42,
    "speech_rate": 3.4,
    "f0_change": 0.24,
    "energy_change": 0.21,
    "speech_rate_change": 0.28,
    "pause_irregularity": 0.39,
    "voice_instability": 0.31
  },
  "acoustic_change_score": 0.72
}
```
- `acoustics` 是**纯物理声学指标**（已有 `AudioAcoustics.vad_ratio/rms/speech_rate`，扩展 `f0_change` 等；源自 `AudioSegmentEvent`）；
- `acoustic_change_score`（**替代 `voice_stress_score` 命名**）：声学变化评分，**不是心理压力评分**——"F0↑/语速↑/能量↑/jitter↑"只能说明 **vocal arousal / vocal instability 增强**，不能证明"老人害怕了"；
- `acoustic_state`（如 `ELEVATED`）：**由 CI fixture 明确给出的解释层事实**，Viewer 只展示，**不推导**。

**展示**：
```
[acoustic_change_score 0.72]  [f0 ↑] [语速 ↑] [能量 ↑] [jitter/shimmer ↑]
状态（CI 给定）：NORMAL ──→ ELEVATED
08.4s 声学变化 → 点击播放 08.4s（§1.2 反向定位）
标注："声学状态变化 → 风险信号，非诈骗判定，非心理压力判定"
```

### 3.3 Suppression Reason（不误报原因卡 · benign vs ambiguous 拉开）

| | benign（TN） | ambiguous（证据不足） |
| --- | --- | --- |
| 链路 | NO EVENT → NO RISK → NO ACTION | WEAK SIGNAL → LOW CONFIDENCE → INSUFFICIENT → MONITOR |
| 本质 | 没有异常，所以不触发 | **有异常信号，但证据不够**，所以仍不触发 |
| 命题 | 系统能识别正常环境 | 系统处理不确定性而不过度报警 |

> **"没报警"不是一个状态，而是有原因的**——Suppression Reason 把两个 Case 的原因显式化。

### 3.4 决策升级链 / 跨模态徽章（轻量组件）

- Decision Escalation Chain（repeated_visit）：NORMAL→MONITOR→NOTIFY_FAMILY；
- Cross-modal Badge（telephone_risk）：来自 `cross_modal_links`（既有 graph 组件，补充首屏徽章位）。

---

## 4. 数据流（先冻结命题 → 再冻结展示）

```
data/golden/<case>/manifest.yaml（时间真相源 + prior_episodes + alignment + expected）
   ↓ G0-2 转化（冻结命题 + 冻结允许展示字段）
golden CI fixtures（scenario yaml：memory.prior_episodes / acoustics / acoustic_state / expected）
   ↓ G0-3/G0-4
build_trusted_case → canonical（memory 落库 / decision trace / acoustics / audio / cross_modal）
   ↓ loader 投影
EvidenceProjection（memory.prior_episodes + acoustics 扩展 + acoustic_state[CI 给定]）
   ↓ render（VM-11 面板编排）
Case Viewer（Memory Timeline / Acoustic Change / Suppression / 闭环 / 锚点）
```

**约束**：任何展示字段都必须先在 fixture 事实层定义（先冻结命题）——Viewer 缺字段时**不占位编造**（VM-7 显式缺失）。

---

## 5. 案例组合设计：能力谱系导航

### 5.1 顶层：Golden Case 导航 = 能力谱系（非风险谱）

```
数据/golden/ 入口（Case Viewer 旗舰页）
┌────────────────────────────────────────────────┐
│ 能力谱系（6 case 一排，按能力标签组织）           │
│                                                │
│  [不做事]  [弱异常]  [历史]  [跨模态]  [闭环]  [克制]│
│   benign   stranger  repeat  phone   high   ambi │
│                                                │
│ （点击任一 → 进入单 case 展示层）                 │
└────────────────────────────────────────────────┘
```

- 视觉语言：**能力标签**（不做事/弱异常/使用历史/融合模态/完成闭环/处理不确定性），**不是** LOW→MEDIUM→HIGH 风险条；
- 每 case 卡片：命题一句话 + 覆盖矩阵徽章（Memory/Audio/CrossModal/Workflow）。

### 5.2 演示顺序（两种组织，Story 可切换）

1. **能力谱系顺序**（默认 · 适合"系统能力全貌"）：benign → stranger_visit → repeated_visit → telephone_risk → high_risk → ambiguous——从"不做事"到"克制"，展示完整能力梯度；
2. **对比顺序**（适合"一个点讲透"）：
   - benign ↔ ambiguous：**两种"不报警"**（没有异常 vs 证据不足）并排对比；
   - stranger_visit ↔ repeated_visit：**历史的价值**（首访 vs 三天后，同一访客）；
   - telephone_risk case_a ↔ case_b：**多模态的价值**（仅视觉 vs 视觉+声学）。

### 5.3 单页 vs 多页

- **默认：单页纵向**（现有多场景堆叠渲染复用）+ **顶部能力谱系导航条**（点击滚动/切换）；
- **演示模式（P1 Story）**：按 Story Script 顺序引导（Story 不驱动 Runtime）；
- **P0 Overall Gate**：high_risk 一条链（Live Video → … → Resolution）在同一页面走通。

### 5.4 与初版 demo 的关系

- **不搬 legacy HTML**：初版 Dashboard（三端视图/检测框/实时流）的**能力**重新实现到 Case Viewer（P0-1~P0-3），UI 结构不复刻；
- **继承的是"评委三问 + 故事节奏"的产品精神**（ADR-0017 §2.4），Golden Case 的命题一句话 + 能力谱系正是这三问的显式化。

---

## 6. 验收

- **每 case**：Case Header 命题一句话可见；关键 Evidence 可反向定位 Case Time（点击跳转+播放+高亮）；
- **VM 合规**：所有组件只投影 EvidenceProjection；Viewer 不推导 `acoustic_state`/`STRESS`（断言 render 不生成解释层字段）；
- **G0-3 绑定**：Memory Timeline"uses ep_001/ep_002"与 CI Decision Trace 断言（`historical_record_ids`）同源；
- **组合**：能力谱系导航可见 6 能力标签；benign↔ambiguous 对比可切换；
- **对齐**：`event_windows` 超容差 fail-closed。

---

## 7. 一句话总结

> **Viewer 是证据解释器，不是第二套推理系统：先冻结每个 Case 的命题，再冻结 EvidenceProjection 允许展示什么；六 Case 按能力谱系组合（不做事/弱异常/历史/跨模态/闭环/克制），每关键 Evidence 都能反向定位到 Case Time——这才是"可被验证、可被解释"的黄金案例展示。**
