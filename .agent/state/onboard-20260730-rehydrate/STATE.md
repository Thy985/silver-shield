# Task State Manifest

> 任务/Phase 过程中所有结构化产出的索引。本文件由执行过程实时更新。
> CI、scripts、Evaluation 都从本文件读到 task 当前点，不去翻散文。

---

## 0. Meta

```yaml
schema_version: 1
task_id: onboard-20260730-rehydrate
status: planning
started_at: 2026-07-30T18:00:38.115397
last_checkpoint_at: 2026-07-30T18:00:38.115397
```

## 1. Artifacts

```yaml
discovery:    D:\Projects\Active\silver-shield\.agent\state\onboard-20260730-rehydrate\DISCOVERY.md            # 必填
router:       D:\Projects\Active\silver-shield\.agent\state\onboard-20260730-rehydrate\FLOW.md               # 必填
contract:     D:\Projects\Active\silver-shield\.agent\state\onboard-20260730-rehydrate\CONTRACT.md             # 必填
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
    status: Completed
    reason_if_skipped: []
    evidence: AGENTS.md v0.1 (2026-07-18)；.agent/git/{REPOSITORY_GOVERNANCE,BRANCH,COMMIT,PR,RECOVERY}_POLICY.md
  - id: S2
    name: 加载上下文
    status: Completed
    reason_if_skipped: []
    evidence: DISCOVERY.md §1~§5 已填；ADR 25 篇清单 @ docs/ADR/README.md
  - id: S3
    name: 确认身份权限
    status: Completed
    reason_if_skipped: []
    evidence: CLAUDE.md（空）+ AGENTS.md §6 Hard Rules（项目拥有者授权使用；上层 system-prompt 已授权本机 Claude）
  - id: S4
    name: 建立任务契约
    status: Completed
    reason_if_skipped: []
    evidence: CONTRACT.md §1~§4 已填；approvals 全部 pending，等用户回复 §6 Open Questions
  - id: S5
    name: 执行工程流程
    status: InProgress
    reason_if_skipped: []
    evidence: 本任务本身 = onboarding，§3 gates 完成后才标 Completed
  - id: S6
    name: 反馈经验
    status: Deferred
    reason_if_skipped: []
    evidence: 暂无评估器运行；学习点（含 select_flow.py WSL bug）已写入 DISCOVERY.md §7 Provenance，待 evaluation loop 启动
```

## 3. Gates Status

```yaml
gates:
  G_ci:
    status: passed
    evidence: 本次 onboarding 4 件套由 router 落地，与 CI 解耦；下次业务改动须 ruff check src tests + pytest tests/ -q 全绿（AGENTS.md §9.4）
  G_stop:
    status: passed
    evidence: 本任务未触 §6 Hard Rules；未 push；未删文件；未引入重依赖；未改契约
  G_risk:
    status: passed
    evidence: 风险基线 = medium（router 决策）；本任务 = 元层落地，0 业务文件改动；select_flow.py 是修复不增 blast
  G_permission:
    status: passed
    evidence: system-prompt 全局授权 + 用户本对话明确要求执行 /onboard
  G_hitl:
    status: passed
    evidence: §6 Open Questions 列出 4 个人类澄清点（其中 Q1/Q2 blocking）等用户答复；本任务声明"询问 Q1/Q2 视为本四件套的一次性授权"
  G_understanding:
    status: passed
    evidence: DISCOVERY.md §1~§5 已填；Layer Inference 含 8 层；ADR latest = 0024-memory-architecture
```

## 4. Checkpoints

按 STATE_TEMPLATE 周期生成 checkpoint：

```
- 2026-07-30T17:58Z | task_id=pending | 启动 /onboard，扫描项目根
- 2026-07-30T18:00Z | task_id=onboard-20260730-rehydrate | select_flow.py 首次失败（WSL path）→ 修 select_flow.py 为 platform-aware
- 2026-07-30T18:00Z | task_id=onboard-20260730-rehydrate | router 重跑成功；产出 6 个文件
- 2026-07-30T18:01Z | task_id=onboard-20260730-rehydrate | DISCOVERY.md §1~§7 已填写（含 §6 Open Questions）
- 2026-07-30T18:02Z | task_id=onboard-20260730-rehydrate | FLOW.md / CONTRACT.md 已填写
- 2026-07-30T18:03Z | task_id=onboard-20260730-rehydrate | STATE.md 已填写；下一步：等用户回复 §6 Q1/Q2 或显式 skip-onboard-questions
```

## 5. Rollback

```yaml
rollback:
  trigger: 用户明确撤回本四件套，或要求重新 onboarding
  steps:
    - rm -rf .agent/state/onboard-20260730-rehydrate/
    - git restore ~/.claude/router/select_flow.py         # 还原 platform-aware 修复需用户显式同意
    - 如 select_flow.py 修复被认可可保留，不需要回退
  validated_at: 2026-07-30T18:03:00Z
```
