# ADR-0042: Audio Evidence Strength 分级与升级契约（telephone_risk）

- 状态：Accepted（Owner 于 2026-08-22 ADR Preflight Review 拍板：**冻结五档等级，
  不冻结阈值参数**）
- 日期：2026-08-22
- 决策者：Owner
- 相关：
  - `docs/reports/ADR-PREFLIGHT-REVIEW-2026-08-22.md`（Q4 论证全文 + Owner 修订）
  - ADR-0001（只产事实不裁决）、ADR-0026（音频感知链路）、
    ADR-0038（phone_interaction 降级 + Audio → Risk 独立路径验证）、
    ADR-0040（risk_signals 一等输入）、ADR-0041（SignalTemporalLinker——ESCALATE 前置件）

---

## 背景（Context）

音频链路当前为"单事件单信号"模型：每个 `AudioPerceptionEvent` 经 `adapt_audio_event`
直接翻译为一个 RAISED RiskSignal——无持续时长、无 CLEARED 路径、无跨事件聚合。

关键数据事实：YAMNet `class_map_path=""` 修复前，9 个真实音频事件**全部** fallback 为
`audio_distress_cry`。在此数据质量下，任何"单事件 → 高等级动作"的映射都会形成
运行时事实到产品判定的幻觉跃迁。

Preflight Review 曾建议默认参数 N=3 / T=10s / score≥0.3 / confidence≥0.5。
Owner 判定：

> 五档是好架构，但默认阈值不能现在冻结。……尤其当前 class_map_path="" 会把 9 个事件
> 全部 fallback 成 audio_distress_cry，所以现在根本不能根据现有数据去估计这些阈值。
>
> **冻结语义，不冻结参数。**

## 决策（Decision）

### D1：五档证据强度等级（冻结）

```python
class EvidenceStrength(str, Enum):
    INSUFFICIENT = "insufficient"   # 证据不足 → 不产信号（静默）
    MONITOR       = "monitor"       # 单次弱信号 → 仅记录观察
    RAISE         = "raise"         # 持续信号 → 升起本地风险信号（RAISED）
    NOTIFY        = "notify"        # 多独立信号 → 通知家属
    ESCALATE      = "escalate"      # 多模态验证链 → 升级中心
```

语义定义（冻结）：

| 档位 | 语义 |
|------|------|
| INSUFFICIENT | 置信或强度低于门槛，不产出任何 RiskSignal |
| MONITOR | 单次可信信号，仅观察记录，不触发告警动作 |
| RAISE | 同类声学信号具有持续性，升起本地风险信号 |
| NOTIFY | 多种独立声学信号并存，风险值得通知家属 |
| ESCALATE | Vision + Audio 经时间对齐构成多模态验证链，升级中心 |

### D2：判定维度（冻结）与阈值参数（不冻结）

各档判定的**维度**固定为：score、confidence、同类持续性（次数/时长）、独立信号多样性
（不同 kind 数量）、跨模态关联（经 ADR-0041 LinkedSignalPair）。

各维度的**阈值数值**全部为候选参数，不在本 ADR 冻结。

### D3：参数确定流程（Open Items，顺序不可跳步）

```
本 ADR（冻结五档 + 门控原则）
    ↓
修 YAMNet class_map_path（真实标签验证）
    ↓
重新获得真实 AudioKind 分布
    ↓
TelephoneRisk E2E
    ↓
测 precision / recall / false escalation
    ↓
确定 N / T / score / confidence 阈值（回填 config + 契约测试）
```

### D4：MONITOR ceiling 门控（硬闸门）

> 在 ADR-0042 的 class_map 修复前，不要让 telephone_risk 的 Audio RiskSignal 进入
> 真实高等级 Decision 路径。——Owner 2026-08-22

- 链路可以先接通：Audio → RiskSignal → Evidence Synthesis 全链路允许落地；
- 但证据强度**封顶 MONITOR**：fallback 事件永不驱动 RAISE 及以上；
- YAMNet 标签真实性验证通过后，经配置开关分级解除 ceiling；
- 该门控与 ADR-0038 Live Runtime 已验证的行为一致（LOW → MONITOR → LOG_ONLY）。

### D5：实现载体——新建 RealTimeAudioRiskEvaluator

独立组件（`analysis/realtime_audio_risk_evaluator.py`），不扩展现有视觉评估器：

- 维护同 kind 会话窗口，产出带状态机的 RAISED/CLEARED 信号对；
- CLEARED 语义 = 同 kind 静默超时（≠ 视觉侧"主体离场"兜底）;
- 与 `RealTimeRiskEvaluator` 共享 `RiskSignal` 类型，不共享实例状态。

### D6：ESCALATE 的反幻觉约束

ESCALATE 必须经 ADR-0041 时间关联验证（SAME_FRAME 或 NEAR_WINDOW 的 LinkedSignalPair），
不接受两个孤立 warning 的伪合成，亦不接受以 audio 单方面推断视觉事实
——与 ADR-0038 对替代方案 A4（用 Audio 推断 phone_interaction）的否决一脉相承。

## 架构位置

```
Vision RiskSignal ─────────┐
                           ↓
                 SignalTemporalLinker      ← ADR-0041
                           ↓
                 Evidence Synthesis
                           ↓
                  Evidence Strength        ← 本 ADR（五档）
                           ↓
                     DecisionPolicy        ← modality-aware routing
                           ↓
                     Warning / Action
```

## 动机（Rationale）

- Evidence Continuity > Event Count：持续性/多样性/跨模态验证是升级依据，单事件不是；
- 数据质量门控（D4）结构性封死"9/9 fallback → 高等级误报"路径；
- 显式化"检测 → 风险 → 决策 → 行动"之间的证据强度，使升级路径可解释、可审计、可灰度。

## 后果（Consequences）

### 正面
- telephone_risk 升级路径获得冻结的语义骨架，参数可由验收数据安全回填；
- 与 ADR-0038 已验证行为（MONITOR 封顶期）无缝衔接；
- RealTimeAudioRiskEvaluator 补齐音频侧缺失的 CLEARED 状态机。

### 负面 / 约束
- 新增 1 个评估器组件 + 配置面扩大（5+ 参数项，含 window/N/T/置信门槛/ceiling 开关）；
- class_map 修复成为 RAISE 放大的硬前置依赖（排期风险显式化）；
- 参数悬空期间 ESCALATE 不可用——有意为之的安全默认。

## 替代方案（Alternatives）

| 方案 | 描述 | 否决原因 |
|------|------|---------|
| 单音频直接 raise | 一发生 RiskSignal 即升级 | 9/9 fallback 下 100% 幻觉；违反 Evidence Continuity 哲学 |
| 仅持续性维度 | 同 kind 持续 N 秒即升级 | 方向正确但单独不足：现模型无状态机无 CLEARED，"持续"无从判定；已并入 RAISE 档维度 |
| 冻结五档 + 默认参数一并落库 | Preflight Review 原方案 | Owner 否决：候选规则 ≠ ADR 默认事实；须先修 class_map 再以 precision/recall 定参 |

## 与既有 ADR 的关系

- **ADR-0001**：五档均为"证据强度"描述，非诈骗判定；最终裁决归中心；
- **ADR-0026**：消费其产出的 AudioPerceptionEvent（5 类 kind）；
- **ADR-0038**：承接其 Audio → Risk 独立路径验证结论；其 MONITOR/LOG_ONLY 行为即本 ADR
  ceiling 期的预期表现；其 A4 否决（audio 不推断视觉）在 D6 重申；
- **ADR-0040**：Evidence Strength 产物作为 risk_signals 一等输入进入 DecisionPolicy；
- **ADR-0041**：ESCALATE 档以其 LinkedSignalPair 为硬前提（依赖方向：Q3 → Q4）。