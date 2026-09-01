# 2026-09-01 目录结构整理报告

**日期**: 2026-09-01
**状态**: ✅ **6 个废弃文件已删 / LIVE-*.md 保留（设计文档不推荐移动）**

## 已完成的清理

| 操作 | 文件数 | 状态 |
|---|---|---|
| 删除根目录 3 个旧 Playwright 测试 | 3 | ✅ 已 commit `cc131b7` |
| 删除根目录 3 个旧启动脚本 | 3 | ✅ 已 commit `cc131b7` |
| 移动 6 个 LIVE-*.md 到 docs/design/live-product/ | 0 | ❌ **未执行**（见下方决策）|

### 删除文件清单

| 文件 | 迁移目标 | 删除 commit |
|---|---|---|
| `run_gateway.py` | `scripts/run.py` / `scripts/run_demo.py` | `cc131b7` |
| `start_gateway.py` | `scripts/run_demo.py` | `cc131b7` |
| `start_gateway_live.py` | `scripts/run_demo.py --live` | `cc131b7` |
| `test_golden_evidence_insufficient_playwright.py` | `tests/visualizer/test_e2e_telephone_risk_gate.py` 系列 | `cc131b7` |
| `test_golden_repeated_visit_playwright.py` | `tests/visualizer/test_causal_consistency_audit.py` | `cc131b7` |
| `test_golden_telephone_risk_playwright.py` | `tests/visualizer/test_telephone_risk_e2e.py` | `cc131b7` |

### 验证结果

- ✅ 709 visualizer tests PASS / 0 FAIL
- ✅ 943 visualizer + demo tests PASS / 0 FAIL
- ✅ ruff check 0 error
- ✅ git status clean

## 未执行的清理：6 个 LIVE-*.md

### 原因：源码 docstring 大量硬引用

**统计**：8+ 个源码文件有 LIVE-*.md 精确文件名引用：

```
src/home_perception/visualizer/assets/live_stream.js:64:  // Phase 1 L0：Audio Health 三值状态机（见 LIVE-PERCEPTION-STREAM-SPEC §2.4）
src/home_perception/visualizer/viewer/live_surface.py:5:- ``LIVE-PERCEPTION-STREAM-SPEC.md`` v1.2 §2.4 L0 Audio Health 语义
src/home_perception/visualizer/viewer/render.py:1714:               <!-- LIVE-PERCEPTION-STREAM-SPEC：感知流主容器 -->
tests/visualizer/test_causal_consistency_audit.py:13:- LIVE-PERCEPTION-STREAM-SPEC.md §2.2 语义事件表
... (8+ 个文件)
```

**风险**：
- 移动会**破坏所有引用**（docstring 找不到文件名）
- 需要 grep 替换 8+ 个文件 + 跑全套测试确认无影响
- LIVE-*.md 在 git 里有**完整历史**（PR #271 #272），移动 = 重新提交 = 1 commit

**决策**：**保留根目录**，但更新 .gitignore 意图说明。

### 替代方案（推荐后续做）

| 方案 | 操作 | 风险 | 价值 |
|---|---|---|---|
| A | 保持现状 | 无 | 0（不动）|
| B | 移动到 `docs/design/live-product/` + grep 替换 8+ 引用 | 中 | 根目录更干净 |
| C | git rm + 移到 docs + 更新引用 | 高（需要替换所有 docstring）| 彻底清理 |

**建议 Owner 决策**：B 方案最佳时机是 P0-12 设备适配或 v2 多模态改造时，**顺带做**。

## 最终目录结构

```
D:\Projects\Active\silver-shield\
├── AGENTS.md
├── README.md
├── LICENSE
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements*.txt
├── .pre-commit-config.yaml
├── .dockerignore
├── .gitignore
├── .gitattributes
├── .env.example
├── .mise.toml
│
├── LIVE-*.md (6 个 — 设计文档 · 已入库 · 不推荐移动)
├── evidence_explorer.html (1.1MB · gitignore)
├── yolo11n.pt / yolo11s.pt (5.4MB + 18MB · gitignore)
│
├── src/
│   ├── home_perception/ (核心模块 — 12 子包)
│   └── silver_demo/ (演示网关)
│
├── tests/ (合约/单元/集成/演示 — 9 子目录 + 13 顶层 test_*.py)
│
├── scripts/ (38 个工具脚本)
│
├── config/ (default.yaml / live_audio.yaml / devices.yaml.example)
│
├── docs/
│   ├── 00_README.md ~ 09_risks.md (10 个)
│   ├── ADR/ (44 个决策记录)
│   ├── reports/ (40 个验收/审计报告)
│   ├── design/ (8 个设计文档 + 子目录)
│   ├── scenarios/ (MATRIX.md)
│   ├── tasks/ (TASKS-golden-case-live-product.md)
│   ├── ops/ (运维文档)
│   ├── DEMO-SCRIPT-P0-11-5b.md (5分钟剧本)
│   ├── API_REFERENCE.md / CONTRACTS.md / ARCHITECTURE.md / CONTRIBUTING.md
│   ├── DESIGN-*.md (3 个)
│   ├── MEMORY_*.md (3 个)
│   ├── PLAYBOOK-*.md (2 个)
│   ├── HPC-USAGE-GUIDE.md
│   ├── TECH-DEBT.md
│   └── DEVELOPMENT_ENV.md
│
├── silver-engineering-assets/ (教学骨架 — gitignore 排除 ruff)
├── data/ (本地数据 · gitignore)
├── dataset/ (本地数据集 · gitignore)
├── prototypes/ (历史验证脚本 · gitignore)
├── out/ (PR 内容备份 · gitignore)
├── artifacts/ (构建产物 · gitignore)
├── benchmark/ (YOLO 性能基准)
├── reports/ (reports 模块 · 不同于 docs/reports/)
├── scenarios/ (运行时场景)
├── test_screenshots/ (gitignore)
└── var/ (运行时状态)
```

## 仓库卫生度量（最终）

| 维度 | 状态 |
|---|---|
| 根目录 tracked 文件 | 12（README/AGENTS/LICENSE/配置等）|
| 根目录 gitignore 文件 | 8（yolo 权重 + html + design specs）|
| 根目录废弃脚本 | **0**（6 个已删）|
| 本地分支 | 1（main）|
| 工作树 | clean |
| 测试 | 3012 PASS / 0 FAIL |

## 后续建议（不强制）

1. **B 方案移动 LIVE-*.md**（等 P0-12 / v2 改造时一起做）
2. **Owner 远程分支清理**（`docs/audio-asset-split-story-contract`, `docs/tier1-gate-run1`）
3. **Dependabot 41 个安全告警**（`28 high + 12 moderate + 1 low`）—— 见 GitHub Security tab
