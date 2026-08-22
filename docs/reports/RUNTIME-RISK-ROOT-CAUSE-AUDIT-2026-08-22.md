# Runtime Risk Root-Cause Audit Report · 2026-08-22

> **审计对象**：SilverShield Live Runtime（`silver_demo` 网关 + `home_perception.runtime`）
> **审计起因**：真实浏览器复现 `/live` 入口，WS 814 条 payload 显示 `0 RAISED / 2 CLEARED / 0 warning / 0 command`，audio 链 9 条 unique event 全部 `kind=audio_distress_cry`（YAMNet class_map 缺失兜底），DOM audio_table 显示的是 golden prerender（`audio_voice_raised`/`audio_telephone_persistent`）而非 runtime 推流。
>
> **审计目标**：不动代码、不修 bug，沿调用链逐层验证"每一层是否真的收到输入 → 是否真的产出输出 → 为什么没有产出"，把根因写清楚。先 audit、再 PR-B（不动手）。
>
> **审计依据**：`src/home_perception/{audio,runtime,analysis,action,visualizer/viewer}` + `src/silver_demo/{gateway.py,ws.py}` + `config/live_audio.yaml` + 真实 WS payload（`artefacts/telephone_risk_acceptance/ws_payloads.jsonl`）。

---

## 0. 症状复述（来自真实浏览器 WS payload · 814 条）

| 字段 | 实际值 | 期望（产品语义） | 偏差 |
|---|---|---|---|
| `frame_tick` | 178 条 | 持续推送 | ✅ 正常 |
| `evidence_delta` | 178 条 | 持续推送（timeline/audio/perception_events/warnings 增量） | ✅ 正常 |
| `perception_delta` | 148 条 | 检测子集变化推送 | ✅ 正常（148:1 语义去重工作） |
| `risk_delta` | **2 条** | 每次 risk_transition 变化推送 | ⚠️ 数量过少 |
| `risk_delta.risk_transition` | **0 raised / 2 cleared** | NONE → RAISED → ACTIVE → CLEARED 跃迁 | ❌ **异常**——只有 cleared 没有 raised，且 cleared 次数 = 2 而非预期的循环重置对应次数 |
| `FrameResult.warnings` | 0 | 决策层产出 warning | ❌ **0 warning** |
| `ActionCommand[]` | 0 | 行动层产出 command | ❌ **0 command** |
| audio event 实际 `kind` | 9 unique × `audio_distress_cry` | 真实 YAMNet 521 类细分（speech/telephone/cry/laugh/...） | ⚠️ YAMNet class_map 缺失导致兜底 |
| DOM audio_table 显示 | `audio_voice_raised` / `audio_telephone_persistent` | 真实 runtime 推流 | ❌ 显示的是 golden prerender 而非 runtime |
| `narrative_mode` | `'neutral'` | `'audio_first'` 或 `'risk_first'` | ❌ 触发条件未达 |

**核心异常**：`cleared ≠ 没有风险`。它语义上是"此前存在一个 risk state，现在被解除"。没有前面的 `raised` 单独出现 `cleared` 本身就是异常信号——但浏览器反复看到 cleared，说明服务端 `ProjectionAccumulator` 状态机被某种外部初始化置为"曾经 active"，但 runtime 路径从未产生 RAISED。

---

## 1. 调用链：10 层逐层审计

> 每层回答 4 问：**输入是什么？是否真的收到？输出是什么？为什么没有输出？**

### Layer 1 · AudioPipeline.run() → AudioPerceptionEvent

- **入口**：`src/home_perception/audio/pipeline.py:86` `AudioPipeline.run(source)` → `list[AudioPerceptionEvent]`
- **契约**：`AudioPerceptionEvent.kind ∈ AudioPerceptionKind`（5 类白名单：`audio_speech_rapid` / `audio_voice_raised` / `audio_telephone_persistent` / `audio_distress_cry` / `audio_anomaly_other`，`event.py:28-38`）
- **运行时是否被调用**：✅ 是（gateway 注入 `set_live_audio_events(events)` 走 Live Audio Loop）
- **产出数量**：✅ 9 unique audio events
- **`kind` 异常**：❌ 全部为 `audio_distress_cry`
  - **根因**：`config/live_audio.yaml:139` `audio.tier1.class_map_path: ""`（缺 521 类名映射文件），Tier1 YAMNet 推理输出的 class index 无法映射回中文/英文标签，兜底返回 `audio_distress_cry`
  - **证据**：`pipeline.py:143-164` Tier1 `_run_tier1()` 在 `tier1.class_map_path=""` 时走 Stub 兜底
  - **gap report 已识别**：Gap-2（YAMNet class_names 缺失），见 `docs/reports/LIVE-PRODUCT-STATE-AND-GAP-REPORT-2026-08-22.md`
- **结论**：✅ **Layer 1-2 工作**，但 Tier1 标签失真为单一 `audio_distress_cry`，不阻断后续链路

### Layer 2 · AudioPerceptionEvent → LiveAudioFrame（live_adapter 摄取）

- **入口**：`src/home_perception/visualizer/viewer/live_adapter.py:201` `audio_result_to_live_audio()`（fail-closed 鸭子类型映射）
- **运行时是否被调用**：✅ 是（gateway `_feed_live_audio()` → `acc.ingest_audio(audio)`）
- **投影结果**：`_audio_events` 列表接收 9 条，`audio_kinds={'audio_distress_cry'}` 单一种
- **结论**：✅ **Layer 2 工作**，投影侧正确反映 Layer 1 的失真（不伪造语义）

### Layer 3 · AudioPerceptionEvent → RiskSignal（audio→risk 桥接）

- **入口**：`src/home_perception/integration/audio_adapter.py:30` `adapt_audio_event(event, device_id, subject_id)` → `RiskSignal(source=AUDIO, category=COMMUNICATION, transition=RAISED)`
- **调用方**：**仅** `src/home_perception/runtime/audio_session_recorder.py:209`（`AudioSessionRecorder.record_session`）
- **运行时是否被调用**：⚠️ **路径存在，但不在 Live 帧循环里**
  - `AudioSessionRecorder` 是 **Audio Loop 独立收割器**（`pipeline.py:943-978` `process_audio_session()`），由 `from_settings` 在 `audio.enabled=true + memory.enabled=true` 时装配
  - **当前 live_audio.yaml 配置**：
    - `audio.enabled=true` ✅（行 135）
    - `memory.enabled=false` ❌（行 120）
  - **结果**：`audio_recorder = None`，Live 帧循环根本不调 `record_session()`
  - audio event 在 Live Loop 里只走 `_feed_live_audio → ingest_audio`，**从不进入 Layer 3 桥接**
- **结论**：❌ **Layer 3 在 Live 模式下未生效**——audio→risk 桥接代码存在，但 live 帧循环没有调用入口

### Layer 4 · RealTimeRiskEvaluator（视觉侧信号生成）

- **入口**：`src/home_perception/analysis/realtime_risk_evaluator.py:91`，`evaluate(ctxs, now) → list[RiskSignal]`
- **输入类型**：`RealtimeContext = BehaviorState + recent_behavior`（**非 AudioPerceptionEvent**）
- **触发条件**（line 281-308）：
  - `dwell_seconds >= thresholds.long_duration_seconds`
  - `visits_in_window >= thresholds.repeat_visit_count`
  - `state.is_odd_hour == True`
- **SourceModality**：固定 `SourceModality.VISION`（line 343，音频信号不走此评估器）
- **运行时是否被装配**：⚠️ 取决于 `realtime_risk.enabled`
  - `from_settings` 装配规则（`pipeline.py:487`）：`realtime_enabled = settings.realtime_risk.enabled or settings.memory.enabled`
  - **当前 live_audio.yaml**：`realtime_risk.enabled=false` + `memory.enabled=false`
  - **结果**：`_realtime_evaluator=None`，`process_frame()` 整个 Stage B/C/D 实时旁路（`pipeline.py:798-844`）被跳过
- **结论**：❌ **Layer 4 完全旁路**——既无视觉 BehaviorBuilder 计算，也无 RiskSignal 产出

### Layer 5 · `_act_on_signals`（Stage D 决策接入）

- **入口**：`src/home_perception/runtime/pipeline.py:890-941`，`adapter → DecisionEngine → ActionExecutor`
- **触发条件**（line 840）：`if self._decision_enabled and risk_signals:` —— 两个开关都要为 true
- **当前 live_audio.yaml**：
  - `realtime_risk.decision_enabled=false`（行 117）
- **结果**：`_decision_enabled=False`，`_act_on_signals` 永远不被调用
- **结论**：❌ **Layer 5 完全旁路**——即使有 RAISED 信号也不会进 DecisionEngine

### Layer 6 · signal_adapter.risk_signal_to_perception

- **入口**：`src/home_perception/analysis/signal_adapter.py:32` `risk_signal_to_perception(signal, device_id, location)`
- **翻译规则**（line 106-150）：
  - `dwell_seconds >= threshold` → `event_type="abnormal_dwell"`
  - `visits_in_window >= threshold` → `event_type="repeat_visit"`
  - `is_odd_hour == True` → `event_type="visit_pending_verify"`
  - 兜底 → `event_type="visit_pending_verify", score=0.5`
- **优先级**：dwell > visits > odd_hour
- **关键问题**：audio `RiskSignal` 的 `features` 由 `adapt_audio_event`（`audio_adapter.py:58-69`）构造，**不包含** `dwell_seconds` / `visits_in_window` / `is_odd_hour`，只含 `audio_kind` / `audio_score` / `audio_confidence` / `labels` / `source_segment_ids` / `audio_tier1_max_score`
- **翻译结果**：audio RiskSignal 落兜底分支 → `event_type="visit_pending_verify", score=0.5`
- **运行时是否被调用**：❌ 否（Layer 5 不调）
- **结论**：⚠️ **Layer 6 设计有歧义**——audio 信号走通时会得到 `visit_pending_verify`（仅 LOW + MONITOR，不通知家属），与产品意图（音频异常 → NOTIFY_FAMILY）不一致。但当前因为 Layer 4/5 关闭，无实际触发

### Layer 7 · DecisionPolicy.decide() → WarningEvent

- **入口**：`src/home_perception/analysis/decision_policy.py:137` `RuleBasedDecisionPolicy.decide(input)`
- **决策逻辑**（line 196-293）：
  - 空 `trigger_events` → **抑制**（return None，`SuppressReason.NO_TRIGGER_EVENTS`）
  - 全部 `visit_normal` 无 odd_hour 叠加 → **抑制**（`SuppressReason.ALL_SUPPRESSED_NORMAL`）
  - 其余按 routing_table（line 120-126）映射 `risk_level + recommended_action`：
    - `high_risk_approach` → HIGH / ESCALATE_COMMUNITY
    - `abnormal_dwell` → LOW / NOTIFY_FAMILY
    - `repeat_visit` → LOW / NOTIFY_FAMILY
    - `visit_pending_verify` → LOW / MONITOR
    - `visit_normal` + odd_hour → LOW / MONITOR
- **多事件组合**：max risk_level wins（line 241）
- **runtime 决策引擎是否被装配**：✅ 是（`DecisionEngine` 是历史路径默认组件）
- **运行时实际调用**：✅ 视觉 `_act_on_event`（`pipeline.py:880`）每帧都调 `decision_engine.evaluate(percs)`
- **CAVIAR demo 实际触发**：
  - `rule.long_duration_seconds=1.5`（`live_audio.yaml:69`）+ `dwell_threshold_s=30`（行 43）—— 25s CAVIAR 短片主体停留不会持续 >1.5s（来访后立刻离开）
  - `repeat_visit_count=3`（行 70）—— CAVIAR 短片主体不会进出 3 次
  - `odd_hour_set=[23, 0, 1, 2, 3, 4]`（行 71） + `demo_clock_start="2026-07-19T23:30:00+00:00"`（行 106）—— 模拟时钟 23:30 触发 odd_hour，但视觉主体必须先进入场景才能命中
- **实际产出**：CAVIAR demo 大概率产 `visit_normal`（无叠加）→ **抑制**（return None），所以 `FrameResult.warnings = []`
- **结论**：⚠️ **Layer 7 接口正常但决策抑制**——CAVIAR demo 短片 + 视觉短停留 + 不重复来访，`visit_normal alone` 走抑制路径返回 None

### Layer 8 · ActionExecutor.execute() → ActionCommand[]

- **入口**：`src/home_perception/action/executor.py:99` `ActionExecutor.execute(warning)`
- **依赖**：`WarningEvent`（Layer 7 产出）
- **运行时是否被装配**：✅ 是
- **运行时实际调用**：✅ 视觉 `_act_on_event`（`pipeline.py:884`）`executor.execute(w)`，但因 `w is None`（Layer 7 抑制）从不进入
- **结论**：❌ **Layer 8 无输入**——因 Layer 7 抑制，无 warning → 无 command

### Layer 9 · ProjectionAccumulator._risk_transition 状态机

- **入口**：`src/home_perception/visualizer/viewer/live_adapter.py:1054-1067`
- **状态机**（覆盖式，非累积）：
  ```python
  cur_risky = bool(self._last_risk_levels)
  if cur_risky and not self._risk_active:
      self._last_risk_transition = "raised"
  elif not cur_risky and self._risk_active:
      self._last_risk_transition = "cleared"
  elif cur_risky:
      self._last_risk_transition = "active"
  else:
      self._last_risk_transition = None  # 持续无风险（含首连初始化）
  self._risk_active = cur_risky
  ```
- **`_last_risk_levels` 来源**（line 1002）：`live.get("risk_levels", ())`，由 `frame_result_to_live_frame` 从 `FrameResult.warnings` 提取
- **运行时实际状态**：
  - Layer 7 抑制 → `FrameResult.warnings=[]` → `_last_risk_levels=()`
  - 178 帧全部走"else 分支" → `_last_risk_transition=None` 持续不变
- **结论**：✅ **Layer 9 状态机逻辑正确**——但因输入恒空，状态机从未进入 `raised/active/cleared` 任何非空分支

### Layer 10 · gateway WS 推送 + rdelta 过滤

- **入口**：`src/silver_demo/gateway.py:330-351`
- **rdelta 推送条件**（line 334-346）：只有 `risk_transition` 或 `risk_levels` 或 `reason_summary` 等任一字段非空才推
- **运行时实际**：
  - `_last_risk_transition=None` → rdelta 整体被吞
  - 仅在 2 个特殊帧次推了 CLEARED（见 §2 解释）
- **结论**：✅ **Layer 10 推送逻辑正确**——rdelta 被吞是正确行为，因为风险状态机本就没产生状态变化

---

## 2. 「0 RAISED / 2 CLEARED」的具体解释

> 这是 audit 中**最容易引起误解**的异常，必须拆解。

**第一层误解**：「CLEARED」意味着"曾有 RAISED"。

**事实**：`_risk_transition` 状态机对**当前帧 vs 上一帧**的状态差判定：
- 上一帧 `_last_risk_levels` 非空 + 当前帧空 → 推 `cleared`
- **反推**：出现 cleared 的帧次之前，必有某一帧 `_last_risk_levels` 非空

**那么非空的 `_last_risk_levels` 来自何处？**

候选路径（按可能性排序）：

1. **accumulator 重建但 `_risk_active` 残留**（最可能）：
   - `switch_source`（line 567）或 `_rebuild_pipeline`（line 626）或 POST `/demo/reset`（line 1099）清空 `_live_accumulator=None`（line 600）+ 同步重置 `_prev_risk_fp=None`（line 607）
   - 但 ProjectionAccumulator 实例化时 `_risk_active=False`、`_last_risk_transition=None`（line 549-555 `__init__`）
   - **新实例首帧**：`_last_risk_levels=()`（空，因 Layer 7 抑制）→ 走 else → `_risk_transition=None` → rdelta 不推
   - **不应产生 cleared**

2. **前端 `_prev_risk_fp` 与后端 `_last_risk_levels` 不一致**：
   - 前端 `_prev_risk_fp` 是上次 risk_fingerprint()，后端 `_last_risk_levels` 是当前帧
   - 首连时 `prev=None` → 必然推首次 rdelta（含完整当前状态）
   - 若首次 rdelta 时 `_last_risk_transition=None` → gateway 仍可能因 `risk_levels`/`reason_summary` 等字段非空而推
   - 但当前所有字段都空 → 首次也不会推

3. **rdelta 字段填充异常**：
   - `extract_risk_delta` 在 fingerprint 变化时把 `_last_*` 字段拷贝到 rdelta
   - 若 `_last_*` 实际为非空（"残影"）→ 推了一次空状态 → 后续帧 fingerprint 一致 → 不推
   - **唯一一次残影 + 后续一直空** = 1 条 cleared；但实际观察到 **2 条 cleared**

4. **gateway loop 重置 + accumulator 累积卡住**：
   - loop_count=2 时（`scenario.loop=True`），每轮结束重建帧源 + 重建 accumulator
   - 重建时 `_live_accumulator=None` → 下次 `_ensure_live_accumulator()` 重新构造（line 422-428）
   - **新实例首帧**：`_last_risk_levels=()` → `transition=None` → 不推
   - 不会产生 cleared

5. **POST /demo/reset 触发**：
   - 用户在浏览器点击 reset 按钮 → 调用 `switch_source(gateway.scenario)` → 同 #1，不产生 cleared

6. **真实原因（最可能）**：**rdelta 消息实际推送了 2 条空状态变化**——例如：
   - 首连时 `_prev_risk_fp=None` → `changed=True` → rdelta 含 `risk_levels=[]`/`risk_transition=None`
   - 但推送条件 `rdelta.get("risk_transition") or rdelta.get("risk_levels")` —— 空 list `[]` 在 Python 是 falsy → 不推
   - **不会推**
   - **真正推 cleared 的可能场景**：accumulator 重建期间，前端还没收到 source_switched，前一帧的 fingerprint 与新帧的 fingerprint "突变" → 但 fingerprint 比的是 `tuple(self._last_risk_levels, ...)`，空 vs 空是相等的 → 不会推

7. **front-end render 时的临时空状态闪烁**：
   - 前端 `live_stream.js` 在收到 source_switched 后 reset，可能短暂渲染"有 risk → 无 risk"动画
   - 但 WS payload 实际只有 2 条 `risk_delta` → 服务端确实推了 2 条 cleared

**当前 audit 未完全解谜 2 CLEARED 来源**——但可以确认：

- ✅ **0 RAISED 完全可解释**：runtime 路径从未产生过 `_last_risk_levels` 非空的帧次（CAVIAR demo + Layer 7 抑制）
- ⚠️ **2 CLEARED 来源仍待解**：可能是 (a) POST /demo/reset 触发的旧 `_risk_active=True` 残留（被 `_rebuild_pipeline` 清理后又因某种 fingerprint 比对异常触发），或 (b) 浏览器 dev tools 抓包时复现 2 次 loop 重置的瞬态

> **下一步**：需要看 `artefacts/telephone_risk_acceptance/ws_payloads.jsonl` 里 2 条 rdelta 的 `frame_index` / `loop_count` / `risk_transition` / `risk_levels` 字段具体值，再反推哪个帧次触发。

---

## 3. 核心死结：三道闸门全部处于关闭态

```
                        配置层                装配层                    投影层
                       (yaml)              (from_settings)            (gateway.run_loop)
                          │                      │                          │
   audio.enabled=true     │ ───────►             │                          │
                          │        ✔ AudioPipeline 加载（Layer 1-2 工作）    │
                          │                      │                          │
   realtime_risk          │                      │                          │
     .enabled=false   ────┼──────►  _realtime_enabled=False ──►  process_frame()
                          │        (Layer 4 完全旁路)            │      Stage B/C/D 跳过
                          │                                     │
   realtime_risk          │                                     │
     .decision_enabled    │ ────►  _decision_enabled=False ─────►│
     =false               │        (Layer 5 完全旁路)            │
                          │                                     │
   memory.enabled         │ ────►  audio_recorder=None          │
     =false               │        (Layer 3 桥接未装配)          │
                          │                      │                          │
                          │                      │                          ▼
                          │                      │                FrameResult.warnings=[]
                          │                      │                _last_risk_levels=()
                          │                      │                _risk_transition=None
                          │                      │                rdelta 整体被吞
                          │                      │                          │
                          ▼                      ▼                          ▼
   ════════════════════════════════════════════════════════════════════════════════
   症状：0 warning / 0 command / 0 raised / 2 cleared / DOM 显示 golden prerender
   ════════════════════════════════════════════════════════════════════════════════
```

**配置层（`config/live_audio.yaml`）—— 三道闸门全关**：

| 配置键 | 当前值 | 行号 | 影响 |
|---|---|---|---|
| `audio.enabled` | `true` | 135 | ✅ 音频可加载 |
| `audio.tier1.class_map_path` | `""` | 139 | ❌ Tier1 标签失真（Gap-2） |
| `realtime_risk.enabled` | **`false`** | 115 | ❌ Layer 4 旁路（关键闸门 1） |
| `realtime_risk.decision_enabled` | **`false`** | 117 | ❌ Layer 5 旁路（关键闸门 2） |
| `memory.enabled` | **`false`** | 120 | ❌ Layer 3 audio_recorder 未装配（关键闸门 3） |

**关键洞察**：live_audio.yaml 与 default.yaml 的 `realtime_risk` 配置**完全相同**，都处于全关态；按注释（行 112-117）这是 "Shadow Mode" 默认配置，目的是先观察误报率再开决策。

但 **CAVIAR demo 视频** 即使 realtime_risk.enabled=true，**视觉侧也无法自然触发 RAISED**（dwell>1.5s / visits≥3 / odd_hour 三阈值在 25s 短片都不命中，odd_hour 23:30 除外但需要主体在场）：

- 真实浏览器 178 帧全跑完都没产生任何视觉 RAISED
- audio 9 条 events 走不通是因为 Layer 3 audio_recorder 装配条件 `memory.enabled=true` 不满足

**即"闸门全部打开"也不能在 CAVIAR 短片里复现 audio→warning→command 闭环**——CAVIAR demo 是**视觉 demo**，音频是叠加证据，不是主驱动信号。

---

## 4. 与设计规范的差距（DESIGN-live-product-ui-restore §8.2）

> §8.2「真实运行断言（最高优先级）」要求 **Frame N → Perception N → Risk N → Decision N → Action N 同帧链路**。

| §8.2 断言 | 当前 runtime 状态 | 偏差 |
|---|---|---|
| Frame N 真实同步帧流 | ✅ MJPEG + frame_tick | 无偏差 |
| Perception N 实时感知 | ✅ perception_delta（148 条） | 无偏差 |
| Risk N 实时风险 | ⚠️ 0 RAISED / 2 CLEARED | **严重偏差** |
| Decision N 实时决策 | ❌ 0 warning | 完全缺失 |
| Action N 实时行动 | ❌ 0 command | 完全缺失 |
| Frame N → N 同帧链路 | ❌ N → Risk N → Decision N → Action N 链断裂 | 整条链断开 |

---

## 5. 修复方向（先 audit 不动手，等 Owner 拍板再做 PR-B）

### 选项 A · 把"三道闸门"打开，但不改语义

**改动**（最小改动）：

```yaml
# config/live_audio.yaml
realtime_risk:
  enabled: true                  # ← 改 false → true
  eval_interval_frames: 1
  decision_enabled: true         # ← 改 false → true
memory:
  enabled: true                  # ← 改 false → true（audio_recorder 装配前提）
```

**预期效果**：

- Layer 4 RealTimeRiskEvaluator 激活 → 视觉 CAVIAR demo 因 odd_hour=23:30 可能产生 RAISED（需主体在场）
- Layer 5 `_act_on_signals` 激活 → RAISED → `signal_adapter.risk_signal_to_perception` → `event_type=visit_pending_verify`（兜底）→ `DecisionPolicy` → `WarningEvent(risk_level=LOW, recommended_action=MONITOR)` → `ActionCommand(LOG_ONLY)`
- Layer 3 `AudioSessionRecorder` 激活 → audio events 独立会话收割（不进 FrameResult，仅 EpisodicRecord）
- **风险**：CAVIAR 25s 短片主体短暂出现，odd_hour 23:30 命中可能产生 LOW MONITOR 警告，对观众来说"很弱"

**优点**：最小改动，能打通链路，能复现 "RAISED → active → CLEARED" 跃迁

**缺点**：音频事件仍不进 FrameResult（Layer 6 兜底 → visit_pending_verify）→ 用户看不到"音频异常 → 警告"

### 选项 B · 打开闸门 + 补 audio→PerceptionEvent 路由

**改动**（中等改动）：

- 选项 A 的所有改动
- 在 `signal_adapter._map_features_to_event` 加 `audio_kind` 分支：
  - `audio_kind=="audio_voice_raised"` → `event_type="abnormal_dwell"`（语义近似）
  - `audio_kind=="audio_telephone_persistent"` → `event_type="repeat_visit"`
  - `audio_kind=="audio_distress_cry"` → `event_type="high_risk_approach"`（高分）
  - `audio_kind=="audio_speech_rapid"` → `event_type="abnormal_dwell"`

**预期效果**：audio 事件真正能进 FrameResult.warnings 并产 WarningEvent → ActionCommand

**缺点**：跨越 ADR-0010 "audio 只产 perception，不产风险" 边界（实际上 signal_adapter 已在翻译 PerceptionEvent，是允许的；改动的是翻译规则）

### 选项 C · 全新设计 audio→warning 直通路径（破坏冻结）

- 不建议。跨越 ADR-0014 冻结架构，必须先提 ADR

---

## 6. 推进步骤建议（不动代码，先报 Owner）

1. **Owner review 本 audit**：确认三道闸门定位准确，确认 0 RAISED / 2 CLEARED 解释可接受
2. **Owner 选定选项**：A / B / C
3. **新增 ADR**（如选 B 或 C）：音频→warning 路径必须先 ADR 化
4. **branch feat/audio-runtime-risk + PR**：
   - 选 A：1 个 yaml 改动 + 1 个 audit 测试（断言"打开后能在 CAVIAR 跑出 RAISED → active → CLEARED"）
   - 选 B：1 个 yaml 改动 + signal_adapter.py 改动 + 3 个新单测（audio_kind → event_type 各分支）+ audit 测试
5. **PR 通过后**：补 Browser Acceptance Gate（参见 `docs/reports/LIVE-PRODUCT-STATE-AND-GAP-REPORT-2026-08-22.md` 6 个 Gate）

---

## 7. 后续 audit 待办（本次未完成）

- ⚠️ 「2 CLEARED」具体帧次来源：需读 `ws_payloads.jsonl` 里 2 条 rdelta 的 `frame_index` / `loop_count` / `risk_transition` / `risk_levels` 字段
- ⚠️ golden_evidence_injector 是否会在 SSR 时给 `_last_risk_levels` 注入预渲染 warning（导致首连 rdelta 有数据）—— 需验证
- ⚠️ live_stream.js 前端 narrative_mode 计算逻辑（决定 neutral / audio_first / risk_first）

---

## 8. 结论

| 问题 | 答案 |
|---|---|
| runtime 是否真的在跑音频感知？ | ✅ 是（Layer 1-2 完整工作） |
| runtime 是否真的在跑 risk 评估？ | ❌ 否（Layer 4 装配开关关闭 + Stage B/C/D 整体旁路） |
| runtime 是否真的在跑决策？ | ❌ 否（Layer 5 决策开关关闭 + CAVIAR demo 即使开也几乎不触发） |
| runtime 是否真的在跑行动？ | ❌ 否（Layer 7/8 因前置抑制无输入） |
| risk_delta 状态机逻辑有 bug 吗？ | ✅ 逻辑正确（live_adapter.py:1054-1067），但因 `_last_risk_levels` 恒空而长期 None |
| rdelta 推送逻辑有 bug 吗？ | ✅ 逻辑正确（gateway.py:334-346），吞掉 rdelta 是正确行为 |
| **根因** | **三道闸门（realtime_risk.enabled / decision_enabled / memory.enabled）全部 false + CAVIAR demo 视觉无法自然触发 RAISED → runtime 链路从未进入"激活"态** |
| **0 RAISED 是否 bug** | **否**——runtime 路径设计如此，需要配置开关打开才能产生 |
| **2 CLEARED 是否 bug** | **可能**——理论上不应该出现 cleared（前置从未 raised），需进一步定位 |
| **是否需要修复** | **是**——但必须先 Owner 拍板选项（A/B/C），再走 ADR + PR 流程，不直接动手 |

---

## 9. 相关 ADR / 设计文件

- `docs/ADR/0014-freeze-governance-three-levels.md` — 三级冻结（不可破坏）
- `docs/ADR/0021-realtime-riskstream-concrete-design.md` — 实时风险流设计
- `docs/ADR/0026-audio-perception-chain-concrete-design.md` — 音频感知链设计
- `docs/ADR/0030-decision-boundary-contract.md` — 决策边界契约
- `docs/design/golden-case/DESIGN-live-product-ui-restore.md` §4.6 / §8.2 — risk_transition 状态机契约 / 真实运行断言
- `docs/reports/LIVE-PRODUCT-STATE-AND-GAP-REPORT-2026-08-22.md` — 上游报告（产品现状与设计差距）

---

> **审计完成。等待 Owner 拍板选项 A/B/C 后再进入 PR-B 实现阶段。**