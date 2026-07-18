# SilverShield · Home 感知模块 — 长期记忆

## 项目定位
- SilverShield = 老年人诈骗风险数字孪生与协同预警系统（比赛项目）。
- 本仓库负责 **Home 感知模块**（网络工程 owner）：基于萤石摄像头的家庭入口实时感知 + 风险证据采集。

## 架构边界（硬约束）
- 本模块 = Perceive 逻辑模块 + 门前时空异常与蹲守识别子系统，部署 Home 端。
- **只输出标签/事件，不直接判定"诈骗人员"**；是否诈骗由中心综合语义/物品/历史画像判定。
- 风险引擎 = Rule + ML 两层；LLM 解释推迟到 v2（不在本模块、不在第一版）。
- 设计依据：`.doc/银龄盾_老年诈骗风险数字孪生系统_架构设计完善版(1).docx`（V2 终稿）；其余 `.doc/设计思路研究/*` 仅作理论参考（六维机理/三方博弈），且已 gitignore。

## 对外契约（改动需评审 + 升 schema_version）
- 事件 Schema：`docs/07_event_schema.md`（5 类 EventType + EvidenceRef）。
- 接口契约：`docs/06_api_contract.md`（MQTT topic `silvershield/home/{device_id}/events` + Envelope）。
- 与中心数据对象 `VisitorEvent` / `RiskTwin` 对齐。

## 工程约定
- 分支：轻量 GitFlow（main + feature/* + fix/*），MVP 可直接 main 短分支；Conventional Commits。
- 凭证：仅走 `.env`（gitignore）；`prototypes/`、`config/devices.yaml` 已 gitignore（含真实序列号/凭证）。
- 流：MVP 用 RTSP（低延迟）优先，HLS 回退；抽帧 `fps_target=8` 控算力。
- 测试：契约测试锁事件格式/配置/规则；venv 路径 `~/.workbuddy/binaries/python/envs/ss_home`。

## 下一步
- Phase1 任务拆解见 `docs/08_roadmap.md`（P0-1~P0-7）。优先 YOLO 检测与门前规则，再取证与 MQTT 上报。
