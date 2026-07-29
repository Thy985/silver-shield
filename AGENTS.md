# AGENTS.md — AI 协作开发规范（SilverShield · Home 感知模块）

> 本文件是 **SilverShield · Home 感知模块** 对所有 AI 协作开发者（含 WorkBuddy / Claude Code / Cursor / 人工协作者）的强制规范。
> 所有 PR 必须通过本文档的检查项才能合并。Git 细节见 [`docs/05_git_workflow.md`](docs/05_git_workflow.md)；事件契约见 [`docs/07_event_schema.md`](docs/07_event_schema.md) / [`docs/06_api_contract.md`](docs/06_api_contract.md)。
> 本文档由项目技术负责人维护，版本 v0.1，生效日期 2026-07-18。

---

## 0. 项目愿景与定位

**SilverShield** 的目标是成为 **老年人诈骗风险数字孪生与协同预警系统**；本仓库负责其中的 **Home 感知模块**。

- 本模块 = SilverShield 全局的 **Perceive 感知逻辑模块** + **门前时空异常与蹲守识别子系统**，部署于 **Home 端**。
- 角色：**风险数字孪生（RiskTwin）的前端事实采集器** —— 把家庭入口的视频流，转成结构化"标签事件 + 风险证据"，上报中心风控引擎。
- 上下游：上游是萤石（EZVIZ）摄像头视频流；下游是中心 AI 分析服务 / 业务服务（经由 MQTT 消费 `VisitorEvent`）。
- 当前阶段：**MVP Release Candidate 已交付 + P0-11 多角色协同闭环 Demo 落地**。优先满足比赛演示；保留 v2 扩展（LLM 解释、多设备、中心联动）。

**模块边界铁律（最高优先级）**：本模块 **只输出"标签 / 事件"**（普通来访 / 待核验来访 / 异常停留 / 重复来访 / 高风险接近），**绝不直接输出"诈骗人员"结论**。是否诈骗由中心结合入户语音、物品、历史记录综合分析。

**当前阶段禁区（Phase 0 / MVP）**：
- 不输出"诈骗 / fraud / suspect"判定或字段；
- 不做 LLM 解释（推迟 v2）；
- 不做中心风控聚合 / 决策（那是中心服务职责）；
- 不引入重模型 / 重依赖拖垮边缘 CPU。

---

## 1. 架构原则

### 1.1 分层（严格自上而下依赖）

```
core/         配置 / 事件模型 / 日志 / 时间工具（被所有层依赖，不依赖业务）
  ↑
ingestion/    EZVIZ 接入 + 帧源（取流、抽帧、断流重连）
  ↑
detection/    YOLO 人员检测 + 多目标跟踪（输出带 track_id 的 Detection）
  ↑
analysis/     规则 / 时空异常（Dwell / Repeat / Pending / HighRisk）→ 生成事件
  ↑
evidence/     触发式取证（高风险才存片段，本地 + 自动过期）
  ↑
output/       MQTT 上报（Publisher，topic: silvershield/home/{device_id}/events）
common/       横切（logging / timeutil）
```

**强制规则**：
- `core` 不允许反向 import `ingestion`/`detection`/`analysis`/`evidence`/`output` 的业务代码；
- `ingestion` 不反向依赖 `detection`/`analysis`；
- `analysis` 不依赖 `evidence`/`output` 的具体实现，仅依赖其 **接口（ABC）**；
- `output` 是最外层，依赖契约（事件 JSON），不反向依赖上游；
- 循环依赖零容忍。

### 1.2 单一职责与文件大小

- 一个 `.py` 文件 = 一个类 / 一个职责；
- 单文件超过 **400 行** 必须考虑拆分（检测 / 规则层尤其要保持清晰）；
- `Detector` / `Rule` / `EvidenceCollector` / `Publisher` 均为可替换接口（ABC），主流程用 `Pipeline` 组装，禁止在 `main.py` 里堆业务。

### 1.3 显式依赖与配置

- 配置经 pydantic `Settings` 注入（`core/config.py`），支持 `${ENV:-default}` 展开；**禁止** 模块级硬编码凭证 / 设备序列号 / token；
- 不写全局可变单例（除非明确标注为只读 cache）；
- 外部客户端（EZVIZ / MQTT）通过依赖注入或工厂创建，便于测试时替换 fake。

### 1.4 模块边界铁律（详见 §3）

只输出 5 类标签事件（见 `EventType`）：`visit_normal` / `visit_pending_verify` / `abnormal_dwell` / `repeat_visit` / `high_risk_approach`。任何代码不得输出"诈骗人员 / fraud / suspect"类判定或字段。

---

## 2. Python 编码规范

### 2.1 版本与运行环境

- **Python 3.11+**；
- 依赖隔离：使用 venv（项目 `.venv` 或 `~/.workbuddy/binaries/python/envs/ss_home`），**禁止** `pip install` 污染系统 / 全局环境；
- 重依赖（torch / opencv / ultralytics）仅装 venv，CI 使用同一 venv；本地验证只需轻量契约依赖（pydantic / pyyaml / structlog / pytest）即可跑测试。

### 2.2 命名

| 类型 | 规则 | 示例 |
|------|------|------|
| 类 / 异常 | UpperCamelCase | `FrameSource`、`VisitorEvent` |
| 文件 | snake_case.py | `frame_source.py` |
| 函数 / 变量 | snake_case | `get_stream_url`、`is_odd_hour` |
| 常量 | UPPER_SNAKE_CASE | `FPS_TARGET`、`MQTT_TOPIC_PREFIX` |
| 私有 | 前缀 `_` | `_expand_env`、`_open` |

### 2.3 类型与文档

- **强制类型注解**：所有函数签名标注参数与返回类型；
- 数据模型用 `dataclasses` 或 `pydantic`；
- public API 写 `"""` docstring；实现细节用 `#`；禁止无意义注释（如 `# constructor`）；
- 中文注释允许，本团队以中文为主。

### 2.4 日志（禁止 `print`）

- 必须用 **structlog 结构化日志**（`common/logging.setup_logging` / `get_logger`）；**禁止** 裸 `print()`；
- 生产配置 `json_logs=true`（JSON 便于边缘采集）；
- **禁止** 把密钥 / token / 视频帧 / 人脸写入日志。

### 2.5 错误处理

- **禁止** `except:` 或 `except Exception:` 后静默吞掉；必须记录 + 可恢复或显式 `raise`；
- 外部调用（EZVIZ API / MQTT / 文件）包裹重试 + **指数退避**；帧源断流必须自动重连（已实现 `FrameSource`）；
- 不向上抛未分类异常；业务失败走"事件 / 状态"，不崩溃进程；
- 资源（相机 / MQTT 连接 / 文件句柄）必须显式释放（`__exit__` / context manager），进程退出前 flush 日志、停止发布、释放相机。

---

## 3. 模块边界与契约（AI 关键约束）

### 3.1 事件 Schema 不可破坏

- `PerceptionEvent` / `VisitorEvent` 字段变更属 **BREAKING**：必须先改 `docs/07` + `docs/06`，且 PR **必须 Owner 评审**；
- 新增事件类型必须先在 `docs/07` 登记，且仍限于 5 类语义（**不得** 新增"诈骗"类）；
- 契约模型的字段增删必须有对应 schema 测试（`tests/test_event.py` 等）。

### 3.2 MQTT 契约

- topic 固定：`silvershield/home/{device_id}/events`；
- payload 为事件 JSON（`PerceptionEvent.to_dict()`）；
- 离线用 ring buffer（`BufferConfig`）；publish 失败 **不丢事件**（缓冲后重发）。

### 3.3 隐私与凭证

- 凭证只在 `.env` / `config/devices.yaml`，且被 `.gitignore`；代码读环境变量，不硬编码；
- `prototypes/` 含真实凭证，已 gitignore，**绝不提交**；
- 视频帧 / 片段默认 **不离开 Home 端**；只有高风险事件触发才上报"证据引用（本地路径 / 片段 id）"，**不上传原视频到中心**；
- 默认不存全量视频；片段本地留存 + 自动过期删除。

---

## 4. 资源与性能约束（边缘 CPU）

### 4.1 帧采样预算

- 默认 `fps_target=8` 抽帧；YOLO 推理在 CPU 上控制频率，**不得** 全帧跑重模型；
- 不得为"更准"无脑提帧率 / 提模型尺寸导致边缘卡死；模型选型（YOLOv8n / v11n）需评估 CPU 推理时延。

### 4.2 资源释放

- `cv2.VideoCapture` / MQTT client / 文件句柄必须显式释放；
- 进程退出前 flush 日志、停止发布、释放相机。

---

## 5. Git 提交规范（摘要，详见 [`docs/05_git_workflow.md`](docs/05_git_workflow.md)）

- **AI / Human 分工**：AI 必须 `branch` + `commit`（含 Task scope）+ `PR`；**禁止** 直推 / merge `main`；架构决策文件仅 Owner 可落库（§6.3）；
- **分支类型**：`feat/` `fix/` `refactor/` `chore/` `docs/` `test/`（注意用 `feat/`，不是 `feature/`）；
- **Commit 格式**：`<type>(<scope>): <subject>` + body 必须含 `Task scope: ROADMAP P0-x | issue #n | governance`；
- **scope**：`ingestion` / `detection` / `analysis` / `evidence` / `output` / `core` / `config` / `deps` / `ci` / `docs`；
- **PR**：必须经 `docs/05` §3 + `.github/PULL_REQUEST_TEMPLATE.md` 自检（`ruff` + `pytest` 全绿）。

---

## 6. 禁止事项（Hard Rules）

### 6.1 业务 / 领域禁区

1. ❌ 输出"诈骗人员 / fraud / suspect"判定或字段（模块边界铁律）；
2. ❌ 破坏事件 Schema / MQTT 契约（BREAKING 未走 Owner 评审）；
3. ❌ 默认存储全量视频，或把原视频上传中心；
4. ❌ 在代码里硬编码凭证 / 设备序列号 / token；
5. ❌ 在本模块引入 LLM 解释逻辑（v2 才做）；
6. ❌ 越过本模块职责做中心风控聚合 / 决策。

### 6.2 工程禁区

1. ❌ 提交 `.env` / `config/devices.yaml` / `prototypes/`（`.gitignore` 已拦，仍禁止手动 `git add -f`）；
2. ❌ 裸 `print()`（用 structlog）；
3. ❌ `except:` / `except Exception:` 后静默吞异常；
4. ❌ 全局 `pip install` 污染；重依赖必须 venv；
5. ❌ 提交 `data/evidence/**` 与 `data/models/*.pt`（`.gitignore`）；
6. ❌ 删除 / 改坏测试以通过 CI；
7. ❌ 跳过 CI 直推 `main`；
8. ❌ 在仓库根遗留一次性文件（CI 日志 / 调试输出 / 临时压缩包）—— 用完即删，不入库；
9. ❌ 在 commit message / 代码 / 日志里写入密钥 / token；
10. ❌ 提交本应忽略的文件：模型权重 `*.pt`、证据片段 `data/evidence/**`、venv、缓存、构建产物 —— **提交前须确认 `.gitignore` 已覆盖**，不得用 `git add -f` 强加。

### 6.3 AI 协作禁区（编程约束扩展）

1. ❌ **凭空设计**：架构 / 接口决策必须有代码或 ADR 依据；跨模块 / 契约改动先提 ADR；
2. ❌ **跨阶段实现**：Phase 0 / MVP 范围外的大功能（多设备中心联动、LLM、重模型）不得现在做；
3. ❌ **重构与功能混同一 PR**：重构 PR 必须 0 行为变化；
4. ❌ **不读文档就编码**：动手前必须读 `AGENTS.md` + 相关 `docs/` + 相关代码（以代码为准，不靠文档想象）；
5. ❌ **无测试交付**：新功能 / 修 bug 必须有测试（pytest）；契约模型变更加 schema 测试；
6. ❌ **PR 前不自检**：必须跑 `ruff check src tests` 与 `pytest tests/ -q` 全绿；
7. ❌ **自行 merge / 直推 main**（§5 分工）；
8. ❌ **未授权改架构决策文件**：`docs/02_architecture.md`、`docs/08_roadmap.md`、`docs/ADR/*`、`AGENTS.md`、`CONTRIBUTING.md` —— 除非 Owner 在任务中**明确授权**（如本任务）；
9. ❌ **大规模新依赖不加说明**：新增重依赖（torch / opencv / ultralytics）须在 PR 描述说明体积 / 边缘推理影响；
10. ❌ **夹带未说明的改动**：PR 只含描述范围内的改动，不得顺手改无关文件。

---

### 6.4 仓库卫生（Repository Hygiene）

1. ❌ 提交 `.workbuddy/`（本地 Agent 工作记忆：架构决策 / 约定 / 笔记）—— 它是本地状态，已在 `.gitignore` 中排除，**永不入库**；
2. ❌ 提交 `.doc/`（团队研究草稿、AI-Risk-Agent 调研等）—— 终稿已沉淀到 `docs/`，研究稿属版本控制外产物；
3. 引入"本应忽略"的新文件（凭证 / 模型权重 `*.pt` / 证据片段 / 缓存 / venv / 构建产物 / 本地 Agent 状态）时，**先更新 `.gitignore` 再 `git add`**，不得用 `git add -f` 强加；
4. PR 不得夹带一次性文件（CI 日志 / 调试输出 / 临时压缩包）—— 用完即删。

---

## 7. 文档体系

- 顶层 `AGENTS.md`（本文件）+ `docs/00`~`docs/09` + `docs/ADR/*`；
- 文档清单见 [`docs/00_README.md`](docs/00_README.md)；
- **ADR 编写规则**：文件名 `NNNN-<kebab-case-title>.md`，NNNN 从 0001 递增不复用；状态 `Proposed → Accepted → Superseded by ADR-NNNN / Deprecated`；内容必须含背景 / 决策 / 动机 / 后果 / 替代方案；
- **本模块架构决策文件（Owner 专属）**：`docs/02_architecture.md`、`docs/08_roadmap.md`、`docs/ADR/*`、`AGENTS.md`、`CONTRIBUTING.md`。

---

## 8. CI 与质量门禁

- **当前状态**：MVP Release Candidate 已交付（tag `v0.1.0-mvp-rc`，2026-07-20）；CI 已建立（GitHub Actions：`ruff` lint + torch-free 合约测试每 PR，全栈 runtime 测试仅 main）；**289 测试全绿**；AI 经 branch + PR 协作，不直推 / 不 merge；
- **门禁目标**（接入 GitHub Actions 后）：`ruff check` 无 error + `pytest` 全过 + 契约 schema 校验；
- **PR 合并须满足**：`docs/05` §3.2 + `.github/PULL_REQUEST_TEMPLATE.md` 自检清单；
- 未建立分支保护前，仍遵循本规范走 branch + PR，AI 不直推 / 不 merge。

---

## 9. AI 协作工作流

### 9.1 接到任务时的标准流程

1. **先读文档**：本文件 + 相关 ADR + `docs/08_roadmap.md` 当前 Phase；
2. **再读代码**：相关模块的实际实现，不依赖文档描述；
3. **判断阶段**：当前任务是否在允许的阶段范围内；
4. **写 todo**：复杂任务（> 3 步）必须用 TaskCreate 记录；
5. **最小改动**：能改一行不改两行；
6. **写测试**：新功能必须有测试；bug 修复必须有回归测试；
7. **写文档**：架构决策必须落 ADR；
8. **自检**：对照 §6 逐条确认；**提交前审视本次改动是否产生需忽略的新文件**（凭证 / 模型权重 `*.pt` / 证据片段 / 缓存 / venv / 构建产物），必要时先更新 `.gitignore` 再 `git add`。

### 9.2 编码前必须回答的四个问题（Task Contract）

AI Agent 在开始编码前，必须明确回答：

1. **What changes?** —— 修改哪些文件？为什么？
2. **How to verify?** —— 测试在哪里？如何证明正确？
3. **What feedback signals exist?** —— 成功指标是什么？失败指标是什么？
4. **What is done?** —— 什么条件满足才算完成？

复杂任务（Risk Medium+ 或涉及架构 / 契约变更）的 Task Contract 须先交 Owner 审批再实现。

### 9.3 不确定时的升级路径

- 业务范围不清 → 看 `docs/08_roadmap.md` / 问用户；
- 架构选型不清 → 看 `docs/ADR/` / 提新 ADR；
- API / 契约兼容疑问 → 看 `docs/06_api_contract.md`、`docs/07_event_schema.md`；
- 测试策略疑问 → 看 `docs/04_development_standards.md` / `tests/`。

### 9.4 PR 提交前的自检清单

- [ ] 读了 `AGENTS.md` 相关章节
- [ ] 没有违反任何 §6 Hard Rules
- [ ] 改动范围与 PR 描述一致，无夹带
- [ ] 测试覆盖完整（新功能 / bug 有测试，契约变更加 schema 测试）
- [ ] 文档已同步（如需）
- [ ] `ruff check src tests` 无 error
- [ ] `pytest tests/ -q` 全部通过
- [ ] commit message 含 `Task scope`
- [ ] 提交前已确认 `.gitignore` 覆盖本次新增产物（无凭证 / 大文件 / 缓存误提交；必要时已先更新 `.gitignore`）

---

## 10. 当前阶段与例外说明

| 项 | 说明 |
| --- | --- |
| 阶段 | MVP Release Candidate 已交付（tag `v0.1.0-mvp-rc`，P0-1~P0-10 全链路 + P0-10.5 冻结治理，289 测试全绿）；P0-11 多角色协同闭环 Demo 已完成（12/12 端到端验证）。**本行为 README「当前状态」SSOT 的投影，须与 README 保持一致** |
| 已完成 | P0-1 工程脚手架；P0-2 萤石稳健取流（RTSP/HLS + 断流重连）；P0-3 YOLO 检测；P0-4 FPS Benchmark；P0-5 目标跟踪；P0-6 VisitorEvent；P0-7a/b 特征 + 规则引擎；P0-8/9/10 决策 / 行动 / main 装配；P0-10.5 架构冻结治理；P0-11 多角色协同闭环 Demo（详见 `docs/08_roadmap.md` 与 `README.md`） |
| 待办 | 无（MVP 范围全部交付）。后续增强归 v2 / P1：LLM 解释、多设备、中心联动、真实 App / 用户体系 / 推送 |
| 已知基线偏差 | 早期 3 个脚手架提交已落 `origin/main`（早于本约定，属基线）；`prototypes/` 为历史验证脚本（含真实凭证，已 gitignore） |
| 例外不视为违规 | 上述基线偏差已记录；新增代码必须按目标架构，不得延续"硬编码凭证 / 裸 print / 静默异常"等问题 |

**新增代码不得延续已知问题，必须按目标架构编写。**
