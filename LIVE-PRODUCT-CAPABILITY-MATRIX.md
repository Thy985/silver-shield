# Live Product Capability Matrix — 产品能力矩阵

> **设计时间**: 2026-08-21  
> **状态**: Draft  
> **核心原则**: Capability 成熟度分级，只有 ✅ Verified 能力可进入 P0 UI

---

## 一、能力分级定义

| 等级 | 标识 | 含义 | 是否可进入 P0 UI |
|------|------|------|----------------|
| **Verified** | ✅ | 已通过 E2E 验证，Runtime Fact 真实可用 | 是 |
| **Partial** | 🟡 | 部分可用，需推断或降级处理 | 是（需标注） |
| **Missing** | 🔴 | 当前未实现，禁止展示 | 否 |
| **Simulated** | ● | Golden Case 预定义，非 Runtime | 仅场景注解层 |

---

## 二、Runtime Capability Audit

### 2.1 感知层

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| YOLO Person Detection | ✅ | `perception_delta.detections[]` | ✅ | bbox, conf, track_id |
| Visitor Tracking | ✅ | `track_id` 跨帧关联 | ✅ | dwell 时间可计算 |
| Audio Event Detection | ✅ | `evidence_delta.audio[].kind` | ✅ | telephone/distress |
| RMS Segment | ✅ | `evidence_delta.audio[].rms` | 🟡 | per-segment，非连续 |
| Windowed RMS Stream | 🔴 | 不存在 | ❌ | Phase 3 后端能力 |
| VAD Ratio | 🟡 | `audio_features.vad_ratio` | 🟡 | segment-level |
| Audio Buffer Level | 🔴 | 未实现 | ❌ | **禁止显示** |
| Phone Interaction Detection | 🔴 | Benchmark Recall=0% | ❌ | ADR-0038 已确认 |

### 2.2 风险层

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| RealTimeRiskEvaluator | ✅ | `risk_delta` | ✅ | MONITOR/RAISED/ CLEARED |
| Risk Transition | ✅ | `risk_delta.risk_transition` | ✅ | 状态跃迁可追踪 |
| Reason Summary | ✅ | `risk_delta.reason_summary[]` | ✅ | 必须来自 runtime |
| Risk Score | 🟡 | `perception_score` | 🟡 | 仅 LOW/MEDIUM/HIGH |
| Cross-Modal Link | 🟡 | `cross_modal_links[]` | 🟡 | 当前为 0（已知限制） |

### 2.3 决策层

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| Decision Policy | ✅ | `decision_policy.routing_table` | ✅ | LOW→MONITOR, HIGH→ESCALATE |
| Command Types | ✅ | `command_types` | ✅ | LOG_ONLY/MONITOR/NOTIFY/ESCALATE |
| Action Execution | ✅ | `action.executed` | ✅ | status=CONFIRMED |
| Notify Family | 🔴 | 未实现 | ❌ | **禁止显示为可执行** |
| Escalate Community | 🔴 | 未实现 | ❌ | **禁止显示为可执行** |

### 2.4 证据层

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| Evidence Projection | ✅ | `EvidenceProjection` | ✅ | 统一 View Model |
| Provenance Kind | ✅ | `provenance_kind` | ✅ | SIMULATED/REAL_SENSOR/FIXTURE |
| Media Reference | ✅ | `media_ref` | ✅ | frame_index, segment_id |
| Evidence Timeline | ✅ | `evidence_delta.timeline[]` | ✅ | 可回溯 |
| Evidence Graph | ✅ | `evidence_items + links` | ✅ | 因果链可视化 |
| Cross-Modal Evidence | 🟡 | `cross_modal_links[]` | 🟡 | 当前为 0 |

### 2.5 记忆层（Phase 2）

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| Memory Episodes | 🟡 | Memory API | 🟡 | 需暴露访问记录 |
| Memory Matching | 🔴 | 未实现 | ❌ | Phase 2 阻塞项 |
| Memory Pattern | 🔴 | 未实现 | ❌ | 需多次访问积累 |

### 2.6 音频健康度

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| Audio Available | 🟡 | `evidence_delta.audio[]` 非空推断 | 🟡 | 需前端 5s 判定 |
| Audio Stream Health | 🔴 | 无独立字段 | ❌ | **禁止声称"音频正常"** |
| Acoustic State Machine | 🔴 | Runtime 不存在 | ❌ | **禁止展示"NORMAL→STRESS"** |

---

## 三、Scenario Surface Matrix

### 3.1 telephone_risk — Audio-first

| Surface | Layer | Capability | 等级 | 是否显示 |
|---------|-------|------------|------|---------|
| L0 Runtime Presence | Shell | frame_tick + audio推断 | ✅ | ✅ |
| L1 Person Perception | Scenario | YOLO detection | ✅ | ✅（辅助） |
| L1 Audio Trust | Scenario | audio event 非空 | 🟡 | ✅ |
| L1 Audio Perception | Scenario | RMS segment | 🟡 | ✅（柱状图） |
| L2 Acoustic State | Scenario | **不存在** | 🔴 | ❌（替代：audio event 序列） |
| L2 Risk Transition | Shell | risk_delta | ✅ | ✅ |
| L3 Evidence Synthesis | Shell | evidence_items | ✅ | ✅ |
| L4 Action | Shell | commandMap | ✅ | ✅ |
| L5 Provenance | Shell | provenance_kind | ✅ | ✅ |

**Note**: L2 Acoustic State 当前用 `audio_event 序列` 替代，标注"声学事件序列（非状态机输出）"。

### 3.2 cctv_surveillance — Vision-first

| Surface | Layer | Capability | 等级 | 是否显示 |
|---------|-------|------------|------|---------|
| L0 Runtime Presence | Shell | frame_tick | ✅ | ✅ |
| L1 Person Perception | Scenario | YOLO detection | ✅ | ✅（P0，核心） |
| L1 Audio Trust | Scenario | **无音频轨** | 🔴 | ❌（完全隐藏） |
| L1 Audio Perception | Scenario | **无音频轨** | 🔴 | ❌（完全隐藏） |
| L2 Acoustic State | Scenario | **无音频轨** | 🔴 | ❌（完全隐藏） |
| L2 Risk Transition | Shell | risk_delta | ✅ | ✅ |
| L3 Evidence Synthesis | Shell | evidence_items | ✅ | ✅ |
| L4 Action | Shell | commandMap | ✅ | ✅ |
| L5 Provenance | Shell | provenance_kind | ✅ | ✅ |

**Note**: 所有音频面板**完全隐藏**，不显示"无音频"。

### 3.3 repeated_visit — Memory-first

| Surface | Layer | Capability | 等级 | 是否显示 |
|---------|-------|------------|------|---------|
| L0 Runtime Presence | Shell | frame_tick | ✅ | ✅ |
| L1 Person Perception | Scenario | YOLO detection | ✅ | ✅（P0） |
| L1 Audio Trust | Scenario | **无音频轨** | 🔴 | ❌（完全隐藏） |
| L2 Risk Transition | Shell | risk_delta | ✅ | ✅ |
| L3 Evidence Synthesis | Shell | evidence_items | ✅ | ✅ |
| L4 Action | Shell | commandMap | ✅ | ✅ |
| L5 Provenance | Shell | provenance_kind | ✅ | ✅ |
| Memory Context | Scenario | **Memory API** | 🟡 | 🟡（Phase 2） |

**Note**: Memory Context 依赖 Memory API，当前标注 Phase 2。

---

## 四、禁止行为清单

### 4.1 禁止展示的未实现能力

| 禁止内容 | 原因 | 正确做法 |
|---------|------|---------|
| `audio_buffer_level` | 🔴 未实现 | 隐藏或标注"暂不支持" |
| `windowed_rms_stream` | 🔴 未实现 | 仅展示 segment-level RMS |
| "音频正常" | 🔴 硬件健康度未知 | "最近检测到电话声" |
| "NORMAL → STRESS" | 🔴 Runtime 无状态机 | "检测到电话声 + 声音变化" |
| "通知家属"按钮 | 🔴 Notify Family 未实现 | "已记录 · 建议继续观察" |
| "升级社区"按钮 | 🔴 Escalate Community 未实现 | 同上 |
| 延迟 ~120ms | 🔴 前端单边估算 | 隐藏或标注"*估算值" |
| `frame_index` 原始值 | — | 转换为 `case_time` |
| `event_id` / `fingerprint` | — | 不进感知流 |

### 4.2 禁止的叙事偷换

| 错误叙事 | 正确叙事 |
|---------|---------|
| "系统检测到异常停留" | "检测到人员 · 当前处于异常时段" |
| "风险升高因为电话+声学压力" | "风险：关注 · 原因：未在白名单" |
| "声学状态：STRESS" | "检测到持续电话声" |
| "Audio Health: NORMAL" | "最近检测到持续电话声" |

---

## 五、Phase 路线图

### Phase 1: 基础感知流（当前）

- [x] PERSON_ENTERED / PERSON_PRESENT
- [x] AUDIO_DETECTED
- [x] RISK_RAISED / RISK_CLEARED
- [x] CURRENT STATE + RECENT CHANGES + HISTORY
- [x]telephone_risk 六层感知流（替代 Acoustic State）

### Phase 2: 场景适配

- [ ] PERSON_DWELLING（需后端 `abnormal_dwell` 事件）
- [ ] PERSON_REAPPEARED（需后端 `repeat_visit` 事件）
- [ ] MEMORY_MATCHED（需 Memory API 暴露）
- [ ] AUDIO_LEVEL_CHANGED（需后端 `rms_delta` 字段）

### Phase 3: 后端能力补齐

- [ ] `perception_delta` 增加 `person_present` 状态机
- [ ] `evidence_delta` 增加 `rms_window` 字段（连续波形）
- [ ] 新增 `memory_timeline` 消息类型

---

## 六、附录：Capability 状态机

```
Capability Maturity Model:

TECHNICAL FEASIBILITY
    ↓
INTEGRATION VALIDATION
    ↓
RUNTIME FACT VERIFICATION
    ↓
PRODUCT CONSUMER ACCEPTANCE
    ↓
PHASE 1 READY
```

---

**文档版本**: v0.1  
**最后更新**: 2026-08-21  
**状态**: Draft  
**配套**: `LIVE-PRODUCT-SURFACE-SPEC.md`（产品表面规格）