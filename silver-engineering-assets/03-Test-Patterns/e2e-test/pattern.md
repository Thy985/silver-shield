# Pattern · E2E Verification Template（端到端验证模板）

> 银龄盾最大价值之一的测试资产：**真实链路闭环验证**。

- 来源：Silver Shield `scripts/e2e_validate_demo.py`（12/12 断言通过）
- 类别：[03-Test-Patterns](../README.md)
- 阶段：一（最高价值测试资产）

---

## 问题

复杂 AI 系统（多组件 / 有状态 / 实时流）常被「单测全绿」误导为「系统正常」。
但单测只证明零件对；mock 只能证明接口对齐，**证明不了闭环成立**。

---

## 原始方案

```
pytest 全绿 ≈ 系统正常
```

功能齐全、模块正常，但整个系统的**运行状态**不正确——典型「功能正确 ≠ 系统正确」。

---

## 最终方案（模板）

任何 AI 系统，交付前**至少**验证以下真实链路：

```
真实输入        （真实场景数据，非硬编码样例 / Mock）
   ↓
真实模型        （真实检测 / 推理，非桩）
   ↓
真实服务        （真实网关 / 业务装配，非 mock server）
   ↓
真实协议        （真实 WS / MQTT / HTTP，非假协议）
   ↓
真实用户动作    （真实点击 / 提交，非脚本直调内部函数）
   ↓
真实反馈闭环    （状态真的回写、广播、被消费）
```

**银龄盾实证流程**（见 [example/e2e_validate_demo.py](example/e2e_validate_demo.py)）：

1. `create_app()` 真实 FastAPI 网关（assemble → 加载 YOLO → run_loop 真实视频帧）
2. `TestClient` + 真实 `WebSocket` 连接（经 httpx 驱动，**非 mock**）
3. 断言 WS 首连 `snapshot`（晚连恢复）
4. 收集帧，直到断言核心三件套：**HIGH 风险 + 家属命令 + 社区任务**
5. 断言 `warning_id` 贯通三视图（命令引用的 warning 必须是真实出现过的）
6. **上行回写闭环**：`ws.send(action)` → 断言 `state_update` 广播状态翻转（family_handled → community_done）

---

## 为什么这样设计

- **真实组装 + 真实协议**才能暴露集成级缺陷（单测抓不到）。
- 一次性暴露了 3 个集成级缺陷：功能未进运行链路 / fps 快进破坏规则 / 首见状态覆盖。
- 不依赖浏览器：验证「网关 → WS → 视图数据」协议链路，DOM 渲染由独立单测覆盖。

---

## E2E 检查清单（逐项打勾）

- [ ] 真实输入能跑通（不靠 Mock 证明系统成立）
- [ ] 真实模型产出真实事实
- [ ] 真实服务组装无误（无 null 配置 / 无漏接规则）
- [ ] 真实协议收发无误（WS / MQTT 帧结构正确）
- [ ] 真实用户动作触发真实状态变更
- [ ] 反馈闭环端到端打通（状态回写 → 广播 → 消费一致）
- [ ] **运行态验证**：长时间 + 多轮 + 切换 + 晚连 + 重置，均正确

---

## 相关资产

- 范例：[example/e2e_validate_demo.py](example/e2e_validate_demo.py)（银龄盾真实脚本）
- 契约测试：[../contract-test/pattern.md](../contract-test/pattern.md)
- 失败案例：[../../09-Failure-Cases/README.md](../../09-Failure-Cases/README.md)（E2E 暴露的 3 个缺陷）
