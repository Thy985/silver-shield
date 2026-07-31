# Pattern · Demo Lifecycle（Demo 生命周期）

> 把 Demo 从「一次性演示脚本」升级为「可重复运行的产品入口」。

- 来源：Silver Shield ADR-0016（Demo Runtime Lifecycle）
- 类别：[05-Demo-Engineering](../README.md)
- 阶段：二

---

## 状态机

```
CREATED
   ↓
LOADED        （加载模型 / 配置 / 场景）
   ↓
RUNNING       （真实数据流入，实时推理）
   ↓
PAUSED        （暂停推理，保留状态）
   ↓
RESETTING     （清空状态，回到初始，不重载模型）
   ↓
STOPPED       （释放资源）
```

**最小可信核心（v1）**：`RUNNING` + 可 `reset` 的聚合状态即可表达「系统正在运行」。
完整 `SessionStatus`（含 PAUSED / STOPPED 全套转移）留 P2——Demo 阶段收益低，避免把本阶段做成小型平台。

---

## 关键能力（按优先级）

| 能力 | 优先级 | 说明 |
|------|--------|------|
| 服务端聚合状态（单一事实来源） | P0 | 未来产品数据层雏形 |
| 首连 Snapshot 恢复 | P0 | 晚连有历史 |
| Reset（POST /demo/reset） | P0 | 演示确定性，换组 ≤30s 恢复 |
| 运行状态面板 | P0 | 评委「系统感」 |
| 轻量 Source 抽象 | P1 | 证明 Demo 不绑定 MP4 |
| 完整 Session 状态机 / Pause·Resume | P2 | 留待后续 |

---

## 完成标准

> **任意时间打开 Demo，都能看到一个正在运行中的风险感知系统。**

而非「支持暂停 / 切换 / 停止」这类功能清单。

---

## 相关资产

- 代码模式：[../../02-Code-Patterns/lifecycle-management/pattern.md](../../02-Code-Patterns/lifecycle-management/pattern.md)
- 快照：[snapshot-recovery](snapshot-recovery/pattern.md)
- ADR：[../../08-ADR-Templates/runtime-lifecycle/pattern.md](../../08-ADR-Templates/runtime-lifecycle/pattern.md)
