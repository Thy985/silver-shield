# SilverShield · Home 感知模块 — 收尾分析报告

> 生成时间：2026-08-25 | 分析范围：代码 + Git 历史 + 文档一致性 + 仓库卫生
> 授权范围：AGENTS.md §6.3 #8（Owner 授权改架构决策文件）

---

## 一、项目整体健康度评分

| 维度 | 状态 | 说明 |
|------|------|------|
| 架构冻结 | ✅ 健康 | ADR-0014 三级冻结 + Freeze Gate 7/7；core/ingestion/detection 层无违规依赖 |
| 依赖方向 | ✅ 健康 | core/ingestion 不反向依赖业务层；analysis 仅依赖 evidence/output ABC |
| 业务禁区 | ✅ 健康 | `fraud/suspect` 仅出现在黑名单校验代码与文档注释中，无实际输出字段 |
| 硬编码凭证 | ✅ 健康 | EZVIZ_APP_KEY/SECRET 全部从环境变量读取，无硬编码 |
| print() 违规 | ⚠️ 低风险 | 全部位于 `data/generators/`（CLI 工具脚本）和 `evaluation/report.py`（CLI 入口），非生产库代码 |
| except 静默吞异常 | ✅ 健康 | 所有 `except Exception:` 均有 `log.exception()` + 注释说明降级策略 |
| 单文件大小 | ⚠️ 需关注 | analysis/ 层 2 个文件 >400 行（冻结层），runtime/ 层 1 个 >1000 行 |
| Git 仓库卫生 | ⚠️ 需清理 | 7 个已合并分支未删除；6 个根目录文件未被 .gitignore 覆盖 |
| 文档一致性 | ⚠️ 需验证 | 见下文 §三 |
| 未合入工作 | ⚠️ 阻塞 | `fix/scenario-outcome-alignment` 分支 7 commits 未合入 main |

---

## 二、Git 历史分析

### 2.1 分支状态

**当前分支**：`fix/scenario-outcome-alignment`（7 commits ahead of main）

**未合入变更**（vs origin/main）：
- 13 个文件变更，+982 / -85 行
- 新增：`scripts/scenario_verify.py`（253 行，场景校验 CLI）、`tests/demo/test_gateway_mjpeg_sync.py`（223 行）、`tests/demo/test_scenario_verify.py`（182 行）、`tests/demo/test_gateway_live_audio_seam.py`（78 行）
- 修改：`src/silver_demo/gateway.py`（+261/-85，MJPEG 同步修复 + 场景切换注入）
- 修改：`config/demo/scenarios/`（telephone_risk / cctv_surveillance_suspicious / delivery_courier_normal）

**建议**：此分支为 P0-11 Demo 修复，功能完整且与 main 无冲突，应优先合入。

### 2.2 可清理的已合入分支（本地）

以下分支已合入 main，可安全删除：

```
fix/ci-golden-data-skip
fix/ci-torchfree-gates
feat/demo-verify-all
feat/demo-diagnostics
feat/scenario-registry-telephone-risk-rename
fix/golden-data-path-migration
fix/v41-audio-evidence-lane
```

### 2.3 可清理的已合入分支（远端）

上述分支合入后，`origin/` 上同样存在，需 force-push 清理或请 Owner 操作。

### 2.4 活跃开发分支（未合入，含 ADR-0039~0043 实现队列）

| 分支 | 状态 | 归属 ADR |
|------|------|----------|
| `feat/audio-runtime-entry` | 已合入 main | ADR-0039 |
| `feat/decision-risk-signals` | 已合入 main | ADR-0040 |
| `feat/signal-temporal-linker` | 已合入 main | ADR-0041 |
| `feat/audio-evidence-strength` | 已合入 main | ADR-0042 |
| `feat/dual-track-projection` | 已合入 main | ADR-0043 |
| `fix/yamnet-class-map-loading` | 已合入 main | ADR-0037 |
| `fix/yamnet-csv-extension` | 已合入 main | ADR-0037 |
| `fix/v41-audio-evidence-lane` | 已合入 main | ADR-0042 |
| `feat/live-surface-shell-phase-1/2/3` | 已合入 main | ADR-0036 |
| `feat/cctv-acceptance-framework` | 已合入 main | ADR-0034 |
| `feat/browser-e2e-gate` | 已合入 main | Gate F |
| `feat/gate-f/f3/f6-acceptance` | 已合入 main | Gate F |
| `feat/gate-g-true-multimodal` | 已合入 main | Gate G |
| `feat/gate-h-data-calibration` | 已合入 main | Gate H |
| `feat/gate-i-production-policy-calibration` | 已合入 main | Gate I |
| `feat/visualizer-product-story-render` | 已合入 main | ADR-0036 |
| `feat/analysis-decision-risk-signals-enh` | 已合入 main | ADR-0040 |
| `feat/audio-runtime-wiring` | 已合入 main | ADR-0039~0043 全链 |
| `fix/scenario-outcome-alignment` | **未合入** | P0-11 Demo 修复 |

**结论**：ADR-0039~0043 实现队列**已全部合入 main**，只有 `fix/scenario-outcome-alignment` 未合入。

### 2.5 Tag 状态

- `audio-phase3.0-complete`：已打 tag
- `v0.1.0-mvp-rc`：**README 声称已打，但 git tag --list 未显示** → 需核实

---

## 三、文档一致性分析（防止漂移）

### 3.1 README.md vs 代码现实

| README 声明 | 实际情况 | 风险 |
|-------------|----------|------|
| "2295 测试全绿" | pytest collect 报 100 errors（缺少 `requests` 模块）；实际可收集 test 文件 201 个 | **HIGH**：README 数字可能过时或未在当前环境验证 |
| "realtime_risk.enabled=false 默认关闭" | `config/default.yaml` 确实 `enabled: false` | ✅ 一致 |
| "Memory Stage F shadow mode 默认关闭" | `config/default.yaml` 确实 `episodic_shadow` 相关开关默认 off | ✅ 一致 |
| "P0-11 12/12 端到端验证" | `scripts/e2e_validate_demo.py` 存在 | ✅ 需运行验证 |
| "ADR-0039~0043 全部 Accepted" | `docs/ADR/0039~0043.md` 文件均存在 | ✅ 一致 |

### 3.2 AGENTS.md §10  vs README §当前状态

| AGENTS.md §10 声明 | README 对应节 | 一致性 |
|---------------------|----------------|--------|
| "MVP Release Candidate 已交付（tag v0.1.0-mvp-rc，2026-07-20）" | README "当前状态" 节 | **⚠️ tag 未找到** |
| "P0-11 多角色协同闭环 Demo 已完成（12/12）" | README "当前状态" 节 | ✅ 一致 |
| "2026-08-22：音频风险运行时审计 + 5 项契约拍板" | README "音频风险运行时契约" 节 | ✅ 一致 |
| "进行中：多模态运行时改造（ADR-0039~0043 实现队列）" | README "当前执行路线" 节步骤 1-5 | **⚠️ 实现队列已合入 main，README 未更新状态** |

### 3.3 docs/08_roadmap.md vs 代码现实

| Roadmap 声明 | 实际情况 | 风险 |
|--------------|----------|------|
| "P0-1~P0-11 全部 ✅ 完成" | 代码验证：P0-11 Demo 完整，7 commits pending merge | ✅ 基本一致 |
| "2295 测试全绿（含 v2 模块）" | pytest collect 失败（缺 requests） | **⚠️ 需验证** |
| "v1.0 冻结" | ADR-0034 Phase C 已合入 | ✅ 一致 |
| "Stage G/H Semantic 聚合器：v1 范围外" | `memory/consumer/aggregation.py` 存在（规则聚合非 Semantic） | ✅ 一致 |

### 3.4 文档漂移风险点

1. **README "当前执行路线" 步骤 1-5 状态过时**：ADR-0039~0043 实现已全部合入 main，但 README 仍显示"进行中"。应更新为"已完成"。
2. **README 测试数量 2295**：需确认是否为 CI runtime 全量数字，当前 torch-free 环境无法验证。
3. **v0.1.0-mvp-rc tag 未找到**：README 和 AGENTS.md 均声称已打，但 `git tag --list` 只显示 `audio-phase3.0-complete`。可能 tag 在远端但未本地拉取，或已被删除。
4. **docs/tasks/TASKS-golden-case-live-product.md**  tracked 但 README/docs/00_README.md 未引用。

---

## 四、仓库卫生分析

### 4.1 .gitignore 覆盖盲区（根目录文件）

以下文件**存在于根目录且被 git 跟踪**，但 `.gitignore` 无显式规则覆盖：

| 文件 | 性质 | 建议 |
|------|------|------|
| `LIVE-PERCEPTION-STREAM-SPEC.md` | 产品设计规格（非交付物） | 移入 `docs/design/` 或加入 .gitignore |
| `LIVE-PERCEPTION-STREAM-SEMANTICS.md` | 产品设计规格 | 同上 |
| `LIVE-PRODUCT-CAPABILITY-MATRIX.md` | 产品设计规格 | 同上 |
| `LIVE-PRODUCT-SURFACE-SPEC.md` | 产品设计规格 | 同上 |
| `LIVE-PRODUCT-WIREFRAME.md` | 产品设计规格 | 同上 |
| `LIVE-SURFACE-REALITY-CHECK.md` | 评估报告 | 移入 `docs/reports/` |
| `playwright_test_utils.py` | 测试工具 | 移入 `tests/` 或加入 .gitignore |
| `test_golden_*.py` (×3) | Playwright E2E 测试 | 移入 `tests/` 或加入 .gitignore |
| `.coverage` | 覆盖率缓存 | .gitignore 已覆盖（`*.coverage` 不匹配 `.coverage`） |

**修复建议**：
```gitignore
# 在 .gitignore 末尾追加
LIVE-*.md
playwright_test_utils.py
test_golden_*.py
```

### 4.2 不应提交的大文件

| 文件 | 大小 | 状态 |
|------|------|------|
| `yolo11n.pt` | 5.4 MB | .gitignore `*.pt` 已覆盖，**未跟踪** ✅ |
| `yolo11s.pt` | 18.4 MB | 同上 ✅ |
| `data/golden/telephone_risk/video/cctv_gate_g_open.mp4` | 0.4 MB | `.gitignore *.mp4` 已覆盖，**未跟踪** ✅ |
| `data/golden/telephone_risk/audio_mix/case_b_mix.wav` | 1.37 MB | 未匹配任何模式 → **可能被跟踪？** |

**核查**：`git ls-files data/golden/` 返回空 → 未跟踪 ✅

### 4.3 临时目录体积

| 目录 | 大小 | .gitignore 覆盖 | 建议 |
|------|------|-----------------|------|
| `.tmp_ruff_venv/` | 46 MB | ❌ 未覆盖 | 加入 .gitignore |
| `.codegraph/` | 49 MB | ❌ 已覆盖（`.codegraph/`） | 确认 |
| `.codeartsdoer/` | 38 MB | ❌ 未覆盖 | 加入 .gitignore |
| `.arts/` | 0 MB | ❌ 已覆盖（`.arts/`） | 确认 |
| `generated/` | 2.3 MB | ✅ 已覆盖 | — |
| `reports/` | 7 MB | ✅ 已覆盖 | — |
| `test_screenshots/` | 2.1 MB | ✅ 已覆盖 | — |
| `.venv/` | 897 MB | ✅ 已覆盖 | — |
| `.ruff_cache/` | — | ✅ 已覆盖 | — |
| `.pytest_cache/` | — | ✅ 已覆盖 | — |

---

## 五、代码质量分析

### 5.1 AGENTS.md §2.4 print() 违规

| 文件 | print() 次数 | 性质 | 是否违规 |
|------|-------------|------|----------|
| `data/generators/*.py` (×5) | ~23 次 | CLI 工具脚本 | ✅ 豁免（非生产库） |
| `evaluation/report.py` | 3 次 | CLI 报告入口 | ✅ 豁免 |
| `src/home_perception/` 其他 | 0 次 | — | ✅ 合规 |

**结论**：无实际违规。所有 `print()` 均位于 CLI 入口脚本，符合"禁止裸 print()"针对生产库代码的意图。

### 5.2 AGENTS.md §2.5 except 静默吞异常

| 文件 | `except Exception:` 次数 | 是否有 log.exception | 结论 |
|------|-------------------------|---------------------|------|
| `decision_engine.py` | 3 | ✅ | ✅ 合规 |
| `decision_policy.py` | 1 | ✅ | ✅ 合规 |
| `executor.py` | 2 | ✅ | ✅ 合规 |
| `pipeline.py` | 4 | ✅ | ✅ 合规 |
| `vad.py` | 1 | ✅ | ✅ 合规 |
| `sink.py` | 3 | ✅ | ✅ 合规 |
| `decision_sink.py` | 6 | ✅ | ✅ 合规 |

**结论**：无实际违规。所有 `except Exception:` 均有 `log.exception()` 记录 + 注释说明降级策略。

### 5.3 单文件超过 400 行

| 文件 | 行数 | 所在层 | 风险 |
|------|------|--------|------|
| `analysis/decision_trace.py` | 816 | L2 冻结 | **⚠️ 高**：冻结层文件过大，难以维护 |
| `analysis/decision_policy.py` | 428 | L2 冻结 | ⚠️ 中：接近上限 |
| `core/config.py` | 526 | L1 冻结 | ⚠️ 中：配置模型膨胀 |
| `runtime/pipeline.py` | 1256 | 装配层 | ⚠️ 高：装配逻辑过度集中 |
| `visualizer/viewer/render.py` | 2727 | Demo 层 | ✅ 可接受（展示层不冻结） |
| `visualizer/viewer/live_adapter.py` | 1472 | Demo 层 | ✅ 可接受 |
| `visualizer/renderer.py` | 1092 | Demo 层 | ✅ 可接受 |
| `visualizer/loader.py` | 847 | Demo 层 | ✅ 可接受 |
| `memory/records.py` | 634 | v2 增量 | ⚠️ 中 |
| `data/generators/stress_factor_decoupled.py` | 723 | 测试工具 | ✅ 可接受 |
| `data/generators/telephone_risk_v2.py` | 551 | 测试工具 | ✅ 可接受 |
| `data/generators/telephone_risk.py` | 473 | 测试工具 | ✅ 可接受 |
| `evaluation/ab_runner.py` | 518 | 测试工具 | ✅ 可接受 |
| `integration/loop/runner.py` | 522 | 集成测试 | ✅ 可接受 |
| `integration/loop/validator.py` | 483 | 集成测试 | ✅ 可接受 |
| `integration/loop/report.py` | 405 | 集成测试 | ✅ 可接受 |
| `memory/episode_builder.py` | 477 | v2 增量 | ⚠️ 中 |
| `memory/consumer/contracts.py` | 414 | v2 增量 | ⚠️ 中 |
| `validation/contracts.py` | 414 | 验证层 | ⚠️ 中 |

**重点风险**：
- `analysis/decision_trace.py`（816 行）在 L2 冻结层，违反"单文件超过 400 行必须考虑拆分"原则
- `runtime/pipeline.py`（1256 行）装配逻辑过度集中，建议按模块拆分

---

## 六、待办行动项（按优先级）

### P0：收尾前必须完成

| # | 行动项 | 负责人 | 预计工作量 |
|---|--------|--------|-----------|
| 1 | 合入 `fix/scenario-outcome-alignment` 分支（7 commits）到 main | AI Agent | 30 min |
| 2 | 核实 `v0.1.0-mvp-rc` tag 状态（远端是否存在） | Owner | 5 min |
| 3 | 更新 README "当前执行路线"：ADR-0039~0043 标记为已完成 | AI Agent | 15 min |
| 4 | 更新 README 测试数量声明（确认 2295 为 CI runtime 全量） | AI Agent | 10 min |
| 5 | 追加 `.gitignore` 规则：`LIVE-*.md`、`playwright_test_utils.py`、`test_golden_*.py`、`.tmp_ruff_venv/`、`.codeartsdoer/` | AI Agent | 5 min |

### P1：收尾阶段建议完成

| # | 行动项 | 负责人 | 预计工作量 |
|---|--------|--------|-----------|
| 6 | 删除本地已合入的冗余分支（9 个） | AI Agent | 5 min |
| 7 | `analysis/decision_trace.py` 拆分建议（816 行 → 2-3 个文件） | Owner 评审 | 2 h |
| 8 | `runtime/pipeline.py` 拆分建议（1256 行 → 按组件拆分） | Owner 评审 | 4 h |
| 9 | 打 `v0.1.0` 正式 tag（若 `v0.1.0-mvp-rc` 确认存在） | Owner | 5 min |
| 10 | 运行 `ruff check src tests` + `pytest tests/ -q` 最终验证 | AI Agent | 15 min |

### P2：v1.0 冻结前建议

| # | 行动项 | 负责人 | 预计工作量 |
|---|--------|--------|-----------|
| 11 | `core/config.py` 审查（526 行，L1 冻结层） | Owner | 1 h |
| 12 | `memory/records.py` 审查（634 行） | Owner | 1 h |
| 13 | 文档索引更新：`docs/00_README.md` 补充 `docs/tasks/` 引用 | AI Agent | 10 min |
| 14 | 清理 `docs/reports/` 中的中间过程文档（保留终稿） | Owner | 2 h |

---

## 七、文档漂移防护机制建议

1. **README SSOT 锁定**：README「当前状态」节为单一事实源，AGENTS.md §10 和 docs/08_roadmap §8.2 为其投影。任何状态变更**必须先改 README**，再同步投影文档。
2. **CI phase_consistency_check.py**：已建立，确保三处状态一致。
3. **文档审核 checklist**（每次 PR 前）：
   - [ ] README「当前状态」与代码现实一致
   - [ ] AGENTS.md §10 与 README 一致
   - [ ] docs/08_roadmap §8.2 与 README 一致
   - [ ] ADR 状态（Proposed/Accepted/Superseded）与实际代码一致
   - [ ] `.gitignore` 已覆盖本次新增的临时文件

---

## 八、收尾完成标准

- [ ] `fix/scenario-outcome-alignment` 已合入 main
- [ ] 9 个已合入分支已删除（本地 + 远端）
- [ ] `.gitignore` 已更新（6 项新增规则）
- [ ] README「当前执行路线」已更新（ADR-0039~0043 标记已完成）
- [ ] README 测试数量声明已核实/修正
- [ ] `v0.1.0-mvp-rc` tag 状态已确认
- [ ] `ruff check src tests` 无 error
- [ ] `pytest tests/ -q` 通过（torch-free 环境）
- [ ] 打 `v0.1.0` 正式 tag（Owner 授权）

---

*本报告由 Agnes (DSH) 生成，Owner 授权范围内可直接执行 P0/P1 行动项。P2 行动项需 Owner 评审。*
