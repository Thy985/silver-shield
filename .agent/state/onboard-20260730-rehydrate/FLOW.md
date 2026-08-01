# Router Output · Adaptive Workflow Selection

> 本文件由 Router（`select_flow.py` 或 `select_flow.md` 引导的人工决策）产出，
> 是 Discovery 之后唯一权威的 flow / gates / roles 决策。
> Execution Layer（Agent / 人类）必须按本文件执行；偏离须显式记录在 `task-state/checkpoint.md`。

---

## 0. Meta

```yaml
schema_version: 1
router_id: 20260730-180038-router
based_on_discovery: 20260730-180038-422942e0
decided_by: <human | ai | select_flow.py>
decided_at: 2026-07-30T18:00:38.114258
```

## 1. Flow Selection

方法论 §3 表格的强制结构体版：

```yaml
flow:
  name: Standard
  rationale: medium blast radius × production_repo → Standard 等级；G_ci/G_stop/G_risk/G_understanding required；G_hitl/G_permission optional
  skill_pipeline:                   # 本次 flow 引用的原子笔记
    - ~/.claude/templates/discovery/DISCOVERY.template.md
    - ~/.claude/templates/task-state/CONTRACT.template.md
    - ~/.claude/templates/task-state/STATE.template.md
    - AGENTS.md §9.2（编码前 4W Task Contract）
    - AGENTS.md §9.4（PR 自检清单）
    - docs/05_git_workflow.md
gates:
  G_ci: required
  G_stop: required
  G_risk: required
  G_permission: optional
  G_hitl: optional
  G_understanding: required
roles:                                # 动态角色序列，Router 输出而非写死
  - TechLead
  - Developer
  - Reviewer
execution_status_model:                # 阶段/子步必须标注，未执行 ≠ 遗漏
  enforced: true
  values: [Completed, Skipped, Deferred, Escalated]
  skipped_requires_reason: true
```

## 2. Task Contract (Sync)

Router 选路完成后立即同步生成 TaskContract 见 `templates/task-state/CONTRACT.template.md`。
本节只放 cross-reference：

```yaml
contract_ref: .agent/state/onboard-20260730-rehydrate/CONTRACT.md
```

## 3. Risk & Rollback Pre-flight

```yaml
risk_score: medium
risk_signals:
  - blast_radius_score=medium
  - production_repo=yes
  - adr_count=25
  - multi_collaborator=yes (gitee mirror)
  - multi_agent=yes (claude-review.yml)
rollback_plan:
  steps:
    - 删除 .agent/state/onboard-20260730-rehydrate/ 整目录
    - git checkout -- .agent/state/  # 仅当目录已入库时
    - 任何对 ~/.claude/router/select_flow.py 的修改用 git 还原或保留（已加 platform-aware 是修复，不需回退）
  cost: low
```

## 4. Escalation Triggers

```yaml
triggers:
  - condition: same_task_failures > 5
    escalate_to: human
  - condition: scope_drift == true
    escalate_to: human
  - condition: requires_new_adr == true
    escalate_to: human
```
