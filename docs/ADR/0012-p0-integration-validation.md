# ADR-0012 · P0 Integration Validation · 系统级冻结前验收

## 状态

Accepted · 2026-07-19

## 背景

P0-3 ~ P0-9 全部完成（256 个单元测试全绿），银龄盾 MVP 感知 → 理解 → 决策 → 行动
的链路已经形成。但 Owner 在 P0-9 review 后明确指出：

> "如果马上进入 P0-10，会把两个问题混在一起：
> 1. 前面领域链路的问题；
> 2. main/pipeline 装配问题。
> 后续发现失败时，很难判断到底是哪一层的问题。"

并建议增加一个 P0 Integration Validation 里程碑作为"冻结前验收"。

## 决策

### 决策 1：在进入 P0-10 之前必须做一次系统级端到端验证

P0-10 是装配层（"怎么启动系统"），不应承担逻辑层（"为什么风险等级错了"）的验证
责任。**模块级单测不能替代系统级验证** —— 6 个 Golden Scenarios 是验证模块组合后
行为的唯一手段。

### 决策 2：6 个 Golden Scenarios 是必含的最小集合

按 Owner 列举的场景，覆盖银龄盾 MVP 的所有关键业务路径：

| Scenario | 验证目标 | 业务价值 |
|----------|---------|---------|
| 1 正常访客 | 看到人不报警（默认抑制）| 避免噪音 / 防止用户麻木 |
| 2 异常停留 | LongDurationRule → LOW/NOTIFY_FAMILY | 老人独自在家异常停留 |
| 3 重复访问 | RepeatVisitRule → LOW/NOTIFY_FAMILY | 多次踩点可疑访客 |
| 4 高风险组合 | CompositeRule → HIGH/ESCALATE_COMMUNITY | 比赛 Demo 核心链 |
| 5 误报抑制 | 白名单边界（不升级到 HIGH） | 快递员 / 物业常见误报 |
| 6 重复消息 | ActionExecutor 幂等（warning_id 去重） | MQTT ACK 丢失不重复派单 |

### 决策 3：状态机独立 + 互不污染必须验证

Owner 强调"状态描述决策生命周期，不描述执行结果"。这意味着：

- **WarningEvent.status**（CREATED/PENDING/CONFIRMED/RESOLVED/REJECTED）是决策层语义
- **ActionCommand.status**（PENDING/RETRYING/DONE/FAILED/GIVEN_UP）是执行层语义
- 两个状态机独立演化，通过 ActionExecutor 协调但状态空间完全分离

集成测试必须验证：改 cmd.status 不影响 warning.status；warning 翻 REJECTED 时 cmd 仍为 FAILED。

### 决策 4：故障注入是必含项

P0-9 Owner 三大必验证中两条是故障类（"失败保护" + "重复执行"）。集成验证必须验证：

- Publisher 失败 → Warning 保持 PENDING（不丢）→ meta.dispatch_error 记录原因
- 重试成功 → CONFIRMED + DONE
- 重试耗尽 → REJECTED + GIVEN_UP
- 重复 execute → 幂等（publish_count 不变）
- 边界数据（duration=0）→ 不产生错误 Warning

### 决策 5：CAVIAR 真实视频流必须跑通

单模块单测用 mock 数据没问题，但系统级验证必须用真实视频流。复用现有
`tests/fixtures/doorway/` 三个 CAVIAR 场景：

- OneStopEnter1cor（单人进门）
- OneLeaveShopReenter1cor（离开再返回）
- Meet_WalkTogether1（两人同行）

跑 frame → ActionCommand 全链路，断言：执行无异常 + 无字段污染 + publisher/notifier
调用与 commands 数量一致 + DONE command 引用有效 warning_id。

### 决策 6：集成测试报告必须可审计

放在 `docs/test-report/P0-integration-validation.md`，包含：

- 执行摘要（绿/红/异常数）
- 6 个 Golden Scenarios 结果表
- 状态机验证结果（双状态机独立）
- 故障注入结果
- CAVIAR 端到端结果
- 模块职责边界验证（不读 / 不产生 / 不执行 / 不判定）
- 测试覆盖矩阵（每个模块在三个测试层级都有覆盖）
- 已知限制与 v1 路径
- 进入 P0-10 的准入条件

## 影响

### 正面

- P0-10 装配阶段如果出问题，可以**确定是装配问题而不是逻辑问题**（已在本报告充分验证逻辑）
- 6 个 Golden Scenarios 是**可复用的回归用例** —— 后续模块修改后跑一次即可
- CAVIAR 端到端报告可作为**比赛 Demo 的功能清单**

### 成本

- 集成测试运行时间：~17s（274 测试总计 ~70s）
- 维护成本：单测 256 + 集成 18 + CAVIAR 3 = 274 条；新增模块需同步补集成测试

## 验收

- `pytest` **274 全绿**（之前 256 + 集成 18）
- `ruff` 全绿
- CAVIAR 三个场景端到端跑通（无 exception）
- 6 个 Golden Scenarios 全绿
- 状态机双独立验证通过
- 故障注入（失败 / 重复 / 数据缺失）全绿
- 业务判定字段污染 = 0
- 集成测试报告 `docs/test-report/P0-integration-validation.md` 已发布

## 后续

- P0-10 装配联调：本 PR 准入条件已满足，详见 P0 Integration Validation 报告 §9
- 集成测试作为**回归基线**：每个新 PR 必须保证 274 测试不退化
- v1 路径：替换 MockPublisher/MockNotifier + 持久化幂等（Redis/SQLite）—— 需新开 ADR-0013 评审
