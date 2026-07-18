# SilverShield · Home 感知模块

> 老年人诈骗风险数字孪生与协同预警系统 —— **家庭入口实时感知**子模块

基于萤石（EZVIZ）摄像头视频流，对家庭入口区域做实时感知，生成结构化异常事件并采集风险证据，
上报至 SilverShield 中心风控引擎，支撑家庭数字孪生与协同预警。

## 当前状态

- ✅ 已验证：萤石摄像头接入、直播流获取、OpenCV 读取视频
- 🚧 下一步：接入 YOLO 完成人员检测（见 `docs/08_roadmap.md` 第一阶段）

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
