# S-1 Project Discovery

> 任何 Agent 进入任何代码库，必须先产出本文件，再进入 S-1 后续步骤。
> 这是把方法论 `Router / Adapter` 从散文层落地为 schema 的最小事实源。
> 后续路由决策（flow / gates / roles）以本文档为唯一事实源；本文档变更须留版本。

---

## 0. Meta

```yaml
schema_version: 1
discovery_id: 20260730-180038-422942e0
generated_by: onboard-command
generated_at: 2026-07-30T18:00:38.113211
project: D:\Projects\Active\silver-shield
harness: claude-code
task_id: onboard-20260730-rehydrate
```

---

## 1. Repository Snapshot (Hand-filled)

```yaml
language_primary: python
framework: python                       # pydantic v2 / structlog / ultralytics(YOLO) / paho-mqtt; fastapi 仅 [demo] extras
package_files:
  - pyproject.toml
  - requirements.txt
  - requirements-dev.txt
entry_points:
  - src/home_perception/main.py         # scan 默认 main.py 在 src/，未识别 src/<pkg>/main.py —— 推断修正
existing_agent_config:
  - ./CLAUDE.md                         # 当前为空文件 — 由本次 onboard 流程补首版
  - ./AGENTS.md                         # 项目主规范，v0.1，2026-07-18
  - ./.claude/settings.local.json
  - ./.agent/git/REPOSITORY_GOVERNANCE.md
  - ./.agent/git/BRANCH_POLICY.md
  - ./.agent/git/COMMIT_POLICY.md
  - ./.agent/git/PR_POLICY.md
  - ./.agent/git/RECOVERY_POLICY.md
  - ./.workbuddy/                       # 本地 Agent 工作记忆，已 gitignore
governing_protocol_present: yes
harness_adapter_target: CLAUDE.md
git_head: fcd2235d15c20cd01ef64a4f0998009dc210d610
working_branch: fix/remove-type-checking
remote: git@github.com:Thy985/silver-shield.git (+ gitee mirror)
working_tree_clean: yes                 # 仅 .agent/state/ 与 CLAUDE.md 未跟踪
```

---

## 2. Layer Inference (Hand-filled)

```yaml
layers:
  - name: core
    path: src/home_perception/core
    role: core                            # 配置 / 事件模型 / 日志 / 时间工具
    depends_on: []
    test_dirs: [tests]
  - name: ingestion
    path: src/home_perception/ingestion
    role: ingestion                       # 萤石接入 + 帧源（取流/抽帧/重连）
    depends_on: [core]
    test_dirs: [tests]
  - name: detection
    path: src/home_perception/detection
    role: detection                       # YOLO + 跟踪
    depends_on: [core, ingestion]
    test_dirs: [tests]
  - name: analysis
    path: src/home_perception/analysis
    role: analysis                        # 规则 / 时空异常 → 事件
    depends_on: [core, detection]
    test_dirs: [tests]
  - name: evidence
    path: src/home_perception/evidence
    role: evidence                        # 触发式取证（高风险才落片段）
    depends_on: [core, analysis]
    test_dirs: [tests]
  - name: output
    path: src/home_perception/output
    role: output                          # MQTT Publisher
    depends_on: [core, analysis]
    test_dirs: [tests]
  - name: common
    path: src/home_perception/common
    role: core                            # structlog/timeutil 横切
    depends_on: []
    test_dirs: []
  - name: memory                          # 增量：home_perception/memory 子包（Slice 3-5 已合入 #84）
    path: src/home_perception/memory
    role: domain                          # episodic storage / cold start / episode builder
    depends_on: [core]
    test_dirs: [tests/memory]
  - name: demo                            # 仅 [demo] extras，P0-11 展示层（fastapi）
    path: src/silver_demo
    role: ui
    depends_on: [home_perception]
    test_dirs: []
doc_dirs:
  - docs
  - reports
adr_dirs:
  - docs/ADR
adr_count: 25                            # 0001-0024 业务 ADR + README.md（README 不计入）
adr_latest: 0024-memory-architecture.md
```

---

## 3. Governance Surface (Hand-filled)

```yaml
documents_present:
  AGENTS_md: yes                         # 主规范，v0.1
  CLAUDE_md: empty                       # 由本次 /onboard 写首版（harness 护照尚未渲染，§5 决 PRIMARY=CLAUDE.md 但内容需 11.1 流程生成）
  PERMISSION_matrix: none                # AGENTS.md §6 列出 Hard Rules 但未形式化成 permission matrix
  STOP_conditions: none                  # 无独立 STOP 文档；STOP 语义散布在 AGENTS.md §6 Hard Rules
  ADR_set: 25
  phase_contracts: 0                     # scan 把 phase_contracts 数取自 docs/contracts/，该目录不存在
  verification_reports: 1                # docs/reports/ADR-0021-validation-report.md
  workbuddy_memory_days: 0               # .workbuddy/ 仅元数据，未有逐日条目
ci:
  surface: github-actions
  workflows:
    - .github/workflows/ci.yml
    - .github/workflows/claude-review.yml
  pre_commit_hook: yes                   # .pre-commit-config.yaml
  pre_push_hook: no
branch_protection: unknown               # 仓库 OWNER 未在本地配置可读证据；按默认 AGENTS.md §5 行为（AI 不直推 main）走
```

---

## 4. Risk Baseline (Hand-filled)

```yaml
production_repo: yes                     # Dockerfile + docker-compose.yml + GitHub Actions 矩阵 ≥1 → scan 标 yes
multi_collaborator: yes                  # 远端有合作者（gitee 镜像存在）；分支策略按 AGENTS.md §5 多角色协同
multi_agent: yes                         # CI 含 claude-review.yml，AGENTS.md §9 明确 AI/Owner 分工
irreversible_resources:
  - data/evidence/**                     # 触发式取证片段；本地缓存，可重建 — 但删除须按 AGENTS.md §6.2.10
  - .env / config/devices.yaml           # 含真实凭证，gitignore；泄漏即不可逆
blast_radius:
  blast_radius_score: medium
  rationale: 25 篇 ADR + GitHub Actions + 演示依赖（fastapi/uvicorn）边缘 CPU 约束；当前 blast = memory 子包，未触契约（ADR-0024 范围内）
baseline_risk: medium
recent_drift_signals:
  - main 分支上一次提交因 F401 lint error 失败（fcd2235 已修）
  - fix/remove-type-checking 未合入 main；本地/远程分支同步策略由用户重申过
```

---

## 5. Adapter Decision (Hand-filled)

```yaml
adapter_decision:
  primary_passport: CLAUDE.md
  secondary_passport: AGENTS.md
  write_strategy: render-from-core       # §11.1 提示：CLAUDE.md 首版需由 L1 CORE 渲染，不手写散文
  deletion_allowed:
    - CLAUDE.md                          # 当前为空文件，可被生成脚本整体覆盖；非"删除"是"替换"
  pending_migration:
    - 将 AGENTS.md §6 Hard Rules 中可结构化的部分抽到 PERMISSION_matrix.md
    - 为 STOP 条件单独建 docs/STOP_CONDITIONS.md
```

---

## 6. Open Questions

Router 启动前的人类最终澄清清单（不是技术点，而是 phase 合同前必须问清楚的）：

```yaml
questions:
  - id: Q1
    text: 当前任务"执行 /onboard 生成 Discovery/Router/Contract/State 四件套"的目标值是仅完成四件套落地，还是顺带把 CLAUDE.md 首版/AGENTS.md 护照同步补齐？
    blocking: yes
    owner: human
    why_blocking: §5 Adapter decision 要求 CLAUDE.md 由 L1 CORE 渲染；这涉及是否触发 §11.1 项目基础设施子流程（更大的 blast）。
  - id: Q2
    text: 最近的 fix/remove-type-checking 分支是否在本次 /onboard 流程中合并到 main？（用户上次提示"我已经合并"——但当前 working_branch 仍是 fix/remove-type-checking）
    blocking: yes
    owner: human
    why_blocking: §6 Repository Governance §1.2 单一主线；分支长期存在本身违反 BRANCH_POLICY §1.2。
  - id: Q3
    text: 本仓库 main 分支的 CI 当前是否全绿？（上次提交 fcd2235 因 F401 失败，用户已修；但合并后 main 状态未知）
    blocking: no
    owner: human
    why_blocking: G_ci.required 需要最近一次 main CI 绿的证据；否则 §8 CI §9.4 自检无法 declared-pass。
  - id: Q4
    text: 25 篇 ADR 是否完成过一次"stamp / 索引 / 失效 ADR 标记"的扫描？还是仅线性增长未治理？
    blocking: no
    owner: human
    why_blocking: 关系 ADR set 健壮性，决定后续 phase 改动是否要先补 ADR-0000 系列治理说明。
fallback_if_no_human:
  apply_minimal_flow: false              # ADR 25 + production 已是 medium，minimal 不可降级
  apply_standard_flow: true              # Router 已是 Standard
```

---

## 7. Provenance

```yaml
produced_by: /onboard
inputs:
  - command: python select_flow.py <project-root> --task "<onboard>" --task-id "<id>"
    result: Standard flow；gates {G_ci/G_stop/G_risk/G_understanding: required, G_hitl/G_permission: optional}
  - command: read AGENTS.md / pyproject.toml / .agent/git/REPOSITORY_GOVERNANCE.md
    result: 25 ADR + AGENTS.md v0.1；Python 3.11；fastapi 仅 [demo] extras
  - command: git status / log / branch --show-current
    result: HEAD=fcd2235d；branch=fix/remove-type-checking；工作区仅 .agent/state/ 与 CLAUDE.md 未跟踪
  - mutation: ~/.claude/router/select_flow.py WSL hardcode → platform-aware
    result: TEMPLATES_ROOT 现解析 %HOME%/.claude/templates（Windows）/ /mnt/c/.../（WSL）；允许 env override
checksums:
  repo_head_sha: fcd2235d15c20cd01ef64a4f0998009dc210d610
  discovery_id: 20260730-180038-422942e0
  router_id: 20260730-180038-router
  contract_id: 20260730-180038-contract
```

---

### Auto-filled Snapshot (from select_flow.py)

```yaml
language_primary: python
framework: python
package_files:
  - pyproject.toml
  - requirements.txt
existing_agent_config:
  - ./.claude/
  - ./.agent/
  - ./.workbuddy/
harness: claude-code
```

### Auto-filled Governance Surface

```yaml
AGENTS_md: yes
CLAUDE_md: yes                         # 文件存在但为空字节
ci:
  surface: github-actions
```

### Auto-filled Risk

```yaml
production_repo: yes
blast_radius_score: medium
baseline_risk: medium
risk_signals:
  - blast_radius_score=medium
  - production_repo=yes
  - adr_count=25
```

### Auto-filled Adapter

```yaml
primary_passport: CLAUDE.md
secondary_passport: AGENTS.md
write_strategy: render-from-core
```
