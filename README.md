# SilverShield · Home 感知模块

> 老年人诈骗风险数字孪生与协同预警系统 —— **家庭入口实时感知**子模块

基于萤石（EZVIZ）摄像头视频流，对家庭入口区域做实时感知，生成结构化异常事件并采集风险证据，
上报至 SilverShield 中心风控引擎，支撑家庭数字孪生与协同预警。

## 当前状态

- ✅ 已验证：萤石摄像头接入、直播流获取、OpenCV 读取视频
- ✅ P0-3：YOLO 检测闭环（萤石流 → OpenCV → 640 resize → YOLO11n → `DetectionResult`）
- 🚧 P0-4：视频流稳定化 + FPS 基准（`benchmark/yolo_speed.py`，见下）
- 边界：本模块只做事实采集 + 事件生成，**不做诈骗风险判断、不输出 risk score、不调用 LLM**

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

普通电脑目标：**检测 15–30 FPS**（抽帧后 YOLO 推理预算；1080p 先降采样到 640 再推理，避免 CPU 直接跑满分辨率）。所有性能结论以实测为准（见 `docs/09_risks.md` 9.4）。

## P0-4 · 性能基准（benchmark）

量化 `萤石流 → OpenCV → YOLO → DetectionResult` 的端到端性能，为答辩与"门前踩点识别"落地提供硬数据，并为 P0-5 跟踪定基线。基准脚本**只测性能**，不生成事件、不判风险。

```bash
# 合成模式（默认，无需摄像头/网络，可复现；测纯推理+resize 开销）
python benchmark/yolo_speed.py --synthetic --duration 30 --json out/bench.json

# 调不同推理分辨率对比
python benchmark/yolo_speed.py --synthetic --duration 15 --imgsz 480
python benchmark/yolo_speed.py --synthetic --duration 15 --imgsz 416

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

| imgsz | 推理 avg | Inference FPS | 端到端 avg | 达标 |
| --- | --- | --- | --- | --- |
| 640（默认） | 124 ms | 8.1 | 142 ms | ❌ 推理/FPS 未达标 |
| 480 | 86 ms | 11.6 | 104 ms | ✅ 推理/端到端达标 |
| 416 | 47 ms | 21.5 | 64 ms | ✅ 全部达标（有裕量） |

**工程结论**：纯 CPU 开发机上 `yolo11n@640` 推理约 124 ms、未达 <100 ms/≥10 FPS 目标；**降到 imgsz=480 即满足推理目标，416 全达标且有裕量**。门口场景人目标占比大，480/416 精度足够。是否将默认 `imgsz` 从 640 下调由 Owner 决策（当前默认仍为 640，本 PR 不改配置默认值）。配合 `fps_target=8` 抽帧，链路可稳定实时。
