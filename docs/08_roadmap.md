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
    3 类 recommended_action + 4 类 status + UTC 强制 + 黑名单校验 + `to_dict()` structlog-safe）
  - `analysis/decision_policy.py`：`DecisionContext` + `DecisionPolicy` 抽象基类 +
    `RuleBasedDecisionPolicy`（routing_table 查表 + max wins 聚合 + 定制支持）
  - `analysis/decision_engine.py`：`DecisionEngine` 编排器（注入 elder_id + policy + now_provider）
  - `tests/test_warning.py`：**65 个测试**（WarningEvent 字段/UUID/UTC/枚举/黑名单 +
    DecisionPolicy 单事件/组合/抑制 + DecisionEngine 编排 + RuleEngine→DecisionEngine 集成 +
    决策层字段无业务判定 + CAVIAR 端到端）
- 验收：`pytest` **197 全绿**（之前 132 + 65）；`ruff` 全绿；CAVIAR 端到端
  `detector → tracker → event → feature → rule → perception → decision` 全链路跑通。

> **P0-9 行动层（Action Executor + MQTT + 通知 + 升级）** —— 按 `WarningEvent.recommended_action`
> 路由执行，详见 ADR-0011（待写）。

### P1-11 测试与可复现
- 任务：补充契约/规则单测；提供 `docker compose up` 一键演示；消融实验数据收集脚本。
- 验收：CI 绿；评委可本地复现核心闭环。

### P2-12 增强（比赛后/增强版）
- 多摄像头协同、ROI 自动标定、个体作息基线、与中心白名单实时回写联调、COS 长期归档。

## 8.3 里程碑建议（修订）

- **M1（第 1–2 周）**：P0-1~P0-3 完成，本地能产出结构化 `DetectionResult`
  （**Home Perception v0.1：视觉事实采集能力可运行**，已达成）。
- **M1.5**：P0-4 基准 + P0-5 跟踪，确定"门前踩点识别"可落地的性能与连续性基线。
- **M2（第 3 周）**：P0-6~P0-9 完成，`VisitorEvent` → 门前规则 → 决策 → 行动 全链路。
- **M3（第 4 周）**：P0-10 + P1/P2 完成，演示闭环 + 单测 + 消融数据。
