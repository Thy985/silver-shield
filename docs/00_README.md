# SilverShield · Home 感知模块 — 设计文档索引

> 本目录为 **Home 感知模块**（家庭入口区域实时感知与风险证据采集）的工程与架构设计文档。
> 模块在 SilverShield 全局中对应 **Perceive 感知** 逻辑模块 + **门前时空异常与蹲守识别** 子系统，
> 部署于 **Home 端**，是风险数字孪生（RiskTwin）的前端事实采集器。

## 文档清单

| 编号 | 文件 | 职责 |
| --- | --- | --- |
| AGENTS | [`../AGENTS.md`](../AGENTS.md) | **AI 协作开发强制规范（顶层）** —— 所有 PR 须满足；Git 约定见其 §5 |
| 00 | `00_README.md` | 本索引 |
| API | `API_REFERENCE.md` | **团队第一入口**：稳定公共 API 表面（入口 / 可替换接口 / 禁止依赖） |
| CONTRACT | `CONTRACTS.md` | 冻结契约（三级冻结 + Freeze Gate + 黑名单字段） |
| ARCH | `ARCHITECTURE.md` | 系统架构总览（数据流图 + 分层映射 + 红线摘要） |
| CONTRIB | `CONTRIBUTING.md` | 贡献指南（分支 / 提交 / 测试 / 冻结纪律） |
| 01 | `01_module_positioning.md` | 模块在系统中的定位、边界、上下游 |
| 02 | `02_architecture.md` | 模块内部架构、数据流、与三层引擎的关系 |
| 03 | `03_directory_layout.md` | 目录树与每个目录的职责 |
| 04 | `04_development_standards.md` | 编码、测试、日志、配置、提 PR 规范 |
| 05 | `05_git_workflow.md` | 分支策略、提交规范、发布 |
| 06 | `06_api_contract.md` | 与中心（AI 分析服务 / 业务服务）的接口契约 |
| 07 | `07_event_schema.md` | 感知事件（VisitorEvent）字段与取值说明 |
| 08 | `08_roadmap.md` | 分阶段研发路线与第一阶段任务拆解 |
| 09 | `09_risks.md` | 技术 / 项目风险与缓解 |
| ENV | `DEVELOPMENT_ENV.md` | 开发/运行双环境说明（managed venv 跑 ruff/pytest · system Py3.14 跑 AI 栈 + E2E） |
| ADR | [`ADR/`](ADR/) | **架构决策记录**（为什么这样设计，可追溯）；编写规则见 [`ADR/README.md`](ADR/README.md) |

### P0-11 多角色协同闭环展示层（Demo）文档

| 编号 | 文件 | 职责 |
| --- | --- | --- |
| DESIGN-11.4 | `DESIGN-p0-11-4-role-based-workflow.md` | P0-11.4 三视图（① 风险发现 / ② 家属确认 / ③ 社区处置）设计：阶段叙事、共享 `DemoAggregateState`、方案 A 单按钮 |
| DEMO-SCRIPT | `DEMO-SCRIPT-P0-11-5b.md` | P0-11.5b 5 分钟演示剧本（口播 + 切 Tab + 点按钮 SOP，与 E2E 对齐） |

> Demo 展示层的架构决策见 [`ADR/0015`](ADR/0015-p0-11-demo-architecture.md)（技术选型）、
> [`ADR/0016`](ADR/0016-p0-11-3-5-demo-runtime-lifecycle.md)（运行时生命周期）、
> [`ADR/0017`](ADR/0017-p0-11-role-based-workflow-demo.md)（多角色协同闭环范围收敛）。

## 设计依据

本模块设计对齐团队《银龄盾 架构设计完善版（V2.0）》以及《IRMS 工程定稿版》，核心约束摘录：

- **边界**：本模块只输出"标签/事件"（普通来访 / 待核验来访 / 异常停留 / 重复来访 / 高风险接近），
  **不直接输出"诈骗人员"结论**；是否诈骗由中心结合入户语音、物品、历史记录综合分析。
- **归属**：网络工程同学负责萤石平台接入、设备接入、视频/事件流、部署、安全与**门前采集**（即本模块）。
- **引擎分层**：风险引擎为 **Rule + ML 两层**，LLM 解释推迟到第二版；本模块负责 Rule + ML 侧的
  门前信号抽取与评分输入，**不负责 LLM 解释**。
- **隐私**：范围仅覆盖自家门前，敏感区域遮挡，高风险才存片段，片段设自动删除期。

## 与 `.doc` 设计稿的关系

`../.doc/设计思路研究/` 下为比赛前期的多版思路草稿（九层/八层/四层体系、IRMS、三方博弈、六维机理等）。
其中 `银龄盾_老年诈骗风险数字孪生系统_架构设计完善版(1).docx` 为**统一终稿**，本目录所有结论以该终稿为准；
其余草稿仅作理论背景参考（如"诈骗成功方程式-六维机理"支撑门前信号选取，"三方博弈"支撑协同升级策略）。
