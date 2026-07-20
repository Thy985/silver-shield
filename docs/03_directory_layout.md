# 03 · 目录结构（Repository Layout）

> **新人第一站**：先看本文档了解仓库结构，再看 [`API_REFERENCE.md`](API_REFERENCE.md)（接口入口）与 [`CONTRACTS.md`](CONTRACTS.md)（冻结契约）。
> 本文档描述 `origin/main` 的当前结构（P0-10.5.4 仓库卫生清理后）。

## 顶层结构

```
silver-shield/
├── README.md                  # 项目入口 + 架构图 + "团队第一入口"链接
├── AGENTS.md                  # AI 协作开发规范（所有协作者强制）
├── pyproject.toml             # 包元数据 + ruff/pytest 配置
├── requirements.txt           # 运行时依赖
├── requirements-dev.txt       # 开发/测试依赖
├── .gitignore                 # 忽略 .env / .workbuddy / .doc / 模型权重 / 证据
├── .env.example               # 环境变量模板（凭证占位，真实值不入库）
├── .pre-commit-config.yaml    # 提交前 lint/format
├── Dockerfile                 # 边缘部署镜像
├── docker-compose.yml         # 本地 MQTT + 模块一键起
├── config/                    # 可调参数与设备注册（改配置不改代码）
├── src/home_perception/       # 包根（见下）
├── tests/                     # 单元测试 + 契约测试
├── scripts/                   # 运行 / Demo 脚本
├── benchmark/                 # 性能基准
├── docs/                      # 文档体系（00-09 + ADR + DX 四篇）
└── data/                      # 本地运行数据（均 gitignore）
```

## `src/home_perception` 包结构

```
home_perception/
├── main.py              # CLI 入口：调用 runtime.from_settings 装配并运行
├── core/                # 中枢契约：配置、事件枚举、工具（被所有层依赖，不依赖业务）
│   ├── config.py        #   Settings 加载 + ${ENV} 展开 + pydantic 校验
│   └── event.py         #   EventType 枚举 + EvidenceRef（PerceptionEvent 唯一权威在 analysis/perception.py）
├── ingestion/           # 取流与帧源（唯一允许持有流地址/凭证逻辑的地方）
│   ├── frame_source.py  #   FrameSource(ABC) + CaviarFrameSource（比赛 Demo 实现）
│   └── ezviz_client.py  #   EZVIZ token/流地址获取
├── detection/           # 目标检测与跟踪
│   ├── detector.py      #   Detector(ABC) + YOLODetector
│   ├── tracker.py       #   Tracker（多目标跟踪，输出 track_id）
│   └── schemas.py       #   DetectionResult 等数据结构
├── analysis/            # 领域推理层（见"三层语义"）
│   ├── event.py         #   VisitorEvent（事实层：进入/离开/停留）
│   ├── event_builder.py #   DetectionResult → VisitorTrack → VisitorEvent
│   ├── feature.py       #   RiskFeature 数值特征
│   ├── feature_extractor.py # FeatureExtractor：VisitorEvent → RiskFeature
│   ├── perception.py    #   PerceptionEvent（规则发现的异常感知事件，唯一权威）
│   ├── rule_engine.py   #   RuleEngine：RiskFeature → PerceptionEvent（5 类 + 1 复合 + CooldownGate）
│   ├── warning.py       #   WarningEvent（决策层状态机）
│   ├── decision_engine.py   # DecisionEngine：PerceptionEvent → WarningEvent
│   ├── decision_policy.py   # DecisionPolicy（可替换决策策略）
│   └── anomaly.py       #   CooldownGate 等聚合/冷却工具
├── evidence/            # 风险证据采集（副作用）
│   ├── clip_collector.py #  触发式快照/片段采集
│   └── storage.py       #  本地/COS 存储
├── output/              # 事件上报（副作用）
│   ├── publisher.py     #   Publisher(ABC) + MQTTPublisher：上报感知事件到中心
│   └── schemas.py       #  事件 schema 再导出
├── action/              # 行动层（副作用执行）
│   ├── executor.py      #   ActionExecutor：WarningEvent → List[ActionCommand]
│   ├── command.py       #   ActionCommand（行动指令，含幂等 id）
│   ├── dispatcher.py    #   ActionDispatcher：分发执行
│   ├── publisher.py     #   MQTTPublisher（下发社区工单等）
│   └── notifier.py      #   通知适配器（家属通知）
├── runtime/             # 装配层（Assembly）
│   ├── pipeline.py      #   PerceptionPipeline.from_settings()：唯一装配入口
│   ├── config.py        #   运行时 Settings 组装
│   └── lifecycle.py     #   DemoClock / run_demo（演示与生命周期）
└── common/              # 横切能力
    ├── logging.py       #   structlog 配置
    └── timeutil.py      #   时间戳工具
```

## 三层语义（避免职责错位）

| 层 | 角色 | 典型误用（禁止） |
|----|------|----------------|
| `runtime/` | **装配层**：用 `from_settings()` 把各层连成流水线；不写业务逻辑 | 在 Pipeline 里直接调 RuleEngine 做决策、绕过装配 |
| `analysis/` | **领域推理层**：事实 → 特征 → 异常事件 → 告警事件；纯函数/规则，无副作用 | 在 RuleEngine 里发 MQTT / 调外部服务 / 产生最终判定 |
| `action/` | **副作用执行层**：把 WarningEvent 变成对外行动（MQTT/通知/工单） | 把 MQTT 写进 RuleEngine；把决策逻辑放进 Executor |

数据流向（自上而下，禁止反向依赖）：

```
ingestion → detection → analysis → evidence/output → action → runtime(组装)
core  被所有层依赖，不依赖业务
common 横切
```

## `tests/` 结构

```
tests/
├── conftest.py            # 公共 fixture
├── test_*.py              # 单元/集成测试（config/event/feature/detector/tracker/benchmark/runtime/warning 等）
├── contract/              # 契约测试（冻结门禁，攻击性测试）：schema / interface / state-machine / input-attack / config
└── fixtures/              # 测试数据（CAVIAR 真实场景）+ 下载脚本
```

## 被忽略、不应入库的目录

以下目录/文件已被 `.gitignore` 排除，**禁止 `git add -f`**：

- `.env` / `config/devices.yaml` / `prototypes/`：密钥与真实凭证
- `.doc/`：团队研究草稿（终稿已沉淀到 `docs/`）
- `.workbuddy/`：本地 Agent 工作记忆（仅本地，不共享）
- `data/models/*.pt`：模型权重；`data/evidence/**`：取证片段
- `__pycache__/`、`.venv/`、`.pytest_cache/`、构建产物

## 各目录职责一句话

- `config/`：一切可调参数与设备注册，"改配置不改代码"。
- `core/`：模块的中枢契约（配置、事件枚举、工具），稳定且少变。
- `ingestion/`：与外部视频源打交道，唯一允许持有流地址/凭证逻辑。
- `detection/` / `analysis/`：算法与规则，迭代最快、最可能替换实现。
- `evidence/` / `output/` / `action/`：对外副作用（落盘、上报、行动），便于测试时替换为 fake。
- `runtime/`：装配入口，稳定契约，新成员接入从 `PerceptionPipeline.from_settings()` 开始。
- `tests/`：契约与规则单测，保证"改算法不破坏对外事件格式"。
- `scripts/` / `benchmark/`：运行 / Demo / 性能基准，非核心链路。
