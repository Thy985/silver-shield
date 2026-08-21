# Live Surface → Reality Check — Runtime Fact 审计

> **审计时间**: 2026-08-21  
> **审计目标**: 逐项验证每个 Surface 是否有真实 Runtime Fact 支撑  
> **核心问题**: Capability available ≠ Surface useful in current scenario ≠ Evidence continuous

---

## 一、审计方法

每个 Surface 回答 7 个问题：

| # | 问题 | 说明 |
|---|------|------|
| 1 | 能力真的存在吗？ | Runtime Capability 是否已实现 |
| 2 | 当前场景真的有这个能力吗？ | Scenario Support（支持/部分/不可用） |
| 3 | 数据是否连续？ | Evidence Continuity（continuous/per-event/none） |
| 4 | 数据变化是否足够明显？ | Signal Strength（strong/medium/weak） |
| 5 | 能否在 UI 上实时表现？ | Realtime Feasibility（是/否/有限） |
| 6 | 能否回到原始证据？ | Verifiability（full/partial/none） |
| 7 | 值不值得占首屏空间？ | UI Priority（P0/P1/P2/禁止） |

---

## 二、telephone_risk（多模态风险识别）

### L0: Runtime Presence

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | WebSocket + frame_tick + audio_segment_tick |
| 2 | 场景支持吗？ | ✅ Strong | 视频 + 音频双输入 |
| 3 | 数据连续？ | ✅ Continuous | 帧级 + 段级持续流动 |
| 4 | 变化明显？ | N/A | 存在性证据，非信号 |
| 5 | UI 实时表现？ | ✅ 是 | LIVE badge + 输入状态指示灯 |
| 6 | 可回溯？ | ✅ Full | case_time + websocket logs |
| 7 | UI 优先级？ | **P0** | 用户首先必须相信系统在工作 |

**实际 Runtime Evidence**:
- `frame_index`: 0-449（15s @ 30fps 媒体帧）
- `runtime_tick_count`: ~8 fps（配置帧率）
- `audio_segments`: 9 段（VAD 分割）
- `websocket_heartbeat`: 持续

**Continuity**: session 生命周期

**Product Value**: Critical — 没有 L0，后面所有判断都是黑箱

**UI Priority**: P0 — 首屏顶部固定显示

---

### L1: Person Perception

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | YOLO11n Person class_id=0 |
| 2 | 场景支持吗？ | 🟡 Partial | 有人但非主要风险信号 |
| 3 | 数据连续？ | ✅ Continuous | 8 fps 持续检测 |
| 4 | 变化明显？ | 🟡 Medium | 人员在场是静态事实，非风险信号 |
| 5 | UI 实时表现？ | ✅ 是 | bbox overlay + 计数 |
| 6 | 可回溯？ | ✅ Full | event_id + timestamp + media_ref |
| 7 | UI 优先级？ | **P1** | 辅助证据，非核心叙事 |

**实际 Runtime Evidence**:
- `person_count`: 1（持续）
- `detection_count`: ~449（全帧检出）
- `avg_confidence`: 0.83

**Continuity**: 跨帧 track_id 保持

**Product Value**: Medium — 证明"有人在场"，但非风险判定依据

**UI Priority**: P1 — 首屏摘要区显示"1 人在场"，详情进 Timeline

---

### L1: Audio Perception — Trust Layer

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | audio_available 布尔推断 |
| 2 | 场景支持吗？ | ✅ Strong | 完整音频链路 |
| 3 | 数据连续？ | ✅ Continuous | 段级持续 |
| 4 | 变化明显？ | N/A | 存在性证据 |
| 5 | UI 实时表现？ | ✅ 是 | 🔊 动态脉冲（驱动于真实 buffer） |
| 6 | 可回溯？ | ✅ Full | segment_id + timestamp |
| 7 | UI 优先级？ | **P0** | 用户必须相信"系统在听" |

**实际 Runtime Evidence**:
- `audio_available`: true（推断自 audio 段非空）
- `vad_ratio`: 0.85（高语音活动）
- `buffer_level`: 持续填充（**未直接暴露**，前端推断）

**Continuity**: session 生命周期

**Product Value**: Critical — 音频场景的 L0 子层

**UI Priority**: P0 — 与 L0 合并显示

---

### L1: Audio Perception — Perception Layer（Waveform）

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | 🟡 Partial | RMS segment 存在，非连续 stream |
| 2 | 场景支持吗？ | ✅ Strong | 完整音频 |
| 3 | 数据连续？ | ⚠️ Per-segment | 段级离散值 |
| 4 | 变化明显？ | ✅ Strong | 0.20 → 0.05（4x 变化） |
| 5 | UI 实时表现？ | ✅ 是 | Canvas 柱状图（每段一根柱） |
| 6 | 可回溯？ | ✅ Full | 原始音频段可回放 |
| 7 | UI 优先级？ | **P0.5** | 声音强度可视化，ROI 最高 |

**实际 Runtime Evidence**:
- `rms_segment_0`: 0.2027
- `rms_segment_1`: 0.1942
- 连续窗口化值：**不存在**（需后端扩展）

**Continuity**: 段内连续，段间离散

**⚠️ Reality Check**:
- Wireframe 标注 "~100ms 连续" → **错误**
- 实际：segment-level RMS（~1-2s 粒度）
- UI 应标注："RMS 分段值（非连续流）"

**Product Value**: High — 让用户"看到"声音

**UI Priority**: P0.5 — 音频面板核心元素

---

### L1: Audio Perception — Interpretation Layer

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | AudioPerceptionKind 枚举 |
| 2 | 场景支持吗？ | ✅ Strong | 核心风险信号 |
| 3 | 数据连续？ | ⚠️ Per-event | 段级事件 |
| 4 | 变化明显？ | ✅ Strong | AUDIO_TELEPHONE_PERSISTENT + AUDIO_DISTRESS_CRY |
| 5 | UI 实时表现？ | ✅ 是 | 人话标签 |
| 6 | 可回溯？ | ✅ Full | event_id + source_segment_ids |
| 7 | UI 优先级？ | **P0** | 核心风险叙事 |

**实际 Runtime Evidence**:
- `events[0]`: AUDIO_TELEPHONE_PERSISTENT (score=0.92)
- `events[1]`: AUDIO_DISTRESS_CRY (score=0.72)

**Continuity**: 事件离散，但语义连续（状态机）

**Product Value**: Critical — 这是多模态价值的核心证明

**UI Priority**: P0 — 首屏风险叙事主干

---

### L2: Acoustic State Transition

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | 🟡 Partial | golden_audio_state 存在，但非 runtime |
| 2 | 场景支持吗？ | ✅ Strong | 核心叙事（Golden Case） |
| 3 | 数据连续？ | ⚠️ Per-event | 状态跃迁事件 |
| 4 | 变化明显？ | ✅ Strong | NORMAL → ATTENTION → AROUSAL → STRESS |
| 5 | UI 实时表现？ | ✅ 是 | 状态机图 + 时间标记 |
| 6 | 可回溯？ | ✅ Full | 每个状态有 timestamp + evidence |
| 7 | UI 优先级？ | **P0** | 多模态场景的核心差异点 |

**实际 Runtime Evidence**:
- `state_progression`: [NORMAL(0-6s), ATTENTION(6-9s), AROUSAL(9-12.5s), STRESS(12.5-15s)]
- `f0_delta`: 0.24
- `speech_rate_delta`: 0.29

**⚠️ Reality Check**:
- Wireframe 标注"Runtime Acoustic State Machine" → **误导性**
- 实际：golden_audio_state 来自 Golden Case manifest（SIMULATED）
- UI 必须标注："声学状态（Golden Case 预定义）"

**Continuity**: 状态机跨段保持

**Product Value**: Critical — 证明"风险来自声学状态变化"

**UI Priority**: P0 — 首屏风险叙事主干

---

### L2: Risk Transition

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | RealTimeRiskEvaluator |
| 2 | 场景支持吗？ | ✅ Strong | RISK_SIGNAL → LOW |
| 3 | 数据连续？ | ⚠️ Per-event | 风险信号事件 |
| 4 | 变化明显？ | ✅ Strong | MONITOR → RAISED → LOW |
| 5 | UI 实时表现？ | ✅ 是 | 状态变化动画 + 趋势箭头 |
| 6 | 可回溯？ | ✅ Full | signal_id + decision_trace |
| 7 | UI 优先级？ | **P0** | 风险判断的核心展示 |

**实际 Runtime Evidence**:
- `risk_transitions`: [RAISED, CLEARED]
- `warnings`: [LOW]
- `decision_detail`: "Acoustic state change detected..."

**Continuity**: stateful（风险状态跨事件保持）

**Product Value**: Critical — 用户最关心的判断

**UI Priority**: P0 — 首屏风险徽章

---

### L3: Evidence Synthesis

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | Vision + Audio 独立路径汇聚 |
| 2 | 场景支持吗？ | 🟡 Partial | 主路径成立，cross_modal=0 |
| 3 | 数据连续？ | ⚠️ Per-event | 证据汇聚事件 |
| 4 | 变化明显？ | 🟡 Medium | 主路径强，增强路径缺失 |
| 5 | UI 实时表现？ | ✅ 是 | 证据链可视化 |
| 6 | 可回溯？ | ✅ Full | evidence_items + fusion_log |
| 7 | UI 优先级？ | **P1** | 解释性证据，非核心叙事 |

**实际 Runtime Evidence**:
- `primary_path`: [person_in_area, telephone_interaction, acoustic_state_change]
- `supporting_path`: []（phone_detection=0）
- `cross_modal_links`: 0

**⚠️ Reality Check**:
- cross_modal=0 是已知限制（ADR-0038）
- UI 必须标注："主路径成立，无跨模态佐证"

**Continuity**: 证据链跨事件保持

**Product Value**: High — 解释"为什么有风险"

**UI Priority**: P1 — 首屏摘要区显示证据链，详情进 Timeline

---

### L4: Action

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | RuleBasedDecisionPolicy → Command |
| 2 | 场景支持吗？ | ✅ Strong | LOG_ONLY + continue_observation |
| 3 | 数据连续？ | ⚠️ Per-decision | 决策事件 |
| 4 | 变化明显？ | ✅ Strong | 从"无行动"到"记录风险" |
| 5 | UI 实时表现？ | ✅ 是 | 行动卡片 + 状态回执 |
| 6 | 可回溯？ | ✅ Full | command_id + execution_log |
| 7 | UI 优先级？ | **P1** | 处置闭环 |

**实际 Runtime Evidence**:
- `commands`: [LOG_ONLY, MONITOR]
- `execution_status`: executed

**Continuity**: action_state 跨决策保持

**Product Value**: High — 证明系统有处置能力

**UI Priority**: P1 — 首屏行动建议区

---

### L5: Evidence & Provenance

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | EvidenceProjection → Timeline |
| 2 | 场景支持吗？ | ✅ Strong | 完整证据链 |
| 3 | 数据连续？ | ✅ Continuous | 每帧/每段都有 provenance |
| 4 | 变化明显？ | N/A | 存在性证据 |
| 5 | UI 实时表现？ | ✅ 是 | 时间轴 + 证据链接 |
| 6 | 可回溯？ | ✅ Full | event_id → media_ref → seek_position |
| 7 | UI 优先级？ | **P0** | 信任基础 |

**实际 Runtime Evidence**:
- `provenance_kind`: REAL_SENSOR
- `source_segments`: [seg_001, seg_002]
- `media_ref`: case_b_vision_audio.mp4

**Continuity**: audit trail 跨 session 保持

**Product Value**: Critical — 用户信任的根基

**UI Priority**: P0 — 首屏证据入口 + Details 区完整展示

---

## 三、cctv_surveillance_suspicious（夜间异常访问）

### L0: Runtime Presence

同 telephone_risk L0 — ✅ P0

### L1: Person Perception

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1-6 | 同 telephone | ✅ | 核心风险信号 |
| 7 | UI 优先级？ | **P0** | 核心叙事 |

**实际 Runtime Evidence**:
- `person_count`: 1（持续）
- `visitor_events`: 8
- `detection_count`: 920

**Product Value**: Critical — 这是核心风险信号

**UI Priority**: P0 — 首屏核心

---

### L1: Audio Perception

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 2 | 场景支持吗？ | ⚪ Not Available | 无音频轨 |
| 7 | UI 优先级？ | **禁止** | 绝对禁止显示 |

**处理**: 完全隐藏音频面板，不显示"无音频"

---

### L2: Acoustic State

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 7 | UI 优先级？ | **禁止** | 无音频，禁止伪造 |

---

### L2: Risk Transition

同 telephone_risk L2 Risk — ✅ P0

---

## 四、repeated_visit（重复访问识别）

### 核心差异：记忆上下文

| Surface | telephone_risk | repeated_visit |
|---------|---------------|----------------|
| L5 Provenance | ✅ 完整 | ✅ **多 timepoint 对比** |
| L3 Evidence | 🟡 Partial | 🟡 Partial（视觉 + 历史） |
| Memory Context | ❌ 无 | ✅ **核心叙事** |

**repeated_visit 独特价值**:
- L5 具有跨 episode 证据对比能力
- 证明"Memory 能力"的核心场景

**记忆上下文面板规格**:
| 属性 | 值 |
|------|-----|
| 位置 | ① 主视觉区 右侧（大尺寸） |
| 数据源 | Memory Episodes（历史访问记录） |
| 显示 | "过去 → 昨天 → 今天"时间线 |
| 叙事 | "系统识别出重复访问模式" |
| Capability | ✅ Verified（ADR-0024/0025 Memory 架构） |

---

## 五、Key Findings & Corrections

### 5.1 必须修正的误导性标注

| Surface | Wireframe 标注 | 实际 Reality | 修正建议 |
|---------|---------------|--------------|---------|
| L1 Audio Perception | "~100ms continuous RMS stream" | Segment-level RMS (~1-2s) | 标注"RMS 分段值（非连续流）" |
| L2 Acoustic State | "Runtime Acoustic State Machine" | Golden Case pre-defined | 标注"声学状态（Golden Case 预定义）" |
| L3 Evidence Synthesis | "Cross-modal fusion" | cross_modal=0 (known limit) | 标注"主路径成立，无跨模态佐证" |
| Audio Buffer Level | "动态填充条" | **不存在** | **禁止显示** |

### 5.2 不能显示的 Capability

| Capability | 原因 |
|------------|------|
| Phone Detection | Benchmark Recall=0%（ADR-0038） |
| Audio Buffer Level | 未实现，禁止声称"音频正常" |
| Acoustic State Machine (Runtime) | 仅 Golden Case 有预定义 |
| Cross-Modal Fusion | 当前为 0，已知限制 |

### 5.3 必须标注 Simulated 的场景

| 场景 | Surface | 标注要求 |
|------|---------|---------|
| telephone_risk | L2 Acoustic State | "🎭 Golden Case 预定义，非 Runtime" |
| stranger_visit | L1 Audio | "SIMULATED 音频，不进入风险判断" |
| repeated_visit | L1 Audio | "SIMULATED 音频，不进入风险判断" |

---

## 六、Evidence Continuity 原则

```
❌ "检测到 449 个 person bounding box"
✅ "系统连续 10 秒检测到人员在场"

❌ "声学时 3 次状态变化"
✅ "声学状态从 NORMAL 跃迁至 STRESS"

❌ "8 个风险信号事件"
✅ "风险状态：观察 → 关注（声学状态变化 · 3.2s）"
```

**原则**: 连续状态 > 离散事件 > 事件数量

---

## 七、下一步行动

### 7.1 Wireframe 设计原则

每个场景的 Wireframe 应该：
1. 只显示该场景 Strong / Partial 的 Surface
2. 明确标注 Unavailable / Must Not Show 的 Surface
3. 优先展示该场景的核心产品故事
4. 对"No Event"场景，明确显示"无风险信号"而非隐藏

### 7.2 Wireframe 审批 checklist

- [ ] 每个 Surface 有对应的 Runtime Fact
- [ ] 没有伪造的实时感
- [ ] Fallback 状态明确
- [ ] 场景特异性清晰（不同场景不同布局）
- [ ] SIMULATED 音频不进入风险判断
- [ ] "无风险"场景明确显示"No Event"
- [ ] 每个 P0 Surface 回答 7 个问题均为"是/高"

---

**文档版本**: v0.1  
**最后更新**: 2026-08-21  
**状态**: Draft → 待与 Spec v2 联合审批  
**约束**: Capability available ≠ Surface useful ≠ Evidence continuous