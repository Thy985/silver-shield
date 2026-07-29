# ADR-0010: P0-8 WarningEvent 决策架构 —— 决策层与执行层的分离

- 状态：Accepted
- 日期：2026-07-19
- 决策者：Owner
- 相关：`docs/08_roadmap.md`（P0-8 / P0-9 重排）、`docs/07_event_schema.md`（5 类 PerceptionEvent）、
  `src/home_perception/analysis/{perception,rule,rule_engine}.py`（P0-7b 风险语义层）、
  ADR-0001（只产事实）、ADR-0007（事实层 vs 语义层）、ADR-0009（Rule Engine）、
  `src/home_perception/analysis/{warning,decision_policy,decision_engine}.py`（本 ADR）

## 背景（Context）

P0-7b 完成后，`PerceptionEvent` 流已经是干净的"风险语义事件"（5 类 + score）。
Owner P0-7b review 明确指出"Rule 不产生 WarningEvent"，下一步应把"是否干预"的决策
从 Rule 层剥离出来，由**独立的决策层**完成：

> "下一步不要急着接萤石、MQTT、LLM。先把：
> **PerceptionEvent → WarningEvent**
> 这个决策闭环设计正确。"

```
DetectionResult
      │
      ▼
VisitorTrack
      │
      ▼
VisitorEvent (P0-6 事实)
      │
      ▼
RiskFeature (P0-7a 数值信号)
      │
      ▼
PerceptionEvent (P0-7b 风险语义 · §7.2 5 类)
      │
      ▼
WarningEvent (P0-8 决策层)            ← 本 ADR
      │
      ▼
MQTT / 协同处置 (P0-9 行动层)          ← 后续 ADR
```

## 决策（Decision）

### Decision 1: PerceptionEvent 不直接触发通知

继续分层不跳级（避免"是否干预"被推到 Rule 层）：
- `PerceptionEvent` 是 Rule 的输出（§7.2 5 类 + score），不携带"系统决定怎么办"语义
- 通知 / 升级 / 取证的决策 **必须** 经 `DecisionPolicy` → `WarningEvent` 才下发
- 反模式：
  ```python
  if perception.event_type == "high_risk_approach":
      send_sms_to_family()  # ❌ Rule/Perception 层直接执行
  ```
- 正模式：
  ```python
  warning = decision_engine.evaluate(perception_events)  # 决策层
  if warning:
      action_executor.execute(warning)  # P0-9 行动层
  ```

### Decision 2: WarningEvent 是决策层对象

`WarningEvent` 表达"系统准备采取什么行动"，与 `PerceptionEvent` 严格分离：

| 维度 | PerceptionEvent (P0-7b) | WarningEvent (P0-8) |
| --- | --- | --- |
| 性质 | 风险语义标签 | 决策事件 |
| 等级 | `score` ∈ [0,1]（**规则命中强度**）| `risk_level` ∈ {LOW, MEDIUM, HIGH}（**决策严重度**）|
| 动作 | 不携带 | `recommended_action` ∈ {MONITOR, NOTIFY_FAMILY, ESCALATE_COMMUNITY} |
| 状态 | 单次（`created_at`）| 生命周期（`status` ∈ {CREATED, PENDING, CONFIRMED, RESOLVED, REJECTED}）|
| 主语 | "观察到 X 类现象"| "决定执行 Y 级别响应"|

字段：
- `warning_id` = UUID4（独立生命周期，区别于 visitor_id 跨摄像头复用）
- `elder_id`：被守护老人 ID（来自配置 / 上级系统）
- `device_id`：触发决策的 Home 端设备 ID
- `risk_level` ∈ {`LOW`, `MEDIUM`, `HIGH`}
- `recommended_action` ∈ {`MONITOR`, `NOTIFY_FAMILY`, `ESCALATE_COMMUNITY`}
- `status` ∈ {`CREATED`, `PENDING`, `CONFIRMED`, `RESOLVED`, `REJECTED`}（默认 `CREATED`；P0-9 行动层翻转）
  - 语义约定：**这些状态描述决策生命周期，不描述执行结果**（"NOTIFY_FAMILY 已完成"不是状态，是 P0-9 内部日志）
- `trigger_events`：触发本次决策的 PerceptionEvent 摘要（仅 dict 引用，不存对象）
- `reason_summary`：人话可读的触发原因（中文，**不**含 fraud/scam/verdict 等字样）
- `perception_score` ∈ [0,1]（聚合 = max(trigger_events.score)）
- `evidence`：取证引用（snapshot/clip URI）；P0-8 不填，P0-9 行动层填
- `meta.policy`：决策策略名（可审计）
- `meta.decided_at`：决策 UTC 时间
- `created_at`：本决策生成 UTC 时间

### Decision 3: 风险等级不是诈骗概率

与 ADR-0009 Decision 3 同构：`risk_level` 是**决策严重度**（0/1/2 三档），**不是**诈骗概率。
- 命名差异：
  - `PerceptionEvent.score`：规则命中强度（float ∈ [0,1]）
  - `WarningEvent.risk_level`：决策严重度（3 档枚举）
- 中心侧（AI Understand / Predict）综合 `WarningEvent` 流 + 老人画像 + 历史趋势 → 才形成最终判断
- docstring 强制说明此边界（与 `PerceptionEvent` 同款"领域污染防火墙"）

### Decision 4: DecisionPolicy 独立于 Rule

继续 ADR-0009 "Rule 独立于 Feature" 哲学：
- `DecisionPolicy` 消费 `PerceptionEvent[]`（不重算 Feature / 不重新组合 Rule）
- 组合判断已由 `CompositeRule`（P0-7b `HighRiskApproachRule`）完成，输出 `high_risk_approach` 事件
- `DecisionPolicy` 只做"事件 → 决策"映射，不做"事件 → 事件"组合

可替换性：
- `RuleBasedDecisionPolicy`（MVP，按 `routing_table` 查表）
- v2：ML 评分策略
- v3：LLM 解释策略（deferred，不在第一版）

路由表（per-event `(level, action, reason)`）：
```python
DEFAULT_ROUTING_TABLE = {
    "high_risk_approach":    ("HIGH",   "ESCALATE_COMMUNITY", "多风险规则同时命中"),
    "abnormal_dwell":        ("LOW",    "NOTIFY_FAMILY",      "异常停留"),
    "repeat_visit":          ("LOW",    "NOTIFY_FAMILY",      "重复访问"),
    "visit_pending_verify":  ("LOW",    "MONITOR",            "未在白名单"),
    "visit_normal":          ("LOW",    "MONITOR",            "异常时段访问"),  # 仅 is_odd_hour=true 时实际触发
}
```

聚合规则（Owner："max wins" 哲学）：
- **risk_level** = max(risk_level per event)  →  HIGH + LOW = HIGH
- **recommended_action** = chosen event 的 per-event action  →  HIGH + LOW 的 action = HIGH 的 action
- **reason_summary** = 合并去重，保留所有触发原因
- **perception_score** = max(score per event)
- 多个 candidate 时，`chosen = max(_event_priority(event))`，确保取最严重的

如家庭需"按 level 强制覆盖"（例：不愿直接升级社区，先联系家属），可在 `routing_table`
中把同 level 多个 event 映射为同 action。这是 per-family 定制点。

### Decision 5: Action 执行延迟到 P0-9

`WarningEvent` 是**决策**（severity + suggested action 的字符串），**不是**执行。
边界：
- ✅ P0-8 可做：构造 `WarningEvent`、记录决策日志、序列化到日志/MQ
- ❌ P0-8 **不**做：调 MQTT / 发短信 / 拨电话 / 升级社区 / 启动录像取证

`recommended_action` 是字符串 hint，P0-9 行动层按 hint 路由到对应 executor：
- `MONITOR` → 仅记录 + 上报中心
- `NOTIFY_FAMILY` → 推 App 消息 / 短信家属
- `ESCALATE_COMMUNITY` → 通知社区 / 物业 / 警方

这样保证：
- 决策策略可独立测试（无需 mock MQTT）
- 行动层可独立升级（v2 加微信 / 钉钉通道不影响决策）
- 单元测试可单测决策层（mock decision_policy + DecisionEngine + WarningEvent，无外部副作用）

## 动机（Rationale）

- **守 ADR-0007 / ADR-0009 边界**：Rule 已经是"事实 → 风险语义"的最后一道加工，
  再加一层"风险语义 → 决策"才能形成"检测 → 判断 → 处置"的清晰职责分离。
- **可替换**：决策策略可换 ML / LLM，不影响 Rule 层和事实层。
- **可解释**：每条 `WarningEvent` 都带 `meta.policy` 标识"哪条策略决策" + `trigger_events` 列出
  触发的 PerceptionEvent + `reason_summary` 人话原因，可审计。
- **可测试**：决策层无外部副作用，单元测试只需构造 PerceptionEvent 列表 + 断言 WarningEvent 字段。
- **MVP 落地不冒进**：先打通"决策闭环"，再接萤石 / MQTT / LLM，避免一上来就耦合多个未稳定子系统。

## 后果（Consequences）

- ✅ P0-8 决策层职责清晰：`PerceptionEvent[]` → `WarningEvent`（**不**调任何外部系统）
- ✅ `WarningEvent` 严格不含最终判定字段（`fraud_result` / `is_fraud` / `verdict` 等黑名单）
- ✅ `risk_level` 语义明确（严重度，不是诈骗概率），与 `PerceptionEvent.score` 区分清楚
- ✅ `DecisionPolicy` 独立于 `Rule`（不重算 Feature / 不重新组合 Rule）
- ✅ 组合规则 `max wins`（HIGH + LOW = HIGH + HIGH action），符合 Owner 期望
- ✅ 决策层不调 MQTT / 不通知家属 / 不升级社区（→ P0-9 行动层）
- ✅ 65 个新单元测试 + 1 个 CAVIAR 端到端集成测试；总测试 197 全绿；ruff 干净
- ⚠️ P0-8 范围中等（WarningEvent + DecisionPolicy + DecisionEngine + 65 tests），需充分单测
- ⚠️ `DecisionPolicy` 第一版只支持 RuleBased 策略；ML / LLM 策略 v2/v3
- ⚠️ `elder_id` 当前来自设备配置（MVP 一设备 = 一老人），v2 可来自中心 RiskTwin
- 📌 约束后续：WarningEvent 字段增删按 ADR-0005 走 schema_version 评审；DecisionPolicy 策略增删
  需新开 ADR；MQTT / 通知 / 升级等执行层细节留给 P0-9 行动层 ADR-0011（待写）

## 替代方案（Alternatives）

- **PerceptionEvent 直接触发通知**：否决。跳级，破坏 ADR-0007 "事实层 vs 语义层" 边界。
- **WarningEvent 含最终判定（`is_fraud` / `fraud_probability`）**：否决。决策层不是犯罪认定层，
  中心综合判断才能形成最终判定（ADR-0001）。
- **DecisionPolicy 重算 Feature / 重新组合 Rule**：否决。重复计算 + 难以审计 +
  Rule 与 Policy 双层职责混乱。
- **DecisionEngine 直接调 MQTT / 通知**：否决。决策与执行耦合，无法独立测试，无法独立升级通道。
- **`risk_level` 复用 `PerceptionEvent.score` 命名**：否决。语义不同（决策严重度 vs 规则强度），
  同名会导致中心误读。
- **per-level action 映射（`action_for_level`）**：考虑后否决。per-event action 更精细
  （abnormal_dwell → NOTIFY_FAMILY vs visit_pending_verify → MONITOR 都是 LOW 但 action 不同），
  组合时取 chosen event 的 action（max wins）已能覆盖"按 level 升级"需求。
- **`reason_summary` 用英文**：否决。决策层面向"家属 / 社区"用户，中文更可读。
