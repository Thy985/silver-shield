# Unit Test（单元测试）

- 类别：[03-Test-Patterns](../README.md)
- 阶段：二

## 定位

验证**单模块正确性**：输入 A → 输出 B。最底层、最快、最多。

## 银龄盾实践

- 每层产出**结构化、可断言**的中间产物 → 每模块单测：
  - `VisitorTracker` → `VisitorEvent`（事实事件，可断言）
  - `RuleEngine` → `WarningEvent`（风险事件，可断言）
  - `DecisionEngine` → `ActionCommand`（动作指令，可断言）
- 状态机（`DemoStateStore` 翻转）单测：合法翻转 / 非法翻转拒绝 / 幂等。

## 注意

- 单测全绿 **≠ 系统正确**。它只证明「零件对」。
- 用 Mock 自检接口可以，但**不能用 Mock 证明系统成立**（见 e2e-test）。
- 单测必须随「每阶段可验证资产」纪律产出（见 00-Overview/SilverShield-Lessons 原则 11）。

## 检查清单

- [ ] 每个模块有单测，断言其结构化产物
- [ ] 状态机：合法 / 非法 / 幂等全覆盖
- [ ] 边界值（空 / 极值 / 类型错）有断言
- [ ] 不依赖真实模型 / 真实网络（用 fake）
