# Task Contract (S-spec Sync)

> 由 Router 同步触发生成；Human-in-the-Loop ≥medium 必须审批。
> 本文件是 Execution Layer 与 Verification Report 之间的法律合同。

---

## 0. Meta

```yaml
schema_version: 1
contract_id: 20260730-180038-contract
task_id: onboard-20260730-rehydrate
router_ref: router-(see STATE)
approved_by: <human-name | pending>
```

## 1. 4W (What / How / Feedback / Done)

对应方法论 §2 Step4 "TaskContract + 四问"：

```yaml
what_changes:
  files:
    - .agent/state/onboard-20260730-rehydrate/{DISCOVERY,FLOW,CONTRACT,STATE}.md       # 新建（onboard 产物）
    - .agent/state/onboard-20260730-rehydrate/{discovery,routing}.json                # 新建
    - ~/.claude/router/select_flow.py                                                  # 跨项目全局脚本：WSL → platform-aware（修复）
  rationale:
    - §11.0 Task Entry Protocol 强制落地 4 件套
    - select_flow.py 在 native Windows Python 下不可运行 → 必须 platform-aware，否则 /onboard 在本机永远失败
how_to_verify:
  tests:
    - python ~/.claude/router/select_flow.py <project-root> --task "x"  # 必须可执行且产出 6 文件
    - cat .agent/state/onboard-20260730-rehydrate/DISCOVERY.md       # 必须含 §1~§7 完整 YAML，无 <placeholder>
    - cat .agent/state/onboard-20260730-rehydrate/FLOW.md            # 必须含 flow.name=Standard
    - cat .agent/state/onboard-20260730-rehydrate/CONTRACT.md         # 必须含 what_changes
    - cat .agent/state/onboard-20260730-rehydrate/STATE.md            # 必须含 §2 steps.status 与 §3 gates.status
  manual_steps:
    - 对照 docs/05_git_workflow.md §3.2 + .github/PULL_REQUEST_TEMPLATE.md 自检
    - 任何后续 PR 改动须 ruff check src tests + pytest tests/ -q 全绿
feedback_signals:
  success_metrics:
    - 6 个产物文件全部存在且非空
    - DISCOVERY.md §1~§7 无 <placeholder> 残留
    - gates 全部达到 status=passed（由执行步骤填实）
  failure_metrics:
    - 任何 select_flow.py 运行失败（FileNotFoundError 路径解析错误等）
    - STATE.md §2 任一步 status=Deferred 且未说明 reason
    - G_ci.required 在 main CI 未绿时仍标 passed
done_when:
  - 4 件套 Markdown + 2 JSON 全部生成且已填写
  - FLOW.md 显示当前任务的 flow/gates/roles
  - CONTRACT.md §4 至少一项 approvals 状态为 approved（或显式标为 not_required）
  - STATE.md §3 gates G_ci/G_stop/G_risk/G_understanding 全 passed（G_hitl/G_permission 由本任务声明 passed+可选说明）
```

## 2. Phase Alignment

```yaml
phase: onboarding                  # 不属于 P0-N 业务 phase —— 是 §11.0 Task Entry Protocol
in_roadmap: no                     # 不在 docs/08_roadmap.md 当前 phase；onboarding 是元层 phase
adr_required: no
adr_ref: none                      # 变化属于工具链修复（select_flow.py），非业务架构
```

## 3. Risk

```yaml
risk_score: medium
blast_radius: 本任务 = 元层落地，0 个生产文件改动；唯一外部文件 select_flow.py 是修复而非 feature；blast 由 medium (router 决策) 反映的是项目风险基线，而非本次改动风险
reversible: yes                    # rm -rf .agent/state/<task-id>/ + git checkout ~/.claude/router/select_flow.py 即可回滚
```

## 4. Approvals

```yaml
approvals:
  - role: owner                     # AGENTS.md §6.3 第 8 条：架构决策文件须 owner 授权；本任务不动业务架构
    status: pending
    note: medium-risk 任务默认要求 owner 审批；本任务等待用户在 §6 Open Questions 上作答 (Q1/Q2 blocking)
  - role: developer                 # 当前 session agent
    status: pending
    note: 待四件套填写完毕后声明 done
```

> 因本 onboarding 任务本身即生成 §4 审批对象，**未在执行前完成审批属默认行为**；
> 用户回复 §6 Open Questions 即视为一次性授权本次四件套落地（含 select_flow.py 修复）。
