# 03 · Test Patterns（测试模式）

> 银龄盾最大的隐藏价值其实不是 YOLO，而是**测试体系**。
> 你已验证：「模块正确」不等于「系统正确」。

## 验证金字塔

```
                  E2E
        真实数据 · 真实链路 · 真实闭环
                  ↑
           Integration Test   模块组合测试
                  ↑
            Contract Test     接口契约测试（攻击性边界守护）
                  ↑
             Unit Test        单模块测试
```

## 本目录资产

| 资产 | 阶段 | 说明 |
|------|------|------|
| [e2e-test](e2e-test/pattern.md) | 一 | **真实链路闭环验证**（最高价值） |
| [contract-test](contract-test/pattern.md) | 一 | 攻击性边界守护（冻结契约） |
| [unit-test](unit-test/README.md) | 二 | 单模块测试 |
| [integration-test](integration-test/README.md) | 二 | 模块组合测试 |

## 核心认知

- 单测全绿只证明「每个零件对」。
- 集成测试证明「零件拼起来能跑」。
- 契约测试证明「边界没被碰坏」。
- **E2E 证明「系统真的成立」**——唯一能验证闭环的一层。
- 运行态验证（长时间 / 多轮 / 切换 / 晚连 / 重置）是 AI 项目最易遗漏的一层。

> 银龄盾实证：真实 E2E 一次暴露 3 个集成级缺陷，单测一个都没抓到。
> 详见 [09-Failure-Cases](../09-Failure-Cases/README.md)。
