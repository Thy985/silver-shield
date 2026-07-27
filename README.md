# SilverShield · Home 感知模块

> 老年人诈骗风险数字孪生与协同预警系统 —— **家庭入口实时感知**子模块

基于萤石（EZVIZ）摄像头视频流，对家庭入口区域做实时感知，生成结构化异常事件并采集风险证据，
上报至 SilverShield 中心风控引擎，支撑家庭数字孪生与协同预警。

## 当前状态（MVP Release Candidate + 多角色协同闭环 Demo 完成）

- ✅ P0-3~P0-10：检测 → 跟踪 → 事件 → 特征 → 规则 → 决策 → 行动 → 装配 全链路完成（289 测试全绿）
- ✅ P0-10.5 架构冻结治理（ADR-0014 三级冻结 + 契约测试 + 收敛清理），架构漂移清零
- ✅ P0-10.5.3 Developer API Surface 文档层（DX）已建立（见下方「团队第一入口」）
- ✅ P0-10.5.4 仓库卫生清理完成；**Release Candidate tag `v0.1.0-mvp-rc` 已打（2026-07-20）**
- ✅ **P0-11 多角色协同闭环展示层（Demo）全阶段完成**：单 Dashboard 三视图 Tab
  （① 风险发现 / ② 家属确认 / ③ 社区处置）共享同一 `DemoAggregateState`，
  确定性 HIGH 闭环 + 5 分钟演示剧本，并经 `scripts/e2e_validate_demo.py` 真实端到端验证（12/12）——
  详见 `docs/ADR/0017` 与 `docs/DEMO-SCRIPT-P0-11-5b.md`。展示层零穿透 7 层冻结契约（ADR-0015）。
- 边界：本模块只做事实采集 + 事件生成，**不做诈骗风险判断、不输出 risk score、不调用 LLM**

### v2 实时风险状态流（设计完成 · 未集成 · 未启用）

> **严格区分**：设计完成 ≠ 集成完成 ≠ 默认启用。

- **设计完成**：ADR-0018/0019/0020（方向）+ ADR-0021/0022/0023（具体设计）+ `docs/DESIGN-realtime-riskstream-engineering-plan.md`（工程方案）已就位。
- **工程迁移 Stage A 进行中**：`BehaviorState` / `RiskSignal` / `RecentBehaviorStore` 类型 + 契约测试已在工作区落地，**未接入 pipeline**（`pipeline.py` diff 为空）。
- **当前运行路径**：仍为 MVP 历史事件流（`VisitorEvent` 离场生成 → `RuleEngine` → `WarningEvent`），`realtime_risk.enabled=false` 默认关闭。
- 后续 Stage B/C/D（工程方案 §9）才逐步接入实时状态 / 信号 / 决策；详见 `docs/08_roadmap.md` §8.4 产品 Phase 1。

## 架构总览（团队入口）

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

图例：**\[稳定\]** = 接口 / 装配入口（签名冻结）；**\[可替换\]** = 换实现不改契约；**\[禁止\]** = 红线（跨层跳级 / 最终判定 / 绕过 Pipeline）。

> **团队第一入口**：接 Dashboard / 新设备 / AI Agent 前，先读 [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)（公共 API 表面）与 [`docs/CONTRACTS.md`](docs/CONTRACTS.md)（冻结契约，什么不能改）。

## 快速开始

```bash
# 1. 准备环境
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置凭证与设备
cp .env.example .env            # 填写 EZVIZ_APP_KEY / EZVIZ_APP_SECRET
cp config/devices.example.yaml config/devices.yaml   # 填写设备序列号

# 3. （可选）本地 MQTT
docker compose up -d mqtt

# 4. 运行
python scripts/run.py
```

## 目录与文档

- 代码：`src/home_perception/`
- AI 协作规范：`AGENTS.md`（所有 PR 须满足）
- 设计文档：`docs/`（见 `docs/00_README.md` 索引）
- **团队第一入口（API 表面 / 冻结契约）**：`docs/API_REFERENCE.md` · `docs/CONTRACTS.md` · `docs/ARCHITECTURE.md` · `docs/CONTRIBUTING.md`
- 阶段任务与风险：`docs/08_roadmap.md`、`docs/09_risks.md`

> ⚠️ `prototypes/` 下为早期验证脚本，含真实凭证，**已被 gitignore，切勿提交**。

## YOLO 检测（P0-3）

门前异常行为感知第一阶段：萤石 1080p 流 → OpenCV 抽帧 → **显式 resize 到 640×640** → YOLO11n 推理 → 结构化 `DetectionResult`。

### 模型下载

默认模型 `yolo11n.pt`（Ultralytics 官方小模型，CPU 可跑）。首次推理时由 `ultralytics` 自动下载并缓存到 `~/.cache/ultralytics`；也可手动预取：

```bash
python - <<'PY'
from ultralytics import YOLO
YOLO("yolo11n.pt")  # 触发下载，验证可用性
PY
```

如需更好精度或 GPU，改 `config/default.yaml` 的 `detection.model`（如 `yolo11s.pt`）与 `detection.device`（`cuda:0`）。**不要扩展检测类别**——本阶段仅 `person / backpack / handbag / cell phone`（见 `AGENTS.md` §3 边界约束）。

### 推理流程

```
萤石 1080p 帧
    ↓ FrameSource 抽帧（fps_target，默认 8）
    ↓ YOLODetector：cv2.resize -> 640×640
    ↓ YOLO11n 推理（CPU）
    ↓ 检测框映射回原始帧坐标
    ↓ DetectionResult（detections + 推理耗时 + 尺寸）
```

`YOLODetector.detect(frame)` 返回 `DetectionResult`，每个 `Detection` 含 `class_id / class_name / confidence / bbox / timestamp`。检测器**不输出任何风险结论**，仅提供事实。

### 最小闭环演示

```bash
# 需要 .env 配置 EZVIZ_APP_KEY / EZVIZ_APP_SECRET，且 config/devices.yaml 有该序列号
python scripts/detect_demo.py --serial BK6415780 --duration 20 --protocol rtsp
```

脚本仅打印检测事实（类别@置信度 + 推理耗时），**不生成事件、不判风险**，用于验证"事实采集"链路。

### 性能测试

```bash
# 流质量基线（FPS / 延迟 / 重连）
python scripts/eval_stream.py --serial BK6415780 --duration 30 --protocol rtsp

# 单元/契约测试（含模型加载、空图、正常图推理、输出 Schema）
pytest tests/ -q
```

普通电脑目标：**检测 15–30 FPS**（抽帧后 YOLO 推理预算；1080p 默认降采样到 480 再推理，避免 CPU 直接跑满分辨率；精度优先场景可切 accuracy=640）。所有性能结论以实测为准（见 `docs/09_risks.md` 9.4）。

## P0-4 · 性能基准（benchmark）

量化 `萤石流 → OpenCV → YOLO → DetectionResult` 的端到端性能，为答辩与"门前踩点识别"落地提供硬数据，并为 P0-5 跟踪定基线。基准脚本**只测性能**，不生成事件、不判风险。

```bash
# 合成模式（默认，无需摄像头/网络，可复现；测纯推理+resize 开销）
python benchmark/yolo_speed.py --synthetic --duration 30 --json out/bench.json

# 调不同推理分辨率对比（--profile 对应 accuracy=640 / balanced=480 / realtime=416）
python benchmark/yolo_speed.py --synthetic --duration 15 --profile balanced
python benchmark/yolo_speed.py --synthetic --duration 15 --profile accuracy
python benchmark/yolo_speed.py --synthetic --duration 15 --profile realtime

# 实时模式（接真实萤石流，测端到端；需 .env）
python benchmark/yolo_speed.py --serial BK6415780 --duration 1800 --protocol rtsp
```

输出：Camera FPS / Inference FPS / 推理耗时(avg/p50/p95/max) / 端到端延迟 / CPU / 内存，并对照目标表自动判定达标。

### 目标（roadmap P0-4）

| 指标 | 目标 |
| --- | --- |
| 输入 FPS | 10–15 |
| YOLO 推理耗时 | < 100 ms |
| 端到端延迟 | < 300 ms |
| 连续运行 | ≥ 30 分钟 |

### 实测参考（开发机 CPU · yolo11n · 1080p 合成帧）

> 数据来自本机合成基准（`--duration 30/15`），仅供**相对比较与调参**参考；实际以目标硬件实测为准。合成模式下每帧随机生成 1080p 图约耗 16 ms，会拉低 camera_fps，**推理耗时 / Inference FPS 才是硬件代表指标**。

| imgsz | profile | 推理 avg | Inference FPS | 端到端 avg | 达标 |
| --- | --- | --- | --- | --- | --- |
| 640 | accuracy | 124 ms | 8.1 | 142 ms | ❌ 推理/FPS 未达标 |
| 480（默认） | balanced | 86 ms | 11.6 | 104 ms | ✅ 推理/端到端达标 |
| 416 | realtime | 47 ms | 21.5 | 64 ms | ✅ 全部达标（有裕量） |

**工程结论（P0-4 实测推翻原假设）**：原设计假设"YOLO11n@640 可实时"被实验推翻——纯 CPU 边缘机推理 ~124ms、未达 <100ms/≥10FPS。基于 CPU 边缘部署测试，**MVP 默认采用 `yolo11n@480`（balanced）**，在保证门前人员检测需求的同时满足实时性能约束；`640` 作 accuracy 精度模式（GPU/算力充足时），`416` 作 realtime 低延迟模式（预留算力给 Tracker/ROI/事件，小目标精度略降）。这正是工程开发区别于 PPT 方案之处。

**配置化（不写死分辨率）**：用 `imgsz_profile` 切换，而非改死 `imgsz`：

```yaml
# config/default.yaml · detection
imgsz: 480                # 默认 = balanced；显式赋值即覆盖 profile
imgsz_profile: balanced   # accuracy(640) / balanced(480) / realtime(416)
```

> GPU 不是当前阻塞点：现阶段最缺的是"从检测结果生成连续事件"（P0-5/6），而非 YOLO 精度。故不为 640 引入 GPU。配合 `fps_target=8` 抽帧，链路可稳定实时。
