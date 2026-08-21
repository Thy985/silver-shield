# Live Perception Stream Semantics — 感知流语义表

> **设计时间**: 2026-08-21  
> **状态**: Draft  
> **配套**: `LIVE-PERCEPTION-STREAM-SPEC.md`（规格文档）

---

## 一、Semantic Event 完整语义表

### 1.1 人员感知事件

| Semantic Event | Runtime 原始事实 | UI 文案 | 触发条件 | 去重 Key | 持续状态 | 可回溯 |
|---------------|-----------------|---------|---------|---------|---------|--------|
| `PERSON_ENTERED` | `perception_events[]` 新 track_id | `👤 发现 1 人进入画面` | 首次 track_id 出现 | `track_id` | 是 | 是 |
| `PERSON_PRESENT` | `perception_delta.detections[]` track 持续存在（> N 秒） | `👤 1 人持续在场 X.Xs` | track 仍存在且 dwell > 1s | `track_id` | 是 | 是 |
| `PERSON_DWELLING` | `perception_events[].event_type = abnormal_dwell` | `⏱ 停留超过阈值（持续 Xs）` | dwell >= threshold | `track_id + dwell_threshold` | 否 | 是 |
| `PERSON_LEFT` | `perception_events[].event_type = visitor_leave` | `👤 人员离开画面` | track status = left | `track_id` | 否 | 是 |
| `PERSON_REAPPEARED` | `perception_events[].event_type = repeat_visit` | `🔁 访客#1 再次出现（第 N 次）` | visits >= 2 | `track_id + visit_count` | 否 | 是 |

### 1.2 音频感知事件

| Semantic Event | Runtime 原始事实 | UI 文案 | 触发条件 | 去重 Key | 持续状态 | 可回溯 |
|---------------|-----------------|---------|---------|---------|---------|--------|
| `AUDIO_DETECTED` | `evidence_delta.audio[].kind` 新 event_id | `🔊 检测到持续电话声` | 新 event_id | `event_id` | 否 | 是 |
| `AUDIO_LEVEL_CHANGED` | `evidence_delta.audio[].rms_delta > threshold` | `📈 最近音频片段声音强度明显变化` | rms_delta > 6dB | `segment_id` | 否 | 是 |
| `AUDIO_QUIET` | `evidence_delta.audio[]` 5s 内无新事件 | `🔇 最近无声音事件` | last_audio_ts + 5s | `quiet_window` | 否 | 否 |

### 1.3 风险感知事件

| Semantic Event | Runtime 原始事实 | UI 文案 | 触发条件 | 去重 Key | 持续状态 | 可回溯 |
|---------------|-----------------|---------|---------|---------|---------|--------|
| `RISK_RAISED` | `risk_delta.risk_transition = raised` | `⚠ 风险状态：观察 → 关注 · reason_summary` | risk_transition = raised | `signal_id` | 否 | 是 |
| `RISK_CLEARED` | `risk_delta.risk_transition = cleared` | `✓ 风险状态：关注 → 解除` | risk_transition = cleared | `signal_id + cleared_at` | 否 | 是 |
| `RISK_ESCALATED` | `risk_delta.risk_level = CRITICAL` | `🔴 风险升级：关注 → 高风险` | risk_level = CRITICAL | `signal_id + escalated_at` | 否 | 是 |

### 1.4 记忆关联事件（Phase 2）

| Semantic Event | Runtime 原始事实 | UI 文案 | 触发条件 | 去重 Key | 持续状态 | 可回溯 |
|---------------|-----------------|---------|---------|---------|---------|--------|
| `MEMORY_MATCHED` | Memory Episodes API 返回匹配记录 | `🧠 记忆关联：X 天前访问（ep_XXX）` | episode.timestamp < now - 24h | `track_id + episode_id` | 否 | 是 |
| `MEMORY_PATTERN` | 多次记忆匹配形成模式 | `🔁 重复访问模式识别（N 次访问）` | visit_count >= 2 | `track_id + pattern` | 否 | 是 |

### 1.5 Golden Case 注解事件（非 Runtime）

| Semantic Event | Runtime 原始事实 | UI 文案 | 触发条件 | 去重 Key | 持续状态 | 可回溯 |
|---------------|-----------------|---------|---------|---------|---------|--------|
| `GOLDEN_AUDIO_STATE` | `evidence_delta.timeline[]` type=golden_audio_state | `🎭 声学状态：ATTENTION → STRESS`（浅灰底色 + 🎭 图标） | provenance_kind = SIMULATED | `golden_state_id` | 否 | 是 |

> **注意**: Golden Case 事件不与 Runtime 感知流混合展示，应作为场景注解层独立渲染。

---

## 二、去重与合并规则

### 2.1 去重 Key 规则

| Semantic Event | Dedup Key | 规则 |
|---------------|-----------|------|
| `PERSON_ENTERED` | `track_id` | 同一 track_id 只生成一次 ENTERED |
| `PERSON_PRESENT` | `track_id` | 同一 track_id 只更新一次（原地刷新，不新增） |
| `PERSON_DWELLING` | `track_id + dwell_threshold` | 达到阈值后只生成一次 DWELLING |
| `PERSON_REAPPEARED` | `track_id + visit_count` | 每次访问计数递增 |
| `AUDIO_DETECTED` | `event_id` | 幂等，同一 event_id 不重复显示 |
| `AUDIO_LEVEL_CHANGED` | `segment_id` | 同一段落只显示一次变化 |
| `RISK_RAISED` | `signal_id` | 同一信号只显示一次 raised |
| `RISK_CLEARED` | `signal_id + cleared_at` | cleared 事件需带时间戳去重 |
| `MEMORY_MATCHED` | `track_id + episode_id` | 同一次记忆关联不重复显示 |

### 2.2 合并 Key 规则

| Semantic Event | Merge Key | 合并规则 |
|---------------|-----------|---------|
| `AUDIO_DETECTED` | `kind` | 同 kind 短间隔（< 3s）音频事件自动合并为"持续检测到 X" |
| `PERSON_PRESENT` | `count` | 多人同时在场时合并为"N 人持续在场" |
| `RISK_RAISED` | — | 不合并，每次跃迁单独显示 |

---

## 三、语义转换规则

### 3.1 Runtime Fact → Product Fact

| Runtime Fact | Product Fact | 转换规则 |
|-------------|-------------|---------|
| `frame_index=1432` | `case_time=5.2s` | `case_time = (frame_index - start_frame) * frame_interval` |
| `visitor_id="6f369b89-..."` | `访客#1` | 按首次出现顺序映射编号 |
| `event_id="abcd-1234"` | —（隐藏） | 技术字段不进感知流 |
| `track_id=5` | `访客#1`（首次） | 建立 track_id → visitor_id 映射 |
| `risk_level="LOW"` | `观察` | 枚举映射 |
| `risk_level="MEDIUM"` | `关注` | 枚举映射 |
| `risk_level="HIGH"` | `高风险` | 枚举映射 |
| `reason_summary=["未在白名单"]` | `未在白名单` | 直接展示 |
| `command_type="LOG_ONLY"` | `已记录` | 显示执行状态 |
| `provenance_kind="SIMULATED"` | `● GOLDEN CASE` | 标注来源 |
| `provenance_kind="REAL_SENSOR"` | `● LIVE` | 标注来源 |

### 3.2 状态机转换

```
PERSON_ENTERED
    ↓
PERSON_PRESENT（持续刷新 dwell 时间）
    ↓
PERSON_DWELLING（达到阈值）
    ↓
PERSON_LEFT（离开画面）

RISK_RAISED
    ↓
RISK_CLEARED（风险解除）
    ↓
RISK_RAISED（再次触发）
```

---

## 四、UI 文案映射表

### 4.1 人员事件文案

| Semantic Event | Icon | Label | Detail |
|---------------|------|-------|--------|
| `PERSON_ENTERED` | 👤 | 首次出现 | 访客#1 进入门口画面 |
| `PERSON_PRESENT` | 👤 | 持续在场 | 1 人持续在场 12.4s |
| `PERSON_DWELLING` | ⏱ | 停留超时 | 停留超过阈值（持续 3 分 17 秒） |
| `PERSON_LEFT` | 👤 | 人员离开 | 访客#1 离开画面 |
| `PERSON_REAPPEARED` | 🔁 | 再次出现 | 访客#1 再次出现（第 3 次） |

### 4.2 音频事件文案

| Semantic Event | Icon | Label | Detail |
|---------------|------|-------|--------|
| `AUDIO_DETECTED` | 🔊 | 检测到声音 | 持续电话声 |
| `AUDIO_LEVEL_CHANGED` | 📈 | 声音变化 | 最近音频片段声音强度明显变化 |
| `AUDIO_QUIET` | 🔇 | 无声音事件 | 最近无声音事件 |

### 4.3 风险事件文案

| Semantic Event | Icon | Label | Detail |
|---------------|------|-------|--------|
| `RISK_RAISED` | ⚠ | 风险升高 | 观察 → 关注 · 未在白名单 |
| `RISK_CLEARED` | ✓ | 风险解除 | 关注 → 解除 |
| `RISK_ESCALATED` | 🔴 | 风险升级 | 关注 → 高风险 · 异常停留 + 重复访问 |

---

## 五、禁止行为清单

### 5.1 禁止展示的工程信息

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

### 5.2 禁止的文案表述

| 禁止文案 | 原因 | 正确文案 |
|---------|------|---------|
| "音频正常" | 硬件健康度未知 | "最近检测到电话声" |
| "音频中断" | 可能是静默期 | "最近无声音事件" |
| "NORMAL → ATTENTION → STRESS" | Runtime 无此状态机 | "检测到电话声 + 声音强度变化" |
| "风险升高因为电话+声学压力" | 产品预写，非 runtime 输出 | "风险：关注 · 原因：未在白名单" |
| "系统正在分析声音" | 伪实时感 | "最近检测到持续电话声" |
| "延迟 ~120ms" | 前端单边估算，非真实端到端延迟 | 隐藏或标注"*基于前端估算" |

### 5.3 禁止的行为

| 行为 | 原因 |
|------|------|
| 用静态动画模拟活动 | 伪造实时感 |
| 隐藏输入中断状态 | 缺乏透明度 |
| 伪造帧计数 | `frame_index ≠ runtime_tick_count` |
| 显示未实现的能力 | 如 `audio_buffer_level` |
| 让 Runtime Event 改变 Layout | 应保持 Narrative Mode 稳定 |
| 混合 Golden Case 叙事与 Runtime 感知 | 违反 D5 三层隔离 |

---

## 六、Phase 分级

### Phase 1: 基础感知流（当前实现）

- [x] `PERSON_ENTERED`
- [x] `PERSON_PRESENT`（前端推导）
- [x] `AUDIO_DETECTED`
- [x] `RISK_RAISED` / `RISK_CLEARED`
- [x] CURRENT STATE + RECENT CHANGES + HISTORY 三层结构

### Phase 2: 场景适配

- [ ] `PERSON_DWELLING`（需后端新增 `abnormal_dwell` 事件）
- [ ] `PERSON_REAPPEARED`（需后端新增 `repeat_visit` 事件）
- [ ] `MEMORY_MATCHED`（需 Memory API 暴露）
- [ ] `AUDIO_LEVEL_CHANGED`（需后端新增 rms_delta 字段）

### Phase 3: 后端能力补齐

- [ ] `perception_delta` 增加 `person_present` 状态机
- [ ] `evidence_delta` 增加 `rms_window` 字段（连续波形）
- [ ] 新增 `memory_timeline` 消息类型

---

## 七、附录：Semantic Event 枚举

```typescript
type PerceptionEventType =
  | 'PERSON_ENTERED'
  | 'PERSON_PRESENT'
  | 'PERSON_DWELLING'
  | 'PERSON_LEFT'
  | 'PERSON_REAPPEARED'
  | 'AUDIO_DETECTED'
  | 'AUDIO_LEVEL_CHANGED'
  | 'AUDIO_QUIET'
  | 'RISK_RAISED'
  | 'RISK_CLEARED'
  | 'RISK_ESCALATED'
  | 'MEMORY_MATCHED'
  | 'MEMORY_PATTERN'
  | 'GOLDEN_AUDIO_STATE';

interface PerceptionStreamEntry {
  timestamp: string;           // case_time 格式 "14:32:05"
  icon: string;                // emoji 图标
  label: string;               // 人话标签
  detail?: string;             // 可选详情
  type: PerceptionEventType;
  dedup_key: string;           // 去重 key
  merge_key?: string;          // 合并 key
  media_ref?: {
    kind: 'video_frame' | 'audio_segment' | 'episode';
    frame_index?: number;
    segment_id?: string;
    episode_id?: string;
    seek_position?: number;
    bbox?: { x: number; y: number; w: number; h: number };
  };
  is_transient?: boolean;      // Golden Case 注解事件
}
```

---

**文档版本**: v0.1  
**最后更新**: 2026-08-21  
**状态**: Draft  
**配套**: `LIVE-PERCEPTION-STREAM-SPEC.md`（规格文档）