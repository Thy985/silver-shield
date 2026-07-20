# ADR-0014: 契约冻结治理 —— 三级冻结定义 + Contract Test + 版本策略

- 状态：Proposed（待 Owner 合并即 Accepted）
- 日期：2026-07-20
- 决策者：Owner
- 相关：ADR-0005（Schema/MQTT 契约稳定）、ADR-0007~0013、`docs/06_api_contract.md`、`docs/07_event_schema.md`、`docs/08_roadmap.md`（P0-10.5）、PR #24 / #25

## 背景（Context）

P0-10（ADR-0013）已完成：7 层链路装配跑通，`PerceptionPipeline.from_settings()` 成为系统入口，289 测试全绿，CAVIAR 三场景端到端 EXIT=0。

此时项目最大的风险**不是功能不足，而是"进入展示开发（Dashboard / 三端 Demo / 设备接入）后，为赶进度快速修改，导致架构腐化"**。典型比赛项目的第二阶段常见退化路径：

```
第一阶段：架构清晰 + 测试完整
        ↓（为赶 Demo）
第二阶段：前端直连模型 / 后端绕过接口 / 规则写死 / 字段乱加
        ↓
最终：展示能跑，但系统失去一致性，无法维护、无法讲清边界
```

ADR-0005 已确立"事件 Schema 与 MQTT 契约是稳定对外接口"，但它是**口头约定级**的，缺少：
1. 分级定义（哪些绝对不能动、哪些是接口层、哪些是装配层）；
2. 可执行的守护（Contract Test）；
3. 变更时的版本语义（什么改动升哪一位版本号）。

本 ADR 把"稳定契约"从口头约定升级为**可执行的三级冻结治理**。

> **诚实前提**：调研发现当前代码存在 3 处契约缺口（见 §冻结前置条件）。若不先修复就宣称"已冻结"，冻结的将是"两个互相冲突的定义"。因此本 ADR 同时定义**冻结前置条件**，作为打 RC tag 的强制门禁。

## 决策（Decision）

不采用简单的"冻结 / 不冻结"二元表述，而是定义**三个冻结等级**，每级明确"冻结什么 / 允许什么 / 禁止什么"，并以 Contract Test 守护、以版本策略管理其演进。

---

### Frozen Level 1 · Schema Contract（数据契约 —— 必须冻结）

**对象**（下游依赖直接消费的数据对象）：

| 对象 | 文件 | 状态 |
| --- | --- | --- |
| `VisitorEvent` | `analysis/event.py` | 已实现 · 冻结 |
| `PerceptionEvent` | `analysis/perception.py`（**唯一权威版**，见前置条件 #1） | 已实现 · 冻结 |
| `WarningEvent` | `analysis/warning.py` | 已实现 · 冻结 |
| `ActionCommand` | `action/command.py` | 已实现 · 冻结 |
| `Envelope` | `output/`（MQTT 上报信封） | **计划态**（见前置条件 #3），Phase 1 落地后纳入冻结 |

**冻结（改动 = BREAKING，必须 Owner 评审 + 升 MAJOR）**：
- 字段名称（`visitor_id` 不得改名 `person_id` —— 下游依赖会断）
- 字段类型
- 字段语义
- 时间格式：**UTC timezone-aware，ISO 8601**（所有 datetime 字段 `__post_init__` 已强制 tz-aware）
- 枚举取值（下表全部字面量）

**枚举冻结清单（权威取值，任何一项删除/改名 = BREAKING）**：

| 枚举 | 文件 | 冻结取值 |
| --- | --- | --- |
| `EventType` | `core/event.py` | `visit_normal` / `visit_pending_verify` / `abnormal_dwell` / `repeat_visit` / `high_risk_approach` |
| `WarningEvent.status` | `analysis/warning.py` | `CREATED` / `PENDING` / `CONFIRMED` / `RESOLVED` / `REJECTED` |
| `WarningEvent.risk_level` | `analysis/warning.py` | `LOW` / `MEDIUM` / `HIGH` |
| `WarningEvent.recommended_action` | `analysis/warning.py` | `MONITOR` / `NOTIFY_FAMILY` / `ESCALATE_COMMUNITY` |
| `ActionCommand.status` | `action/command.py` | `PENDING` / `DONE` / `FAILED` / `RETRYING` / `GIVEN_UP` |
| `ActionCommand.command_type` | `action/command.py` | `LOG_ONLY` / `SEND_FAMILY_MESSAGE` / `CREATE_COMMUNITY_TASK` |

**允许（向后兼容演进，升 MINOR）**：
- 新增 **optional 字段**（必须带默认值，旧消费方忽略即可）
- 通过 `meta: Dict[str, Any]` 逃生舱承载扩展信息

**逃生舱示例（允许）**：
```json
{
  "visitor_id": "0b1c...uuid",
  "duration_seconds": 120,
  "meta": { "camera_id": "001" }
}
```

**反例（禁止 —— 下游依赖会断）**：把 `visitor_id` 改成 `person_id`；把 `duration_seconds` 从 `float` 改 `str`；删除 `high_risk_approach` 枚举值。

> **注意**：`meta` 是逃生舱不是垃圾桶。凡是**下游需要稳定依赖**的字段，不能长期塞在 `meta` 里，应走"新增 optional 字段 + 升 MINOR"的正式流程；`meta` 只承载实验性/调试性/单端私有的信息。

---

### Frozen Level 2 · Interface Contract（接口契约 —— 冻结）

**核心原则：实现可以变化，接口不能随便变化。**

**对象（ABC / Protocol）与冻结签名**：

| 接口 | 文件 | 冻结签名 | 异常语义 |
| --- | --- | --- | --- |
| `Detector` | `detection/detector.py` | `detect(frame: np.ndarray) -> DetectionResult` | 单帧结构化结果 |
| `Rule` / `CompositeRule` | `analysis/rule.py`（**唯一权威版**，见前置条件 #2） | `evaluate(ctx, risk) -> List[RuleResult]` | 只出 RuleResult，不跳级 |
| `DecisionPolicy` | `analysis/decision_policy.py` | `decide(perception_events, ctx) -> Optional[WarningEvent]` | 空/普通访问返回 None |
| `MQTTPublisher` | `action/publisher.py` | `publish(topic, payload) -> bool` | **不抛异常**（失败用 `False`）/ 不做幂等 / 不做重试 |
| `NotificationAdapter` | `action/notifier.py` | `notify_family(contact, msg) -> bool` / `notify_community(endpoint, task) -> bool` | 失败用 `False` |
| `Publisher` | `output/publisher.py` | `publish(event: PerceptionEvent) -> None` | 失败缓冲不丢事件 |
| `EvidenceCollector` / `EvidenceStorage` | `evidence/` | `collect(...) -> list[EvidenceRef]` / `save(data, name) -> str` | — |
| `NowProvider` / `TickableNowProvider` | `runtime/pipeline.py` | `__call__() -> datetime` / `tick(dt) -> None` | runtime_checkable |

**冻结**：方法名 / 输入输出类型 / 异常语义（尤其"用 bool 表达失败还是抛异常"这一约定绝不能悄悄改）。

**不冻结（自由替换）**：接口背后的实现 —— YOLO / OpenCV / TensorRT / 云端 API 全部可替换。例如 `Detector` 从本地 YOLO11n 换成云端推理，只要仍返回 `DetectionResult` 即合规。

---

### Frozen Level 3 · Runtime Assembly Contract（运行时装配契约 —— 冻结）

`PerceptionPipeline.from_settings(settings, ...)` 已是系统唯一入口。三段式数据流必须保持不变：

```
Source  →  Pipeline  →  Consumer
(帧从哪来)  (7 层处理)   (Publisher/Notifier)
```

**冻结**：
- 装配入口 `PerceptionPipeline.from_settings()` 的语义（从配置一键构建 7 层）；
- `Source → Pipeline → Consumer` 三段解耦：**Pipeline 不感知 Source 的具体类型**。
  - Demo：CAVIAR fixtures（`read_caviar_frames`）
  - 生产：`RTSPSource` / `EZVIZSource`（Phase 1 P0-12）
  - Pipeline 对以上一律无感知 —— 换源不改 Pipeline。

**当前缺口（前置条件 #4）**：`ingestion/frame_source.py::FrameSource` 是**具体类**，尚无抽象 `Source`/`FrameSource` 接口（ABC/Protocol）。Level 3 真正可冻结的前提是先抽出该接口，让 CAVIAR / RTSP / EZVIZ 三种源实现同一契约。

---

### Contract Test —— 攻击性测试作为契约测试

**定位区分**：
- **普通测试**：验证正常路径（输入 A → 输出 B）。
- **Contract Test（攻击性测试）**：验证系统面对现实世界的**异常输入时是否保持边界**。更接近生产。

摄像头系统在生产中**必然**遇到 NTP 同步跳变、设备重启、缓存恢复、网络抖动。Contract Test 就是把这些异常前置到 CI，守护三级冻结不被"能跑就行"的改动悄悄破坏。

**Contract Test 矩阵**（P0-10.5 交付 `tests/contract/`）：

| 类别 | 攻击输入 | 期望的边界行为（断言） | 守护等级 |
| --- | --- | --- | --- |
| 时间异常 | Frame1=10:00:10，Frame2=10:00:05（时间倒流 / NTP 回跳） | **不得**产生 `duration_seconds < 0`；不污染数据（`VisitorEvent.__post_init__` 已守 `duration>=0` 且 `leave>=enter`） | L1 |
| 脏输入 | `visitor_id=""`、`duration_seconds="abc"` | **不得**进入 `RiskFeature`（否则后续 ML/Rule 全污染）；schema 校验拒绝 | L1 |
| 高频压力 | 1 秒 100 帧连续 `visitor enter` | **不得**生成 100 个 `VisitorEvent`；跟踪状态机去重（同一 track 只在 active→left 翻转时出一个事件） | L2/L3 |
| 状态机攻击 | `WarningEvent`：`CREATED → RESOLVED`（跳过 PENDING/CONFIRMED） | **必须拒绝**非法转移（合法：`CREATED→{PENDING,REJECTED}`…，见 `command.py::WARNING_TRANSITIONS`） | L1 |
| 配置攻击 | `long_duration_seconds: -100` 或 `NaN` | 系统**必须明确报错或拒绝启动，不得静默运行**（当前无校验，见前置条件 #5，需补 `field_validator`） | L1/L3 |
| 通道失败 | Publisher/Notifier 返回 `False` | `WarningEvent` 保持 `PENDING` 等待重试，失败不丢；同 `warning_id` 重放幂等（`publish_count=1`） | L2 |
| 边界空源 | 空视频 / 摄像头断开（EOF） | `run([])` 返回空 `RunSummary`，优雅结束，`errors=0` | L3 |

Contract Test **与实现解耦**：只断言契约（字段/枚举/状态机/异常语义/装配边界），不依赖具体算法。因此替换 YOLO / Publisher 实现时，Contract Test 应当继续通过。

---

### 版本策略（Version Policy）

**SemVer 映射到三级冻结**：

| 变更 | 版本位 | 例 |
| --- | --- | --- |
| 破坏 Level 1 数据契约（改名/改类型/改语义/删枚举） | **MAJOR** | `visitor_id`→`person_id` |
| 破坏 Level 2 接口签名 / 异常语义 | **MAJOR** | `publish` 从 `->bool` 改为抛异常 |
| 新增 optional 字段 / 新增枚举值 / 新增接口实现 | **MINOR** | 新增 `meta.camera_id` 约定字段 |
| 实现内部变化（算法/阈值默认值/性能） | **PATCH** | YOLO11n → 云端推理 |

- **`schema_version`**：当前代码未落地（仅文档策略）。随 `Envelope` 于 Phase 1 落地时，作为 payload 顶层字段承载 Level 1 契约版本。
- **MVP Release Candidate**：满足全部冻结前置条件后，从干净 `main` 打 tag `v0.1.0-silver-shield-mvp`，作为三级契约的第一个冻结基线。

---

## 冻结前置条件（Freeze Preconditions —— 打 RC tag 前必须清零）

以下缺口若不先修，"冻结"将冻结到不一致的定义上。列为 P0-10.5 的强制门禁：

1. **`PerceptionEvent` 双定义冲突**：`core/event.py`（少字段、`event_type` 为枚举）与 `analysis/perception.py`（多字段、`event_type` 为 str、含 `visitor_id/source_video/created_at`）并存。Pipeline 实际依赖后者，`output/schemas.py` 却再导出前者。→ **定 `analysis/perception.py` 为唯一权威，删/收敛另一份。**
2. **`Rule` 基类双定义冲突**：`analysis/rule.py`（现役：`evaluate(ctx, risk)->List[RuleResult]`）与 `analysis/rules.py`（残留：`evaluate(ctx)->PerceptionEvent|None`）签名不兼容。→ **废弃 `rules.py`。**
3. **`Envelope` / `schema_version` 未落地**：代码无该类/字段。→ 本 ADR 明确标注为**计划态**；Phase 1（P0-12）落地后才纳入 Level 1 冻结。
4. **抽象 `Source` 接口缺失**：`FrameSource` 是具体类而非 ABC/Protocol。→ Level 3 冻结前抽出接口，供 CAVIAR/RTSP/EZVIZ 共同实现。
5. **配置无取值校验**：`RuleConfig.long_duration_seconds` 等阈值无 `>0` / 非 NaN 守卫，负值/NaN 会静默流入规则。→ 补 pydantic `field_validator`（配置攻击 Contract Test 守护）。

## 后果（Consequences）

**正面**：
- 变更有明确红线，AI 协作者/人工都能自检"这个改动破坏了哪一级、该升哪位版本号"；
- Contract Test 把生产级异常前置到 CI，杜绝"能跑就行"式腐化；
- 三段式装配契约保证 Demo 源与生产源可无痛切换（P0-12 设备接入不改核心链路）。

**负面 / 技术债**：
- 冻结前置条件（5 项）需先投入清理，短期内不产出"新功能"；
- Contract Test 维护成本；契约演进需走版本流程，比"直接改"慢 —— 这是刻意的摩擦。

**后续动作**：见 `docs/08_roadmap.md` 新增的 **P0-10.5 冻结治理 → P0-11 产品层 → P0-12 设备适配** 顺序。核心纪律：**P0-11/P0-12 一律通过已冻结的契约/Protocol 接入，不修改核心链路。**

## 替代方案（Alternatives）

- **简单"冻结 / 不冻结"二元**：否决 —— 太粗，要么锁死无法合理扩展，要么形同虚设。
- **不冻结、靠团队自觉**：否决 —— 正是本 ADR 要防的腐化路径。
- **一次性冻结全部（含未实现的 Envelope）**：否决 —— 等于"冻结空气"，与代码现状不符，反而损害契约可信度。故对未落地对象明确标"计划态"。
