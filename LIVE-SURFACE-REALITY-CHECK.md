# Live Surface → Reality Check — Runtime Fact 审计

> **审计时间**: 2026-08-21  
> **审计目标**: 逐项验证每个 Surface 是否有真实 Runtime Fact 支撑  
> **核心问题**: Capability available ≠ Surface useful in current scenario ≠ Evidence continuous
> **配套**: `LIVE-PERCEPTION-STREAM-SPEC.md`（主规格）、`LIVE-PERCEPTION-STREAM-SEMANTICS.md`（语义表）

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
| 1 | 能力存在吗？ | ✅ 是 | WebSocket + frame_tick + audio event 推断 |
| 2 | 场景支持吗？ | ✅ Strong | 视频 + 音频双输入 |
| 3 | 数据连续？ | ✅ Continuous | 帧级 + 段级持续流动 |
| 4 | 变化明显？ | N/A | 存在性证据，非信号 |
| 5 | UI 实时表现？ | ✅ 是 | LIVE badge + 输入状态指示灯 |
| 6 | 可回溯？ | ✅ Full | case_time + websocket logs |
| 7 | UI 优先级？ | **P0** | 用户首先必须相信系统在工作 |

**实际 Runtime Evidence**:
- `frame_index`: 0-449（15s @ 30fps 媒体帧）
- `runtime_tick_count`: ~8 fps（配置帧率，≠ frame_index）
- `audio_segments`: 9 段（VAD 分割）
- `websocket_heartbeat`: 持续

**⚠️ Reality Check**:
- 不能声称"延迟 120ms"（前端估算，非端到端测量）
- 应标注"延迟 ~120ms*"

---

### ① OBSERVE: Video Input

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | MJPEG 流（`/mjpeg/{sid}`） |
| 2 | 场景支持吗？ | ✅ Strong | 完整视频输入 |
| 3 | 数据连续？ | ✅ Continuous | ~8 fps 持续推流 |
| 4 | 变化明显？ | ✅ Strong | 每帧有内容变化 |
| 5 | UI 实时表现？ | ✅ 是 | `<img src="/mjpeg/{sid}">` |
| 6 | 可回溯？ | ✅ Full | frame_index → case_time |
| 7 | UI 优先级？ | **P0** | 核心视觉输入 |

**实际 Runtime Evidence**:
- `frame_tick`: 每帧推送 base64 JPEG
- `case_time`: 从 frame_index 推导

**禁止**:
- ❌ 显示 `frame_index`（应显示 `case_time`）
- ❌ 显示检测数 overlay（`ov-det`）— 工程信息

---

### ① OBSERVE: Audio Input

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | 🟡 Partial | RMS segment 存在，非连续流 |
| 2 | 场景支持吗？ | ✅ Strong | 完整音频链路 |
| 3 | 数据连续？ | ⚠️ Per-segment | 段级离散值 |
| 4 | 变化明显？ | ✅ Strong | 0.20 → 0.05（4x 变化） |
| 5 | UI 实时表现？ | ✅ 是 | Canvas 柱状图（每段一根柱） |
| 6 | 可回溯？ | ✅ Full | 原始音频段可回放 |
| 7 | UI 优先级？ | **P0** | 音频场景的核心差异点 |

**实际 Runtime Evidence**:
- `rms_segment_0`: 0.2027
- `rms_segment_1`: 0.1942
- 连续窗口化值：**不存在**（需后端扩展）

**⚠️ Reality Check**:
- Wireframe 标注"~100ms 连续" → **错误**
- 实际：segment-level RMS（~1-2s 粒度）
- UI 必须标注："RMS 分段值（非连续流）"

---

### ② UNDERSTAND: CURRENT STATE

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | perception_delta + risk_delta |
| 2 | 场景支持吗？ | ✅ Strong | 核心叙事 |
| 3 | 数据连续？ | ✅ Continuous | 状态跨帧保持 |
| 4 | 变化明显？ | ✅ Strong | 人员在场 + 风险状态 |
| 5 | UI 实时表现？ | ✅ 是 | 原地刷新 |
| 6 | 可回溯？ | ✅ Full | event_id + timestamp |
| 7 | UI 优先级？ | **P0** | 用户最关心的"现在怎样" |

**实际 Runtime Evidence**:
- `perception_delta.detections[]`: track_id + conf
- `risk_delta.risk_level`: MONITOR/RAISED/CLEARED
- `risk_delta.reason_summary`: ["未在白名单"]

---

### ② UNDERSTAND: RECENT CHANGES

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | evidence_delta.perception_events + audio |
| 2 | 场景支持吗？ | ✅ Strong | 核心叙事 |
| 3 | 数据连续？ | ⚠️ Per-event | 事件驱动 |
| 4 | 变化明显？ | ✅ Strong | 新事件立即入场 |
| 5 | UI 实时表现？ | ✅ 是 | 动画入场 |
| 6 | 可回溯？ | ✅ Full | media_ref → 原始证据 |
| 7 | UI 优先级？ | **P0** | 状态变化的增量流 |

**实际 Runtime Evidence**:
- `evidence_delta.perception_events[]`: PERSON_ENTERED / PERSON_REAPPEARED
- `evidence_delta.audio[]`: AUDIO_DETECTED / AUDIO_LEVEL_CHANGED
- `risk_delta.risk_transition`: raised/cleared

---

### ② UNDERSTAND: HISTORY

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | perceptionStream.history |
| 2 | 场景支持吗？ | ✅ Strong | 可展开查看 |
| 3 | 数据连续？ | ⚠️ Per-event | 历史条目 |
| 4 | 变化明显？ | N/A | 折叠区 |
| 5 | UI 实时表现？ | ✅ 是 | 点击展开 |
| 6 | 可回溯？ | ✅ Full | 同 RECENT CHANGES |
| 7 | UI 优先级？ | **P1** | 次要叙事 |

---

### RISK

| # | 问题 | 答案 | 说明 |
|---|------|------|------|
| 1 | 能力存在吗？ | ✅ 是 | RealTimeRiskEvaluator |
| 2 | 场景支持吗？ | ✅ Strong | 核心判断 |
| 3 | 数据连续？ | ⚠️ Per-event | 风险跃迁 |
| 4 | 变化明显？ | ✅ Strong | MONITOR → RAISED |
| 5 | UI 实时表现？ | ✅ 是 | 状态徽章 + 原因 |
| 6 | 可回溯？ | ✅ Full | signal_id + decision_trace |
| 7 | UI 优先级？ | **P0** | 用户最关心的判断 |

**实际 Runtime Evidence**:
- `risk_delta.risk_transition`: raised/cleared
- `risk_delta.reason_summary`: ["未在白名单"]

**⚠️ Reality Check**:
- 禁止产品预写原因（如"声学状态变化 + 电话交互"）
- 必须使用 `reason_summary[]`（来自 runtime）

---

### VERIFY

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

---

## 三、cctv_surveillance（夜间异常访问）

### 与 telephone_risk 的关键差异

| Surface | telephone_risk | cctv_surveillance |
|---------|---------------|-------------------|
| ① Audio Input | ✅ Strong | ❌ **完全隐藏** |
| ② Audio Events | ✅ Strong | ❌ **禁止显示** |
| ① Video Input | 🟡 Partial（辅助） | ✅ **P0（核心）** |
| ② PERSON_REAPPEARED | 🟡 Partial | ✅ **P0（核心叙事）** |

**处理规则**:
- 音频面板**完全隐藏**，不显示"无音频"
- 感知流中**过滤掉**所有 audio 类型条目
- Person Perception 升级为 P0（核心风险信号）

---

## 四、repeated_visit（重复访问识别）

### 核心差异

| Surface | telephone_risk | repeated_visit |
|---------|---------------|----------------|
| ② MEMORY_MATCHED | ❌ 无 | ✅ **核心叙事** |
| ② PERSON_REAPPEARED | 🟡 Partial | ✅ **P0** |
| VERIFY | 视频 + 音频证据 | **视频 + 历史访问记录** |

**Memory 能力审计**:
- `Memory Episodes API`: 🟡 Partial（已实现，但未完全暴露）
- `Memory Matching`: 🔴 Missing（Phase 2 阻塞项）
- `Memory Pattern`: 🔴 Missing（需多次访问积累）

**UI 处理**:
- `MEMORY_MATCHED` 条目标注"🟡 Phase 2"
- 不阻断主叙事（PERSON_REAPPEARED + RISK 仍是核心）

---

## 五、Key Findings & Corrections

### 5.1 必须修正的误导性标注

| Surface | Wireframe 标注 | 实际 Reality | 修正建议 |
|---------|---------------|--------------|---------|
| ① Audio Input | "~100ms continuous RMS stream" | Segment-level RMS (~1-2s) | 标注"RMS 分段值（非连续流）" |
| ② Acoustic State | "Runtime State Machine" | **不存在** | **禁止展示**，改为 audio_event 序列 |
| ② Cross-modal | "Cross-modal fusion" | cross_modal=0 (known limit) | 标注"主路径成立，无跨模态佐证" |
| L0 Audio | "音频正常" | **硬件健康度未知** | 改为"最近检测到电话声" |
| L0 Delay | "延迟 120ms" | **前端估算，非端到端** | 标注"延迟 ~120ms*" |

### 5.2 不能显示的 Capability

| Capability | 原因 |
|------------|------|
| Phone Detection | Benchmark Recall=0%（ADR-0038） |
| Audio Buffer Level | 未实现，禁止声称"音频正常" |
| Acoustic State Machine (Runtime) | Runtime 无状态机，仅 Golden Case 有预定义 |
| Cross-Modal Fusion | 当前为 0，已知限制 |
| "音频正常/中断"二元判断 | 应为三值状态（RECENT_EVENT/NO_RECENT_EVENT/UNAVAILABLE） |

### 5.3 必须标注 Simulated 的场景

| 场景 | Surface | 标注要求 |
|------|---------|---------|
| telephone_risk | ② Acoustic State (if shown) | "🎭 Golden Case 预定义，非 Runtime" |
| stranger_visit | ① Audio Input | "SIMULATED 音频，不进入风险判断" |
| repeated_visit | ① Audio Input | "SIMULATED 音频，不进入风险判断" |

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

## 七、下一步

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
