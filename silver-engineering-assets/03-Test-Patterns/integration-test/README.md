# Integration Test（集成测试）

- 类别：[03-Test-Patterns](../README.md)
- 阶段：二

## 定位

验证**模块组合正确性**：零件拼起来能跑。介于单测与 E2E 之间。

## 银龄盾实践

- 装配测试：`PerceptionPipeline.from_settings()` 一键构建 7 层，断言链路跑通。
- 合约层集成：冻结契约消费方（silver_demo）经白名单装配，断言不反向依赖核心。
- 帧循环集成（torch-free 子集）：用 fake 帧源驱动 `run_loop`，断言状态累积正确。

## 与 E2E 的区别

| 维度 | Integration | E2E |
|------|-------------|-----|
| 模型 | 可用 fake / 轻量 | **真实** |
| 协议 | 可直调内部 | **真实 WS / MQTT** |
| 目标 | 模块拼装正确 | **闭环成立** |

## 检查清单

- [ ] 装配入口（from_settings）产出完整链路
- [ ] 相邻两层组合行为正确
- [ ] 冻结边界消费方装配合规
- [ ] 不依赖真实重模型（CI 用 torch-free 子集）
