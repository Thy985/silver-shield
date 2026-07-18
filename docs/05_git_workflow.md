# 05 · Git 分支策略

## 5.1 总体策略（轻量 GitFlow，适配 5 人小队 + 比赛节奏）

```
main            ← 稳定可演示/可交付（受保护，只接受 merge）
  ▲
develop         ← 日常集成干线
  ▲
feature/*      ← 功能开发（从 develop 切出，完成后 squash 回 develop）
fix/*          ← 紧急修复
```

- **MVP 阶段可进一步简化**：直接从 `main` 切 `feature/*`，PR 审查后 squash 合回 `main`，
  先不长期维护 `develop`，降低协作成本；进入"增强版"开发后再起 `develop`。
- 单人短时改动允许直接推 `main` 短分支；但凡跨成员联调，**必须 PR + review**。

## 5.2 分支命名

| 类型 | 命名 | 示例 |
| --- | --- | --- |
| 功能 | `feature/<简短描述>` | `feature/yolo-detector` |
| 修复 | `fix/<问题>` | `fix/reconnect-loop` |
| 文档 | `docs/<主题>` | `docs/event-schema` |
| 实验 | `exp/<想法>` | `exp/rtsp-latency` |

## 5.3 提交信息（Conventional Commits）

```
<type>(<scope>): <subject>
# type: feat | fix | docs | refactor | test | chore | perf
# scope: ingestion | detection | analysis | evidence | output | core | ci
```
示例：
- `feat(detection): add YOLOv8n person detector`
- `fix(ingestion): reconnect on read failure with backoff`
- `docs(event-schema): align labels with architecture v2`

## 5.4 版本与发布

- 语义化版本 `vX.Y.Z`；MVP 演示前打 `v0.1.0`（alpha）、`v1.0.0`（可交付）。
- 打 tag 即触发构建产物（Docker 镜像 / 部署包）。
- 每次 tag 在 `docs/` 追加变更说明（或在 Release Notes）。

## 5.5 保护规则（仓库设置）

- `main` 禁止直接 push；需 PR + 至少 1 review + CI 绿。
- 强制 `.gitignore` 覆盖 `.env`、`prototypes/`、`config/devices.yaml`、`data/evidence/**`、
  `data/models/*.pt`，从机制上堵住凭证/大文件入库。
- 启用 PR 模板（动机/改动/测试/契约影响）。

## 5.6 初始提交建议

当前脚手架已就位，首次提交只包含**基础设施**（无业务算法）：

```
chore(scaffold): project skeleton, config, docs and contracts
```
业务算法（YOLO/规则/上报）在后续 `feature/*` 分支逐步实现，保持主分支始终可运行。
