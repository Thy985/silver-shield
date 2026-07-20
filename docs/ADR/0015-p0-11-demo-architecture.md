# ADR-0015: P0-11 MVP Demo 架构（三端风险闭环展示层）

- **状态**：Draft（v3 · Owner 二审决策已落实：4 项开放问题拍板 + HTML 观察窗口提前 + 数据真实性声明）
- **作者**：AI（design pass，v3）
- **依赖**：ADR-0014（三级冻结）、P0-10 Runtime Assembly、P0-10.5.x 治理、v0.1.0-mvp-rc tag
- **范围**：ROADMAP P0-11（MVP Demo v0.1）

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
- **运行时**：网关进程内 `load_detector()` 加载 YOLO（与 pipeline 同进程，torch 已随 home_perception 引入）。

### 2.4 三端数据流（摄像机不独立成端，帧经 base64 推送）

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
- 🟡 **可替换实现（黄）**：`CaviarFrameSource`（未来 RTSPSource/EZVIZSource）、HTML Dashboard（未来真 App）
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
- **`night_visit` fixture 选定（Owner 拍板）**：
  - **主选 `OneLeaveShopReenter1cor`**：天然体现「出现 → 离开 → 再次出现」，对应 `RepeatVisitRule`，
    最容易解释 *"AI 不是看到一个人就报警，而是理解访问模式"*。
  - 辅选 `OneStopEnter1cor`：展示「进入 → 停留 → 异常停留」，对应 `LongDurationRule`。
  - **最终比赛剧本**：`OneLeaveShopReenter1cor` + `DemoClock` 设为夜间 + 阈值调低
    → 「夜间 + 重复出现 + 长停留」组合触发 `HighRiskApproachRule`（CompositeRule）→ `HIGH_RISK_APPROACH`。
    具体阈值调优是 P0-11.5 任务，本 ADR 仅定方向。
- 场景参数放 `config/demo/scenarios/night_visit.yaml`：source / start_time / frame_interval_s / 可选叙述标记。

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

> P0-11 使用 CAVIAR 公开 fixture 作为**确定性输入**，用于演示系统闭环与架构消费契约；
> **不代表真实部署环境的性能指标**，亦**不用于证明模型泛化能力**。
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
├── scenarios.py     # 加载 config/demo/scenarios/*.yaml
├── ws.py            # WebSocket 端点 + 极简广播（单演示连接即可）
└── dashboard/       # 纯静态展示层（HTML + Vanilla JS），只消费 WS，不碰任何算法代码
    ├── index.html   # 单页观察窗口，含 5 区域
    ├── style.css
    └── app.js       # 原生 WebSocket 封装 + 5 区域渲染（无框架、无构建步骤）

config/demo/scenarios/night_visit.yaml

tests/demo/
└── test_freeze_boundary.py   # 证明 silver_demo 只消费冻结契约（见 §5）
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

## 4. 分阶段计划（P0-11.1 ~ P0-11.5，Dashboard 提前）

| 阶段 | 目标 | 验收（可演示） |
| --- | --- | --- |
| **P0-11.1** | FastAPI Gateway + `CaviarFrameSource` 驱动 `process_frame` + WebSocket 广播（JSON + base64 JPEG） | 起服务后 WS 推送 `FrameResult` 流 + 视频帧可达 |
| **P0-11.2** | **HTML Dashboard MVP** ⭐（区域 1 视频 + 区域 2 时间线 + 区域 5 架构图） | 单页打开即见实时视频 + 感知时间线；快速验证冻结架构可作产品接口 |
| **P0-11.3** | 风险解释卡片（区域 3，消费 `WarningEvent`）+ 行动区骨架（区域 4，消费 `ActionCommand`） | 风险等级 + 人话原因列表（无"诈骗概率"）+ 家属/社区任务卡 |
| **P0-11.4** | 家属/社区交互模拟：按钮 `[认识][通知社区][接受][完成]` 写入 `DemoStateStore` + 闭环状态广播 | 点击后三端状态翻转（"已处理/已核验/已闭环"） |
| **P0-11.5** | **5 分钟演示脚本** + demo README（含数据真实性声明）+ `night_visit` 阈值调优（OneLeaveShopReenter1cor + 夜间 + CompositeRule 触发 HIGH） | 单故事讲完：23:30 陌生人→停留→重复→HIGH→三端联动→闭环 |

每个阶段独立 PR（不一次性大改）；每阶段**不修改 `home_perception` 任何文件**。
**不过度扩展**——P0-11.5 之后即停，不做 Agent / LLM 解释 / 真实 App / 数据库 / 用户体系（见 §6，归 P1/P2）。

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
> 它也是本 ADR §1 原则（"Demo 是消费者，不是架构参与者"）的可执行证据。

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

> 二审新增两项已落入本文：§2.8 **Demo 数据真实性声明**；§3/§4 **单页 HTML Dashboard 提前至 P0-11.2**（替代原 Vue 三端 SPA）。

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
