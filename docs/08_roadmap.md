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

### P0-6 生成 VisitorEvent（事件层）【后续】
- 任务：在 `DetectionResult` + `VisitorTrack` 之上生成第一个对银龄盾有意义的数据对象
  `VisitorEvent`：`visitor_id / enter_time / leave_time / duration / source`。
- 边界：仍只是"有人在门口停留 X 分钟"的事实，**不是诈骗结论**；风险标签由后续规则/引擎产生。
- 验收：`VisitorEvent` 结构经契约测试；可被中心消费。

### 后续横向能力（P0-7+，按原架构补全）
> 下列为原 P0-4~P0-7 的横向交付，须叠加在感知深度链路上：
- **P0-7 门前规则（analysis）**：5 类标签 `OddHourRule`(已实现)/`DwellRule`/`RepeatVisitRule`/
  `PendingVerifyRule`/`HighRiskApproachRule` + `CooldownGate` 防刷。
- **P0-8 取证采集（evidence）**：中/高风险存快照+短片段，敏感区遮挡后落盘/可选 COS。
- **P0-9 事件上报（output）**：`MQTTPublisher` 按 `06_api_contract.md` 信封上报 + 离线环形缓冲。
- **P0-10 装配与联调（main/pipeline）**：单设备端到端；对接中心模拟消费者。

### P1-11 测试与可复现
- 任务：补充契约/规则单测；提供 `docker compose up` 一键演示；消融实验数据收集脚本。
- 验收：CI 绿；评委可本地复现核心闭环。

### P2-12 增强（比赛后/增强版）
- 多摄像头协同、ROI 自动标定、个体作息基线、与中心白名单实时回写联调、COS 长期归档。

## 8.3 里程碑建议（修订）

- **M1（第 1–2 周）**：P0-1~P0-3 完成，本地能产出结构化 `DetectionResult`
  （**Home Perception v0.1：视觉事实采集能力可运行**，已达成）。
- **M1.5**：P0-4 基准 + P0-5 跟踪，确定"门前踩点识别"可落地的性能与连续性基线。
- **M2（第 3 周）**：P0-6~P0-9 完成，`VisitorEvent` → 门前规则 → 取证 → 上报 全链路。
- **M3（第 4 周）**：P0-10 + P1/P2 完成，演示闭环 + 单测 + 消融数据。
