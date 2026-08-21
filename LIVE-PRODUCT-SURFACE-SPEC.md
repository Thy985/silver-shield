# Live Product Surface Spec — 产品表面规格

> **设计时间**: 2026-08-21  
> **状态**: Draft v2.0  
> **核心架构**: 统一壳层 + 场景叙事层  
> **约束**: 禁止伪造实时感、Capability 成熟度分级、Evidence Continuity > Event Count

---

## 一、架构总览

```
                    SilverShield Live Shell
                          │
         ┌────────────────┼────────────────┐
         ↓                ↓                ↓
  telephone_risk      CCTV        repeated_visit
  Audio-first         Vision-first   Memory-first
  多模态/声学优先       视觉/风险优先     记忆/历史优先
```

### 两层架构原则

| 层级 | 职责 | 是否场景共享 |
|------|------|-------------|
| **Shell（产品骨架）** | L0/L5 实时状态、统一风险表达、统一交互、统一 Details、统一视觉语言 | ✅ 所有场景共享 |
| **Scenario Narrative（叙事层）** | "正在发生什么"的核心叙事 | ❌ 每个场景独立定义 |

---

## 二、统一壳层（Shared Shell）

### 2.1 Shell 组成

所有场景共享以下 6 个元素，位置和样式固定：

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  [L5] Provenance Banner      ● LIVE · 受控演示输入 · 非 7×24 真实设备            │
├─────────────────────────────────────────────────────────────────────────────────┤
│  [L0] Runtime Presence       🟢 LIVE  │  📹 视频正常  │  🔊 音频正常  │  延迟 120ms │
├─────────────────────────────────────────────────────────────────────────────────┤
│  [Tabs] ① 风险发现  ② 家属确认  ③ 社区处置                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │              [Scenario Narrative Layer — 场景专属]                          │  │
│  │                     （每个场景布局不同）                                      │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│  [Unified Risk State]   风险: MONITOR → RAISED · 持续 3.2s · 声学状态变化        │
├─────────────────────────────────────────────────────────────────────────────────┤
│  [Unified Action]       系统建议：继续观察 · LOG_ONLY                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│  <details> [L5] 详细证据（Timeline + Evidence Graph + Gate）                      │
│  </details>                                                                       │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Shell 元素详细规范

#### L0: Runtime Presence（统一）

| 属性 | 值 |
|------|-----|
| **位置** | Header 下方，固定条 |
| **显示** | LIVE badge + 三重输入健康度（Connection / Video / Audio） |
| **数据源** | `frame_tick` (video) + `audio_segment_tick` (audio) + WebSocket heartbeat |
| **更新频率** | continuous（帧级 ~8fps，音频段级 ~1-2s） |
| **状态** | 🟢 LIVE / 🟡 降级 / 🔴 中断 / ⚪ 连接中 |

**降级分层显示**（禁止隐藏）：

```
✅ 正常:  "● LIVE · 视频正常 · 音频正常 · 延迟 120ms"
🟡 视频中断: "● LIVE · ⚠ 视频输入中断 · 音频正常"
🟡 音频中断: "● LIVE · 视频正常 · ⚠ 音频输入中断"
🔴 都中断:  "● LIVE · ⚠ 传感器输入中断"
⚪ 连接中:  "○ 连接中..."
```

**禁止行为**：
- ❌ 用静态动画模拟活动
- ❌ 隐藏输入中断状态
- ❌ 伪造帧计数（`frame_index` ≠ `runtime_tick_count`）

---

#### L5: Provenance Banner（统一）

| 属性 | 值 |
|------|-----|
| **位置** | 页面最顶部 |
| **显示** | 徽章 + 可展开证据链来源 |
| **数据源** | `provenance_kind` (SIMULATED / REAL_SENSOR / FIXTURE) |
| **更新频率** | session 生命周期 |
| **渲染函数** | `render.py: _render_provenance_banner` |

**文案（Owner 决策）**:

```yaml
SIMULATED:  "● GOLDEN CASE · SIMULATED · 程序化场景 · 可复现"
REAL_SENSOR: "● LIVE · 受控演示输入 · 非 7×24 真实设备 · 演示素材"
FIXTURE:    "● FIXTURE · 固定测试素材 · 非实时"
```

---

#### 统一风险状态表达（Unified Risk State）

| 属性 | 值 |
|------|-----|
| **位置** | Shell 底部，固定条 |
| **显示** | 风险状态徽章 + 跃迁原因摘要 |
| **数据源** | `risk_delta` → `RealTimeRiskEvaluator` |
| **状态序列** | MONITOR → RAISED → CLEARED |

**Evidence Continuity 原则**：

```
❌ "8 个风险信号事件"
✅ "风险状态：观察 → 关注（声学状态变化 · 3.2s）"
```

---

#### 统一行动卡片（Unified Action）

| 属性 | 值 |
|------|-----|
| **位置** | Shell 底部，风险状态下方 |
| **显示** | 系统建议 + 执行状态 |
| **数据源** | `commandMap` + `state_update` |
| **命令类型** | LOG_ONLY, MONITOR, NOTIFY_FAMILY, ESCALATE_COMMUNITY |
| **JS 处理** | `live_actions.js: _render` |

---

#### 统一 Details / 审计入口

| 属性 | 值 |
|------|-----|
| **位置** | Shell 最底部，折叠 |
| **展开内容** | Evidence Timeline + Evidence Graph + Provenance Detail + Gate |
| **数据来源** | EvidenceProjection（全量） |
| **可回溯** | 每个节点 → media_ref + seek_position |

---

#### 统一视觉语言

```css
/* Risk Levels */
--risk-monitor: #64748b;    /* 灰色 - 观察中 */
--risk-raised: #d97706;     /* 橙色 - 风险关注 */
--risk-critical: #dc2626;   /* 红色 - 高风险 */
--risk-cleared: #16a34a;    /* 绿色 - 风险解除 */

/* Modality Colors */
--modality-vision: #4a90d9; /* 蓝色 - 视觉 */
--modality-audio: #9b59b6;  /* 紫色 - 音频 */
--modality-action: #d68910; /* 金色 - 行动 */
--modality-memory: #0891b2; /* 青色 - 记忆 */

/* Status Indicators */
--status-live: #16a34a;     /* 绿色 - 实时 */
--status-degraded: #d97706; /* 橙色 - 降级 */
--status-offline: #dc2626;  /* 红色 - 离线 */
```

---

## 三、telephone_risk — Audio-first 叙事层

### 3.1 核心叙事流

```
实时感知
   ↓
🔊 正在听（L1 Audio Trust）
   ↓
声音强度变化（L1 Audio Perception — Waveform）
   ↓
电话相关声音 + 声学状态跃迁（L2 Acoustic State）
   ↓
NORMAL → ATTENTION → AROUSAL → STRESS
   ↓
风险状态（L2 Risk Transition）
   ↓
证据回听（L5 Verifiability）
```

### 3.2 布局全貌

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L5] ● LIVE · 受控演示输入 · 非 7×24 真实设备                                         │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [L0] 🟢 LIVE  │  📹 视频正常  │  🔊 音频正常  │  延迟 120ms  │  Session: 00:15      │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [Tabs] ① 风险发现  ② 家属确认  ③ 社区处置                                             │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │  ① 主视觉区（Audio-first 布局）                                                   │ │
│  │                                                                                 │ │
│  │  ┌─────────────────────────┬─────────────────────────────────────────────────┐  │ │
│  │  │  📹 视频传感器           │  🔊 音频传感器（大尺寸波形区）                     │  │ │
│  │  │  [LIVE badge]            │  [ACTIVE badge · 🔊 脉冲驱动于真实 audio_buf]     │  │ │
│  │  │                         │  ┌─────────────────────────────────────────────┐  │  │ │
│  │  │          Case Video     │  │  Waveform (RMS 分段值)                      │  │  │ │
│  │  │          (RTSP/HLS)     │  │  ▂▃▅▆▇▆▅▃▂  ~100ms 粒度                     │  │  │ │
│  │  │                         │  └─────────────────────────────────────────────┘  │  │ │
│  │  │  Overlay: 👤 1人在场    │  点击回放音频证据                                   │  │ │
│  │  │  case_time=00:12.4s     │                                                     │  │ │
│  │  └─────────────────────────┴─────────────────────────────────────────────────┘  │ │
│  │                                                                                 │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  ┌─────────────────────┬─────────────┬─────────────┬──────────────────────────────┐ │
│  │② 感知理解           │③ 风险状态    │④ 系统行动    │⑤ 证据详情                  │ │
│  │                     │             │             │                              │ │
│  │ 👤 1人在场(持续12.4s)│  MONITOR     │  LOG_ONLY  │  - video: 449 帧            │ │
│  │ 🔊 持续电话声        │  → RAISED    │  NOTIFY    │  - audio: 9 段              │ │
│  │ 🔊 哭腔/求助         │  ↓ CLEARED   │  ESCALATE  │  - acoustic: 4 阶段         │ │
│  │                     │             │             │                              │ │
│  │ 声学状态进度条       │  触发规则:    │  执行状态:   │                              │ │
│  │ NORMAL→ATTENTION    │  acoustic_   │  pending/   │                              │ │
│  │ →AROUSAL→STRESS    │  state_change│  done       │                              │ │
│  │ [15s 连续]          │  + telephone │             │                              │ │
│  └─────────────────────┴─────────────┴─────────────┴──────────────────────────────┘ │
│                                                                                 │
│  [统一 Shell] 风险: RAISED · 持续 3.2s · 声学状态变化 + 电话交互                     │
│  [统一 Shell] 系统建议：继续观察 · LOG_ONLY                                         │
│  <details> [L5] 详细证据（Timeline + Evidence Graph + Gate）                        │
│  </details>                                                                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 各 Surface 详细规格

#### L0: Runtime Presence（Shell 固定）

| 属性 | 值 |
|------|-----|
| **位置** | Shell 固定条 |
| **数据源** | `frame_tick` + `audio_segment_tick` + WebSocket heartbeat |
| **字段** | `frame_index`, `case_time`, `server_ts`, `audio_available` |

---

#### L1: Person Perception（P1 — 辅助）

| 属性 | 值 |
|------|-----|
| **位置** | ② 感知理解 顶部 |
| **数据源** | `perception_delta` → YOLO11n Person detection |
| **Evidence Continuity** | "持续 1 人在场，持续时间 12.4s"（非"449 帧检测"） |

---

#### L1: Audio Trust（P0）

| 属性 | 值 |
|------|-----|
| **位置** | ① 主视觉区 AUDIO SENSOR 卡片头部 |
| **数据源** | `audio_available` (bool) + `vad_ratio` |
| **显示** | 🔊 动态脉冲（驱动于真实 audio_available） |
| **Capability** | ✅ Verified |
| **Fallback** | "音频不可用"（明确状态，非静默） |

---

#### L1: Audio Perception — Waveform（P0.5 — ROI 最高）

| 属性 | 值 |
|------|-----|
| **位置** | ① 主视觉区 右侧大尺寸波形区 |
| **数据源** | `rms` (windowed RMS stream) |
| **更新频率** | continuous（~100ms 粒度） |
| **JS 处理** | `audio_sync.js: _activate`（点击事件） + Canvas 绘制 |
| **Capability** | 🟡 Partial（segment-level，非连续流） |
| **Product Value** | 让用户"看到"声音 = Runtime Presence Visualization |

**关键洞察**：

> 这不是"波形图为了好看"，而是 **Runtime Presence Visualization**。
> 
> 用户看到真实变化的波形 = 亲眼看到声音输入在持续变化 = 信任建立。

---

#### L2: Acoustic State Transition（P0）

| 属性 | 值 |
|------|-----|
| **位置** | ② 感知理解 底部进度条 |
| **数据源** | `golden_audio_state` timeline 节点 |
| **显示** | 状态机可视化：NORMAL → ATTENTION → AROUSAL → STRESS |
| **渲染函数** | `render.py: _render_acoustic_state_panel` |
| **Evidence Continuity** | "声学状态从 NORMAL 跃迁至 STRESS（持续 15s）" |
| **⚠️ Reality Check** | **Golden Case 预定义，非 Runtime** |

---

#### L2: Risk Transition（Shell 固定）

| 属性 | 值 |
|------|-----|
| **位置** | Shell 底部统一风险状态条 |
| **数据源** | `risk_delta` → `RealTimeRiskEvaluator` |
| **显示** | "风险状态：观察 → 关注（声学状态变化 + 电话交互）" |
| **JS 处理** | `live_stream.js: _applyRiskDelta` |

---

#### L3: Evidence Synthesis（P1）

| 属性 | 值 |
|------|-----|
| **位置** | ③ 为什么关注 详情区 |
| **数据源** | `evidence_items` + `cross_modal_links` |
| **主路径** | vision(person_in_area) + audio(telephone_interaction) + audio(acoustic_state_change) |
| **增强路径** | phone_interaction（当前 blocked，cross_modal=0） |
| **关键标注** | `cross_modal=0` 不是故障，是"当前无额外关联证据" |

---

#### L4: Action（Shell 固定）

| 属性 | 值 |
|------|-----|
| **位置** | Shell 底部统一行动卡片 |
| **数据源** | `commandMap` + `state_update` |
| **JS 处理** | `live_actions.js: _render` |

---

#### L5: Provenance（Shell 固定）

| 属性 | 值 |
|------|-----|
| **位置** | Shell 顶部 Provenance Banner |
| **数据源** | `provenance_kind` + `source_segment_ids` |
| **三层信任** | provenance（数据来源）+ traceability（时间）+ verifiability（回溯） |

---

### 3.4 telephone_risk 专属适配

**必须显示的 Surface**：
- ✅ L0 Runtime Presence（Shell 固定）
- ✅ L1 Person Perception（P1，辅助）
- ✅ L1 Audio Trust（P0）
- ✅ L1 Audio Perception — Waveform（P0.5，ROI 最高）
- ✅ L2 Acoustic State（P0）
- ✅ L2 Risk Transition（Shell 固定）
- ✅ L3 Evidence Synthesis（P1）
- ✅ L4 Action（Shell 固定）
- ✅ L5 Provenance（Shell 固定）

**跨模态标注**：

```
主路径: vision → audio → risk_signal ✅

增强路径: phone_interaction ❌ (blocked, cross_modal=0，不阻断主路径)
```

---

## 四、cctv_surveillance — Vision-first 叙事层

### 4.1 核心叙事流

```
实时感知
   ↓
👤 有人进入
   ↓
持续停留 / 重复出现
   ↓
夜间异常
   ↓
风险升高
   ↓
行动
```

### 4.2 布局全貌

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L5] ● LIVE · 受控演示输入 · 非 7×24 真实设备                                         │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [L0] 🟢 LIVE  │  📹 视频正常  │  🔇 音频不可用（隐藏）  │  延迟 95ms  │  Session: 00:09 │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [Tabs] ① 风险发现  ② 家属确认  ③ 社区处置                                             │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │  ① 主视觉区（Vision-first 布局 — 视频占主导）                                     │ │
│  │                                                                                 │ │
│  │  ┌───────────────────────────────────────────────────────────────────────────┐  │ │
│  │  │  📹 VIDEO SENSOR（full-width 视频主视觉）                                 │  │ │
│  │  │  [LIVE badge]                                                           │  │ │
│  │  │                                                                         │  │ │
│  │  │              Case Video Player (night vision)                           │  │ │
│  │  │              Overlay: bbox + track_id + person_count                    │  │ │
│  │  │                                                                         │  │ │
│  │  │              检测到 1 人，停留 9.5s（持续）                               │  │ │
│  │  │                                                                         │  │ │
│  │  │  frame_count=287 · case_time=00:09.5s                                   │  │ │
│  │  └───────────────────────────────────────────────────────────────────────────┘  │ │
│  │                                                                                 │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  ┌─────────────────────┬─────────────┬─────────────┬──────────────────────────────┐ │
│  │② 感知理解           │③ 风险状态    │④ 系统行动    │⑤ 证据详情                  │ │
│  │                     │             │             │                              │ │
│  │ 👤 首次出现 (0.0s)  │  MONITOR    │  LOG_ONLY  │  - video: 287 帧            │ │
│  │ 👤 再次出现 (3.2s)  │  → RAISED   │  NOTIFY    │  - no audio evidence        │ │
│  │ ⏱ 停留超时 (9.5s)  │  ↓ CLEARED  │  ESCALATE  │  - visual only              │ │
│  │                     │             │             │                              │ │
│  │ 视觉感知状态         │  触发规则:    │  执行状态:   │                              │ │
│  │ - 1 人在场 (9.5s)   │  abnormal_  │  pending/   │                              │ │
│  │ - 重复访问 (第3次)   │  dwell +    │  done       │                              │ │
│  │                     │  repeat_visit│             │                              │ │
│  └─────────────────────┴─────────────┴─────────────┴──────────────────────────────┘ │
│                                                                                 │
│  [统一 Shell] 风险: RAISED · 持续 2.1s · 夜间异常访问 + 重复访问                     │
│  [统一 Shell] 系统建议：继续观察 · LOG_ONLY                                         │
│  <details> [L5] 详细证据（Timeline + Evidence Graph + Gate）                        │
│  </details>                                                                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 cctv_surveillance 专属规则

**完全隐藏的面板**（禁止显示，而非显示"无音频"）：
- ❌ L1 Audio Trust
- ❌ L1 Audio Perception（Waveform）
- ❌ L2 Acoustic State

**升级的 Surface**：
- 🟡 L1 Person Perception → **P0**（核心风险信号）

**必须显示的 Surface**：
- ✅ L0 Runtime Presence（Shell 固定）
- ✅ L1 Person Perception（P0，核心）
- ✅ L2 Risk Transition（Shell 固定）
- ✅ L3 Evidence Synthesis（P1）
- ✅ L4 Action（Shell 固定）
- ✅ L5 Provenance（Shell 固定）

---

## 五、repeated_visit — Memory-first 叙事层

### 5.1 核心叙事流

```
现在看到谁
   ↓
3天前来过
   ↓
昨天又来
   ↓
今天再次出现
   ↓
系统识别出重复模式
   ↓
风险升级
```

### 5.2 布局全貌

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L5] ● LIVE · 受控演示输入 · 非 7×24 真实设备                                         │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [L0] 🟢 LIVE  │  📹 视频正常  │  🔇 音频不可用（隐藏）  │  延迟 110ms  │  Session: 00:22 │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [Tabs] ① 风险发现  ② 家属确认  ③ 社区处置                                             │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │  ① 主视觉区（Memory-first 布局 — 记忆叙事主导）                                   │ │
│  │                                                                                 │ │
│  │  ┌─────────────────────────┬─────────────────────────────────────────────────┐  │ │
│  │  │  📹 视频传感器           │  🧠 记忆上下文（核心差异点）                     │  │ │
│  │  │  [LIVE badge]            │                                                 │  │ │
│  │  │                         │  ┌─────────────────────────────────────────┐    │  │ │
│  │  │          Case Video     │  │  今日访问  14:32:00  停留 22s           │    │  │ │
│  │  │          (RTSP/HLS)     │  ├─────────────────────────────────────────┤    │  │ │
│  │  │                         │  │  昨天访问  13:15:00  停留 18s           │    │  │ │
│  │  │  Overlay: 👤 1人在场    │  ├─────────────────────────────────────────┤    │  │ │
│  │  │  case_time=00:22.1s     │  │  3天前访问  11:08:00  停留 25s          │    │  │ │
│  │  │                         │  └─────────────────────────────────────────┘    │  │ │
│  │  │                         │                                                 │  │ │
│  │  └─────────────────────────┴─────────────────────────────────────────────────┘  │ │
│  │                                                                                 │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  ┌─────────────────────┬─────────────┬─────────────┬──────────────────────────────┐ │
│  │② 记忆时间线         │③ 风险状态    │④ 系统行动    │⑤ 证据详情                  │ │
│  │                     │             │             │                              │ │
│  │  📅 3天前          │  MONITOR    │  LOG_ONLY  │  - video: 512 帧            │ │
│  │      首次访问       │  → RAISED   │  NOTIFY    │  - memory: 3 episodes       │ │
│  │                     │  ↓ CLEARED  │  ESCALATE  │  - pattern: 重复访问          │ │
│  │  📅 昨天            │             │             │                              │ │
│  │      再次出现       │  触发规则:    │  执行状态:   │                              │ │
│  │                     │  repeat_     │  pending/   │                              │ │
│  │  📅 今天            │  visit       │  done       │                              │ │
│  │      第3次出现      │  模式: 3次访问│             │                              │ │
│  │                     │  间隔: 1天    │             │                              │ │
│  └─────────────────────┴─────────────┴─────────────┴──────────────────────────────┘ │
│                                                                                 │
│  [统一 Shell] 风险: RAISED · 持续 1.5s · 重复访问模式识别                           │
│  [统一 Shell] 系统建议：通知家属 · NOTIFY_FAMILY                                   │
│  <details> [L5] 详细证据（Timeline + Memory Graph + Gate）                          │
│  </details>                                                                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 5.3 repeated_visit 专属规则

**核心差异**：记忆上下文是主要风险信号，不是实时检测。

**必须显示的 Surface**：
- ✅ L0 Runtime Presence（Shell 固定）
- ✅ L1 Person Perception（P0）
- ✅ L2 Risk Transition（Shell 固定）
- ✅ L3 Evidence Synthesis（P1）
- ✅ L4 Action（Shell 固定）
- ✅ L5 Provenance（Shell 固定）
- ✅ **记忆上下文面板**（Memory Context — repeated_visit 专属）

**记忆上下文面板规格**：

| 属性 | 值 |
|------|-----|
| **位置** | ① 主视觉区 右侧（大尺寸） |
| **数据源** | Memory Episodes（历史访问记录） |
| **显示** | "过去 → 昨天 → 今天"时间线 |
| **叙事** | "系统识别出重复访问模式" |
| **Capability** | ✅ Verified（ADR-0024/0025 Memory 架构） |

---

## 六、Fallback States 完整定义

### 6.1 L0 Runtime Presence

| 状态 | 触发条件 | 显示内容 | 图标 |
|------|---------|---------|------|
| **正常** | WS connected + video/audio active | "● LIVE · 视频正常 · 音频正常 · 延迟 120ms" | 🟢 |
| **视频中断** | video disconnected | "● LIVE · ⚠ 视频输入中断 · 音频正常" | 🟡 |
| **音频中断** | audio disconnected | "● LIVE · 视频正常 · ⚠ 音频输入中断" | 🟡 |
| **都中断** | both disconnected | "● LIVE · ⚠ 传感器输入中断" | 🔴 |
| **连接中** | WS connecting | "○ 连接中..." | ⚪ |
| **离线** | WS closed | "● OFFLINE" | ⚫ |

### 6.2 L1 Audio Trust（telephone_risk 专属）

| 状态 | 触发条件 | 显示内容 |
|------|---------|---------|
| **正常** | audio_available=true | "🔊 正在分析声音" |
| **降噪中** | VAD not active | "降噪中..." |
| **无音频** | audio_available=false | "音频不可用" |
| **禁止** | 无音频轨场景 | （面板完全隐藏） |

### 6.3 L2 Acoustic State（telephone_risk 专属）

| 状态 | 触发条件 | 显示内容 |
|------|---------|---------|
| **NORMAL** | f0_mean < threshold | "平静态" |
| **ATTENTION** | f0_delta > 0.1 | "关注态" |
| **AROUSAL** | speech_rate > threshold | "唤起态" |
| **STRESS** | voice_stress > threshold | "应激态" |
| **无数据** | 无 audio evidence | （面板隐藏） |

### 6.4 L2 Risk Transition（Shell 固定）

| 状态 | 触发条件 | 显示内容 |
|------|---------|---------|
| **MONITOR** | 初始状态 | "观察中" |
| **RAISED** | risk_signal 触发 | "风险关注" |
| **CLEARED** | 风险解除 | "风险解除" |
| **无信号** | 无 risk event | "无风险信号" |

### 6.5 L3 Evidence Synthesis

| 状态 | 触发条件 | 显示内容 |
|------|---------|---------|
| **主路径成立** | vision + audio 汇聚 | "多模态证据汇聚" |
| **单模态** | 仅 vision | "单模态证据（仅视觉）" |
| **跨模态阻塞** | cross_modal=0 | "主路径成立，无跨模态佐证" |
| **无证据** | 无 evidence | "无有效证据" |

### 6.6 "无风险"场景处理

**关键原则**：

```
错误做法                        正确做法
────────────────────────────────────────────────────────────
隐藏 L2-L4（让用户以为系统故障）   明确显示"无风险信号"
显示"风险：LOW"（误导用户）        显示"无风险事件"
静默无反馈                       明确说明"系统正在观察，暂无异常"
```

---

## 七、Component Boundaries

### 7.1 JS 模块职责边界

```
┌─────────────────────────────────────────────────────────────────────┐
│  live_stream.js                                                      │
│  ├── 职责：EvidenceProjection delta 流消费                            │
│  ├── 不创造 Evidence、不跑规则、不判断风险                             │
│  ├── 核心函数：                                                        │
│  │   ├── _applySnapshot()       → L0 Runtime Presence               │
│  │   ├── _applyPerceptionDelta() → L1 Person Perception              │
│  │   ├── _applyRiskDelta()       → L2 Risk Transition                │
│  │   ├── _renderBehaviorTimeline() → 行为时间线                       │
│  │   └── _renderRiskSignals()    → 风险信号                          │
│  └── 状态管理：__LiveState (warningMap, behaviorEvents, riskSignalMap)│
├─────────────────────────────────────────────────────────────────────┤
│  audio_sync.js                                                       │
│  ├── 职责：音频证据 ↔ 媒体时间轴联动                                   │
│  ├── 核心函数：                                                        │
│  │   ├── bind()                  → 初始化点击监听                     │
│  │   ├── _activate(kind, caseTime) → 高亮 card + seek/play           │
│  │   ├── _seekAndPlay(audioEl)     → 安全播放（readyState guard）     │
│  │   └── _readManifest()         → 读取 audio manifest                │
│  └── 依赖：data-kind + data-time HTML 属性                            │
├─────────────────────────────────────────────────────────────────────┤
│  live_actions.js                                                     │
│  ├── 职责：行动闭环面板 WS 客户端                                       │
│  ├── 核心函数：                                                        │
│  │   ├── _render(panel, sid, state)       → 主面板渲染                │
│  │   ├── _renderSummary(panel, sid, cur)   → 轻量摘要渲染             │
│  │   └── _renderTabViews(panel, active, cur) → 角色视图同步            │
│  └── 边界铁律：不写 EvidenceProjection，只处理 UI/Workflow 态           │
└─────────────────────────────────────────────────────────────────────┘
```

### 7.2 Python Renderer 职责

| 渲染函数 | 负责 Surface | 适用场景 |
|---------|-------------|---------|
| `_render_live_shell()` | 整体 Shell 布局 | 所有场景 |
| `_render_case_video_inner()` | ① 主视觉区 | 所有场景 |
| `_render_audio_sensor_status()` | L1 Audio Trust | telephone_risk |
| `_render_acoustic_state_panel()` | L2 Acoustic State | telephone_risk |
| `_render_provenance_banner()` | L5 Provenance | 所有场景 |
| `_render_evidence_graph()` | L3 Evidence Synthesis | 所有场景 |
| `_render_action_closure()` | L4 Action | 所有场景 |
| `_render_live_memory_context()` | 记忆上下文 | repeated_visit |

---

## 八、Implementation Checklist

### Phase 1：统一壳层实现（P0）

- [ ] **L0 Runtime Presence**
  - [ ] 实现 LIVE badge 组件（`.prov-badge`）
  - [ ] 实现输入状态指示灯（`.video-status-dot`, `.audio-status-dot`）
  - [ ] 实现分层降级显示
  - [ ] 对接 `live_stream.js: _applySnapshot`

- [ ] **L5 Provenance Banner**
  - [ ] 实现 provenance banner（`.prov-banner`）
  - [ ] 实现可展开证据链来源（`.prov-details`）
  - [ ] 对接 `render.py: _render_provenance_banner`

- [ ] **统一风险状态条**
  - [ ] 实现风险状态徽章（MONITOR / RAISED / CLEARED）
  - [ ] 实现趋势箭头动画
  - [ ] 对接 `live_stream.js: _applyRiskDelta`

- [ ] **统一行动卡片**
  - [ ] 实现行动卡片组件
  - [ ] 对接 `live_actions.js: _render`

- [ ] **统一 Details / 审计入口**
  - [ ] 实现折叠面板
  - [ ] 对接 EvidenceProjection 全量数据

### Phase 2：telephone_risk 叙事层（P0.5）

- [ ] **Waveform 组件**（ROI 最高）
  - [ ] 实现 Canvas waveform 组件
  - [ ] 实现 RMS 数据驱动（~100ms 粒度更新）
  - [ ] 对接 `audio_sync.js: _activate`
  - [ ] **优先级最高**

- [ ] **L2 Acoustic State**
  - [ ] 实现状态机可视化（NORMAL → ATTENTION → AROUSAL → STRESS）
  - [ ] 实现进度条 + 时间标记
  - [ ] 对接 `render.py: _render_acoustic_state_panel`

- [ ] **场景布局配置**
  - [ ] 实现 `_getScenarioSurfaces(scenario_id)` 函数
  - [ ] telephone_risk: 显示完整六层 + Waveform

### Phase 3：cctv_surveillance 叙事层（P0）

- [ ] **场景配置化**
  - [ ] cctv_surveillance: 隐藏所有音频 Surface
  - [ ] L1 Person Perception 升级到 P0（核心风险信号）

- [ ] **视觉强化**
  - [ ] 夜间模式 bbox 可见性增强
  - [ ] 人员轨迹可视化

### Phase 4：repeated_visit 叙事层（P1）

- [ ] **记忆上下文面板**
  - [ ] 实现记忆时间线组件
  - [ ] 对接 Memory Episodes API
  - [ ] "过去 → 昨天 → 今天"叙事布局

- [ ] **场景配置化**
  - [ ] repeated_visit: 记忆上下文作为核心差异化

### Phase 5：Fallback 与降级（P1）

- [ ] **L0 降级处理**
  - [ ] 实现 reconnecting 状态显示
  - [ ] 实现 offline 状态显示
  - [ ] 实现输入中断分层显示

- [ ] **"无风险"场景处理**
  - [ ] 实现 `.tl-empty.observing` 样式
  - [ ] 实现明确的"No Event"文案
  - [ ] 禁止显示"风险：LOW"

---

## 九、CSS 设计规范

### 9.1 颜色语义

```css
/* Risk Levels */
--risk-monitor: #64748b;    /* 灰色 - 观察中 */
--risk-raised: #d97706;     /* 橙色 - 风险关注 */
--risk-critical: #dc2626;   /* 红色 - 高风险 */
--risk-cleared: #16a34a;    /* 绿色 - 风险解除 */

/* Modality Colors */
--modality-vision: #4a90d9; /* 蓝色 - 视觉 */
--modality-audio: #9b59b6;  /* 紫色 - 音频 */
--modality-action: #d68910; /* 金色 - 行动 */
--modality-memory: #0891b2; /* 青色 - 记忆 */

/* Status Indicators */
--status-live: #16a34a;     /* 绿色 - 实时 */
--status-degraded: #d97706; /* 橙色 - 降级 */
--status-offline: #dc2626;  /* 红色 - 离线 */
```

### 9.2 关键组件样式

```css
/* LIVE Badge */
.prov-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
}

.prov-badge.prov-real-sensor {
  background: #f0fdf4;
  color: var(--status-live);
  border: 1px solid var(--status-live);
}

/* Risk State Card */
.lrk-card {
  border-left: 4px solid var(--risk-raised);
  padding: 12px;
  background: #fff7ed;
}

.lrk-level {
  font-size: 24px;
  font-weight: 700;
}

/* Acoustic State Progress */
.acoustic-state-bar {
  display: flex;
  height: 24px;
  border-radius: 4px;
  overflow: hidden;
}

.acoustic-state-bar .phase {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 600;
}

.phase-normal { background: #e0f2fe; color: #0369a1; }
.phase-attention { background: #fef3c7; color: #92400e; }
.phase-arousal { background: #fee2e2; color: #991b1b; }
.phase-stress { background: #dc2626; color: white; }

/* Memory Timeline */
.memory-timeline {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
}

.memory-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  background: #f8fafc;
  border-radius: 6px;
  border-left: 3px solid var(--modality-memory);
}

/* Empty State */
.tl-empty.observing {
  padding: 24px;
  text-align: center;
  color: var(--status-live);
  font-weight: 500;
}
```

---

## 十、关键设计决策记录

### D1: 统一壳层 + 场景叙事层分离

**决策**: Shell 固定 6 个元素，叙事层每个场景独立定义

**理由**: 强行共用一套 UI 会导致"什么都有、什么都不突出"

**应用**:
- Shell: L0/L5 实时状态、统一风险表达、统一交互、统一 Details
- 叙事层: telephone_risk (Audio-first) / cctv (Vision-first) / repeated_visit (Memory-first)

---

### D2: Evidence Continuity > Event Count

**决策**: 连续状态 > 离散事件 > 事件数量

**理由**: 用户关心"现在发生什么"，不关心"发生了多少次"

**应用**:
- 显示"持续 12.4s"而非"449 帧检测"
- 显示"NORMAL → STRESS"而非"4 个声学状态事件"
- 显示"风险状态：观察 → 关注"而非"8 个风险信号事件"

---

### D3: Capability 成熟度铁律

**决策**: 只有 ✅ Verified 能力可进入 P0 UI

**理由**: 避免展示未验证能力导致误导

**应用**:
- `audio_available` ✅ → P0 UI
- `vad_ratio` ✅ → P0 UI
- `audio_buffer_level` 🟡 → **禁止 P0 UI**
- `phone_detection` ❌ → **禁止显示**（Benchmark Recall=0%）

---

### D4: 媒体时间轴 ≠ Runtime 时间轴

**决策**: `frame_index` ≠ `runtime_tick_count`

**理由**: 媒体帧数是静态事实，runtime tick 是动态测量值

**应用**:
- 显示"source_frame_count: 449"而非"449 个 runtime ticks"
- 明确标注 `runtime_fps`（可能配置为 8 fps）
- 节流逻辑：`FRAME_DISPLAY_INTERVAL_MS = 150`（~6.7 fps 最大显示频率）

---

### D5: 无音频场景必须完全隐藏

**决策**: cctv_surveillance 等无音频场景，音频面板不显示

**理由**: 显示"无音频"会误导用户认为系统故障

**应用**:
- cctv_surveillance: 隐藏 L1 Audio Trust / L1 Audio Perception / L2 Acoustic State
- 而非显示"🔊 音频传感器: IDLE"

---

### D6: "无风险"也是产品叙事

**决策**: delivery_courier_normal 等场景，明确显示"No Event"

**理由**: 证明系统不误报是核心产品价值

**应用**:
- 显示"🟢 无风险信号 · 系统持续观察中"
- 禁止显示"风险：LOW"（会误导用户认为有风险）

---

## 十一、下一步行动

### 11.1 前端开发优先级

| 优先级 | 任务 | 依赖 |
|--------|------|------|
| **P0** | 统一壳层实现（L0/L5/风险/行动/Details） | 无 |
| **P0.5** | telephone_risk Waveform 组件 | Shell 完成 |
| **P0** | cctv_surveillance 场景适配 | Shell 完成 |
| **P1** | repeated_visit 记忆上下文面板 | Shell 完成 |
| **P1** | 场景配置化函数 `_getScenarioSurfaces()` | 所有场景开发 |
| **P1** | Fallback 与降级状态 | Shell 完成 |

### 11.2 测试计划

1. **单元测试**: 每个 Surface 组件的渲染逻辑
2. **集成测试**: WebSocket delta 流 → UI 渲染链路
3. **场景测试**: telephone_risk / cctv_surveillance / repeated_visit 完整流程
4. **降级测试**: 所有 Fallback 状态验证
5. **场景隔离测试**: 确认 cctv 不显示音频面板、telephone_risk 显示完整六层

### 11.3 验收清单

- [ ] 每个 Surface 有对应的 Runtime Fact
- [ ] 没有伪造的实时感
- [ ] Fallback 状态明确
- [ ] 场景特异性清晰（不同场景不同布局）
- [ ] SIMULATED 音频不进入风险判断
- [ ] "无风险"场景明确显示"No Event"
- [ ] Capability Candidate 未进入 P0 UI
- [ ] ruff check 无 error
- [ ] pytest tests/ -q 全部通过

---

**文档版本**: v2.0  
**最后更新**: 2026-08-21  
**状态**: Draft → 待 Owner 审批  
**Owner**: Home Perception Visualizer Team