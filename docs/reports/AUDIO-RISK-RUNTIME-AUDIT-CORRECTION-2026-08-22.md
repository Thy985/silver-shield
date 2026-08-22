# Runtime Risk Audit Correction + Audio→Risk Runtime Entry Audit · 2026-08-22

> **审计对象**：SilverShield Live Runtime（`silver_demo` + `home_perception.runtime`）
>
> **审计起因**：在 `RUNTIME-RISK-ROOT-CAUSE-AUDIT-2026-08-22.md` 写完后，Owner 指出 (a) 原报告 A/B 选项划分有偏差（拒绝把 audio 硬翻译成视觉 event_type，提议 "接通而非重新发明"），(b) 要求先做 3 个 ADR 前审查问题（Live Runtime 入口 / Audio RiskSignal 进 Decision / telephone_risk decision contract），(c) 要求先把 2 CLEARED 的真实 payload 拉出来取证，再决定下一步。
>
> **本报告**：
> 1. **重大修正** —— 推翻原报告对 Layer 4 的判断（"完全旁路" 是错的）
> 2. **Step 1 取证** —— 2 CLEARED 真实 payload 完整拆解
> 3. **Step 2-4 ADR 前审查** —— Audio→Risk Live Runtime 入口 / memory 错耦合 / Decision contract modality-aware 缺口
> 4. **Owner 3 问答案 + 修复方向**（不动代码）
>
> **关键认识**：原 audit 把系统描述成"Perception Demo"——这个判断**仍然成立**，但**根因细节错了**。runtime **已经产生过 RAISED + CLEARED 视觉信号**（来自 `_apply_demo_memory_overrides` 强制覆盖 memory.enabled=true 连带开启 realtime），只是因为 `decision_enabled=false` 而没进 DecisionEngine。

---

## 0. TL;DR（关键结论）

| 议题 | 结论 |
|---|---|
| 原报告「Layer 4 完全旁路」判定 | **错误**。runtime 实际产生过 1 个 RAISED + 1 个 CLEARED（视觉信号）。原因：`_apply_demo_memory_overrides` 把 `memory.enabled=True` 强制覆盖，连带 `realtime_enabled=True`，RealTimeRiskEvaluator 装配生效 |
| 2 CLEARED 来源 | 同一条 `RiskSignal(transition=CLEARED, features.reason="subject_missing")` 被推了 2 次，**不是** 2 条不同信号。视觉侧 RealTimeRiskEvaluator `_emit_cleared_missing()` 在 frame_index=1 兜底产物 |
| `risk_transition` 顶层字段语义 | **不是 RAISED/CLEARED 的判定**，是 ProjectionAccumulator 服务端状态机的"当前帧 vs 上一帧"覆盖式信号（None=无变化）。真正的 RAISED/CLEARED 语义在 `risk_signals[].transition` |
| Audio→Risk Live Runtime 入口 | **完全不存在**。`_feed_live_audio` 只做 evidence 投影，不进 risk 链；`process_audio_session` 是独立 Audio Loop 入口（不在帧循环里） |
| `audio_recorder` 与 `memory` 耦合 | **架构错耦合**。`audio_recorder` 装配条件是 `audio.enabled + episodic_shadow + episode_builder + memory_store`，缺一不可——这是因为 ADR-0027 D3「决策门槛」把"产生 Warning 才落库"硬绑到 audio 主链路上 |
| Decision contract modality-aware | **缺失**。`DecisionInput.trigger_events: tuple[PerceptionEvent, ...]` 强制只收 PerceptionEvent；`signal_adapter` 把 audio RiskSignal 兜底翻译成 `visit_pending_verify`；`routing_table` 是 5 个视觉 event_type 表，没有 modality 分流 |
| 修复方向 | 拆 `audio_recorder`（实时风险+决策+行动 vs 落库可拆）；DecisionInput 加 `risk_signals` 字段 + modality-aware routing（按 source/category 分流）；Live frame loop 加 audio 入口（在 process_frame 或新 `_act_on_audio_signal`） |

---

## 1. 重大修正：原 audit Layer 4 判定错误

### 1.1 原报告判断

`RUNTIME-RISK-ROOT-CAUSE-AUDIT-2026-08-22.md` §3「三道闸门全关」判断：

> `realtime_risk.enabled=false`（`live_audio.yaml:115`） → Layer 4 旁路
> `realtime_risk.decision_enabled=false`（`live_audio.yaml:117`） → Layer 5 旁路
> `memory.enabled=false`（`live_audio.yaml:120`） → Layer 3 audio_recorder 未装配

### 1.2 事实反驳

**`gateway.py:520-538` `_apply_demo_memory_overrides` 在 `assemble` 和 `_rebuild_pipeline` 时强制覆盖**：

```python
mem = self.hp_settings.memory
mem.enabled = True           # ← 强制 true
mem.episodic_shadow = True
mem.consumer_enabled = True
mem.reasoning_enabled = True
```

`pipeline.py:487` `from_settings` 装配规则：

```python
realtime_enabled = settings.realtime_risk.enabled or settings.memory.enabled
```

→ `memory.enabled=True`（被强制） → `realtime_enabled=True` → RealTimeRiskEvaluator 装配生效

→ `_emit_raised` 在 frame_index=0 时（demo 模拟时钟 23:30，在 odd_hour 区间）触发 RAISED（signal_id=6396d097）
→ frame_index=1 时该 subject 已离场 → `_emit_cleared_missing` 兜底产 CLEARED（signal_id=5e8b03b3，paired_signal_id=6396d097）

### 1.3 修正后判定

| 层 | 原判定 | 修正后判定 |
|---|---|---|
| Layer 4 RealTimeRiskEvaluator | ❌ 完全旁路 | ✅ **激活**（因 memory.enabled 强制 true 连带开启）；产出 RAISED + CLEARED |
| Layer 5 `_act_on_signals` | ❌ 完全旁路 | ❌ **仍完全旁路**（decision_enabled 真的为 false） |
| Layer 3 audio_recorder | ❌ 未装配 | ❌ **仍未装配**（audio_session_recorder 仅在 `_act_on_audio_session` 显式调用时跑；live 帧循环不调） |

**Layer 4 已激活但 Stage D 决策接入仍被阻断** —— 这才是真正的死结。

---

## 2. Step 1 取证：2 CLEARED 真实 payload

### 2.1 数据源

`artifacts/telephone_risk_acceptance/ws_payloads.jsonl`（166 KB，`.gitignore` 排除，810 条 wrapper `{"recv_ts", "msg"}`），其中 `risk_delta` 仅 **2 条**。

### 2.2 消息类型分布（810 条 wrapper）

| type | 计数 |
|---|---|
| frame_tick | 178 |
| evidence_delta | 178 |
| perception_delta | 148 |
| snapshot | 2 |
| source_switched | 2 |
| risk_delta | **2** |

### 2.3 2 条 risk_delta 完整 payload（结构相同 / 同信号 / 间隔 4ms）

> **重大发现**：两条 rdelta 的 `risk_signals[0].signal_id` 都是 `5e8b03b3-22c7-4d36-9de7-9197220d6ffa`，是**同一条信号被推了 2 次**——大概率是前端开了 2 个 WS 连接，或 `_prev_risk_fp=None` 时第一次全量 + 第二次基线更新后的去重判断异常。

**risk_delta #1（line 451，recv_ts 13:05:31.958）**：

```json
{
  "type": "risk_delta",
  "frame_index": 0,
  "risk_levels": [],
  "reason_summary": [],
  "recommended_actions": [],
  "command_types": [],
  "risk_transition": null,
  "trigger_events": [],
  "perception_scores": [],
  "warning_ids": [],
  "risk_signals": [{
    "signal_id": "5e8b03b3-22c7-4d36-9de7-9197220d6ffa",
    "subject_type": "visitor",
    "subject_id": "713c3f23-4d18-4346-9963-82857f7f71c3",
    "category": "behavioral",
    "source": "vision",
    "transition": "cleared",
    "features": {"reason": "subject_missing"},
    "paired_signal_id": "6396d097-0472-49e9-ada7-38ee2540b90e",
    "track_id": null,
    "visitor_instance_id": "713c3f23-4d18-4346-9963-82857f7f71c3",
    "severity_hint": null,
    "created_at": "2026-07-19T23:30:00.500000+00:00"
  }],
  "device_id": null,
  "elder_id": null,
  "active_warnings": [],
  "case_time": 0.0
}
```

**risk_delta #2（line 454，recv_ts 13:05:31.962）**：除 `recv_ts` 外与 #1 完全相同（同 signal_id、同 paired_signal_id、同 created_at）。

### 2.4 关键事实拆解

| 字段 | 值 | 含义 |
|---|---|---|
| `risk_transition` | `null` | **不是 cleared** —— ProjectionAccumulator 服务端状态机的"覆盖式"字段，None=本帧风险指纹无变化（详情见下） |
| `risk_levels / reason_summary / recommended_actions / command_types` | 全部 `[]` | FrameResult.warnings=[]（CAVIAR 视觉不触发；Layer 7 抑制） |
| `risk_signals[0].source` | `"vision"` | **不是 audio 信号**——是视觉侧 RealTimeRiskEvaluator 产出 |
| `risk_signals[0].category` | `"behavioral"` | 视觉侧 category |
| `risk_signals[0].transition` | `"cleared"` | **真正的 RAISED/CLEARED 语义在这里**（RiskSignal 自身的 transition） |
| `risk_signals[0].features.reason` | `"subject_missing"` | **只能由 `_emit_cleared_missing()` 路径产出**（`realtime_risk_evaluator.py:202-208`） |
| `risk_signals[0].paired_signal_id` | `"6396d097-..."` | **指向之前发出过的 RAISED signal_id** |
| `risk_signals[0].created_at` | `"2026-07-19T23:30:00.500000+00:00"` | demo 模拟时钟起点 +0.5s，第 1 帧 |

### 2.5 真实发生过程（基于证据反推）

```
demo_clock 23:30:00.000 (frame_index=0)
    ↓
    RealTimeRiskEvaluator._is_triggered:
      - subject 713c3f23 首次出现在 ctxs
      - state.is_odd_hour = True（demo_clock 23:30 在 odd_hour_set [23,0,1,2,3,4]）
      → triggered = True
    ↓
    _emit_raised(ctx, now) → RiskSignal(
      signal_id=6396d097-..., transition=RAISED,
      features={"dwell_seconds":0,"visits_in_window":0,"is_odd_hour":True,"thresholds":{...}}
    )
    ↓
    FrameResult.risk_signals = [RiskSignal(6396d097, RAISED)]
    ProjectionAccumulator 累积

demo_clock 23:30:00.125 (frame_index=1)
    ↓
    subject 713c3f23 已离场（CAVIAR 第 1 帧就消失）
    ↓
    RealTimeRiskEvaluator missing_ids 路径
    → _emit_cleared_missing(713c3f23, "6396d097-...", now)
    → RiskSignal(
      signal_id=5e8b03b3-..., transition=CLEARED,
      paired_signal_id=6396d097-...,
      features={"reason":"subject_missing"}
    )
    ↓
    FrameResult.risk_signals = [RiskSignal(5e8b03b3, CLEARED)]
    ProjectionAccumulator 累积

后续 177 帧：subject 不再出现，risk_signals 累积列表里保留这 1 条 CLEARED
    ↓
    risk_fingerprint 变化 → extract_risk_delta 推 rdelta
    ↓
    _last_risk_levels=[] → _risk_transition=None（不是 cleared）
    ↓
    但 _last_risk_signals 非空 → rdelta 被推（risk_signals 字段）
    ↓
    推 2 次（前端 2 WS 连接 或 基线更新异常）
```

### 2.6 「0 RAISED / 2 CLEARED」统计的真相

- **0 RAISED 计数**：是真的——`_last_risk_signals` 累积里**没有 RAISED 信号**，只有 1 条 CLEARED。
  - **为什么 RAISED 没在累积里**：`_emit_raised` 触发的同一帧 `_emit_cleared_missing` 也被推，两条都会进 `FrameResult.risk_signals`。但 ProjectionAccumulator 在 `_accumulate` 时把 `risk_signals` 列表**整体覆盖**到 `_last_risk_signals`（line 1012）。
  - frame_index=0 RAISED → _last_risk_signals=[RAISED]
  - frame_index=1 CLEARED → _last_risk_signals=[CLEARED]（RAISED 被覆盖）
  - 后续帧空 → _last_risk_signals=[]
  - 累积到 LIVE Adapter 时只有最后 1 条 CLEARED 留下
  - **浏览器 DOM 看到「CLEARED 但无 RAISED」**——因为 RAISED 已经被后到的 CLEARED 覆盖
- **2 CLEARED 计数**：是真的——同一条 CLEARED 被推了 2 次
  - 第一次推：浏览器首连，前端 `_prev_risk_fp=None` → 携带完整当前状态
  - 第二次推：4ms 后，浏览器 second connect 或 _prev_risk_fp 异常触发

### 2.7 「`risk_transition` 字段 vs `risk_signals[].transition` 字段」语义区分

| 字段 | 来源 | 语义 |
|---|---|---|
| `risk_transition` | ProjectionAccumulator `_accumulate` 状态机（`live_adapter.py:1054-1067`） | "本帧 vs 上一帧"覆盖式信号：`raised` / `cleared` / `active` / `None`。**基于 `_last_risk_levels`（即 FrameResult.warnings 的 risk_level）**——CAVIAR demo 持续为空，所以持续 None |
| `risk_signals[].transition` | RealTimeRiskEvaluator 产出（`realtime_risk_evaluator.py:91-220`） | RiskSignal 自身的 RAISED/CLEARED 语义——基于 BehaviorState 状态机。**这是真正的"风险机"语义** |

**原 audit 误以为 `risk_transition=null` = "无风险"**——这是错的。`risk_transition=null` 仅表示"本帧 vs 上一帧 FrameResult.warnings 没变化"，并不表示 RealTimeRiskEvaluator 没产 RAISED/CLEARED。RiskSignal 的状态跃迁在 `risk_signals` 字段里。

---

## 3. Step 2 A 问：Audio→Risk Live Runtime 入口审查

### 3.1 三条可能路径排查

**路径 a · `_feed_live_audio`（gateway.py:376-398）**

```python
def _feed_live_audio(self, acc: ProjectionAccumulator) -> None:
    """按帧位置把注入的音频事件流入 Live Adapter（确定性、幂等）。"""
    events = self._live_audio_events
    if not events: return
    idx = self._frame_index
    if idx < 0 or idx >= len(events): return
    ev = events[idx]
    data = ev.to_dict() if hasattr(ev, "to_dict") else ev
    try:
        acc.ingest_audio(data)  # ← 只调 Live Adapter 投影
    except Exception as exc:
        structlog.get_logger(__name__).warning("live_audio_frame_ingest_failed", ...)
```

**判定**：**只进 Live Adapter evidence 投影，不进 risk 链**。完全没调 `adapt_audio_event` / `risk_signal_to_perception` / `decision_engine.evaluate`。

**路径 b · `process_audio_session`（pipeline.py:943-978）**

```python
def process_audio_session(
    self,
    events: list[AudioPerceptionEvent],
    *,
    audio_session_id: str | None = None,
    source_path: str | None = None,
) -> AudioSessionSummary | None:
    """收割一次音频会话（ADR-0027 运行时接线，独立 Audio Loop 入口）。"""
    if self._audio_recorder is None or not self._audio_recorder.enabled:
        return None
    return self._audio_recorder.record_session(events, ...)
```

调用方：`integration/loop/runner.py:486`

```python
# 帧循环结束后
audio_summary = pipeline.process_audio_session(
    list(synth.audio_events),
    audio_session_id="integration_audio_session",
)
```

**判定**：**独立 Audio Loop 入口，由 integration test runner 显式调用，不在 Live frame loop 里**。`gateway.run_loop` 不调它。

**路径 c · `process_frame`（pipeline.py:780-862）**

```python
# Stage B/C
if self._realtime_enabled and self._behavior_builder is not None:
    is_eval_frame = (frame_index % self._eval_interval_frames) == 0
    if is_eval_frame:
        ...
        risk_signals = self._realtime_evaluator.evaluate(ctxs, now)

        # Stage D
        if self._decision_enabled and risk_signals:
            rt_percs, rt_warnings, rt_cmds = self._act_on_signals(risk_signals, now)
```

**判定**：**视觉 RiskSignal 走这里**——但 `process_frame` **不接收任何 audio 入参**（只有 `frame`, `frame_index`）。

### 3.2 Step 2 A 问答案

**Audio→Risk Live Runtime 入口完全不存在**。

- 路径 a（`_feed_live_audio`）：只进 evidence 投影
- 路径 b（`process_audio_session`）：独立 Audio Loop，不在帧循环里
- 路径 c（`process_frame`）：视觉专用，无 audio 入参

架构事实：**当前 Live Runtime 是"视觉实时 + 音频异步收割"两套**——视觉随帧，音频会话结束后才进决策。**没有"音频随帧进决策"的入口**。

### 3.3 Owner 倾向方案

> **倾向方案**：让 Live frame loop 加 audio 入口（修改 `process_frame` 或新增 `_act_on_audio_signal` 路径），audio 随每帧走通"audio → RiskSignal → DecisionEngine → Warning → Action"完整链，而不是会话结束才收割。

---

## 4. Step 3：audio_recorder 与 memory 错耦合审查

### 4.1 当前装配条件（pipeline.py:582-609）

```python
audio_recorder: AudioSessionRecorder | None = None
pipeline_memory_hook: MemoryHook | None = None
pipeline_metrics = PipelineMetrics()
if episodic_shadow and episode_builder is not None and memory_store is not None:
    # 必须有 hook 才能构造 audio_recorder
    link_store = CrossModalLinkStore()
    cross_modal_runtime = CrossModalLinkRuntime(memory_store, link_store, min_overlap_seconds=0.0)
    pipeline_memory_hook = MemoryHook(
        episode_builder, memory_store, True, pipeline_metrics,
        cross_modal_runtime=cross_modal_runtime,
    )
if settings.audio.enabled:
    if pipeline_memory_hook is not None:
        audio_recorder = AudioSessionRecorder(
            decision_engine, executor, pipeline_memory_hook, device_id=device_id,
        )
    else:
        log.warning(
            "pipeline.audio_requires_episodic_shadow",
            note="audio.enabled=true 但 Memory 影子未激活（需 memory.enabled + episodic_shadow），音频闭环未装配",
        )
```

### 4.2 错耦合的事实

**`audio_recorder` 必须满足 4 个条件才能装配**：

1. `audio.enabled=true`
2. `episodic_shadow=true`
3. `episode_builder is not None`
4. `memory_store is not None`

→ 即 **`audio.enabled=true` 但 `memory.enabled=false` 时 audio_recorder 永远不会被装配**（pipeline.py:606-609 仅 warning，不构造）。

### 4.3 错耦合的根因（ADR-0027 D3）

`AudioSessionRecorder.record_session`（`runtime/audio_session_recorder.py:125-295`）的设计：

```python
# Step 1: 收集 evidence
evidence = [...]
# Step 2: 翻译成 RiskSignal
signals = [adapt_audio_event(ev, ...) for ev in events]
# Step 3: 翻译成 PerceptionEvent → DecisionEngine
percs = [risk_signal_to_perception(sig, ...) for sig in signals]
warning = self._decision_engine.evaluate(percs) if percs else None
# Step 4: ActionExecutor
if warning is not None:
    actions = list(self._executor.execute(warning))
# Step 5: 落库（无 WarningEvent → 不落库）
if warning is None:
    return self._empty_summary(...)  # D3 门槛
if self._memory_hook is not None:
    self._memory_hook.record(None, [warning], actions, ...)
```

**D3 决策门槛**：只有产生 WarningEvent 的会话才落库 EpisodicRecord。所以 `memory_hook` 是必需的。

**但**：audio→risk→decision→action 主链路（Step 1-4）**不依赖 memory_hook**——只有 Step 5 落库依赖。**架构把 Step 1-4 的可用性绑死在 Step 5 上**，这是错耦合。

### 4.4 Owner 提的拆开方向（待 ADR 化）

> 把 `AudioSessionRecorder` 拆成两部分：
>
> - **AudioRiskDispatcher**（实时风险+决策+行动，可选，含 decision_enabled 开关）—— **不依赖 memory**
> - **EpisodicMemoryWriter**（落库，可选，含 episodic_shadow 开关）—— 仅当 memory 启用时存在
>
> 这样 audio→risk→decision→action 主链路不再受 memory 配置阻断。

### 4.5 Step 3 答案

**`audio_recorder` 装配条件确实是错耦合**——audio 主链路（实时风险）被"memory 落库"绑死，违反"实时感知"和"历史记忆"分层。修复方向已清晰（拆 AudioRiskDispatcher / EpisodicMemoryWriter），但需要新 ADR 化才能动。

---

## 5. Step 4：Decision contract modality-aware 缺口审查

### 5.1 当前 DecisionInput 契约（`analysis/decision_contract.py:159-204`）

```python
DECISION_INPUT_FIELD_WHITELIST: frozenset[str] = frozenset(
    {
        "trigger_events",           # tuple[PerceptionEvent, ...] ← 强制只收 PerceptionEvent
        "decision_context",
        "reasoning_input",
        "reasoning_result",
        "prior_warning",
    }
)

@dataclass(frozen=True)
class DecisionInput:
    trigger_events: tuple[PerceptionEvent, ...]
    decision_context: DecisionContext
    reasoning_input: ReasoningInput | None = None
    reasoning_result: ReasoningResult | None = None
    prior_warning: WarningEvent | None = None
```

**导入期 fail-closed 断言（line 311-329）**：字段名必须等于白名单；任何新增字段会立刻炸。

### 5.2 当前 RuleBasedDecisionPolicy 路由表（`analysis/decision_policy.py:120-126`）

```python
DEFAULT_ROUTING_TABLE: dict[str, tuple[str, str, str]] = {
    "high_risk_approach": ("HIGH", "ESCALATE_COMMUNITY", "多风险规则同时命中"),
    "abnormal_dwell": ("LOW", "NOTIFY_FAMILY", "异常停留"),
    "repeat_visit": ("LOW", "NOTIFY_FAMILY", "重复访问"),
    "visit_pending_verify": ("LOW", "MONITOR", "未在白名单"),
    "visit_normal": ("LOW", "MONITOR", "异常时段访问"),
}
```

**5 个 key 全部是视觉 event_type**——`event_type` 来自 `PerceptionEvent.event_type`（视觉侧 5 类枚举）。

### 5.3 当前 audio RiskSignal 进 Decision 的唯一路径（`runtime/audio_session_recorder.py:225-251`）

```python
# 2) 信号翻译（AudioPerceptionEvent → RiskSignal）
signals = []
for ev in events:
    sig = adapt_audio_event(ev, ...)
    if sig.transition is SignalTransition.RAISED:
        signals.append(sig)

# 3) 感知映射 + 决策
percs: list[PerceptionEvent] = []
for sig in signals:
    perc = risk_signal_to_perception(sig, self._device_id)
    if perc is not None:
        percs.append(perc)
warning: WarningEvent | None = None
actions: list[ActionCommand] = []
warning = self._decision_engine.evaluate(percs) if percs else None
```

**翻译器行为**（`signal_adapter.py:106-150` `_map_features_to_event`）：
- `dwell_seconds >= threshold` → `abnormal_dwell`
- `visits_in_window >= threshold` → `repeat_visit`
- `is_odd_hour == True` → `visit_pending_verify`
- 兜底 → `visit_pending_verify, score=0.5`

**audio RiskSignal 的 features**（`audio_adapter.py:58-69`）：
```python
features = {
    "audio_kind": event.kind.value,
    "audio_score": round(event.score, 4),
    "audio_confidence": round(event.confidence, 4),
    "labels": list(event.labels),
    "source_segment_ids": list(event.source_segment_ids),
    "audio_tier1_max_score": round(...),
    "audio_tier1_scored_labels": [...],
}
```

**audio features 不含 `dwell_seconds` / `visits_in_window` / `is_odd_hour`**——所以 `signal_adapter` 必然走兜底分支 → `event_type="visit_pending_verify", score=0.5`。

**后果**：audio → Decision 永远产出 `LOW / MONITOR`（即 LOG_ONLY），**不会通知家属，不会升级社区**。

### 5.4 Step 4 答案

**Decision contract 完全偏视觉**：

| 缺口 | 表现 |
|---|---|
| `DecisionInput` 不接收 `RiskSignal` | 只收 `PerceptionEvent`，audio RiskSignal 必须经过 `signal_adapter` 翻译才能进 |
| `signal_adapter` 是单模态翻译器 | audio 落兜底 `visit_pending_verify` |
| `routing_table` 是视觉 5 类表 | 没有 AUDIO modality 入口 |
| `RuleBasedDecisionPolicy.decide()` 只看 `event_type` | 不感知 source/category |
| `DecisionEngine._build_reasoning_input` 只查 Memory | audio 上下文无来源 |

### 5.5 Owner 提的修复方向

> **保持 AudioPerceptionEvent / RiskSignal / DecisionEvent 三层语义边界不变**：
>
> ```
> AudioPipeline → AudioPerceptionEvent
> audio_adapter.adapt_audio_event() → RiskSignal(source=AUDIO, category=COMMUNICATION)
> DecisionEngine / DecisionPolicy (modality-aware routing)
> Warning → ActionCommand
> Projection → risk_delta / action
> Browser
> ```
>
> **modality-aware decision mapping**：
>
> ```
> VISION RiskSignal → vision routing (已有 routing_table)
> AUDIO RiskSignal → audio routing (新增 audio_routing_table)
> VISION + AUDIO → combined decision (新增 modality_combiner)
> ```

### 5.6 实现选项（待 ADR 拍板）

**选项 X · DecisionInput 加 `risk_signals: tuple[RiskSignal, ...]` 字段 + DecisionPolicy 按 source 路由**

- 改动：decision_contract.py（白名单 +1）、decision_policy.py（加 source 分流 + audio_routing_table）
- 优点：最小改动，语义清晰（RiskSignal 是一等公民）
- 缺点：DecisionPolicy 决策路径变 2 条，组合逻辑要新设计

**选项 Y · 保留 PerceptionEvent path，但 DecisionInput 加 `meta.modality_context` 携带 RiskSignal 上下文 + DecisionPolicy 看 meta 分流**

- 改动：decision_contract.py（meta 字段）、decision_policy.py（meta 解析）、signal_adapter.py（保留翻译，但加 modality_meta 透传）
- 优点：不破坏既有 DecisionInput trigger_events 字段
- 缺点：meta 是 dict，类型安全弱；audio→PerceptionEvent 仍是翻译（违背"接通而非翻译"原则）

**选项 Z · 引入 ModalityRouter 中间层，独立组件，分流 RiskSignal 到对应 routing_table，组合输出统一 DecisionInput**

- 改动：新增 `analysis/modality_router.py`、修改 decision_policy.py（按 router 输出路由）、pipeline.py（process_frame 在 _act_on_signals 前调用 router）
- 优点：分层清晰（router / policy / engine 三层各司其职）；VISION+AUDIO 组合逻辑独立
- 缺点：新组件，需 ADR 化

**Owner 倾向**：选项 X（最简单、最贴合"接通而非重新发明"原则）。

---

## 6. Owner 三问答案（ADR 前审查）

### A. Audio→Risk 到底应该从哪里进入 Live Runtime？

**当前架构事实**：不存在 Live Runtime 入口（`_feed_live_audio` 只做投影；`process_audio_session` 是异步收割入口）。

**推荐方案**：在 `pipeline.process_frame` 加 audio 入参（接收 `list[AudioPerceptionEvent]` 当帧切片），新增 `_act_on_audio_signals(events, now)` 方法，与现有 `_act_on_signals(risk_signals, now)`（视觉）平行。具体步骤：

```
process_frame(frame, frame_index=..., audio_events=None)
    ↓
    # Stage B/C（视觉）— 已存在
    risk_signals = self._realtime_evaluator.evaluate(ctxs, now)

    # Stage D（视觉）— 已存在
    if self._decision_enabled and risk_signals:
        ...

    # NEW: Stage D（音频）— 新增
    if self._decision_enabled and audio_events:
        audio_risk_signals = [adapt_audio_event(ev, ...) for ev in audio_events]
        # 直接进 DecisionEngine（不再走 signal_adapter 翻译）
        audio_percs = []  # audio 不再转 PerceptionEvent
        # OR 保留 PerceptionEvent 翻译但 modality 标记
        ...
        audio_warnings = self._decision_engine.evaluate(audio_percs)
        if audio_warnings:
            ...
```

**gateway.run_loop 改动**：每帧循环里调 `pipeline.process_frame(frame, frame_index=k, audio_events=[...])`（切片由组装层提供）。

### B. Audio RiskSignal 如何进入 DecisionEngine？

**两条可选路径**：

**B1 · 直通路径**（推荐，选项 X）：
```
RiskSignal(source=AUDIO, category=COMMUNICATION, ...)
    ↓
DecisionEngine 接收 RiskSignal 直接评估
    ↓
DecisionPolicy 按 (source, category) 路由
    ↓
Warning(risk_level=LOW/HIGH, recommended_action=NOTIFY_FAMILY/...)
```

**B2 · 翻译路径**（保留，但补 modality）：
```
RiskSignal(source=AUDIO)
    ↓
risk_signal_to_perception(sig) + meta.modality="AUDIO"
    ↓
PerceptionEvent(event_type="audio_telephone_persistent", meta.modality="AUDIO")
    ↓
DecisionEngine 接收 PerceptionEvent，按 meta.modality 分流
```

B1 更直接、不破坏 audio→PerceptionEvent→DecisionEngine 既有路径；B2 改动小但留有翻译语义负担。

### C. telephone_risk 的 Decision Contract 到底是什么？

> 产品/规则契约必须由 Owner 确定，Agent 不能猜。

**当前决策产出**（基于 §5.3 翻译路径）：

| audio_kind | 当前 event_type | 当前 risk_level | 当前 recommended_action |
|---|---|---|---|
| audio_speech_rapid | visit_pending_verify（兜底） | LOW | MONITOR |
| audio_voice_raised | visit_pending_verify | LOW | MONITOR |
| audio_telephone_persistent | visit_pending_verify | LOW | MONITOR |
| audio_distress_cry | visit_pending_verify | LOW | MONITOR |
| audio_anomaly_other | visit_pending_verify | LOW | MONITOR |

**统一 LOW/MONITOR 不通知家属、不升级社区** —— 这与"telephone_risk"作为诈骗风险场景的产品意图不符。

**建议 Owner 拍板的 contract（示例，需 Owner review）**：

| audio_kind + audio_score | risk_level | recommended_action | 说明 |
|---|---|---|---|
| audio_distress_cry + score ≥ 0.7 | HIGH | ESCALATE_COMMUNITY | 哭腔+高分立即升级社区 |
| audio_distress_cry + score ≥ 0.4 | MEDIUM | NOTIFY_FAMILY | 中分通知家属 |
| audio_voice_raised + 持续 60s+ | MEDIUM | NOTIFY_FAMILY | 高声争吵 |
| audio_telephone_persistent + score ≥ 0.5 | MEDIUM | NOTIFY_FAMILY | 持续通话，疑似电话诈骗 |
| audio_telephone_persistent + is_odd_hour | HIGH | ESCALATE_COMMUNITY | 异常时段电话，最危险 |
| audio_speech_rapid + 持续 60s+ | LOW | MONITOR | 急促语言，仅记录 |
| audio_anomaly_other | LOW | MONITOR | 其他 |

**这套 contract 必须由 Owner 在 ADR 中定稿**——不能由 Agent 推断。

---

## 7. 修复推进顺序（按 Owner 8 步）

| Step | 状态 | 备注 |
|---|---|---|
| Step 1 解析 2 CLEARED payload | ✅ 完成 | 本报告 §2 |
| Step 2 确认 Audio→Risk Live Runtime 入口 | ✅ 完成 | 本报告 §3（推荐方案：在 process_frame 加 audio_events 入参） |
| Step 3 确认 memory 是否错误成为实时风险链依赖 | ✅ 完成 | 本报告 §4（确认错耦合；建议拆 AudioRiskDispatcher / EpisodicMemoryWriter） |
| Step 4 确认 Audio RiskSignal → Decision contract | ✅ 完成 | 本报告 §5（当前 contract 偏视觉；推荐选项 X = DecisionInput 加 risk_signals 字段） |
| Step 5 写 ADR / Contract | ⏳ 待 Owner 拍板 | §6 三个 ADR 前审查问题答案需 Owner 确认；audio decision routing_table 需 Owner 拍板 |
| Step 6 实现 Runtime 接通 | ⏳ 待 ADR | (a) Live frame loop 加 audio 入口 (b) AudioRiskDispatcher 拆开 (c) DecisionInput 加 risk_signals |
| Step 7 Browser E2E | ⏳ 待实现 | 6 个 Gate（Runtime Presence / Perception / Risk / Decision-Action / Product Story / Trust-Verify） |
| Step 8 修 audio DOM / risk card / risk signal UI | ⏳ 待实现 | 等 Runtime 接通后再做 UI |

---

## 8. 不动代码承诺

**本报告不动一行代码**。所有修复建议等 Owner 拍板 §6 三个 ADR 前审查问题答案后，再走 ADR → branch → PR 流程：

1. Owner 确认 Live Runtime 入口方案（在 process_frame 加 audio_events 入参 / 还是新增 _audio_loop task）
2. Owner 确认 audio RiskSignal 进 DecisionEngine 路径（直通 B1 / 翻译 B2）
3. Owner 拍板 telephone_risk decision contract（audio_kind × audio_score → risk_level × recommended_action 完整映射表）
4. Owner 决定是否拆 AudioRiskDispatcher / EpisodicMemoryWriter（涉及 ADR-0027 D3 决策门槛修改）

---

## 9. 相关文件

- `src/silver_demo/gateway.py:73-78` `live_audio_builder` 钩子
- `src/silver_demo/gateway.py:253` `_feed_live_audio(acc)` 调用点
- `src/silver_demo/gateway.py:364-374` `set_live_audio_events`
- `src/silver_demo/gateway.py:376-398` `_feed_live_audio` 实现
- `src/silver_demo/gateway.py:417-428` `_ensure_live_accumulator`
- `src/silver_demo/gateway.py:520-538` `_apply_demo_memory_overrides` ← **关键修正点**
- `src/home_perception/runtime/pipeline.py:265-405` `__init__` 装配
- `src/home_perception/runtime/pipeline.py:425-637` `from_settings` 装配
- `src/home_perception/runtime/pipeline.py:487` `realtime_enabled = settings.realtime_risk.enabled or settings.memory.enabled`
- `src/home_perception/runtime/pipeline.py:582-609` `audio_recorder` 装配条件
- `src/home_perception/runtime/pipeline.py:798-844` Stage B/C/D 视觉旁路
- `src/home_perception/runtime/pipeline.py:890-941` `_act_on_signals` 视觉 Stage D
- `src/home_perception/runtime/pipeline.py:943-978` `process_audio_session` 独立 Audio Loop
- `src/home_perception/runtime/audio_session_recorder.py:125-295` `record_session` D3 门槛
- `src/home_perception/analysis/realtime_risk_evaluator.py:91-220` `RealTimeRiskEvaluator.evaluate` 视觉侧
- `src/home_perception/analysis/realtime_risk_evaluator.py:202-208` `_emit_cleared_missing` 兜底
- `src/home_perception/analysis/signal_adapter.py:32-150` `risk_signal_to_perception` 翻译器（audio 落兜底）
- `src/home_perception/analysis/decision_contract.py:159-204` `DecisionInput` 白名单
- `src/home_perception/analysis/decision_policy.py:120-126` `DEFAULT_ROUTING_TABLE` 视觉 5 类
- `src/home_perception/analysis/risk_signal.py:137-229` `RiskSignal` 字段（source=AUDIO/VISION/SENSOR）
- `src/home_perception/integration/audio_adapter.py:30-83` `adapt_audio_event`
- `src/home_perception/integration/loop/runner.py:380-489` `process_audio_session` 调用方（integration test runner）

---

## 10. 关联文档

- `docs/reports/RUNTIME-RISK-ROOT-CAUSE-AUDIT-2026-08-22.md`（原报告，Layer 4 判定错误已被本报告修正）
- `docs/reports/LIVE-PRODUCT-STATE-AND-GAP-REPORT-2026-08-22.md`（产品现状与设计差距）
- `docs/ADR/0014-freeze-governance-three-levels.md`（冻结架构）
- `docs/ADR/0021-realtime-riskstream-concrete-design.md`（实时风险流）
- `docs/ADR/0026-audio-perception-chain-concrete-design.md`（音频感知链）
- `docs/ADR/0027-audio-runtime-wiring.md`（音频运行时接线 — 含 D3 决策门槛）
- `docs/ADR/0030-decision-boundary-contract.md`（决策边界契约）

---

> **审计修正完成 + Step 2-4 ADR 前审查完成。等待 Owner 拍板 3 个 ADR 前审查问题答案后，再启动 ADR / PR 流程。**