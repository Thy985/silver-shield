# 架构决策记录（ADR · Architecture Decision Records）

> 本目录记录 **Home 感知模块** 已发生的、有长期影响的架构与选型决策。
> 目的：让"为什么这样设计"可追溯，避免后人（含 AI 协作者）在无背景下推翻既定约束或重复讨论。

## 什么时候必须写 ADR

依据 [`../../AGENTS.md`](../../AGENTS.md) §6.3 / §7 / §9：

- 跨模块 / 对外契约的改动（事件 Schema、MQTT topic、与中心的数据对象对齐）；
- 影响边界的决策（本模块产出什么、不产出什么）；
- 关键技术选型（模型、协议、引擎分层、部署形态）及其被**实测数据推翻/确认**时；
- 任何"以后有人会问为什么"的决策。

> 不需要 ADR 的：局部实现细节、变量命名、可随时无痛回退的小改动。

## 编写规则（对齐 AGENTS.md §7）

- **文件名**：`NNNN-<kebab-case-title>.md`，`NNNN` 从 `0001` 递增，**编号不复用**（即使 ADR 被废弃，编号也保留）。
- **状态机**：`Proposed → Accepted → Superseded by ADR-NNNN`（被取代）/ `Deprecated`（废弃不再适用）。
- **必含小节**：背景（Context）/ 决策（Decision）/ 动机（Rationale）/ 后果（Consequences）/ 替代方案（Alternatives）。
- **不可变原则**：ADR 一旦 `Accepted`，**不原地重写**；决策变化时新开一篇 ADR 并在旧篇标注 `Superseded by ADR-NNNN`。
- **归属**：`docs/ADR/*` 为 Owner 专属受保护路径；AI 可提 PR，但**不得自行 merge / 直推 main**。

## ADR 模板

新建 ADR 时复制以下骨架：

```markdown
# ADR-NNNN: <标题>

- 状态：Proposed | Accepted | Superseded by ADR-NNNN | Deprecated
- 日期：YYYY-MM-DD
- 决策者：Owner
- 相关：docs/xx、ADR-NNNN、PR #N

## 背景（Context）
（问题是什么、约束条件、触发这次决策的场景。）

## 决策（Decision）
（我们决定做什么，一句话可陈述。）

## 动机（Rationale）
（为什么这样选，依据是什么——实测数据 / 边界约束 / 比赛目标。）

## 后果（Consequences）
（正面 + 负面 + 需要承担的技术债 / 后续动作。）

## 替代方案（Alternatives）
（考虑过但未采用的方案，及否决原因。）
```

## ADR 清单

| 编号 | 标题 | 状态 | 日期 |
| --- | --- | --- | --- |
| [0001](0001-perceive-outputs-facts-not-verdicts.md) | 感知模块只产"事实/标签"，不裁决"诈骗人员" | Accepted | 2026-07-18 |
| [0002](0002-rule-ml-two-layer-engine-defer-llm.md) | 风险引擎采用 Rule + ML 两层，LLM 解释推迟到 v2 | Accepted | 2026-07-18 |
| [0003](0003-yolo11n-explicit-resize-imgsz-profiles.md) | 检测采用 YOLO11n + 显式 resize，imgsz 配置化（默认 480） | Accepted | 2026-07-18 |
| [0004](0004-rtsp-over-hls-for-realtime-stream.md) | 实时取流 RTSP 优先、HLS 回退 | Accepted | 2026-07-18 |
| [0005](0005-event-schema-mqtt-contract-stability.md) | 事件 Schema 与 MQTT 契约作为稳定对外接口 | Accepted | 2026-07-18 |
| [0006](0006-yolo-trackid-wrapped-as-visitor-track.md) | YOLO track_id 封装为银龄盾自己的 VisitorTrack 领域对象（P0-5） | Accepted | 2026-07-19 |
| [0007](0007-p0-6-facts-vs-p0-7-semantics.md) | P0-6 事实事件层 vs P0-7 风险语义层 —— 领域对象边界固化 | Accepted | 2026-07-19 |
| [0008](0008-feature-extraction-architecture.md) | P0-7a Feature Extraction 体系：结构化数值信号层 | Accepted | 2026-07-19 |
| [0009](0009-rule-engine-architecture.md) | P0-7b Rule Engine 架构：风险语义层与五类规则 | Accepted | 2026-07-19 |
| [0010](0010-warning-event-decision-architecture.md) | P0-8 WarningEvent 决策架构：决策层与执行层分离 | Accepted | 2026-07-19 |
| [0011](0011-action-layer-architecture.md) | P0-9 ActionLayer 行动层架构：决策的执行与外部通道 | Accepted | 2026-07-19 |
| [0012](0012-p0-integration-validation.md) | P0 Integration Validation 系统级冻结前验收：6 Golden Scenarios + 状态机独立 + 故障注入 + CAVIAR 端到端 | Accepted | 2026-07-19 |
