# ADR-0007: P0-6 事实事件层 vs P0-7 风险语义层 —— 领域对象边界固化

- 状态：Accepted
- 日期：2026-07-19
- 决策者：Owner
- 相关：`docs/08_roadmap.md`（P0-6/P0-7）、`docs/07_event_schema.md`（5 类 PerceptionEvent 终态）、
  `src/home_perception/analysis/event.py`、`src/home_perception/analysis/event_builder.py`、
  ADR-0001（只产事实）、ADR-0005（事件契约稳定性）、ADR-0006（VisitorTrack 领域封装）

## 背景（Context）

P0-5 把 YOLO `track_id` 封装为 `VisitorTrack` 领域对象后，下一个对象是 `VisitorEvent`。
但 `VisitorEvent` 在不同人脑里指向不同东西：

- **直觉 A（错误）**：`VisitorEvent` = 5 类门前标签事件（`visit_normal` / `abnormal_dwell` / ...），
  字段含 `event_type` / `score` / `is_odd_hour` —— 这是 §7.2 的最终对外形态，但已经是
  "事实到语义的第一次解释"（480s 是否异常取决于家庭/时间/历史）。
- **直觉 B（正确）**：`VisitorEvent` = "什么时候来、什么时候走、停了多久、从哪条视频"——
  纯事实，不含任何业务判断。把 5 类标签交给后续 P0-7 Rule Engine。

直觉 A 的诱惑在于"一步到位"——既然 VisitorEvent 早晚要变 5 类事件，不如现在直接打标。
但 5 类标签的判断**依赖**家庭环境、时间窗口、历史基线、老人习惯，这些都是 Home 端没有的
上下文；提前打标 = Home 端越权，等于把 P0-7 的事抢了，污染 P0-6 的领域对象稳定性。

> Owner 原则：**领域对象应该保存"发生了什么"，而不是"系统认为它意味着什么"。**

## 决策（Decision）

固化三层领域对象边界，对应银龄盾的事件总线：

```
DetectionResult（Perceive · P0-3 · 帧级事实）
       ↓
VisitorTrack（P0-5 · 跨帧状态对象）
       ↓
VisitorEvent（P0-6 · 事实事件，离场即生成）  ← 本 ADR
       ↓
Feature Extraction（P0-7 · 特征抽取：停留时长/访问次数/时间分布/轨迹模式）
       ↓
Rule Engine（P0-7 · 5 类风险标签 + risk_score）
       ↓
RiskFeature（P0-7 · 解释层输出）
       ↓
WarningEvent（P0-8/P0-9 · 决策层事件，中心最终消费）
```

**`VisitorEvent` 严格只含"发生了什么"**：
- ✅ `event_id` / `visitor_id` / `enter_time` / `leave_time` / `duration_seconds` / `source_video` / `created_at`
- ❌ **不含**：`event_type` / `score` / `risk_level` / `visit_type` / `is_suspicious` / `repeat_count` /
  `is_odd_hour` / `evidence` —— 这些都是 P0-7 之后的事

**触发时机**：仅在 `VisitorTrack.status` 从 `active` 转 `left`（absence_gap 兜底）时生成。
**不**生成"进入"事件、**不**生成"在场"周期快照、**不**生成"reenter"事件 —— 那些是
P0-7 特征抽取的输入需求，不是事实事件。

**职责约束（写入 `VisitorEvent` docstring 强制）**：
1. P0-6 范围内，`VisitorEventBuilder` **不允许**新增任何"判断"类字段（违反则 CI/contract test
   `test_no_business_judgment_fields` 立即报警）。
2. P0-7 规则层需要新信号时，**应新建** `Feature` / `RiskFeature` / `WarningEvent` 对象，
   **不**就地扩展 `VisitorEvent`。
3. `VisitorEvent` 的字段增删按 ADR-0005 走 schema_version 评审（这是契约的一部分）。

## 动机（Rationale）

- **守住"事实 vs 语义"边界**：5 类标签判断所需信息（家庭/时间/历史/习惯）Home 端没有；
  越权 = 高误报 + 伦理法律风险（与 ADR-0001 同因）。
- **解耦 ML/LLM 接入**：未来 P0-7 可以叠加 ML 模型或 LLM 解释（v2），但事实层**结构稳定**，
  不会因为换了 ML 模型就改 `VisitorEvent` 字段。
- **可辩护**：输出仅"visitor X 在 cam01 上停了 8 秒"——客观事实；不像"high_risk_approach"
  需要辩护依据。
- **测试简化**：P0-6 测试只验证"事实生成正确"，不需要为每条规则维护 fixture；
  规则层的 fixture 数量会爆炸。
- **架构一致性**：`DetectionResult` / `VisitorTrack` / `VisitorEvent` 三个对象全部属于
  "事实/状态"层，`Feature` / `RiskFeature` / `WarningEvent` 三个对象属于"解释/决策"层，
  层与层之间通过稳定接口（`VisitorEvent` → 特征抽取）连接，与 ADR-0001/0006 同构。

## 后果（Consequences）

- ✅ P0-6 范围清晰可独立交付：15 个测试（含 1 个 CAVIAR 真实链路）只验证事实生成与边界，
  不依赖 P0-7。
- ✅ P0-7 规则层可以自由设计（基于特征 / 基于规则 / 基于 ML / 基于 LLM 解释），
  不影响 P0-6 稳定接口。
- ✅ 中心消费侧只看到 5 类事件（§7.2 终态），不直接消费 `VisitorEvent`；
  `VisitorEvent` 是 Home 端"事实流"，WarningEvent 是 Home→中心的"决策流"，职责清晰。
- ✅ CI 用 `test_no_business_judgment_fields` 守住 P0-7 边界，新增业务字段立刻报警。
- ⚠️ 团队成员可能误以为"VisitorEvent 就是门前事件"——文档 + 命名 (`VisitorEvent` vs `WarningEvent`)
  + ADR 三处对齐强调"VisitorEvent = 事实；WarningEvent = 决策"。
- ⚠️ P0-7 阶段需要再设计 `Feature` / `RiskFeature` / `WarningEvent` 三个对象，
  不能图省事往 `VisitorEvent` 加字段（这是刻意的摩擦）。
- 📌 约束后续：P0-7 引入的 `Feature` / `WarningEvent` 等对象须新开 ADR 评审；改 `VisitorEvent` 字段
  须升 `schema_version`（按 ADR-0005 治理）。

## 替代方案（Alternatives）

- **VisitorEvent 直接含 5 类标签 + score**：否决。把 P0-7 的事提前到 P0-6 做了，
  等于把 5 类标签的判断责任绑到 `VisitorEventBuilder` 上，违反"事实 vs 语义"分层。
- **`VisitorEvent` 含 `repeat_count` 等"半事实"字段**：否决。`repeat_count` 看似"重复次数是事实"，
  但"重复"的定义本身是规则（同一 track 在 N 分钟内出现 M 次算"重复"？N 和 M 多少？）。
  属于 P0-7 Feature Extraction 的输出，不属于 P0-6 事实。
- **生成 enter + leave + reenter 三路事件**：否决。reenter 是 track 状态变化，不是新事件；
  它可以通过消费 `VisitorEvent` 流后端聚合得出（按 visitor_id + 时间窗口），不增加 P0-6 复杂度。
  同样，"reenter"是规则标签（revisit/abnormal），不是事实。
- **P0-6 范围收缩到只生成一个 dict**：否决。VisitorEvent 作为领域对象（dataclass + 校验 +
  序列化）比裸 dict 更稳定，能用 contract test 锁格式，符合 ADR-0005 治理。
