# ADR-0031: 决策审计血缘契约（Decision Audit Trace Contract）

- **Status**: In Review（Owner 评审中；Proposed 阶段由 Owner 评审，冻结后修订权属 Owner）
- **Date**: 2026-08-08
- **Owner**: SilverShield 技术负责人
- **Related**:
  - ADR-0030（决策边界契约·`DecisionInput` 唯一收敛载体；本 ADR 由其 §5.1 路线图直接派生）
  - ADR-0010（单一决策中心）/ ADR-0001（仅产事实不裁决）/ ADR-0002（隐私铁律）
  - ADR-0014（三级冻结治理·Level 1 Schema Contract 与 `meta` 逃生舱晋升条款）
  - ADR-0024（Memory 架构·I4 可解释性）/ ADR-0025（Memory Consumer·C1 不决策 / C5 溯源）
  - ADR-0027（音频记忆集成·D2 证据以 ID 引用）/ ADR-0028（跨模态运行时接线·D4 可选注入零行为变化）
  - ADR-0029（跨模态检索与解释·`CrossModalContext` 纯结构化事实）
- **Phase**: v2 · Phase 3 → 决策边界契约（ADR-0030）→ **决策审计血缘（Slice C 受控验证门的硬性前置）**

---

## 0. 背景与动机（Context）

> **引用约定（可维护性）**：本 ADR 引用代码以**符号名 + 简短引文**为准（行号会漂移，仅作辅助定位）。文中行号均截至 commit `45a6028`（ADR-0030 Slice A/B 代码合入 `main`，PR #156）。若行号与实际不符，以符号名与引文为准。

ADR-0030 已把决策入口收敛为 `DecisionInput`，并在 §5.1 把 ADR-0031 定位为 **Slice C（Memory → Decision Controlled Validation Gate）的硬性前置**：

> 「没有完整 trace 就无法做 `baseline`（perception-only）vs `candidate`（perception+memory）的可审计双轨比较。」

同时给出了草案骨架：

```
DecisionTrace = { decision_id, trigger_events_refs, reasoning_context_refs,
                  reasoning_result_ref, policy_version, chosen_action,
                  rejected_actions, timestamp }
```

**本 ADR 的第一项工作，是用代码实情校正这份草案。** 逐条核对后，草案有三处与代码冲突、一处遗漏了最关键的场景。

### 0.1 最需要审计的决策，恰恰是当前完全静默的

`RuleBasedDecisionPolicy.decide`（`analysis/decision_policy.py`）有 **三条相互独立的 `return None` 路径**：

| # | 位置（引文）                                          | 语义                              |
| - | ----------------------------------------------- | ------------------------------- |
| 1 | `if not perception_events: return None`         | 本周期无触发事件                        |
| 2 | `if not candidates: return None  # 全部是普通访问，无警告` | 全被 `visit_normal` 抑制规则过滤        |
| 3 | `if decision is None: return None`（路由表未命中）      | `event_type` 不在 `routing_table` |

三条路径**没有任何日志、没有任何产物**。而 `DecisionEngine.evaluate`（`analysis/decision_engine.py:85`）只在 `warning is not None` 时记 `decision.warning_emitted`：

推论：**「系统为什么没有报警」在今天的 SilverShield 里不留下任何痕迹。**

这对 Slice C 是致命的。Slice C 要比较的四项指标里，`false negative`（该报未报）在决策层的物理表现**就是** `None`。若 trace 只在 `WarningEvent` 存在时产生，则 Slice C 恰恰测不到它存在的理由。草案的 `chosen_action` 字段隐含「决策必有产物」，这个前提在代码里不成立。

> 对一个老年人安全产品而言，**漏报比误报更危险**，而漏报现在是不可观测的。

### 0.2 `WarningEvent.meta` 已经在事实上承担 trace，且已触发 ADR-0014 晋升条款

`RuleBasedDecisionPolicy.decide` 构造 `WarningEvent` 时写入：

```python
meta = {
    "policy": self.name,
    "decided_at": ctx.now.isoformat(),
    "trigger_event_types": sorted({ev.event_type for ev in candidates}),
    "routing_table_version": "v1",
}
```

这四个键**全部是审计血缘字段**，却住在逃生舱里。ADR-0014 §Level 1 的晋升条款白纸黑字：

> 「若某 `meta` 子字段被 **≥2 个消费方稳定依赖**，或**跨 ≥2 个 MINOR 周期**仍以 `meta` 形式存在，则**必须晋升为正式 optional 字段**……该条款防止 `meta` 悄无声息变成事实上的不稳定 Schema。」

且 `routing_table_version: "v1"` 是**硬编码字符串**——而 `RuleBasedDecisionPolicy.__init__(routing_table=...)` 允许注入自定义路由表。**任何定制路由表都会谎称自己是 "v1"**。这是一个已存在的审计缺陷：trace 声称的策略版本与实际生效的策略不对应。

### 0.3 `trigger_events_refs` 无稳定引用对象可指

草案要求 `trigger_events_refs`。但 `PerceptionEvent`（`analysis/perception.py:73-86`）的字段是 `device_id / event_type / score / visitor_id / source_video / timestamp / track_id / bbox / location / repeat_count / is_odd_hour / evidence / meta / created_at`——**没有 `event_id`**。`visitor_id` 是访客身份，不是事件身份。

现有代码用拼接伪造：

```python
"event_id": f"{ev.visitor_id}:{ev.event_type}",
```

同一访客在同一评估周期内产生两条 `abnormal_dwell` 时，**两条事件的 `event_id` 完全相同**。这个"引用"不可用于审计。

而给 `PerceptionEvent` 加 `event_id` 属于 **ADR-0014 Level 1 Schema Contract 变更**（冻结级，需独立 MINOR + Owner 评审 + MQTT 契约同步），**不应被一个可观测性 ADR 顺手夹带**。

### 0.4 `rejected_actions` 在当前策略下不可计算，强求会污染决策逻辑

草案的 `rejected_actions` 预设「策略枚举候选动作再择一」。代码实情相反：

```python
chosen = max(candidates, key=lambda ev: self._event_priority(ev))
decision = self.routing_table.get(chosen.event_type)
final_action = self.routing_table[chosen.event_type][1]
```

`RuleBasedDecisionPolicy` 是 **"取最高优先级候选 → 单次查表"**，从不构造"被拒绝的动作集合"。要产出 `rejected_actions`，必须让策略**为了可观测性而改变决策逻辑**——这违反 AGENTS.md §6.3「重构 PR 必须 0 行为变化」，也让 trace 记录的是**虚构的反事实**而非发生过的事实。

> **trace 记录"策略实际做了什么"，不记录"策略本可以做什么"。** 后者是仿真与实验的职责，不是审计的职责。

### 0.5 现状小结

| 审计问题                                | 今天能否回答                                                   |
| ----------------------------------- | -------------------------------------------------------- |
| 为什么报了这个警？                           | 部分——`meta.policy` / `trigger_event_types` 有线索，但候选与择一过程丢失 |
| 为什么**没有**报警？                        | **完全不能**（三条静默 `return None`）                             |
| 这次决策参考了哪些 Memory？                   | 不能（Slice B 一律传 `None`，Slice C 后也无记录）                     |
| Reasoning 给的 hint 是否被采纳？            | 不能——因而 **C1「hint 永不权威」无法被事后审计证明**                        |
| 生效的路由表到底是哪一版？                       | 不能（硬编码 `"v1"`）                                           |
| baseline / candidate 两臂是否只差 Memory？ | 不能（决策层无 A/B 载体）                                          |

`memory/evaluation/ab_runner.py` 已有 `ABRun{case_id, result_baseline, result_memory}`，但它工作在 **Reasoning 层**（`ReasoningInput → ReasoningResult`），且 `ReasoningResult` **必然存在**。决策层缺失对等物，且决策层多出一个 Reasoning 层没有的形态：**空产物**。

---

## 1. 决策（Decision）

### D1 · Trace 覆盖「空决策」，`outcome` 是带标签联合而非可选字段

**`DecisionTrace` 对每一次 `DecisionPolicy.decide` 调用产出恰好一条记录，无论是否产生 `WarningEvent`。**

```
TraceOutcome:
  kind: "WARN" | "SUPPRESS"          # 带标签联合，构造期校验互斥
  # kind == "WARN":
  risk_level:         str            # 决策产物（非输入，不违反 ADR-0030 C1）
  recommended_action: str
  warning_id:         str            # 与 WarningEvent.warning_id 交叉引用
  # kind == "SUPPRESS":
  suppress_reason:    SuppressReason
```

`SuppressReason` **严格派生自代码里三条真实返回点**，不新造语义：

| 枚举值                     | 对应返回点                      |
| ----------------------- | -------------------------- |
| `no_trigger_events`     | `if not perception_events` |
| `all_suppressed_normal` | `if not candidates`        |
| `unroutable_event_type` | 路由表未命中                     |

> 未来新增抑制路径 **MUST** 同步新增枚举值；契约测试遍历策略的返回点做覆盖断言（见 T7）。

**这是本 ADR 相对 ADR-0030 §5.1 草案的首要修正**，也是 Slice C 能测量 false negative 的唯一前提。

### D2 · 五个具名 Bundle，沿用 ADR-0030 C7 的防膨胀纪律

草案的 8 个平铺字段若直接实现，加上本 ADR 必需的 `correlation_id` / `arm` / 摘要等会膨胀到 13+ 个——正是 ADR-0030 C7 要防的 God Object。本 ADR **对自己适用同一条纪律**：顶层字段必须属于一个**封闭的具名 Bundle 集合**——当前最小集合 = 5 个具名 Bundle。**5 是「当前最小集合」，不是终态冻结**；新增 Bundle 必须经 ADR 评审扩充白名单，禁止横向平铺字段。且新增 Bundle 须满&#x8DB3;**「该审计问题无法由既有 Bundle 表达」**——若只是某 Bundle 的运行环境 / 上下文（如 `environment` / `deployment` / `hardware` / `runtime`），应作为既有 Bundle 的扩展字段而非新开 Bundle，避免 Bundle 退化为新的字段垃圾桶。

```
DecisionTrace
├── identity:   TraceIdentity    { decision_id, correlation_id, arm, created_at }
├── provenance: TraceProvenance  { input_digest, trigger_digest, trigger_refs, memory_refs }
├── policy:     TracePolicy      { name, fingerprint }
├── rationale:  TraceRationale   { considered_candidates, chosen_index }
└── outcome:    TraceOutcome     { kind, ...（见 D1）}
```

与 ADR-0030 同构，`DECISION_TRACE_FIELD_WHITELIST` 在**导入期 fail-closed** 断言顶层字段集合为「白名单 Bundle 集合的子集」：任何 (a) 平铺的非 Bundle 字段、或 (b) 白名单之外的 Bundle 字段，在 `import` 瞬间抛错。新增 Bundle 必须走 ADR 评审并扩充白名单。**纪律的目标是从源头阻止 God Object 的增生，而非永久冻结演化空间**——审计系统天然会增长（未来可能出现 `comparison` / `environment` / `timing` 等 Bundle，如 latency、deployment_version、hardware_context），关键约束是「新增必须评审」，不是「不能再新增」。

- `arm`：`"production" | "baseline" | "candidate"`，默认 `"production"`。Slice C 双轨的载体。
- `identity.decision_id`：每次 `decide()` 唯一（UUID）。**不复用 `warning_id`**——SUPPRESS 时根本没有 `WarningEvent`。
- `identity.correlation_id`：**同一评估周期共享**。双轨比较时两臂 `correlation_id` 相同、`decision_id` 不同。这正面回答了 ADR-0030 §5 的开放问题「`DecisionInput` 是否需 `correlation_id`」——答案是：**不加进 `DecisionInput`（那是输入契约），加进 trace（输出侧记录）**。

### D3 · 用 `considered_candidates` 取代 `rejected_actions`——记录事实，不构造反事实

```
CandidateRecord { trigger_index, event_type, routed_level, routed_action, priority }
TraceRationale  { considered_candidates: tuple[CandidateRecord, ...], chosen_index: int | None }
```

这些**全部是 `decide()` 内已存在的中间态**（`candidates` 列表、`_event_priority`、`routing_table` 查表结果），零逻辑改动即可采集。它回答的审计问题与 `rejected_actions` 相同——"为何选 A 不选 B"——但答案是**发生过的事实**：哪些候选在场、各自被路由到什么、优先级多少、谁胜出。

`chosen_index` 在 SUPPRESS 时为 `None`；`considered_candidates` 在 `all_suppressed_normal` 时为空元组（正是"候选被过滤空"的证据）。

**信息边界（闭集，采纳 review 收紧）**：`CandidateRecord` 是**闭集**——只允许上述 5 个结构化字段（`trigger_index` / `event_type` / `routed_level` / `routed_action` / `priority`），语义上等价于评审建议的 `{event_type, priority, route_target, selection_state}`（`route_target` = `routed_level`+`routed_action`；`selection_state` 由 rationale 层 `chosen_index` 表达）。**严禁**向候选记录塞入任何自由文本 / 解释 / 评分明细 / 原始证据（如 `reasoning_text` / `explanation` / `score_detail` / `raw_evidence`）——那属 Explanation / Simulation 层（呼应 Non-goal #8）。`considered_candidates` 承载的是"实际发生了什么"，不是"应该怎么解释"。

### D4 · 引用而非复制；不夹带 Level 1 Schema 变更

**`trigger_refs`（不引入 `PerceptionEvent.event_id`）**：

```
TriggerRef { index, visitor_id, event_type, timestamp }
```

`index` 是在 **ADR-0030 C3 规范化之后**的 `trigger_events` 元组下标——C3 保证「同一组事件的任意排列 → 同一顺序」，因此该下标是**确定性的、可回放的**。配合 `(visitor_id, event_type, timestamp)` 三元组，在实践中足以定位。

> **已知缺陷显式登记（不掩盖）**：`PerceptionEvent` 缺稳定 `event_id`，现有 `f"{visitor_id}:{event_type}"` 在同访客同类型多事件时冲突。本 ADR **不修复**它——那是 ADR-0014 Level 1 Schema 变更，须独立 MINOR + MQTT 契约同步。本 ADR 记录该债务并以位置 + 三元组绕行（见 §5 开放问题）。

**`memory_refs`（只存引用，不内嵌大对象）**：

```
MemoryRefs {
  reasoning_input_present:  bool
  reasoning_result_present: bool
  historical_record_ids:    tuple[str, ...]
  cross_modal_link_ids:     tuple[str, ...]
  evidence_ref_ids:         tuple[str, ...]
  suggested_action_hint:    str | None
}
```

沿用 ADR-0027 D2「证据以 ID 引用、不复制」。**不内嵌完整 `ReasoningInput`**：既避免 trace 变成第二份真相源，也避免隐私面扩大。

> **`suggested_action_hint` 入 trace 是有意的**：它让 ADR-0030 **C1「hint 只参与排序、永不权威」在事后可被证明**——审计者对比 `memory_refs.suggested_action_hint` 与 `outcome.recommended_action`，即可发现任何越权。**记录 hint 不等于服从 hint。**

**摘要（digest）**：`input_digest` = `DecisionInput.to_dict()` 的规范化 JSON 摘要；`trigger_digest` = 仅 `trigger_events` 部分的摘要。后者是 Slice C「两臂唯一变量 = Memory」的机器可验证证明（见 D7）。

### D5 · `policy.fingerprint` 必须反映**实际生效**的路由表

替换硬编码 `routing_table_version: "v1"`：

```
TracePolicy { name: str, fingerprint: str }   # fingerprint = 实际 routing_table 的规范化摘要
```

定制路由表自动得到不同 fingerprint。审计与回放可据此判定「两条 trace 是否出自同一策略配置」。

计算 MUST 用**规范化序列化**（`json.dumps(..., sort_keys=True)` / canonical form）——Python `dict` 键序不同（如 `{"a":1,"b":2}` vs `{"b":2,"a":1}`）会导致同配置不同摘要。该约束已升为 **T10 不变式**（见 §2），由契约测试钉死。

**`WarningEvent.meta` 的四个既有键（`policy` / `decided_at` / `trigger_event_types` / `routing_table_version`）冻结为 legacy**：为向后兼容保留、**不再扩展**，权威副本迁至 `DecisionTrace`。这是对 ADR-0014 晋升条款的正面回应——**不把 `meta` 养成事实 Schema，而是给它一个正式的家**。

> 为什么不是"把这四个键晋升为 `WarningEvent` 的正式 optional 字段"？因为 **SUPPRESS 时不存在 `WarningEvent`**。把审计血缘挂在决策产物上，就永远覆盖不到"没有产物"的决策。这是结构性理由，不是偏好。

### D6 · 采集经**可选注入**，默认关闭、零行为变化

沿用 ADR-0028 D4（`CrossModalLinkRuntime` 可选注入）的既有范式：

```
DecisionTraceRecorder (Protocol)
├── NullRecorder      # 默认；所有方法 no-op
└── InMemoryRecorder  # 测试 / Slice C 双轨用
```

`DecisionEngine.__init__(..., trace_recorder=None)`；`recorder is None` 时行为与今日**逐字节相同**。

**为什么 recorder 不能走 `DecisionInput`**：`DECISION_INPUT_FIELD_WHITELIST` 是导入期 fail-closed 的 5 字段白名单，加 `trace_recorder` 会 (a) 当场炸导入、(b) 违反 C7、(c) 把**输出通道塞进输入契约**。ADR-0030 的守卫在此正确地否决了错误设计——这是它生效的实证。

**采集接缝**：`self.policy.decide(input)` 全仓**仅一处调用点**（`decision_engine.py:84`），故 `DecisionEngine.evaluate` 是 trace 的生命周期边界（开 span → 调用 → 落 trace）。但 `SuppressReason` 只在 `RuleBasedDecisionPolicy` 内部可辨（engine 只看到 `None`），故策略在三条返回点前经 recorder **写入**抑制原因与候选记录，engine 负责封口与发射。

> 备选方案「engine 侧重新推导抑制原因」被否决：会产生与策略分支条件的重复实现，必然漂移。见 §4 备选方案。

### D6.1 · Trace 生命周期契约（Lifecycle Contract，采纳 review 收紧）

D6 定义了"产生什么"，本小节钉死"由谁在何时写"，避免实现期漂移（policy 写一半、engine 又覆盖、recorder 重复 flush）：

```
CREATED      DecisionEngine.evaluate 进入即开 span，分配 identity
             （decision_id / created_at / correlation_id / arm）
   │
   ↓
COLLECTING   policy.decide 执行；策略在三条 return None 前经 recorder
             **写入 partial**：SuppressReason + considered_candidates
   │
   ↓
FINALIZED    engine 拿到 decide 结果后封口：填充 outcome{kind,
             suppress_reason?, recommended_action?}，trace 定型
   │
   ↓
PERSISTED?   （可选，仅 Slice E）recorder.flush 落盘
```

**所有权与防漂移规则**：

- **单一写主**：`DecisionEngine` 拥有 span 生命周期并封口 `identity` / `outcome`；`DecisionPolicy` 只写它独有的 partial（`suppress_reason` / `candidates`），**禁止**策略写 `identity` 或覆盖 `outcome` 封口字段。
- **幂等封口**：`FINALIZED` 只发生一次；重复 `flush` 以 `decision_id` 去重，**不得**产生第二条 trace（recorder 契约须保证）。
- **失败隔离**：`COLLECTING` 阶段 recorder 抛异常 → 仅 `log.exception`，trace 仍由 engine 封口为 `FINALIZED`（承 T3，决策与 trace 完整性都不因 recorder 故障而丢）。

### D7 · Slice C 双轨的决策层对等物 `DecisionABRun`

对齐既有 `ABRun`（Reasoning 层），补齐决策层：

```
DecisionABRun { correlation_id, trace_baseline: DecisionTrace, trace_candidate: DecisionTrace }
```

**唯一变量守恒（机器可验证）**：两臂 MUST 满足以下六条（由 `assert_conserved` 事后证明，而非由构造过程承诺）：

- `(1)` `self.correlation_id == trace_baseline.identity.correlation_id == trace_candidate.identity.correlation_id`（载体自身字段不得与两臂静默偏离）
- `(2)` `trace_baseline.identity.decision_id != trace_candidate.identity.decision_id`（两臂必须是不同决策实例；同一条 trace 不能同时充当两臂——补强 `TraceIdentity` docstring 既有承诺）
- `(3)` `trace_baseline.provenance.trigger_digest == trace_candidate.provenance.trigger_digest`
- `(4)` `trace_baseline.policy.fingerprint == trace_candidate.policy.fingerprint`
- `(5)` `trace_baseline.provenance.memory_refs.reasoning_input_present is False`（baseline = perception-only）
- `(6)` `trace_candidate.provenance.memory_refs.reasoning_input_present is True`（candidate 必须**真的**携带 Memory；否则「无差异」可能是装配 bug 伪装的结论——本条为 ADR-0031 增补条目）

这正是 `build_baseline_input` 在 Reasoning 层做的事（「两臂唯一差异 = 历史上下文有无」），在决策层的对等表达——**但由 trace 事后证明，而非由构造过程承诺。** 第 (2) 条把 `TraceIdentity` 已写明的「双轨时 `decision_id` 不同」从 docstring 承诺升级为机器可验证；第 (6) 条封堵双轨实验最危险的失败模式（不报错、却给出看似合理的错误结论）。

`outcome.kind` 的四种配对**首次**让决策层的混淆矩阵可观测：

| baseline | candidate | 含义                                |
| -------- | --------- | --------------------------------- |
| SUPPRESS | WARN      | Memory 唤醒了一次漏报（**Slice C 的收益假设**） |
| WARN     | SUPPRESS  | Memory 压制了一次误报                    |
| WARN     | WARN      | 需比较 `risk_level` / `action` 是否被抬升 |
| SUPPRESS | SUPPRESS  | 无差异                               |

> **命名注记（不影响架构）**：未来若 `MemoryABRun` / `ReasoningABRun` 等增多，可抽象为 `EvaluationRun{EvaluationKind, ...}`；本 ADR 沿用 `DecisionABRun` 以与 reasoning 层 `ABRun` 命名一致，不抢先泛化。

---

## 2. 不变式（Invariants，契约测试钉死）

| #       | 不变式                                                                                                                                                                                                                                           | 契约测试                                                |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| **T1**  | **Trace 只写不读**：`DecisionPolicy` / `DecisionInput` 任何实现 MUST NOT 读取 trace 影响决策                                                                                                                                                                 | `test_policy_never_reads_trace`                     |
| **T2**  | **Trace 不改变决策**：同一 `DecisionInput`，`recorder=None` 与 `recorder=InMemoryRecorder()` 产出的 `WarningEvent` 逐字段相同（含同为 `None`）                                                                                                                       | `test_warning_identical_with_and_without_tracing`   |
| **T3**  | **Trace 失败不影响决策**：recorder 抛异常时决策照常返回，仅 `log.exception`（ADR-0028 D4 先例）                                                                                                                                                                       | `test_recorder_exception_does_not_break_decision`   |
| **T4**  | **Trace 不含判定**：禁 `fraud` / `suspect` / `verdict` / `is_fraud` 等字段；导入期 fail-closed（ADR-0001）                                                                                                                                                   | `test_trace_has_no_verdict_fields`                  |
| **T5**  | **隐私**：trace 不含帧 / 人脸 / 音频原始数据 / 凭证 / 文件路径；证据仅以 ID 引用（ADR-0002 / ADR-0027 D2）                                                                                                                                                                 | `test_trace_contains_no_raw_media_or_secrets`       |
| **T6**  | **确定性**：同归一化 `DecisionInput` + 同 `policy.fingerprint` + 同 **runtime config**（recorder 类型一致 / `arm` / `correlation_id` 来源一致）+ 同运行环境 → trace 除 `{identity.decision_id, identity.created_at}` 外逐字段相同；runtime config 差异 MUST NOT 泄漏到非 identity 字段 | `test_trace_deterministic_except_identity`          |
| **T7**  | **抑制必留痕**：任何返回 `None` 的决策 MUST 产出 `outcome.kind == "SUPPRESS"` 且 `suppress_reason` 非空；三条真实返回点全覆盖                                                                                                                                              | `test_every_suppression_path_emits_trace`           |
| **T8**  | **不重复真相**：trace 不内嵌完整 `ReasoningInput` / `PerceptionEvent` 对象，只存引用 + digest；`WarningEvent.meta` 四个 legacy 键不再新增                                                                                                                               | `test_trace_stores_refs_not_payloads`               |
| **T9**  | **候选顺序确定性**：同归一化 `DecisionInput` + 同 `policy.fingerprint` → `rationale.considered_candidates` 元素顺序逐条相同；采集侧 MUST NOT 依赖 `set` / `dict.values()` 等非稳定迭代顺序构造候选（否则 AB 比对会出现「内容相同但 diff 报变化」）                                                      | `test_considered_candidates_order_deterministic`    |
| **T10** | **fingerprint 规范化稳定且可分辨**：同一规范化路由表 MUST 产生相同 `policy.fingerprint`；不同路由表 MUST 以压倒性概率产生不同 fingerprint；计算 MUST 用规范化序列化（`sort_keys=True` / canonical form）——否则 Python `dict` 键序不同（如 `{"a":1,"b":2}` vs `{"b":2,"a":1}`）将导致同配置不同摘要                 | `test_policy_fingerprint_stable_and_discriminating` |

> **T4 与"记录 `chosen_action`"不矛盾**：ADR-0030 C1 禁止的是「决策语义进入**输入**」；trace 是**输出侧记录**，记录已发生的 `risk_level` / `recommended_action` 是审计的本职。T4 禁止的是**判定性**字段（诈骗与否），不是决策产物字段（严重度 / 建议动作）。

---

## 3. 范围与非目标（Scope / Non-Goals）

**在范围内**：`DecisionTrace` 数据契约、采集接缝、抑制原因枚举、双轨载体、导入期守卫与契约测试。

**明确不做**：

1. ❌ **不改 `DecisionPolicy.decide` 签名**——Slice B 刚完成迁移，不再动（ADR-0030 D4）；
2. ❌ **不给 `PerceptionEvent` 加 `event_id`**——Level 1 Schema 变更，独立 MINOR（§5）；
3. ❌ **不改变任何决策行为**——Slice A–C 零行为变化，T2 钉死；
4. ❌ **不做 Memory → Decision 接线**——那是 ADR-0030 Slice C，需 Owner 放行；
5. ❌ **不做 trace 的中心上报**——本地留存，遵 ADR-0002「视频/证据不离 Home 端」；trace 只含引用 ID；
6. ❌ **不产出任何"诈骗/suspect"语义**（AGENTS.md 模块边界铁律）；
7. ❌ **不构造反事实**（`rejected_actions`，见 D3）——反事实归仿真/实验层，不归审计层。
8. ❌ **不生成自然语言解释**——`DecisionTrace` 只保存**机器可验证血缘**（引用 / digest / fingerprint / 候选序列），Human-readable explanation（含 LLM 生成文本）属独立的 **Explanation Layer**，不得塞进 trace。审计链一旦混入「为什么」的解释文本即失去可证明性；若未来需 counterfactual / policy simulation，应另建 `SimulationTrace` 而非污染 `DecisionTrace`。

---

## 4. 后果与备选方案（Consequences / Alternatives）

**正面**：漏报首次可观测；C1「hint 永不权威」从"设计承诺"变为"可事后证伪的事实"；Slice C 双轨具备可审计载体；`meta` 逃生舱不再增生；策略 fingerprint 消除"定制表谎称 v1"的缺陷；并实质奠定**安全证据链（Safety / Assurance Case）基础设施**——未来可沿 `perception evidence → reasoning → memory → policy.fingerprint → action` 反推任意一次报警 / 漏报的端到端证据，是家庭真实部署的准入条件而非附加功能。

**代价**：新增一个契约模块与一层可选注入；开启 trace 后每次决策多一次摘要计算（仅在 recorder 非 None 时发生）；`WarningEvent.meta` 四键与 trace 短期内并存（legacy 冻结，不扩展）。

**备选方案（已否决）**：

| 方案                                                            | 否决理由                                                |
| ------------------------------------------------------------- | --------------------------------------------------- |
| 扩展 `WarningEvent.meta` 承载 trace                               | **SUPPRESS 时无 `WarningEvent` 可挂**；且违反 ADR-0014 晋升条款 |
| 把四键晋升为 `WarningEvent` 正式 optional 字段                          | 同上——覆盖不到空决策；且扩大 Level 1 冻结面                         |
| `decide()` 返回富类型（`DecisionOutcome` 而非 `WarningEvent \| None`） | 紧接 Slice B 再破一次实现方签名，且把审计需求压进决策契约                   |
| engine 侧重新推导 `SuppressReason`                                 | 与策略分支条件重复实现，必然漂移（D6）                                |
| 直接实现 §5.1 草案的 `rejected_actions`                              | 需为可观测性改决策逻辑，且记录虚构反事实（D3）                            |
| 用 `structlog` 日志代替结构化 trace                                   | 日志不是契约：无 schema、无往返、无法做双轨集合比较                       |

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

- **`PerceptionEvent.event_id` 何时晋升？** 本 ADR 以「C3 规范化后下标 + `(visitor_id, event_type, timestamp)` 三元组」绕行。真正修复需 Level 1 MINOR + `docs/07_event_schema.md` + MQTT 契约同步——建议在 Slice C 放行前作为独立 PR 处理；
- **`correlation_id` 向上游传播**（`PerceptionEvent` / `EpisodicRecord` / `AudioSegmentEvent` 携带同一 id）：本 ADR **v1 只做决策域内**（trace 内部生成与共享），向上游注入是 Level 1 变更，归后续；
- **并发语义**：v1 假设「一个 `DecisionEngine` 实例串行评估」（与今日 pipeline 一致）。多线程/异步下 span 作用域如何界定，待有真实并发需求再定；
- **留存与轮转**：trace 落盘格式（JSONL？）、保留期、脱敏策略——归 Slice E，须与 ADR-0027 D9 分层留存对齐；
- **trace 是否进 Dashboard**：与 `reasoning_results` 的 Shadow 展示语义如何并置——归 ADR-0025 后续；
- **`policy.fingerprint` 规范化（已升为 T10）**：同配置必同摘要、异配置高概率异摘要，计算 MUST 用规范化序列化（`sort_keys=True`）；不再作为开放问题，由契约测试钉死。

---

## 6. 实施切片（Slices）与验收清单

> 契约先行、行为分级。A–C **零行为变化**可先合；D/E 触及运行时与落盘，门控评审。

- **Slice A（契约定义，零行为变化）**：新增 `analysis/decision_trace.py`——`DecisionTrace` + 具名 Bundle 集合（当前最小集合 = 5 个）+ `SuppressReason` / `TraceOutcomeKind` 枚举 + `to_dict` / `from_dict` + `DECISION_TRACE_FIELD_WHITELIST` / `DECISION_TRACE_FORBIDDEN_FIELDS` 导入期 fail-closed 守卫（与 `decision_contract.py` 同构）。测试：T4 / T6 / T8 / T9 / T10 + 往返稳定 + `outcome` 联合互斥校验。**不接任何运行时。**
- **Slice B（采集接缝，默认关闭）**：`DecisionTraceRecorder` Protocol + `NullRecorder` + `InMemoryRecorder`；`DecisionEngine` 可选注入；WARN 路径产出完整 trace。测试：T1 / T2 / T3。
- **Slice C（抑制留痕）**：三条 `return None` 路径接入 recorder，产出 `SUPPRESS` trace（**本 ADR 的核心价值**）。测试：T7 全覆盖 + 变异验证（新增第四条返回路径未登记枚举时测试必须失败）。
- **Slice D（双轨载体，门控）**：`DecisionABRun` + 唯一变量守恒断言（D7 六条），与 `memory/evaluation/ab_runner.py` 风格对齐。**不启用 Memory 接线**，仅提供载体供 ADR-0030 Slice C 使用。
- **Slice E（落盘与留存，门控 · 已落地）**：新增 `analysis/decision_sink.py`——本地 JSONL sink + 保留期轮转 + 落盘期脱敏守卫，对齐 ADR-0002（本地留存、证据仅引用 ID）/ ADR-0027 D9（分层留存，默认 `retention_days=30` 对齐 MEDIUM；本地 UTC 计时、幂等删除、失败不阻塞主链）：
  - `JsonlTraceRecorder`（实现 `DecisionTraceRecorder`）：每条已封口 trace 经 `to_dict` 序列化 + `assert_desensitized` 守卫后追加一行 JSON 到本地文件；`flush` 做 fsync；`record` / `flush` 异常仅 `log.exception`（T3 失败隔离，绝不外抛）。
  - `JsonlABRunRecorder`：决策层双轨 `DecisionABRun` 的对等落盘（供 ADR-0030 Slice C 产出），复用同一脱敏守卫与保留期轮转。
  - `DecisionABRun.to_dict` / `from_dict`：补齐 review #4（序列化归 Slice E），与模块内其他契约类型一致（T8 不复制 Bundle 语义）。
  - `assert_desensitized`（fail-closed 兜底）：拒绝落盘任何含 T4 判定字段 / 密钥类键 / 绝对路径或 URL 串的 payload——trace 数据模型已凭构造满足 T4 / T5，守卫是落盘边界的最后兜底。
  - `prune_jsonl`：按 `retention_days`（本地 UTC）删除过期记录；保留边界 inclusive、幂等、损坏行仅告警保留（不阻塞主链）。
  - **零行为变化**：`JsonlTraceRecorder` 结构上满足 `DecisionTraceRecorder` Protocol，可直接注入 `DecisionEngine(trace_recorder=...)`，无需改动 engine。单独 PR + Owner 评审。

### 验收清单（Acceptance Criteria）

1. **D1 空决策覆盖**：`SuppressReason` 三枚举与代码三条返回点一一对应；T7 通过且经变异验证；
2. **D2 防膨胀**：`DecisionTrace` 顶层字段必须属于白名单 Bundle 集合（当前最小集合 = 5），新增 Bundle 经 ADR 评审；`DECISION_TRACE_FIELD_WHITELIST` 导入期断言生效（平铺字段 / 越界 Bundle 即炸）；
3. **D3 无反事实**：trace 不含 `rejected_actions` 类字段；`considered_candidates` 全部可由 `decide()` 现有中间态导出，路由逻辑逐字未改；
4. **D4 引用不复制**：T8 通过；`trigger_refs.index` 与 C3 规范化后顺序一致（回归测试钉死乱序输入→同 index）；
5. **D5 fingerprint 真实 + T10 规范化**：注入自定义 `routing_table` 的策略产出的 fingerprint 与默认表不同（测试钉死）；同配置跨进程 / 键序打乱仍得相同摘要（`sort_keys=True`）；`WarningEvent.meta` 四键未新增；
6. **D6 零行为变化**：T2 / T3 通过；`recorder=None` 为默认；`DecisionInput` 字段集合未变（ADR-0030 白名单仍绿）；
7. **D7 双轨守恒**：`DecisionABRun` 六条守恒断言通过；四种 outcome 配对均有用例；
8. **边界铁律**：T4 / T5 通过；全量 `ruff check src tests` + `pytest` 全绿（AGENTS.md 基线，不允许回归）。
9. **D6.1 生命周期契约**：trace 状态机 `CREATED→COLLECTING→FINALIZED→PERSISTED?` 由 `DecisionEngine` 拥有 span；`DecisionPolicy` 仅写 partial（`suppress_reason` / `candidates`）；重复 `flush` 以 `decision_id` 去重不产生第二条 trace；`COLLECTING` 阶段 recorder 异常仍由 engine 封口为 `FINALIZED`（T3 失败隔离）；T10 规范化测试通过。
10. **Slice E 落盘与留存**：`JsonlTraceRecorder` 每条 trace 落一行本地 JSON、往返一致（`from_dict` 重建等价 trace）；`assert_desensitized` 拒绝 forbidden / 敏感键 / 路径串且对正常 trace 无误报；`prune_jsonl` 按 `retention_days`（本地 UTC）删除过期记录、边界 inclusive、幂等、损坏行不阻断；`DecisionABRun` 序列化往返一致且守恒在往返后仍成立；`record` / `flush` 写盘异常不向外传播（T3）。

---

## 7. 修订记录（Changelog）

> **修订权属（呼应 AGENTS.md §6.3）**：本 ADR 处于 Proposed 阶段由 Owner 评审；**冻结（Accepted）后的修订由 Owner 追加新条目，AI 不修改修订记录**。

- **2026-08-08**：初稿（Proposed）。承接 ADR-0030 §5.1 路线图，并**以代码实情校正其 `DecisionTrace` 草案四处**：(1) 草案 `chosen_action` 隐含「决策必有产物」，而 `RuleBasedDecisionPolicy` 有三条**完全静默**的 `return None` 路径——漏报在今日不可观测，故 D1 把 `outcome` 定为 `WARN | SUPPRESS` 带标签联合，`SuppressReason` 严格派生自三条真实返回点；(2) 草案 `trigger_events_refs` 无稳定引用对象——`PerceptionEvent` 无 `event_id`，现有 `f"{visitor_id}:{event_type}"` 同访客同类型即冲突，故 D4 以「C3 规范化后下标 + 三元组」绕行并显式登记该债务，拒绝夹带 Level 1 Schema 变更；(3) 草案 `rejected_actions` 在"取 max 候选 + 单次查表"的策略下不可计算，强求需为可观测性改决策逻辑并记录虚构反事实，故 D3 代之以 `considered_candidates`（零逻辑改动、全部为已发生事实）；(4) 草案 `policy_version` 对应的现有 `meta.routing_table_version` 是硬编码 `"v1"`，定制路由表亦谎称 v1，故 D5 改为实际生效路由表的 fingerprint。另新增：D2 对自身适用 ADR-0030 C7 防膨胀纪律（顶层 5 个具名 Bundle + 导入期 fail-closed）；D5 回应 ADR-0014 `meta` 晋升条款，指出「挂在 `WarningEvent` 上永远覆盖不到空决策」这一结构性理由；D6 复用 ADR-0028 D4 可选注入范式，并记录 `DECISION_INPUT_FIELD_WHITELIST` 正确否决了「recorder 走 `DecisionInput`」的错误设计；D7 补齐 `ab_runner.ABRun` 在决策层的对等物 `DecisionABRun` 与「唯一变量守恒」的机器可验证断言。T1–T8 不变式钉死「trace 只写不读 / 不改决策 / 失败隔离 / 无判定 / 隐私 / 确定性 / 抑制必留痕 / 不重复真相」。本 ADR 仅冻结契约，不实现模型、不接 Memory。
- **2026-08-08（Owner review 修订）**：吸收 Owner 评审三点收紧，状态由 Proposed 置为 In Review。(a) **D2 不过早冻结**——顶层不再宣称「恰好 5 字段」，改为「字段必须属于白名单 Bundle 集合（当前最小集合 = 5），新增 Bundle 须经 ADR 评审」；纪律目标从「永久冻结」改为「从源头阻止 God Object 增生，同时保留演化空间」。(b) **T6 收紧 + 新增 T9**——明确 `rationale.considered_candidates` 元素顺序必须确定性可复现，采集侧禁止依赖 `set` / `dict.values()` 等非稳定迭代顺序，否则 AB 比对会出现「内容相同但 diff 报变化」。(c) **新增 Non-goal #8**——`DecisionTrace` 不生成自然语言解释，Human-readable explanation（含 LLM 文本）属独立 Explanation Layer，不得污染事实层（审计链混入解释文本即失去可证明性）。Owner 明确要求**本 ADR 不再扩展功能**，下一步先实现 Slice A 用实情反验契约，再考虑 ADR-0032（程序化视频 / 场景生成）与 ADR-0033（Benchmark Harness）。
- **2026-08-08（ARB review 第二轮修订）**：吸收 ARB 级评审五处收紧（架构正确性 / 边界治理 / 工程可落地 / 文档成熟度均 ≥8.5/10）。(a) **新增 D6.1 Trace 生命周期契约**：状态机 `CREATED→COLLECTING→FINALIZED→PERSISTED?`，单一写主（engine 拥 span、policy 只写 partial）、幂等封口（`decision_id` 去重不产生第二条 trace）、失败隔离（承 T3），防止实现期 policy/engine/recorder 互相覆盖或重复 flush。(b) **T6 扩展 + 新增 T10**：T6 把 `runtime config`（recorder 类型 / `arm` / `correlation_id` 来源）纳入确定性前提且差异不得泄漏非 identity 字段；T10 把 `policy.fingerprint` 规范化（必 `sort_keys=True`，同配置必同摘要、异配置高概率异摘要）升为不变式，原 Open Question 出列。(c) **D3 定义候选信息边界（闭集）**：`CandidateRecord` 仅允许 5 个结构化字段，严禁 `reasoning_text` / `explanation` / `score_detail` 等自由文本（呼应 Non-goal #8）。(d) **D2 Bundle 扩展准则**：新增 Bundle 须满足「该审计问题无法由既有 Bundle 表达」，防退化为字段垃圾桶。(e) **D7 命名注记**：未来可泛化为 `EvaluationRun`，本 ADR 沿用 `DecisionABRun` 与 reasoning 层 `ABRun` 一致。(f) **Consequences 补强**：点明本 ADR 实质奠定 Safety / Assurance Case 证据链基础设施。
