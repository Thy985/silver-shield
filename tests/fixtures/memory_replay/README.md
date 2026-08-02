# Memory Consumer 回放数据集（M0 · 数据闭环）

本目录是 `DESIGN-memory-replay-dataset.md` 的落地交付物，用于验证 **Memory Consumer
真的从过去记忆中获得了新的理解**（而不是只跑通 Consumer 代码）。

## 每个 case 证明什么

| Case | 证明的 Memory 价值 | 关键信号 |
| --- | --- | --- |
| `case_001_repeat_visitor` | 孤立事件 → 关联画像（重复夜间访客） | `visitor_profile.visit_count=5` / `night_visit_ratio=1.0` |
| `case_002_behavior_escalation` | 单看当前得不到的行为升级模式 | `risk_pattern.tags` 含 `escalating_behavior` |
| `case_003_conflict_transparency` | 历史正常 vs 当前异常 → 冲突透明（C4） | `conflicts` 非空、`type=behavior_shift`、新旧并存 |

## 文件结构

每个 case 目录：
- `history.json`：`list[EpisodicRecord]`（ADR-0024 schema，直接复用 `EpisodicRecord`）。
- `current.json`：`CurrentEvent`（当前触发事件）。
- `expected_reasoning_input.json`：`ReasoningInput` 独立 oracle（**不含** `risk_score` /
  `decision` / `warning`，C1）。

## 数据来源与校准（重要）

> 本数据集是 **设计派生（design-derived）** 的基线：history 中的 `EpisodicRecord` 按 case
> 语义手工构造为 schema-valid 的合法记录（非随机数据），用于 M0 先打通数据闭环与回放
> 断言。它们**不是**来自真实 CCTV 回放。

按 `DESIGN-memory-replay-dataset.md` §4 的规划，真实的「Memory 价值」证明应当由
`EpisodeReplayLayer` 跑**真实 CCTV 样本**（复用 `PerceptionPipeline` + `MemoryHook`）
产出的 `EpisodicRecord` 来校准本目录的 fixture——届时用 `MEMORY_UPDATE_REPLAY=1` 风格
的脚本重生成 `history.json`，并以本目录的 case 语义为验收基准。在真实回放校准前，本
数据集作为可版本化、可断言的回归基线存在。

## 运行

```bash
pytest tests/memory/test_memory_replay_dataset.py -q
```
