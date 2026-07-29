# ADR Template · Runtime Lifecycle（运行时生命周期）

> 当系统要**长时运行 / 可重复演示 / 有跨帧状态**时写这个 ADR。

## 模板（复制填写）

```markdown
# ADR-NNNN: Demo/系统 Runtime Lifecycle
- 状态：Proposed
- 日期：
- 作者：

## 1. 背景与动机
（演示稳定性暴露的同根症状表：
 | 症状 | 根因 |
 | 循环后状态污染 | 跨循环累积从不重置 |
 | 切换源状态残留 | 切换未清空跨帧聚合 |
 | 晚连无历史 | 状态只在客户端、无快照 |
 | 重启才恢复 | 无 Reset |
 本质：缺少 Lifecycle Management，系统是"一次性脚本"非"产品入口"。）

## 2. 目标与非目标
- 目标：任意时间打开都能看到"正在运行中的系统"
- 非目标：完整 Session 状态机 / Pause·Resume（留 P2，避免做成小型平台）

## 3. 核心设计变更（单一事实来源）
（把聚合状态提升为服务端权威，客户端退化为快照渲染器+增量消费者。
 层级与真实产品同构：感知→状态→事件→决策→角色消费）

## 4. 能力清单与优先级
| 能力 | 优先级 | 说明 |
| 聚合状态（SSOT） | P0 | 未来产品数据层雏形 |
| 首连 Snapshot | P0 | 晚连有历史 |
| Reset | P0 | 演示确定性 |
| 状态面板 | P0 | "系统感" |
| 源抽象 | P1 | 证明不绑定某输入 |
| 完整状态机 | P2 | 留待后续 |

## 5. WS 协议增量（向后兼容）
（frame 加运行时字段；snapshot 仅新连接；reset 复用切换通道）

## 6. 测试
（聚合状态 ingest/clear/snapshot；reset 清空；晚连恢复）

## 7. 验收口径
（打开→状态面板 RUNNING；循环 N 轮风险稳定；切换干净边界；晚连有历史；reset ≤30s 恢复；多次演示稳定）
```

## 银龄盾实例（参照）

- ADR-0016 Demo Runtime Lifecycle（真实版本见 `docs/ADR/0016-p0-11-3-5-demo-runtime-lifecycle.md`）。
- 关键纪律：**v1 只做运行时系统最小可信核心**，不做完整状态机（避免把阶段做成平台）。
- 关联模式：[../../02-Code-Patterns/lifecycle-management](../02-Code-Patterns/lifecycle-management/pattern.md) · [../../05-Demo-Engineering/lifecycle](../../05-Demo-Engineering/lifecycle/pattern.md) · [../../05-Demo-Engineering/snapshot-recovery](../../05-Demo-Engineering/snapshot-recovery/pattern.md)
