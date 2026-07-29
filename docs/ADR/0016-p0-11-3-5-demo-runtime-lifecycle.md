# ADR-0016 · P0-11.3.5 Demo Runtime Lifecycle

- **状态**：Approved（范围已收敛 · v1 聚焦「运行时系统最小可信核心」，待实现）
- **日期**：2026-07-21
- **作者**：Owner 决策 + AI 起草
- **关联**：ADR-0015（P0-11 Demo 架构与冻结边界）；PR #44（帧源抽象，已合 main）、#45（Dashboard 状态层，已合 main）、#46（视频输入适配，已合 main）、#47（环境契约，已合 main）

---

## 1. 背景与动机

P0-11.1–P0-11.4 已完成「数据入口 → 网关 → 可视化 → 状态层 → 视频输入」全链。但演示稳定性暴露出一组**同根症状**：

| 症状 | 根因 |
| --- | --- |
| 循环播放后 pipeline 状态污染（区②③④变空白） | `PerceptionPipeline` 跨循环累积、从不重置 |
| 切换视频源状态残留（旧视频数据串场） | 切换未清空跨帧聚合状态 |
| 浏览器晚连接看不到历史状态 | 聚合状态只在客户端、且逐帧覆盖，无服务端快照 |
| Demo 重启才能恢复干净状态 | 无 Reset 能力 |
| 多次演示稳定性不足 | 无统一生命周期模型，状态散落各处 |

**本质**：Demo 缺少 **Lifecycle Management（生命周期管理）**。当前 Demo 是「一次性演示脚本」，而非「可重复运行的产品入口」。

> 核心要证明的不是「上传视频→AI 告诉你危险」（那很像调 GPT API），而是：
> **摄像头持续观察 → 建立访客轨迹 → 理解行为变化 → 风险逐渐升级 → 解释原因 → 通知不同角色 → 形成闭环**——
> 即银龄盾是一个**持续运行的风险感知系统**。

## 2. 目标与非目标

**目标（v1 完成标准）**：让 Demo 从「一次性演示脚本」升级为「可重复运行的产品入口」——
**任意时间打开 Demo，都能看到一个正在运行中的风险感知系统**。具体解决三个真实问题：
① 演示重复运行稳定（循环/切换/重置后确定性重现）；② 新用户连接能看到当前状态（晚连有历史）；
③ 视频源切换有干净边界（旧状态不串场）。

**非目标（v1 不做）**：
- 完整 Session 状态机（CREATED/LOADED/PAUSED/RESETTING/STOPPED 全套转移）→ 留 P2。
- `Pause` / `Resume`（隐藏复杂度高，Demo 收益低，见 §10）→ 留 P2。
- 真实家属 App / 社区 Web → v1 之后以 **三角色视角**（单 Dashboard 内角色切换）模拟，见 §7。
- 模型准确率优化。
- 真实 RTSP / EZVIZ 设备协议接入（P0-12）；v1 仅做轻量 `Source` 抽象验证「Demo 不绑定 MP4」。

## 3. 推荐路线（Owner 重排 + 范围收敛）

```
现在 → (#45/#46/#47 已合 main) → P0-11.3.5 生命周期(收敛v1) → 三角色视角 → P0-11.5 演示剧本 → P0-12 真设备接入
```

P0-11.3.5 优先级最高且**先于三角色视角**：角色视角建立在稳定状态之上，状态不稳则把问题放大。
**v1 范围收敛**：只做「运行时系统最小可信核心」（聚合状态 + 快照 + 重置 + 状态面板 + 轻量源抽象），
不做完整 Session 状态机与 Pause/Resume（避免把本阶段做成小型平台）。

## 4. 核心设计变更（单一事实来源）

现状：`warningMap` / `behaviorEvents` / `commandMap` **仅存在于 Dashboard 客户端 JS**（由每帧 `active_warnings` / `routed_commands` 累积），服务端不持有。这是「晚连无历史」「无服务端快照」的根因。

**决策**：把聚合状态提升为**服务端权威状态** `DemoAggregateState`，客户端退化为「快照渲染器 + 增量消费者」。这与用户强调的「系统」叙事一致（服务端持续运行、随时可查），也是四个 P0 能力（聚合状态 / 快照 / 重置 / 状态面板）的共同基础。

**与产品架构演进的同构性**：本次升级使 Demo 的层结构与真实 AI 产品一致——

```
感知层(摄像头/YOLO) → 状态层(DemoAggregateState) → 事件层(behavior/warning) → 决策层(command) → 角色消费层(Dashboard/家属/社区)
```

这正是 ADR-0014「冻结核心能力」的价值兑现：若没有冻结，做 Dashboard 很容易滑向
「前端要字段 → 改模型 → 加字段 → 接口污染」；现在反过来是「冻结能力 → 构建消费者 → 验证产品价值」的工业研发路线。
AggregateState 因此**不只是 Demo 功能，而是未来产品的数据层雏形**（家属端 App / 社区 Web / 手机推送的第一个服务端状态源）。

```
服务端 DemoAggregateState (权威)
   ├─ warnings:   dict[warning_id, WarningView]   # 镜像客户端 warningMap（保活/终态移除/prune）
   ├─ behaviors:  list[BehaviorEvent]             # 镜像 behaviorEvents（跨帧去重里程碑）
   ├─ commands:   dict[warning_id, dict[type, CommandView]]  # 镜像 commandMap
   ├─ frame_index / loop_count / last_warning / session_status / started_at
   ↓ 每帧 ingest(active_warnings, routed_commands, perception_events, warnings) 更新
   ↓ WS 下行：每帧 frame 消息（含 status/loop/last_warning）+ 新连接先发 snapshot
客户端
   └─ 接收 snapshot 恢复本地 maps；接收 frame 增量更新；纯渲染，不再自管累积逻辑
```

> 注：行为里程碑（behaviorEvents）的派生逻辑当前在客户端 `ingestBehavior()`。提升到服务端即在 `DemoAggregateState.ingest` 内复用同一去重规则（`enter|vid` / `pe|vid|type|repeat` / `warn|wid`），避免双份逻辑。若实现期认为成本过高，可降级为「服务端只快照 warnings+commands，behaviors 晚连者从空重建」——但推荐完整提升以强化「系统」叙事。

## 5. 能力清单与优先级（v1 范围收敛）

| 能力 | 优先级 | 说明 |
| --- | --- | --- |
| 服务端 DemoAggregateState（单一事实来源） | **P0** | 最重要；同时是未来产品数据层雏形 |
| 首次连接 Snapshot 恢复 | **P0** | AggregateState 的消费者；解决「晚连无历史」 |
| Reset 生命周期（POST /demo/reset） | **P0** | 演示确定性；换组 ≤30s 恢复干净状态 |
| 运行状态面板（renderStatus） | **P0** | 评委「系统感」；证明非播放网页 |
| 轻量 Source 抽象（load / iterator） | **P1** | 证明 Demo 不绑定 MP4；不做 RTSP/EZVIZ 复杂桩 |
| 完整 Session 状态机 / Pause·Resume | **P2** | 本阶段不做；隐藏复杂度高、Demo 收益低 |

> v1 = 4 个 P0 能力；P1 不阻塞实现；P2 留待后续阶段。

### 能力 1（P0）· 服务端 DemoAggregateState（单一事实来源）

**本 ADR 最关键的能力**。把聚合状态提升为**服务端权威状态**，客户端退化为「快照渲染器 + 增量消费者」。

- **新增** `src/silver_demo/state.py`（或并入 `session.py`）：
  ```python
  class DemoAggregateState:
      warnings: dict[str, WarningView]                 # 镜像 warningMap（保活/终态移除/prune）
      behaviors: list[BehaviorEvent]                   # 镜像 behaviorEvents（跨帧去重里程碑，完整上移服务端）
      commands: dict[str, dict[str, CommandView]]      # 镜像 commandMap
      frame_index: int = 0
      loop_count: int = 0
      session_status: str = "RUNNING"
      started_at: float = 0.0
      last_warning: WarningView | None = None
      scenario: str = ""
      source: str = ""
      source_type: str = ""
      n_frames: int = 0

      def ingest(self, active_warnings, routed_commands, perception_events, warnings) -> None:
          # 复用客户端既有去重规则（enter|vid / pe|vid|type|repeat / warn|wid）
          # 更新 warnings/behaviors/commands/frame_index/loop_count/last_warning
      def clear(self) -> None:
          # 仅清聚合，不动 pipeline；供 Reset 调用
      def snapshot(self) -> dict:
          # 供 WS 首连 snapshot 消息
  ```
- 网关 `run_loop` 每帧调 `self.aggregate_state.ingest(...)`；`gateway.py` 持有 `self.aggregate_state`。
- **行为里程碑（behaviors）完整上移服务端**（开放问题 #2 决议）：强化「系统」叙事，避免客户端/服务端双份去重逻辑。
- 这是未来「家属端 App / 社区 Web / 手机推送」的第一个服务端状态源雏形。

### 能力 2（P0）· 首次连接 Snapshot 恢复

- WS 连接建立后（`websocket_endpoint` 内 `hub.connect(ws)` 之后），**仅向该 ws** 发送初始消息：
  ```json
  {"type":"snapshot","warnings":[...],"commands":{...},"behaviors":[...],
   "frame_index":N,"loop_count":L,"session_status":"RUNNING",
   "started_at":"...","last_warning":{...},
   "scenario":"...","source":"...","source_type":"...","n_frames":N}
  ```
- 客户端 `handle()`：识别 `msg.type === "snapshot"` → 恢复 `warningMap` / `behaviorEvents` / `commandMap` / `frame_index` / 时钟显示，再继续接收 `frame` 增量。
- 解决「设备端不断运行、用户随时打开 App」的真实系统语义。

### 能力 3（P0）· Reset 生命周期（POST /demo/reset）

- **新增** `POST /demo/reset`（`gateway.py`）：
  - 调 `self._rebuild_pipeline(scenario)`（复用 YOLO detector，清空追踪/窗口/决策状态）
  - `self._frame_index = 0`、`self.clock` 重置到 `scenario.start_time`、`self.loop_count = 0`
  - `self.store = DemoStateStore()`、`self.aggregate_state.clear()`
  - 广播 `source_switched` 事件（复用既有通道；前端 `resetSession()` 监听该消息已清空本地 maps，见 §6.1）
  - 返回 `{status:"ok", frame_index:0, session_status:"RUNNING", loop_count:0}`
- 比赛价值：上一组选手跑完 → 点 Reset → ≤30 秒内恢复干净状态。

### 能力 4（P0）· 运行状态面板（renderStatus）

- Dashboard **新增**「Demo 状态」卡片，字段：
  - `Source`：`scenario.source` / 视频文件名
  - `Frame`：`frame_index / n_frames`
  - `Loop`：`loop_count`
  - `Pipeline`：`session_status`（v1 恒为 `RUNNING`；完整状态机留 P2）
  - `Last Warning`：`last_warning` 最高严重度 + 原因（来自聚合状态）
  - `Session`：自 `started_at` 起 elapsed `mm:ss`
- 由每帧广播字段 + snapshot 驱动；新增 `renderStatus()`。向评委证明「这是系统，不是播放网页」。

### 能力 5（P1）· 轻量 Source 抽象（不绑定 MP4）

**方向对，但不做完整 `DemoSourceManager` + RTSP/EZVIZ 桩**。v1 仅需证明「Demo 不绑定 MP4」：
```python
class Source:
    def load(self, scenario: ScenarioConfig) -> None: ...
    def __iter__(self) -> Iterator[Frame]: ...
```
- 复用既有 `DemoFrameSource` ABC（#44 已完成，最大价值已具备）。
- 网关 `switch_source` / `assemble` 可改委托轻量 `Source`；**不实现 RTSP/EZVIZ 协议**（P0-12 真设备接入时再填 `start()`/`__iter__`，管理器与网关零改动）。
- P1 不阻塞 P0 实现。

### （P2 延后）完整 Session 状态机 / Pause·Resume

- 完整 `SessionStatus`（CREATED/LOADED/PAUSED/RESETTING/STOPPED 全套转移）→ 留 P2。v1 用简单 `RUNNING` + 可 reset 的聚合状态即可表达「系统正在运行」。
- `Pause`/`Resume` 隐藏复杂度高（暂停点定义、visitor duration 是否计入暂停时长等状态机问题），Demo 阶段收益低 → 留 P2。

## 6. WS 协议增量（向后兼容）

| 消息 | 方向 | 新增字段 / 类型 |
| --- | --- | --- |
| `frame` | 下行 | `session_status`, `loop_count`, `last_warning`（每帧） |
| `snapshot` | 下行（仅新连接） | 完整聚合状态（见 §5 能力 4） |
| `source_switched` | 下行（广播） | **双用途**：切换视频源 **与** Reset（复用 `switch_source(同场景)`）均广播此消息，触发前端 `resetSession()` 清空本地 maps；无需独立 `session_reset` 消息（实现收敛，见 §6.1） |

冻结边界：P0-11.3.5 **不新增任何 `home_perception` 内部 import**，`tests/demo/test_freeze_boundary.py` 仍守白名单。

### 6.1 实现偏差记录：Reset 复用 `source_switched`（非独立 `session_reset`）

ADR 初稿 §5 能力 3 拟新增独立的 `session_reset` 广播消息，前端据以调用 `resetSession()`。
**实现期收敛为复用既有 `source_switched` 通道**，理由：

- Reset 的实现路径是 `POST /demo/reset` → `switch_source(同场景)`（停旧循环 → 重建流水线 → 清空聚合 + store → 重开循环），其**天然**会广播 `source_switched`。
- 前端 `resetSession()` 早已监听 `source_switched`（视频源切换语义一致：新视频 = 新会话 = 清空本地 maps）。Reset 与切换的输入边界完全相同，**无需新增消息类型**即可复用同一处理函数。
- 减少一类 WS 消息 = 减少客户端分支与回归面，符合「运行时系统最小可信核心」的收敛目标。

**结论**：WS 协议不引入 `session_reset`；Reset 经 `source_switched` 完成。`gateway.py` 的 `reset_demo` 端点与 `switch_source` 共用实现与广播，测试 `test_dashboard_lifecycle.py::test_reset_endpoint_clears_aggregate` 已验证该路径清空服务端聚合。

## 7. 三角色视角（本阶段之后的下一阶段，非本 ADR 范围）

**关键术语修正**：不叫「三端闭环」，而叫 **三角色视角（Three-Role View）**。真正产品里是一个风险事件被三个消费者共享：

```
一个风险事件
   ├── AI中心   （当前 5 区域全集）
   ├── 家属端   （聚焦家属任务卡 + 风险人话解释 + 通知状态）
   └── 社区端   （聚焦社区工单 + 风险强度 + 处置状态）
```

Demo 应模拟**角色切换**（顶部 `[AI中心][家属端][社区端]` 切视图），**不是三个独立应用**——否则工程量指数增加。
本 ADR 仅把状态做稳（聚合状态 + 快照 + 重置 + 状态面板），为三角色视角提供稳定数据源；进入该阶段时直接消费 `DemoAggregateState`。

## 8. 落地顺序（范围收敛后）

| Step | 能力 | 内容 | 价值 |
| --- | --- | --- | --- |
| **1** | 聚合状态（P0） | `DemoAggregateState`：服务端持有 warning/command/behavior/runtime status；每帧 `ingest`；frame 广播加 `session_status`/`loop_count`/`last_warning` | 单一事实来源，未来产品数据层雏形 |
| **2** | 快照（P0） | WS 首连 `snapshot`；客户端退化为渲染器，晚连恢复历史 | 修「晚连无历史」 |
| **3** | 重置（P0） | `POST /demo/reset`：清聚合 + pipeline + clock + frame_index，广播 `source_switched`（见 §6.1） | 演示确定性，换组 ≤30s 恢复 |
| **4** | 状态面板（P0） | Dashboard `renderStatus()`：Source/Frame/Loop/Pipeline/Last Warning/Session | 评委「系统感」 |
| **5** | 源抽象（P1） | 轻量 `Source`（load/iterator），复用 `DemoFrameSource`；不做 RTSP/EZVIZ 复杂桩 | 证明 Demo 不绑定 MP4 |

> 顺序说明：Step 1 先于 Step 2（快照消费聚合状态）；Step 2 先于 Step 3（reset 必须能清服务端聚合）；Step 4 可与 Step 1–3 并行（只消费广播字段）。Pause/Resume 完整状态机不在本表（P2）。

**完成标准（非「支持暂停/切换/停止」，而是）**：
> 任意时间打开 Demo，都能看到一个正在运行中的风险感知系统。

## 9. 测试

- `tests/demo/test_aggregate_state.py`：`DemoAggregateState.ingest` 正确 upsert `warnings`/`behaviors`/`commands` 并去重；`clear()` 后全空；`snapshot()` 字段齐全。
- `tests/demo/test_dashboard_reset.py`：`POST /demo/reset` 后 `aggregate_state.warnings` 空、`frame_index==0`、`loop_count==0`、广播 `session_reset`（用 `TestClient` + `monkeypatch` 隔离 YOLO，沿用 `test_gateway_integration.py` 模式）。
- `tests/demo/test_dashboard_snapshot.py`：WS 新连接收到 `snapshot` 含当前 `warnings`；晚连客户端 `warningMap` 被恢复。
- 轻量 `Source` 抽象：`load` CaviarJpg / VideoFile 成功、`__iter__` 产出帧（不强制 RTSP/EZVIZ 桩测试）。
- 回归：现有 `test_dashboard_state_layer.py` / `test_dashboard_video_input.py` / `test_freeze_boundary.py` 不受影响（客户端 `resetSession` 仍被 `source_switched`/`session_reset` 调用）。

## 10. 风险与开放问题

- **最大改动** = 聚合状态上移服务端（能力 1）。缓解：客户端保留渲染逻辑，仅移除自管累积；先 Step 1–2 后 Step 3 降低耦合。
- snapshot 体积：服务端 `prune` 旧 warnings/behaviors（沿用客户端 `pruneWarnings` 上限），避免无限增长。
- 开放问题（已决议）：
  1. **Pause/Resume → P2 延后**：隐藏复杂度高（暂停点定义、visitor duration 是否计入暂停时长等状态机问题），Demo 阶段收益低，不阻塞 v1。
  2. **行为里程碑完整上移服务端（已采纳）**：`behaviors` 随 `warnings`/`commands` 一同在 `DemoAggregateState.ingest` 内派生，避免客户端/服务端双份去重逻辑，强化「系统」叙事。
- 待 Owner 拍板（进入三角色视角前）：角色切换是否复用同一份 `DemoAggregateState` 的视图过滤，还是每角色独立快照？建议前者（单一数据源 + 角色视图投影）。

## 11. 验收口径

**总口径**：任意时间打开 Demo，都能看到一个正在运行中的风险感知系统。

- 打开 Demo → 状态面板显示 `Pipeline: RUNNING` + 实时 `Frame/Loop/Session`。
- 循环播放 N 轮：每轮风险稳定重现（无空白），`loop_count` 递增。
- 切换视频源：旧 `warningMap` 清空，新源从干净状态开始（干净边界）。
- 浏览器在告警已发生后才连接：仍能看到历史 `warningMap`/`behaviorEvents`/`commandMap`（快照恢复）。
- 点 Reset：≤30s 内恢复 `frame_index=0`、`loop_count=0`、无历史告警，可重跑（演示确定性）。
- 多次演示（切换+重播+重置）连续稳定，无需重启网关。
