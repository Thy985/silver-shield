# ADR-0018: 实时风险信号与历史事件流分离（Real-time Risk Signal vs Historical Visitor Event）

- **状态**：Proposed（Owner 建议 · 2026-07-26）
- **范围**：未来架构方向（v2 / 后 MVP），**当前 MVP 不实现**；本 ADR 仅固化决策，不改动现有冻结契约。
- **决策者**：Owner
- **相关**：ADR-0010（WarningEvent 决策架构）、ADR-0006（VisitorTrack）、
  `src/home_perception/analysis/{event_builder,event,feature_extractor}.py`、
  ADR-0019（多模态）、ADR-0020（身份分离）

## 1. 背景（Context）

当前已冻结的链路（ADR-0010 §2）是一条**线性**管道：

```
DetectionResult
      │
      ▼
VisitorTrack
      │
      ▼
VisitorEvent          ← 离场即生成（event_builder.py:9,116；event.py:6）
      │
      ▼
RiskFeature           ← 窗口基于 leave_time（feature_extractor.py:64-70）
      │
      ▼
PerceptionEvent
      │
      ▼
WarningEvent          ← 决策层
```

存在一个**隐含假设**，直到本次复盘才被显式指出：

> 风险判断依赖「完整访问过程结束」。

证据（代码事实）：
- `VisitorEvent` 只在 `VisitorTrack` 转 `LEFT`（离场）时生成（`event_builder.py:116`「track 在上一次 update 是 active，本次变 left → 生成 VisitorEvent」；`event.py:6`「离场即生成的离散事实事件」）。
- `FeatureExtractor` 的频率窗口以 `leave_time` 为起点（feature_extractor.py:64-70）：`visits_in_window` 统计的是**已离场**的历史 `VisitorEvent`。即 `repeat_visit` 这类关键风险，只能在访客**走后**才被算出。
- 因此 `WarningEvent`（决策层）必然在访客离场之后才能产生。

**与产品目标的冲突**：SilverShield 的定位是「数字孪生 + 协同预警」，**预警必须在风险发生过程中触发**（蹲守中、异常停留中、重复徘徊中），而不是等人走光了再出报告。当前架构无法在访问进行中产出 `WarningEvent`。

## 2. 决策（Decision）

### 2.1 保留 `VisitorEvent` 不变（历史事件流）
不改动 `VisitorEvent` 的「离场即生成 / 完整生命周期」语义，也不破坏其 Schema（ADR-0005 稳定性、ADR-0010）。
它继续承担：**访问总结、历史分析、Memory/Profile 喂养**。

### 2.2 新增 `Behavior State` 中期节点 + 双下游分流
在 `VisitorTrack` 之后、原 `VisitorEvent` 之前，引入**实时行为状态**节点（每帧 / 短间隔产出，描述进行中的行为：在场、停留累积、重复出现计数、异常时段、接近度）。

`Behavior State` 向下分流为两条独立输出：

```
                 DetectionResult
                       │
                  VisitorTrack
                       │
                  Behavior State
                     /        \
                    /          \
         Real-time Risk    Historical Event
              │                 │
              ▼                 ▼
        RiskSignal         VisitorEvent   ← 离场生成（保持不变）
              │                 │
              ▼                 ▼
        WarningEvent        Memory / Profile
              │
              ▼
           Action
```

### 2.3 新增事件对象 `RiskSignal`
`RiskSignal` 是 `VisitorEvent` 的**实时对应物**：在访问进行中产出，承载「行为状态变化」而非「完整访问总结」。它是实时风险判断的输入，可**在访客离场前**触发 `WarningEvent → Action`。

## 3. 动机（Rationale）

- **产品目标是实时干预**：预警的价值在于「事中」，事后总结无法驱动「家属提醒 / 社区核验」的及时性。
- **不破坏已有契约**：`VisitorEvent` 下游（中心、历史分析）期望完整生命周期；新增并行实时流，而非改写它——符合 ADR-0005 稳定性与 ADR-0010 分层。
- **为 ADR-0019 铺路**：`RiskSignal` 是多模态信号（语音）进入决策的天然入口。
- **避免边界回潮**：若把风险判断直接塞回 `VisitorTrack`，会重演 ADR-0007/0009 已修复的「事实层 vs 语义层」混淆；`Behavior State` 是干净的中间抽象。

## 4. 后果（Consequences）

### 正面
- ✅ 支持**访问进行中**实时推送 `WarningEvent` → Action。
- ✅ `VisitorEvent` 契约零破坏，下游 / 中心无需改动。
- ✅ 实时信号流与历史记录流职责清晰，可独立演进、独立测试。

### 负面 / 约束
- ⚠️ 新增 `RiskSignal` 对象 + 新管道分支 → 需新 Schema（走 ADR-0005 评审）+ 新 Contract Test（ADR-0014 冻结治理）。
- ⚠️ `Behavior State` 节点必须足够轻（每帧级），且**不复刻** `FeatureExtractor` 的离场后统计逻辑。
- ⚠️ `DecisionPolicy` 需明确如何同时消费 `RiskSignal`（实时）与 `PerceptionEvent`（语义）——避免双源重复决策。

### 后续动作
- 定义 `RiskSignal` Schema（字段建议：`track_id`/`visitor_id`、`behavior_type`、`state`、`confidence`、窗口计数）；
- 新开 ADR 锁定 `RiskSignal` Schema + 对应 Contract Test；
- 明确 `Behavior State` 与 `FeatureExtractor` 的职责切分。

## 5. 替代方案（Alternatives）

- **让 `VisitorEvent` 在访问中触发**：否决。破坏「离场即生成」语义，且下游消费者期望完整生命周期（enter/leave/duration）；违反 ADR-0005 稳定性。
- **不经新节点、直接从 `VisitorTrack` 算风险**：否决。把「事实」（ADR-0001）与「实时风险信号」混淆，重演 ADR-0007/0009 已修的边界违规。
- **把干预全部推迟到访客离场后**：否决。与产品「实时预警」目标直接矛盾。

## 6. 与既有 ADR 的关系

- **ADR-0010**：本 ADR **扩展而非取代**——`WarningEvent` 仍是决策对象，现在可由 `RiskSignal` 实时喂入（也可由 `PerceptionEvent` 喂入）。
- **ADR-0006**：`Behavior State` 消费 `VisitorTrack`（`track_id`），自然衔接 ADR-0020 的身份分离。
- **ADR-0019**：`RiskSignal` 是多模态（音频）信号汇入决策的天然载体。
