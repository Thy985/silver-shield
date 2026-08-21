# Live Product Wireframe — 核心场景原型

> **设计时间**: 2026-08-21  
> **状态**: Draft  
> **架构原则**: 左侧 OBSERVE + 右侧 UNDERSTAND + 底部 RISK/VERIFY（已对齐感知流规格）

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
├── L0 Runtime Presence（顶部状态条）
├── ① OBSERVE（左侧：视频 + 音频输入）
├── ② UNDERSTAND（右侧：实时感知流）
├── RISK（底部：风险状态）
└── VERIFY（底部：证据回溯）

ScenarioSurface（场景专属适配）
├── TelephoneRiskSurface（audio-first）
├── CCTVSurface（vision-first）
├── RepeatedVisitSurface（memory-first）
├── DeliveryNormalSurface（negative control）
└── EvidenceInsufficientSurface（edge case）
```

### 核心原则
> **Shell 提供跨场景一致的状态、行动和信任基础设施。**
> 
> **ScenarioSurface 只讲"这个场景为什么值得被展示"。**

---

## 二、Wireframe 1: telephone_risk（多模态/声学优先）

### 产品目标
证明 **多模态实时感知 + 声学状态变化 + 风险判断可信**

### 布局全貌

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L0] 🟢 LIVE  │  📹 视频正常  │  🔊 最近检测到电话声  │  延迟 ~120ms*  │  Session: 00:15 │
├──────────────────────────┬──────────────────────────────────────────────────────┤
│                          │                                                      │
│   ① OBSERVE              │   ② UNDERSTAND（实时感知流）                          │
│   （传感器输入）           │   （AI 从输入中实时感知的状态变化）                    │
│                          │                                                      │
│   ┌──────────────────┐   │   ┌─ CURRENT STATE ───────────────────────────────┐  │
│   │                  │   │   │ 👤 1 人持续在场 12.4s                          │  │
│   │     VIDEO        │   │   │ 🔊 最近检测到持续电话声                        │  │
│   │   (MJPEG 流)     │   │   │ ⚠ 风险：关注                                    │  │
│   │                  │   │   └────────────────────────────────────────────────┘  │
│   │   Overlay:       │   │                                                      │
│   │   case_time=00:12│   │   ┌─ RECENT CHANGES ──────────────────────────────┐  │
│   │                  │   │   │ 14:32:05  👤 发现 1 人进入画面                 │  │
│   └──────────────────┘   │   │ 14:32:08  🔊 检测到电话声                      │  │
│                          │   │ 14:32:15  📈 最近音频片段声音强度明显变化        │  │
│   ┌──────────────────┐   │   │ 14:32:15  ⚠ 风险状态：观察 → 关注 · 未在白名单  │  │
│   │                  │   │   └────────────────────────────────────────────────┘  │
│   │     AUDIO        │   │                                                      │
│   │                  │   │   ┌─ HISTORY > 查看更多 ──────────────────────────┐  │
│   │   ▂▃▅▆▇▆▅▃▂     │   │   │ 14:31:58  👤 发现 1 人进入画面                  │  │
│   │   RMS 分段值     │   │   └────────────────────────────────────────────────┘  │
│   │   (非连续流)     │   │                                                      │
│   └──────────────────┘   │                                                      │
│                          │                                                      │
├──────────────────────────┴──────────────────────────────────────────────────────┤
│  [RISK] 关注 · 未在白名单 · 建议：继续观察                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│  [VERIFY] 查看声音证据 · 查看视频证据 · 查看完整时间线                             │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Surface 优先级

| Surface | 层级 | 优先级 | 数据源 |
|---------|------|--------|--------|
| L0 Runtime Presence | Shell | P0 | frame_tick + audio event 推断 |
| ① Video Input | OBSERVE | P0 | MJPEG 流 |
| ① Audio Input | OBSERVE | P0 | rms segment-level |
| ② CURRENT STATE | UNDERSTAND | P0 | perception_delta + risk_delta |
| ② RECENT CHANGES | UNDERSTAND | P0 | evidence_delta |
| ② HISTORY | UNDERSTAND | P1 | perceptionStream.history |
| RISK | Shell | P0 | risk_delta |
| VERIFY | Shell | P0 | evidence_items + media_ref |

### 关键标注
- Waveform: "RMS 分段值（非连续流）" — **禁止标榜"实时波形"**
- Acoustic State: 不展示"NORMAL→STRESS"（Runtime 无状态机）
- Cross-modal: "cross_modal=0，当前无额外关联证据" — **禁止假装融合**
- 延迟: "延迟 ~120ms*" — **标注"*基于前端估算"**

---

## 三、Wireframe 2: cctv_surveillance（视觉/风险优先）

### 产品目标
证明 **视觉实时感知 + 异常访问识别**

### 布局全貌

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L0] 🟢 LIVE  │  📹 视频正常 · 287 帧  │  🔇 无音频轨  │  延迟 ~95ms*  │  Session: 00:09 │
├──────────────────────────┬──────────────────────────────────────────────────────┤
│                          │                                                      │
│   ① OBSERVE              │   ② UNDERSTAND（实时感知流）                          │
│   （传感器输入）           │   （AI 从输入中实时感知的状态变化）                    │
│                          │                                                      │
│   ┌───────────────────────────────────────────────────────────────────────────┐  │
│   │  📹 VIDEO SENSOR（full-width 视频主视觉）                                 │  │
│   │  [LIVE badge]                                                           │  │
│   │                                                                         │  │
│   │                    Case Video Player (night vision)                     │  │
│   │                    Overlay: bbox + track_id + person_count              │  │
│   │                                                                         │  │
│   │                    case_time=00:09.5s                                   │  │
│   └───────────────────────────────────────────────────────────────────────────┘  │
│                          │                                                      │
│   ┌──────────────────┐   │   ┌─ CURRENT STATE ───────────────────────────────┐  │
│   │     (无音频输入)   │   │   │ 👤 1 人持续在场 9.5s                          │  │
│   │     音频面板隐藏   │   │   │ ⚠ 风险：关注                                    │  │
│   └──────────────────┘   │   └────────────────────────────────────────────────┘  │
│                          │                                                      │
│                          │   ┌─ RECENT CHANGES ──────────────────────────────┐  │
│                          │   │ 14:30:00  👤 发现 1 人进入画面                 │  │
│                          │   │ 14:30:07  ⏱ 停留超过阈值（持续 7s）🟡 Phase 2  │  │
│                          │   │ 14:30:45  🔁 访客#1 再次出现（第 3 次）          │  │
│                          │   │ 14:30:45  ⚠ 风险状态：观察 → 关注 · 异常停留   │  │
│                          │   └────────────────────────────────────────────────┘  │
│                          │                                                      │
├──────────────────────────┴──────────────────────────────────────────────────────┤
│  [RISK] 关注 · 异常停留 + 重复访问 · 建议：通知家属                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│  [VERIFY] 查看视频证据 · 查看完整时间线                                           │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 场景规则

**完全隐藏的面板**（禁止显示，而非显示"无音频"）：
- ❌ 音频输入区（OBSERVE 左侧下方）
- ❌ 音频相关感知流条目（UNDERSTAND 右侧）

**升级的 Surface**：
- 🟡 视频输入 → **P0**（核心风险信号）
- Person Perception → **P0**（核心叙事）

---

## 四、Wireframe 3: repeated_visit（记忆/历史优先）

### 产品目标
证明 **系统真的"记得过去" + 识别重复模式**

### 布局全貌

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  [L0] 🟢 LIVE  │  📹 视频正常 · 512 帧  │  🔇 无音频轨  │  延迟 ~110ms*  │  Session: 00:22 │
├──────────────────────────┬──────────────────────────────────────────────────────┤
│                          │                                                      │
│   ① OBSERVE              │   ② UNDERSTAND（实时感知流）                          │
│   （传感器输入）           │   （AI 从输入中实时感知的状态变化）                    │
│                          │                                                      │
│   ┌──────────────────┐   │   ┌─ CURRENT STATE ───────────────────────────────┐  │
│   │                  │   │   │ ⚠ 风险：关注                                    │  │
│   │     VIDEO        │   │   └────────────────────────────────────────────────┘  │
│   │   (MJPEG 流)     │   │                                                      │
│   │                  │   │   ┌─ RECENT CHANGES ──────────────────────────────┐  │
│   │  Overlay: 👤 1人在场 │   │ 14:30:00  👤 发现 1 人进入画面                 │  │
│   │  case_time=00:22.1s │   │ 14:30:05  🔁 访客#1 再次出现（第 3 次）          │  │
│   │                  │   │ 14:30:05  🧠 记忆关联：3 天前访问（ep_001）🟡 Phase 2│  │
│   └──────────────────┘   │ 14:30:05  ⚠ 风险状态：观察 → 关注 · 重复访问       │  │
│                          │   └────────────────────────────────────────────────┘  │
│   ┌──────────────────┐   │                                                      │
│   │     (无音频输入)   │   │   ┌─ HISTORY > 查看更多 ──────────────────────────┐  │
│   │     音频面板隐藏   │   │   │ 14:28:00  👤 发现 1 人进入画面                  │  │
│   └──────────────────┘   │   │ 14:28:05  🔁 访客#1 再次出现（第 2 次）          │  │
│                          │   └────────────────────────────────────────────────┘  │
├──────────────────────────┴──────────────────────────────────────────────────────┤
│  [RISK] 关注 · 重复访问模式识别 · 建议：通知家属                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│  [VERIFY] 查看视频证据 · 查看历史访问记录 · 查看完整时间线                         │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 核心差异

- **Memory Context** 不是独立面板，而是作为感知流条目（`🧠 记忆关联：3 天前访问`）
- L5 VERIFY 强调"查看历史访问记录"（跨 episode 证据对比）

---

## 五、Negative Control Wireframes

### 5.1 delivery_courier_normal

**产品目标**: 证明系统**不误报**

**叙事**: "系统持续观察，暂无异常"

**布局差异**:
- 感知流 RECENT CHANGES 区显示"持续观察中"（无新事件）
- RISK 区显示"观察 · 无异常"
- VERIFY 区正常显示

**禁止**:
- ❌ 显示"风险：LOW"（误导）
- ❌ 隐藏 L2-L4（让用户以为故障）

---

### 5.2 evidence_insufficient

**产品目标**: 证明系统**知道什么时候不能下结论**

**叙事**: "正在观察 → 发现模糊证据 → 证据不足 → 不做风险升级"

**布局差异**:
- L1 Person Perception 显示低置信检测（detection_score=0.31）
- RISK 区显示"观察 · 证据不足"
- VERIFY 区强调"查看置信度详情"

**关键标注**:
- "低置信检测，不触发风险"
- 证明系统边界

---

## 六、实现约束

### 6.1 共享组件

```
LiveShell（所有场景共享）
├── LivePresence（L0 顶部状态条）
├── RiskState（底部风险条）
├── VerifySection（底部证据入口）
└── CSS 变量（--risk-monitor, --modality-vision 等）
```

### 6.2 场景适配函数

```python
# src/home_perception/visualizer/viewer/scenario_config.py

SCENARIO_CONFIG = {
    "telephone_risk": {
        "narrative_mode": "audio_first",
        "observe_layout": "video_small + audio_large",
        "hidden_surfaces": [],
        "special_entries": ["AUDIO_DETECTED", "AUDIO_LEVEL_CHANGED"]
    },
    "cctv_surveillance_suspicious": {
        "narrative_mode": "vision_first",
        "observe_layout": "video_full_width",
        "hidden_surfaces": ["audio_input", "audio_events"],
        "special_entries": ["PERSON_REAPPEARED"]
    },
    "live_repeated_visit": {
        "narrative_mode": "memory_first",
        "observe_layout": "video_normal",
        "hidden_surfaces": ["audio_input", "audio_events"],
        "special_entries": ["MEMORY_MATCHED", "PERSON_REAPPEARED"]
    }
}
```

---

## 七、验收清单

- [ ] 所有场景共享 LiveShell（L0/RISK/VERIFY）
- [ ] 每个场景有独立 ScenarioSurface 配置
- [ ] telephone_risk: Audio 区域较大，感知流含 audio 事件
- [ ] cctv_surveillance: Video 全宽，音频面板完全隐藏
- [ ] repeated_visit: 感知流含 MEMORY_MATCHED 条目
- [ ] delivery_courier_normal: 明确显示"持续观察中"
- [ ] evidence_insufficient: 明确显示"证据不足"
- [ ] 无伪造的实时感
- [ ] SIMULATED 数据明确标注
- [ ] `pytest tests/ -q` 全部通过
- [ ] `ruff check src/ tests/` 无 error

---

**文档版本**: v1.0  
**最后更新**: 2026-08-21  
**状态**: Draft → 待 Owner 审批  
**配套**: `LIVE-PERCEPTION-STREAM-SPEC.md`、`LIVE-PRODUCT-SURFACE-SPEC.md`
