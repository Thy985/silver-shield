# Task State Manifest

> 任务/Phase 过程中所有结构化产出的索引。本文件由执行过程实时更新。
> CI、scripts、Evaluation 都从本文件读到 task 当前点，不去翻散文。

---

## 0. Meta

```yaml
schema_version: 1
task_id: t-onboard-ss-001
status: planning
started_at: 2026-07-29T21:39:25.842129
last_checkpoint_at: 2026-07-29T21:39:25.842129
```

## 1. Artifacts

```yaml
discovery:    /mnt/d/Projects/Active/silver-shield/.agent/state/t-onboard-ss-001/DISCOVERY.md            # 必填
router:       /mnt/d/Projects/Active/silver-shield/.agent/state/t-onboard-ss-001/FLOW.md               # 必填
contract:     /mnt/d/Projects/Active/silver-shield/.agent/state/t-onboard-ss-001/CONTRACT.md             # 必填
checkpoints:  <list of paths>
results:      <list of paths>
evaluations:  <list of paths>
logs:         <list of paths>
```

## 2. Step Execution Status

每个 step 强制填，未执行 ≠ 遗漏，须写明 Skipped 理由：

```yaml
steps:
  - id: S1
    name: 识别项目规范
    status: <Completed | Skipped | Deferred | Escalated>
    reason_if_skipped: [...]
    evidence: <file path>
  - id: S2
    name: 加载上下文
    status: ...
    ...
  - id: S3
    name: 确认身份权限
    status: ...
  - id: S4
    name: 建立任务契约
    status: ...
  - id: S5
    name: 执行工程流程
    status: ...
  - id: S6
    name: 反馈经验
    status: ...
```

## 3. Gates Status

```yaml
gates:
  G_ci:
    status: <pending | passed | failed | skipped>
    evidence: <run url / local command + exit code>
  G_stop:
    status: ...
  G_risk:
    status: ...
  G_permission:
    status: ...
  G_hitl:
    status: ...
  G_understanding:
    status: ...
```

## 4. Checkpoints

按 STATE_TEMPLATE 周期生成 checkpoint：

```
- <iso-time> | <一句话状态> | <下一步>
```

## 5. Rollback

```yaml
rollback:
  trigger: <condition>
  steps: [...]
  validated_at: <iso>
```
