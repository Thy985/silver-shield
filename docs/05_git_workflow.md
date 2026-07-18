# 05 · Git 工作流（对齐 AGENTS.md §5）

> 本文件是顶层 `AGENTS.md` 第 5 章《Git 提交规范》在 Home 感知模块（Python 包 `home_perception`）的**详细落地版**。
> 权威来源以 `AGENTS.md` 为准；本文件仅把 scope / ROADMAP 任务编号 / 命令替换为适配本仓库的取值。
> `AGENTS.md` 已建立（仓库根）；`CONTRIBUTING.md` 暂未单独建立，以 `AGENTS.md` 为准。

## 0. AI / Human 提交分工

### 0.1 核心规则

| 行为 | AI | Human Owner | 备注 |
| --- | --- | --- | --- |
| 创建独立 branch | ✅ 必须 | ✅ | AI 不允许在 `main` 直接工作 |
| 创建 commit | ✅ 可以 | ✅ | 必须含 Task scope（见 §2.5） |
| 创建 PR | ✅ 必须 | ✅ | 所有改动必须经 PR |
| 直接 push 到 `main` | ❌ 禁止 | ✅ | `main` 受保护 |
| Merge PR | ❌ 禁止 | ✅ 专属权限 | 仅 Human Owner 可 merge |
| 架构决策类文件 commit | ❌ 禁止（除非明确授权） | ✅ 专属权限 | 见 §0.3 |

### 0.2 AI 标准工作流

```
1. git checkout -b <type>/<scope>-<short-desc>     # 从 main（Phase 0 无 develop）
2. 修改文件 / 创建 commit（必须含 Task scope）
3. git push -u origin <branch>                      # 需先配置 remote
4. gh pr create ...（或 GitHub UI 创建 PR）
5. 通知 Human Owner review + merge
```

**禁止**：`git push origin main` · `git merge` / `gh pr merge` · 修改 `main` 分支。

### 0.3 架构决策类文件清单（本仓库）

以下文件 AI **不得自行 commit / merge**，仅 Human Owner 可落库：

- `docs/02_architecture.md`（= 顶层架构文档 ARCHITECTURE）
- `docs/08_roadmap.md`（= 顶层路线图 ROADMAP）
- `docs/ADR/*.md`（架构决策记录，若建立）
- `AGENTS.md`（已建立，仓库根）/ `CONTRIBUTING.md`（暂未单独建立）
- 例外：Human Owner 在任务中明确授权时，AI 可改该次涉及的具体文件，但仍须走 branch + PR，且不得自行 merge。

> 注意：`docs/06_api_contract.md` 与 `docs/07_event_schema.md` 是对外接口契约，
> 破坏性改动会直接击穿中心消费端，**合并前必须 Owner 评审**（虽不在硬禁止清单）。

## 1. 分支策略

### 1.1 分支模型

```
main          受保护，只接受 PR 合入；始终可构建可运行
  └─ develop   Phase 1 启用后的日常集成分支（当前 Phase 0 直接从 main 切）
       ├─ feat/<scope>-<short-desc>
       ├─ fix/<scope>-<short-desc>
       ├─ refactor/<scope>-<short-desc>
       ├─ chore/<short-desc>
       ├─ docs/<short-desc>
       └─ test/<short-desc>
release/<version>   Phase 4 启用
```

### 1.2 分支类型（注意：用 `feat/`，不是 `feature/`）

| 类型 | 格式 | 示例 |
| --- | --- | --- |
| 功能 | `feat/<scope>-<short-desc>` | `feat/detection-yolo` |
| 修复 | `fix/<scope>-<short-desc>` | `fix/ingestion-reconnect` |
| 重构 | `refactor/<scope>-<short-desc>` | `refactor/output-mqtt` |
| 工程 | `chore/<short-desc>` | `chore/add-precommit` |
| 文档 | `docs/<short-desc>` | `docs/git-workflow-convention` |
| 测试 | `test/<short-desc>` | `test/rules-dwell` |

- 全小写、单词用 `-`、不含 issue 编号、长度 ≤ 40 字符。

### 1.3 保护规则

- `main`：禁止直接 push，必须 PR + 至少 1 人 review + CI 通过。
- `develop`（启用后）：禁止直接 push，必须 PR + CI 通过。
- Phase 0（当前）允许单人项目跳过 review，但 CI（lint + test）必须通过。

## 2. Commit Message 规范

### 2.1 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 2.2 type 取值

`feat` / `fix` / `refactor` / `docs` / `chore` / `test` / `perf` / `style` / `ci` / `build`
（语义同 AGENTS.md §2.2）。

### 2.3 scope 取值（本仓库）

| scope | 对应模块 |
| --- | --- |
| `ingestion` | `src/home_perception/ingestion/` |
| `detection` | `src/home_perception/detection/` |
| `analysis` | `src/home_perception/analysis/` |
| `evidence` | `src/home_perception/evidence/` |
| `output` | `src/home_perception/output/` |
| `core` | `src/home_perception/core/` |
| `config` | `config/`（default.yaml、devices） |
| `deps` | `requirements*.txt`、`pyproject.toml` |
| `ci` | `Dockerfile`、`docker-compose.yml`、`.github/`、`.pre-commit-config.yaml` |
| `docs` | `docs/` |

### 2.4 subject

- 中文，祈使句，≤ 50 字符，不加句号：`接入 YOLO 人员检测` 而非 `添加了检测`。

### 2.5 body（AI 强制）

解释 what + why（不写 how）；每行 ≤ 72 字符；**必须含任务范围**：

```
Task scope: <ROADMAP P0-x | issue #n | governance>
```

### 2.6 footer

`Closes #n` / `Refs #n` / `ROADMAP P0-x` / `BREAKING CHANGE:`。

### 2.7 示例

```
feat(detection): 接入 ultralytics YOLOv8n 人员检测

基于 ultralytics YOLOv8n + ByteTrack 实现 Detector.detect，
仅检测 person 类，输出带 track_id 的 Detection 供规则层使用。

Task scope: ROADMAP P0-3
```

```
fix(ingestion): 重连后重置帧缓冲避免串帧

重连后 recent_frames 未清空，导致取证片段混入旧帧。
已在 _open 成功后重置缓冲。

Task scope: ROADMAP P0-2
```

```
docs(git): 对齐 AGENTS.md 第5章 Git 工作流

采纳 AI/Human 提交分工、分支类型、Conventional Commits + Task scope、
PR/Tag 策略，并适配本模块 scope 与 ROADMAP 任务编号。

Task scope: governance (AGENTS.md §5)
```

### 2.8 禁止

`update` / `misc` / `wip` 等无信息 type；无意义 subject；一 commit 多无关改动；
commit message 含密钥/token。

## 3. PR 流程

### 3.1 PR 模板

见 `.github/PULL_REQUEST_TEMPLATE.md`（已随本约定加入）。

### 3.2 合并前检查

- CI：lint（ruff）+ test（pytest）全绿。
- 人工：描述清晰、范围一致、测试覆盖充分、文档同步、commit 合规。
- Phase：当前 PR 是否在允许范围内（Phase 0 不接受业务大改动）；不跨阶段混改。

### 3.3 合并策略

- 默认 **Squash and merge**；大重构可 **Rebase and merge**；禁止普通 merge commit。
- 合并后删除 feature branch。

## 4. Tag / Release

- SemVer：`MAJOR.MINOR.PATCH`；`0.x.y` 为 Phase 0–2 预发布。
- 标签格式：`v0.1.0`、`v0.2.0-alpha.1`、`v1.0.0`。
- 首次可演示打 `v0.1.0`（alpha）；可交付打 `v1.0.0`。

## 5. 特殊情况

- **hotfix**：从 `main` 切 `hotfix/<short-desc>`，修复后 PR 到 `main` 与 `develop`，打 patch tag。
- **回滚**：优先 `git revert`，不用 `git reset --hard`；数据相关回滚需备份方案。
- **大重构**：先有 ADR（owner 落库），拆小 PR，保留旧实现过渡期。

## 6. 本仓库现状备注

- **Remote 已配置**：`origin` → `git@github.com:Thy985/silver-shield.git`（`fetch`/`push` 均存在）。
- **基线提交已落 `main`**：早期三次脚手架提交（`chore(scaffold)`、`chore: repo hygiene`、
  `chore: 追加 .gitkeep`）已被 push 到 `origin/main`，属初始基线，**早于本约定**生效；
  自本约定起，所有新改动走 branch + PR，AI 不直推 `main`、不 merge。
- **分支保护 / CI 尚未建立**（Phase 0 单人项目）：`main` 当前可被直推，但遵循本约定应改为 PR 合入；
  待接入 GitHub Branch Protection + CI（lint+test）后，§1.3 的保护规则即生效。
- **AI 实际可行动作**：`git push -u origin <branch>` 推送特性分支 → `gh pr create` 建 PR →
  通知 Human Owner review + merge。AI 不执行 `git push origin main`、不 `gh pr merge`。
