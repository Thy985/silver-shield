# Case Viewer 产品化总原则（叙事层架构）

- **状态**：冻结（Owner 2026-08-16 评审确认，作为下一轮展示层改造的总纲）
- **定位**：升级 `DESIGN-golden-case-viewer.md`（后者定组件，本文定 **Case 呈现模型**）
- **一句话**：**Case 是一个可被人理解的产品事件，不是一组证据集合。**

---

## 0. 总原则（本轮冻结，不可违背）

> **停止增加信息，开始删除信息。**

当前 Viewer 的问题不是"缺面板、缺 Story"，而是把 Case 当成**证据集合**（Evidence 很丰富 → 人不知道看什么 → 人也不知道为什么看 → 只能自己推理产品价值）。这是典型的：

```
Engineering completeness > Product comprehensibility
```

**反向动作**：不再堆 Evidence / fingerprint / CI assertion / graph / panel；改为把已有能力**重组为给人看的叙事**。重写 runtime / EvidenceProjection / CI 是错误方向（事实链已扎实，缺的是叙事层）。

**验收标尺**：评委在 **30 秒内**明白——这个 Case 证明了什么、证据何时出现、系统为什么做出这个决定、最后有没有闭环。

---

## 1. 信息层级架构（从"仪表盘"到"事件播放器"）

```
┌─────────────────────────────────────────────┐
│ REPEATED VISIT                              │  一级：Case Header
│ This case demonstrates:                     │      （命题一句话 + ▶ Play Case）
│ 系统能够利用历史事件改变当前风险判断。      │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│                 CASE TIME                   │  二级：Case Time 统一主轴
│ 00 ───────────────●─────────────── 30s     │      （唯一时间轴，驱动全部媒体）
└─────────────────────────────────────────────┘

┌───────────────┐  ┌─────────────────────────┐
│ Video         │  │ What is happening?      │  三级：正在发生什么
│               │  │ Unknown visitor         │      （视频 + 人话解释，非证据堆砌）
│               │  │ Previous visit detected │
└───────────────┘  └─────────────────────────┘

┌─────────────────────────────────────────────┐
│ Evidence → Decision → Action                │  四级：因果链
│ EP001 → EP002 → CURRENT                    │      （历史 → 当前 → 升级）
│       NORMAL → MONITOR → NOTIFY_FAMILY      │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│ Why?                                        │  五级：人话解释
│ "今天的事件与过去两次访客事件具有连续性。" │
└─────────────────────────────────────────────┘
```

**技术证据（fingerprint / provenance / CI / gate / graph / raw evidence）统一退到**
**`Technical Evidence / Audit Details`（折叠区）**——它们是"系统可信"的自证，不是
"Case 在讲什么"的叙事。

> **调和说明（AC-7）**：`provenance_kind`（SIMULATED / REAL_SENSOR）保留为一等视觉
> （诚实性承诺，非自证堆砌）；但 fingerprint 哈希、gate verdict、CI 徽章等**工程细节**
> 一律移入 Audit Details。删除的是"堆料"，不是"诚实"。

---

## 2. 一级架构元素：Case Header / Product Question

不是 Header 文案，是**整个 Viewer 的顶层结构**——所有组件都围绕它服务：

```
CASE
  ↓
This case demonstrates:（30 秒讲清 Case 要证明什么）
  ↓
Evidence sequence → Decision → Outcome（证明过程）
```

- 六 Case 命题一句话已冻结（DESIGN-golden-case-viewer §2）：benign=为什么没报警 /
  stranger=弱异常 / repeated=历史改变判断 / telephone=多模态补充视觉 / high_risk=闭环 /
  ambiguous=克制不误报；
- 数据源：manifest（product_question）+ 场景声明，**不新造事实**（VM-1 只投影）；
- 验收：评委看到 Header 就知道这个 Case 在讲什么，不需要自己推理。

---

## 3. 二级架构：Case Time 统一主轴（底层交互模型，非 UI）

**最大结构性缺口**。当前五个时间系统各自解释自己：

```
Video Time  |  Audio Time  |  Timeline Time  |  Memory Time  |  Decision Time
```

产品化后必须是**唯一时间轴**：

```
                 CASE TIME
                     │
       ┌─────────────┼─────────────┐
       ↓             ↓             ↓
     Video          Audio       Evidence
       │             │             │
       └─────────────┼─────────────┘
                     ↓
                  Decision
                     ↓
                   Action
```

- 所有视觉元素回答"**此刻发生了什么**"（Case Time = t 时，视频帧/音频轨/证据节点/
  记忆/决策全部对齐到 t）；
- 代码基础已存在：`media.js` MediaPlayer 已有 `seekByTime`（正向）/`seekByEvidence`
  （反向）双轨同步 + videoEl 桥接——**扩展为多轨（+audio track + memory node）**，
  非新造；
- 边界：Case Time 是**展示层映射**（VM-11），不改 EvidenceProjection 事实模型。

---

## 4. Evidence Replay（替代 Storytelling）

不是 `Step 1 → Step 2 → Step 3` 的传统 Story 条（那是 UI）；是**播放器时间推进后
事件自动涌现**：

```
00:00  正常环境
05:20  出现陌生人
14.2s  门铃事件
18.7s  事件确认
       Decision: MONITOR
```

- 画面、音频、时间线、Evidence、Decision **被同一个 Case Time 驱动**；
- 播放 = 证明过程重放；暂停/拖动 = 任意时刻对齐；
- 这才是初版 demo"5 分钟故事"的正确复活方式——**故事是 Case Time 的自然产物**，
  不是叠加的节拍条。

---

## 5. Suppression Reason / Negative Capability（升 P0）

成熟风险系统必须证明的不是只会"报警"，而是**知道什么时候不应该做什么**：

| Case | 语义 |
|---|---|
| benign | 系统观察到了正常状态，因此**没有理由**生成风险事件（TN，非"什么都没发生"） |
| ambiguous | 系统确实观察到异常，但**证据不足以支持升级**，因此保持克制（MONITOR） |

- Suppression Reason 卡：首屏解释"为什么没报警"（低置信 0.31 / 遮挡可见比 0.35 /
  背光剪影 / 无事件），数据已在 canonical（suppress_reasons）；
- **升 P0**：这是差异化最强的能力（多数 AI 项目只展示"识别到了/报警了"）；
- 呈现：benign/ambiguous 的 Case Header 下直接展示"系统为什么保持克制"，避免
  空面板观感。

---

## 6. telephone_risk 声学定位（不暗示心理状态）

Viewer 展示**声学状态变化**，不展示"老人害怕/电话诈骗"：

```
CASE TIME
0–5s    baseline
5–8s    speech pattern change
8–11s   pitch/energy change
11–15s  sustained acoustic deviation
→ 展示: Acoustic state changed（声学状态变化）
→ 不展示: 老人进入恐惧状态
```

- 事实层：物理声学指标（f0/energy/speech_rate 等，EvidenceProjection `AudioAcoustics`
  已承载）+ `acoustic_change_score` + CI 给定的 `acoustic_state`；
- Viewer **绝不推导** STRESS / 诈骗（VM-9 无 ASR/LLM；守模块边界）；
- 跨模态呈现：`视觉 phone interaction + 音频 speech detected/acoustic deviation
  → CROSS_MODAL: SUPPORTS → Decision: continue observation / elevated attention`，
  而不是 `电话 + 声音 = 诈骗`。

---

## 7. Live 诚实性（真 Live 或改名，二选一）

"Live"不是视觉标签，是**用户承诺**（现在→正在发生→我能看到→我能操作）。
当前静态视频 + 实时按钮 = 制造虚假实时感。

| 方案 | 条件 |
|---|---|
| 真 Live | 实现 P0-2 LiveFrameSource（实时帧 → 实时事件 → 实时决策） |
| 非 Live（推荐先做） | 老实改名 **Replay / Scenario / Case Playback**，绝不用 Live 包装 Replay |

P0-2（LiveFrameSource）本就是计划内产品能力，但**在叙事层完成前不做**——先用
Replay 语义，避免虚假承诺。

---

## 8. 角色视角（降 P2）

家属/社区分屏有价值，但在"Evidence → Case Time → Decision → Action"没做好之前
先做角色视角 = 增加 UI 而非增加价值。**降 P2**，等叙事层闭环后再做。

---

## 9. 重排实施顺序（替换旧 P0/P1/P2 表格）

| 优先级 | 项 | 本质 | 依赖 |
|---|---|---|---|
| **P0-1** | Case Header / Product Question | 一级架构元素（30 秒讲清） | 无（设计已冻结） |
| **P0-2** | Case Time 统一主轴 | 底层交互模型（唯一时间轴） | media.js 双轨同步扩展 |
| **P0-3** | Evidence Replay | 播放器驱动事件涌现（非 Story 条） | P0-2 |
| **P0-4** | Suppression Reason | Negative Capability（差异化） | 无（数据已在 canonical） |
| **P1** | Memory Timeline 嵌入 Case Time | 历史记忆自然嵌进统一时间轴 | P0-2 |
| **P1** | Acoustic State（telephone_risk） | 声学状态变化呈现 | P0-2 + golden fixture |
| **P2** | Case Switcher / Presentation Mode | 解 6 Case 1.29MB 堆叠 | P0 全 |
| **P2** | 真 Live（LiveFrameSource）或永久 Replay 语义 | 诚实性 | P0 全 |

> Memory Timeline / Acoustic State 不是被降级，而是**必须在 Case Time 之后**——
> 没有统一时间轴，它们只是孤立的漂亮组件；有了 Case Time，它们才是叙事的一部分。

---

## 10. 边界红线（全程不可破）

- **VM-1**：只投影 EvidenceProjection，不生成新证据（命题一句话/Suppression Reason
  数据均来自既有事实字段）；
- **VM-6**：Projection 不回写（交互状态是 UI 态，不进事实模型）；
- **VM-9**：无 ASR/LLM（acoustic 只展示状态变化，不推导心理/诈骗）；
- **VM-11**：Case Time / 面板编排 / 信息层级全是**展示编排**，不新增事实；
- 数据源不变：manifest + canonical + 场景声明，**零 runtime 改动**（叙事层是
  展示侧的纯重组）。

---

## 11. 验收（P0 四件完成后）

一个评委，一条路径，30 秒：

```
打开 Case → Header 讲清命题 → ▶ Play → Case Time 推进
→ 事件涌现 → Evidence → Decision → Action 自动跟随
→ benign/ambiguous 展示"为什么克制"
→ 全程不需要滚动找、不需要自己推理、不需要看指纹
```

技术证据（fingerprint/provenance/CI/gate/graph）只在需要审计时展开。
