# ADR-0041: Signal 级跨模态时间对齐机制（SignalTemporalLinker）

- 状态：Accepted（Owner 于 2026-08-22 ADR Preflight Review 拍板：**冻结机制，不冻结窗口数值**）
- 日期：2026-08-22
- 决策者：Owner
- 相关：
  - `docs/reports/ADR-PREFLIGHT-REVIEW-2026-08-22.md`（Q3 论证全文 + Owner 修订）
  - ADR-0019（Evidence Fusion 独立阶段——本 ADR 是其 Phase 1 落地件）、
    ADR-0028（episode 级 CrossModalLinker——与本 ADR 职责分离）、
    ADR-0039（RuntimeFrameContext.case_time——时钟统一锚点）、
    ADR-0042（Evidence Strength——本 ADR 是其 Evidence Synthesis 前置件）

---

## 背景（Context）

Vision 与 Audio RiskSignal 分属两个时钟域：

- `AudioPerceptionEvent.timestamp` = Unix 墙钟秒（audio/event.py）；
- 视觉侧 `case_time = frame_index * frame_interval_s`（demo 伪时钟，gateway 三处重复计算）。

现有 `CrossModalLinker`（ADR-0028）是 **episode 级、异步、落库后**的关联器：
输入 `EpisodicRecord.enter/leave` 时间窗、经 `MemoryHook.record` 落库后触发全量扫描。
它回答"两条 Memory episode 是否同源"，**不回答"同一时刻的 Vision 信号与 Audio 信号
是否该合并为 Combined Risk"**。

Preflight Review 曾建议默认窗口 2.0s，Owner 判定证据不足：

> 这更像一个合理的工程初始值，而不是代码审计能够证明的事实。……
> Q3 现在只冻结「必须存在 signal-level temporal alignment」，不要冻结默认 2.0s。

## 决策（Decision）

### D1：机制冻结

必须存在 signal 级时间对齐组件 `SignalTemporalLinker`（`analysis/` 层纯函数、零状态、可单测），
职责：判定 Vision RiskSignal 与 Audio RiskSignal 是否构成时间关联，产出
`LinkedSignalPair(vision_signal, audio_signal, link_strength, delta)`。

关联分级：

| 级别 | 判定 | 说明 |
|------|------|------|
| SAME_FRAME | 同一 `frame_index` 内共现 | 强关联，零阈值 |
| NEAR_WINDOW | `|case_time_v - case_time_a| <= window_s` | 弱关联，窗口可配置 |
| UNLINKED | 以上皆否 | 不合并 |

### D2：窗口数值不冻结

```
window_s = configurable        # 配置项 signal_temporal_window_s
default  = TBD by acceptance data   # 由验收数据决定，不在本 ADR 拍板
```

候选档位（same frame / ≤0.5s / ≤1.0s / ≤2.0s）仅为测量后的选择空间，
本 ADR **不预设答案**。

### D3：前置依赖——统一 Runtime 时钟语义

窗口计算前，以下时间字段必须进入同一 runtime timeline：

```
RuntimeFrameContext.case_time          ← ADR-0039 显式化（锚点）
AudioPerceptionEvent → case_time       ← 经 episode_start_unix 锚定换算
RiskSignal → case_time / frame_index   ← 继承产出帧的 timeline 位置
```

runtime 会话启动时锚定一次 `episode_start_unix`，audio Unix 秒统一换算为
`case_time = audio_ts - episode_start_unix`。

### D4：数值确定的证据流程（Open Items）

默认窗口由真实 telephone_risk 验收数据决定，流程：

```
时钟统一（D3）
    ↓
采集真实数据：Person ENTERED / Telephone detected / RMS change / Risk signal
    ↓
统计 temporal delta distribution
    ↓
据分布选定档位（same frame / ≤0.5s / ≤1.0s / ≤2.0s）
    ↓
回填 default 并更新契约测试
```

### D5：与 episode 级关联器职责分离

| | SignalTemporalLinker（本 ADR） | CrossModalLinker（ADR-0028） |
|---|---|---|
| 层级 | signal 级 | episode 级 |
| 时机 | 同步、按帧/按信号 | 异步、落库后 |
| 输入 | RiskSignal 对 | EpisodicRecord 对 |
| 产物 | LinkedSignalPair（决策输入） | CrossModalLink（Memory 边索引） |

两者不共享代码、不共享配置。

## 架构位置（依赖方向）

```
Vision RiskSignal ─────────┐
                           │
Audio RiskSignal ──────────┤
                           ↓
                 SignalTemporalLinker     ← 本 ADR
                           ↓
                 Evidence Synthesis       ← ADR-0019 Evidence Fusion 的 Phase 1 落地
                           ↓
                  Evidence Strength       ← ADR-0042
                           ↓
                     DecisionPolicy
                           ↓
                     Warning / Action
```

Temporal Alignment 在前，Evidence Strength 在后——**Q3 是 Q4 的前置件，不是平行件**
（Owner 2026-08-22 修订）。

## 动机（Rationale）

- 时钟域统一是一切窗口计算的前置条件，否则 Δt 无意义；
- Combined Risk 需要 signal 级同步关联，episode 级异步关联器类型与时机均不匹配；
- 窗口数值应由真实数据分布驱动，而非工程直觉预设；
- 纯函数设计保证 VM-8 重放确定性。

## 后果（Consequences）

### 正面
- Evidence Synthesis（ADR-0042 ESCALATE 档）获得可信的时间维度输入；
- 窗口可配置化，调参不动架构；
- 时钟统一使 audio/vision 信号可在同一时间轴上审计与展示。

### 负面 / 约束
- 新增 1 组件 + 1 配置项 + `episode_start_unix` 锚定逻辑（含会话重启处理）；
- 默认窗口悬空期间，NEAR_WINDOW 关联不可用（SAME_FRAME 不受影响），
  ADR-0042 的 ESCALATE 放大在数据回填前保持关闭——这是有意为之的安全默认。

## 替代方案（Alternatives）

| 方案 | 描述 | 否决原因 |
|------|------|---------|
| 复用 CrossModalLinker | 为 RiskSignal 伪造 enter/leave 时间窗 | 类型污染（瞬时消息 ≠ episode）；触发时机不匹配（异步落库 vs 同帧决策） |
| RiskSignal 转 EpisodicRecord 走现有链路 | 伪装持久化实体 | 限界上下文污染；引入 Memory 落库副作用到决策路径 |
| 冻结默认 2.0s | Preflight Review 原方案 | Owner 否决：工程初始值 ≠ 审计可证事实；应先统一时钟再以真实 Δt 分布定档 |

## 与既有 ADR 的关系

- **ADR-0019**：其预留的"Evidence Fusion 独立阶段"在本 ADR + ADR-0042 中开始 Phase 1 落地；
  其"负面后果 ⚠️ 跨模态时序对齐需明确定义"正是本 ADR 回答的问题；
- **ADR-0028**：职责显式分离（D5），互不替代；
- **ADR-0039**：case_time 显式化是 D3 时钟统一的锚点；
- **ADR-0042**：下游消费者；ESCALATE 档的"多模态验证链"以本 ADR 的 LinkedSignalPair 为前提。