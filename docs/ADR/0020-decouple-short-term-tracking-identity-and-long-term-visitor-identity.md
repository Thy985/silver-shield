# ADR-0020: 短期追踪身份与长期访客身份分离（Decouple Short-term Tracking Identity and Long-term Visitor Identity）

- **状态**：Proposed
- **日期**：2026-07-26
- **范围**：未来架构方向（v2 / 后 MVP），**当前 MVP 不实现**；本 ADR 仅固化决策，不改动现有冻结契约。
- **决策者**：Owner
- **相关**：ADR-0006（VisitorTrack = track_id，会话级）、
  `src/home_perception/detection/{tracker,schemas}.py`、`analysis/event.py`（visitor_id）、
  ADR-0018（Historical Event → Memory/Profile）

## 1. 背景（Context）

ADR-0006 Decision 4 已明确：

> `VisitorTrack` 只代表**当前摄像头会话内**的同一人，**不做跨天身份保持**；`track_id` 仅当前摄像头会话内有效；跨天 / 跨设备重识别属 P0-6 / P1。

但当前 `VisitorEvent.visitor_id` 实际上就是 ByteTrack 的 `track_id`（会话级）。这导致两个概念被**隐式等同**：

| 概念 | 含义 | 生命周期 |
| --- | --- | --- |
| **Tracking ID**（track_id） | 当前摄像头、当前时间、当前 session 内的帧间关联 | 短：一次在场 / 一次会话 |
| **Identity**（现实世界的人） | 跨 session / 跨天 / 跨摄像头稳定的人 | 长：持久 |

**隐藏风险**：一旦我们在 ADR-0018 的「历史事件流 → Memory / Profile」分支、或中心 RiskTwin 之上，基于 `visitor_id` 建画像，我们实际上是在给一个** track_id** 建画像，而不是给**一个人**：
- 同一个人当天离开又回来 → 新 `track_id` → 身份碎片化；
- 摄像头重启 / 短暂遮挡导致 ID 跳变 → 同一人被拆成多份；
- 跨摄像头同一人 → 多个互不关联的 `visitor_id`。

## 2. 决策（Decision）

### 2.1 显式拆成两层
- **Track ID（短期）**：ByteTrack `track_id` / `VisitorTrack`，作用域 = 单摄像头 + 单会话。是 detection/tracking 的输出。
- **Identity（长期）**：解析出的 **Person Identity**（跨会话 / 跨天 / 跨摄像头稳定），由独立组件产出。

### 2.2 引入 `Identity Resolver` 阶段
```
Track ID (track_id / VisitorTrack)
        │
        ▼
  Identity Resolver          ← 新组件：track_id → person_id
        │
        ▼
  Person Identity (person_id)
```

- `VisitorEvent` / `RiskSignal` / `Memory` **引用稳定的 Person Identity**，同时保留 `track_id` 作为工程溯源（provenance）。
- 数据模型明确区分：**provenance（track_id）vs meaning（person_id）**。

### 2.3 `person_id` 分配策略可替换
- v1（MVP 后过渡）：简单的「同摄像头同 session 内合并」+ 家属确认回填；
- v2：外观 embedding / ReID；
- 最终可结合「家属在 P0-11 闭环里确认」做人工校正。

## 3. 动机（Rationale）

- **Memory / Profile 与中心 RiskTwin 需要稳定的人身份**，而不是会闪烁的 track_id。
- **ADR-0006 已预警** `track_id` 是会话级的；本 ADR 把这条边界**正式命名并给出解析路径**，而不是默认把 track_id 当成 identity。
- **为未来能力铺路**：跨摄像头、跨天、家属确认身份，都不必事后重构——`Identity Resolver` 是可替换组件。
- **避免错误画像**：在 track_id 上建画像会产生重复 / 碎片身份，直接污染风险判断与历史分析。

## 4. 后果（Consequences）

### 正面
- ✅ Memory / Profile / 中心拿到**稳定身份**，去重与关联正确。
- ✅ `track_id` 保持廉价（ByteTrack）；`Identity Resolver` 是独立、可替换组件（embedding / ReID / 人工确认）。
- ✅ 数据模型清晰：溯源（track_id）与语义（person_id）分离。

### 负面 / 约束
- ⚠️ 新增组件 `Identity Resolver` + 新 id 空间 `person_id` + 映射表。
- ⚠️ 解析有错误模式（误合并 / 误拆分），需阈值 + 人工确认（与 P0-11 家属确认闭环天然契合）。
- ⚠️ embedding 存储涉及隐私审查（ADR-0001 / AGENTS §3.3 精神）。

### 后续动作
- 定义 `PersonIdentity` Schema 与 `person_id` 分配规则；
- 新开 ADR 锁定 `Identity Resolver` 策略（ReID vs 确认 vs 简单合并）与隐私边界。

## 5. 替代方案（Alternatives）

- **把 track_id 当 person_id（现状）**：否决。ADR-0006 已注明其会话级局限；在其上建画像会碎片化身份。
- **一开始就上完整 ReID**：否决。重、隐私负担大，MVP 不需要（ADR-0006 同款理由）。
- **纯人工身份、无解析器**：否决。无法自动化 Memory / Profile 关联，不可扩展。

## 6. 与既有 ADR 的关系

- **ADR-0006**：本 ADR 直接建立在它的「track_id 会话级」预警之上，补上它推迟的**解析层**。
- **ADR-0018**：本 ADR 的「Historical Visitor Event → Memory / Profile」分支消费的是 **Person Identity**，而非 Track ID——两条 ADR 共同构成「实时 / 历史 + 身份」的未来骨架。
