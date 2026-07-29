# 开发环境与环境契约（DEVELOPMENT_ENV）

> 本文档定义 SilverShield Demo 的**环境契约**：在原型阶段如何稳定地跑通
> `代码 → 依赖 → 测试 → Demo 运行` 这条链路，并让一台干净机器能在 10 分钟内复现
> `视频 → 行为分析 → 风险 → 闭环`。

---

## 1. 当前环境状态（双环境）

项目处于 P0-11 Demo 稳定化阶段，**AI 运行时依赖与开发工具分处两个 Python 环境**。
这是典型 AI 工程原型状态，在当前阶段是可接受的。

| 环境 | 用途 | 关键包 |
| --- | --- | --- |
| **managed venv**（`~/.workbuddy/binaries/python/envs/default`，Python 3.13） | 开发/测试**工具链** | `ruff`、`pytest`、`pytest-asyncio` |
| **system Python 3.14** | **AI 运行时** | `torch`(CUDA)、`ultralytics`、`opencv-python`、`fastapi`、`uvicorn`、`websockets`、`python-multipart` |

**为什么 AI 运行时在 system Python 3.14？**
重依赖（torch CUDA 包很大、ultralytics 依赖复杂、OpenCV 环境易冲突）只装在该环境；
`home_perception` 的导入链在模块加载期即需要 `torch / cv2 / ultralytics`
（`runtime.pipeline` → `detection.detector`）。managed venv（3.13）没有这些包，
因此**完整测试与 Demo 运行实际都走 system Python 3.14**。

---

## 2. 为什么不现在迁移

| 维度 | 评估 |
| --- | --- |
| 成本 | torch CUDA 包很大；ultralytics 依赖复杂；OpenCV 环境可能冲突 |
| 收益（当前） | 很低——单人开发者、原型阶段，双环境已能稳定复现 |
| 合适时机 | 团队多人开发 / Docker 部署 / 服务器运行时（见 §7） |

**结论**：保持双环境，先补齐"环境契约"（本文档 + 脚本），避免未来团队成员踩坑。
**不要**现在把 torch 整体迁移到 managed venv。

---

## 3. 复现路径（Demo Reproducibility）

```bash
# 1. 拉代码
git clone <repo> && cd silver-shield

# 2. 安装依赖（核心 AI 栈 + Web 网关栈）
pip install -e ".[demo]"

# 3. 拉取 CAVIAR 帧（不入库，gitignore；默认场景 night_visit 需要）
python tests/fixtures/download_fixtures.py

# 4. 启动 Demo（默认 night_visit 场景，CAVIAR 帧）
python scripts/run_demo.py
#    打开 http://127.0.0.1:8765/
```

**用本地真实视频直接接入**（无需 CAVIAR 帧）：

```bash
python scripts/run_demo.py --video data/demo/my_door.mp4
```

> 视频只是"传感器"：经冻结 Pipeline 产出
> `身份 → 轨迹 → 行为 → 风险 → 解释 → 干预` 全链，不调用任何视觉大模型 API。

---

## 4. `scripts/check_env.py` — 环境预检

比赛现场 / 启动前一键检查依赖是否就绪，**纯标准库实现，绝不 import torch**（避免未装就崩）。

```bash
python scripts/check_env.py
```

输出分三类，缺失项给出安装提示：

- **AI 运行时 (Demo 必需)**：`torch` / `opencv-python` / `ultralytics`
- **网关 (Web)**：`fastapi` / `uvicorn` / `websockets` / `python-multipart`
- **测试**：`pytest` / `pytest-asyncio`

退出码：`0` 就绪 / `1` 有缺失（可被 CI / 脚本串联）。

也可被 `run_demo.py` 导入复用：`from check_env import run_checks`。

---

## 5. `scripts/run_demo.py` — 统一启动器

封装"先预检 → 再解析场景 → 再校验媒体 → 最后启动"的完整流程，失败时给出明确指引
而非静默崩在 YOLO 装配阶段。

```bash
python scripts/run_demo.py                       # 默认 night_visit 场景
python scripts/run_demo.py --scenario delivery_courier_normal
python scripts/run_demo.py --video data/demo/my_door.mp4
python scripts/run_demo.py --check               # 仅做环境预检，不启动
python scripts/run_demo.py --host 0.0.0.0 --port 9000
```

要点：

- **先预检，后加载**：环境检查只用标准库；通过后才懒加载 `silver_demo.gateway`
  （其顶层 import 会拉 torch）。
- **优雅降级**：视频 / CAVIAR 帧缺失时提示
  `python tests/fixtures/download_fixtures.py` 或 `--video <path>`，不让用户对着
  `ModuleNotFoundError: torch` 发懵。
- 底层等价于 `python -m silver_demo.gateway`（读取 `DEMO_SCENARIO` 等环境变量）。

---

## 6. 运行测试

```bash
python -m pytest          # system Python 3.14（已装 pytest-asyncio）
```

- `pyproject.toml` 已配置 `asyncio_mode = "auto"`，异步测试无需手动标记。
- 完整套件（42 passed）需要 AI 运行时；仅做契约/仪表盘校验时见下。

### torch-free 子集（CI 每 PR 运行，不装 AI 栈）

以下测试**不 import torch**，可在仅装轻量依赖（`pytest pytest-asyncio pydantic pyyaml
structlog numpy` + `pip install --no-deps -e .`）的环境跑通：

```
tests/contract/test_config_contract.py
tests/contract/test_state_machine_contract.py
tests/contract/test_schema_contract.py
tests/demo/test_dashboard_p0_11_4.py
tests/demo/test_dashboard_state_layer.py
tests/demo/test_dashboard_video_input.py
tests/demo/test_freeze_boundary.py
```

> 注：`tests/contract/test_input_attack_contract.py` 与
> `test_interface_contract.py` 会 import `runtime.pipeline` / `detection.detector`，
> **需要 torch**，属于 runtime 子集（见 §7 CI 拆分）。

---

## 7. CI 拆分原则（不在每 PR 装 AI 栈）

`.github/workflows/ci.yml` 分为三个 job：

| job | 触发 | 是否装 torch | 范围 |
| --- | --- | --- | --- |
| `lint` | 每 PR | 否（仅 `ruff`） | `ruff check src tests` |
| `test-contracts` | 每 PR | 否（torch-free 子集） | §6 的 7 个文件 |
| `test-runtime` | 仅 `main` / `workflow_dispatch` | 是（完整 AI 栈，CPU torch） | 完整 `pytest` |

> **关于 CI 的 Python 版本**：CI 通过 `setup-python@v5` 安装 **Python 3.12**，
> 这与本地 system Python 3.14（AI 运行时）和 managed venv Python 3.13（工具链）
> 是**相互独立的第三个运行环境**。三个小版本不一致不影响功能——CI 只跑
> 纯 Python 测试，不依赖任何特定小版本特性。

**理由**：CI 的"契约 / 配置 / 状态机 / 仪表盘"测试不需要 GPU / torch；
强行在每 PR 安装整个 AI 栈既慢又浪费。重型 runtime 测试仅在合入 `main` 或手动触发时跑。

---

## 8. 已知"不入库"资产

| 资产 | 位置 | 获取方式 |
| --- | --- | --- |
| CAVIAR 帧（jpg） | `tests/fixtures/doorway/<seq>/` | `python tests/fixtures/download_fixtures.py` |
| 真实门口视频（mp4） | `data/demo/*.mp4` | 演示者自备（gitignore） |
| caviar / cctv / delivery 场景 yaml | `config/demo/scenarios/` | 本地 untracked（引用本地视频）；入库的仅有 `night_visit.yaml` / `real_doorway.yaml` |

因此"一台干净机器 10 分钟复现"依赖：① `pip install -e ".[demo]"` ② 拉 CAVIAR 帧
（或自备 `--video`）③ `python scripts/run_demo.py`。

---

## 9. 后续（P1 以后）

- **统一 Python 环境 / Docker 化**：等团队多人开发、服务器部署、7×24 运行时再考虑。
- **摄像头 RTSP / EZVIZ 接入**：属 P0-12（真实设备源，非当前 Demo 传感器）。
- **声音多模态感知**：属 P1 / P2（用户已明确当前不做）。
- **AI 栈核心依赖独立分组**：当前 `opencv-python` / `ultralytics` 声明在 `[project.dependencies]`，
  导致 `pip install -e .`（无 `[demo]`）仍要装重型 AI 包；CI 的 torch-free job 用 `--no-deps` 绕过。
  若 torch-free 子集继续扩展，建议将 AI 栈（`opencv-python` / `ultralytics` 等）移至独立的
  `optional-dependencies` 分组（如 `[runtime]`），使 torch-free 安装无需 `--no-deps`。
  （本项非当前 PR 必需，仅记录为后续改进。）

---

## 10. 小结

| 事项 | 状态 |
| --- | --- |
| pytest-asyncio 补齐 | ✅ 已完成 |
| ruff 修复（F401 等） | ✅ 已完成 |
| 测试 42 passed | ✅ 达标 |
| 环境说明文档（本文） | ✅ 已落地 |
| 环境检查脚本 `check_env.py` | ✅ 已落地 |
| 统一启动器 `run_demo.py` | ✅ 已落地 |
| CI 拆 torch-free / runtime | ✅ 已落地 |
| 迁移全部 Python 环境 | ❌ 暂缓 |
| Docker 化 | P1 以后 |
