# SilverShield · Home 感知模块 — 冻结契约（Developer Contracts）

> 本文档是**冻结契约的开发者视图**，定义哪些接口 / 字段 / 装配方式「不可破坏」。它是 `docs/API_REFERENCE.md` 的契约底座：
> 接 Dashboard / 设备 / Agent 时，凡本文档标 `[稳定]` 的，都不要改签名或字段。
>
> - 决策背景见 **ADR-0014「三级冻结治理」**（随 PR #26 合入）。
> - 测试保障见 `tests/contract/`（PR #27，77 例攻击性契约测试）。

---

## 0. 为什么需要冻结契约

项目已从「算法验证阶段」进入「平台化阶段」。最大风险不再是算法错误，而是：

- 新成员不知道哪些接口是稳定入口 → 绕过架构 → 架构漂移；
- 不知道哪些模块可替换、哪些文件禁止直接依赖；
- 接 Dashboard / 设备 / Agent 时容易越过边界。

三级冻结 + Freeze Gate 把「口头约定」升级为**可执行、可测试**的契约纪律。

---

## 1. 三级冻结（Frozen Levels）

### Level 1 — Schema Contract（数据契约）

**冻结对象**（5 类对外消息）：

- `VisitorEvent`（`analysis/event.py`）
- `PerceptionEvent`（`analysis/perception.py`，**唯一权威**）
- `WarningEvent`（`analysis/warning.py`）
- `ActionCommand`（`action/command.py`）
- `Envelope`（事件上报信封，见 `docs/06_api_contract.md`，Phase 1 落地）

**冻结内容**：

- 字段名 / 类型 / 语义；
- 时间格式（全部 UTC timezone-aware，naive datetime 拒绝）；
- 枚举值：
  - `EventType` 5 类：`visit_normal` / `visit_pending_verify` / `abnormal_dwell` / `repeat_visit` / `high_risk_approach`（`core/event.py`）
  - `risk_level` 3 类：LOW / MEDIUM / HIGH
  - `recommended_action` 3 类：MONITOR / NOTIFY_FAMILY / ESCALATE_COMMUNITY
  - `command_type` 3 类：LOG_ONLY / SEND_FAMILY_MESSAGE / CREATE_COMMUNITY_TASK
  - `WarningEvent.status` 5 类：CREATED / PENDING / CONFIRMED / RESOLVED / REJECTED
  - `ActionCommand.status` 5 类：PENDING / DONE / FAILED / RETRYING / GIVEN_UP

**允许**：

- 新增 **optional** 字段（须 ADR + `schema_version` 评审 + 加 schema 测试）；
- `meta` 透传扩展（但禁止出现业务判定黑名单字段，见 §3）。

**禁止**：

- 删除 / 改名 / 改类型已有字段（BREAKING）；
- 新增「诈骗 / fraud」类字段（模块边界铁律，ADR-0001）。

**变更流程**：先改 `docs/07` + `docs/06` → 升 `schema_version` → **Owner review** → 加 schema 测试 → 提 PR。

---

### Level 2 — Interface Contract（接口契约）

**冻结对象**（ABC / Protocol 的方法签名 + 抽象语义 + 异常约定）：

| 接口 | 位置 | 说明 |
| --- | --- | --- |
| `FrameSource(ABC)` | `ingestion/frame_source.py` | 视频源；`__iter__() -> (ts, frame)` |
| `Detector(ABC)` | `detection/detector.py` | `detect(frame) -> DetectionResult` |
| `Rule(ABC)` / `CompositeRule(ABC)` | `analysis/rule.py` | `evaluate(ctx, risk) -> List[RuleResult]` |
| `DecisionPolicy(ABC)` | `analysis/decision_policy.py` | `decide(events, ctx) -> Optional[WarningEvent]` |
| `MQTTPublisher(Protocol)` | `action/publisher.py` | `publish(topic, payload) -> bool`（不抛） |
| `NotificationAdapter(Protocol)` | `action/notifier.py` | `notify_family / notify_community -> bool`（不抛） |
| `Publisher(ABC)` | `output/publisher.py` | `publish(event: PerceptionEvent) -> None` |

**冻结内容**：方法签名 + 抽象语义 + 异常约定（Protocol 返回 `bool` 而非 `raise`，重试 / 幂等由调用方负责）。

**允许**：新增实现类（如 `RTSPFrameSource` / `EZVIZFrameSource` / `MLDecisionPolicy`）。

**禁止**：改 ABC / Protocol 方法签名（BREAKING，须 ADR）。

> **注**：`VisitorTracker` / `FeatureExtractor` / `RuleEngine` / `DecisionEngine` / `ActionExecutor` / `ActionDispatcher` 目前是**具体类**（非 ABC），但其公共方法签名已是**事实契约**，改动须评估所有调用方（契约测试见 `tests/contract/test_interface_contract.py`）。

---

### Level 3 — Runtime Assembly Contract（装配契约）

**冻结对象**：`PerceptionPipeline.from_settings()` 入口 + **Source → Pipeline → Consumer** 三段解耦。

**冻结内容**：

- 装配入口签名：`from_settings(settings, device_id="home_entry_01", ...) -> PerceptionPipeline`；
- Pipeline 仅依赖 `FrameSource` 抽象，**不感知**具体来源类型（CAVIAR / RTSP / EZVIZ 差异在 Source 内封装）。

**允许**：新增 `FrameSource` 实现；调整内部组件构造顺序（不破坏 `from_settings` 签名）。

**禁止**：在 Pipeline 之外另起装配入口绕过分层（如业务代码直接串 `RuleEngine → ActionExecutor`）。

---

## 2. Freeze Gate（打 RC tag 的验收门禁）

进入 RC（如 `v0.1.0-mvp-rc`）前，须全部满足（源自 ADR-0014）：

1. **`tests/contract/` 全绿** —— Schema + Interface 不变量（攻击性契约测试）。
2. **无重复领域对象** —— `PerceptionEvent` 仅 `analysis/perception.py`；`EventType` 仅 `core/event.py`。
3. **无 legacy import** —— 无代码指向已删除模块（`analysis/rules.py` / `core/pipeline.py` 等）。
4. **ABC 唯一权威** —— `FrameSource` 仅 `ingestion/frame_source.py`。
5. **配置校验生效** —— `core/config.py` 拒绝负值 / NaN / 范围越界 / 非法枚举 / bool 误传。
6. **`ruff check src tests` 无 error**。
7. **集成测试全绿** —— `tests/test_*` + P0 Integration Validation（详见 `docs/test-report/P0-integration-validation.md`）。

---

## 3. 模块边界铁律（黑名单字段）

以下字段**严禁**出现在任何 `PerceptionEvent` / `WarningEvent` / `ActionCommand` 的字段或 `meta`（构造期即抛 `ValueError`）：

```
fraud_result  fraud_probability  is_fraud  is_scammer  is_criminal
verdict  final_decision  crime_probability  guilt_score  arrest_probability  deception_score
```

> 本模块**只输出标签 / 事件**，绝不直接输出「诈骗人员」结论。是否诈骗由中心综合语义 / 物品 / 历史画像判定（ADR-0001）。

---

## 4. 契约测试（攻击性保障）

| 文件 | 覆盖 |
| --- | --- |
| `tests/contract/test_schema_contract.py` | 5 类消息字段 / 枚举冻结 + 禁止判定字段 + 无重复领域对象 |
| `tests/contract/test_interface_contract.py` | 接口签名冻结 + `from_settings` 入口 + `FrameSource` ABC |
| `tests/contract/test_state_machine_contract.py` | `WarningEvent` 状态机转移冻结（禁 CREATED→RESOLVED） |
| `tests/contract/test_input_attack_contract.py` | 空视频 / 时间倒流 / 脏输入 / 通道失败 / 重复幂等 |
| `tests/contract/test_config_contract.py` | 配置攻击（负值 / NaN / 范围 / 枚举 / 类型 / bool / 默认值） |

---

## 5. 版本策略

- 当前 MVP 处于「冻结准备」阶段（P0-10.5）。合入 ADR-0014 + `tests/contract/` + 通过 Freeze Gate 后，由 **Owner** 打 `v0.1.0-mvp-rc`。
- 字段新增走 `schema_version` + `meta` 逃生舱（详见 ADR-0014）：小扩展进 `meta`，结构性变更升版本号。

---

## 6. 相关文档

- `docs/API_REFERENCE.md` —— 公共 API 表面（怎么接）
- `docs/ARCHITECTURE.md` —— 系统架构总览
- `docs/02_architecture.md` —— 分层设计详述
- `docs/06_api_contract.md` / `docs/07_event_schema.md` —— 对外契约与事件字段
- `docs/ADR/0014-freeze-governance-three-levels.md` —— 冻结治理决策（PR #26）
