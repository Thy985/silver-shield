# SilverShield · Home 感知模块 — API Reference (MVP)

> **团队第一入口。** 本文档描述本模块的**稳定公共 API 表面**：运行时入口、可替换接口、以及**禁止直接依赖**的红线。
> 接 Dashboard / 新设备 / AI Agent 之前，先读本文档，就知道「从哪里接、不该碰哪里、哪些接口未来不会变」。
>
> - 架构设计详述：`docs/02_architecture.md`
> - 冻结契约（什么不能改）：`docs/CONTRACTS.md`
> - 贡献规范：`docs/CONTRIBUTING.md` / `AGENTS.md`

---

## 0. 30 秒速览

- **唯一运行时入口**：`PerceptionPipeline`（程序化装配）或 `run_demo(settings)`（Demo 一键跑）。
- **调用链严格自上而下**（每层只读上一层，绝不跳级）：
  `FrameSource → Detector → VisitorTracker → VisitorEventBuilder → FeatureExtractor → RuleEngine → DecisionEngine → ActionExecutor`
- **图例（全文通用）**：
  - `[稳定]` —— 稳定契约（接口 / 装配入口），签名冻结，不要随意改。
  - `[可替换]` —— 可替换实现，换实现不改契约。
  - `[禁止]` —— 架构红线：跨层跳级 / 最终判定字段 / 绕过 Pipeline。

---

## 1. Runtime Entry Point（推荐入口）

### PerceptionPipeline  `[稳定]`

- **位置**：`src/home_perception/runtime/pipeline.py`
- **用途**：7 层感知链路的**唯一装配入口**。`from_settings()` 装配组件，`run()` 执行，`close()` 释放资源。
- **禁止**：
  - `[禁止]` 业务代码直接调 `RuleEngine.evaluate(...)`（绕过 Pipeline 会丢失状态机 / 幂等 / 失败保护）。
  - `[禁止]` 绕过 Pipeline 直接调 `ActionExecutor.execute(...)`。
  - `[禁止]` 跨场景重建 `YOLODetector` 实例（破坏 `track_id` 跨帧一致性 —— `model.track(persist=True)` 要求同一实例）。

**程序化调用示例**：

```python
from home_perception.core.config import Settings
from home_perception.runtime.pipeline import PerceptionPipeline

settings = Settings.load("config/default.yaml")
pipeline = PerceptionPipeline.from_settings(settings, device_id="home_entry_01")
pipeline.load_detector()                                   # 懒加载 YOLO 权重（构造期不触发 torch）
summary = pipeline.run(frames, scenario="one_stop_enter")  # frames: List[np.ndarray]
pipeline.close()

# summary: RunSummary
#   frames_processed / n_visitor_events / n_perception /
#   n_warnings / n_commands / publish_count / errors / duration_s ...
```

**Demo 便捷入口**：`run_demo(settings)`（`src/home_perception/runtime/lifecycle.py`）—— CAVIAR 三场景端到端，注入 `DemoClock` 模拟时序：

```python
from home_perception.core.config import Settings
from home_perception.runtime.lifecycle import run_demo

summaries = run_demo(Settings.load("config/default.yaml"))  # List[RunSummary]
```

- **输入（程序化）**：`frames: List[np.ndarray]`（BGR 帧序列）。
- **输出**：`RunSummary`（dataclass，见 `runtime/pipeline.py`）。
- **关于 `FrameSource`**：`FrameSource` 是 Source→Pipeline 解耦边界（见 §2）。当前 Demo 用 `runtime/config.read_caviar_frames` 读 fixtures 喂给 `run()`；P0-12 起由 `CaviarFrameSource`（及未来的 RTSP/EZVIZ 实现）接入 Pipeline，届时 `from_settings` 会直接消费 `FrameSource` 流。

---

## 2. 数据采集接口

### FrameSource（ABC）  `[稳定]`

- **位置**：`src/home_perception/ingestion/frame_source.py`
- **用途**：所有视频来源的抽象接口（ADR-0014 Level 3 Runtime Assembly Contract）。Pipeline 仅依赖此抽象，不感知具体来源类型。
- **接口**：

```python
class FrameSource(ABC):
    @abstractmethod
    def __iter__(self) -> Iterator[tuple[float, object]]:
        """产出 (timestamp, frame) 元组流。"""
        ...
```

- **当前实现**：

  | 实现 | 状态 | 用途 |
  | --- | --- | --- |
  | `CaviarFrameSource` | ✅ 已实现（MVP Demo） | CAVIAR 抽帧 JPG / 视频，带断流重连 + 限速 |
  | `RTSPFrameSource` | ⏳ P0-12 规划 | 真实 RTSP 摄像头 |
  | `EZVIZFrameSource` | ⏳ P0-12 规划 | 萤石设备流 |

- **新增设备**：只需实现 `FrameSource`（产出 `(ts, frame)` 流）。**无需修改** `Detector` / `VisitorTracker` / `Rule` / `Decision` / `Action`。

---

## 3. 感知层接口

### Detector（ABC）  `[可替换]`

- **位置**：`src/home_perception/detection/detector.py`
- **实现**：`YOLODetector`（ultralytics YOLO11n，CPU）
- **职责**：`Frame → DetectionResult`（结构化事实，带 `track_id`）。**不产生风险、不产生事件**。
- **接口**：`detect(frame: np.ndarray) -> DetectionResult`
- **禁止**：`[禁止]` 输出 risk score / event_type / 任何业务结论。

### VisitorTracker  `[可替换]`

- **位置**：`src/home_perception/detection/tracker.py`
- **职责**：`List[Detection] → List[VisitorTrack]`（当前在场状态）。
- **输出** `VisitorTrack`（状态对象，**非事件**）：
  `track_id / status∈{active,left} / enter_time / leave_time / duration_s / bbox / frame_count / confidence`
- **注意**：`track_id` 仅当前摄像头会话内有效，**不代表跨天身份**（跨天重识别属 P1）。

---

## 4. 事件层 API

### VisitorEvent  `[稳定]`

- **位置**：`src/home_perception/analysis/event.py`
- **语义**：**事实层**。某人何时进入 / 离开 / 停留多久（在 `VisitorTrack` 转 `left` 时由 `VisitorEventBuilder` 生成）。
- **字段**：`visitor_id(UUID) / enter_time / leave_time / duration_seconds / source_video / event_id / created_at`（全部 UTC timezone-aware）。
- **不含**：`risk` / `score` / `warning` / `event_type`（那些是上层的事）。

### VisitorEventBuilder  `[稳定]`

- **位置**：`src/home_perception/analysis/event_builder.py`
- **职责**：包裹 `VisitorTracker`，监听 `active→left` 翻转，生成 `VisitorEvent`；`pending()` / `ack()` 供行动层失败重发。
- **⚠️ 不要绕过 Builder 直接调 `tracker.update()`**（会丢事件）。

### PerceptionEvent  `[稳定]`

- **位置**：`src/home_perception/analysis/perception.py`（**唯一权威定义**，P0-10.5.2 收敛后仅此一处）
- **语义**：规则发现的异常感知事件（§7.2 五类之一 + `score`）。
- **5 类 `event_type`**：`visit_normal` / `visit_pending_verify` / `abnormal_dwell` / `repeat_visit` / `high_risk_approach`
- **`score` ∈ [0,1]** = 规则命中强度，**不是诈骗概率**。
- **不是最终报警**：最终报警是 `WarningEvent`（见 §6）。

---

## 5. 风险分析接口

### FeatureExtractor  `[可替换]`

- **位置**：`src/home_perception/analysis/feature_extractor.py`
- **输入**：`VisitorEvent` → **输出**：`RiskFeature`
- **4 个具体 Feature**（均为「被测量的数值」，不含判断）：
  `DurationFeature` / `VisitFrequencyFeature` / `TimeFeature` / `TrajectoryFeature`
- 编排器维护每 `visitor_id` 的滑动窗口状态（`frequency_window_s`，默认 30 分钟）。

### RuleEngine  `[可替换]`

- **位置**：`src/home_perception/analysis/rule_engine.py`
- **输入**：`RiskFeature` → **输出**：`List[PerceptionEvent]`
- **组成**：4 条基础 Rule（`LongDurationRule` / `RepeatVisitRule` / `OddHourRule` / `PendingVerifyRule`） + 1 条 Composite（`HighRiskApproachRule`） + `CooldownGate` + `ThresholdConfig`（阈值配置化，不硬编码）。
- **禁止**：
  - `[禁止]` 不发送消息、不调 MQTT（→ 行动层）。
  - `[禁止]` 不直接输出 `WarningEvent`（不跳级）。

---

## 6. 决策接口

### DecisionEngine  `[可替换]`

- **位置**：`src/home_perception/analysis/decision_engine.py`
- **输入**：`List[PerceptionEvent]` → **输出**：`Optional[WarningEvent]`
- **职责**：委托 `DecisionPolicy.decide()` 决策；**不直接执行**任何动作（MQTT / 通知 / 升级 → 行动层）。
- **`risk_level` ∈ {LOW, MEDIUM, HIGH}** = 决策严重度，**不是诈骗概率**。
- **`recommended_action` ∈ {MONITOR, NOTIFY_FAMILY, ESCALATE_COMMUNITY}**。

### DecisionPolicy（ABC）  `[可替换]`

- **位置**：`src/home_perception/analysis/decision_policy.py`
- **实现**：`RuleBasedDecisionPolicy`（routing_table 可定制）。
- **v2 可换**：ML 评分 / LLM 解释策略（替换实现，**不改变 `WarningEvent` 契约**）。

---

## 7. 行动接口

### ActionExecutor  `[可替换]`

- **位置**：`src/home_perception/action/executor.py`
- **输入**：`WarningEvent` → **输出**：`List[ActionCommand]`
- **三大保证**（ADR-0011）：
  1. **幂等**：同 `warning_id` 重复 `execute()` 只产生一个下游任务。
  2. **失败保护**：publisher 失败时 `WarningEvent.status` 保持 `PENDING`（不丢）；重试 `max_retries` 次后进入 `GIVEN_UP` + `REJECTED`。
  3. **消费正确**：`HIGH → ESCALATE_COMMUNITY` 真的走社区通道。
- **依赖注入**（均为 Protocol / 可替换）：`ActionDispatcher` + `MQTTPublisher` + `NotificationAdapter`。

### ActionCommand  `[稳定]`

- **位置**：`src/home_perception/action/command.py`
- **`command_type` ∈ {LOG_ONLY, SEND_FAMILY_MESSAGE, CREATE_COMMUNITY_TASK}**
- **`status` ∈ {PENDING, DONE, FAILED, RETRYING, GIVEN_UP}**（执行层状态机，**独立于** `WarningEvent.status`）。

---

## 8. 外部接入点（最重要）

> 接新系统（Dashboard / 设备 / Agent）时，只对「稳定契约」编程，不要碰「禁止修改」的列。

| 需求 | 实现接口（稳定契约） | 禁止修改 |
| --- | --- | --- |
| 接摄像头 / 视频源 | `FrameSource`（ABC，`ingestion/frame_source.py`） | `PerceptionPipeline` 装配逻辑 |
| 换检测模型 | `Detector`（实现 `YOLODetector` 或新 `Detector`） | `Rule` / `FeatureExtractor` |
| 换规则 | `RuleEngine` + `Rule` / `ThresholdConfig` | `FeatureExtractor` / `VisitorEvent` |
| 接 AI Agent / 决策策略 | `DecisionPolicy`（ABC） | `PerceptionEvent` |
| 上报感知事件到中心 | `Publisher`（ABC，`output/publisher.py`）+ `output/schemas.py` | `PerceptionEvent` 字段 |
| 行动下发 MQTT（社区工单） | `MQTTPublisher`（Protocol，`action/publisher.py`） | `ActionExecutor` |
| 接短信 / App 推送家属 | `NotificationAdapter`（Protocol，`action/notifier.py`） | `WarningEvent` |

> **注意：本模块有「两个 Publisher」**，不要混淆：
> - `output/publisher.py::Publisher` —— 上报 **`PerceptionEvent` / `VisitorEvent`** 到中心 MQTT（`silvershield/home/{device_id}/events`，见 `docs/06_api_contract.md`）。MVP 的 `MQTTPublisher(Publisher)` 尚未接真实 broker（Phase 1 实现）。
> - `action/publisher.py::MQTTPublisher` —— 行动层下发 **`ActionCommand`**（社区工单升级）到 MQTT。MVP 用 `MockPublisher`（写本地 JSONL）。

---

## 9. 禁止依赖与架构红线  `[禁止]`

1. **禁止跨层跳级**：
   - `FeatureExtractor` 不读 `VisitorEvent` 之外的越层对象（只消费 `VisitorEvent`）。
   - `Rule` 不读 `VisitorEvent`（只消费 `RiskFeature`）。
   - `DecisionEngine` 不重算 Feature / 不重新组合 Rule。
   - `ActionExecutor` 不修改 `WarningEvent.recommended_action` / `risk_level`（只翻 `status`）。
2. **禁止最终判定字段**：`fraud_result` / `is_fraud` / `verdict` / `crime_probability` / `guilt_score` 等出现在任何 `PerceptionEvent` / `WarningEvent` / `ActionCommand` 的字段或 `meta` → **构造期直接抛错**（黑名单测试，见各自 `__post_init__`）。
3. **禁止绕过 Pipeline**：业务代码不要直接调 `RuleEngine` / `DecisionEngine` / `ActionExecutor`。
4. **禁止改 schema 不评审**：5 类 `EventType`、`PerceptionEvent` / `WarningEvent` / `ActionCommand` 字段变更属 **BREAKING**，必须 ADR + Owner review（见 `docs/CONTRACTS.md` L1）。
5. **禁止硬编码凭证 / 设备序列号**：走 `.env` / `config/devices.yaml`（均 gitignored）。
6. **禁止双定义漂移**：`PerceptionEvent` 仅定义于 `analysis/perception.py`；`EventType` 仅定义于 `core/event.py`。新增定义 = 架构漂移。

---

## 10. 配置入口

- **`Settings`**：`src/home_perception/core/config.py`，`Settings.load("config/default.yaml")` 从 YAML 读取（支持 `${ENV_VAR:-default}` 展开，凭证走环境变量）。
- **阈值 / 权重**：`RuleConfig`（YAML）→ `ThresholdConfig`（规则层内部），集中配置化（ADR-0009）。
- **配置校验**：负值 / NaN / 范围越界 / 非法枚举 / bool 误传会被 pydantic 校验拒绝（ADR-0014 前置 #5，见 `tests/contract/test_config_contract.py`）。

---

## 11. 调用链总图（颜色编码）

```
External Device / Video
        │
   [稳定] FrameSource (ABC)
        │  (timestamp, frame)
   [可替换] YOLODetector (Detector)
        │  DetectionResult
   [可替换] VisitorTracker
        │  List[VisitorTrack]
   [稳定] VisitorEventBuilder
        │  VisitorEvent
   [可替换] FeatureExtractor
        │  RiskFeature
   [可替换] RuleEngine (4 Rule + 1 Composite + Cooldown)
        │  PerceptionEvent (5 类标签 + score)
   [可替换] DecisionEngine + DecisionPolicy
        │  WarningEvent (risk_level + recommended_action)
   [可替换] ActionExecutor + ActionDispatcher
        │  ActionCommand
   [稳定] MQTTPublisher / NotificationAdapter (Protocol)
        │
   MQTT / App / Community
```

图例：
- **[稳定]** = 接口 / 装配入口，签名冻结（详见 `docs/CONTRACTS.md` L1 / L3）。
- **[可替换]** = 换实现不改契约（Detector / Rule / Policy / Publisher / Notifier）。
- **[禁止]** = 红线：跨层跳级 / 最终判定字段 / 绕过 Pipeline（见 §9）。

---

## 12. 相关文档

- `docs/CONTRACTS.md` —— 冻结契约（什么不能改）
- `docs/ARCHITECTURE.md` —— 系统架构总览
- `docs/02_architecture.md` —— 分层设计详述
- `docs/06_api_contract.md` —— 与中心 MQTT 契约
- `docs/07_event_schema.md` —— 事件字段与取值
- `docs/CONTRIBUTING.md` —— 贡献规范
- `AGENTS.md` —— AI 协作强制规范（所有 PR 须满足）
