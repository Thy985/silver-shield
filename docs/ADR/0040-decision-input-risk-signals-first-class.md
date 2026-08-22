# ADR-0040: DecisionInput 引入 risk_signals 一等输入（多模态决策契约）

- 状态：Accepted（Owner 于 2026-08-22 ADR Preflight Review 拍板 Option C）
- 日期：2026-08-22
- 决策者：Owner
- 相关：
  - `docs/reports/ADR-PREFLIGHT-REVIEW-2026-08-22.md`（Q2 论证全文）
  - ADR-0030（Decision Boundary Contract——本 ADR 是其 C7 白名单的一次受控扩展）、
    ADR-0021（RiskSignal 类型与黑名单守卫）、ADR-0019（Evidence Fusion 独立阶段——
    本 ADR 使融合产物能以原生形态进入决策层）、ADR-0038（Audio → Risk 独立路径验证）
- 实施位置：`src/home_perception/analysis/decision_contract.py`、
  `src/home_perception/analysis/decision_policy.py`

---

## 背景（Context）

音频链路已能产出 `RiskSignal(source=AUDIO)`（`adapt_audio_event`），但：

1. `DecisionInput` 白名单仅 5 字段（trigger_events / decision_context / reasoning_input /
   reasoning_result / prior_warning），C7 导入期 fail-closed 断言使任何字段变更强制走 ADR；
2. 现有 `signal_adapter.risk_signal_to_perception` 的 features 映射表只覆盖视觉证据
   （dwell / visits / odd_hour），**完全不识别 audio_kind**——audio RiskSignal 经其翻译必落
   兜底 `visit_pending_verify, 0.5`，形成"运行时事实 → 产品幻觉"；
3. YAMNet `class_map_path=""` 修复前，9 个真实音频事件全部 fallback 为
   `audio_distress_cry`，进一步放大幻觉风险。

必须彻底结束"Audio RiskSignal 伪装成 PerceptionEvent 再进决策"的路径。

### Owner 修订意见（2026-08-22）

> - 不要把「6」理解成长期架构设计目标。应写成：**当前 DecisionInput 冻结上限从 5 个字段
>   临时扩展到 6 个；6 是硬顶**。
> - 必须明确：`risk_signals = Runtime RiskSignal 输入 ≠ Decision Result`。
>   绝对不能让 `RiskSignal.features` 里逐渐开始塞 `risk_level / recommended_action /
>   verdict / decision`，否则 C1 会重新失效。

## 决策（Decision）

### D1：新增一等输入字段

```python
@dataclass(frozen=True)
class DecisionInput:
    trigger_events: tuple[PerceptionEvent, ...]
    decision_context: DecisionContext
    risk_signals: tuple[RiskSignal, ...] = ()     # 新增（含 audio；空元组合法）
    reasoning_input: ReasoningInput | None = None
    reasoning_result: ReasoningResult | None = None
    prior_warning: WarningEvent | None = field(default=None)
```

### D2：C7 受控扩展（临时 + 硬顶）

`DECISION_INPUT_FIELD_WHITELIST` 从 5 字段扩展到 **6 字段**。
表述口径：这是当前冻结上限的**临时扩展，6 是硬顶**——不是长期架构设计目标，
不构成 5→6→7→8 的演进先例。此后任何新增字段必须触发 Bundle 化重构（ADR-0030 C7 原条款）。

### D3：语义边界（防 C1 失效）

- `risk_signals` = **Runtime RiskSignal 输入**（事实层瞬时跃迁消息），≠ Decision Result；
- `RiskSignal.features` 只允许证据强度描述键（audio_score / confidence / tier1 分数等），
  **禁止**出现 `risk_level / recommended_action / verdict / decision` 等决策语义键
  ——该禁区由 `RiskSignal.__post_init__` 的黑名单结构性守卫（ADR-0021）持续保证，
  本 ADR 重申且不豁免；
- 决策产物（risk_level / recommended_action）仍然只能出现在输出侧 `WarningEvent`。

### D4：确定性规范化

`risk_signals` 在 `__post_init__` 按 `(created_at, signal_id)` 升序稳定排序（C3 对齐）；
无信号传 `()`，语义 = "本次决策无实时风险信号"（对齐 Memory 可缺席原则）。

### D5：视觉兼容路径保留但降级为非唯一通路

`signal_adapter.risk_signal_to_perception` 继续服务于已有视觉兼容路径，
但**不再是 Audio → Decision 的桥**（audio 翻译即幻觉）。

### D6：假通电防护（前置条件）

`RuleBasedDecisionPolicy` 升级消费 `risk_signals` 之前，gateway 不接通 audio→risk 链
——防止"加了字段没人消费"的新静默旁路。policy 升级与本 ADR 同 PR 或紧随其后落地。

## 动机（Rationale）

- 多模态 Evidence Synthesis 需要 Vision + Audio 信号以**原生形态**同场进入决策层；
- 一等输入使 modality-aware routing（ADR-0042 Evidence Strength → Action）有结构基础；
- 翻译层幻觉被结构性消除（不再依赖 features 映射表的模态覆盖度）；
- 回放确定性由构造期排序保证（VM-8 / C3）。

## 后果（Consequences）

### 正面
- audio RiskSignal 无损进入决策链路；
- `DecisionInput` 成为真正的多模态收敛载体（Vision → RiskSignal ─┐ / Audio → RiskSignal ─┘
  → DecisionInput → Policy）；
- schema 测试同步钉死 6 字段白名单，越界改动导入期即炸。

### 负面 / 约束
- C7 名额消耗（6/6 满员，后续字段只能走 Bundle 化）；
- `RuleBasedDecisionPolicy` 必须升级，否则字段被静默忽略（D6 门控防住）；
- contract test 需覆盖：白名单断言更新、排序规范化、空元组、features 黑名单回归。

## 替代方案（Alternatives）

| 方案 | 描述 | 否决原因 |
|------|------|---------|
| A：加字段但不修订 C7 表述 | 直接 5→6 无硬顶声明 | 会形成逐次放宽先例，重回 God Object |
| B：PerceptionBundle 聚合 | vision_events + audio_signals 打包 | 过度抽象；混淆"感知事件"与"瞬时跃迁信号"两个限界上下文（ADR-0014 vs ADR-0021） |
| D：Engine 内部翻译 | 不动契约，Engine 里把 risk_signals 转 PerceptionEvent | 即 signal_adapter 幻觉路径的第二套实现，双口径漂移更糟 |

## 与既有 ADR 的关系

- **ADR-0030**：本 ADR 是其 C7 白名单的一次受控扩展（+1 字段，硬顶），D3 重申 C1 边界；
- **ADR-0019**：其"Evidence Fusion 独立阶段"产出的带模态标记证据集合，经本 ADR 获得
  进入决策层的原生通道；
- **ADR-0021**：RiskSignal 黑名单守卫是 D3 的结构性保障；
- **ADR-0042**：modality-aware routing 建立在 risk_signals 一等输入之上。