# 技术债台账（Tech Debt Ledger）

> 本文件记录 SilverShield Home 感知模块沉淀的技术债，按编号追踪。
> **原则**：不阻断 MVP / Phase 1 交付的问题不立即修复，记录后进入 v2 backlog；阻断性问题不进入本台账，直接开 PR 修复。
>
> 与 `docs/09_risks.md` 的区别：风险是"可能发生的不确定性"，技术债是"已经存在但暂缓修复的工程问题"。
>
> 编号规则：`TD-NNN`，从 001 递增不复用。状态 `Open → Accepted(v2) → Resolved`。

---

## 台账

### TD-001 · RecentBehaviorStore eviction optimization

| 字段 | 值 |
| --- | --- |
| **状态** | Accepted (v2 backlog) |
| **优先级** | P1 |
| **归属阶段** | v2 / Phase 1+（ADR-0021 Stage E 发现） |
| **发现来源** | [ADR-0021 验证报告 §5.2](reports/ADR-0021-validation-report.md) · R3 Soak Test |
| **发现日期** | 2026-07-27 |
| **相关 PR** | #71（Stage E Soak Test） |
| **相关 ADR** | ADR-0021（实时风险状态流） |

**现象**：

R3 长期稳定性测试（2h / 75 loops / 75299 帧）中，`RecentBehaviorStore._entries` 从 2 线性增长至 308，p50 延迟从 60ms 漂移至 86ms（漂移率 +13ms/h）。

**根因**：

[`recent_behavior_store.py`](../src/home_perception/analysis/recent_behavior_store.py) 的 `update()` 仅在访客**再次进入**时清理窗口外记录：

```python
# recent_behavior_store.py:67-77
bucket = self._entries.setdefault(visitor_instance_id, [])
if enter_time not in bucket:
    bucket.append(enter_time)

cutoff = now - timedelta(seconds=window_seconds)
in_window = [t for t in bucket if t >= cutoff]
if in_window:
    self._entries[visitor_instance_id] = in_window
else:
    self._entries.pop(visitor_instance_id, None)
```

测试场景中视频循环产生新 `visitor_instance_id`，旧访客不再进入，其条目不会主动清理，导致 `_entries` dict 线性增长。`snapshot()` / `update()` 的 `O(N)` 遍历（N = store_entries）导致 p50 漂移。

**影响评估**：

| 维度 | 评估 |
| --- | --- |
| **是否阻断 MVP** | ❌ 否。MVP 场景下访客流是连续的，旧访客条目会被滑窗清理（窗口 30min） |
| **内存影响** | 可忽略。2h 内 308 条目 × ~100 bytes ≈ 30KB |
| **延迟影响** | 可接受。按 +13ms/h 速率，24h 漂移 +312ms，仍在 p99 < 500ms 范围 |
| **业务语义影响** | ❌ 无。`visits_in_window` 计数正确性不受影响（窗口外条目不参与计数） |
| **触发条件** | Soak Test 视频循环场景特有（同一访客不会以相同 visitor_instance_id 再进入）；生产环境访客流连续，不易触发 |

**为何不立即修**：

1. ADR-0021 Stage A-D 已交付并冻结，pipeline 不再改动
2. 非 MVP 阻断问题，业务语义正确
3. 修复需新增定期扫描逻辑或 LRU 缓存，属于 `RecentBehaviorStore` 内部实现重构，应纳入 v2 优化批次而非单点 patch
4. R3 已验证 2h 内不影响稳定性，足够支撑比赛 Demo

**建议方案**（v2 实施时评估）：

| 方案 | 描述 | 优缺点 |
| --- | --- | --- |
| **A. 定期扫描** | 每 N 帧（如 100 帧）扫描所有条目，清理窗口外记录 | 简单；扫描帧偶发延迟抖动 |
| **B. LRU 驱逐** | 给 `_entries` 加上限（如 1000 条），超限时驱逐最旧条目 | 可控；需调上限参数 |
| **C. 后台清理** | 独立协程/定时器定期清理 | 无主循环抖动；pipeline 单线程，引入复杂度 |
| **D. 索引优化** | `snapshot()` 用有序结构（如 SortedDict）按时间索引 | 降低 O(N) → O(log N)；结构改动大 |

**推荐**：方案 A（定期扫描），改动最小且足够；若 v2 引入多设备/多访客高并发，再升级为 B。

**验收标准**（v2 修复时）：

- [ ] 修复后重跑 R3 Soak Test（2h / 75 loops）
- [ ] `store_entries` 趋势：线性增长 → 在场景访客数附近波动（期望 < 50）
- [ ] p50 延迟漂移率：从 +13ms/h 降至 < +2ms/h
- [ ] `visits_in_window` 计数与修复前一致（不破坏语义）
- [ ] 新增单元测试：模拟 1000 个不同 visitor_instance_id 依次进入，断言 `_entries` 不超过上限

---

## 状态汇总

| 编号 | 标题 | 状态 | 优先级 | 阶段 |
| --- | --- | --- | --- | --- |
| TD-001 | RecentBehaviorStore eviction optimization | Accepted (v2) | P1 | v2 / Phase 1+ |

---

## 维护规则

1. **新增条目**：发现非阻断问题时，先记录到本台账，再决定是否进入 v2 backlog
2. **状态流转**：`Open`（待评估）→ `Accepted (v2)`（确认进入 v2 backlog）→ `Resolved`（v2 修复 PR 合并）
3. **不立即修复原则**：MVP / Phase 1 范围内不阻断交付的问题，记录后不修；阻断性问题不进入本台账，直接开 PR
4. **引用闭环**：每条 TD 必须引用发现来源（验证报告 / PR / ADR）；修复后必须引用修复 PR
