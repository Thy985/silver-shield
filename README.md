# SilverShield · Home 感知模块

> 老年人诈骗风险数字孪生与协同预警系统 —— **家庭入口实时感知**子模块

基于萤石（EZVIZ）摄像头视频流，对家庭入口区域做实时感知，生成结构化异常事件并采集风险证据，
上报至 SilverShield 中心风控引擎，支撑家庭数字孪生与协同预警。

## 当前状态

- ✅ 已验证：萤石摄像头接入、直播流获取、OpenCV 读取视频
- 🚧 进行中：YOLO 检测闭环（P0-3，见 `docs/08_roadmap.md` 第一阶段）
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
