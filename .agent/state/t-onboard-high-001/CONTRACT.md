# Task Contract (S-spec Sync)

> 由 Router 同步触发生成；Human-in-the-Loop ≥medium 必须审批。
> 本文件是 Execution Layer 与 Verification Report 之间的法律合同。

---

## 0. Meta

```yaml
schema_version: 1
contract_id: 20260729-213940-contract
task_id: t-onboard-high-001
router_ref: router-(see STATE)
approved_by: <human-name | pending>
```

## 1. 4W (What / How / Feedback / Done)

对应方法论 §2 Step4 "TaskContract + 四问"：

```yaml
what_changes:
  files: [...]
  rationale: [...]
how_to_verify:
  tests: [...]
  manual_steps: [...]
feedback_signals:
  success_metrics: [...]
  failure_metrics: [...]
done_when:
  - <可观测条件 1>
  - <可观测条件 2>
```

## 2. Phase Alignment

```yaml
phase: <phase 编号/名称 e.g. phase3.5>
in_roadmap: <yes | no | drift>
adr_required: <yes | no>
adr_ref: <id | none>
```

## 3. Risk

```yaml
risk_score: high
blast_radius: <一句话>
reversible: <yes | no | partial>
```

## 4. Approvals

```yaml
approvals:
  - role: <reviewer>
    status: <pending | approved | rejected>
    note: <一句话>
```
