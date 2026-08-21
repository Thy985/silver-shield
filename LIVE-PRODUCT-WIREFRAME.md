# Live Product Wireframe — 核心场景原型

> **设计时间**: 2026-08-21  
> **状态**: Draft  
> **架构原则**: 共享框架（LiveShell）+ 独立叙事（ScenarioSurface）

---

## 一、架构决策

### 不要建立
```
PageTelephone
PageCCTV
PageRepeatedVisit
PageDelivery
PageEvidenceInsufficient
```
→ 五套代码，大量分叉，重复维护。

### 建立
```
LiveShell（共享框架）
├── LivePresence（L0）
├── RiskState（L2）
├── Provenance（L5）
├── EvidenceDetails（折叠）
└── ScenarioSurface（场景专属）
    ├── TelephoneRiskSurface（audio-first）
    ├── CCTVSurface（vision-first）
    ├── RepeatedVisitSurface（memory-first）
    ├── DeliveryNormalSurface（negative control）
    └── EvidenceInsufficientSurface（edge case）
```

### 核心原则
> **Shell 不讲场景故事，只提供跨场景一致的状态、行动和信任基础设施。**
> 
> **ScenarioSurface 只讲"这个场景为什么值得被展示"。**

---

## 二、Wireframe 1: telephone_risk（多模态/声学优先）

### 产品目标
证明 **多模态实时感知 + 声学状态变化 + 风险判断可信**

### 必须突出
- L0 Runtime Presence（系统正在工作）
- L1 Audio Trust（系统在听）
- L1 Audio Perception — Waveform（声音可视化）
- L2 Acoustic State Transition（NORMAL → STRESS）
- L2 Risk Transition（观察 → 关注）
- L5 Evidence Verifiability（可回听）

### 不要突出
- Phone Detection（Benchmark Recall=0%，ADR-0038）
- 技术字段（frame_index, confidence 等）
- 大量 Timeline（压缩叙事）

### Wireframe 布局

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L5] ● LIVE · 受控演示输入 · 非 7×24 真实设备 · [证据链来源 ▾]                     │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [L0] 🟢 LIVE  │  📹 视频正常 · 449 帧  │  🔊 音频正常 · 9 段  │  延迟 120ms  │ 00:15 │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [Tabs] ① 风险发现  ② 家属确认  ③ 社区处置                                           │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                                                                             │ │
│  │   ┌─────────────────────┬─────────────────────────────────────────────────┐  │ │
│  │   │  📹 VIDEO SENSOR    │  🔊 AUDIO SENSOR                              │  │ │
│  │   │  [LIVE]             │  [ACTIVE · 🔊 脉冲驱动于真实 audio_buf]         │  │ │
│  │   │                     │                                                 │  │ │
│  │   │  Case Video Player  │  ┌───────────────────────────────────────────┐  │  │ │
│  │   │  (RTSP/HLS stream)  │  │  Waveform (RMS 分段值)                    │  │  │ │
│  │   │                     │  │  ▂▃▅▆▇▆▅▃▂▃▅▇▆▃▂▂▃▅▆▇▆▅▃▂                   │  │  │ │
│  │   │  Overlay: 👤 1人在场 │  │  ~100ms 粒度（segment-level，非连续流）    │  │  │ │
│  │   │  case_time=00:12.4s │  └───────────────────────────────────────────┘  │  │ │
│  │   │                     │                                                 │  │ │
│  │   └─────────────────────┴─────────────────────────────────────────────────┘  │ │
│  │                                                                             │ │
│  │   [点击 waveform 区域回听对应音频证据]                                         │ │
│  │                                                                             │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  ┌─────────────────────┬─────────────────────┬──────────────────────────────────┐ │
│  │ ② 感知理解          │ ③ 风险状态           │ ⑤ 证据详情                      │ │
│  │                     │                     │                                  │ │
│  │ 👤 持续 1 人在场     │  MONITOR            │  - video: 449 帧                │ │
│  │   持续时间 12.4s    │    ↓ RAISED         │  - audio: 9 段                  │ │
│  │                     │    ↓ CLEARED        │  - acoustic: 4 阶段              │ │
│  │ 🔊 检测到持续电话声  │                     │  - cross_modal: 0（无佐证）      │ │
│  │ 🔊 哭腔/求助        │  触发规则:           │                                  │ │
│  │                     │  acoustic_state     │                                  │ │
│  │ 声学状态进度条:      │  + telephone_       │                                  │ │
│  │ NORMAL→ATTENTION    │  interaction        │                                  │ │
│  │ →AROUSAL→STRESS    │                     │                                  │ │
│  │ [15s 连续]          │                     │                                  │ │
│  └─────────────────────┴─────────────────────┴──────────────────────────────────┘ │
│                                                                                 │
│  [统一 Shell] 风险: RAISED · 持续 3.2s · 声学状态变化 + 电话交互                   │
│  [统一 Shell] 系统建议：继续观察 · LOG_ONLY                                       │
│  <details> [L5] 详细证据（Timeline + Evidence Graph + Gate）                      │
│  </details>                                                                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Surface 优先级

| Surface | 层级 | 优先级 | 数据源 |
|---------|------|--------|--------|
| L0 Runtime Presence | Shell | P0 | frame_tick + audio_segment_tick |
| L1 Audio Trust | Scenario | P0 | audio_available (bool) |
| L1 Audio Perception | Scenario | P0.5 | rms (segment-level) |
| L2 Acoustic State | Scenario | P0 | golden_audio_state (SIMULATED) |
| L2 Risk Transition | Shell | P0 | risk_delta |
| L3 Evidence Synthesis | Shell | P1 | evidence_items + cross_modal_links |
| L4 Action | Shell | P1 | commandMap |
| L5 Provenance | Shell | P0 | provenance_kind |

### 关键标注
- Waveform: "RMS 分段值（非连续流）" — **禁止标榜"实时波形"**
- Acoustic State: "🎭 Golden Case 预定义" — **禁止声称"Runtime 状态机"**
- Cross-modal: "cross_modal=0，当前无额外关联证据" — **禁止假装融合**

---

## 三、Wireframe 2: cctv_surveillance（视觉/风险优先）

### 产品目标
证明 **视觉实时感知 + 异常访问识别**

### 必须突出
- L0 Runtime Presence（系统正在工作）
- L1 Person Perception（核心风险信号）
- L2 Risk Transition（夜间异常访问 → 风险升高）
- L4 Action（建议行动）
- L5 Evidence Verifiability（可回溯）

### 禁止显示
- ❌ L1 Audio Trust（无音频轨）
- ❌ L1 Audio Perception（Waveform）
- ❌ L2 Acoustic State（无音频轨）

### Wireframe 布局

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L5] ● LIVE · 受控演示输入 · 非 7×24 真实设备 · [证据链来源 ▾]                     │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [L0] 🟢 LIVE  │  📹 视频正常 · 287 帧  │  🔇 音频不可用（隐藏）  │  延迟 95ms  │ 00:09 │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [Tabs] ① 风险发现  ② 家属确认  ③ 社区处置                                           │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                                                                             │ │
│  │   ┌─────────────────────────────────────────────────────────────────────┐   │ │
│  │   │  📹 VIDEO SENSOR（full-width 视频主视觉）                           │   │ │
│  │   │  [LIVE badge]                                                     │   │ │
│  │   │                                                                     │   │ │
│  │   │                    Case Video Player (night vision)                │   │ │
│  │   │                    Overlay: bbox + track_id + person_count         │   │ │
│  │   │                                                                     │   │ │
│  │   │                    检测到 1 人，停留 9.5s（持续）                   │   │ │
│  │   │                                                                     │   │ │
│  │   │  frame_count=287 · case_time=00:09.5s                             │   │ │
│  │   └─────────────────────────────────────────────────────────────────────┘   │ │
│  │                                                                             │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  ┌─────────────────────┬─────────────────────┬──────────────────────────────────┐ │
│  │ ② 感知理解          │ ③ 风险状态           │ ⑤ 证据详情                      │ │
│  │                     │                     │                                  │ │
│  │ 👤 首次出现 (0.0s)  │  MONITOR            │  - video: 287 帧                │ │
│  │ 👤 再次出现 (3.2s)  │    ↓ RAISED         │  - no audio evidence            │ │
│  │ ⏱ 停留超时 (9.5s)  │    ↓ CLEARED        │  - visual only                  │ │
│  │                     │                     │                                  │ │
│  │ 视觉感知状态:        │  触发规则:           │                                  │ │
│  │ - 1 人在场 (9.5s)   │  abnormal_dwell +   │                                  │ │
│  │ - 重复访问 (第3次)  │  repeat_visit       │                                  │ │
│  └─────────────────────┴─────────────────────┴──────────────────────────────────┘ │
│                                                                                 │
│  [统一 Shell] 风险: RAISED · 持续 2.1s · 夜间异常访问 + 重复访问                   │
│  [统一 Shell] 系统建议：继续观察 · LOG_ONLY                                       │
│  <details> [L5] 详细证据（Timeline + Evidence Graph + Gate）                      │
│  </details>                                                                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Surface 优先级

| Surface | 层级 | 优先级 | 数据源 |
|---------|------|--------|--------|
| L0 Runtime Presence | Shell | P0 | frame_tick |
| L1 Person Perception | Scenario | P0 | perception_delta (YOLO11n) |
| L2 Risk Transition | Shell | P0 | risk_delta |
| L3 Evidence Synthesis | Shell | P1 | evidence_items (visual only) |
| L4 Action | Shell | P1 | commandMap |
| L5 Provenance | Shell | P0 | provenance_kind |

### 关键标注
- **音频面板完全隐藏**，不显示"无音频"
- Person Perception 升级为 P0（核心风险信号）
- 夜间模式 bbox 可见性增强

---

## 四、Wireframe 3: repeated_visit（记忆/历史优先）

### 产品目标
证明 **系统真的"记得过去" + 识别重复模式**

### 必须突出
- L0 Runtime Presence（系统正在工作）
- L1 Person Perception（当前访客）
- **Memory Context Panel**（核心差异点）
- L2 Risk Transition（历史积累 → 风险升级）
- L5 Evidence Verifiability（跨 episode 对比）

### 特殊能力
- L5 具有跨 timepoint 证据对比能力
- 证明"Memory 能力"的核心场景

### Wireframe 布局

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L5] ● LIVE · 受控演示输入 · 非 7×24 真实设备 · [证据链来源 ▾]                     │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [L0] 🟢 LIVE  │  📹 视频正常 · 512 帧  │  🔇 音频不可用（隐藏）  │  延迟 110ms  │ 00:22 │
├─────────────────────────────────────────────────────────────────────────────────────┤
│  [Tabs] ① 风险发现  ② 家属确认  ③ 社区处置                                           │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                                                                             │ │
│  │   ┌─────────────────────┬─────────────────────────────────────────────────┐  │ │
│  │   │  📹 VIDEO SENSOR    │  🧠 MEMORY CONTEXT（核心差异点）                │  │ │
│  │   │  [LIVE badge]        │                                                 │  │ │
│  │   │                     │  ┌───────────────────────────────────────────┐  │  │ │
│  │   │  Case Video Player  │  │  今日访问  14:32:00  停留 22s             │  │  │ │
│  │   │  (RTSP/HLS stream)  │  ├───────────────────────────────────────────┤  │  │ │
│  │   │                     │  │  昨天访问  13:15:00  停留 18s             │  │  │ │
│  │   │  Overlay: 👤 1人在场 │  ├───────────────────────────────────────────┤  │  │ │
│  │   │  case_time=00:22.1s │  │  3天前访问  11:08:00  停留 25s            │  │  │ │
│  │   │                     │  └───────────────────────────────────────────┘  │  │ │
│  │   │                     │                                                 │  │ │
│  │   └─────────────────────┴─────────────────────────────────────────────────┘  │ │
│  │                                                                             │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  ┌─────────────────────┬─────────────────────┬──────────────────────────────────┐ │
│  │ ② 记忆时间线        │ ③ 风险状态           │ ⑤ 证据详情                      │ │
│  │                     │                     │                                  │ │
│  │  📅 3天前           │  MONITOR            │  - video: 512 帧                │ │
│  │      首次访问       │    ↓ RAISED         │  - memory: 3 episodes           │ │
│  │                     │    ↓ CLEARED        │  - pattern: 重复访问              │ │
│  │  📅 昨天            │                     │                                  │ │
│  │      再次出现       │  触发规则:           │                                  │ │
│  │                     │  repeat_visit       │                                  │ │
│  │  📅 今天            │  模式: 3次访问      │                                  │ │
│  │      第3次出现      │  间隔: 1天          │                                  │ │
│  └─────────────────────┴─────────────────────┴──────────────────────────────────┘ │
│                                                                                 │
│  [统一 Shell] 风险: RAISED · 持续 1.5s · 重复访问模式识别                         │
│  [统一 Shell] 系统建议：通知家属 · NOTIFY_FAMILY                                 │
│  <details> [L5] 详细证据（Timeline + Memory Graph + Gate）                        │
│  </details>                                                                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Surface 优先级

| Surface | 层级 | 优先级 | 数据源 |
|---------|------|--------|--------|
| L0 Runtime Presence | Shell | P0 | frame_tick |
| L1 Person Perception | Scenario | P0 | perception_delta |
| **Memory Context** | Scenario | **P0** | Memory Episodes API |
| L2 Risk Transition | Shell | P0 | risk_delta |
| L3 Evidence Synthesis | Shell | P1 | evidence_items + memory |
| L4 Action | Shell | P1 | commandMap |
| L5 Provenance | Shell | P0 | provenance_kind (跨 episode) |

### 关键标注
- **Memory Context Panel 是核心差异化**
- 叙事："过去 → 昨天 → 今天"时间线
- 证明"系统真的记得"

---

## 五、Negative Control Wireframes

### 5.1 delivery_courier_normal（正常配送）

**产品目标**: 证明系统**不误报**

**叙事**: "系统持续观察，暂无异常"

**布局**: 与 cctv 相同，但：
- 风险状态：MONITOR（不跃迁）
- 行动：LOG_ONLY（默认）
- 明确显示："🟢 无风险信号 · 系统持续观察中"

**禁止**:
- ❌ 显示"风险：LOW"（误导）
- ❌ 隐藏 L2-L4（让用户以为故障）

---

### 5.2 evidence_insufficient（证据不足）

**产品目标**: 证明系统**知道什么时候不能下结论**

**叙事**: "正在观察 → 发现模糊证据 → 证据不足 → 不做风险升级"

**布局**: 与 cctv 相同，但：
- L1 Person Perception：detection_score=0.31 < 0.5
- L2-L4：明确"No Event"

**关键标注**:
- "低置信检测，不触发风险"
- 证明系统边界

---

## 六、实现约束

### 6.1 JS 模块职责边界

```
LiveShell（共享）
├── live_stream.js        → L0/L2/L3 渲染
├── live_actions.js       → L4 行动卡片
└── live_tabs.js          → Tab 切换

ScenarioSurface（场景专属）
├── telephone_risk.js     → Waveform + Acoustic State
├── cctv_surface.js       → Person + Vision-only
└── repeated_visit.js     → Memory Context
```

### 6.2 Python Renderer 职责

| 渲染函数 | 负责 Surface | 适用场景 |
|---------|-------------|---------|
| `_render_live_shell()` | 整体 Shell | 所有场景 |
| `_render_scenario_surface()` | ScenarioSurface | 按 scenario_id 路由 |
| `_render_telephone_risk_surface()` | telephone_risk | telephone_risk |
| `_render_cctv_surface()` | cctv | cctv_surveillance |
| `_render_repeated_visit_surface()` | repeated_visit | repeated_visit |

### 6.3 场景配置化

```python
# src/home_perception/visualizer/viewer/scenario_config.py

SCENARIO_SURFACES = {
    "telephone_risk": {
        "narrative_mode": "audio_first",
        "priority_surfaces": ["L0", "L1_audio", "L2_acoustic", "L2_risk", "L5"],
        "hidden_surfaces": [],
        "special": ["waveform", "acoustic_state_panel"]
    },
    "cctv_surveillance_suspicious": {
        "narrative_mode": "vision_first",
        "priority_surfaces": ["L0", "L1_person", "L2_risk", "L4", "L5"],
        "hidden_surfaces": ["L1_audio", "L2_acoustic"],
        "special": []
    },
    "live_repeated_visit": {
        "narrative_mode": "memory_first",
        "priority_surfaces": ["L0", "L1_person", "memory_context", "L2_risk", "L5"],
        "hidden_surfaces": ["L1_audio", "L2_acoustic"],
        "special": ["memory_timeline"]
    }
}
```

---

## 七、验收清单

### 7.1 架构验收
- [ ] 所有场景共享 LiveShell（L0/L2/L4/L5）
- [ ] 每个场景有独立 ScenarioSurface
- [ ] 无重复渲染（Shell 和 Scenario 不重叠）
- [ ] 场景配置化（SCENARIO_SURFACES 字典）

### 7.2 Wireframe 验收
- [ ] telephone_risk: Waveform + Acoustic State 突出
- [ ] cctv: 视频主视觉 + Person Perception 突出
- [ ] repeated_visit: Memory Context 突出
- [ ] delivery: 明确"No Event"
- [ ] evidence_insufficient: 明确"证据不足"

### 7.3 Reality Check 验收
- [ ] 每个 Surface 有对应 Runtime Fact
- [ ] 没有伪造的实时感
- [ ] SIMULATED 数据明确标注
- [ ] 无音频场景完全隐藏音频面板
- [ ] "无风险"场景明确显示"No Event"

---

**文档版本**: v1.0  
**最后更新**: 2026-08-21  
**状态**: Draft → 待 Owner 审批  
**Owner**: Home Perception Visualizer Team