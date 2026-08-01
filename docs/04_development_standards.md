# 04 · 开发规范

## 4.1 语言与运行时

- Python 3.11+（部署用 3.11-slim；本机若用 3.13 仅限开发，注意 ultralytics/opencv 轮子）。
- 类型注解全程开启；`mypy` 作为可选门禁。

## 4.2 编码风格

- 格式化：`black`（line-length 100）+ `ruff-format`；门禁：`ruff`（CI/commit）。
- 命名：包/模块 `snake_case`；类 `PascalCase`；函数/变量 `snake_case`；常量 `UPPER`。
- 单文件不超过 ~400 行；阶段逻辑互不耦合，靠接口（`Detector`/`Rule`/`Publisher`）解耦。

## 4.3 配置与密钥（强制）

- **任何凭证（APP_KEY/SECRET/序列号）只走环境变量或 `.env`（gitignore）**，禁止硬编码。
- 设备清单用 `config/devices.yaml`（gitignore），模板见 `config/devices.example.yaml`。
- 阈值/模型/上报参数全部进 `config/default.yaml`，支持 `${ENV:-default}` 覆盖。
- `prototypes/` 下旧脚本含真实凭证，**已被 gitignore，禁止 `git add`**。

## 4.4 日志与可观测

- 统一 `common/logging.setup_logging()`，生产用 JSON 行日志。
- 每条对外事件日志必须带：`device_id`、`track_id`、`event_type`、`conf`。
- 禁止在日志打印完整凭证或人脸图像；取证路径可记，像素不记。

## 4.5 测试要求

- 框架 `pytest`，`asyncio_mode=auto`。
- **契约测试必写**：`test_event.py` 校验事件序列化字段；`test_config.py` 校验默认配置可加载；
  `test_rules.py` 校验规则确定性（同输入同输出）。
- 算法可替换部分（Detector/Publisher/Storage）用 fake 实现注入，保证无摄像头也能跑测试。
- 门禁：单测全绿才允许合并；覆盖率不强制数值，但核心规则需覆盖。

## 4.6 接口与版本

- 对外事件格式见 `07_event_schema.md`，**字段新增向后兼容**（只加字段不删不改语义）。
- MQTT topic / payload 变更属破坏性改动，须经 `06_api_contract.md` 评审并升版本。

## 4.7 提 PR 规范

- 一个 PR 只做一件事；标题遵循 Conventional Commits（见 05）。
- PR 描述含：动机、改动点、测试、对对外契约的影响。
- 至少 1 名 reviewer；涉及阈值/契约/隐私的 PR 需网络工程 owner 批准。
- CI（lint + test）通过方可合并；合并即删除源分支（squash）。
