# 银龄盾（SilverShield）· 高性能计算公共平台（傲飞）使用指南

> 适用对象：已申请到「高性能计算公共平台（傲飞 Aofei）」账号、但对该平台不熟悉的本项目成员。
> 目标：把 **SilverShield Home 感知模块 / 多角色协同闭环 Demo** 在平台上跑起来（运行 GPU 推理 + 演示 Dashboard），并知道怎么上传代码、模型、视频，怎么用 GPU，怎么对外访问。
> 文档性质：操作手册，按步骤执行即可；平台界面名词均来自《高性能计算公共平台普通用户手册》。

---

## 0. 先搞清楚：这个平台到底是什么

傲飞平台是一个 **网页控制台** 式的 AI / HPC 算力平台（底层是 Kubernetes）。你**不需要**自己装 Linux、配 SSH 服务器、买显卡——你只要在网页上「创建环境 / 提交作业」，平台给你分配带 CPU/GPU 的容器。

和你本地 Windows 电脑的区别：

| 你本地（Windows） | 傲飞平台 |
|---|---|
| 双击 `python` 运行 | 在网页创建「开发环境」或「任务式建模」作业 |
| 文件在 `C:\...\` | 文件在平台的「文件管理 / 私有文件」（`/private`） |
| GPU 是你显卡 | GPU 由平台「资源组」分配（整卡 / vGPU / MIG） |
| 浏览器直接开 `127.0.0.1:8765` | 服务端口要先做「端口映射」或走 SSH 隧道才能从外网访问 |

平台里有 **AI 空间** 和 **HPC 空间** 两大块。对本项目，**AI 空间** 就够用了（开发环境、任务式建模、推理服务、数据/模型管理都在这里）。HPC 空间的「控制台 / 应用模板」是命令行作业，本项目暂不需要。

---

## 1. 本项目的需求 → 平台功能映射

一键对照，知道每件事该用平台的哪个功能：

| 我们要做的事 | 平台对应功能 | 是否必须 |
|---|---|---|
| 交互式跑 Demo（Dashboard 常驻、可点按钮） | **AI 空间 → 开发环境**（开 GPU + 端口映射） | ✅ 主路径 |
| 批量跑一次推理 / 评测 / 训练 | **AI 空间 → 任务式建模**（PyTorch 作业） | 可选 |
| 把检测器封装成 REST/gRPC 在线服务 | **AI 空间 → 推理服务** + 模型管理 | 进阶可选 |
| 存放源码、视频、模型权重 | **文件管理 → 私有文件**（`/private`） | ✅ |
| 存放演示数据集 | **AI 空间 → 数据管理 → 数据集**（`/dataset`，可挂载） | 可选 |
| 看训练/推理曲线 | **AI 空间 → TensorBoard** | 可选 |

---

## 2. 第一次上平台：前置准备

### 2.1 确认你有 GPU 额度
- 左侧导航 → **工单管理 → 特殊规格配置**：查看是否已有「AI 资源（含 GPU）」配额。
- 如果没有，先提 **创建工单** 申请 GPU 资源组（普通用户创建开发环境时「选择资源组」会列出可用规格）。
- 本项目 Demo 至少需要 **1 张 GPU（整卡或 vGPU 均可）**；CPU 模式也能跑，但 YOLO 推理很慢，不建议用于演示。

### 2.2 把项目代码弄到平台
两种方式，任选其一：

**方式 A（推荐，若平台容器能上网）：在开发环境里 `git clone`**
- 先按 §3 创建开发环境并进入终端（JupyterLab 终端或 SSH），然后：
  ```bash
  cd /private
  git clone <你的仓库地址> silver-shield
  cd silver-shield
  ```

**方式 B（离线稳妥）：先传压缩包**
- 平台 **文件管理 → 私有文件** → 上传 `silver-shield.zip`。
- 进入开发环境终端后解压：
  ```bash
  cd /private
  unzip silver-shield.zip -d silver-shield
  cd silver-shield
  ```

> ⚠️ 不要上传 `.env`、`config/devices.yaml`、`prototypes/`、`data/evidence/**`、模型权重 `*.pt` 到公开位置；这些按项目规范本就 `.gitignore`，平台侧也不要放进「共享文件」。

### 2.3 上传运行必需的资源文件
| 资源 | 放哪 | 说明 |
|---|---|---|
| `yolo11n.pt`（YOLO 权重） | 仓库根目录（即 `silver-shield/yolo11n.pt`） | 检测器 `YOLO("yolo11n.pt")` 会在工作目录找它；平台若无外网，ultralytics 无法自动下载，**必须上传** |
| 演示视频 `*.mp4` | `silver-shield/data/demo/` | 用 `--video` 直接接入，最省事 |
| 场景 YAML | `silver-shield/config/demo/scenarios/` | 已入库（`night_visit.yaml` / `real_doorway.yaml`）；`cctv_surveillance_suspicious.yaml` 等本地文件需自行上传 |

> CAVIAR 真帧（`tests/fixtures/...`）默认不入库，且需联网 `python tests/fixtures/download_fixtures.py` 拉取。**在平台上做演示，最稳的是直接传一段真实门前视频，用 `--video data/demo/xxx.mp4` 跑**，避免依赖外网。

---

## 3. 方案 A：用「开发环境」跑 Demo（推荐主路径）

开发环境 = 一个**常驻**的带 GPU 容器，你可以在里面像用远程电脑一样跑命令、开 JupyterLab、SSH 登录，并且通过「端口映射」把 Dashboard 暴露出来。

### 步骤 1 · 创建开发环境
左侧导航 → **AI 空间 → 开发环境** → 点 **创建**，按下面填：

| 参数 | 填什么 | 备注 |
|---|---|---|
| 开发环境名称 | `silver-shield-demo` | 只能小写字母/数字/中划线，≤36 位 |
| 选择资源组 | 选含 **GPU** 的规格 | 至少 1 GPU |
| 镜像类型 | **内置镜像** → 选系统预制的 **PyTorch（含 CUDA）** 镜像 | 已带 torch+CUDA，最省事；若没有合适内置镜像，选「自定义」上传你按 §5 打的镜像 |
| SSH 远程开发 | **开启** | 登录方式选「密码」（平台自动生成）或上传密钥对 |
| 立即启动 | 勾选 | 创建完直接进「运行中」 |
| 工作目录 | 选 `/private/silver-shield`（你 §2.2 上传的代码目录） | 会映射进容器内 `/private` |
| 数据集 | 可选 | 若把视频放「数据集」里，会挂到 `/dataset` |
| 端口映射 | 见步骤 4 | 先创建，之后也能补 |
| 环境变量 | 见 §6 | 先创建，之后也能补 |

创建成功后，环境状态变「运行中」。

### 步骤 2 · 进入环境并装依赖
- 点 **打开** → JupyterLab（内置镜像才有），或用 **SSH 连接信息** 里的命令登录。
- 在终端里安装项目依赖（一次性，装完建议「保存镜像」见步骤 5）：
  ```bash
  cd /private/silver-shield
  pip install -e ".[demo]"
  ```
  这会把 `torch / opencv / ultralytics / fastapi / uvicorn / websockets` 全部装上。
- 装完做预检（项目自带，纯标准库，不碰 torch）：
  ```bash
  python scripts/check_env.py
  ```
  看到 `✅ 环境就绪` 即可。

### 步骤 3 · 放好模型权重与视频
```bash
# 确保 yolo11n.pt 在仓库根目录（从你上传的位置拷过来）
ls /private/silver-shield/yolo11n.pt

# 演示视频放这（示例）
mkdir -p /private/silver-shield/data/demo
# 把你的 mp4 上传到 私有文件 后，在终端 mv/cp 进来
```

### 步骤 4 · 配置端口映射（关键，否则外网打不开 Dashboard）
- 在开发环境行点 **端口映射** → 创建：
  - 服务名称：`demo-8765`（小写字母/数字开头）
  - 端口号：`8765`（Demo 网关默认端口）
- 保存后，详情页会显示「平台分配的外部端口」。你从自己浏览器访问的是 **这个外部端口**，不是 8765。

### 步骤 5 · 启动 Demo（务必后台运行）
要让 Dashboard 在容器里常驻，用 `nohup` 或 JupyterLab 的「终端」里跑（不要在前台直接 `python` 然后把网页关了，否则进程跟着死）：
```bash
cd /private/silver-shield
# 强制用 GPU、绑定 0.0.0.0 以便端口映射能转发
export SILVER_DEMO_DEVICE=cuda:0
export DEMO_HOST=0.0.0.0
export DEMO_PORT=8765
nohup python scripts/run_demo.py --scenario cctv_surveillance_suspicious \
    > demo.log 2>&1 &
```
- 想用本地视频替代场景：`--video data/demo/xxx.mp4`。
- 想先只验证不启动：`python scripts/run_demo.py --check`。

启动后看日志：
```bash
tail -f demo.log
```
出现 `访问: http://127.0.0.1:8765/` 即成功。

### 步骤 6 · 从外网打开 Dashboard
- 用步骤 4 拿到的「平台分配外部端口」，在浏览器打开 `http://<平台分配地址>:<外部端口>/`。
- 或走 SSH 隧道（更稳，不依赖端口映射）：
  ```bash
  # 在你本机执行（不是平台里）
  ssh -N -L 8765:localhost:8765 <平台SSH连接信息里的 用户@IP -p 端口>
  # 然后本机浏览器开 http://127.0.0.1:8765/
  ```

### 步骤 7 · 停止前先「保存镜像」
开发环境**停止时会警告改动丢失**。如果你装了依赖、想下次直接复用：
- 点环境 **更多 → 保存镜像**，填镜像名（如 `silver-shield-gpu`），下次创建开发环境选「自定义镜像」即可，免去重装 torch。

---

## 4. 方案 B：用「任务式建模」跑一次性批处理（可选）

适合：离线跑一遍评测、跑 benchmark、或（将来）训练/微调 YOLO。任务是**提交即运行、结束即退出**，不适合常驻 Dashboard。

左侧导航 → **AI 空间 → 训练服务 → 任务式建模** → **创建**：

| 参数 | 填什么 |
|---|---|
| 任务名称 | `silver-shield-eval` |
| 资源组 | 选含 GPU 的 |
| 创建方式 | **自定义算法** |
| 任务类型 | **PyTorch** |
| 高性能网络 | 可选开启（多卡才需要，单卡无视） |
| 容器镜像 | 选你按 §5 打的自定义镜像（含全部依赖），或平台 PyTorch 镜像 |
| 运行命令 | `python scripts/run_demo.py --scenario cctv_surveillance_suspicious` 或你的评测脚本 |
| 工作目录 | 指向你在文件系统上传的代码目录 |
| 输入目录 / 输出目录 | 数据集 / 结果输出目录 |
| 环境变量 | `SILVER_DEMO_DEVICE=cuda:0` 等（见 §6） |

提交后平台拉起容器跑命令，在「实例信息 → 日志」看输出，「资源占用」看 GPU 使用情况。跑完状态变「成功 / 失败」。

---

## 5. 进阶：打一个自定义 Docker 镜像（推荐长期做法）

每次 `pip install -e ".[demo]"` 要下几百 MB 的 torch/ultralytics，慢且占配额。**一次打好镜像，处处复用**：

项目根已有 `Dockerfile`（基于 `python:3.11-slim`）。但它装的是 **CPU 版 torch**（默认 pip 源）。要 GPU，请改用官方 PyTorch CUDA 基础镜像，例如：

```dockerfile
FROM pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 演示入口（按需）
EXPOSE 8765
CMD ["python", "scripts/run_demo.py"]
```

构建并推到平台 **镜像管理 → 我的镜像 → docker**（按手册上传），之后「开发环境 / 任务式建模」选「自定义镜像」即可，无需再 pip 安装。

> 平台也支持 **Singularity** 镜像（`镜像管理 → 我的镜像 → Singularity`），HPC 场景常用；本项目用 docker 镜像足够。

---

## 6. 必设的环境变量与配置清单

| 变量 / 配置 | 值 | 作用 |
|---|---|---|
| `SILVER_DEMO_DEVICE` | `cuda:0` | **强制用 GPU**。网关解析顺序：此变量 > `torch.cuda.is_available()` > `cuda:0` > `cpu`；不设且平台有 GPU 也会自动用 cuda:0，但显式设最稳 |
| `DEMO_HOST` | `0.0.0.0` | **必须 0.0.0.0**，端口映射才能从外部转发；默认 `127.0.0.1` 外部访问不到 |
| `DEMO_PORT` | `8765` | Dashboard 端口，对应步骤 4 的端口映射 |
| `config/default.yaml` → `runtime.detector.model` | `yolo11n.pt` | 权重文件名，确保该文件在仓库根目录 |
| `config/default.yaml` → `runtime.detector.device` | `cpu`（原值） | Demo 网关会被上面的 `SILVER_DEMO_DEVICE` 覆盖，不必改；若直接跑 `scripts/run.py` 生产路径才读这个 |

---

## 7. 常见坑 & 排错（都是本项目踩过的）

1. **外网打不开 Dashboard**
   → 九成是 `DEMO_HOST` 没设 `0.0.0.0`，或没做端口映射 / SSH 隧道。确认 `nohup` 进程还在（`ps aux | grep run_demo`）。

2. **`❌ 缺少依赖` / import torch 失败**
   → 没装 `-e ".[demo]`，或用了非 GPU 镜像导致 torch 缺失。重跑 `pip install -e ".[demo]"`；装完 `python scripts/check_env.py` 验证。

3. **YOLO 报找不到 `yolo11n.pt` 或一直尝试下载**
   → 平台无外网时 ultralytics 无法自动下载。把 `yolo11n.pt` 上传到**仓库根目录**（工作目录）。

4. **GPU 没用上 / `cuda:0` 不可用**
   → 开发环境资源组没选 GPU；或没设 `SILVER_DEMO_DEVICE=cuda:0`。`nvidia-smi` 看容器里有没有卡。

5. **⚠️ 千万不要把场景里的 `fps_target` 设成 `0`**
   → 实测：抽帧参数变 0 会导致重复来访检测窗口被拉宽，HIGH 风险永远不触发，Demo 故事讲不通。保持默认（如 8 / 0.5 秒间隔）。

6. **CAVIAR 帧缺失**
   → 用 `--video data/demo/xxx.mp4` 直接接本地视频，最简单；别依赖联网下载 fixture。

7. **停止环境后依赖没了**
   → 停止前「保存镜像」（步骤 7），或改用 §5 自定义镜像，避免每次重装。

8. **端口映射显示为空**
   → 管理员改过映射端口范围会把旧分配端口关掉；点列表上方「刷新」重新分配即可（手册 §3.1.6）。

---

## 8. 速查表

| 我想… | 去平台哪里 | 关键动作 |
|---|---|---|
| 跑 Demo 看 Dashboard | AI 空间 → 开发环境 | 开 GPU + 端口映射 8765 + `SILVER_DEMO_DEVICE=cuda:0` + `DEMO_HOST=0.0.0.0` |
| 传代码/视频/权重 | 文件管理 → 私有文件 | 上传后映射到 `/private` |
| 跑一次评测/训练 | AI 空间 → 任务式建模 | PyTorch 作业 + 自定义镜像 + 运行命令 |
| 把检测器变 REST 服务 | AI 空间 → 推理服务 | 模型管理注册 + `handle.py`/`deploy.json` 自定义镜像 |
| 看 GPU 占用 | 任务式建模 → 实例 → 资源占用 | — |
| 复用环境不重装 | 开发环境 → 更多 → 保存镜像 | 下次选自定义镜像 |

---

## 9. 一键启动检查清单

- [ ] 已申请并获得 GPU 资源组额度
- [ ] 代码已进 `/private/silver-shield`（git clone 或上传解压）
- [ ] `yolo11n.pt` 在仓库根目录；演示视频在 `data/demo/`
- [ ] 开发环境已创建、状态「运行中」、已选 GPU 镜像
- [ ] 已 `pip install -e ".[demo]"` 且 `python scripts/check_env.py` 通过
- [ ] 已配置端口映射（8765）或准备 SSH 隧道
- [ ] 启动命令带 `SILVER_DEMO_DEVICE=cuda:0 DEMO_HOST=0.0.0.0`
- [ ] `tail -f demo.log` 看到 `访问: http://...:8765/`
- [ ] 外网能打开 Dashboard，三视图（风险发现 / 家属确认 / 社区处置）切换正常
- [ ] 停止前已「保存镜像」

---

> 备注：本指南基于《高性能计算公共平台普通用户手册》（傲飞）与 SilverShield 当前代码（`scripts/run_demo.py`、`config/default.yaml`、检测器 `YOLO("yolo11n.pt")`、网关 `_resolve_inference_device`）整理。平台界面若升级，以平台实际为准；项目启动命令与端口以仓库代码为准。



## 限制条件：

高性能计算机平台的可用资源就只有2核CPU和2G内存
