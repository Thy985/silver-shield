# 08 · 研发路线（Roadmap）

对齐《架构设计完善版》第十六章"分阶段研发路线"。本仓库聚焦其中**阶段 1（统一设计）**
与**阶段 4（萤石接入）**在 Home 端的落地；阶段 2/3/5 的 AI/业务/协同部分由其他成员负责，
本模块通过稳定契约与之联调。

## 8.1 全局阶段（引用终稿）

| 阶段 | 目标 | 本模块参与 |
| --- | --- | --- |
| 1 统一设计 | 场景/标签/阶段/接口/萤石权限 | ✅ 本仓库已交付脚手架 + 契约 |
| 2 模拟闭环 | 不依赖真实设备跑通风险事件 | ✅ 提供模拟帧源 + fake 输出 |
| 3 核心 AI | 意图/阶段/门前停留/评分 | 部分（门前规则+ML 信号） |
| 4 萤石接入 | 设备状态/视频/事件/授权查看 | ✅ 本模块主责 |
| 5 Trust 与协同 | 柔性提醒/家属核实/社区升级 | 接口侧（control 通道） |
| 6 测试交付 | 消融/隐私/文档/演示 | ✅ 证据/可复现 |

## 8.2 第一阶段（MVP）任务拆解 —— D 交付物

> 优先级 P0（比赛必交）/ P1（强建议）/ P2（增强）。每项给出产出与验收。

### P0-1 工程脚手架（已完成本次）
- 产出：目录树、`pyproject`、依赖、`config`、`docs`、契约代码、git 初始化。
- 验收：`pytest` 空跑通过；`.gitignore` 生效（无凭证入库）。

### P0-2 萤石稳健取流（ingestion）
- 任务：在 `ezviz_client`/`frame_source` 基础上，支持 **RTSP 优先 + HLS 回退**，
  参数化 `quality/channel`，断流指数退避重连（沿用 `prototypes/` 已验证逻辑）。
- 验收：模拟断网可自动恢复；输出 FPS/延迟基线报告（`scripts/eval_stream.py`）。

### P0-3 YOLO 人员/物品检测（detection）（✅ 已完成 · v0.1）
- 任务：实现 `YOLODetector`（ultralytics **YOLO11n**，CPU），1080p 帧**显式 resize 640×640**
  再推理，bbox 映射回原始帧坐标；仅第一阶段 4 类 `person/backpack/handbag/cell phone`
  （COCO 0/24/26/67）；模型惰性加载。
- 边界：输出 `DetectionResult`（事实），**不判诈骗、不输出 risk score、不调 LLM**。
- 验收：`tests/test_detector.py` 8/8 全过（含模型加载/空图/正常图/输出 Schema）；
  `ruff`/`compileall` 干净。
- 注：跟踪（`track_id`）本阶段**未开启**（`enable_track=False`），留待 P0-5。

### P0-4 视频流稳定化 + FPS Benchmark（benchmark）【下一步】
- 任务：建立 `benchmark/yolo_speed.py`，实测真实运行
  `萤石流 → OpenCV → YOLO → DetectionResult` 的端到端性能，量化：
  | 指标 | 目标 |
  | --- | --- |
  | 输入 FPS | 10–15 |
  | YOLO 推理耗时 | <100ms |
  | 端到端延迟 | <300ms |
  | 连续运行 | ≥30 分钟（稳定性） |
  输出：Camera FPS / Inference FPS / Average latency / CPU usage / Memory。
- 价值：答辩与"门前踩点识别"创新点落地的硬数据；为 P0-5 跟踪的帧一致性要求定基线。
- 验收：可复现基准脚本；在目标硬件上产出上述指标报告。

### P0-5 目标跟踪（Tracker → VisitorTrack）【✅ 已完成】
- 任务：开启跨帧跟踪，把 YOLO 的 frame-level `track_id` **封装成银龄盾自己的 `VisitorTrack` 领域对象**
  （访客生命周期状态），而非仅调用 ByteTrack。
- 方案（见 Owner 决策）：固定摄像头 / 单区域 / CPU / 停留分析 → **ByteTrack**（轻量、无 ReID，
  BoT-SORT 的 ReID 价值不在 MVP）。`model.track(persist=True)` 保证 `YOLODetector` 实例在相机循环里
  **复用**、跨帧 ID 稳定（不能每帧 new model）。
- 交付：
  - `detection/schemas.py`：`VisitorTrack` 领域对象（track_id / first_seen / last_seen / frame_count /
    bbox / confidence / status∈{active,left}，含 to_log 供结构化日志）。**只代表当前摄像头会话内的同一人，
    不引入跨天身份**（跨天重识别属 P0-6/P1）。
  - `detection/tracker.py`：`VisitorTracker`（职责单一：仅维护在场/离场状态，不做风险/重复/陌生人判断）；
    输入单帧 `Detection` 列表、输出活跃 `VisitorTrack`；离场判定用 `absence_gap_s` 兜底漏检闪烁。
  - `config`：新增 `tracking.enabled` / `tracking.algorithm=bytetrack`（保留 `enable_track`/`tracker` 向后兼容）。
- 验收：真实 `tests/fixtures/person.jpg` 链路验证 person 检出 + 跨帧 `track_id` 一致；纯单测覆盖
  在场/离场/重访/多访客/无 ID 跳过/非法 gap。基准对比：**开启 ByteTrack 开销 ≈ 0ms（<10ms 目标）**，
  ID 稳定性连续。
- 注意：本沙箱 CPU 弱于 P0-4 目标硬件，绝对推理/FPS 数值受环境限制；趋势结论（480 实时 + 跟踪零开销）不变。

### P0-6 生成 VisitorEvent（事实事件层）【✅ 已完成】
- 任务：在 `DetectionResult` + `VisitorTrack` 之上生成第一个对银龄盾有意义的数据对象
  `VisitorEvent`：`event_id / visitor_id / enter_time / leave_time / duration_seconds / source_video / created_at`。
- 边界（见 ADR-0007 · **事实事件层 vs 风险语义层**）：仍只是"有人在门口停留 X 秒"的事实，
  **不包含** `event_type` / `score` / `risk_level` / `visit_type` / `is_suspicious` / `repeat_count`
  / `is_odd_hour` / `evidence` 等任何业务判断字段 —— 那些是 P0-7 Rule Engine + P0-8 决策层的事。
- 触发时机：`VisitorTrack.status` 从 `active` 转 `left`（absence_gap 兜底）时，由
  `VisitorEventBuilder` 生成；同一 track 离场后**重新进入 → 再离场**会生成第二个事件。
- 交付：
  - `analysis/event.py`：`VisitorEvent` 领域对象（UUID event_id + structlog-safe `to_dict` /
    `to_json`，无 datetime 序列化问题）。
  - `analysis/event_builder.py`：`VisitorEventBuilder` 包裹 `VisitorTracker`，监听
    `active→left` 状态翻转，生成 `VisitorEvent`；含 `pending()` / `ack()` 供 P0-9 行动层失败重发。
  - `tests/test_event.py`：6 个 `VisitorEvent` 字段/序列化/边界测试 + 7 个 `VisitorEventBuilder`
    状态机测试（enter/leave/track interruption/revisit/multi-visitor/source/ack/reset）
    + 1 个 CAVIAR `OneStopEnter1cor` 端到端真实链路。
- 验收：`pytest` 51 全绿；`ruff` 全绿；CAVIAR 真实监控数据端到端跑通；
  `test_no_business_judgment_fields` 守住 P0-7 边界（强制不含任何业务字段）。

### 后续横向能力（P0-7+，按原架构补全）
> 按 Owner P0-6 review 决策，原"P0-7 门前规则"拆为两步：**P0-7a Feature Extraction + P0-7b Rule Engine**。
> 反对"规则层直接读 event"模式 —— 阈值变了要回头改 event，违反 ADR-0005 契约稳定。

### P0-7a Feature Extraction（结构化数值信号层）【✅ 已完成】
- 任务：从 `VisitorEvent` 流提取结构化数值特征 `DurationFeature` / `VisitFrequencyFeature` /
  `TimeFeature` / `TrajectoryFeature`，聚合成 `RiskFeature` 供 P0-7b Rule Engine 消费。
- 边界（见 ADR-0008）：**Feature 是"被测量的数值"，不是"判断的标签"**。禁止出现
  `is_long_visit` / `is_odd_hour` / `is_suspicious` / `risk_level` / `score` /
  `visit_type` / `event_type` / `is_repeat` 等任何判断/阈值字段 —— 留给 P0-7b Rule Engine。
- 允许的字段类型：数值（int/float）、类别（enum/str）、日历事实（`is_weekend` 由
  `day_of_week in (5, 6)` 派生，本身是事实不是判断）。
- 交付：
  - `analysis/feature.py`：`Feature` 基类 + 4 个具体 Feature + `RiskFeature` 聚合容器。
    所有 datetime 字段 UTC timezone-aware（`__post_init__` 校验，naive 拒绝）；
    `to_dict()` structlog-safe；`RiskFeature` 4 个 Feature 可空（缺某 Feature 时 Rule 跳过）。
  - `analysis/feature_extractor.py`：4 个具体 `*FeatureExtractor`（纯函数为主，
    `VisitFrequencyFeatureExtractor` 需滑动窗口）+ `FeatureExtractor` 编排器
    （维护 `visitor_id → 历史事件 deque`，默认 30 分钟窗口 / 上限 100 条/visitor）。
  - `tests/test_feature.py`：36 个测试（4 个 Feature 子类字段 + 4 个 Extractor 纯函数 +
    RiskFeature 聚合 + FeatureExtractor 编排 + 滑动窗口 + 契约边界
    `test_feature_contract_boundary` + CAVIAR `OneStopEnter1cor` 端到端）。
- 验收：`pytest` 92 全绿（之前 56 + 36）；`ruff` 全绿；CAVIAR 真实链路
  `detector → tracker → event → feature` 端到端跑通。
- Trajectory 占位：MVP 单摄像头 `bbox_center_displacement=0` / `segment_count=1`，
  P1 多摄扩展时按 schema_version 评审（ADR-0005）。

### P0-7b Rule Engine（风险语义层）【✅ 已完成】
- 任务：消费 `RiskFeature`（P0-7a），按 4 条基础 Rule + 1 条 CompositeRule 输出
  `PerceptionEvent`（§7.2 5 类）+ `score`；`CooldownGate` 防重复触发；`ThresholdConfig` 配置化阈值。
- 边界（见 ADR-0009 6 条决策）：
  - Rule 消费 `RiskFeature`，**不**直接读 VisitorEvent
  - Rule 输出 `PerceptionEvent`，**不**直接输出 WarningEvent（不跳级）
  - `score` = 规则命中强度（0-1），**不是诈骗概率**
  - CompositeRule 消费 `RuleResult[]` 组合（不复算 Feature）
  - `CooldownGate` 防同 (visitor_id, rule_name) 短时重复触发（30fps 关键防御）
  - 阈值与权重全部在 `ThresholdConfig` 配置化（不硬编码）
- 5 条 Rule 状态（按 §7.2 5 类对齐）：
  - `LongDurationRule` → `abnormal_dwell`（duration > 300s）✅
  - `RepeatVisitRule` → `repeat_visit`（visits > 3）✅
  - `OddHourRule` → `visit_normal` + `is_odd_hour=true` 叠加标记 ✅
  - `PendingVerifyRule` → `visit_pending_verify` ⏸ **留接口不实现**（需 Whitelist 数据源，v2 接入）
  - `HighRiskApproachRule`（Composite）→ `high_risk_approach`（长+重复+异常时间 组合）✅
- 交付：
  - `analysis/rule.py`：`Rule` 抽象基类 + `CompositeRule` 基类 + `RuleContext` + `RuleResult` 领域对象
  - `analysis/perception.py`：`PerceptionEvent` 领域对象（§7.2 字段 + `event_type` 枚举校验 + `meta.rule` 必填）
  - `analysis/cooldown.py`：`CooldownGate` 状态机（`INACTIVE` → `ACTIVE` → `COOLDOWN` → 循环 + `reset_gap` 重置）
  - `analysis/rule_engine.py`：`ThresholdConfig` + `WhitelistProvider` protocol + 4 条基础 Rule + 1 条 CompositeRule + `RuleEngine` 编排器
  - `tests/test_rule.py`：**40 个测试**（ThresholdConfig / RuleResult / 4 条基础 Rule 独立 / CompositeRule / CooldownGate 状态机 5 个转移 / PerceptionEvent 校验 / RuleEngine 编排含 Cooldown 抑制 / 契约边界 / CAVIAR 端到端）
- 验收：`pytest` **132 全绿**（之前 92 + 40）；`ruff` 全绿；CAVIAR 端到端 `detector → tracker → event → feature → rule → perception` 全链路跑通。

### P0-8 决策层（WarningEvent + DecisionPolicy）【✅ 已完成】
- 任务：消费 `PerceptionEvent[]`（P0-7b），按 `DecisionPolicy` 决策，输出 `WarningEvent`。
  决策层 **不**直接执行任何动作（MQTT / 通知 / 升级 → P0-9 行动层）。
- 边界（见 ADR-0010 5 条决策）：
  - `PerceptionEvent` **不**直接触发通知 → 必须先经 `DecisionEngine` → `WarningEvent`
  - `WarningEvent` 是决策层对象（`risk_level` + `recommended_action` + `status`），**不**含最终判定
  - `risk_level` 是**决策严重度**（LOW/MEDIUM/HIGH），**不是诈骗概率**
  - `DecisionPolicy` 独立于 `Rule`（不复算 Feature / 不重新组合 Rule），可替换为 ML/LLM
  - Action 执行（MQTT / 通知 / 升级）延迟到 P0-9 行动层
- 路由表（per-event `(level, action, reason)`）：
  - `high_risk_approach` → HIGH / `ESCALATE_COMMUNITY`（CompositeRule 产物）
  - `abnormal_dwell` → LOW / `NOTIFY_FAMILY`
  - `repeat_visit` → LOW / `NOTIFY_FAMILY`
  - `visit_pending_verify` → LOW / `MONITOR`
  - `visit_normal` + `is_odd_hour=true` → LOW / `MONITOR`（异常时段访问）
  - `visit_normal` 单独 → 抑制（不警告）
- 组合规则：max wins（HIGH + LOW = HIGH + HIGH 的 action；reason_summary 合并去重）
- 严格黑名单（决策层不做最终判定）：`fraud_result` / `fraud_probability` / `is_fraud` /
  `is_scammer` / `verdict` / `crime_probability` / `final_decision` / `guilt_score` / `arrest_probability`
- 交付：
  - `analysis/warning.py`：`WarningEvent` 领域对象（UUID warning_id + 3 类 risk_level +
    3 类 recommended_action + 5 类 status（CREATED→PENDING→CONFIRMED→RESOLVED/REJECTED，
    描述决策生命周期而非执行结果）+ UTC 强制 + 黑名单校验 + `to_dict()` structlog-safe）
  - `analysis/decision_policy.py`：`DecisionContext` + `DecisionPolicy` 抽象基类 +
    `RuleBasedDecisionPolicy`（routing_table 查表 + max wins 聚合 + 定制支持）
  - `analysis/decision_engine.py`：`DecisionEngine` 编排器（注入 elder_id + policy + now_provider）
  - `tests/test_warning.py`：**65 个测试**（WarningEvent 字段/UUID/UTC/枚举/黑名单 +
    DecisionPolicy 单事件/组合/抑制 + DecisionEngine 编排 + RuleEngine→DecisionEngine 集成 +
    决策层字段无业务判定 + CAVIAR 端到端）
- 验收：`pytest` **197 全绿**（之前 132 + 65）；`ruff` 全绿；CAVIAR 端到端
  `detector → tracker → event → feature → rule → perception → decision` 全链路跑通。

### P0-9 行动层（ActionCommand + ActionDispatcher + ActionExecutor）【✅ 已完成】
- 任务：消费 `WarningEvent`（P0-8），按 `ActionDispatcher` 路由 → `ActionCommand` →
  通过 `MQTTPublisher` / `NotificationAdapter` 执行。
  MVP 收敛：**不**接真实萤石 / **不**做完整 App / **不**做社区系统（先用 Mock 跑通骨架）。
- 边界（见 ADR-0011 5 条核心决策）：
  - `MQTTPublisher` / `NotificationAdapter` **Protocol** 接口（可替换；MVP Mock）
  - 幂等基于 `warning_id`（in-memory set；同 warning_id 重复 execute → publish 次数=1）
  - 失败可重试（`max_retries` 默认 3；总尝试 = 1+max_retries；Warning 失败时保持 `PENDING` 不丢）
  - 状态描述决策生命周期不描述执行结果（WarningEvent.status 5 类 + ActionCommand.status 5 类）
  - **不**直接调真实设备（MVP Mock；v1 接 paho-mqtt / 短信网关）
- 三大必验证（Owner 强调）：
  1. **消费正确**：`HIGH → ESCALATE_COMMUNITY` 真的走社区通道（`command_type=CREATE_COMMUNITY_TASK`）
  2. **幂等**：同 `warning_id` 重复 execute → `publisher.publish_count == 1`
  3. **失败保护**：publisher fail → `command.status=FAILED` + `warning.status=PENDING`（不丢）；
     超过 `max_retries` → `command.status=GIVEN_UP` + `warning.status=REJECTED`
- 路由表（per `WarningEvent.recommended_action`）：
  - `MONITOR`              → `LOG_ONLY`（仅记录，不下发）
  - `NOTIFY_FAMILY`        → `SEND_FAMILY_MESSAGE`（发短信/App 通知家属）
  - `ESCALATE_COMMUNITY`   → `CREATE_COMMUNITY_TASK`（创建社区工单）
- 严格黑名单（行动层不做最终判定）：与 WarningEvent 一致（fraud/verdict/crime_probability 等）
- 交付：
  - `action/command.py`：`ActionCommand` 领域对象（UUID command_id + warning_id 关联 +
    3 类 command_type + 5 类 status + UTC 强制 + 黑名单校验 + 状态翻转规则）
  - `action/dispatcher.py`：`DispatcherConfig` + `ActionDispatcher`（3 类路由 + family_contact 缺失降级）
  - `action/publisher.py`：`MQTTPublisher` Protocol + `MockPublisher`（写本地 JSONL + fail_next 模拟）
  - `action/notifier.py`：`NotificationAdapter` Protocol + `MockNotifier` + `FamilyContact`
  - `action/executor.py`：`ActionExecutor` 编排器（in-memory 幂等 + 失败重试 + 状态翻转）
  - `action/__init__.py`：模块导出
  - `tests/test_action.py`：**59 个测试**（ActionCommand 字段/UUID/UTC/枚举/黑名单 +
    状态翻转 + 3 类 Dispatcher 路由 + MockPublisher/Notifier 失败模拟 +
    Executor 3 类 action 执行 + **幂等测试** + **失败保护测试**（重试成功/重试耗尽）+ 警告无业务判定 +
    CAVIAR 端到端）
  - `docs/ADR/0011-action-layer-architecture.md`：5 条核心决策完整文档
  - `docs/ADR/README.md`：新增 ADR-0011 条目
- 验收：`pytest` **256 全绿**（之前 197 + 59 新增）；`ruff` 全绿；CAVIAR 端到端
  `detector → tracker → event → feature → rule → perception → decision → action` 全链路跑通

### P0-Integration 系统级冻结前验收【✅ 已完成 · 2026-07-19】
- 任务：在进入 P0-10 装配联调之前，把 P0-3~P0-9 模块串成完整链路做端到端验证，
  避免 P0-10 装配时混淆"逻辑问题"和"装配问题"。
- 触发：Owner P0-9 review 后明确"建议顺序：先做一次完整测试，再开 P0-10 装配联调"。
- 6 个 Golden Scenarios（系统级 e2e，详见 `docs/test-report/P0-integration-validation.md`）：
  1. **正常访客**（30s + 上午）→ 无 PerceptionEvent / 无 Warning / 无 Action（"看到人不报警"边界）
  2. **异常停留**（600s）→ `abnormal_dwell` → LOW / NOTIFY_FAMILY / SEND_FAMILY_MESSAGE
  3. **重复访问**（3 次 / 5 分钟间隔）→ `repeat_visit` → LOW / NOTIFY_FAMILY
  4. **高风险组合**（长停留 + odd_hour + frequency=3）→ CompositeRule → HIGH / ESCALATE_COMMUNITY / CREATE_COMMUNITY_TASK
  5. **误报抑制**（白名单命中）→ 不升级到 HIGH；publisher 调用 0 次
  6. **重复消息**（同 warning_id execute 5 次）→ `publisher.publish_count == 1`（幂等）
- 状态机完整验证：
  - WarningEvent：CREATED → PENDING → CONFIRMED（happy）+ PENDING → REJECTED（failure）
  - ActionCommand：PENDING → DONE / FAILED → RETRYING → DONE / FAILED → GIVEN_UP
  - **双状态机独立 / 互不污染**（改 cmd.status 不影响 warning.status，反之亦然）
- 故障注入：Publisher 失败 → Warning PENDING（不丢）+ retry → CONFIRMED / REJECTED
- CAVIAR 真实场景回归：OneStopEnter1cor / OneLeaveShopReenter1cor / Meet_WalkTogether1
  从 frame → ActionCommand 全链路跑通（无 exception + 无字段污染）
- 验收：`pytest` **274 全绿**（256 + 18 新增集成测试）；`ruff` 全绿；详见
  `docs/test-report/P0-integration-validation.md`；ADR-0012 决策固化
- **准入条件**：✅ 全部满足，可进入 P0-10 装配联调阶段

### P0-10 装配与联调（runtime/ 包 + DemoClock + Demo 端到端）【✅ 已完成 · 2026-07-20】
- 任务：新建 `src/home_perception/runtime/` 包，把 P0-3~P0-9 组件装配成可运行 Demo；
  接入 CAVIAR fixtures；实现启动 / 优雅关闭；Demo 汇总日志。
- 边界（见 ADR-0013 6 条决策）：**工程层问题**（"怎么启动系统"），不验证逻辑正确性
  —— 逻辑已由 P0 Integration Validation（274 测试）充分验证。
- 核心交付：
  - `PerceptionPipeline`（7 层装配器，`from_settings()` 从 YAML 一键构建）
  - `DemoClock`（模拟 2fps 时序，驱动 tracker 离场判定；`__call__()` 兼容 now_provider 约定）
  - `run_demo(settings)`（CAVIAR 三场景端到端 + SIGINT 优雅关闭 + 结构化汇总）
  - `PipelineMetrics` / `FrameResult` / `RunSummary`（可观测性数据对象）
- Demo 调优（`config/default.yaml` runtime/rule 段）：
  - `detector_conf: 0.10`（鱼眼俯拍小目标低阈值；class_filter 过噪声）
  - `long_duration_seconds: 1.5`（CAVIAR 短片 ~25s 总长适配）
  - DemoClock 设 **23:30 UTC**（OddHourRule 自然触发）
- 验收：
  - `scripts/run.py` EXIT=0：130 帧 / 114 检测 / 9 访客事件 / 12 感知事件 / 8 告警 / 8 指令 / **0 错误**
  - `pytest` **289 全绿**（274 prior + 15 runtime）；`ruff` 全绿
  - one_stop_enter: 8 events → 10 perception → 7 warnings → 7 commands (LOW/LOG_ONLY)
  - one_leave_reenter: 1 event → 2 perception → 1 warning → 1 command
  - meet_walk_together: 7 detections, tracking active（人物未离场 = 语义正确）
  - 详见 `docs/ADR/0013-p0-10-assembly-integration.md`
- Bug 修复记录：
  1. lifecycle.py try/for 缩进损坏（Edit 工具丢失缩进）→ 手动修正全块
  2. `DemoClock not callable` → 加 `__call__()` 兼容 now_provider() 约定
  3. 测试断言 long_duration_seconds == 300.0 → 更新为 1.5（匹配 Demo YAML）

### P0-10.5 架构冻结治理（ADR-0014 三级冻结）【✅ 已完成 · 2026-07-20】
- 任务：项目进入「平台化阶段」前，把"稳定契约"从口头约定升级为**可执行的三级冻结 + 冻结前置 + Freeze Gate**，防止展示开发期架构腐化。
- 交付（`docs/ADR/0014-freeze-governance-three-levels.md`，PR #26）：
  - Frozen L1（Schema）：VisitorEvent/PerceptionEvent/WarningEvent/ActionCommand/Envelope 字段名/类型/语义/时间格式/枚举冻结
  - Frozen L2（Interface）：Detector 等 ABC/Protocol 签名 + 异常语义冻结，实现可替换
  - Frozen L3（Runtime Assembly）：`PerceptionPipeline.from_settings()` 入口 + Source→Pipeline→Consumer 三段解耦
  - 5 项冻结前置（双定义/缺失接口/无校验等，由 P0-10.5.2 清零）+ Freeze Gate 7 项验收清单（RC 门禁）
- 验收：ADR 落地；后续收敛 / 契约测试 / DX 文档均以此为准。

### P0-10.5.1 契约测试（Contract Test）+ 配置校验【✅ 已完成 · 2026-07-20】
- 任务：建立攻击性 Contract Test 骨架，暴露真实依赖图，并把配置校验前置落地。
- 交付（PR #27）：
  - `tests/contract/` 5 文件（schema / interface / state-machine / input-attack / config），77 例
  - `core/config.py` 增强 pydantic 校验（类型 / 范围 / 枚举 + bool 防护 mode="before"）
- 验收：Contract Test 跑绿；成功暴露双定义依赖图（收敛后改写为 Freeze Gate 不变量）。

### P0-10.5.2 架构收敛清理（Convergence）【✅ 已完成 · 2026-07-20】
- 任务：清零 ADR-0014 五项冻结前置，消除架构漂移。
- 交付（PR #29）：
  - `core/event.py` 删除旧 `PerceptionEvent`（唯一权威收敛到 `analysis/perception.py`）
  - 删除 `analysis/rules.py`（legacy，硬耦合旧 PerceptionEvent）+ `core/pipeline.py`（死代码，0 引用）
  - `ingestion/frame_source.py` 抽出 `FrameSource(ABC)`，原 OpenCV 实现重命名 `CaviarFrameSource`
- 验收：Freeze Gate 7/7 达成（无重复领域对象 / 无 legacy import / ABC 唯一 / config 校验 / ruff / integration）。

### P0-10.5.3 Developer Experience / API Surface Documentation【✅ 已完成 · 2026-07-20】
- 任务：项目进入「平台化阶段」后，补齐**开发者可用性（DX）文档层**，把「个人项目」提升到「团队可协作项目」，
  直接降低 P0-11 三端 Demo（Dashboard / Agent / 设备）的接入复杂度。
- 动因：最大风险不再是算法错误，而是新成员不知道稳定入口 / 可替换模块 / 禁止依赖，接外部系统时易绕过架构。
- 交付（Owner 授权，建立 DX 文档层）：
  - `docs/API_REFERENCE.md`：**团队第一入口**。运行时入口（`PerceptionPipeline` / `run_demo`）、
    `FrameSource`(ABC)、Detector/Tracker、事件层（VisitorEvent/PerceptionEvent）、Feature/Rule、
    Decision、Action、外部接入点表（需求→实现接口→禁止修改）、架构红线、配置入口、调用链总图（颜色编码）。
  - `docs/CONTRACTS.md`：冻结契约开发者视图（ADR-0014 三级冻结 L1/L2/L3 + Freeze Gate 7 项 + 黑名单字段 + 契约测试索引）。
  - `docs/ARCHITECTURE.md`：系统架构总览（数据流图 + 分层包映射 + 红线摘要 + 文档导航），指向 `02_architecture.md` 详述。
  - `docs/CONTRIBUTING.md`：贡献指南（分支/提交/测试/冻结纪律/提交前自检）。
  - `README.md` 顶部新增颜色编码架构图 + 「团队第一入口」链接；`docs/00_README.md` 索引新增 4 个条目。
- 与既有文档关系（不重复）：`02_architecture.md` = 分层设计详述；`API_REFERENCE.md` = 怎么接；
  `CONTRACTS.md` = 什么不能改；`ARCHITECTURE.md` = 一张图总览。四者分工，互不复制。
- 验收：`docs/API_REFERENCE.md` 所有路径 / 签名与代码一致（已对照 `src/home_perception/` 实际实现核对）；
  `ruff` / `pytest` 不受影响（纯文档 PR）。
- 后续：P0-11 产品层（API Gateway → Dashboard → 三端 Demo）经已冻结契约接入，核心链路 0 改动；
  P0-12 设备适配（RTSPFrameSource / EZVIZFrameSource / 真实 MQTTPublisher）仅实现 FrameSource / Protocol。

### P0-10.5.4 仓库卫生清理（Repository Hygiene）【🚧 进行中 · 本任务】
- 任务：RC 前最后整理，把"代码完成"提升为"仓库治理达标"。
- 交付（PR #31，纯治理 0 行为变化）：
  - `.gitignore` 增加 `.workbuddy/`；解除 `.doc/` 与 `.workbuddy/` 的 Git 跟踪（本地文件保留）
  - 重写 `docs/03_directory_layout.md`（对齐收敛后结构，补齐 `rule.py` / `cooldown.py` 等）
  - `AGENTS.md` 新增 §6.4 仓库卫生纪律
  - 修正 `README.md` 过时状态 + 移除 CI 噪声残留；`docs/08_roadmap.md` 补齐 P0-10.5 链与 RC 里程碑
  - **物理删除** `analysis/anomaly.py`（弃用死代码：同名 `CooldownGate` 原型，全仓 0 引用，已被 `cooldown.py` 取代；删除后 `ruff`/`pytest` 无回归）
- 验收：仓库无 tracked 非源码文件；文档与代码一致；RC tag 可直接打。

### Release Candidate Tag（v0.1.0-mvp-rc）【⏳ 待 P0-10.5.4 合入后由 Owner 执行】
- 触发：Freeze Gate 7/7 + 仓库卫生达标。
- 含义：「Silver Shield MVP 的架构、接口、测试、文档、仓库治理均达到 Release Candidate 状态」——
  不是"代码完成"，而是"可协作、可接入、可评审"。
- 后续：P0-11 产品层（API Gateway → Dashboard → 三端 Demo）经已冻结契约接入，核心链路 0 改动；
  P0-12 设备适配（RTSPFrameSource / EZVIZFrameSource / 真实 MQTTPublisher）。

### P2-12 增强（比赛后/增强版）
- 多摄像头协同、ROI 自动标定、个体作息基线、与中心白名单实时回写联调、COS 长期归档。

## 8.3 里程碑建议（修订）

- **M1（第 1–2 周）**：P0-1~P0-3 完成，本地能产出结构化 `DetectionResult`
  （**Home Perception v0.1：视觉事实采集能力可运行**，已达成）。
- **M1.5**：P0-4 基准 + P0-5 跟踪，确定"门前踩点识别"可落地的性能与连续性基线。
- **M2（第 3 周）**：P0-6~P0-9 完成，`VisitorEvent` → 门前规则 → 决策 → 行动 全链路。
- **M3（第 4 周）**：P0-10 + P1/P2 完成，演示闭环 + 单测 + 消融数据。
