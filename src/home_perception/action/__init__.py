"""行动层（P0-9 · Action Layer）。

> **P0-9 = 行动层。** 消费 `WarningEvent`（P0-8 决策层），按 `ActionDispatcher`
> 路由 → `ActionCommand` → 通过 `MQTTPublisher` / `NotificationAdapter` 执行。

**链路终点**（P0-9 完成后银龄盾 MVP 闭环）：
```
DetectionResult (P0-3)
    ↓
VisitorTrack (P0-5)
    ↓
VisitorEvent (P0-6)
    ↓
RiskFeature (P0-7a)
    ↓
PerceptionEvent (P0-7b)
    ↓
WarningEvent (P0-8)
    ↓
ActionCommand (P0-9)        ← 本模块
    ↓
MQTT / SMS / 社区工单
```

**核心保证（ADR-0011）**：
1. **消费正确**：`WarningEvent.recommended_action` → 正确 ActionCommand
2. **幂等**：同 `warning_id` 重复 execute 只产生一个下游任务
3. **失败保护**：`publisher` 失败时 Warning 保持 PENDING 不丢，重试 N 次后标 REJECTED

**边界**：行动层**不**做最终判定（沿用 WarningEvent 黑名单），**不**调真实设备
（MVP 用 MockPublisher / MockNotifier，P1 接真实通道）。

详细设计见 `docs/ADR/0011-action-layer-architecture.md`。
"""
from __future__ import annotations

from .command import (
    COMMAND_STATUSES,
    COMMAND_TYPES,
    FORBIDDEN_ACTION_FIELDS,
    WARNING_TRANSITIONS,
    ActionCommand,
    assert_transition_warning,
    can_transition_warning,
)
from .dispatcher import ActionDispatcher, DispatcherConfig
from .executor import ActionExecutor
from .notifier import FamilyContact, MockNotifier, NotificationAdapter
from .publisher import MQTTPublisher, MockPublisher


__all__ = [
    # 核心
    "ActionCommand",
    "ActionDispatcher",
    "ActionExecutor",
    "DispatcherConfig",
    # 接口
    "MQTTPublisher",
    "NotificationAdapter",
    # Mock
    "MockPublisher",
    "MockNotifier",
    "FamilyContact",
    # 状态
    "COMMAND_STATUSES",
    "COMMAND_TYPES",
    "WARNING_TRANSITIONS",
    "FORBIDDEN_ACTION_FIELDS",
    "can_transition_warning",
    "assert_transition_warning",
]
