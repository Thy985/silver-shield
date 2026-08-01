# Router Output · Adaptive Workflow Selection

> 本文件由 Router（`select_flow.py` 或 `select_flow.md` 引导的人工决策）产出，
> 是 Discovery 之后唯一权威的 flow / gates / roles 决策。
> Execution Layer（Agent / 人类）必须按本文件执行；偏离须显式记录在 `task-state/checkpoint.md`。

---

## 0. Meta

```yaml
schema_version: 1
router_id: 20260729-213925-router
based_on_discovery: 20260729-213925-3ac63b18
decided_by: <human | ai | select_flow.py>
decided_at: 2026-07-29T21:39:25.821208
```

## 1. Flow Selection

方法论 §3 表格的强制结构体版：

```yaml
flow:
  name: Standard
  rationale: <一句话>
  skill_pipeline:                   # 哪几篇原子笔记被本次 flow 引用
    - <file ref e.g. 编码前四问与PR自检.md>
    - <file ref>
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
contract_ref: <relative path to task-state/CONTRACT.md>
```

## 3. Risk & Rollback Pre-flight

```yaml
risk_score: <low | medium | high>
risk_signals:
  - blast_radius_score=medium
  - production_repo=yes
  - adr_count=25
rollback_plan:
  steps: [...]
  cost: <low | medium | high>
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
