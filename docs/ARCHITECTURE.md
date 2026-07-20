# SilverShield · Home 感知模块 — 系统架构（团队总览）

> 一张图 + 一张表，快速建立系统心智模型。
> - 分层设计 / 三层引擎 / 取证 / 配置：见 `docs/02_architecture.md`
> - 冻结契约（什么不能改）：见 `docs/CONTRACTS.md`
> - 公共 API 表面（怎么接）：见 `docs/API_REFERENCE.md`

---

## 0. 模块定位

本模块 = SilverShield 全局的 **Perceive 感知逻辑模块** + **门前时空异常与蹲守识别子系统**，部署于 **Home 端**。
角色是**风险数字孪生（RiskTwin）的前端事实采集器**：把家庭入口视频流转成结构化「标签事件 + 风险证据」，上报中心风控引擎。

**边界铁律（最高优先级）**：本模块**只输出标签 / 事件**（普通来访 / 待核验来访 / 异常停留 / 重复来访 / 高风险接近），
**绝不直接输出「诈骗人员」结论**。是否诈骗由中心结合入户语音、物品、历史记录综合分析。

---

## 1. 数据流总图（颜色编码）

```
External Device / Video
        │
   [稳定]     FrameSource (ABC)
        │  (timestamp, frame)
   [可替换]   YOLODetector (Detector)
        │  DetectionResult
   [可替换]   VisitorTracker
        │  List[VisitorTrack]
   [稳定]     VisitorEventBuilder
        │  VisitorEvent
   [可替换]   FeatureExtractor
        │  RiskFeature
   [可替换]   RuleEngine (4 Rule + 1 Composite + Cooldown)
        │  PerceptionEvent (5 类标签 + score)
   [可替换]   DecisionEngine + DecisionPolicy
        │  WarningEvent (risk_level + recommended_action)
   [可替换]   ActionExecutor + ActionDispatcher
        │  ActionCommand
   [稳定]     MQTTPublisher / NotificationAdapter (Protocol)
        │
   MQTT / App / Community
```

**图例**：

- **[稳定]** = 接口 / 装配入口，签名冻结（详见 `docs/CONTRACTS.md` L1 / L3）。
- **[可替换]** = 换实现不改契约（Detector / Rule / Policy / Publisher / Notifier）。
- **[禁止]** = 红线：跨层跳级 / 最终判定字段 / 绕过 Pipeline（见 `docs/API_REFERENCE.md` §9）。

---

## 2. 分层与包映射

| 层 | 包 | 关键类 | 输入 → 输出 |
| --- | --- | --- | --- |
| 取流 | `ingestion` | `FrameSource`(ABC) / `CaviarFrameSource` | 视频地址 → `(ts, frame)` 流 |
| 检测 | `detection` | `Detector`(ABC) / `YOLODetector` / `VisitorTracker` | frame → `DetectionResult` / `List[VisitorTrack]` |
| 分析 | `analysis` | `VisitorEventBuilder` / `FeatureExtractor` / `RuleEngine` / `DecisionEngine` | detections → `VisitorEvent` → `RiskFeature` → `PerceptionEvent` → `WarningEvent` |
| 取证 | `evidence` | `ClipCollector` / `Storage` | event + 帧缓冲 → 快照 / 片段引用 |
| 行动 | `action` | `ActionExecutor` / `ActionDispatcher` / `MQTTPublisher` / `NotificationAdapter` | `WarningEvent` → `ActionCommand`（MQTT / 通知 / 社区） |
| 上报 | `output` | `Publisher`(ABC) / `schemas` | `PerceptionEvent` → 中心 MQTT |
| 核心 | `core` | `config.Settings` / `event.EventType` / `event.EvidenceRef` | 配置 + 最小契约基底 |
| 运行期 | `runtime` | `PerceptionPipeline` / `DemoClock` / `run_demo` | 装配 + 启动 + 优雅关闭 |

> 严格自上而下依赖：`core` 不反向依赖业务层；`ingestion` 不依赖 `detection`/`analysis`；`analysis` 仅依赖上层接口（ABC），不依赖 `evidence`/`output` 具体实现；`output` 是最外层，依赖契约 JSON，不反向依赖上游。

---

## 3. 关键架构边界（红线摘要）

1. **只产标签 / 证据，不裁决诈骗**（ADR-0001）。
2. **每层只读上一层，绝不跳级**（ADR-0007 ~ 0012）：Feature 不读越层对象；Rule 只消费 `RiskFeature`；Decision 不重算 Feature / Rule；Action 不修改 `WarningEvent` 的 `recommended_action` / `risk_level`。
3. **每层不做最终判定**：业务判定黑名单字段在构造期即被拒（见 `docs/CONTRACTS.md` §3）。
4. **双状态机独立**：`WarningEvent.status`（决策生命周期）与 `ActionCommand.status`（执行状态）互不污染。
5. **失败不丢 / 幂等不重**：publisher 失败 → `WarningEvent` 保持 `PENDING`；同 `warning_id` 重复 `execute` 只下发一次。
6. **`score` ≠ 诈骗概率**：`perception_score` 是规则命中强度；`risk_level` 是决策严重度。

---

## 4. 文档导航

| 文档 | 用途 |
| --- | --- |
| `docs/API_REFERENCE.md` | **第一入口**：公共 API 表面（怎么接） |
| `docs/CONTRACTS.md` | 冻结契约（什么不能改） |
| `docs/02_architecture.md` | 分层设计详述（怎么设计） |
| `docs/06_api_contract.md` | 与中心 MQTT 契约 |
| `docs/07_event_schema.md` | 事件字段与取值 |
| `docs/08_roadmap.md` | 分阶段研发路线 |
| `docs/ADR/` | 架构决策记录（为什么这样设计） |
| `docs/CONTRIBUTING.md` | 贡献规范 |
| `AGENTS.md` | AI 协作强制规范（所有 PR 须满足） |
