# Live Product Surface Spec — 产品表面规格

> **设计时间**: 2026-08-21  
> **状态**: Draft v2.1（已对齐感知流规格）  
> **核心架构**: 左侧 OBSERVE（传感器输入）+ 右侧 UNDERSTAND（实时感知流）+ 底部 RISK/VERIFY  
> **配套文档**: `LIVE-PERCEPTION-STREAM-SPEC.md`（主规格）、`LIVE-PERCEPTION-STREAM-SEMANTICS.md`（语义表）

---

## 一、架构总览

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  [L0] Runtime Presence     🟢 LIVE · 视频正常 · 🔊 最近声音事件 · 延迟 ~120ms*          │
├──────────────────────────┬──────────────────────────────────────────────────────┤
│                          │                                                      │
│   ① OBSERVE              │   ② UNDERSTAND（实时感知流）                          │
│   （传感器输入）           │   （AI 从输入中实时感知的状态变化）                    │
│                          │                                                      │
│   ┌──────────────────┐   │   ┌─ CURRENT STATE（持续状态，原地刷新）─────────┐    │
│   │                  │   │   │ 👤 1 人持续在场 Xs                         │    │
│   │     VIDEO        │   │   │ 🔊 最近检测到持续电话声                       │    │
│   │   (MJPEG 流)     │   │   │ ⚠ 风险：关注                                  │    │
│   │                  │   │   └───────────────────────────────────────────────┘    │
│   │   Overlay:       │   │                                                      │
│   │   case_time=5.2s │   │   ┌─ RECENT CHANGES（瞬时事件，去重后入场）──────┐    │
│   │                  │   │   │ 14:32:08  🔊 检测到电话声                    │    │
│   └──────────────────┘   │   │ 14:32:15  ⚠ 风险状态升高                     │    │
│                          │   │ 14:32:05  👤 首次出现                        │    │
│   ┌──────────────────┐   │   └───────────────────────────────────────────────┘    │
│   │                  │   │                                                      │
│   │     AUDIO        │   │   ┌─ HISTORY > 查看更多 ──────────────────────────┐  │
│   │                  │   │   │ 14:31:58  👤 发现 1 人进入画面                  │  │
│   │   ▂▃▅▆▇▆▅▃▂     │   │   └───────────────────────────────────────────────┘  │
│   │   RMS 分段值     │   │                                                      │
│   │   (非连续流)     │   │                                                      │
│   └──────────────────┘   │                                                      │
│                          │                                                      │
├──────────────────────────┴──────────────────────────────────────────────────────┤
│  [RISK] 关注 · 未在白名单 · 建议：继续观察                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│  [VERIFY] 查看声音证据 · 查看视频证据 · 查看完整时间线                             │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 核心交互范式

| 区域 | 问题 | 答案 |
|------|------|------|
| **OBSERVE** | "系统现在接收到什么？" | 视频 + 音频的实时输入 |
| **UNDERSTAND** | "系统从输入中实时感知到什么？" | 状态变化的增量流 |
| **RISK** | "所以现在需要做什么？" | 风险状态 + 建议行动 |
| **VERIFY** | "为什么相信？" | 证据回溯 + 原始媒体 |

---

## 二、场景适配层

虽然核心架构统一，但不同场景在 OBSERVE 和 UNDERSTAND 区域有差异化展示：

### 2.1 telephone_risk（Audio-first）

```
OBSERVE:
  - Video (MJPEG 流，较小)
  - Audio (RMS 分段柱状图，较大)

UNDERSTAND:
  CURRENT STATE:
    👤 1 人持续在场 5.2s
    🔊 最近检测到持续电话声
    ⚠ 风险：关注

  RECENT CHANGES:
    14:32:05  👤 发现 1 人进入画面
    14:32:08  🔊 检测到电话声
    14:32:15  📈 最近音频片段声音强度明显变化
    14:32:15  ⚠ 风险状态：观察 → 关注 · 未在白名单

  [RISK] 关注 · 未在白名单 · 建议：继续观察
  [VERIFY] 查看声音证据 · 查看视频证据 · 查看完整时间线
```

> **禁止**：展示 "NORMAL → ATTENTION → AROUSAL → STRESS"（Golden Case 叙事，非 Runtime）
> **正确**：展示 audio_event 序列 + RMS 变化趋势

---

### 2.2 cctv_surveillance（Vision-first）

```
OBSERVE:
  - Video (MJPEG 流，夜间模式，full-width)

UNDERSTAND:
  CURRENT STATE:
    👤 1 人持续在场 9.5s
    ⚠ 风险：关注

  RECENT CHANGES:
    14:30:00  👤 发现 1 人进入画面
    14:30:07  ⏱ 停留超过阈值（持续 7s）🟡 Phase 2
    14:30:45  🔁 访客#1 再次出现（第 3 次）
    14:30:45  ⚠ 风险状态：观察 → 关注 · 异常停留 + 重复访问

  [RISK] 关注 · 异常停留 + 重复访问 · 建议：通知家属
  [VERIFY] 查看视频证据 · 查看完整时间线
```

> **完全隐藏**：所有音频相关感知流条目（无 audio evidence 数据源）

---

### 2.3 repeated_visit（Memory-first）

```
OBSERVE:
  - Video (MJPEG 流)

UNDERSTAND:
  CURRENT STATE:
    ⚠ 风险：关注

  RECENT CHANGES:
    14:30:00  👤 发现 1 人进入画面
    14:30:05  🔁 访客#1 再次出现（第 3 次）
    14:30:05  🧠 记忆关联：3 天前访问（ep_001）🟡 Phase 2
    14:30:05  ⚠ 风险状态：观察 → 关注 · 重复访问

  [RISK] 关注 · 重复访问模式识别 · 建议：通知家属
  [VERIFY] 查看视频证据 · 查看历史访问记录 · 查看完整时间线
```

> `MEMORY_MATCHED` 依赖 Memory API，标注 Phase 2。

---

### 2.4 delivery_courier_normal（Negative Control）

```
OBSERVE:
  - Video (MJPEG 流)

UNDERSTAND:
  CURRENT STATE:
    👤 1 人持续在场 3.2s

  RECENT CHANGES:
    （无新事件，显示"持续观察中"）

  [RISK] 观察 · 无异常 · 建议：继续观察
  [VERIFY] 查看视频证据 · 查看完整时间线
```

> **关键**：明确显示"无风险信号"，而非隐藏 L2-L4

---

### 2.5 evidence_insufficient（Edge Case）

```
OBSERVE:
  - Video (MJPEG 流，低质量)

UNDERSTAND:
  CURRENT STATE:
    ⚠ 风险：观察

  RECENT CHANGES:
    14:30:00  👤 检测到疑似人员（低置信 0.31）
    （证据不足，不做风险升级）

  [RISK] 观察 · 证据不足 · 建议：继续观察
  [VERIFY] 查看视频证据 · 查看置信度详情
```

> **关键**：证明系统知道"什么时候不能下结论"

---

## 三、能力成熟度分级

| 等级 | 标识 | 含义 | 是否可进入 UI |
|------|------|------|-------------|
| **Verified** | ✅ | 已通过 E2E 验证，Runtime Fact 真实可用 | 是 |
| **Partial** | 🟡 | 部分可用，需推断或降级处理 | 是（需标注） |
| **Missing** | 🔴 | 当前未实现，禁止展示 | 否 |
| **Simulated** | ● | Golden Case 预定义，非 Runtime | 仅场景注解层 |

### 3.1 感知层

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

### 3.2 风险层

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| RealTimeRiskEvaluator | ✅ | `risk_delta` | ✅ | MONITOR/RAISED/ CLEARED |
| Risk Transition | ✅ | `risk_delta.risk_transition` | ✅ | 状态跃迁可追踪 |
| Reason Summary | ✅ | `risk_delta.reason_summary[]` | ✅ | 必须来自 runtime |
| Risk Score | 🟡 | `perception_score` | 🟡 | 仅 LOW/MEDIUM/HIGH |
| Cross-Modal Link | 🟡 | `cross_modal_links[]` | 🟡 | 当前为 0（已知限制） |

### 3.3 决策层

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| Decision Policy | ✅ | `decision_policy.routing_table` | ✅ | LOW→MONITOR, HIGH→ESCALATE |
| Command Types | ✅ | `command_types` | ✅ | LOG_ONLY/MONITOR/NOTIFY/ESCALATE |
| Action Execution | ✅ | `action.executed` | ✅ | status=CONFIRMED |
| Notify Family | 🔴 | 未实现 | ❌ | **禁止显示为可执行** |
| Escalate Community | 🔴 | 未实现 | ❌ | **禁止显示为可执行** |

### 3.4 证据层

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| Evidence Projection | ✅ | `EvidenceProjection` | ✅ | 统一 View Model |
| Provenance Kind | ✅ | `provenance_kind` | ✅ | SIMULATED/REAL_SENSOR/FIXTURE |
| Media Reference | ✅ | `media_ref` | ✅ | frame_index, segment_id |
| Evidence Timeline | ✅ | `evidence_delta.timeline[]` | ✅ | 可回溯 |
| Evidence Graph | ✅ | `evidence_items + links` | ✅ | 因果链可视化 |
| Cross-Modal Evidence | 🟡 | `cross_modal_links[]` | 🟡 | 当前为 0 |

### 3.5 记忆层（Phase 2）

| 能力 | 等级 | Runtime Fact | UI 可用 | 备注 |
|------|------|-------------|---------|------|
| Memory Episodes | 🟡 | Memory API | 🟡 | 需暴露访问记录 |
| Memory Matching | 🔴 | 未实现 | ❌ | Phase 2 阻塞项 |
| Memory Pattern | 🔴 | 未实现 | ❌ | 需多次访问积累 |

---

## 四、禁止行为清单

### 4.1 禁止展示的工程信息

| 工程字段 | 原因 |
|---------|------|
| `frame_index` | 应转换为 `case_time` |
| `event_id` | 技术标识，用户无需知道 |
| `fingerprint` | 内部校验用，不进感知流 |
| `visitor_instance_id` | 应翻译为"访客#N" |
| `track_id` | 应翻译为"访客#N" |
| `server_ts` | 应转换为 `case_time` |
| `audio_buffer_level` | 🔴 未实现，禁止显示 |
| `vad_ratio` | 当前仅有 segment-level，禁止声称实时 |
| `runtime_tick_count` | 不应等于 `frame_index` |

### 4.2 禁止的文案表述

| 禁止文案 | 原因 | 正确文案 |
|---------|------|---------|
| "音频正常" | 硬件健康度未知 | "最近检测到持续电话声" |
| "音频中断" | 可能是静默期 | "最近无声音事件" |
| "NORMAL → ATTENTION → STRESS" | Runtime 无此状态机 | "检测到电话声 + 声音强度变化" |
| "风险升高因为电话+声学压力" | 产品预写，非 runtime 输出 | "风险：关注 · 原因：未在白名单" |
| "系统正在分析声音" | 伪实时感 | "最近检测到持续电话声" |
| "延迟 ~120ms" | 前端单边估算，非真实端到端延迟 | 隐藏或标注"*基于前端估算" |

### 4.3 禁止的行为

| 行为 | 原因 |
|------|------|
| 用静态动画模拟活动 | 伪造实时感 |
| 隐藏输入中断状态 | 缺乏透明度 |
| 伪造帧计数 | `frame_index ≠ runtime_tick_count` |
| 显示未实现的能力 | 如 `audio_buffer_level` |
| 让 Runtime Event 改变 Layout | 应保持 Narrative Mode 稳定 |
| 混合 Golden Case 叙事与 Runtime 感知 | 违反 D5 三层隔离 |

---

## 五、Evidence Continuity 原则

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

## 六、实现优先级

### Phase 1: 基础感知流（无需后端变更）

- [ ] 实现 `perceptionStream` 数据结构
- [ ] 实现 `_renderPerceptionStream()` 渲染函数
- [ ] 对接 `evidence_delta.perception_events` → 行为里程碑
- [ ] 对接 `evidence_delta.audio` → 音频事件
- [ ] 对接 `risk_delta.risk_transition` → 风险跃迁
- [ ] 实现 CSS 样式

### Phase 2: 场景适配（无需后端变更）

- [ ] telephone_risk: 完整六层感知流
- [ ] cctv_surveillance: 视觉闭环感知流
- [ ] repeated_visit: 记忆关联感知流（需 Memory API）

### Phase 3: 后端能力补齐（阻塞项）

- [ ] `perception_delta` 增加 `person_present` 状态机（"持续 N 人在场"）
- [ ] `evidence_delta` 增加 `rms_window` 字段（连续波形）
- [ ] 新增 `memory_timeline` 消息类型（历史访问记录）

---

## 七、验收清单

- [ ] 右侧结构为 CURRENT STATE + RECENT CHANGES + HISTORY 三层
- [ ] 右侧感知流只展示状态变化，不展示持续状态（除 PERSON_PRESENT）
- [ ] 工程字段（frame_index, event_id）不进入感知流
- [ ] 每种事件类型有独立颜色标识
- [ ] 历史记录可展开查看
- [ ] 场景切换时感知流清空
- [ ] 无伪造的实时感（frame_index ≠ runtime_tick_count）
- [ ] Fallback 状态明确（无事件时显示"持续观察中"）
- [ ] **Acoustic State**: Runtime 无状态机时，不展示"NORMAL→STRESS"
- [ ] **Risk Reason**: 严格使用 `reason_summary[]`，不展示产品预写文案
- [ ] **Golden Case**: `golden_audio_state` 仅 SIMULATED provenance 可展示，且标注🎭
- [ ] **L0 Audio**: 使用"最近声音事件"而非"音频正常"
- [ ] **场景适配**: 不同场景有不同布局权重（telephone/audio-first, cctv/vision-first, repeated/memory-first）
- [ ] **"无风险"场景**: 明确显示"No Event"而非隐藏
- [ ] `pytest tests/ -q` 全部通过
- [ ] `ruff check src/ tests/` 无 error

---

**文档版本**: v2.1  
**最后更新**: 2026-08-21  
**状态**: Draft → 待 Owner 审批  
**配套**: `LIVE-PERCEPTION-STREAM-SPEC.md`（主规格）、`LIVE-PERCEPTION-STREAM-SEMANTICS.md`（语义表）
