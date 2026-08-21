# Live Perception Stream — 右侧实时感知流规格

> **设计时间**: 2026-08-21  
> **状态**: Contract Freeze → 待 Owner 审批  
> **核心原则**: Raw → Product Fact → Technical Detail（右侧是 Product Fact，Details 是 Raw Fact）
> **配套审计**: `LIVE-SURFACE-REALITY-CHECK.md`（Runtime Fact 真实性验证）

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

## 二、右侧感知流设计规范

### 2.1 展示原则

**只展示状态变化，不展示持续状态**。

```
❌ 错误（Raw Delta）:
   F127 检测到 1 个目标
   F128 检测到 1 个目标
   F129 检测到 1 个目标
   ...（刷屏）

✅ 正确（Product Fact）:
   14:32:05  👤 首次出现（访客#1 进入门口画面）
   ...（人员持续在场，不重复显示）
   14:35:22  👤 停留超过阈值（持续 3 分 17 秒）
```

### 2.2 增量事件类型

右侧感知流应该展示以下增量事件（完整语义表见 `LIVE-PERCEPTION-STREAM-SEMANTICS.md`）：

| Semantic Event | 类别 | Runtime 原始事实 | UI 文案 | 数据源 |
|---------------|------|-----------------|---------|--------|
| `PERSON_ENTERED` | A.新事实 | `perception_events[]` 新 track_id | `👤 发现 1 人进入画面` | `evidence_delta.perception_events` |
| `PERSON_PRESENT` | A.新事实 | `perception_delta.detections[]` track 持续存在（> N 秒） | `👤 1 人持续在场 X.Xs` | `evidence_delta.perception_delta` |
| `PERSON_DWELLING` 🟡 Phase 2 | B.状态跃迁 | `perception_events[].event_type = abnormal_dwell` | `⏱ 停留超过阈值（持续 Xs）` | `evidence_delta.perception_events` |
| `PERSON_REAPPEARED` | A.新事实 | `perception_events[].event_type = repeat_visit` | `🔁 访客#1 再次出现（第 N 次）` | `evidence_delta.perception_events` |
| `AUDIO_DETECTED` | A.新事实 | `evidence_delta.audio[].kind` 新 event_id | `🔊 检测到持续电话声` | `evidence_delta.audio` |
| `AUDIO_LEVEL_CHANGED` | B.状态跃迁 | `evidence_delta.audio[].rms_delta > threshold` | `📈 最近音频片段声音强度明显变化` | `evidence_delta.audio` |
| `RISK_RAISED` | B.状态跃迁 | `risk_delta.risk_transition = raised` | `⚠ 风险状态：观察 → 关注 · reason_summary` | `risk_delta` |
| `RISK_CLEARED` | B.状态跃迁 | `risk_delta.risk_transition = cleared` | `✓ 风险状态：关注 → 解除` | `risk_delta` |
| `GOLDEN_AUDIO_STATE` | — | （场景注解层，非感知流本体） | `🎭 声学状态：ATTENTION → STRESS`（仅 Golden Case，浅灰底色 + 🎭 图标） | `evidence_delta.timeline` |

> **类别说明**：A.新事实 = 新增条目；B.状态跃迁 = 原地更新或阈值触发的事件。

### 2.3 不展示的事件

以下事件**不应**出现在右侧感知流：

| 事件 | 原因 |
|------|------|
| `frame_tick` | 纯进度心跳，无业务语义 |
| `perception_delta.detections[]` | 裸检测列表，非状态变化 |
| `risk_delta`（指纹未变） | 风险状态未变化，不推送 |
| `evidence_delta`（timeline 无新节点） | 无新证据 |
| 技术字段（`frame_index`, `event_id`, `fingerprint`） | 进 Details，不进感知流 |
| 声学状态叙事（NORMAL→ATTENTION→STRESS） | 仅 Golden Case 在场景注解层展示 |

---

### 2.4 L0 Audio Health 语义定义

音频健康度为**三值状态**（非二元"正常/中断"）：

| 状态 | 判定条件 | 含义 |
|------|---------|------|
| `RECENT_EVENT` | `evidence_delta.audio[]` 非空（最近有音频事件） | 最近检测到音频事件 |
| `NO_RECENT_EVENT` | 5s 内无 audio event | 当前无事件，**非**"音频中断"，可能是静默期 |
| `UNAVAILABLE` | 场景本身无音频轨（如 `cctv_surveillance`） | 场景硬件配置无音频 |

**禁止映射**：
- ❌ `RECENT_EVENT` ≠ "音频正常"（硬件健康度未知）
- ❌ `NO_RECENT_EVENT` ≠ "音频中断"（可能是静默期）
- ✅ 正确文案："最近检测到持续电话声" / "最近无声音事件"

---

## 三、左侧 OBSERVE 区规范

### 3.1 视频输入

| 属性 | 值 |
|------|-----|
| **显示** | MJPEG 流（`/mjpeg/{scenario_id}`） |
| **Overlay** | frame_count + case_time（节流 150ms） |
| **数据来源** | `frame_tick` (gateway.py:272) |
| **更新频率** | 每帧（节流后 ~6.7fps） |
| **能力状态** | ✅ Verified |

**禁止行为**:
- ❌ 显示 `frame_index` 原始值（应显示为 `case_time`）
- ❌ 显示检测数 overlay（`ov-det`）——这是工程信息，不是产品信息

### 3.2 音频输入

| 属性 | 值 |
|------|-----|
| **显示** | RMS 分段柱状图（非连续波形） |
| **数据来源** | `evidence_delta.audio[].rms` |
| **更新频率** | per-segment（~1-2s） |
| **能力状态** | 🟡 Partial（无连续流） |
| **标注** | "RMS 分段值（非连续流）" |

**注意**: 当前系统无 `windowed_rms_stream`，只能展示 segment-level RMS。

---

## 四、现有 WebSocket 事件 → 感知流映射

### 4.1 已完成映射（可直接使用）

| WebSocket 事件 | 感知流条目 | 前端处理 |
|---------------|-----------|---------|
| `evidence_delta.perception_events[]` | 👤 首次出现 / ⏱ 停留超时 / 🔁 再次出现 | `_ingestBehavior()` + `_renderBehaviorTimeline()` |
| `evidence_delta.audio[]` | 🔊 检测到持续电话声 / 🎵 哭腔/求助 | `_appendAudioEventItem()` |
| `risk_delta`（transition=raised/cleared） | ⚠ 风险状态：MONITOR → RAISED | `_applyRiskDelta()` + `_applyRiskSignal()` |
| `evidence_delta.timeline[]`（type=golden_audio_state） | 📈 声学状态：ATTENTION → STRESS | `_humanSummary()` 已翻译 |

### 4.2 需要扩展的映射

| 需求 | 当前状态 | Gap | 修复方案 |
|------|---------|-----|---------|
| "持续 N 人在场"状态保持 | `perception_delta.detections[]` 每帧推送 | 无状态机，只有裸检测列表 | 后端新增 `person_present` 状态机 |
| 音频健康度推断 | 无独立 `audio_segment_tick` | 需前端从 `evidence_delta.audio` 推断 | 前端实现 5s 无事件判定 |
| 风险持续时间 | 无独立字段 | 前端可推导（从 `risk_transition=raised` 时间戳） | 前端实现 |

---

## 五、右侧感知流实现方案

### 5.1 数据结构

```javascript
// 右侧感知流条目
var perceptionStream = {
  entries: [],           // 当前显示的条目（最多 10 条）
  history: [],           // 折叠的历史条目
  maxVisible: 10,
  maxHistory: 50,
  
  // 添加新条目
  push: function(entry) {
    this.entries.unshift(entry);
    if (this.entries.length > this.maxVisible) {
      this.history.push(this.entries.pop());
    }
  },
  
  // 清空（场景切换时）
  clear: function() {
    this.entries = [];
    this.history = [];
  }
};

// 条目格式
var entry = {
  timestamp: '14:32:05',     // case_time 格式
  icon: '👤',                // 图标
  label: '首次出现',          // 人话标签
  detail: '访客#1 进入门口画面', // 可选详情
  type: 'behavior'           // 类型（behavior/audio/risk/acoustic）
};
```

### 5.2 事件处理映射

```javascript
// 1. perception_events → 行为里程碑
function _onPerceptionEvent(pe) {
  var behav = _BEHAV[pe.event_type];
  if (!behav) return;
  
  var vid = pe.visitor_id || '';
  var who = _friendlyVisitor(vid);
  var detail = pe.location ? ('位置 ' + pe.location) : '';
  
  perceptionStream.push({
    timestamp: formatCaseTime(pe.case_time),
    icon: behav.icon,
    label: behav.label,
    detail: who + (detail ? ' · ' + detail : ''),
    type: 'behavior'
  });
}

// 2. audio events → 音频感知
function _onAudioEvent(a) {
  var kindZh = _AUDIO_KIND_ZH[a.kind] || a.kind;
  perceptionStream.push({
    timestamp: formatCaseTime(a.timestamp),
    icon: '🔊',
    label: '检测到' + kindZh,
    detail: '',
    type: 'audio'
  });
}

// 3. risk transition → 风险感知
function _onRiskTransition(msg) {
  if (msg.risk_transition === 'raised') {
    perceptionStream.push({
      timestamp: formatCaseTime(msg.case_time),
      icon: '⚠',
      label: '风险状态：观察 → 关注',
      detail: msg.reason_summary ? msg.reason_summary.join(' + ') : '',
      type: 'risk'
    });
  } else if (msg.risk_transition === 'cleared') {
    perceptionStream.push({
      timestamp: formatCaseTime(msg.case_time),
      icon: '✓',
      label: '风险状态：关注 → 解除',
      detail: '',
      type: 'risk'
    });
  }
}
```

### 5.3 渲染逻辑

```javascript
function _renderPerceptionStream() {
  var container = global.document.getElementById('perception-stream-' + sid);
  if (!container) return;
  
  // 渲染当前条目
  var html = '<div class="ps-now">现在</div>';
  html += '<div class="ps-divider"></div>';
  
  perceptionStream.entries.forEach(function(entry) {
    html += _renderPsEntry(entry);
  });
  
  // 渲染历史记录（折叠）
  if (perceptionStream.history.length > 0) {
    html += '<details class="ps-history">';
    html += '<summary>历史感知 (' + perceptionStream.history.length + ' 条)</summary>';
    perceptionStream.history.slice(0, 10).reverse().forEach(function(entry) {
      html += _renderPsEntry(entry);
    });
    html += '</details>';
  }
  
  container.innerHTML = html;
}

function _renderPsEntry(entry) {
  return '<div class="ps-entry">' +
    '<span class="ps-time">' + entry.timestamp + '</span>' +
    '<span class="ps-icon">' + entry.icon + '</span>' +
    '<span class="ps-label">' + entry.label + '</span>' +
    (entry.detail ? '<span class="ps-detail">' + entry.detail + '</span>' : '') +
    '</div>';
}
```

---

## 六、CSS 样式规范

```css
/* 右侧感知流容器 */
.perception-stream {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  max-height: 400px;
  overflow-y: auto;
}

/* 现在标记 */
.ps-now {
  font-size: 11px;
  font-weight: 600;
  color: var(--status-live);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

/* 分隔线 */
.ps-divider {
  height: 1px;
  background: var(--border-color);
  margin: 4px 0;
}

/* 感知条目 */
.ps-entry {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px;
  background: #f8fafc;
  border-radius: 6px;
  border-left: 3px solid var(--modality-vision);
  animation: ps-enter 0.3s ease-out;
}

@keyframes ps-enter {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 不同类型着色 */
.ps-entry[type="behavior"] { border-left-color: var(--modality-vision); }
.ps-entry[type="audio"] { border-left-color: var(--modality-audio); }
.ps-entry[type="risk"] { border-left-color: var(--risk-raised); }
.ps-entry[type="acoustic"] { border-left-color: var(--modality-action); }

/* 时间戳 */
.ps-time {
  font-size: 10px;
  color: var(--muted-color);
  min-width: 50px;
}

/* 图标 */
.ps-icon {
  font-size: 16px;
  min-width: 20px;
}

/* 标签 */
.ps-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

/* 详情 */
.ps-detail {
  font-size: 11px;
  color: var(--muted-color);
  margin-left: auto;
}

/* 历史记录折叠 */
.ps-history {
  margin-top: 8px;
}

.ps-history summary {
  font-size: 11px;
  color: var(--muted-color);
  cursor: pointer;
  padding: 4px 0;
}
```

---

## 七、场景适配

### 7.1 telephone_risk

```
OBSERVE:
  - Video (MJPEG 流)
  - Audio (RMS 分段柱状图)

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
```

> **禁止**：展示 "NORMAL → ATTENTION → AROUSAL → STRESS"（Golden Case 叙事，非 Runtime）
> **正确**：展示 audio_event 序列 + RMS 变化趋势

### 7.2 cctv_surveillance

```
OBSERVE:
  - Video (MJPEG 流，夜间模式)

UNDERSTAND:
  CURRENT STATE:
    👤 1 人持续在场 9.5s
    ⚠ 风险：关注

  RECENT CHANGES:
    14:30:00  👤 发现 1 人进入画面
    14:30:07  ⏱ 停留超过阈值（持续 7s）🟡 Phase 2
    14:30:45  🔁 访客#1 再次出现（第 3 次）
    14:30:45  ⚠ 风险状态：观察 → 关注 · 异常停留 + 重复访问
```

> **完全隐藏**：所有音频相关感知流条目（无 audio evidence 数据源）

### 7.3 repeated_visit

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
```

> `MEMORY_MATCHED` 依赖 Memory API，标注 Phase 2。

---

## 八、实现优先级

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

## 九、VERIFY 信任闭环规范

每个感知流条目支持"**一键回证据**"，让用户验证系统判断的依据：

| 感知流类型 | 触发操作 | 实现方式 |
|-----------|---------|---------|
| 音频事件（`AUDIO_DETECTED` / `AUDIO_LEVEL_CHANGED`） | seek 到对应 audio case_time，播放音频片段 | entry 携带 `media_ref.audio_segment_id` + `seek_position`；点击调用 `_seekAndPlayAudio()` |
| 人员事件（`PERSON_ENTERED` / `PERSON_PRESENT` / `PERSON_REAPPEARED`） | seek 到对应 video frame，高亮 bbox | entry 携带 `media_ref.frame_index`；点击调用 `_highlightFrame()` |
| 风险事件（`RISK_RAISED` / `RISK_CLEARED`） | 展开 `risk_delta` 完整字段 | entry 携带 `risk_delta` 完整 payload；点击展开 Details |
| 记忆事件（`MEMORY_MATCHED`） | 跳转到 Memory Episode 详情 | entry 携带 `episode_id`；点击调用 `_navigateToEpisode()` |

**实现要点**：
```javascript
// 每条感知流条目携带媒体引用
var entry = {
  timestamp: '14:32:08',
  icon: '🔊',
  label: '检测到持续电话声',
  type: 'audio',
  media_ref: {
    kind: 'audio_segment',
    segment_id: 'aud_seg_001',
    seek_position: 12.4   // case_time
  },
  // 或
  media_ref: {
    kind: 'video_frame',
    frame_index: 1432,
    bbox: { x: 120, y: 80, w: 60, h: 140 }
  }
};

// 点击处理
function _onEntryClick(entry) {
  if (entry.media_ref.kind === 'audio_segment') {
    _seekAndPlayAudio(entry.media_ref.segment_id, entry.media_ref.seek_position);
  } else if (entry.media_ref.kind === 'video_frame') {
    _highlightFrame(entry.media_ref.frame_index, entry.media_ref.bbox);
  } else if (entry.type === 'risk') {
    _expandRiskDelta(entry.risk_delta);
  } else if (entry.media_ref.kind === 'episode') {
    _navigateToEpisode(entry.media_ref.episode_id);
  }
}
```

**原则**：每个感知流条目的"为什么相信"必须能追溯到原始证据，禁止无依据的断言。

---

## 十、关键设计决策

### D1: 右侧是 Product Fact，不是 Raw Delta

**决策**: 右侧只展示状态变化，不展示持续状态。

**理由**: 用户关心"发生了什么变化"，不关心"现在是什么状态"（那是 L0 的事）。

**应用**:
- ❌ "F127 检测到 1 人"
- ✅ "14:32:05 首次出现（访客#1 进入门口画面）"

---

### D2: 事件类型驱动样式

**决策**: 每种事件类型有独立颜色（vision/audio/risk/golden/behavior）。

**理由**: 用户一眼能区分事件来源。

**应用**:
- 蓝色（behavior）：人员出现/离开/持续在场
- 紫色（audio）：音频事件（AUDIO_DETECTED / AUDIO_LEVEL_CHANGED）
- 橙色（risk）：风险跃迁
- 金色（golden）：Golden Case 声学状态（🎭 图标，非 Runtime）
- 青色（memory）：记忆关联事件（repeated_visit）

---

### D3: 历史记录自动折叠

**决策**: 超过 10 条的条目自动进入历史折叠区。

**理由**: 首屏只展示"现在"，历史可展开查看。

**应用**:
- 当前显示：最多 10 条
- 历史记录：最多 50 条，可展开
- 场景切换时清空

---

### D4: 禁止展示工程信息

**决策**: `frame_index`, `event_id`, `fingerprint` 等技术字段不进感知流。

**理由**: 这是 Product Fact vs Raw Fact 的边界。

**应用**:
- ✅ `case_time` → 时间戳
- ❌ `frame_index` → 不显示
- ✅ `visitor_id` → 翻译为"访客#1"
- ❌ `event_id` → 不显示

---

### D5: Acoustic State 三层严格隔离

**决策**: REAL_RUNTIME / DERIVED_RUNTIME / GOLDEN_CASE 三者禁止混用。

**理由**: 防止 Golden Case 叙事伪装成实时感知（这是过去最大的信任破缺口）。

**应用**:
- REAL_RUNTIME: `AudioPerceptionEvent` 序列 → 展示"检测到持续电话声"
- DERIVED_RUNTIME: segment-level RMS → 展示"声音强度变化"（仅 delta 超阈值）
- GOLDEN_CASE: `golden_audio_state` → 展示"NORMAL→STRESS" + 🎭 图标 + "● GOLDEN CASE" 标注
- **Runtime 无状态机时，禁止展示"ATTENTION → STRESS"**

---

### D6: Risk Reason 必须来自 runtime，禁止产品预写

**决策**: 风险原因文案严格使用 `risk_delta.reason_summary[]`。

**理由**: 避免产品经理替 Runtime 写原因，造成"假因果"幻觉。

**应用**:
- ✅ "未在白名单"（来自 routing_table[visit_pending_verify]）
- ✅ "异常停留"（来自 routing_table[abnormal_dwell]）
- ❌ "声学状态变化 + 电话交互"（产品预写，runtime 无此字段）
- ❌ "风险升高因为声音异常"（模型推断，非 runtime 输出）

---

## 十一、验收标准

- [ ] 右侧结构为 CURRENT STATE + RECENT CHANGES + HISTORY 三层
- [ ] 右侧感知流只展示状态变化，不展示持续状态（除 PERSON_PERSISTING）
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
- [ ] **DEDUP/MERGE**: `dedup_key` 按语义表规则去重；`merge_key` 同 kind 短间隔音频自动合并
- [ ] **ANIMATION**: 新事件入场 0.3s ease-out；禁止无限循环 pulse / 呼吸灯 / 状态条持续动画
- [ ] **VERIFY**: 每条感知流条目支持"一键回证据"（音频 seek / 视频高亮 bbox / 风险字段展开 / 记忆跳转）

---

**文档版本**: v1.2  
**最后更新**: 2026-08-21  
**状态**: Contract Freeze → 待 Owner 审批后进入 Agent 实现  
**配套**: `LIVE-PERCEPTION-STREAM-SEMANTICS.md`（Semantic Event 完整语义表 + 去重/合并规则）# Live Perception Stream — 右侧实时感知流规格

> **设计时间**: 2026-08-21  
> **核心原则**: Raw → Product Fact → Technical Detail（右侧是 Product Fact，Details 是 Raw Fact）
> **配套审计**: `LIVE-SURFACE-REALITY-CHECK.md`（Runtime Fact 真实性验证）

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
│   │                  │   │   │ 👤 1 人持续在场 12.4s                         │    │
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

## 二、右侧感知流设计规范

### 2.1 展示原则

**只展示状态变化，不展示持续状态**。

```
❌ 错误（Raw Delta）:
   F127 检测到 1 个目标
   F128 检测到 1 个目标
   F129 检测到 1 个目标
   ...（刷屏）

✅ 正确（Product Fact）:
   14:32:05  👤 首次出现（访客#1 进入门口画面）
   ...（人员持续在场，不重复显示）
   14:35:22  👤 停留超过阈值（持续 3 分 17 秒）
```

### 2.2 增量事件类型

右侧感知流应该展示以下增量事件（完整语义表见 `LIVE-PERCEPTION-STREAM-SEMANTICS.md`）：

| Semantic Event | 类别 | Runtime 原始事实 | UI 文案 | 数据源 |
|---------------|------|----------------|---------|--------|
| `PERSON_ENTERED` | A.新事实 | `perception_events[]` 新 track_id | `👤 发现 1 人进入画面` | `evidence_delta.perception_events` |
| `PERSON_PRESENT` | A.新事实 | `perception_delta.detections[]` track 持续存在（> N 秒） | `👤 1 人持续在场 X.Xs` | `evidence_delta.perception_delta` |
| `PERSON_DWELLING` 🟡 Phase 2 | B.状态跃迁 | `perception_events[].event_type = abnormal_dwell` | `⏱ 停留超过阈值（持续 Xs）` | `evidence_delta.perception_events` |
| `PERSON_REAPPEARED` | A.新事实 | `perception_events[].event_type = repeat_visit` | `🔁 访客#1 再次出现（第 N 次）` | `evidence_delta.perception_events` |
| `AUDIO_DETECTED` | A.新事实 | `evidence_delta.audio[].kind` 新 event_id | `🔊 检测到持续电话声` | `evidence_delta.audio` |
| `AUDIO_LEVEL_CHANGED` | B.状态跃迁 | `evidence_delta.audio[].rms_delta > threshold` | `📈 最近音频片段声音强度明显变化`（非连续波形，当前仅 segment-level RMS） | `evidence_delta.audio` |
| `RISK_RAISED` | B.状态跃迁 | `risk_delta.risk_transition = raised` | `⚠ 风险状态：观察 → 关注 · reason_summary` | `risk_delta` |
| `RISK_CLEARED` | B.状态跃迁 | `risk_delta.risk_transition = cleared` | `✓ 风险状态：关注 → 解除` | `risk_delta` |
| `GOLDEN_AUDIO_STATE` | — | （场景注解层，非感知流本体） | `🎭 声学状态：ATTENTION → STRESS`（仅 Golden Case，浅灰底色 + 🎭 图标） | `evidence_delta.timeline` |

> **类别说明**：A.新事实 = 新增条目；B.状态跃迁 = 原地更新或阈值触发的事件。

### 2.3 不展示的事件

以下事件**不应**出现在右侧感知流：

| 事件 | 原因 |
|------|------|
| `frame_tick` | 纯进度心跳，无业务语义 |
| `perception_delta.detections[]` | 裸检测列表，非状态变化 |
| `risk_delta`（指纹未变） | 风险状态未变化，不推送 |
| `evidence_delta`（timeline 无新节点） | 无新证据 |
| 技术字段（`frame_index`, `event_id`, `fingerprint`） | 进 Details，不进感知流 |
| 声学状态叙事（NORMAL→ATTENTION→STRESS） | 仅 Golden Case 在场景注解层展示 |

---

### 2.4 L0 Audio Health 语义定义

音频健康度为**三值状态**（非二元"正常/中断"）：

| 状态 | 判定条件 | 含义 |
|------|---------|------|
| `RECENT_EVENT` | `evidence_delta.audio[]` 非空（最近有音频事件） | 最近检测到音频事件 |
| `NO_RECENT_EVENT` | 5s 内无 audio event | 当前无事件，**非**"音频中断"，可能是静默期 |
| `UNAVAILABLE` | 场景本身无音频轨（如 `cctv_surveillance`） | 场景硬件配置无音频 |

**禁止映射**：
- ❌ `RECENT_EVENT` ≠ "音频正常"（硬件健康度未知）
- ❌ `NO_RECENT_EVENT` ≠ "音频中断"（可能是静默期）
- ✅ 正确文案："最近检测到持续电话声" / "最近无声音事件"

---

## 三、左侧 OBSERVE 区规范

### 3.1 视频输入

| 属性 | 值 |
|------|-----|
| **显示** | MJPEG 流（`/mjpeg/{scenario_id}`） |
| **Overlay** | frame_count + case_time（节流 150ms） |
| **数据来源** | `frame_tick` (gateway.py:272) |
| **更新频率** | 每帧（节流后 ~6.7fps） |
| **能力状态** | ✅ Verified |

**禁止行为**:
- ❌ 显示 `frame_index` 原始值（应显示为 `case_time`）
- ❌ 显示检测数 overlay（`ov-det`）——这是工程信息，不是产品信息

### 3.2 音频输入

| 属性 | 值 |
|------|-----|
| **显示** | RMS 分段柱状图（非连续波形） |
| **数据来源** | `evidence_delta.audio[].rms` |
| **更新频率** | per-segment（~1-2s） |
| **能力状态** | 🟡 Partial（无连续流） |
| **标注** | "RMS 分段值（非连续流）" |

**注意**: 当前系统无 `windowed_rms_stream`，只能展示 segment-level RMS。

---

## 四、现有 WebSocket 事件 → 感知流映射

### 4.1 已完成映射（可直接使用）

| WebSocket 事件 | 感知流条目 | 前端处理 |
|---------------|-----------|---------|
| `evidence_delta.perception_events[]` | 👤 首次出现 / ⏱ 停留超时 / 🔁 再次出现 | `_ingestBehavior()` + `_renderBehaviorTimeline()` |
| `evidence_delta.audio[]` | 🔊 检测到持续电话声 / 🎵 哭腔/求助 | `_appendAudioEventItem()` |
| `risk_delta`（transition=raised/cleared） | ⚠ 风险状态：MONITOR → RAISED | `_applyRiskDelta()` + `_applyRiskSignal()` |
| `evidence_delta.timeline[]`（type=golden_audio_state） | 📈 声学状态：ATTENTION → STRESS | `_humanSummary()` 已翻译 |

### 4.2 需要扩展的映射

| 需求 | 当前状态 | Gap | 修复方案 |
|------|---------|-----|---------|
| "持续 N 人在场"状态保持 | `perception_delta.detections[]` 每帧推送 | 无状态机，只有裸检测列表 | 后端新增 `person_present` 状态机 |
| 音频健康度推断 | 无独立 `audio_segment_tick` | 需前端从 `evidence_delta.audio` 推断 | 前端实现 5s 无事件判定 |
| 风险持续时间 | 无独立字段 | 前端可推导（从 `risk_transition=raised` 时间戳） | 前端实现 |

---

## 五、右侧感知流实现方案

### 5.1 数据结构

```javascript
// 右侧感知流条目
var perceptionStream = {
  entries: [],           // 当前显示的条目（最多 10 条）
  history: [],           // 折叠的历史条目
  maxVisible: 10,
  maxHistory: 50,
  
  // 添加新条目
  push: function(entry) {
    this.entries.unshift(entry);
    if (this.entries.length > this.maxVisible) {
      this.history.push(this.entries.pop());
    }
  },
  
  // 清空（场景切换时）
  clear: function() {
    this.entries = [];
    this.history = [];
  }
};

// 条目格式
var entry = {
  timestamp: '14:32:05',     // case_time 格式
  icon: '👤',                // 图标
  label: '首次出现',          // 人话标签
  detail: '访客#1 进入门口画面', // 可选详情
  type: 'behavior'           // 类型（behavior/audio/risk/acoustic）
};
```

### 5.2 事件处理映射

```javascript
// 1. perception_events → 行为里程碑
function _onPerceptionEvent(pe) {
  var behav = _BEHAV[pe.event_type];
  if (!behav) return;
  
  var vid = pe.visitor_id || '';
  var who = _friendlyVisitor(vid);
  var detail = pe.location ? ('位置 ' + pe.location) : '';
  
  perceptionStream.push({
    timestamp: formatCaseTime(pe.case_time),
    icon: behav.icon,
    label: behav.label,
    detail: who + (detail ? ' · ' + detail : ''),
    type: 'behavior'
  });
}

// 2. audio events → 音频感知
function _onAudioEvent(a) {
  var kindZh = _AUDIO_KIND_ZH[a.kind] || a.kind;
  perceptionStream.push({
    timestamp: formatCaseTime(a.timestamp),
    icon: '🔊',
    label: '检测到' + kindZh,
    detail: '',
    type: 'audio'
  });
}

// 3. risk transition → 风险感知
function _onRiskTransition(msg) {
  if (msg.risk_transition === 'raised') {
    perceptionStream.push({
      timestamp: formatCaseTime(msg.case_time),
      icon: '⚠',
      label: '风险状态：观察 → 关注',
      detail: msg.reason_summary ? msg.reason_summary.join(' + ') : '',
      type: 'risk'
    });
  } else if (msg.risk_transition === 'cleared') {
    perceptionStream.push({
      timestamp: formatCaseTime(msg.case_time),
      icon: '✓',
      label: '风险状态：关注 → 解除',
      detail: '',
      type: 'risk'
    });
  }
}

// 4. golden_audio_state → 仅 Golden Case 场景（标注"程序化"，非 Runtime 状态机）
function _onAudioState(n) {
  // 仅在 provenance_kind = SIMULATED 时才展示
  if (n.provenance_kind !== 'SIMULATED') return;
  
  var phaseMatch = /声学状态\s+(\w+)/.exec(n.summary);
  if (!phaseMatch) return;
  
  var phase = phaseMatch[1];
  var phaseZh = { 
    NORMAL: '平静态', 
    ATTENTION: '关注态', 
    AROUSAL: '唤起态', 
    STRESS: '应激态' 
  }[phase] || phase;
  
  perceptionStream.push({
    timestamp: formatCaseTime(n.timestamp),
    icon: '🎭',           // 不同图标：Golden Case 专属
    label: '声学状态：' + phase,
    detail: phaseZh + ' · ● GOLDEN CASE',
    type: 'golden',        // 独立类型，样式不同
    isTransient: true,
    dedupKey: 'golden_state_' + phase
  });
}
```

### 5.3 渲染逻辑

```javascript
function _renderPerceptionStream() {
  var container = global.document.getElementById('perception-stream-' + sid);
  if (!container) return;
  
  // 渲染当前条目
  var html = '<div class="ps-now">现在</div>';
  html += '<div class="ps-divider"></div>';
  
  perceptionStream.entries.forEach(function(entry) {
    html += _renderPsEntry(entry);
  });
  
  // 渲染历史记录（折叠）
  if (perceptionStream.history.length > 0) {
    html += '<details class="ps-history">';
    html += '<summary>历史感知 (' + perceptionStream.history.length + ' 条)</summary>';
    perceptionStream.history.slice(0, 10).reverse().forEach(function(entry) {
      html += _renderPsEntry(entry);
    });
    html += '</details>';
  }
  
  container.innerHTML = html;
}

function _renderPsEntry(entry) {
  return '<div class="ps-entry">' +
    '<span class="ps-time">' + entry.timestamp + '</span>' +
    '<span class="ps-icon">' + entry.icon + '</span>' +
    '<span class="ps-label">' + entry.label + '</span>' +
    (entry.detail ? '<span class="ps-detail">' + entry.detail + '</span>' : '') +
    '</div>';
}
```

---

## 六、CSS 样式规范

```css
/* 右侧感知流容器 */
.perception-stream {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  max-height: 400px;
  overflow-y: auto;
}

/* 现在标记 */
.ps-now {
  font-size: 11px;
  font-weight: 600;
  color: var(--status-live);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

/* 分隔线 */
.ps-divider {
  height: 1px;
  background: var(--border-color);
  margin: 4px 0;
}

/* 感知条目 */
.ps-entry {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px;
  background: #f8fafc;
  border-radius: 6px;
  border-left: 3px solid var(--modality-vision);
  animation: ps-enter 0.3s ease-out;
}

@keyframes ps-enter {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 不同类型着色 */
.ps-entry[type="behavior"] { border-left-color: var(--modality-vision); }
.ps-entry[type="audio"] { border-left-color: var(--modality-audio); }
.ps-entry[type="risk"] { border-left-color: var(--risk-raised); }
.ps-entry[type="acoustic"] { border-left-color: var(--modality-action); }

/* 时间戳 */
.ps-time {
  font-size: 10px;
  color: var(--muted-color);
  min-width: 50px;
}

/* 图标 */
.ps-icon {
  font-size: 16px;
  min-width: 20px;
}

/* 标签 */
.ps-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

/* 详情 */
.ps-detail {
  font-size: 11px;
  color: var(--muted-color);
  margin-left: auto;
}

/* 历史记录折叠 */
.ps-history {
  margin-top: 8px;
}

.ps-history summary {
  font-size: 11px;
  color: var(--muted-color);
  cursor: pointer;
  padding: 4px 0;
}
```

---

## 七、场景适配

### 7.1 telephone_risk

```
OBSERVE:
  - Video (MJPEG 流)
  - Audio (RMS 分段柱状图)

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
```

> **禁止**：展示 "NORMAL → ATTENTION → AROUSAL → STRESS"（Golden Case 叙事，非 Runtime）
> **正确**：展示 audio_event 序列 + RMS 变化趋势

### 7.2 cctv_surveillance

```
OBSERVE:
  - Video (MJPEG 流，夜间模式)

UNDERSTAND:
  CURRENT STATE:
    👤 1 人持续在场 9.5s
    ⚠ 风险：关注

  RECENT CHANGES:
    14:30:00  👤 发现 1 人进入画面
    14:30:07  ⏱ 停留超过阈值（持续 7s）🟡 Phase 2
    14:30:45  🔁 访客#1 再次出现（第 3 次）
    14:30:45  ⚠ 风险状态：观察 → 关注 · 异常停留 + 重复访问
```

> **完全隐藏**：所有音频相关感知流条目（无 audio evidence 数据源）

### 7.3 repeated_visit

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
```

> `MEMORY_MATCHED` 依赖 Memory API，标注 Phase 2。

---

## 八、实现优先级

### Phase 1: 基础感知流（无需后端变更）

- [ ] 实现 `perceptionStream` 数据结构
- [ ] 实现 `_renderPerceptionStream()` 渲染函数
- [ ] 对接 `evidence_delta.perception_events` → 行为里程碑
- [ ] 对接 `evidence_delta.audio` → 音频事件
- [ ] 对接 `risk_delta.risk_transition` → 风险跃迁
- [ ] 对接 `evidence_delta.timeline`（golden_audio_state）→ 声学状态
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

## 九、VERIFY 信任闭环规范

每个感知流条目支持"**一键回证据**"，让用户验证系统判断的依据：

| 感知流类型 | 触发操作 | 实现方式 |
|-----------|---------|---------|
| 音频事件（`AUDIO_DETECTED` / `AUDIO_LEVEL_CHANGED`） | seek 到对应 audio case_time，播放音频片段 | entry 携带 `media_ref.audio_segment_id` + `seek_position`；点击调用 `_seekAndPlayAudio()` |
| 人员事件（`PERSON_ENTERED` / `PERSON_PRESENT` / `PERSON_REAPPEARED`） | seek 到对应 video frame，高亮 bbox | entry 携带 `media_ref.frame_index`；点击调用 `_highlightFrame()` |
| 风险事件（`RISK_RAISED` / `RISK_CLEARED`） | 展开 `risk_delta` 完整字段 | entry 携带 `risk_delta` 完整 payload；点击展开 Details |
| 记忆事件（`MEMORY_MATCHED`） | 跳转到 Memory Episode 详情 | entry 携带 `episode_id`；点击调用 `_navigateToEpisode()` |

**实现要点**：
```javascript
// 每条感知流条目携带媒体引用
var entry = {
  timestamp: '14:32:08',
  icon: '🔊',
  label: '检测到持续电话声',
  type: 'audio',
  media_ref: {
    kind: 'audio_segment',
    segment_id: 'aud_seg_001',
    seek_position: 12.4   // case_time
  },
  // 或
  media_ref: {
    kind: 'video_frame',
    frame_index: 1432,
    bbox: { x: 120, y: 80, w: 60, h: 140 }
  }
};

// 点击处理
function _onEntryClick(entry) {
  if (entry.media_ref.kind === 'audio_segment') {
    _seekAndPlayAudio(entry.media_ref.segment_id, entry.media_ref.seek_position);
  } else if (entry.media_ref.kind === 'video_frame') {
    _highlightFrame(entry.media_ref.frame_index, entry.media_ref.bbox);
  } else if (entry.type === 'risk') {
    _expandRiskDelta(entry.risk_delta);
  } else if (entry.media_ref.kind === 'episode') {
    _navigateToEpisode(entry.media_ref.episode_id);
  }
}
```

**原则**：每个感知流条目的"为什么相信"必须能追溯到原始证据，禁止无依据的断言。

---

## 十、关键设计决策

### D1: 右侧是 Product Fact，不是 Raw Delta

**决策**: 右侧只展示状态变化，不展示持续状态。

**理由**: 用户关心"发生了什么变化"，不关心"现在是什么状态"（那是 L0 的事）。

**应用**:
- ❌ "F127 检测到 1 人"
- ✅ "14:32:05 首次出现（访客#1 进入门口画面）"

---

### D2: 事件类型驱动样式

**决策**: 每种事件类型有独立颜色（vision/audio/risk/golden/behavior）。

**理由**: 用户一眼能区分事件来源。

**应用**:
- 蓝色（behavior）：人员出现/离开/持续在场
- 紫色（audio）：音频事件（AUDIO_DETECTED / AUDIO_LEVEL_CHANGED）
- 橙色（risk）：风险跃迁
- 金色（golden）：Golden Case 声学状态（🎭 图标，非 Runtime）
- 青色（memory）：记忆关联事件（repeated_visit）

---

### D3: 历史记录自动折叠

**决策**: 超过 10 条的条目自动进入历史折叠区。

**理由**: 首屏只展示"现在"，历史可展开查看。

**应用**:
- 当前显示：最多 10 条
- 历史记录：最多 50 条，可展开
- 场景切换时清空

---

### D4: 禁止展示工程信息

**决策**: `frame_index`, `event_id`, `fingerprint` 等技术字段不进感知流。

**理由**: 这是 Product Fact vs Raw Fact 的边界。

**应用**:
- ✅ `case_time` → 时间戳
- ❌ `frame_index` → 不显示
- ✅ `visitor_id` → 翻译为"访客#1"
- ❌ `event_id` → 不显示

---

### D5: Acoustic State 三层严格隔离

**决策**: REAL_RUNTIME / DERIVED_RUNTIME / GOLDEN_CASE 三者禁止混用。

**理由**: 防止 Golden Case 叙事伪装成实时感知（这是过去最大的信任破缺口）。

**应用**:
- REAL_RUNTIME: `AudioPerceptionEvent` 序列 → 展示"检测到持续电话声"
- DERIVED_RUNTIME: segment-level RMS → 展示"声音强度变化"（仅 delta 超阈值）
- GOLDEN_CASE: `golden_audio_state` → 展示"NORMAL→STRESS" + 🎭 图标 + "● GOLDEN CASE" 标注
- **Runtime 无状态机时，禁止展示"ATTENTION → STRESS"**

---

### D6: Risk Reason 必须来自 runtime，禁止产品预写

**决策**: 风险原因文案严格使用 `risk_delta.reason_summary[]`。

**理由**: 避免产品经理替 Runtime 写原因，造成"假因果"幻觉。

**应用**:
- ✅ "未在白名单"（来自 routing_table[visit_pending_verify]）
- ✅ "异常停留"（来自 routing_table[abnormal_dwell]）
- ❌ "声学状态变化 + 电话交互"（产品预写，runtime 无此字段）
- ❌ "风险升高因为声音异常"（模型推断，非 runtime 输出）

---

## 十一、验收标准

- [ ] 右侧结构为 CURRENT STATE + RECENT CHANGES + HISTORY 三层
- [ ] 右侧感知流只展示状态变化，不展示持续状态（除 PERSON_PERSISTING）
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
- [ ] **DEDUP/MERGE**: `dedup_key` 按语义表规则去重；`merge_key` 同 kind 短间隔音频自动合并
- [ ] **ANIMATION**: 新事件入场 0.3s ease-out；禁止无限循环 pulse / 呼吸灯 / 状态条持续动画
- [ ] **VERIFY**: 每条感知流条目支持"一键回证据"（音频 seek / 视频高亮 bbox / 风险字段展开 / 记忆跳转）

---

**文档版本**: v1.2  
**最后更新**: 2026-08-21  
**状态**: Contract Freeze → 待 Owner 审批后进入 Agent 实现  
**配套**: `LIVE-PERCEPTION-STREAM-SEMANTICS.md`（Semantic Event 完整语义表 + 去重/合并规则）
**配套**: `LIVE-PERCEPTION-STREAM-SEMANTICS.md`（Semantic Event 完整语义表）