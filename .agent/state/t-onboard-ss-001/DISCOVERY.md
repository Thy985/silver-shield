# S-1 Project Discovery

> 任何 Agent 进入任何代码库，必须先产出本文件，再进入 S-1 后续步骤。
> 这是把方法论 `Router / Adapter` 从散文层落地为 schema 的最小事实源。
> 后续路由决策（flow / gates / roles）以本文档为唯一事实源；本文档变更须留版本。

---

## 0. Meta

```yaml
schema_version: 1
discovery_id: 20260729-213925-3ac63b18
generated_by: <human | ai | onboard-command>
generated_at: 2026-07-29T21:39:25.812907
project: /mnt/d/Projects/Active/silver-shield
harness: claude-code
```

## 1. Repository Snapshot

由命令自动填，禁止手编：

```yaml
language_primary: <python | flutter-dart | typescript | ...>
framework: <flutter | pytorch | fastapi | ...>
package_files:
  - <path e.g. pyproject.toml>
entry_points:
  - <path>
existing_agent_config:
  - (see existing_agent_config)
  - <path e.g. AGENTS.md>
governing_protocol_present: <yes | partial | no>
harness_adapter_target: <CLAUDE.md | AGENTS.md | .cursorrules | MEMORY.md>
```

## 2. Layer Inference

由命令扫描后填，禁止手编：

```yaml
layers:
  - name: <layer-name>
    path: <glob e.g. src/home_perception/detection>
    role: <core | ingestion | detection | analysis | evidence | output | ui | domain>
depends_on: []      # 严格自上而下依赖方向的反向校验在此写
test_dirs:
  - <path>
doc_dirs:
  - <path>
adr_dirs:
  - <path>
adr_count: <int>
adr_latest: 00xx-<title>.md     # 最近一篇 ADR，用于 Risk 评估
```

## 3. Governance Surface (现状盘点)

```yaml
documents_present:
  AGENTS_md: <yes | no | empty>
  CLAUDE_md: <yes | no | empty>
  PERMISSION_matrix: <path | none>
  STOP_conditions: <path | none>
  ADR_set: <count>
  phase_contracts: <count>
  verification_reports: <count>
  workbuddy_memory_days: <int>
ci:
  surface: <github-actions | gitlab-ci | none>
  workflows: <list of paths>
  pre_commit_hook: <yes | no>
  pre_push_hook: <yes | no>
```

## 4. Risk Baseline

```yaml
production_repo: <yes | no | unknown>
multi_collaborator: <yes | no>
multi_agent: <yes | no>
irreversible_resources: <list | none>
blast_radius:
  blast_radius_score: <low | medium | high>   # 跨模块/数据迁移/公开 API 任一为 high
  rationale: <一句话理由>
baseline_risk: <low | medium | high>          # Adapter / Router 输入
```

## 5. Adapter Decision

把方法论 §4 Harness Adapter 强制落成结构体：

```yaml
adapter_decision:
  primary_passport: CLAUDE.md       # Harness = claude-code 时填这条
  secondary_passport: AGENTS.md     # 跨 harness 兼容时双写
  write_strategy: render-from-core  # 由 L1+L2 渲染，禁止手编 CORE 段落
  deletion_allowed:
    - <path>                       # Adapter 阶段已被声明可不留的旧产物
```

## 6. Open Questions

Router 启动前的人类最终澄清清单（不是技术点，而是 phase 合同前必须问清楚的）：

```yaml
questions:
  - id: Q1
    text: <一句话需求>
    blocking: <yes | no>
    owner: <human>
fallback_if_no_human:
  apply_minimal_flow: true
```

## 7. Provenance

```yaml
produced_by: /onboard
inputs:
  - <命令执行回显段 1>
  - <命令执行回显段 2>
checksums:
  repo_top_level_sha256: <hash>      # 防 Discovery 后仓库被偷偷改路径
```

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
### Auto-filled Layers
```yaml
  ci:
    surface: github-actions
    workflows:
      - .github/workflows/ci.yml
      - .github/workflows/claude-review.yml
    pre_push_hook: no
  adr_dirs:
    - docs/ADR
  adr_count: 25
  adr_latest: README.md
```
### Auto-filled Governance Surface
```yaml
  AGENTS_md: yes
  CLAUDE_md: yes
  phase_contracts: 0
  verification_reports: 0
  ci:
    surface: github-actions
```
### Auto-filled Risk
```yaml
  production_repo: yes
  blast_radius_score: medium
  baseline_risk: medium
```
### Auto-filled Adapter
```yaml
  primary_passport: CLAUDE.md
  secondary_passport: AGENTS.md
  write_strategy: render-from-core
```
