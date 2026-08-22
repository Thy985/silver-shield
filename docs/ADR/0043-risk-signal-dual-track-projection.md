# ADR-0043: RiskSignal 双轨投影契约（覆盖式当前态 + 累积式事件历史）

- 状态：Accepted（Owner 于 2026-08-22 ADR Preflight Review 拍板双轨制）
- 日期：2026-08-22
- 决策者：Owner
- 相关：
  - `docs/reports/ADR-PREFLIGHT-REVIEW-2026-08-22.md`（Q5 论证全文 + Owner 修订）
  - ADR-0021（RiskSignal 瞬时跃迁语义）、ADR-0035 / ADR-0036（Evidence Presentation /
    统一 Case Viewer 的 VM 契约——本 ADR 是其 Live 投影侧扩展）、
    `src/home_perception/visualizer/viewer/live_adapter.py`（ProjectionAccumulator 实施位置）

---

## 背景（Context）

`ProjectionAccumulator._last_risk_signals` 为覆盖式：每帧被最新值替换，
信号被后续帧覆盖后前端永久不可见。

实测实证（ws_payloads.jsonl 取证）：frame=0 的 RAISED 被 frame=1 的 CLEARED 覆盖，
前端只见 CLEARED、0 条 RAISED 存留——"风险莫名其妙被解除"的产品幻觉。
同时 `paired_signal_id` 配对语义依赖历史可见性，覆盖式使其失效。

代码库中累积式先例齐备（`_perception_events_cache` / `_warnings_cache` / `_audio_events`
均为"持久列表 + 去重键 + seq 序号"三件套），且产品层已有同构三分法：
CURRENT STATE / RECENT CHANGES / HISTORY。

## 决策（Decision）

### D1：核心契约（冻结）

> **Projection 必须同时支持「状态」与「事件」。**

- **状态轨**（覆盖式，保留现状）：回答"当前风险是什么？"；
  驱动服务端权威 risk_transition 状态机（PR-B 红线不变：服务端判定 raised/cleared/active，
  前端只渲染不推断）；
- **事件轨**（累积式，新增）：回答"刚刚发生过什么？"；
  RAISED→CLEARED 全生命周期可追溯，`paired_signal_id` 配对可渲染。

两轨与既有 CURRENT STATE（`_last_risk_levels` 等）/ HISTORY（`_warnings_cache` 等）
分层完全同构。

### D2：底层幂等契约（冻结）

- 幂等键 = `signal_id`（VM-8 重放幂等：live 循环重启重喂同一信号不重复累积）;
- 序列 = `seq`（单调递增，供 delta 增量推送判定）。

### D3：payload 形状不冻结

具体 payload 字段名与形状（如 risk_delta 内是否拆 current / recent_events[]、
history 是否随每条 delta 全量携带等）**留给实现设计**决定，本 ADR 只冻结 D1/D2。
实现设计须满足：

- 增量推送（指纹未变不推；新 seq 才推），避免全量刷屏；
- 浏览器渲染分工：CURRENT STATE 卡消费状态轨；风险时间线 / Narrative 消费事件轨；
  前端零推断（VM-1 / PR-B 红线延续）。

### D4：长会话上限策略

事件轨与 `_audio_events` 同为无界持久列表；如需上限另立配置项，
不在本 ADR 范围（显式登记为开放项，避免顺手扩权）。

## 动机（Rationale）

- 单一覆盖语义直接造成实测的"0 RAISED 存留"缺陷，破坏产品叙事；
- 状态机判定需要覆盖式（当前态），叙事渲染需要累积式（历史）——两种语义不可互相替代；
- 三件套模式在本代码库已被三处验证，双轨是模式复用而非新发明。

## 后果（Consequences）

### 正面
- RAISED→CLEARED 全生命周期可追溯；Narrative 获得真实素材；
- 服务端权威状态机零改动（PR-B 红线保持）；
- 幂等由 signal_id 主键结构性保证。

### 负面 / 约束
- delta payload 增加（增量推送缓解）；
- 长会话无界增长（D4 登记为开放项）；
- schema 测试需钉死两轨字段集合 + 重放去重回归。

## 替代方案（Alternatives）

| 方案 | 描述 | 否决原因 |
|------|------|---------|
| 只保留覆盖式 + 加 risk_state 字段 | 在投影层再造长期状态 | 混淆 ADR-0021"瞬时跃迁消息"与"长期状态"；形成第二套风险状态口径与服务端状态机漂移 |
| RiskSignal 历史并入 _warnings_cache | 复用 warning 持久列表 | RiskSignal ≠ WarningEvent（事实含 CLEARED vs 决策仅正向）；task-cards 数据源语义污染 |
| 提前冻结 payload 字段名 | Preflight Review 原方案（risk_signal_history / _history_seqs 入 ADR） | Owner 修订：核心契约是"支持状态和事件"，字段名称属实现细节，过早冻结限制实现自由度 |

## 与既有 ADR 的关系

- **ADR-0021**：其"瞬时跃迁消息"语义是双轨制的类型论依据（瞬时消息天然适合事件轨累积）；
- **ADR-0035 / 0036**：Live 投影侧遵循其 VM 契约（唯一 View Model / 禁 synthetic / 前端零推断）；
  本 ADR 是 EvidenceProjection 在 RiskSignal 维度的补全；
- **ADR-0040**：事件轨素材来自 runtime 经 risk_signals 一等输入流转后的投影。