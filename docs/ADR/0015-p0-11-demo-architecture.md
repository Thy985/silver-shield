# ADR-0015: P0-11 MVP Demo 架构（三端风险闭环展示层）

- **状态**：Proposed（Owner 评审中 · v3：三端风险闭环展示层 + 单页 HTML 观察窗口）
- **日期**：2026-07-20
- **决策者**：Owner
- **作者**：AI（design pass，v3）
- **相关**：ADR-0014（三级冻结）、P0-10 Runtime Assembly、P0-10.5.x 治理、v0.1.0-mvp-rc tag、PR #32
- **范围**：ROADMAP P0-11（MVP Demo v0.1）
- **术语修正（2026-07-22 · 见 ADR-0017）**：本 ADR 中「三端 / 三端闭环」一律指「单 Dashboard 内的多角色逻辑拆分（AI 风险中心 / 家属端 / 社区端）」，**非三个独立产品**；阶段名统一为「**多角色协同闭环模拟（Role-based Workflow Demo）**」，对应 ROADMAP P0-11.4。

---

## 1. 背景与动机（Why）

P0-3 ~ P0-10 已证明**算法链路成立**：

```
摄像头输入 → 感知 → 访客轨迹 → 事件 → 特征 → 规则 → 风险决策 → 行动命令
```

P0-10.5 已证明**架构纪律成立**（冻结契约 + Contract Test + 仓库卫生 + RC tag）。

**P0-11 的定位不是"再造能力"，而是"翻译能力"**：把已冻结的 AI 链路翻译成评委/协作者**可理解、可操作、可感受到的价值闭环**——
从"看到人"升级到"理解访问行为"，再升级到"可信的风险发现 → 解释 → 干预 → 闭环"。

> **关键判断**：v0.1.0-mvp-rc 冻结架构的最大价值，现在才开始体现。
> 很多比赛项目走的是**反路**——先做页面 → 发现数据不够 → 硬改模型 → 架构崩；
> 本项目的路线是：
> ```
> 事实层 → 事件层 → 特征层 → 规则层 → 决策层 → 行动层 → 冻结 → 展示层
> ```
> 这接近真实工业研发流程。所以 P0-11 的原则是：
> **不要让 Demo 反过来污染系统。Demo 是消费者，不是架构参与者。**

> **HTML 可视化 ≠ 产品前端系统**。P0-11 的目标不是开发完整 Vue 三端应用，而是给已冻结的 AI 链路增加
> 一个**观察窗口（observation window）**。对于比赛展示，一个高质量单页 HTML 往往比复杂前端更有效——
> 核心优势是分层架构 / 数据契约 / 风险解释 / 闭环设计，前端复杂度不增加评分。

> 技术护城河当前是 **架构完整性 + 工程可信度**，不是模型 SOTA。
> P0-11 必须**只消费冻结契约、零改动 P0-10 链路**——这正是对 ADR-0014 价值的外部验证。

---

## 2. 决策（Decision）

### 2.1 引入独立的展示层包 `src/silver_demo/`（与冻结包物理隔离）

- 与冻结包 `home_perception` **物理隔离**（different package），保证冻结包不引入任何 Web 依赖。
- `silver_demo` 的 **Runtime Core** 仅经 §2.1.1 白名单消费 `home_perception` 运行时**输出契约**（视为"消费冻结契约"的合法边界）；**Host / Composition Root（`silver_demo.gateway`）** 在此基础上额外允许依赖 §2.1.1 定义的 Presentation Layer（见该节「分层依赖契约」）。

| 消费目标（Runtime Core 白名单） | 来源（白名单） | 用途 |
| --- | --- | --- |
| `PerceptionPipeline` | `home_perception.runtime.pipeline` | 唯一装配入口 |
| `DemoClock` | `home_perception.runtime.pipeline` | 确定性时序源（驱动场景时间） |
| `read_caviar_frames` | `home_perception.runtime.config` | CAVIAR 帧读取（工程验证帧源；CAVIAR 公开序列本地副本） |
| `FrameResult` | `home_perception.runtime.pipeline` | 每帧结果（消费出口） |
| `WarningEvent` | `home_perception.analysis.warning` | AI 中心 / 分流依据（只读） |
| `ActionCommand` | `home_perception.action.command` | 家属端 / 社区端（只读） |
| `Settings` | `home_perception.core.config` | 装配配置 |

> **帧源消费边界（P0-11.3 调整）**：`silver_demo` **不** import 冻结包内的 `FrameSource`（`home_perception.ingestion.frame_source`，属内部模块，仍在冻结测试禁止列表）。
> 改为在 `silver_demo/sources.py` 内定义**结构一致的 `DemoFrameSource` 抽象**，并提供两个消费者侧实现：
> `CaviarJpgFrameSource`（包裹 `read_caviar_frames`，工程验证）+ `VideoFileFrameSource`（真实 MP4，产品展示）。
> 两者均产出 `(timestamp, frame)` 流，网关按场景配置（`source_type`）选择——**Dashboard / Pipeline / WarningEvent 零改动**。
> 这正是 ADR-0014「实现可替换」在消费者侧的体现：消费者可自由提供自己的输入源，无需耦合冻结包内部。
> 冻结包内的 `CaviarFrameSource` 仍作为 FrameSource 契约的参考实现保留，用于工程回归。

- **严禁**：`silver_demo`（Runtime Core 与 Host 均适用）直接或间接 import `rule_engine` / `decision_engine` /
  `decision_policy` / `action.executor` / `action.dispatcher` / `action.notifier` / `action.publisher`
  （即不得穿透 7 层内部）。网关只通过 `PerceptionPipeline.from_settings(...)` 拿到的对象驱动，绝不自行构造/调用层内组件。
  **唯一例外**：`silver_demo.gateway`（仅此一层）允许 import `home_perception.visualizer.viewer`，详见 §2.1.1。

### 2.1.1 分层依赖契约（ADR-0036 收敛后升级：从 import 白名单到 layer dependency contract）

原 §2.1 以「模块级 import 白名单」限定 `silver_demo ↔ home_perception` 的契约面；随着 ADR-0036 统一
Case Viewer 落地，边界升级为**分层依赖契约（layer dependency contract）**，并区分 `silver_demo`
内部的 **Runtime Core** 与 **Host / Composition Root** 两种角色：

| 层 | 范围 | 对 Presentation Layer（`home_perception.visualizer`）的依赖 |
| --- | --- | --- |
| **Runtime Core** | `silver_demo` 内除 `gateway` 外的所有子模块（`config` / `bridge` / `state` / `scenarios` / `sources` / `ws` / `runtime`） | ❌ **禁止** import `visualizer`（含 `viewer` / `render` / `loader` / `schema`） |
| **Host / Composition Root** | `silver_demo.gateway`（FastAPI 装配 + 帧循环 + 路由 + 静态托管） | ✅ **允许** import `home_perception.visualizer.viewer`（仅 `viewer` 子包；用于将 `FrameResult` / `AudioEvidence` 投影为 `EvidenceProjection` 并渲染统一 Case Viewer） |

**依赖方向单一（不可环）**：

```
Runtime Core ──(Output Contract: FrameResult / AudioEvidence / events)──► Presentation Adapter (viewer/)
   │                                                                               │
   │  Host / Composition Root (gateway) 是唯一允许「反向依赖」Presentation Layer 的层  │
   └──────────────────────► home_perception.visualizer.viewer ◄─────────────────────┘
                                            │
                                            ▼
                                      EvidenceProjection ──► Case Viewer
```

- **Presentation Layer 必须单向**：`home_perception.visualizer`（含 `viewer/` / `render/` / `loader/` / `schema`）
  **不得 import `silver_demo`**，绝不参与运行期决策或改变 `silver_demo` 行为（VM-3 / VM-5 / VM-9）。
  它与 `silver_demo` 的关系只能是「被 Host 反向 import」，而非「依赖 `silver_demo`」。
- **真正被放宽的只有 Gateway / Host 这一层（Composition Root），不是整个 `silver_demo` 包**。
  Runtime Core 的禁止规则与原白名单**完全不变**。

> **硬原则（Hard Invariant）**：
> *Presentation Layer may be imported by the Application Host / Composition Root,
> but must never be imported by Runtime Core or participate in runtime decision logic.*

### 2.2 消费边界精确映射（基于真实代码，三端）

`PerceptionPipeline.process_frame(frame) -> FrameResult` 是**唯一消费出口**（不调用 `run()` 批处理，
以保证逐帧 WebSocket 推送）。FrameResult 携带：

| FrameResult 字段 | 消费端 | 映射 |
| --- | --- | --- |
| `n_detections` | **AI 中心（左屏视频 + 元数据）** | "当前检测数" |
| `n_visitor_events` | **AI 中心（左屏视频 + 元数据）** | "在场访客数" |
| `perception_events: PerceptionEvent[]` | AI 中心 | `event_type`(abnormal_dwell/repeat_visit/high_risk_approach) → 访客状态标签 |
| `warnings: WarningEvent[]` | **AI 风险中心** | 实时事件流 + 风险解释卡片 |
| `commands: ActionCommand[]` | **家属端 / 社区端** | 按 `command_type` 路由 |

**三端是逻辑消费拆分，渲染为一个 HTML 观察窗口内的区域**（非 3 个独立 SPA，降低前端复杂度）：
AI 风险中心（核心区）+ 行动闭环区（家属/社区面板）。

**AI 风险中心（核心展示）= `WarningEvent` 驱动**
- `risk_level`（LOW/MEDIUM/HIGH）→ 风险等级徽标
- `reason_summary: List[str]` → **风险解释卡片**（"✓ 夜间访问 ✓ 长时间停留 ✓ 重复出现"），**无欺诈概率**
- `recommended_action`（MONITOR/NOTIFY_FAMILY/ESCALATE_COMMUNITY）→ 建议动作
- `perception_score`(0-1) → 强度条（非概率）
- `trigger_events` → 可下钻的触发依据

**家属端 = `ActionCommand[command_type == SEND_FAMILY_MESSAGE]`**
- `payload.message` → 推送文案（由 `ActionDispatcher._format_family_message` 生成，人话）
- `payload.contact` → 家属信息
- "AI 辅助判断，人最终决策"：提供 `[认识] [通知社区]` 按钮

**社区端 = `ActionCommand[command_type == CREATE_COMMUNITY_TASK]`**
- `payload.task`（elder_id / risk_level / reasons / warning_id / created_at）→ 任务卡
- 提供 `[接受] [完成]` 按钮，体现闭环（简化工单，**不做完整工单系统**）

**Monitor（`LOG_ONLY`）= 仅记录**，不展示为独立页面。

> **时基统一约定（补记 · 2026-07-22）**：`WarningEvent` / `PerceptionEvent` 的 `created_at`
> 由模型 `default_factory=_utc_now` 打的是**墙钟 UTC**，而 Dashboard ① 区状态面板显示的是
> DemoClock **模拟时间**（`demo_time`，按 `frame_interval_s` 逐帧推进）。二者时基不同会导致
> ① 区模拟时间与 ② 区行为时间线错位。约定：桥接层 `bridge.frame_result_to_view` 在
> `to_dict()` **副本**上把 `created_at` 重打为 `demo_time`（仅作用于传给前端的 dict，
> **不修改冻结模型实例**，因此不破坏 ADR-0014 L1 Schema 冻结与本 ADR §2.1 白名单只读约束）。
> 冻结合约回归见 `tests/demo/test_freeze_boundary.py::test_bridge_view_model_restamps_created_at_to_demo_time`。

### 2.3 技术栈（新增，不污染冻结包）

- **网关（Python，同进程托管 pipeline）**：`FastAPI` + `WebSocket`（`uvicorn` 驱动）。
- **展示层 = 单页 HTML + Vanilla JS**（**不用 Vue/Vite 等框架**）：原生 `WebSocket` 连接，
  5 个区域渲染，零前端构建步骤；FastAPI 通过 `StaticFiles` 托管 `silver_demo/dashboard/`。
  > 选择理由：比赛优势在架构/契约/解释/闭环，不在前端复杂度；单页 HTML 比复杂 SPA 更快验证
  > "冻结架构确实适合做产品接口"，且未来 RTSP/EZVIZ/MQTT/Agent 接入时 Dashboard **零重写**。
- **帧传输 = base64 JPEG over WebSocket**（详见 §2.4）：稳定性 > 性能；单摄像头 / 单浏览器 / CAVIAR fixture，
  无需 MJPEG server / WebRTC / 独立视频流服务。
- **依赖隔离**：`fastapi` / `uvicorn[standard]` / `websockets` 放入 `pyproject.toml` 的
  **可选 extra `[demo]`**（`pip install -e ".[demo]"`），核心 `home_perception` 仍零 Web 依赖。
- **运行时**：网关经 `PerceptionPipeline.from_settings(...)` 装配流水线，并调用 `pipeline.load_detector()` 懒加载 YOLO 权重（与 pipeline 同进程，torch 已随 home_perception 引入；`load_detector()` 是 `PerceptionPipeline` 的实例方法，非独立函数）。

### 2.4 三端数据流（摄像机不独立成端，帧经 base64 推送）

```
                CAVIAR (工程验证) / 真实 MP4 (产品展示)
                      │  frame (np.ndarray) — 经 silver_demo.sources 帧源抽象
                      ▼
               PerceptionPipeline
            process_frame(frame, i)
                      │
                      ▼
                  FrameResult
                      │
              silver_demo bridge
   （FrameResult → 三端 view-model；frame → JPEG encode → base64）
                      │
              WebSocket Gateway  ── (JSON + base64 JPEG) ──►  HTML Dashboard
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
    AI 风险中心     家属面板       社区面板
   （视频左屏 +    （SEND_FAMILY   （CREATE_COMMUNITY
    WarningEvent）   _MESSAGE）      _TASK）
```

颜色编码（与 README/API_REFERENCE 一致）：
- 🟢 **稳定契约（绿）**：`PerceptionPipeline` 入口、`FrameResult`/`WarningEvent`/`ActionCommand` 类型
- 🟡 **可替换实现（黄）**：`silver_demo.sources.VideoFileFrameSource`（真实 MP4，替换 CAVIAR 帧源）、`CaviarFrameSource`（冻结包内 FrameSource 契约参考实现）、HTML Dashboard（未来真 App）
- 🔴 **禁止依赖（红）**：`silver_demo` 不得 import 7 层内部；不得自行构造 `RuleEngine`/`DecisionEngine`/`ActionExecutor`；不得改 `WarningEvent`/`ActionCommand` 契约

### 2.5 反馈闭环（DemoStateStore，仅内存 dict，不回写冻结对象）

用户操作（家属 `[认识]` / `[通知社区]`、社区 `[接受]` / `[完成]`）经 WebSocket 上行 →
网关写入 **`DemoStateStore`**（进程内 dict），并广播状态更新到对应视图（"已处理" / "已闭环"）。

```python
# DemoStateStore：第一版仅此结构，无数据库、无登录、无权限
{
  warning_id: {
    "status": "pending",      # pending → family_handled → community_done
    "operator": "family",     # 最近操作者
  }
}
```

- **严禁回写** `WarningEvent` / `ActionCommand`（冻结对象只读消费）。状态翻转只发生在 `DemoStateStore`。
- 不引入数据库、不引入登录、不做跨会话持久化——演示重启即重置。

### 2.6 场景驱动（确定性、可复现）

- 网关持有 `DemoClock(start=datetime(2026, 7, 20, 23, 30, tzinfo=timezone.utc), interval_s=0.5)`，每帧 `clock.tick(interval_s)`（在网关循环内，不在 pipeline 内）。实际签名 `DemoClock(start: Optional[datetime] = None, interval_s: float = 0.5)`，故 `start` 须传 `datetime` 而非时间字符串。
- **场景双轨定位（Owner 调整 · 2026-07-21）**：CAVIAR 与真实 MP4 各司其职，不互相替换删除。
  - **工程验证层（CAVIAR）**：`OneLeaveShopReenter1cor` / `OneStopEnter1cor` 等公开序列，确定性可复现，
    证明 `Tracking → Event → Feature → Rule` 链路正确。`night_visit` 剧本（`config/demo/scenarios/night_visit.yaml`）
    仍用 CAVIAR 做工程回归与阈值调优基线。
  - **产品展示层（真实门口 MP4）**：演示者提供 `data/demo/real_doorway.mp4`（gitignore，不入库），
    证明「银龄盾场景价值」。`config/demo/scenarios/real_doorway.yaml` 用 `source_type: video_file` 接入，
    Dashboard / Pipeline / WarningEvent 零改动（P0-11.3 验证）。
  - **统一输出**：两轨都收敛到 `WarningEvent → HIGH_RISK_APPROACH`，评委看到的是
    「真实场景输入 → 工业级架构 → 风险闭环」，而非单纯 CAVIAR。
  - **为何真实数据提前（P0-11.3）**：验证冻结架构（ADR-0014 L2 FrameSource 契约）是否真的允许外部输入替换——
    把 CAVIAR 帧源换成 `VideoFileFrameSource`（真实 MP4），Dashboard/Pipeline/WarningEvent 不改即是最直接证明；
    且避免「一直用 CAVIAR 到最后，Dashboard 漂亮但业务关联弱」。真实输入从 MP4 起，不接 RTSP/EZVIZ（见 §9.7）。
- **`night_visit` 主选 `OneLeaveShopReenter1cor`**（工程验证）：天然体现「出现 → 离开 → 再次出现」，对应 `RepeatVisitRule`，
  最容易解释 *"AI 不是看到一个人就报警，而是理解访问模式"*。辅选 `OneStopEnter1cor`（长停留）。
  **最终比赛工程剧本**：`OneLeaveShopReenter1cor` + 夜间 `DemoClock` + 阈值调低
  → 「夜间 + 重复出现 + 长停留」组合触发 `HighRiskApproachRule`（CompositeRule）→ `HIGH_RISK_APPROACH`。
  阈值调优是 P0-11.5 任务，本 ADR 仅定方向。
- 场景参数放 `config/demo/scenarios/*.yaml`：`source` / `source_type`（`caviar_jpg` | `video_file`）/ `media_path`（video_file 时）/ `start_time` / `frame_interval_s`。

### 2.7 核心交付物：5 分钟风险闭环故事

比页面更重要。P0-11.5 必须能把下面这条线在 ~5 分钟内讲完、跑完：

| 节拍 | 时间 | 画面 / 事件 | 价值点 |
| --- | --- | --- | --- |
| 开场 | — | 普通摄像头："有人经过" | 无价值基线 |
| AI 介入 | 23:30 | 检测到陌生访客，持续停留 | 从"看到人"→"理解访问行为" |
| 风险升级 | 23:40 | 再次出现，组合规则触发 `HIGH_RISK_APPROACH` | 规则层产物可被解释 |
| 干预 | — | 家属收到"AI 发现异常访问，是否认识？" | AI 辅助，人最终决策 |
| 闭环 | — | 社区"任务完成，已核验" | 三端联动，闭环成立 |

这条故事成立，即证明 v0.1.0-mvp-rc 冻结架构在真实展示场景下**零改动可用**。

### 2.8 Demo 数据真实性声明（比赛可信度护栏）

> P0-11 **双轨输入**均属**受控演示输入**，用于演示系统闭环与架构消费契约，不代表真实部署环境的性能指标，亦不用于证明模型泛化能力：
> - **CAVIAR 公开 fixture**（工程验证层）：确定性、可复现，用于回归 `Tracking → Event → Feature → Rule` 链路。
> - **真实门口 MP4**（`data/demo/real_doorway.mp4`，演示者提供、gitignore 不入库）：属"真实场景素材"而非"真实部署"——
>   它验证冻结架构允许外部真实输入无缝接入，但**仍非 7×24 实时摄像头 / 萤石设备直连**（那属 P0-12）。
> Demo 的目标是被冻结的 AI 链路能够"被发现 → 被解释 → 被干预 → 被闭环"，而非验证检测模型在真实场景的准确率。

在 Dashboard 与 demo README 中明确标注此声明（评委若问"视频数据真实吗"，提前定义边界反而增加可信度）。

---

## 3. 目标包布局（新增，不触碰 home_perception）

```
src/silver_demo/
├── __init__.py
├── config.py        # DemoSettings（bind host/port、caviar fixture、scenario 路径）
├── gateway.py       # FastAPI app：持有 pipeline + DemoClock，帧循环，WS 广播，StaticFiles('/dashboard')
├── bridge.py        # FrameResult → 三端 view-model；frame → JPEG encode → base64
├── state.py         # DemoStateStore（进程内 dict，反馈闭环、warning_id 幂等映射）
├── scenarios.py     # 加载 config/demo/scenarios/*.yaml（扩展 source_type / media_path）
├── sources.py        # P0-11.3：DemoFrameSource 抽象 + CaviarJpgFrameSource + VideoFileFrameSource + 工厂
├── ws.py            # WebSocket 端点 + 极简广播（单演示连接即可）
└── dashboard/       # 纯静态展示层（HTML + Vanilla JS），只消费 WS，不碰任何算法代码
    ├── index.html   # 单页观察窗口，含 5 区域
    ├── style.css
    └── app.js       # 原生 WebSocket 封装 + 5 区域渲染（无框架、无构建步骤）

config/demo/scenarios/night_visit.yaml    # CAVIAR 工程验证剧本
config/demo/scenarios/real_doorway.yaml   # 真实门口 MP4 产品展示剧本（source_type: video_file）

tests/demo/
├── test_freeze_boundary.py   # 证明 silver_demo 只消费冻结契约（见 §5）
└── test_sources.py           # P0-11.3：VideoFileFrameSource / CaviarJpgFrameSource / 工厂分发
```

**Dashboard 5 区域（单页，对应"三端"逻辑拆分）**：

| # | 区域 | 数据来源 | 内容 |
| --- | --- | --- | --- |
| 1 | 实时视频区（最重要） | `FrameResult` + base64 JPEG | 左屏画面 + 检测人数 / 访客事件 / 运行状态 |
| 2 | AI 感知时间线 | `perception_events` / `warnings` | 23:30 检测到访客 → 23:35 停留异常 → 23:40 重复访问 → 23:45 HIGH（讲清"看到人→理解行为"） |
| 3 | 风险解释卡片 | `WarningEvent` | 风险等级 + `reason_summary`（✓ 夜间访问 ✓ 长停留 ✓ 重复出现）+ 建议动作 |
| 4 | 行动闭环区 | `ActionCommand` + `DemoStateStore` | 家属端"已发送提醒/等待确认" + 社区端"已创建任务/处理中" + 交互按钮 |
| 5 | 系统架构小图 | 静态 | 7 层链路图 + `289 Tests Passed` + `v0.1.0 MVP RC`（评委秒懂工程成熟度） |

区域 4 的交互按钮：
- 家属：`[认识] [通知社区]`
- 社区：`[接受] [完成]`
经 WS 上行 → `DemoStateStore` 状态翻转 → 广播更新（P0-11.4 落地）。

---

## 4. 分阶段计划（P0-11.1 ~ P0-11.5 · 2026-07-21 重排：真实输入提前）

> 重排动因：原顺序（架构冻结→Dashboard→解释→交互→演示优化）对软件 Demo 成立，但"真实/准真实输入"一直留到最后，
> 容易出现"Dashboard 漂亮但业务关联弱"。调整后把**真实视频输入适配提前到 P0-11.3**，
> 以最直接方式验证 ADR-0014「冻结架构允许外部输入替换」（Dashboard/Pipeline/WarningEvent 零改动）。

| 阶段 | 目标 | 验收（可演示） |
| --- | --- | --- |
| **P0-11.1** ✅ | FastAPI Gateway + 帧源抽象消费 + WebSocket 广播（JSON + base64 JPEG） | 起服务后 WS 稳定推送 `FrameResult` 流 + 视频帧可达 |
| **P0-11.2** ✅ | **HTML Dashboard MVP** ⭐（5 区域：视频 / 时间线 / 风险卡片 / 行动闭环 / 架构图） | 单页打开即见实时视频 + 感知时间线 + 冻结消费边界；快速验证冻结架构可作产品接口 |
| **P0-11.3** 🔧 | **真实视频输入适配（新增）**：`VideoFileFrameSource`（MP4）替换 CAVIAR 帧源；场景 `source_type` 分发 | `real_doorway`（MP4）跑通，Dashboard **零修改**即可渲染；证明架构可替换输入 |
| **P0-11.4** | **WarningEvent / ActionCommand 产品展示**：风险解释卡片（区域 3）+ 家属/社区任务（区域 4）完整渲染 | 风险等级 + 人话原因（无"诈骗概率"）+ 三端任务卡联动 + 上行交互按钮 |
| **P0-11.5** | **闭环交互 + 5 分钟 Demo 脚本**：一次完整故事演示（CAVIAR 工程 + 真实 MP4 展示双轨）+ demo README | 单故事讲完：陌生人→停留→重复→HIGH→三端联动→闭环 |

每个阶段独立 PR（不一次性大改）；每阶段**不修改 `home_perception` 任何文件**。
**不过度扩展**——P0-11.5 之后即停，不做 Agent / LLM 解释 / 真实 App / 数据库 / 用户体系（见 §6，归 P1/P2）。

---

## 5. 冻结合规证明（呼应 ADR-0014 · 升级为分层依赖契约测试）

新增 / 升级 `tests/demo/test_freeze_boundary.py` 与 `tests/visualizer/test_ast_contract.py`，
把 ADR-0014 L3（Runtime Assembly 契约）+ ADR-0036 §2.1.1 分层依赖铁律从「内部纪律」变成「外部可验证」。

### 5.1 分层依赖契约验收（T0-1 ~ T0-6）

1. **T0-1（Runtime Core 不依赖 Presentation Layer）**：AST 扫描 `silver_demo` 内除 `gateway` 外的
   所有子模块，断言其**不得** import `home_perception.visualizer`（含 `viewer`/`render`/`loader`/`schema`）；
   若出现 → 测试失败。
2. **T0-2（Host 可依赖 Presentation Layer）**：断言 `silver_demo.gateway` **可** import
   `home_perception.visualizer.viewer`（及其 `render_case_viewer` / `ProjectionAccumulator` /
   `build_live_presentation`），作为唯一的 Composition Root 反向依赖。
3. **T0-3（Presentation Layer 不反向依赖 silver_demo）**：AST 扫描 `home_perception.visualizer`，
   断言其**不得** import `silver_demo`（任何子模块）；若出现 → 测试失败。
4. **T0-4（gateway 经 viewer 投影）**：断言 `gateway` 只通过 `viewer/live_adapter` 的
   `build_live_presentation` / `ProjectionAccumulator` 把 `FrameResult` 投影为 `EvidenceProjection`，
   不自行构造 View Model。
5. **T0-5（viewer 不参与运行期决策）**：断言 `home_perception.visualizer` 不 import
   `rule_engine`/`decision_engine`/`decision_policy`/`action.executor` 等运行期决策符号，
   不改变 `silver_demo` 行为。
6. **T0-6（GET /live 收敛到统一 Case Viewer）**：集成测试断言 `GET /live` 与 `GET /` 共用同一
   `render_case_viewer` 渲染器、同一 `EvidenceProjection` View Model、同一套语义体系
   （`tests/demo/test_gateway_serves_case_viewer.py::test_live_mode_serves_case_viewer`）。

### 5.2 冻结消费契约验收（沿用原 §5 三断言，仍有效）

A. **import 边界**：用 `importlib` 导入 `silver_demo.gateway`，遍历其模块依赖图，
   断言运行时消费**仅**引用 §2.1 白名单中的 `home_perception` 子模块（不含 7 层内部）；
   若出现 `rule_engine`/`decision_engine`/`action.executor` 等 → 测试失败。
B. **消费形态**：断言网关只调用 `PerceptionPipeline.from_settings` / `process_frame` /
   读取 `FrameResult` 字段，断言其**不**拥有 `RuleEngine`/`DecisionEngine`/`ActionExecutor` 实例引用。
C. **类型只读**：断言网关对 `WarningEvent`/`ActionCommand` 仅调用 `.to_dict()`，不调用构造器。

> 这条测试把 ADR-0014 / ADR-0036 的边界从「内部纪律」变成「外部可验证」——
> 直接回答 Owner 之前的担忧："后续接 Dashboard/设备/Agent 时容易绕过架构"。
> 它也是本 ADR §1 原则（"Demo 是消费者，不是架构参与者"）的可执行证据——
> 只是「消费者」在 ADR-0036 之后明确包含了「Host 反向依赖 Presentation Layer 渲染」这一合法角色。

---

## 6. 明确不做（Out of Scope，守冻结 + 守护城河）

- ❌ 真 MQTT / 真短信 / 真 EZVIZ / 真 App（P1/P2）
- ❌ 大模型 Agent 决策 / LLM 解释（P2 Trust Layer）
- ❌ 检测框绘制（Dashboard 只显示帧 + 文本状态；框绘制属 P1，且会要求改 `FrameResult`）
- ❌ 大规模数据训练 / 模型 SOTA 优化
- ❌ 修改 `home_perception` 任何 `.py`（包括不扩展 `FrameResult` 加 boxes）
- ❌ 把 Web 依赖加进核心 `home_perception` 包
- ❌ **Vue / 复杂前端框架**（单页 HTML + Vanilla JS 足够；前端复杂度不增评分）
- ❌ **多用户连接管理**（第一版单演示连接即可）
- ❌ **权限 / 登录 / 认证**（Demo 无账户体系）
- ❌ **数据库存储**（仅 `DemoStateStore` 进程内 dict）
- ❌ **历史查询 / 回放系统**
- ❌ **真反馈系统**（不回写冻结对象，仅 `DemoStateStore` 状态翻转）
- ❌ **完整状态同步**（仅广播最新帧状态，不做增量 diff/冲突合并）
- ❌ **Monitor 独立页面**（`LOG_ONLY` 仅记录，不展示）
- ❌ **独立摄像头端页面**（视频并入 Dashboard 实时视频区）

---

## 7. 决策记录（原 §7 开放问题已拍板）

1. **帧传输方式** → **base64 JPEG over WebSocket**。理由：稳定性 > 性能；单摄像头 / 单浏览器 / CAVIAR fixture，
   无需 MJPEG server / WebRTC / 独立视频流服务。未来 P0-12 接真实设备时展示层零改变（帧仍经 FrameResult→base64）。
2. **依赖管理** → **`pyproject` optional extra `[demo]`**（`fastapi` / `uvicorn[standard]` / `websockets`）。
   保持核心 `home_perception` 零 Web 依赖，避免污染设备端 / 边缘部署 / 模型测试。
3. **场景素材** → 主选 **`OneLeaveShopReenter1cor`**（重复访问易解释）；辅选 `OneStopEnter1cor`（长停留）。
   最终剧本：`OneLeaveShopReenter1cor` + 夜间 `DemoClock` + 阈值调低 → 组合触发 `HighRiskApproachRule` → `HIGH_RISK_APPROACH`。
4. **ADR + roadmap 同 PR** → **是**（即本 PR #32，原子提交避免"代码进 P0-11 而 roadmap 仍 P0-10"）。
5. **真实输入提前（2026-07-21 调整）** → 真实视频输入适配从原 P0-11.5 提前到 **P0-11.3**，重排为
   `Gateway → Dashboard → 真实/准真实输入 → 风险闭环展示 → 演示打磨`。理由：最直接验证 ADR-0014
   「冻结架构允许外部输入替换」（Dashboard/Pipeline/WarningEvent 零改动）；避免一直用 CAVIAR 致业务关联弱。
   真实输入从 **MP4 门口视频**起，**不接 RTSP/EZVIZ**（见 §9.7）。

> 二审新增两项已落入本文：§2.8 **Demo 数据真实性声明**；§3/§4 **单页 HTML Dashboard 提前至 P0-11.2**（替代原 Vue 三端 SPA）。
> 2026-07-21 重排：§2.1 修正 `CaviarFrameSource`→`read_caviar_frames` 偏差并明确消费者自提供帧源；§2.4/§2.6/§2.8/§4 反映 CAVIAR 工程验证 + 真实 MP4 产品展示双轨。

---

## 8. 收益（Conclusion）

- P0-11 把项目从"优秀 AI 工程原型"升级为"可向评委展示价值闭环的产品原型"——
  **重点是一次风险闭环故事是否成立**，而非页面 / 框架数量。
- 关键收益：**P0-11 完全不碰冻结链路**，仅消费 `WarningEvent`/`ActionCommand`——
  这本身就是对前面所有架构治理投入（ADR-0014、Contract Test、Convergence、DX Docs、RC tag）的**最终验证**。
- 三端 + 单页 HTML 收敛后，展示层代码量大幅下降，把比赛投入集中在"讲清闭环"而非"堆砌前端"，
  投入产出比显著优于四端产品化 Vue Demo。
- Dashboard 作为统一观察窗口，未来 RTSP / EZVIZ / MQTT / Agent 接入时**零重写**即可复用。
- 后续 P0-12（设备适配）/ P1（DDD 大迁移）/ P2（数字孪生）接入时，展示层代码**零改动**即可对接新实现，
  因为边界是契约而非实现。

---

## 9. 替代方案（Alternatives）

本 ADR 在收敛过程中否决了以下方案，记录以留存决策脉络（对应 §7 已拍板项与 §1 收敛过程）。

### 9.1 四端产品展示层（独立 Camera / Center / Family / Community / Monitor 页）
- **思路**：为每个视角建立独立 SPA 页面，摄像头端独立展示实时视频。
- **否决原因**：比赛投入产出比低——评委关心的是"风险闭环是否成立"而非页面齐全；拆出独立摄像头页
  会分散评委对"为什么不是普通摄像头"这一核心价值的注意力；五个前端视角对 MVP 是过度设计
  （Owner 一审即指出"四端对于 MVP 太多"）。故摄像头视频并入 AI 风险中心单页大屏。

### 9.2 MJPEG 独立视频流服务
- **思路**：另起 MJPEG / WebRTC 视频流服务推送摄像头画面，HTML 通过 `<img>` / `<video>` 消费。
- **否决原因**：稳定性优先于性能；单摄像头 / 单浏览器 / CAVIAR fixture 的演示场景下，
  base64 JPEG 嵌入 WebSocket 消息即可满足，无需额外流媒体服务进程与协议复杂度；
  未来 P0-12 接真实设备时展示层零改变（帧仍经 `FrameResult` → base64）。此为 §7 开放问题①的备选。

### 9.3 WebSocket 二进制帧（而非 base64 JSON）
- **思路**：WebSocket 直接传二进制 JPEG，不 base64。
- **否决原因**：演示首要目标是"让评委看懂数据在流动"，JSON + base64 JPEG 与 `FrameResult` 文本字段
  同包推送，便于在浏览器 DevTools 直接观察契约结构；二进制帧虽省带宽但牺牲可读性，对单演示连接收益可忽略。

### 9.4 Vue / Vite 三端 SPA 前端
- **思路**：用 Vue 组件化构建三端仪表盘。
- **否决原因**：冻结架构的核心优势是分层 / 契约 / 解释 / 闭环，前端框架复杂度不增加评分；
  单页 HTML + Vanilla JS 更快验证"冻结架构确实适合做产品接口"，且零前端构建步骤；
  未来 RTSP / EZVIZ / MQTT / Agent 接入时 Dashboard 零重写（见 §2.3）。

### 9.5 真 MQTT / 真短信 / 真 EZVIZ 接入
- **思路**：P0-11 直接接真实消息总线、短信网关、萤石设备。
- **否决原因**：P0-11 定位是**消费者而非重构者**，目标是验证冻结架构的对外价值闭环；
  `ActionExecutor` 的 `MockPublisher` / `MockNotifier` 已足够驱动家属 / 社区面板；
  真通道属 P0-12（设备适配）/ P1（真实通信）范畴，提前引入会污染冻结链路。

### 9.6 「先做页面 → 硬改模型」反路
- **思路**：先搭漂亮前端，发现数据不够再回头改检测 / 规则模型。
- **否决原因**：这恰是比赛项目常见的架构崩塌路径（见 §1）。本项目选择"先冻结再展示"，
  Demo 只消费契约、零改 `home_perception`，从根上避免架构腐化。

### 9.7 真实输入用 RTSP / EZVIZ 直连（而非 MP4 文件）
- **思路**：P0-11.3 直接接真实摄像头 RTSP 流 / 萤石 EZVIZ 设备作为 Demo 输入。
- **否决原因**：RTSP / EZVIZ 引入**网络 / 设备 / 权限**三类不确定性（断流、鉴权、码流兼容），
  与"可重复 / 可剪辑 / 可控风险触发 / 不依赖网络"的演示目标相悖；比赛阶段这些不是核心。
  故 P0-11.3 真实输入从 **MP4 门口视频文件**起（`VideoFileFrameSource`），把"真实场景素材"与"实时设备直连"解耦——
  前者验证架构可替换输入，后者属 P0-12 设备适配。CAVIAR 仍用于工程回归（确定性可复现）。
