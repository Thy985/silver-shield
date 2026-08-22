# ADR Preflight Review · 2026-08-22

> **Review 目的**：Owner 拍板 3 个核心 Contract 前的最后一道审查。
> **Review 范围**：不动代码；基于代码证据回答 Owner 给出的 5 个 ADR Preflight 问题，
> 每题给出候选方案对比、推荐方案、证据链、ADR 文本草稿。
> **上游输入**：
> - `RUNTIME-RISK-ROOT-CAUSE-AUDIT-2026-08-22.md`（原审计，Layer 4 判定已被本系列修正）
> - `AUDIO-RISK-RUNTIME-AUDIT-CORRECTION-2026-08-22.md`（修正 + Step 1-4 ADR 前审查）
> - `LIVE-PRODUCT-STATE-AND-GAP-REPORT-2026-08-22.md`（产品现状与设计差距）

---

## 0. Owner 已锁死的约束（本 Review 的边界）

### 0.1 推进顺序（不能反过来）

```
ADR / Contract   ← 本 Review 属于此步
    ↓
Audio Runtime Entry
    ↓
RiskSignal → Decision
    ↓
Decision → Action
    ↓
Evidence Projection
    ↓
Browser E2E
    ↓
最后才修 Audio DOM / Risk Card / Narrative
```

### 0.2 禁令（违反即返工）

1. ❌ 不把 audio 硬翻译成视觉 `event_type`（如 `audio_telephone_persistent → repeat_visit`）——违反"Runtime Fact → 产品事实"铁律；
2. ❌ 不通过 `signal_adapter` 翻译 audio RiskSignal；
3. ❌ YAMNet `class_map_path=""` 修复前，不得让 audio RiskSignal 直接驱动高等级决策（当前 9 个事件全 fallback 成 `audio_distress_cry`，会形成"运行时事实 → 产品幻觉"）；
4. ❌ 不立刻把 RiskSignal 塞给现有 `RuleBasedDecisionPolicy`——先明确"可决策事实"规范化层；
5. ❌ Projection 必须区分"当前状态"与"事件历史"，不能用同一种覆盖语义。

### 0.3 产品哲学（Evidence Continuity > Event Count）

多模态价值靠 **Evidence Synthesis**（Vision + Audio RiskSignal + temporal overlap → Combined Risk），
不是两个独立 warning。telephone_risk 升级路径必须体现证据强度分级，不能"单事件一响就升级"。

---

## 1. Review 摘要表

| # | 问题 | 推荐方案 | 风险等级 | Owner 决策点 |
|---|---|---|---|---|
| Q1 | RuntimeFrameContext 放在哪里？ | Option B：升级 `process_frame(RuntimeFrameContext)` | 🟢 低 | 确认数据类字段签名 |
| Q2 | Audio RiskSignal 是否直接成为 DecisionInput 一等输入？ | Option C：新增 `risk_signals` 字段 + ADR-0030 C7 上限修订到 6 | 🟡 中 | 是否接受 C7 上限突破 |
| Q3 | Vision + Audio RiskSignal 时间关联窗口是多少？ | Option B：同 frame_index 配对 + case_time 伪时戳统一 | 🟡 中 | 时钟域对齐方式 + 默认窗口 |
| Q4 | telephone_risk 风险升级需要单音频还是多证据？ | Option C：分证据强度 5 档（insufficient / monitor / raise / notify / escalate） | 🟡 中 | 是否新建 RealTimeAudioRiskEvaluator |
| Q5 | RiskSignal 在 Projection 中是当前状态还是事件历史？ | Option A：双轨并存（覆盖式 `_last_risk_signals` + 累积式 `_risk_signal_history`） | 🟢 低 | 字段命名 |

**总体结论**：5 个问题均有充分代码证据支撑推荐方案。Q1 / Q5 是低风险工程决策（可由 AI 直接起草 ADR）；Q2 / Q3 / Q4 涉及现有契约修订（ADR-0030 C7）或新增模块（RealTimeAudioRiskEvaluator），必须 Owner 拍板后才能进入实现。

---

## 2. Q1 · RuntimeFrameContext 应该放在哪里？

### 2.1 现状取证

| # | 事实 | 位置 |
|---|---|---|
| F1-1 | `process_frame(frame, frame_index)` 当前签名无 audio 入参 | `src/silver_demo/gateway.py:242` |
| F1-2 | `FrameResult` 已是多模态结果容器，含 `risk_signals: list[RiskSignal]` | `src/home_perception/runtime/pipeline.py:145-179, 166` |
| F1-3 | `_feed_live_audio` 只做 evidence 投影，完全不进 risk 链 | `src/silver_demo/gateway.py:376-398` |
| F1-4 | `adapt_audio_event` 桥接已就绪（audio → RiskSignal），runtime 入口未通电 | `src/home_perception/integration/audio_adapter.py:30-83` |
| F1-5 | `process_frame` 调用点全仓只有 1 处 | grep 全仓验证 |

### 2.2 候选方案

#### Option A：加 `audio_events` 关键字参数（最小侵入）

```python
def process_frame(
    self,
    frame,
    frame_index: int,
    audio_events: tuple[AudioPerceptionEvent, ...] = (),
) -> FrameResult:
```

**优点**：改动最小，向后兼容（默认空元组）。
**缺点**：未来加 thermal / door_sensor / imu 每次都要改签名 → 违反开闭原则（OCP）；
参数列表会随传感器数量线性增长，与 AGENTS.md §1.2 "一个类一个职责"相悖。

#### Option B：升级为 `process_frame(RuntimeFrameContext)` ⭐ 推荐

```python
@dataclass(frozen=True)
class RuntimeFrameContext:
    """单帧 runtime 进给容器（对称于 FrameResult 输出容器）。"""
    video_frame: Any                       # np.ndarray 或 None（纯音频帧）
    frame_index: int
    case_time: float                       # frame_index * frame_interval_s
    audio_events: tuple[AudioPerceptionEvent, ...] = ()
    # 未来扩展位（Phase 后）：thermal_events / door_sensor_events / imu_events


def process_frame(self, ctx: RuntimeFrameContext) -> FrameResult:
    ...
```

**优点**：
- 与 `FrameResult` 对称设计（in dataclass / out dataclass），架构一致性强；
- OCP：新模态加字段不改方法签名；
- 调用点只有 1 处（gateway.py:242），一次性改完成本最低；
- `case_time` 显式入参，消除 gateway 内部三处重复计算（gateway.py:274, 314, 348）；
- 为 Q3 时钟域统一预留了唯一入口。

**缺点**：调用方需要构造 dataclass（1 处改动，可控）。

#### Option C：保持原签名 + 新增 `process_frame_with_audio`

**拒绝理由**：双入口导致调用方混乱，违反"Pipeline 单一编排"原则；
未来每加一种模态就要再加一个变体方法，组合爆炸。

#### Option D：内部缓冲队列（Gateway 缓存 audio，下次 process_frame 自动消费）

**拒绝理由**：时序错配不可定位（"这条 audio 到底和哪一帧配对？"），
破坏确定性测试（VM-8 重放幂等依赖显式输入）。

### 2.3 推荐方案：Option B

### 2.4 证据链

- F1-2 表明 `FrameResult` 已经承担了"多模态输出容器"职责——`risk_signals` 字段已预留，
  说明 runtime 层早已按"dataclass 进出"的思路设计，只是入口侧未对齐；
- F1-4 表明 `adapt_audio_event` 桥接已就绪，缺的只是把 audio events 送进 pipeline 的通道；
- F1-5 表明调用点唯一，Option B 的迁移成本 = 一次 gateway 改造；
- `audio_recorder` 强耦合 memory（pipeline.py:582-609）在 Step 3 已确认为错耦合，
  解耦后 `process_frame` 自然需要独立接收 `audio_events`——这正是 Option B 提供的扩展位。

### 2.5 ADR 文本草稿（ADR-0036 Proposed）

> **标题**：ADR-0036 · Runtime Entry Contract（RuntimeFrameContext 单容器进给）
>
> **背景**：当前 `Pipeline.process_frame(frame, frame_index)` 仅接收视觉输入；
> 音频走独立的 `_feed_live_audio` 只做 evidence 投影，不进 risk 链（修正报告 Layer 2 判定）。
> 多模态扩展（thermal / door_sensor / imu）已在 roadmap 中，逐参扩签会违反 OCP。
>
> **决策**：引入 `RuntimeFrameContext` 冻结数据类作为 `process_frame` 唯一入参；
> `video_frame` 允许 None（纯音频帧场景）；`case_time` 显式化，消除 gateway 三处重复计算。
>
> **动机**：与 `FrameResult` 对称设计；OCP；单一调用点低成本迁移；为 Q3 时钟域统一提供唯一入口。
>
> **后果**：
> - 正向：多模态扩展零签名变更；测试可独立构造 ctx；确定性重放更稳；
> - 负向：1 处调用方改造；`RuntimeFrameContext` 成为新公共契约，需 schema 测试钉死字段集合。
>
> **替代方案**：Option A（关键字参数）/ Option C（双入口）/ Option D（缓冲队列）——均因
> OCP 违反 / 组合爆炸 / 确定性破坏被拒。
>
> **迁移步骤**：
> 1. 新增 `core/runtime_context.py` 定义 `RuntimeFrameContext`（frozen dataclass + schema 测试）;
> 2. `Pipeline.process_frame(ctx)` 新签名落地，旧签名保留 1 个版本作 deprecated 别名;
> 3. gateway.py:242 改为构造 ctx;删除三处 case_time 重复计算;
> 4. 回归测试：2295 全绿 + 新增 RuntimeFrameContext schema 测试。
---

## 3. Q2 · Audio RiskSignal 是否直接成为 DecisionInput 一等输入？

### 3.1 现状取证

| # | 事实 | 位置 |
|---|---|---|
| F2-1 | `DecisionInput` 白名单 5 字段：`trigger_events / decision_context / reasoning_input / reasoning_result / prior_warning` | `analysis/decision_contract.py:66-74` |
| F2-2 | C1 黑名单禁止内嵌决策语义字段：`risk_score / score / decision / verdict / warning / risk_level / recommended_action` | `decision_contract.py:49-59` |
| F2-3 | C7 一级聚合约束：字段持续增长须先抽象为 Bundle，防 God Object；**导入期 fail-closed 断言** | `decision_contract.py:61-65, 311-328` |
| F2-4 | `signal_adapter._map_features_to_event` 仅识别 dwell / visits / odd_hour 三类视觉特征 | `signal_adapter.py:106-150` |
| F2-5 | audio RiskSignal 进 signal_adapter 会落兜底 `visit_pending_verify, 0.5`（不识别 audio_kind） | `signal_adapter.py:149-150` |
| F2-6 | audio RiskSignal features 含 `audio_kind / audio_score / audio_confidence / audio_tier1_max_score` 等，无决策语义字段（过 C1） | `integration/audio_adapter.py:58-69` |

**关键判定**：F2-5 直接证明——**把 audio RiskSignal 喂给现有 signal_adapter 是死路**，
会产出"运行时事实 → 产品幻觉"（Owner 禁令 §0.2.1/0.2.3）。audio 必须以 RiskSignal 原生形态进入决策层。

### 3.2 候选方案

#### Option A：直接加 `risk_signals: tuple[RiskSignal, ...]` 字段

**优点**：最直接表达"实时风险信号是一等输入"。
**问题**：
- 字段数 5 → 6，触碰 C7 上限但未超；
- `RiskSignal.features` 内含 `audio_score` 等评分字段——虽不在 C1 黑名单字面内
  （黑名单是顶层字段名，非嵌套 dict key），但语义上接近"score"，需要 ADR 明确豁免边界；
- 与 `trigger_events` 存在语义重叠风险（视觉 RiskSignal 经 signal_adapter 已能转 PerceptionEvent）。

#### Option B：抽象 `PerceptionBundle` 聚合 vision_events + audio_signals

**拒绝理由**：过度抽象。`trigger_events`（PerceptionEvent 数组）与 `risk_signals`
（RiskSignal 数组）是两个不同限界上下文的类型（ADR-0014 冻结 vs ADR-0021 Signal Layer），
强行聚合反而模糊了"感知触发事件"与"实时跃迁信号"的本质区别（ADR-0021 §3.3：
RiskSignal 是瞬时跃迁消息，不是感知事件）。

#### Option C：新增 `risk_signals` 字段 + 修订 ADR-0030 C7 上限到 6 ⭐ 推荐

```python
@dataclass(frozen=True)
class DecisionInput:
    trigger_events: tuple[PerceptionEvent, ...]
    decision_context: DecisionContext
    risk_signals: tuple[RiskSignal, ...] = ()     # 新增：一等输入（含 audio）
    reasoning_input: ReasoningInput | None = None
    reasoning_result: ReasoningResult | None = None
    prior_warning: WarningEvent | None = field(default=None)
```

配套约束（写入 ADR 修订案）：
1. **C7 白名单更新**：`DECISION_INPUT_FIELD_WHITELIST` 加入 `"risk_signals"`，上限从 5 → 6；
   **此后任何新增字段必须触发 Bundle 化**（硬顶，不再放宽）;
2. **C1 豁免声明**：`RiskSignal.features` 内的 `*_score` 类键属"证据强度描述"，不是决策语义；
   决策产物（risk_level / recommended_action）仍只能出现在 `WarningEvent`;
3. **规范化**：`risk_signals` 在 `__post_init__` 按 `(created_at, signal_id)` 升序稳定排序
   （对齐 C3 确定性要求）;
4. **空元组合法**：无信号时传 `()`，语义 = "本次决策无实时风险信号"（对齐 Memory 可缺席原则）;
5. **视觉旁路保留**：现有视觉 RiskSignal → signal_adapter → PerceptionEvent 链路不变
   （向后兼容），`risk_signals` 是**增量输入**，不是替代。

#### Option D：不动 DecisionInput，在 DecisionEngine 内部转换

**拒绝理由**：本质是把 audio RiskSignal 翻译成视觉事件再喂给旧 policy——正是 Owner
禁令 §0.2.1 明令禁止的路径（且 F2-5 证明 signal_adapter 不支持 audio_kind，翻译即幻觉）。
若绕开 signal_adapter 在 Engine 里写第二套翻译逻辑，则产生双口径漂移，更糟。

### 3.3 推荐方案：Option C

### 3.4 证据链

- F2-6 证明 RiskSignal 结构上可过 C1 黑名单（features 键名不含禁词），类型级引入无结构性障碍；
- F2-4/F2-5 证明 signal_adapter 不是 audio 的通路，唯一正道是一等输入；
- F2-3 证明 C7 有导入期 fail-closed 断言，**任何改动都会炸**——这恰好强制走 ADR 流程，
  符合 AGENTS.md §6.3.1 "跨模块 / 契约改动先提 ADR"；
- `RuleBasedDecisionPolicy` 升级为消费 `risk_signals` 属 **Slice B 后续演进**（ADR-0030
  docstring 已预告"签名演进见 Slice B"），本方案与既有演进路线一致。

### 3.5 ADR 文本草稿（ADR-0030 修订案 Proposed）

> **标题**：ADR-0030-r1 · DecisionInput 引入 risk_signals 一等输入（多模态决策契约）
>
> **背景**：音频链路已能产出 `RiskSignal(source=AUDIO)`（adapt_audio_event），
> 但 runtime 入口未通电 + DecisionInput 白名单未容纳。signal_adapter 只覆盖视觉特征映射
> （dwell/visits/odd_hour），audio 信号经其翻译必落兜底 `visit_pending_verify`，
> 形成"运行时事实 → 产品幻觉"。YAMNet class_map 缺失进一步放大该风险（9 事件全 fallback）。
>
> **决策**：
> 1. `DecisionInput` 新增 `risk_signals: tuple[RiskSignal, ...] = ()` 一等字段；
> 2. `DECISION_INPUT_FIELD_WHITELIST` 更新至 6 字段（**硬顶，此后新增字段必须 Bundle 化**）；
> 3. C1 豁免声明：RiskSignal.features 内 `*_score` 键属证据强度描述，非决策语义；
> 4. 构造期按 `(created_at, signal_id)` 稳定排序（C3 对齐）；空元组合法；
> 5. 视觉侧 signal_adapter 链路保留，`risk_signals` 为增量输入。
>
> **动机**：多模态 Evidence Synthesis 需要 Vision + Audio 信号同场进入决策层；
> 一等输入使 modality-aware routing（Q4）有结构基础；避免翻译层幻觉。
>
> **后果**：
> - 正向：audio RiskSignal 无损进入决策；modality-aware routing 可实现；
>   回放确定性由排序规范化保证；
> - 负向：C7 硬顶消耗 1 个名额；`RuleBasedDecisionPolicy` 必须升级（否则字段被忽略，
>   形成新的静默旁路）；schema 测试需同步更新白名单断言；
> - 风险控制：policy 未升级前，gateway 不接通 audio→risk 链（防止"加了字段没人消费"的假通电）。
>
> **替代方案**：Option A（无 C7 修订）/ Option B（Bundle 过度抽象）/ Option D（Engine 内翻译）
> ——分别因契约纪律缺失 / 限界上下文混淆 / 幻觉路径被拒。
>
> **实施前置条件**（Owner 拍板项）：
> - [ ] 接受 C7 上限 5 → 6 且声明硬顶；
> - [ ] 确认 `RuleBasedDecisionPolicy` 升级排期与本 ADR 同 PR 或紧随其后；

---

## 4. Q3 · Vision + Audio RiskSignal 的时间关联窗口是多少？

### 4.1 现状取证

| # | 事实 | 位置 |
|---|---|---|
| F3-1 | `AudioPerceptionEvent.timestamp` = **Unix 秒**（float） | `audio/event.py:113, 129` |
| F3-2 | 视觉侧 `case_time = frame_index * frame_interval_s`（demo 伪时钟） | `gateway.py:274, 314, 348` |
| F3-3 | **两个时钟域不同源**：audio 用真实墙钟，vision demo 用伪时戳 | F3-1 + F3-2 推论 |
| F3-4 | `CrossModalLinker._overlap_window` 输入是 `EpisodicRecord.enter_time/leave_time`（UTC datetime），**episode 级**非 signal 级 | `memory/cross_modal_link.py:384-393` |
| F3-5 | `CrossModalLinkRuntime.on_episode_recorded` 只在 **episode 落库后异步触发**，非按帧调用 | `memory/cross_modal_runtime.py:83-128` |
| F3-6 | `min_overlap_seconds` 是 episode 重叠门控（默认 0.0，几乎无门槛） | `cross_modal_link.py:318-332, 347`；`pipeline.py:588` |
| F3-7 | RiskSignal 自身只有 `created_at`（UTC datetime），**无时间窗概念**（瞬时跃迁消息） | `analysis/risk_signal.py:152, 173` |

**关键判定**：现有 CrossModalLink 体系（F3-4/F3-5/F3-6）是 **episode 级、异步、落库后**
的关联器——它回答"两条 Memory episode 是否同源"，**不回答"同一帧内 Vision 信号与 Audio
信号是否该合并为 Combined Risk"**。后者是 signal 级、同步、按帧的关联，需要新语义。

### 4.2 候选方案

#### Option A：复用 `CrossModalLinker._overlap_window`

**拒绝理由**：
- 输入类型不匹配：需要 enter/leave 时间窗，RiskSignal 是瞬时点（无窗口）；
- 触发时机不匹配：episode 落库后异步，无法支撑"同帧 Combined Risk"的实时决策；
- 若为 RiskSignal 伪造 enter/leave（如 created_at ± ε），是对 ADR-0021 语义的污染。

#### Option B：signal 级"同帧配对 + case_time 伪时戳统一" ⭐ 推荐

核心设计：
```python
# 1) 时钟域统一：audio Unix 秒 → case_time（相对 episode 起点的伪时戳）
def audio_unix_to_case_time(audio_ts: float, episode_start_unix: float) -> float:
    return audio_ts - episode_start_unix

# 2) 同帧配对（强关联，零阈值）
#    Vision RiskSignal.frame_index == 当前帧 → 与本帧内所有 audio signals 配对

# 3) 邻近窗口（弱关联，可配阈值）
def is_temporally_linked(vision_case_time: float, audio_case_time: float,
                         window_s: float = 2.0) -> bool:
    return abs(vision_case_time - audio_case_time) <= window_s
```

**默认窗口建议：2.0 秒**（= 8fps 下 16 帧跨度），理由：
- 人类对话/电话场景中，视觉动作（举手机、敲门）与声学事件（通话声、急促言语）
  的自然时滞在 0.5~2s 量级；
- 8fps 采样下，单帧间隔 0.125s，2s 窗口覆盖 16 帧，足以吸收抽帧抖动；
- 与 `min_overlap_seconds=0.0`（episode 级）不冲突——那是 Memory 落库语义，
  本窗口是 signal 级决策语义，两层独立演进。

**优点**：不污染 ADR-0021 类型；不依赖 Memory 落库时机；同帧强关联 + 邻近弱关联分级，
为 Q4 证据强度分级提供时间维度输入。

**缺点**：需要新增一个 signal 级关联组件（建议命名 `SignalTemporalLinker`，纯函数、
零状态，放 `analysis/` 层）；`episode_start_unix` 需要由 runtime 在会话启动时锚定一次。

#### Option C：把 RiskSignal 转成 EpisodicRecord 走现有链路

**拒绝理由**：RiskSignal 是瞬时跃迁消息（ADR-0021 §3.3），EpisodicRecord 是持久化
episode（ADR-0024）——把信号伪装成 episode 是限界上下文污染，且会触发 Memory 落库副作用。

### 4.3 推荐方案：Option B

### 4.4 证据链

- F3-3 是本问题的**根因**：不统一时钟域，任何窗口计算都是错的（Unix 秒 vs 伪时戳相减无意义）；
  Option B 的第一步就是锚定 `episode_start_unix` 统一时基；
- F3-4/F3-5 证明现有关联器在类型（episode vs signal）与时机（异步 vs 同帧）两个维度都不匹配；
- F3-7 证明 RiskSignal 只有 `created_at` 单点，窗口语义必须由**关联组件**外部赋予，
  而非类型内嵌——支持 Option B 的"纯函数 linker"设计；
- gateway.py:274/314/348 三处重复计算 case_time 的现状，将在 Q1 Option B 的
  `RuntimeFrameContext.case_time` 显式化后收敛为单一来源。

### 4.5 ADR 文本草稿（ADR-0037 Proposed）

> **标题**：ADR-0037 · Cross-Modal RiskSignal Temporal Alignment（signal 级时间对齐契约）
>
> **背景**：Vision 与 Audio RiskSignal 分属两个时钟域（demo 伪时戳 case_time vs Unix 秒）；
> 现有 CrossModalLinker 是 episode 级异步关联器，不支撑同帧 Combined Risk 决策。
> 多模态 Evidence Synthesis（Owner 产品哲学）要求 signal 级时间关联。
>
> **决策**：
> 1. 新增 `SignalTemporalLinker`（`analysis/` 层纯函数组件，零状态、可单测）：
>    - 同帧配对：同 `frame_index` 的 Vision/Audio 信号视为强关联（零阈值）；
>    - 邻近窗口：`|case_time_v - case_time_a| <= window_s`（默认 **2.0s**）视为弱关联；
> 2. 时钟统一：runtime 会话启动时锚定 `episode_start_unix`；
>    audio Unix 秒统一换算为 `case_time = audio_ts - episode_start_unix`；
> 3. 产物：`LinkedSignalPair(vision_signal, audio_signal, link_strength, delta_case_time)`；
>    link_strength ∈ {SAME_FRAME, NEAR_WINDOW}；
> 4. 与 episode 级 CrossModalLinker **职责分离**：后者仍只管 Memory 落库建边，
>    前者只管实时决策输入组装；两者不共享代码、不共享配置。
>
> **动机**：Combined Risk 需要同帧/近邻的多模态信号合并；时钟域统一是一切窗口计算的
> 前置条件；纯函数设计保证 VM-8 重放确定性。
>
> **后果**：
> - 正向：Q4 证据强度分级获得时间维度；窗口阈值可配置化（灰度调参不动架构）；
> - 负向：新增 1 个组件 + 1 个配置项（`signal_temporal_window_s`，默认 2.0）；
>   `episode_start_unix` 锚定逻辑需要处理会话重启（建议随 RuntimeFrameContext 首帧锚定）；
> - 风险控制：窗口默认值写入 config 并有契约测试钉死；调整走配置不改代码。
>
> **替代方案**：Option A（复用 episode 关联器）/ Option C（RiskSignal 伪装 episode）
> ——分别因类型/时机不匹配与限界上下文污染被拒。
>
> **Owner 拍板项**：
> - [ ] 默认窗口 2.0s 是否接受（或指定其他值）；
> - [ ] 同帧配对是否视为强关联（影响 Q4 证据分级中的 `verified multi-evidence chain` 判定）。

---

## 5. Q4 · telephone_risk 的风险升级需要"单音频证据"还是"多证据"？

### 5.1 现状取证

| # | 事实 | 位置 |
|---|---|---|
| F4-1 | `AudioPerceptionKind` 5 类：`audio_speech_rapid / audio_voice_raised / audio_telephone_persistent / audio_distress_cry / audio_anomaly_other` | `audio/event.py:28-38` |
| F4-2 | YAMNet `class_map_path=""` → **9 个真实事件全 fallback 成 `audio_distress_cry`** | `config/live_audio.yaml:139`；修正报告 Step 1 取证 |
| F4-3 | audio RiskSignal features 含 `audio_kind / audio_score / audio_confidence / audio_tier1_max_score / audio_tier1_scored_labels / source_segment_ids / labels`——**无持续时长字段** | `integration/audio_adapter.py:58-69` |
| F4-4 | `RealTimeRiskEvaluator` 仅支持视觉 visitor subject；`_emit_cleared_missing` 兜底语义 = "主体离场" | `analysis/realtime_risk_evaluator.py:202-208` |
| F4-5 | `AudioSessionRecorder.record_session` 是 session 级（会话内音频事件累积），但产物进 Memory，**不产 RiskSignal** | `runtime/audio_session_recorder.py:125-295` |
| F4-6 | `adapt_audio_event` 固定产出 `transition=RAISED`，**无 CLEARED 路径、无状态机** | `audio_adapter.py:71-83` |

**关键判定**：当前音频链路是"单事件单信号"模型（F4-3/F4-6）——每个 AudioPerceptionEvent
直接翻译成一个 RAISED RiskSignal，没有持续时长、没有 CLEARED、没有跨事件聚合。
若在此模型上直接做"telephone_risk 升级"，任何一次 fallback 事件都会触发升级，
而 F4-2 表明 fallback 是**当前常态**——必然形成"运行时事实 → 产品幻觉"。

### 5.2 候选方案

#### Option A：单音频证据 → 直接 raise

**拒绝理由**：
- F4-2：class_map 缺失期，单事件不可信（9/9 全 fallback）；
- 单事件噪声敏感：电话铃一声、电视一句台词都可能误触；
- 违反 Owner 产品哲学"Evidence Continuity > Event Count"。

#### Option B：持续时长 → raise（同 kind 连续 N 秒未解除）

**部分采纳**：方向正确（持续性是 telephone_risk 的核心特征——"异常/持续通话"
的 kind 语义本身就含时间维度），但**单独不足**：
- F4-6：现模型无状态机、无 CLEARED 路径，"持续 N 秒"无从判定；
- F4-4：`RealTimeRiskEvaluator` 的 subject 状态机绑定视觉 visitor（离场即清空），
  音频没有"离场"概念，硬套会产生错误的 CLEARED。

#### Option C：分证据强度 5 档 ⭐ 推荐（含 Option B 的持续性维度）

```python
class EvidenceStrength(str, Enum):
    INSUFFICIENT = "insufficient"   # 证据不足 → 不产信号（静默）
    MONITOR       = "monitor"       # 单次弱信号 → 仅记录观察
    RAISE         = "raise"         # 持续信号 → 升起本地风险（RAISED signal）
    NOTIFY        = "notify"        # 多独立信号 → 通知家属
    ESCALATE      = "escalate"      # 多模态验证链 → 升级中心
```

判定规则（候选阈值，Owner 可调）：

| 档位 | 判定条件 | 前置门控 |
|---|---|---|
| INSUFFICIENT | `confidence < 0.5` 或 `score < 0.3` | — |
| MONITOR | 单次事件，`score ≥ 0.3` 且 `confidence ≥ 0.5` | 过 INSUFFICIENT 门 |
| RAISE | **同 `audio_kind` 在窗口内累计 ≥ N 次**（建议 N=3）或持续 ≥ T 秒（建议 T=10s） | class_map 已修复（见前置门控说明） |
| NOTIFY | ≥ 2 种**不同独立** audio_kind 同时活跃（如 persistent + distress_cry） | 各自过 RAISE 门 |
| ESCALATE | Vision + Audio 信号经 SignalTemporalLinker（Q3）构成 SAME_FRAME 或 NEAR_WINDOW 关联 | 双方各自过 MONITOR 门 |

**前置门控（硬性）**：
1. **class_map 修复前**：所有音频证据强度封顶 MONITOR——fallback 事件永不驱动 RAISE 及以上
   （直接封死 §0.2.3 幻觉路径）；修复后由配置开关放开；
2. RAISE/NOTIFY 需要真实 Tier1 标签支撑（`audio_tier1_max_score > 0`），
   纯规则 score 不足以升级；
3. ESCALATE 必须经 Q3 时间关联验证，不接受两个孤立 warning 的伪合成。

### 5.3 推荐方案：Option C

### 5.4 实现载体：新建 RealTimeAudioRiskEvaluator（不扩展现有评估器）

**理由**：
- F4-4：现有评估器的 subject 生命周期（visitor 进出场）与音频主体（声学会话）语义不同，
  硬扩展会把两套状态机耦合在一个类里，违反 AGENTS.md §1.2 单一职责；
- F4-6：音频需要自己的 RAISED/CLEARED 状态机（CLEARED = 同 kind 会话静默超时，
  而非"主体离场"）；
- 新组件放 `analysis/realtime_audio_risk_evaluator.py`，与现有评估器平行，
  共享 `RiskSignal` 类型但不共享实例状态。

### 5.5 证据链

- F4-2 是本问题的**决定性约束**：9/9 fallback 意味着"单音频→升级"在当前数据质量下
  100% 产生幻觉，必须被前置门控封死；
- F4-3/F4-6 证明现模型缺持续时长与状态机，Option B 无法独立成立；
- F4-5 证明 session 级累积已有载体（AudioSessionRecorder），但其产物走 Memory 不走
  决策——RealTimeAudioRiskEvaluator 可复用其"窗口内事件累积"思想但独立实现
  （决策路径不能依赖 Memory 落库时机，否则引入异步不确定性）;
- 5 档分级与 Owner 给出的 Contract 方向逐档对齐
  （insufficient / single weak / persistent / multiple independent / verified multi-evidence chain）。

### 5.6 ADR 文本草稿（ADR-0038 Proposed）

> **标题**：ADR-0038 · Audio Evidence Strength Grading（telephone_risk 分级升级契约）
>
> **背景**：音频链路当前为"单事件单信号"模型，YAMNet class_map 缺失导致全部事件
> fallback 为 `audio_distress_cry`。在此数据质量下任何"单事件→高等级动作"的映射
> 都会形成运行时事实到产品判定的幻觉跃迁。
>
> **决策**：
> 1. 引入 `EvidenceStrength` 五档：INSUFFICIENT / MONITOR / RAISE / NOTIFY / ESCALATE；
> 2. 新建 `RealTimeAudioRiskEvaluator`（`analysis/` 层，独立于现有视觉评估器）：
>    维护同 kind 会话窗口，产出带状态机的 RAISED/CLEARED 信号对；
>    CLEARED 语义 = 同 kind 静默超时；
> 3. **class_map 前置门控**：修复前所有音频证据强度封顶 MONITOR；
>    修复后经配置开关分级放开；
> 4. RAISE 及以上需 Tier1 真实标签支撑；ESCALATE 必须经 signal 级时间关联（ADR-0037）验证；
> 5. `DecisionPolicy` 升级为 modality-aware routing：按 evidence_strength 与
>    source modality 组合路由 action。
>
> **动机**：Evidence Continuity > Event Count；持续性是 telephone_risk 的核心特征；
> 数据质量门控防止幻觉升级。
>
> **后果**：
> - 正向：升级路径可解释、可审计、可灰度；幻觉路径被结构性封死；
> - 负向：新增 1 个评估器组件 + 配置面扩大（N/T/置信阈值/window_s 等 5+ 参数）；
>   class_map 修复成为 RAISE 放大的硬依赖（排期风险）；
> - 风险控制：所有阈值入 config + 契约测试钉死默认值；门控开关有独立测试。
>
> **替代方案**：Option A（单事件直升）/ Option B（仅持续性维度）——分别因幻觉风险
> 与状态机缺失被拒；Option B 的持续性维度已并入 Option C 的 RAISE 判定。
>
> **Owner 拍板项**：
> - [ ] 接受 5 档分级与默认阈值（N=3 次 / T=10s / confidence≥0.5 / score≥0.3）；
> - [ ] 确认新建 `RealTimeAudioRiskEvaluator`（而非扩展现有评估器）；
> - [ ] 确认 class_map 修复排期为 RAISE 放大的前置依赖。

---

## 6. Q5 · RiskSignal 在 Projection 中是"当前状态"还是"事件历史"？

### 6.1 现状取证

| # | 事实 | 位置 |
|---|---|---|
| F5-1 | `_last_risk_signals` 为**覆盖式**（每帧被最新值替换） | `visualizer/viewer/live_adapter.py:1012` |
| F5-2 | **无 RiskSignal 累积式历史字段**——信号被下一帧覆盖后前端不可见 | F5-1 推论；全类核查 |
| F5-3 | 累积式先例齐备：`_perception_events_cache / _warnings_cache / _audio_events` 均为持久列表 + 去重键 + seq 序号三件套 | `live_adapter.py:532, 562-567` |
| F5-4 | `risk_transition` 服务端状态机 4 态：raised / cleared / active / None，判定基于覆盖式 `_last_risk_levels` | `live_adapter.py:1054-1067` |
| F5-5 | PR-B 红线：服务端权威判定 transition，前端只渲染不推断 | `live_adapter.py:549-555` docstring |
| F5-6 | 实测后果：frame=0 的 RAISED 被 frame=1 的 CLEARED 覆盖 → 前端只见 CLEARED，**0 RAISED 存留**（"莫名其妙被解除"观感） | ws_payloads.jsonl 取证；修正报告 §2 |

**关键判定**：F5-6 正是 Owner 洞察的实证——单一覆盖语义使"RAISED 曾发生过"这一事实
在投影层丢失。但答案不是把覆盖式改成累积式，而是**双轨并存**：状态机判定需要
覆盖式（当前态），叙事渲染需要累积式（历史）。

### 6.2 候选方案

#### Option A：双轨并存 ⭐ 推荐

```python
# 覆盖式"当前状态"（保留现状）
self._last_risk_signals: tuple[dict, ...] = ()     # 驱动 risk_transition 状态机
self._risk_active: bool = False
self._last_risk_transition: str | None = None

# 累积式"事件历史"（新增，与 _warnings_cache 同构三件套）
self._risk_signal_history: list[dict] = []          # 完整对象持久化
self._risk_signal_ids: set[str] = set()             # signal_id 去重（VM-8 幂等）
self._risk_signal_seq: int = 0                      # delta 增量序号
```

delta payload 扩展：

```python
{
    "risk_signals": [...],            # 覆盖式（已有，不动）
    "risk_signal_history": [...],     # 累积式（新增）
    "risk_signal_history_seqs": [..], # 已推送 seq 集合指纹（增量判定）
}
```

**优点**：
- 与既有三件套模式（F5-3）完全同构，实现路径成熟；
- 状态机判定逻辑零改动（仍用覆盖式），符合 PR-B 红线（F5-5）；
- 浏览器侧 RAISED 卡可基于 history 渲染完整叙事（何时升起/何时解除/配对关系
  via `paired_signal_id`）；
- VM-8 重放幂等由 signal_id 去重保证。

**缺点**：delta payload 多 2 个字段（增量推送可控，仅新 seq 才推）。

#### Option B：只保留覆盖式 + 加 `risk_state` 字段

**拒绝理由**：把 ADR-0021 的"瞬时跃迁消息"与"长期状态"混为一谈——RiskSignal 类型
本身已声明"持续态由评估器内部状态机持有，本模块不持有"。在投影层再造一个 state 字段
会形成第二套风险状态口径，与服务端状态机漂移。

#### Option C：RiskSignal 历史并入 `_warnings_cache`

**拒绝理由**：RiskSignal ≠ WarningEvent——前者是事实层的瞬时信号（含 CLEARED），
后者是决策层的产物（只有正向告警）。混入会使 task-cards 数据源语义污染
（CLEARED 信号不是 warning，却出现在 warnings 列表里）。

### 6.3 推荐方案：Option A

### 6.4 证据链

- F5-3 是最强支撑：三处累积式先例证明该模式在本代码库已被验证（去重 + seq +
  持久化三件套），Option A 是模式复用而非新发明；
- F5-4/F5-5 证明覆盖式通道承担着服务端权威状态机职责，不可破坏——双轨而非替换；
- F5-6 提供实证：单覆盖语义直接造成本次审计发现的"0 RAISED 存留"缺陷；
- `paired_signal_id`（risk_signal.py:149）天然支持 history 内 RAISED↔CLEARED 配对渲染，
  累积式历史才能让这个字段发挥作用。

### 6.5 ADR 文本草稿（ADR-0035 修订案 Proposed）

> **标题**：ADR-0035-r1 · RiskSignal 投影双轨契约（覆盖式当前态 + 累积式历史）
>
> **背景**：`ProjectionAccumulator._last_risk_signals` 为覆盖式，信号被后续帧覆盖后
> 前端永久不可见。实测中 frame=0 的 RAISED 被 frame=1 的 CLEARED 覆盖，产生
> "风险莫名其妙被解除"的产品幻觉。同时 `paired_signal_id` 配对语义依赖历史可见性。
>
> **决策**：
> 1. 新增累积式三件套：`_risk_signal_history / _risk_signal_ids / _risk_signal_seq`
>    （与 `_warnings_cache` 同构；signal_id 主键去重保 VM-8 幂等）；
> 2. 覆盖式 `_last_risk_signals` 与 `risk_transition` 状态机**原样保留**
>    （PR-B 服务端权威判定不变）；
> 3. evidence_delta payload 新增 `risk_signal_history` 与 `risk_signal_history_seqs`
>    两字段，增量推送（仅新 seq）；
> 4. 浏览器侧渲染分工：CURRENT STATE 卡消费覆盖式；风险时间线/Narrative 消费累积式；
>    前端不做任何推断（红线不变）。
>
> **动机**：Evidence Continuity > Event Count 在投影层的落地；配对语义需要历史可见。
>
> **后果**：
> - 正向：RAISED→CLEARED 全生命周期可追溯；Narrative 有真实素材；
>   实现完全复用已验证的三件套模式；
> - 负向：payload 字段增加（增量推送缓解）；长会话下 history 无界增长
>   （与 `_audio_events` 同样的既有特性，如需上限另立配置项，不在本 ADR 范围）;
> - 风险控制：schema 测试钉死两轨字段集合；去重测试覆盖重放场景。
>
> **替代方案**：Option B（再造 state 字段）/ Option C（并入 warnings_cache）——
> 分别因双重状态口径与事实/决策语义混淆被拒。

---

## 7. Owner 决策点汇总

### 7.1 五个拍板项

| # | 决策点 | 推荐答案 | 影响 |
|---|---|---|---|
| D1 | Q1：确认 `RuntimeFrameContext` 数据类签名（frozen dataclass；video_frame 允许 None；case_time 显式化；预留多模态扩展位） | Option B | 新增公共契约 + gateway 单点改造 |
| D2 | Q2：接受 ADR-0030 C7 上限 5→6 且声明硬顶（此后新增字段必须 Bundle 化）；`RuleBasedDecisionPolicy` 升级与本 ADR 同期落地 | Option C | 契约修订 + policy 行为升级 |
| D3 | Q3：signal 级时间窗口默认 2.0s；同帧配对视为强关联（SAME_FRAME）；时钟域以 `episode_start_unix` 锚定统一 | Option B | 新增 SignalTemporalLinker 组件 |
| D4 | Q4：接受 5 档证据强度分级与默认阈值；新建 RealTimeAudioRiskEvaluator；class_map 修复为 RAISE 放大硬前置 | Option C | 新增评估器组件 + 配置面扩大 |
| D5 | Q5：RiskSignal 投影双轨制（覆盖式保留 + 累积式三件套新增）；delta payload 加 2 字段 | Option A | Projection 层扩展 |

### 7.2 隐含约束（拍板时须一并确认）

1. YAMNet class_map 修复前，音频证据强度封顶 MONITOR（ADR-0038 门控）——这是防幻觉的硬闸门；
2. audio RiskSignal 不进 `signal_adapter.risk_signal_to_perception` 翻译（保持旁路）；
3. `RuleBasedDecisionPolicy` 未升级消费 `risk_signals` 前，gateway 不接通 audio→risk 链
   （防止"加了字段没人消费"的假通电）；
4. demo 模式 `memory.enabled=True` 会强制 `realtime_enabled=True`（gateway.py:520-538），
   音频接通后 demo 行为会变化，需回归 P0-11 多角色闭环 Demo 的 12 项端到端验证。

### 7.3 拍板后的推进顺序（Owner 已锁死，重申）

```
1. ADR 落库（本 Review 5 个草稿按 Owner 意见修订后提交）
2. Audio Runtime Entry      ← Q1 实现（RuntimeFrameContext + process_frame 改造）
3. RiskSignal → Decision    ← Q2 实现（DecisionInput.risk_signals + policy 升级）
4. Decision → Action        ← Q4 实现（RealTimeAudioRiskEvaluator + modality-aware routing）
5. Evidence Projection      ← Q5 实现（双轨投影 + delta 扩展）
   （Q3 的 SignalTemporalLinker 在步骤 4 中作为 ESCALATE 判定前置件一并落地）
6. Browser E2E              ← 含 class_map 修复后的分级放大回归
7. Audio DOM / Risk Card / Narrative UI 打磨（最后）
```

### 7.4 本 Review 未覆盖、需另行处理的开放项

| 开放项 | 归属 |
|---|---|
| YAMNet class_map_path 修复与真实标签验证 | 独立任务（ADR-0038 门控解锁条件） |
| `_apply_demo_memory_overrides` 强制 memory.enabled 的 demo 行为稳定性 | 步骤 6 回归时评估 |
| `_risk_signal_history` 长会话无界增长的上限策略 | 可选配置项，非本期范围 |
| episode_start_unix 会话重启锚定细节 | Q1/Q3 实现时确定 |

---

## 8. Owner 拍板修订记录（2026-08-22）

Owner 审阅本 Review 后**未原样批准**，对 Q1–Q5 逐项给出收紧意见并拍板。
本节记录修订内容与落库结果，作为 5 个 ADR（0039–0043）的决策依据。

### 8.1 总体判断

> Q1 可以拍板；Q2 基本可以拍板；Q5 可以拍板。
> Q3 的"2 秒"目前证据不足，不应该直接冻结。
> Q4 的"五档 + N=3/T=10s"明显还是候选规则，不应该作为 ADR 默认事实冻结。

| ADR | Owner 判断 | 是否可定 | 落库结果 |
|---|---|---|---|
| RuntimeFrameContext | Option B | ✅ | ADR-0039 Accepted |
| DecisionInput.risk_signals | Option C | ✅ | ADR-0040 Accepted |
| SignalTemporalLinker | 必须有，但 2s 暂不冻结 | 🟡 | ADR-0041 Accepted（机制冻结/数值 TBD） |
| Audio Evidence Strength | 五档冻结，阈值暂不冻结 | 🟡 | ADR-0042 Accepted（等级冻结/参数 TBD） |
| RiskSignal Projection | 双轨 | ✅ | ADR-0043 Accepted |

### 8.2 逐项修订

#### Q1 → ADR-0039
- ✅ 采纳 Option B；
- **修订**：不预留 `thermal_events / imu_events / door_sensor_events` 占位字段——
  "Context 是扩展边界，不是无限字段垃圾桶"；第二种非音视频模态真进入 Runtime 时再走 ADR 扩展。

#### Q2 → ADR-0040
- ✅ 采纳 Option C（risk_signals 一等输入；signal_adapter 保留视觉兼容路径但不再作为
  Audio→Decision 的桥）；
- **修订 1**：C7 表述改为"当前冻结上限从 5 个字段**临时扩展**到 6 个；6 是硬顶"——
  不构成 5→6→7→8 的演进先例，防止重回 God Object；
- **修订 2**：明确 `risk_signals = Runtime RiskSignal 输入 ≠ Decision Result`；
  禁止 `RiskSignal.features` 逐渐塞入 `risk_level / recommended_action / verdict / decision`，
  否则 C1 重新失效。

#### Q3 → ADR-0041
- 🟡 **只冻结机制**（必须存在 signal-level temporal alignment），**不冻结默认 2.0s**；
- Owner 理由："人类对话 0.5~2s / 8fps→16 帧"是合理的工程初始值，不是代码审计能证明的事实；
  且当前 audio = Unix 墙钟、vision = DemoClock 伪时戳，两个时钟域下 Δt 计算无意义；
- 正确顺序：**先建立统一 Runtime 时钟语义 → 再从真实 telephone_risk 数据统计
  Δt distribution（Person ENTERED / Telephone detected / RMS change / Risk signal 之间）
  → 最后决定 same frame / ≤0.5s / ≤1.0s / ≤2.0s**；
- ADR 写法：`window_s = configurable, default = TBD by acceptance data`。

#### Q4 → ADR-0042
- 🟡 **五档冻结，参数不冻结**（"冻结语义，不冻结参数"）；
- 五档（INSUFFICIENT / MONITOR / RAISE / NOTIFY / ESCALATE）把"检测→风险→决策→行动"
  之间的证据强度显式化，Owner 赞成；
- N=3 / T=10s / score≥0.3 / confidence≥0.5 全部为候选参数——class_map_path="" 使
  9 个事件全 fallback，现有数据无法估计阈值；
- 参数确定流程不可跳步：ADR 冻结五档与门控原则 → 修 class_map → 真实 AudioKind 分布
  → TelephoneRisk E2E → 测 precision / recall / false escalation → 定参。

#### Q5 → ADR-0043
- ✅ 采纳双轨制（已被真实数据证明：frame0 RAISED 被 frame1 CLEARED 覆盖 → 只剩 CLEARED，
  破坏产品叙事）；与既有 CURRENT STATE / RECENT CHANGES / HISTORY 分层同构；
- **修订**：不提前冻结 `risk_signal_history / risk_signal_history_seqs` 字段名——
  payload 形状（如 `risk_delta ├─ current └─ recent_events[]`）留给实现设计；
  核心契约是"**Projection 必须同时支持状态和事件**"；底层仍冻结
  `idempotency key = signal_id`、`sequence = seq`。

### 8.3 深层修正：Q3 与 Q4 依赖反转

原 Review 将 Q3 / Q4 视为两个独立 ADR。Owner 指出正确依赖方向：

```
Temporal Alignment        ← Q3：证据之间如何建立关联
        ↓
Evidence Synthesis        ← ADR-0019 Evidence Fusion 的 Phase 1 落地
        ↓
Evidence Strength         ← Q4：关联后的证据有多强
        ↓
Decision
```

推荐架构（已写入 ADR-0041/0042）：

```
Vision RiskSignal ─────────┐
                           ↓
Audio RiskSignal ────────── SignalTemporalLinker
                           ↓
                 Evidence Synthesis
                           ↓
                  Evidence Strength
                           ↓
                     DecisionPolicy
                           ↓
                     Warning / Action
```

这比 AudioEvaluator / VisionEvaluator / DecisionPolicy 各自独立判断更符合
Evidence Continuity > Event Count 的产品哲学。

### 8.4 关键执行边界（MONITOR ceiling）

> 在 ADR-0042 的 class_map 修复前，不要让 telephone_risk 的 Audio RiskSignal 进入
> 真实高等级 Decision 路径。可以把链路先接通，但必须处于
> `Audio → RiskSignal → Evidence Synthesis → MONITOR ceiling`。
> 等 YAMNet 标签真实性验证通过，再解除 MONITOR ceiling。

避免"架构已经打通，但数据语义还是假的"。该门控与 ADR-0038 Live Runtime 已验证行为
（LOW → MONITOR → LOG_ONLY）一致。

### 8.5 拍板后的推进路径（重申）

```
ADR 落库（本节记录，ADR-0039 ~ 0043 已 Accepted）
    ↓
Q1 / Q2 / Q5 直接冻结 → Runtime 改造开始
Q3 冻结机制，不冻结数值（时钟统一先行）
Q4 冻结等级，不冻结参数（class_map 修复先行）
```

### 8.6 本节与正文的关系

正文 §2–§6 保留 Preflight Review 时的论证原貌（含被 Owner 修正的 2.0s 默认窗口、
N=3/T=10s 默认参数等表述），**以本节修订为准**。后续引用请以 ADR-0039 ~ 0043 为
最终事实来源。
