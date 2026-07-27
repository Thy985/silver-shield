# 技术债台账（Tech Debt Ledger）

> 本文件记录 SilverShield Home 感知模块沉淀的技术债，按编号追踪。
>
> **记录原则**：只记录**已经被验证存在、但当前不阻塞架构正确性**的工程问题。不要把未来可能需要的能力提前写成债务。
>
> **不纳入台账的类别**：
> - ❌ 未来能力（Agent Context / 音频接入 / Identity ReID / Observation 独立对象）—— 这些是 Roadmap Phase 2-5 职责，不是缺陷
> - ❌ 阻断性问题 —— 直接开 PR 修复，不进入台账
> - ❌ 架构决策本身 —— 决策属 ADR 职责，债务只记录"决策落地后暴露的工程问题"
>
> 与 `docs/09_risks.md` 的区别：风险是"可能发生的不确定性"，技术债是"已经存在但暂缓修复的工程问题"。
>
> 编号规则：`TD-NNNN`，4 位编号与 ADR 体系对齐，从 0024 起步（ADR 当前到 0023）。状态 `Open → Accepted(v2) → Resolved`。

---

## 台账

### TD-0024 · RecentBehaviorStore 长时间运行淘汰优化

| 字段 | 值 |
| --- | --- |
| **状态** | Open |
| **优先级** | P1（未来优化，不阻塞发布） |
| **归属阶段** | v2 / Phase 1+（ADR-0021 Stage E 发现） |
| **发现来源** | [ADR-0021 验证报告 §5.2](reports/ADR-0021-validation-report.md) · R3 Soak Test |
| **发现日期** | 2026-07-27 |
| **相关 PR** | #71（Stage E Soak Test） |
| **相关 ADR** | ADR-0021（实时风险状态流） |

#### 背景

R3 2h soak test 发现 `store_entries` 持续增长：

```
store_entries: 2 → 308
```

当前逻辑：

- 滑窗数据会清理（窗口内记录正确）
- 风险计算正确
- 无 active track 泄漏

但是 **inactive visitor key 的生命周期管理不足**：

```
visitor_instance_id
        |
        v
RecentBehaviorStore
        |
        v
历史访问记录（旧 key 保留时间过长）
```

#### 影响

**当前**：

- 单设备短时间运行无明显影响
- 2h soak 未导致性能异常（p99 仍 < 200ms）

**长期**（多用户 / 多天连续运行 / 高访问频率）：

```
store_entries ↑
遍历成本 ↑
GC 压力 ↑
```

R3 实测：p50 延迟漂移 +13ms/h（60ms → 86ms），归因 `_entries` dict 的 `O(N)` 遍历。

#### 优化方向

**不是改架构，而是增强 eviction**。

**方案 A（推荐）**：增加 `last_seen_at` 字段，定期清理过期 entry

```python
@dataclass
class BehaviorHistory:
    visitor_instance_id: str
    visits: deque
    last_seen_at: datetime

# 定期扫描
if now - last_seen_at > retention:
    delete()
```

**方案 B**：改成 TTL Cache（如 `cachetools.TTLCache`），自动按 TTL 过期

#### 验收标准

- [ ] 修复后重跑 R3 Soak Test（2h / 75 loops）
- [ ] 新增 soak 指标：`store_entries_after_2h < baseline * threshold`
- [ ] 2h 运行：entries 稳定，不持续线性增长（期望 < 50）
- [ ] p50 延迟漂移率：从 +13ms/h 降至 < +2ms/h
- [ ] `visits_in_window` 计数与修复前一致（不破坏语义）
- [ ] 新增单元测试：模拟 1000 个不同 visitor_instance_id 依次进入，断言 `_entries` 不超过上限

---

### TD-0025 · 实时/历史 Warning 来源可观测性增强

| 字段 | 值 |
| --- | --- |
| **状态** | Open |
| **优先级** | P2 |
| **归属阶段** | v2（ADR-0021 Stage E 发现） |
| **发现来源** | [ADR-0021 验证报告 §4.2.1](reports/ADR-0021-validation-report.md) · R2 Soak Test |
| **发现日期** | 2026-07-27 |
| **相关 PR** | #71（Stage E Soak Test） |
| **相关 ADR** | ADR-0021 / ADR-0010（WarningEvent 决策架构） |

#### 背景

R2 发现 `historical_count=0`，不是 pipeline bug，而是测试无法区分 Warning 来源：

```
WarningEvent
 |
 +-- historical path（VisitorEvent → DecisionEngine）
 |
 +-- realtime path（RiskSignal → adapter → DecisionEngine）
```

当前 Decision 层统一输出 `WarningEvent`，**来源信息丢失**。

#### 影响

**不影响运行**。但影响：

- 调试（无法定位 Warning 来自哪条路径）
- 指标分析（无法拆分 historical vs realtime 贡献）
- 线上监控（只能知道"今天产生 100 次 Warning"，不知道"历史 30 / 实时 70"）

Stage E Soak Test 通过**三模式对照实验**（historical/shadow/realtime 独立运行）绕过此问题验证 `Z >= X`，但这是测试侧 workaround，生产环境仍无法观测。

#### 优化方向

**不要改 WarningEvent 主契约**（ADR-0014 冻结）。

**方向 A**：在 `DecisionContext` 增加 metadata

```python
DecisionContext:
{
  "source": "realtime",  # 或 "historical"
  "origin_signal": "RiskSignal"  # 或 "VisitorEvent"
}
```

**方向 B**：内部 trace（`WarningEvent.meta.trace_id` 关联上游）

```
WarningEvent
    |
    trace_id
        |
        ├── VisitorEvent（historical）
        |
        └── RiskSignal（realtime）
```

#### 验收标准

- [ ] 生产环境能输出 `warning_breakdown: {historical: X, realtime: Y}`
- [ ] 不破坏 WarningEvent 主契约（字段新增属 MINOR，ADR-0014 允许）
- [ ] Soak Test 可移除三模式对照实验的 workaround（直接从单次 realtime 运行拆分 X / Y）

---

### TD-0026 · Soak Test 多主体并发场景补充

| 字段 | 值 |
| --- | --- |
| **状态** | Open |
| **优先级** | P2 |
| **归属阶段** | v2（ADR-0021 Stage E 覆盖度缺口） |
| **发现来源** | [ADR-0021 验证报告 §4.4](reports/ADR-0021-validation-report.md) · R3 Soak Test |
| **发现日期** | 2026-07-27 |
| **相关 PR** | #71（Stage E Soak Test） |
| **相关 ADR** | ADR-0021（实时风险状态流） |

#### 背景

当前 R3 主要验证：

- 单主体
- 异常停留
- 生命周期闭合（RAISED → CLEARED 配对）

但是 `RealTimeRiskEvaluator` 的核心结构是**多主体并发**：

```python
Dict[track_id, _TrackRiskState]
```

真正的风险在多主体场景：

```
Person A: RAISED
Person B: RAISED

A 离开: CLEARED(A)
         └── 不能影响 ACTIVE(B)
```

#### 未覆盖场景

- 多人同时在场，各自独立 RAISED/CLEARED
- 一人离场时 CLEARED 不能错配到另一人的 RAISED
- `paired_signal_id` 在多主体下引用完整性

#### 验收标准

- [ ] 新增多人视频场景（如 CAVIAR `Meet_WalkTogether1` 循环）
- [ ] `paired_signal_id` 正确率 = 100%（多主体下不错配）
- [ ] `cross_track_clear = 0`（A 的 CLEARED 不能清除 B 的 ACTIVE_RISK）
- [ ] `active_tracks_peak` 与场景设计主体数吻合

---

### TD-0027 · RealTimeRiskEvaluator 状态恢复策略

| 字段 | 值 |
| --- | --- |
| **状态** | Deferred |
| **优先级** | P3 |
| **归属阶段** | 未来 Memory ADR |
| **发现来源** | ADR-0021 设计边界 |
| **发现日期** | 2026-07-27 |
| **相关 ADR** | ADR-0021（实时风险状态流）/ 未来 Memory ADR |

#### 背景

当前设计：进程重启后 `ACTIVE_RISK` 状态丢失。

```
重启前: visitor A = ACTIVE_RISK
重启后: visitor A = NONE
```

#### 当前为何接受

ADR-0021 明确：`BehaviorState` / evaluator state 是 **volatile working state**，不是长期状态。状态恢复属 Memory ADR 职责（Short-term Memory persistence），不是实时风险流职责。

#### 未来解决

属 Memory ADR（Short-term Memory persistence），不在 ADR-0021 范围内。

#### 验收标准

- [ ] 由 Memory ADR 定义恢复策略后再回填本条

---

## 状态汇总

| 编号 | 标题 | 状态 | 优先级 | 来源 |
| --- | --- | --- | --- | --- |
| TD-0024 | RecentBehaviorStore 长时间运行淘汰优化 | Open | P1 | ADR-0021 R3 soak test |
| TD-0025 | 实时/历史 Warning 来源可观测性增强 | Open | P2 | R2 historical/realtime attribution issue |
| TD-0026 | Soak Test 多主体并发场景补充 | Open | P2 | ADR-0021 validation coverage |
| TD-0027 | RealTimeRiskEvaluator 状态恢复策略 | Deferred | P3 | Future Memory ADR |

**下一轮工程优化重点**：TD-0024。它是 R3 唯一发现的"系统随时间运行而退化"的问题，属于从 Demo 架构向长期运行系统迁移时必然会出现的问题。

---

## 不纳入台账的类别（明确边界）

以下内容**不是技术债**，不纳入本台账：

| 项 | 原因 | 归属 |
| --- | --- | --- |
| ❌ Agent Context | 不是缺陷。`RiskSignal.to_dict()` / `BehaviorState.to_dict()` 已提供接口，消费层未来设计即可 | Roadmap Phase 5 |
| ❌ 音频接入 | 不是债务。属新能力 | Roadmap Phase 3 / ADR-0022 |
| ❌ Identity / ReID | 不是债务。属新能力 | Roadmap Phase 4 / ADR-0023 |
| ❌ Observation 独立对象 | 不是债务。`FrameResult.behavior_states` 作为 observation 足够，未来 Memory ADR 再决定 | 未来 Memory ADR |

---

## 维护规则

1. **记录原则**：只记录**已被验证存在、但当前不阻塞架构正确性**的工程问题；不把未来能力提前写成债务
2. **新增条目**：发现非阻断问题时，先记录到本台账，再决定是否进入 v2 backlog
3. **状态流转**：`Open`（待评估）→ `Accepted (v2)`（确认进入 v2 backlog）→ `Resolved`（v2 修复 PR 合并）；`Deferred` 表示归属未来 ADR，本模块不主动修复
4. **不立即修复原则**：MVP / Phase 1 范围内不阻断交付的问题，记录后不修；阻断性问题不进入本台账，直接开 PR
5. **引用闭环**：每条 TD 必须引用发现来源（验证报告 / PR / ADR）；修复后必须引用修复 PR
6. **编号规则**：`TD-NNNN`，4 位编号与 ADR 体系对齐，从 0024 起步（ADR 当前到 0023），递增不复用
