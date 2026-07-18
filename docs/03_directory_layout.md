# 03 · 目录结构

```
silver-shield/                        # Home 感知模块仓库
├── README.md                         # 项目入口说明
├── pyproject.toml                    # 包元数据 + ruff/black/pytest 配置
├── requirements.txt                  # 运行时依赖
├── requirements-dev.txt              # 开发/测试依赖
├── .gitignore                        # 忽略 .env / prototypes / 证据 / 模型权重
├── .env.example                      # 环境变量模板（凭证占位）
├── .pre-commit-config.yaml           # 提交前 lint/format
├── Dockerfile                        # 边缘部署镜像
├── docker-compose.yml                # 本地 MQTT + 模块一键起
├── config/
│   ├── default.yaml                  # 全局默认配置（阈值/模型/上报）
│   ├── devices.example.yaml          # 设备清单模板（真实序列号不入库）
│   └── mosquitto.conf                # 本地 MQTT broker 配置
├── src/home_perception/              # 包根
│   ├── __init__.py
│   ├── main.py                       # 入口：装配配置/设备/流水线
│   ├── core/                         # 配置、事件模型、流水线编排
│   │   ├── config.py                 # Settings 加载 + ${ENV} 展开 + pydantic 校验
│   │   ├── event.py                 # PerceptionEvent / EvidenceRef / EventType
│   │   └── pipeline.py              # 流水线编排（装配各阶段）
│   ├── ingestion/                    # 取流与帧源
│   │   ├── ezviz_client.py          # 萤石 token/流地址获取（已落地）
│   │   └── frame_source.py          # 抽帧 + 断流重连（已落地）
│   ├── detection/                    # 目标检测与跟踪
│   │   ├── detector.py              # Detector 接口 + YOLODetector 占位
│   │   └── tracker.py               # 跟踪占位（ultralytics 内置）
│   ├── analysis/                     # 门前规则分析
│   │   ├── rules.py                 # Rule 接口 + 具体规则（如 OddHourRule）
│   │   └── anomaly.py               # 聚合/冷却（CooldownGate）
│   ├── evidence/                     # 风险证据采集
│   │   ├── clip_collector.py        # 快照/片段采集占位
│   │   └── storage.py               # 本地/COS 存储占位
│   └── output/                       # 事件上报
│       ├── publisher.py             # Publisher 接口 + MQTTPublisher 占位
│       └── schemas.py               # 事件 schema 再导出
│   └── common/                       # 公共能力
│       ├── logging.py               # structlog 配置
│       └── timeutil.py              # 时间戳工具
├── tests/                            # 单元测试（配置/事件/规则）
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_event.py
│   └── test_rules.py
├── scripts/
│   ├── run.py                        # 启动入口
│   └── eval_stream.py               # 流质量基线评测（复用 ingestion）
├── docs/                             # 本文档体系（00-09）
├── data/                             # 本地运行数据（均 gitignore）
│   ├── evidence/                     # 取证片段
│   ├── models/                       # YOLO 权重
│   └── cache/
└── prototypes/                       # 早期验证脚本（含真实凭证，gitignore）
```

## 各目录职责一句话

- `config/`：一切可调参数与设备注册，做到"改配置不改代码"。
- `core/`：模块的中枢契约（配置、事件、编排），稳定且少变。
- `ingestion/`：与外部视频源打交道，**唯一允许持有流地址/凭证逻辑的地方**。
- `detection/` / `analysis/`：算法与规则，迭代最快、最可能替换实现。
- `evidence/` / `output/`：对外副作用（落盘、上报），便于测试时替换为 fake。
- `tests/`：契约与规则单测，保证"改算法不破坏对外事件格式"。
- `prototypes/`：已验证的连通性脚本，**仅本地，禁止提交**。
