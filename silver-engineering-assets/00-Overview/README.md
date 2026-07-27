# 00 · Overview（总览）

本目录是资产库的入口与背景。

| 文件 | 内容 |
|------|------|
| [README.md](README.md) | 本目录说明 |
| [Asset-Map.md](Asset-Map.md) | 全部资产清单（按类 + 阶段） |
| [SilverShield-Lessons.md](SilverShield-Lessons.md) | 银龄盾踩坑与教训（母项目来路） |

---

## 本资产库从哪来

Silver Shield（银龄盾）是一条完整走通的 AI 系统路径：

```
问题定义 → 事实层 → 规则层 → 动作层 → E2E 验证 → Demo 产品化 → 复盘 → 资产沉淀
```

它最大的价值**不是某段 YOLO 代码**，而是：
- 验证了一套工程范式（事实层优先、冻结边界、E2E 验证、状态一等公民）；
- 趟出了一组真实失败案例（状态污染、时间尺度错配、状态机误设计）；
- 沉淀了 Demo 作为「产品入口」的工程方法。

这些才是值得留给下一个项目的「可复制能力」。

---

## 三类产物各司其职

| 产物 | 沉淀什么 | 例子 |
|------|----------|------|
| 开发手册（docs/PLAYBOOK-*.md） | **认知**：我们验证了什么、踩了什么坑 | 「先事实层再展示层」 |
| 工程资产（本库） | **模式**：可套用的结构 / 代码骨架 / 模板 | `lifecycle-management/` |
| 代码仓库（src/） | **实现**：具体业务代码 | `gateway.py` |

三者同源互补：手册讲「为什么」，资产给「怎么做」，仓库是「真实样本」。

---

## 如何使用本目录

- 想了解全局 → 读 [Asset-Map.md](Asset-Map.md)
- 想看母项目来路 → 读 [SilverShield-Lessons.md](SilverShield-Lessons.md)
- 想直接使用 → 从根 [README.md](../README.md) 或 [HANDBOOK.md](../HANDBOOK.md) 进入
