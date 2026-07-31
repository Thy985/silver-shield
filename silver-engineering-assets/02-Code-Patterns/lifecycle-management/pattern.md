# Pattern · Lifecycle Management（生命周期管理）

> 解决：**状态污染 / 多次运行 / reset / 会话隔离**。

- 来源：Silver Shield `gateway.py`（`DemoGateway` 的 assemble/run_loop/stop/close、`_rebuild_pipeline`、`switch_source`、`reset_demo`）
- 类别：[02-Code-Patterns](../README.md)
- 阶段：一（最高价值代码模式）

---

## 问题

Demo / 实时 AI 系统常被写成「一次性脚本」：跑完即弃，状态散落各处。一旦要**重复运行**（循环播放 / 切换视频 / 比赛换组重跑），就会出现：
- 循环后状态饱和，warning 不再产生（状态污染）；
- 切换视频源，旧数据串场；
- 必须重启才能恢复干净状态；
- 多次演示稳定性不足。

根因：**缺少 Lifecycle Management**。

---

## 原始方案

```
启动 → while True: 推理 → 推送 → 结束（进程退出才重置）
```

跨帧状态（追踪 / 窗口 / 决策）只在对象构造期初始化，**从不重置**。

---

## 最终方案（模式）

把运行时建模为显式生命周期，并分离「**重建状态组件**」与「**重载模型**」：

```
assemble()      # 装配 pipeline + 读帧 + 加载模型（昂贵，只做一次）
   ↓
run_loop()      # 帧循环（可停 / 可重建状态 / 可切换源）
   ↓
stop()          # 停循环（保留装配）
close()         # 释放资源
```

**关键方法 `_rebuild_pipeline()`**：复用已加载的模型（detector 实例），但**清空跨帧累积状态**（追踪 / 窗口 / 规则计数 / 决策），使每次分析从干净状态开始。循环重放与切换源都调它。

**reset**：`switch_source(同场景)` → 停循环 → `_rebuild_pipeline` → 清空状态存储 + 聚合 → 重置帧索引 / 循环计数 → 重开循环。广播 `reset` 事件，前端据此清空本地累积。

---

## 为什么这样设计

- **模型只加载一次**：`_rebuild_pipeline` 复用 detector，避免每次 reset 重载 YOLO 权重（秒级→毫秒级）。
- **状态与模型解耦**：清状态不碰模型，Reset 快且安全。
- **确定性复现**：循环 / 切换 / 重置后，风险能重新稳定触发（演示可重复）。

---

## 适用范围

所有**长时运行 / 可重复演示 / 有跨帧状态**的 AI 系统：实时视觉、Agent 事件流、IoT 流处理、仿真回放。

---

## 相关资产

- 代码：[session.py](session.py)（抽取骨架）
- 测试：[example_test.py](example_test.py)
- 状态聚合：[../cross-frame-state-aggregation/pattern.md](../cross-frame-state-aggregation/pattern.md)
- ADR：[../../08-ADR-Templates/runtime-lifecycle/pattern.md](../../08-ADR-Templates/runtime-lifecycle/pattern.md)
- Demo 工程：[../../05-Demo-Engineering/lifecycle/pattern.md](../../05-Demo-Engineering/lifecycle/pattern.md)
