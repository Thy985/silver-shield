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

### P0-3 YOLO 人员检测（detection）
- 任务：实现 `YOLODetector`（ultralytics YOLOv8n/s）+ ByteTrack/BotSORT 跟踪，
  仅 `classes=[0]`（person），输出带 `track_id` 的 Detection。
- 验收：单帧检测正确；1080p 下满足准入帧率（参考 `prototypes/` 的 FPS 门槛）。

### P0-4 门前规则（analysis）
- 任务：实现 5 类标签规则：`OddHourRule`（已实现示例）、`DwellRule`、`RepeatVisitRule`、
  `PendingVerifyRule`、`HighRiskApproachRule`；并落地 `CooldownGate` 防刷。
- 验收：`test_rules.py` 确定性通过；阈值集中在 `config/default.yaml`。

### P0-5 取证采集（evidence）
- 任务：触发中/高风险时存快照 + 短片段（前后各 `clip_seconds/2`），敏感区遮挡后落盘/可选 COS。
- 验收：事件能回挂 `EvidenceRef`；非高风险不落像素。

### P0-6 事件上报（output）
- 任务：实现 `MQTTPublisher`，按 `06_api_contract.md` 信封上报；离线环形缓冲补发。
- 验收：本地起 mosquitto 后能订阅到合规 `VisitorEvent`。

### P0-7 装配与联调（main/pipeline）
- 任务：在 `main.py` 装配 EZVIZClient+FrameSource+Detector+Rules+Collector+Publisher，
  单设备端到端跑通；对接中心模拟消费者。
- 验收：真实/模拟流 → 事件 → 证据 → 上报 全链路打通。

### P1-8 测试与可复现
- 任务：补充契约/规则单测；提供 `docker compose up` 一键演示；消融实验数据收集脚本。
- 验收：CI 绿；评委可本地复现核心闭环。

### P2-9 增强（比赛后/增强版）
- 多摄像头协同、ROI 自动标定、个体作息基线、与中心白名单实时回写联调、COS 长期归档。

## 8.3 里程碑建议

- **M1（第 1–2 周）**：P0-1~P0-4 完成，本地能产出门前标签事件（无上报）。
- **M2（第 3 周）**：P0-5~P0-7 完成，端到端上报 + 取证。
- **M3（第 4 周）**：P1-8 完成，演示闭环 + 单测 + 消融数据。
