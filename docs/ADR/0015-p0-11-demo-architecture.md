# ADR-0015: P0-11 MVP Demo 架构（三端风险闭环展示层）

- **状态**：Draft（Owner 评审中 · v2 收敛版，基于 Owner 初审反馈收敛「四端产品展示」→「三端风险闭环」）
- **作者**：AI（design pass，v2 收敛）
- **依赖**：ADR-0014（三级冻结）、P0-10 Runtime Assembly、P0-10.5.x 治理、v0.1.0-mvp-rc tag
- **范围**：ROADMAP P0-11（MVP Demo v0.1）

---

## 1. 背景与动机（Why）

P0-3 ~ P0-10 已证明**算法链路成立**：

```
摄像头输入 → 感知 → 访客轨迹 → 事件 → 特征 → 规则 → 风险决策 → 行动命令
```

P0-10.5 已证明**架构纪律成立**（冻结契约 + Contract Test + 仓库卫生 + RC tag）。

**P0-11 的目标不是"再做几个页面"，而是证明一次完整的风险闭环故事成立**：

```
老人门口
  ↓  AI 感知
发现异常访问
  ↓  风险解释（为什么不是普通摄像头）
家属 / 社区收到干预
  ↓  人工确认（AI 辅助，人最终决策）
闭环
```

评委真正关心的是这个闭环是否成立，而不是四个前端页面是否齐全。因此本 ADR 把 P0-11 从
「四端产品展示层」收敛为「三端风险闭环展示层」，把资源集中在一条可被 5 分钟讲完的故事线上。

> **关键判断**：v0.1.0-mvp-rc 冻结架构的最大价值，现在才开始体现。
> 很多比赛项目走的是**反路**——先做页面 → 发现数据不够 → 硬改模型 → 架构崩；
> 本项目的路线是：
> ```
> 事实层 → 事件层 → 特征层 → 规则层 → 决策层 → 行动层 → 冻结 → 展示层
> ```
> 这接近真实工业研发流程。所以 P0-11 的原则是：
> **不要让 Demo 反过来污染系统。Demo 是消费者，不是架构参与者。**

> 技术护城河当前是 **架构完整性 + 工程可信度**，不是模型 SOTA。
> 因此 P0-11 必须**只消费冻结契约、零改动 P0-10 链路**，这正好是对 ADR-0014 价值的外部验证。

---

## 2. 决策（Decision）

### 2.1 引入独立的展示层包 `src/silver_demo/`（与冻结包物理隔离）

- 与冻结包 `home_perception` **物理隔离**（different package），保证冻结包不引入任何 Web 依赖。
- `silver_demo` **仅 import 以下白名单符号**（视为"消费冻结契约"的合法边界）：

| 消费目标 | 来源（白名单） | 用途 |
| --- | --- | --- |
| `PerceptionPipeline` | `home_perception.runtime.pipeline` | 唯一装配入口 |
| `DemoClock` | `home_perception.runtime.pipeline` | 确定性时序源（驱动场景时间） |
| `CaviarFrameSource` | `home_perception.ingestion.frame_source` | 帧迭代器（模拟摄像头） |
| `FrameResult` | `home_perception.runtime.pipeline` | 每帧结果（消费出口） |
| `WarningEvent` | `home_perception.analysis.warning` | AI 中心 / 分流依据（只读） |
| `ActionCommand` | `home_perception.action.command` | 家属端 / 社区端（只读） |
| `Settings` | `home_perception.core.config` | 装配配置 |

- **严禁**：`silver_demo` 直接或间接 import `rule_engine` / `decision_engine` / `decision_policy` /
  `action.executor` / `action.dispatcher` / `action.notifier` / `action.publisher`（即不得穿透 7 层内部）。
  网关只通过 `PerceptionPipeline.from_settings(...)` 拿到的对象驱动，绝不自行构造/调用层内组件。

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

**摄像头端不作为独立页面**：视频帧 + 检测元数据并入 **AI 风险中心大屏**（左视频、右分析），
避免评委注意力被分散到独立 Camera 视图。

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

### 2.3 技术栈（新增，不污染冻结包）

- **网关（Python，同进程托管 pipeline）**：`FastAPI` + `WebSocket`（`uvicorn` 驱动）。
- **前端（三端视图）**：`Vue 3` + `Vite`，原生 `WebSocket` 连接，无额外状态库。
  AI 中心为单页大屏（左视频 + 右分析），家属端 / 社区端为独立轻量视图。
- **依赖隔离**：`fastapi` / `uvicorn[standard]` / `websockets` 放入 `pyproject.toml` 的
  **可选 extra `[demo]`**（`pip install -e ".[demo]"`），核心 `home_perception` 仍零 Web 依赖。
- **运行时**：网关进程内 `load_detector()` 加载 YOLO（与 pipeline 同进程，torch 已随 home_perception 引入）。
- **第一版明确不做**（详见 §6）：多用户连接管理 / 权限 / 登录 / 数据库 / 历史查询 / 真反馈系统 / 完整状态同步。

### 2.4 三端数据流（摄像机不独立成端）

```
                CAVIAR / 视频流
                      │  frame (np.ndarray)
                      ▼
               PerceptionPipeline
            process_frame(frame, i)
                      │
                      ▼
                  FrameResult
                      │
              silver_demo bridge
          （FrameResult → 三端 view-model）
                      │
              WebSocket Gateway
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
    AI 风险中心     家属端         社区端
   （视频左屏 +    （SEND_FAMILY   （CREATE_COMMUNITY
    WarningEvent）   _MESSAGE）      _TASK）
```

颜色编码（与 README/API_REFERENCE 一致）：
- 🟢 **稳定契约（绿）**：`PerceptionPipeline` 入口、`FrameResult`/`WarningEvent`/`ActionCommand` 类型
- 🟡 **可替换实现（黄）**：`CaviarFrameSource`（未来 RTSPSource/EZVIZSource）、Vue 视图（未来真 App）
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

- 网关持有 `DemoClock(start=23:30, interval_s=0.5)`，每帧 `clock.tick(interval_s)`（在网关循环内，不在 pipeline 内）。
- 帧源：`CaviarFrameSource`（CAVIAR fixture，如 `OneStopEnter1cor` 或选定夜间场景）。
- 场景参数放 `config/demo/scenarios/night_visit.yaml`：source / start_time / frame_interval_s / 可选叙述标记。

### 2.7 核心交付物：5 分钟风险闭环故事

比页面更重要。P0-11.4 必须能把下面这条线在 ~5 分钟内讲完、跑完：

| 节拍 | 时间 | 画面 / 事件 | 价值点 |
| --- | --- | --- | --- |
| 开场 | — | 普通摄像头："有人经过" | 无价值基线 |
| AI 介入 | 23:30 | 检测到陌生访客，持续停留 | 从"看到人"→"理解访问行为" |
| 风险升级 | 23:40 | 再次出现，组合规则触发 `HIGH_RISK_APPROACH` | 规则层产物可被解释 |
| 干预 | — | 家属收到"AI 发现异常访问，是否认识？" | AI 辅助，人最终决策 |
| 闭环 | — | 社区"任务完成，已核验" | 三端联动，闭环成立 |

这条故事成立，即证明 v0.1.0-mvp-rc 冻结架构在真实展示场景下**零改动可用**。

---

## 3. 目标包布局（新增，不触碰 home_perception）

```
src/silver_demo/
├── __init__.py
├── config.py        # DemoSettings（bind host/port、caviar fixture、scenario 路径）
├── gateway.py       # FastAPI app：持有 pipeline + DemoClock，帧循环，WS 广播
├── bridge.py        # FrameResult → 三端 view-model（center/family/community DTO）
├── state.py         # DemoStateStore（进程内 dict，反馈闭环、warning_id 幂等映射）
├── scenarios.py     # 加载 config/demo/scenarios/*.yaml
└── ws.py            # WebSocket 端点 + 极简广播（单演示连接即可）

demo/web/                     # Vue 3 + Vite 前端
├── src/
│   ├── ws.js                 # 原生 WebSocket 封装（3 个端点：center/family/community）
│   └── views/
│       ├── Center.vue        # AI 风险中心（核心）：左视频 + 右实时事件/风险解释
│       ├── Family.vue        # 家属端
│       └── Community.vue     # 社区端（简化任务卡）
└── ...

config/demo/scenarios/night_visit.yaml

tests/demo/
└── test_freeze_boundary.py   # 证明 silver_demo 只消费冻结契约（见 §5）
```

---

## 4. 分阶段计划（P0-11.1 ~ P0-11.4，到此停止）

| 阶段 | 目标 | 验收（可演示） |
| --- | --- | --- |
| **P0-11.1** | FastAPI Gateway + `CaviarFrameSource` 驱动 `process_frame` + WebSocket 广播 + **AI 风险中心**（视频左屏 + 实时事件流 + 风险解释卡片） | 起服务后中心端实时出现 `WarningEvent` 流；左屏显示帧 + 检测数 + 人话原因列表 |
| **P0-11.2** | **家属端**视图（消费 `SEND_FAMILY_MESSAGE` command）+ `[认识][通知社区]` 写入 `DemoStateStore` | 出现推送文案 + 按钮；点击后状态翻转并广播"已处理" |
| **P0-11.3** | **社区端**视图（消费 `CREATE_COMMUNITY_TASK` command）+ `[接受][完成]`；三端闭环状态可见 | 任务卡 + 按钮；完成后三端显示"已核验 / 已闭环" |
| **P0-11.4** | **5 分钟故事脚本** + demo README + 串联三端状态翻转 | 单故事讲完：23:30 陌生人 → 停留 → 重复 → HIGH → 三端联动 → 闭环 |

每个阶段独立 PR（不一次性大改）；每阶段**不修改 `home_perception` 任何文件**。
**不过度扩展**——P0-11.4 之后即停，不做 P0-11.5+（多端同步/历史/登录等见 §6 明确不做）。

---

## 5. 冻结合规证明（呼应 ADR-0014）

新增 `tests/demo/test_freeze_boundary.py`，作为 P0-11 的"攻击性契约测试"：

1. **import 边界**：用 `importlib` 导入 `silver_demo.gateway`，遍历其模块依赖图，
   断言**仅**引用 §2.1 白名单中的 `home_perception` 子模块；
   若出现 `rule_engine`/`decision_engine`/`action.executor` 等 → 测试失败。
2. **消费形态**：断言网关只调用 `PerceptionPipeline.from_settings` / `process_frame` /
   读取 `FrameResult` 字段，断言其**不**拥有 `RuleEngine`/`DecisionEngine`/`ActionExecutor` 实例引用。
3. **类型只读**：断言网关对 `WarningEvent`/`ActionCommand` 仅调用 `.to_dict()`，不调用构造器。

> 这条测试把 ADR-0014 Level 3（Runtime Assembly 契约）从"内部纪律"变成"外部可验证"——
> 直接回答 Owner 之前的担忧："后续接 Dashboard/设备/Agent 时容易绕过架构"。
> 它也是本 ADR §2 原则（"Demo 是消费者，不是架构参与者"）的可执行证据。

---

## 6. 明确不做（Out of Scope，守冻结 + 守护城河）

- ❌ 真 MQTT / 真短信 / 真 EZVIZ / 真 App（P1/P2）
- ❌ 大模型 Agent 决策（P2 Trust Layer）
- ❌ 检测框绘制（AI 中心只显示帧 + 文本状态；框绘制属 P1，且会要求改 `FrameResult`）
- ❌ 大规模数据训练 / 模型 SOTA 优化
- ❌ 修改 `home_perception` 任何 `.py`（包括不扩展 `FrameResult` 加 boxes）
- ❌ 把 Web 依赖加进核心 `home_perception` 包
- ❌ **多用户连接管理**（第一版单演示连接即可）
- ❌ **权限 / 登录 / 认证**（Demo 无账户体系）
- ❌ **数据库存储**（仅 `DemoStateStore` 进程内 dict）
- ❌ **历史查询 / 回放系统**
- ❌ **真反馈系统**（不回写冻结对象，仅 `DemoStateStore` 状态翻转）
- ❌ **完整状态同步**（仅广播最新帧状态，不做增量 diff/冲突合并）
- ❌ **Monitor 独立页面**（`LOG_ONLY` 仅记录，不展示）
- ❌ **独立摄像头端页面**（视频并入 AI 中心大屏左侧）

---

## 7. 待 Owner 评审的开放问题

1. **帧传输方式**：base64 JPEG 嵌入 WS 消息（单连接简单，推荐） vs 独立 MJPEG 流？（默认推荐前者）
2. **依赖管理**：`pyproject` optional extra `[demo]`（推荐） vs `demo/requirements.txt`？（默认推荐前者）
3. **场景素材**：用 CAVIAR 哪个 fixture 作 `night_visit`？（建议选含夜间 / 长停留 / 重复访问的片段；需 Owner 指定确切 fixture id）
4. **ADR 合并方式**：本 ADR-0015 与 `docs/08_roadmap.md` 的 P0-11 章节是否同 PR 提交？（默认推荐同 PR）

> 已收敛项（v2 初审结论，无需再议）：
> - 反馈回写 → **仅 `DemoStateStore(dict)`**，不镜像 `WarningEvent.status`（冻结对象不可变）。
> - 端数量 → **三端**（AI 中心 + 家属 + 社区），摄像头端不独立成页。
> - 范围 → **P0-11.1 ~ P0-11.4 即停**，不做 P0-11.5+。

---

## 8. 收益（Conclusion）

- P0-11 把项目从"优秀 AI 工程原型"升级为"可向评委展示价值闭环的产品原型"——
  **重点不是页面数量，而是一次风险闭环故事是否成立**。
- 关键收益：**P0-11 完全不碰冻结链路**，仅消费 `WarningEvent`/`ActionCommand`——
  这本身就是对前面所有架构治理投入（ADR-0014、Contract Test、Convergence、DX Docs、RC tag）的**最终验证**。
- 三端收敛后，展示层代码量大幅下降，把比赛投入集中在"讲清闭环"而非"堆砌页面"，
  投入产出比显著优于四端产品化 Demo。
- 后续 P0-12（设备适配）/ P1（DDD 大迁移）/ P2（数字孪生）接入时，展示层代码**零改动**即可对接新实现，
  因为边界是契约而非实现。
