# Pattern · Scenario Management（场景管理）

> 解决：**演示必须确定性触发，而不是碰运气触发。**

- 来源：Silver Shield P0-11.5a（稳定 HIGH 闭环）+ Scenario 配置
- 类别：[05-Demo-Engineering](../README.md)
- 阶段：二

---

## 问题

真实数据有随机性，每次演示故事都不一样，无法保证「核心高风险闭环」必然出现。
靠运气演示 = 故事讲不通 = 评审不买账。

---

## 方案

把演示输入与关键参数固化为 **Scenario（场景剧本）**，可配置、可复现。

```
Scenario: 夜间可疑蹲守
   input: cctv_night.mp4
   overrides:
     repeat_visit_count: 2     ← 过拟合点，须记录
     family_contact: enabled
```

**演示确定性三要素**
1. **固定场景**：真实视频 + 固定时间尺度，故事可重放。
2. **场景级阈值覆盖**：`rule_overrides` 局部调参（如 `repeat_visit_count: 3→2`），不影响全局默认。
3. **过拟合点记录**：Demo 阶段允许对故事过拟合，但**必须明确记录过拟合点**，为后续泛化预留出口。

**研发顺序**：单场景成立 → 多场景成立 → 泛化成立。不得跳过前两步直接追求泛化。

---

## 为什么这样设计

- 核心故事能确定性复现，演示才成立（一个能稳定复现的 HIGH，比十个漂亮但空洞的页面值钱）。
- 过拟合点 = 未来的泛化工作项；记录它，资产可演进。

---

## 相关资产

- 生命周期：[lifecycle](lifecycle/pattern.md)
- 失败案例：[../../09-Failure-Cases/high-not-appearing.md](../../09-Failure-Cases/high-not-appearing.md)（时间尺度错配导致 HIGH 不出现）
- 产品验证：[../../06-Frontend-Patterns/README.md](../../06-Frontend-Patterns/README.md)（核心故事闭环）
