# ADR-0011: P0-9 ActionLayer 行动层架构 —— 决策的执行与外部通道

- 状态：Accepted
- 日期：2026-07-19
- 决策者：Owner
- 相关：`docs/08_roadmap.md`（P0-9）、`docs/07_event_schema.md`（5 类 PerceptionEvent）、
  `src/home_perception/analysis/warning.py`（P0-8 WarningEvent）、
  `src/home_perception/action/{command,dispatcher,executor,publisher,notifier}.py`（本 ADR）、
  ADR-0001（只产事实）、ADR-0007（事实层 vs 语义层）、ADR-0009（Rule Engine）、
  ADR-0010（WarningEvent 决策层）

## 背景（Context）

P0-8 决策层完成后，银龄盾感知链完全闭环：
```
DetectionResult → VisitorTrack → VisitorEvent (P0-6) → RiskFeature (P0-7a)
            → PerceptionEvent (P0-7b 5 类 + score)
            → WarningEvent (P0-8 决策层)
            → ??  (P0-9 行动层 · 本 ADR)
```

Owner P0-8 review 明确指出 P0-9 是"第一个容易扩大范围的阶段"，要求：
> "P0-9 MVP职责 只做：
> WarningEvent → ActionDispatcher → MQTT/EventBus模拟 → 三端收到事件
> 不要马上：接真实萤石设备 / 做完整APP / 做社区系统"

并强调三大必验证保证：
1. **WarningEvent 是否正确消费**（HIGH → CommunityAction）
2. **不重复执行**（同 warning_id 不能生成两次社区任务）
3. **Action 失败状态**（MQTT unavailable 时 Warning 不能丢）

```
Decision Layer (P0-8)
    ↓ WarningEvent
Action Layer (P0-9 · 本 ADR)            ← 本 ADR
    ↓ ActionCommand
MQTT / SMS / Community (P0-9 后续增强 / v1 真实集成)
```

## 决策（Decision）

### Decision 1: 可替换 Publisher / Notifier Protocol 接口

行动层**唯一**接触外部传输的接口是 `MQTTPublisher` 和 `NotificationAdapter`（都用 `Protocol` 定义）。
MVP 用 `MockPublisher`（写本地 JSONL）+ `MockNotifier`（写内存列表）演示；v1 接真实通道只需
实现这两个 Protocol，不改其他代码。

接口约束：
```python
class MQTTPublisher(Protocol):
    def publish(self, topic: str, payload: dict) -> bool: ...
    # 失败用 bool 而非 raise（try/except 太重）
    # 不做幂等（→ ActionExecutor 责任）
    # 不做重试（→ ActionExecutor 责任）

class NotificationAdapter(Protocol):
    def notify_family(self, contact: FamilyContact, message: str) -> bool: ...
    def notify_community(self, endpoint: str, task: dict) -> bool: ...
```

这样：
- 单元测试无需 mock 任何 IO（直接 assert publisher.published / notifier.family_messages）
- 真实通道（MQTT / 短信 / 社区平台）实现 Protocol 即可替换
- 行动层逻辑与具体通道解耦

### Decision 2: 幂等基于 warning_id（in-memory set）

ActionExecutor 内部维护 `Set[UUID] of dispatched warning_id`。同一 `warning_id` 重复
`execute()` 直接返回已记录的 commands，**不**再次 dispatch 也不再次 publish/notify。

```python
class ActionExecutor:
    def execute(self, warning: WarningEvent) -> List[ActionCommand]:
        if warning.warning_id in self._dispatched:
            return self._get_commands_for_warning(warning.warning_id)
        # ... dispatch + execute + 记录
        self._dispatched.add(warning.warning_id)
        return executed
```

边界：
- 进程内 set（MVP 演示足够；进程重启幂等丢失可接受）
- 失败重试**不**算新 dispatch（同一 warning_id 重试内部完成，外部不感知）
- Owner 反馈确认：MVP 用 in-memory 即可，v2 走 Redis/SQLite

### Decision 3: 失败可重试（max_retries + Warning 状态保护）

`max_retries` 默认 3。语义：最多重试 `max_retries` 次（不含初始 execute），总尝试次数 = 1 + max_retries。

```python
def retry_pending(self) -> List[ActionCommand]:
    for cmd in self._command_index.values():
        if cmd.status != "FAILED":
            continue
        cmd.attempts += 1
        cmd.status = "RETRYING"
        success = self._execute_command(cmd, warning)
        if not success and cmd.attempts >= 1 + self.max_retries:
            cmd.status = "GIVEN_UP"
            self._transition_warning(warning, "REJECTED")  # 关键
        elif success:
            # 检查同 warning 所有 command 是否都 DONE
            if all(c.status == "DONE" for c in self._get_commands_for_warning(...)):
                self._transition_warning(warning, "CONFIRMED")
```

**关键边界**（Owner 强调的"Warning 不能丢"）：
- publisher 失败 → `command.status = FAILED` + `warning.status` 保持 `PENDING`（不丢）
- 错误原因记到 `warning.meta["dispatch_error"]`（可审计）
- 重试耗尽 → `command.status = GIVEN_UP` + `warning.status = REJECTED`
- 重试成功 → `command.status = DONE` + 同 warning 所有 command 都 DONE → `warning.status = CONFIRMED`

### Decision 4: 状态描述决策生命周期，不描述执行结果

继承 P0-8 ADR-0010 Decision 2 + Owner P0-8 review 反馈。

`WarningEvent.status` 5 类（描述决策生命周期）：
- `CREATED`：决策已生成，**未下发**
- `PENDING`：已下发 ActionDispatcher，**等待下游确认**
- `CONFIRMED`：下游已确认收到（MQTT ACK / 家属 ACK / 社区 ACK）
- `RESOLVED`：处理完毕（家属核实 / 社区介入 / 标记误报闭环）
- `REJECTED`：拒绝/撤销（误报、用户主动关闭、重试耗尽）

`ActionCommand.status` 5 类（执行层内部状态，独立于 WarningEvent.status）：
- `PENDING`：已构造命令，等待执行
- `RETRYING`：正在重试
- `DONE`：执行成功
- `FAILED`：执行失败（待重试）
- `GIVEN_UP`：重试耗尽

这两个状态机**完全独立**：
- `WarningEvent.status` 是决策生命周期（决策层语义）
- `ActionCommand.status` 是执行状态（行动层语义）
- 互相不影响各自的字段

### Decision 5: 不直接调真实设备（MVP 收敛）

行动层 MVP 范围（Owner 拍板）：
- ✅ `ActionCommand` 领域对象 + 黑名单
- ✅ `ActionDispatcher` 路由（3 类 action → 3 类 command）
- ✅ `ActionExecutor` 编排（幂等 + 失败重试 + 状态翻转）
- ✅ `MockPublisher` / `MockNotifier`（写本地 JSONL / 内存）
- ❌ **不**接真实萤石设备
- ❌ **不**做完整 App（家属端 / 社区端 UI）
- ❌ **不**做社区系统

后续 v1 路径（不在本 ADR 范围）：
- v1.0：实现真实 `MQTTPublisher`（paho-mqtt）+ 真实 `NotificationAdapter`（短信网关）
- v1.1：接家属 App push（极光 / 友盟）
- v1.2：接社区工单系统 API

## 动机（Rationale）

- **守 ADR-0001 / ADR-0010 边界**：行动层**只**做"按建议动作执行"，**不**做最终判定、
  **不**做内容生成、**不**做内容审核。
- **可测试**：MockPublisher / MockNotifier 让单元测试无需 IO；256 测试全绿（其中 59 新增）。
- **可替换**：Protocol 接口让 v1 接真实通道时**只**改 Publisher / Notifier 实现，不动其他代码。
- **幂等**：warning_id 维度去重，避免重复触发家属通知（短信是收费的，重复触发浪费 + 骚扰）。
- **失败保护**：MQTT 不可用时 Warning 不能丢，重试机制 + 状态保护让链路"长时间掉线不丢数据"。
- **MVP 收敛**：Owner 明确"不要急着接真实设备"，先用 Mock 把骨架跑通，v1 再接真实。

## 后果（Consequences）

- ✅ P0-9 行动层职责清晰：`WarningEvent` → `ActionCommand` → `MockPublisher/MockNotifier`
- ✅ 三大必验证保证落地：
  1. 消费正确：`test_notify_family_executes_notifier_success` / `test_escalate_community_executes_publisher_success`
  2. 幂等：`test_same_warning_id_dispatched_once`（同 warning_id publish 次数=1）
  3. 失败保护：`test_publisher_failure_keeps_warning_pending` / `test_retry_exhausted_marks_rejected`
- ✅ WarningEvent 状态机扩展到 5 类（CREATED→PENDING→CONFIRMED→RESOLVED/REJECTED）
- ✅ ActionCommand 独立状态机（PENDING/RETRYING/DONE/FAILED/GIVEN_UP）
- ✅ MockPublisher 写本地 JSONL / MockNotifier 写内存 → 单元测试零 IO
- ✅ Protocol 接口预留 v1 真实通道替换点
- ✅ 256 测试全绿（之前 197 + 59 新增）；ruff 全绿
- ⚠️ P0-9 范围中等（6 文件 + 59 tests + ADR-0011），需充分单测
- ⚠️ 进程重启幂等丢失（MVP 接受；v2 走 Redis）
- ⚠️ 真实通道（paho-mqtt / 短信网关）需新开 ADR 评审
- 📌 约束后续：WarningEvent.status / ActionCommand.status 字段增删按 ADR-0005 走 schema_version 评审；
  真实 Publisher / Notifier 实现需新开 ADR 评审；幂等存储升级（Redis/SQLite）需新开 ADR 评审。

## 替代方案（Alternatives）

- **行动层直接调真实 MQTT / 短信**：否决。Owner 明确"不要急着接真实设备"，
  收不到真实通道反而把行动层逻辑耦合进去，未来 v1 接真实通道要回头改。
- **per-event 幂等而非 per-warning 幂等**：否决。per-event 需要 EventId 维度，
  per-warning 更直观（一个 Warning 触发一组 ActionCommand，幂等维度是 Warning）。
- **失败即丢（无重试）**：否决。MQTT 临时不可用应能恢复，丢数据会让 RiskTwin 历史不完整。
- **持久化幂等（Redis/SQLite）**：MVP 过度工程。进程重启幂等丢失可接受，
  v2 接中心 RiskTwin 时再持久化。
- **ActionCommand 含"执行结果"字段（sent_at / success）**：否决。执行结果是 ActionExecutor
  内部状态机的事，不是 ActionCommand 字段。WarningEvent.status 也不应含 SENT / DELIVERED
  （Owner P0-8 review 明确：状态描述决策生命周期，不描述执行结果）。
- **WarningEvent 含 `dispatched_at` 字段**：否决。同上，时间戳是执行层日志，
  不污染决策对象。
- **ActionExecutor 直接调多 publisher（不通过 ActionDispatcher）**：否决。ActionDispatcher
  做"WarningEvent → ActionCommand"路由，ActionExecutor 做"幂等 + 重试 + 状态"，职责分离。
- **MQTTPublisher / NotificationAdapter 合并为一个接口**：否决。MQTT 是异步消息队列
  （用于中心上报），Notification 是同步消息发送（用于家属/社区通知），通道不同，接口分开。
