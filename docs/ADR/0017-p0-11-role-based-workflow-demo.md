# ADR-0017: P0-11 协同闭环 Demo 范围收敛（多角色协同闭环模拟 · 非三产品）

- **状态**：Accepted（Owner 决策 · 2026-07-22）
- **前置**：ADR-0015（P0-11 MVP Demo 架构）、ADR-0016（P0-11.3.5 Demo Runtime Lifecycle）
- **作用域**：仅 Demo 展示层（P0-11.4）的范围与术语收敛；不改动任何冻结契约。

## 1. 背景（Background）

P0-11 的目标是把已冻结的 AI 链路（VisitorEvent → PerceptionEvent → WarningEvent →
ActionCommand）翻译成一次可信的「风险发现 → 解释 → 干预 → 闭环」故事，对外可验证。

在评审 P0-11.4（原「三端闭环」）时，Owner 指出一个**范围漂移风险**：如果把「家属端 /
社区端」理解为独立的真实产品（家属 App、社区 Web 管理系统、用户体系、推送、登录、权限、
数据同步），Demo 会滑入一个**新产品工程**，而不是验证银龄盾核心价值——并且会掩盖前期
在 ABC / Schema / Contract Test 上的架构治理投入。

关键事实：**ADR-0015 与 ADR-0016 早已规定「三端」= 单页 HTML 内的多角色逻辑拆分，不是
三个独立应用**（见 ADR-0015 §2.2「三端是逻辑消费拆分，渲染为一个 HTML 观察窗口内的区域」、
ADR-0016 §7「三角色视角…不是三个独立应用」）。本 ADR 是把这一约定**正式命名、重编号、并补
充评委视角与架构价值论证**，消除「三端 = 三产品」的歧义。

## 2. 决策（Decision）

### 2.1 术语收敛（最重要）

- **不叫**「三端闭环 / 三端开发」。
- **改叫**「**多角色协同闭环模拟（Role-based Workflow Demo）**」。
- 「三端」一律 reinterpret 为「单 Dashboard 内的多角色逻辑拆分」：
  **AI 风险中心 / 家属端 / 社区端**，三者共享**同一份** `DemoAggregateState`，
  仅做**角色视图投影（role-view projection）**，不是三个独立系统。

### 2.2 形态收敛（方案一 · 最推荐）

```
                 AI 风险中心
                    |
              WarningEvent
                    |
        -----------------------
        |                     |
        ↓                     ↓
     家属视角               社区视角
   （一个页面）          （一个页面）
```

- **单 HTML Dashboard + 顶部 Tabs**：`[AI风险中心] [家属端] [社区端]`，切换即可。
- **不构建**：家属 App（Flutter/RN）、社区 Web 管理系统、用户体系、推送服务、登录、
  权限、数据同步后端。这些是 P1 产品验证（真实老人 / 真实家庭 / 真实社区工作人员）才值得做。
- 前期在 `bridge.py` 已完成的路由（`SEND_FAMILY_MESSAGE → family` /
  `CREATE_COMMUNITY_TASK → community` / `LOG_ONLY → log_only`）**就是多角色消费的工程实现**，
  P0-11.4 做的是把它用三个 Tab 视图呈现出来，不是新增后端。

### 2.3 证明目标（Demo 应该证明什么）

> 风险被发现后，信息能够被**正确分发**，并且有**人类介入**形成闭环。

即：`WarningEvent → ActionCommand → 模拟通知 → 人工确认`。
**不是**证明「我们已经开发完成三个完整产品」。

### 2.4 评委三问（Demo 评价标准）

1. **AI 有没有理解行为？** —— `visitor_id` / `trajectory` / `duration` / `repeat_visit` /
   `risk reasoning`（核心）。
2. **风险有没有解释？** —— `reason_summary` / `trigger_events`（第二核心）。
3. **发现风险以后有没有产生行动？** —— `WarningEvent → ActionCommand → 模拟通知 →
   人工确认` 即可。

### 2.5 P0-11 阶段重编号

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| **P0-11.1** | Dashboard 基础（FastAPI Gateway + WebSocket base64 + 基础 5 区域） | ✅ 已完成（PR #43） |
| **P0-11.2** | 真实视频输入（VideoFileFrameSource 帧源抽象 + P0-11.3.5 生命周期） | ✅ 已完成（PR #46 / #48） |
| **P0-11.3** | 风险解释层（WarningEvent/ActionCommand 风险卡人话原因 + AI 行为时间线） | ✅ 已完成（PR #45 / #46 / #48） |
| **P0-11.4** | **协同闭环模拟（多角色协同闭环模拟 · Role-based Workflow Demo）**：单 Dashboard 三视图 | ✅ 已完成（PR #51 · 本 ADR 范围） |
| **P0-11.5** | 演示脚本（5 分钟闭环故事：CAVIAR 工程 + 真实 MP4 展示双轨） | ✅ 已完成（P0-11.5a 稳定 HIGH + P0-11.5b 剧本 `docs/DEMO-SCRIPT-P0-11-5b.md`） |
| **真实端到端验证** | 无浏览器 E2E 断言（`scripts/e2e_validate_demo.py`：真实 create_app + WS 协议驱动真实视频） | ✅ 已完成（12/12 通过） |

> P0-11.3.5（Demo Runtime Lifecycle，PR #48）是 P0-11.4 的数据地基：
> 服务端 `DemoAggregateState` 单一事实源 + 首连 Snapshot + Reset，让三个角色视图
> 直接消费同一状态，无需各自后端。

## 3. 动机（Motivation）

- **避免范围爆炸**：三个独立产品 = 用户体系 + 推送 + 登录 + 权限 + 数据同步，工程量是
  Demo 的指数级，且不构成比赛核心价值。
- **凸显架构价值**：前期投入的 ABC / Schema / Contract Test 的真正回报，是
  「**一个 WarningEvent / ActionCommand，被多个角色消费者共享**」——
  `感知层 → 状态层(AggregateState) → 事件层 → 决策层 → 角色消费层`。
  如果做三个独立端并把接口硬编码进 App/Web，后面设备 / Agent / 第三方接入都会痛苦；
  现在用「单一事实源 + 角色视图投影」，新增消费者零成本。
- **评委看的是「闭环可信」不是「页面数量」**：把工程资源集中在
  「输入真实化 + 行为可解释 + 闭环可信」三点，而非堆砌前端。

## 4. 后果（Consequences）

### 正面
- P0-11.4 从「三产品工程」收敛为「单页三 Tab 视图」：前端复杂度大幅下降，聚焦讲清闭环。
- 数据结构已就绪（`DemoAggregateState` + `routed_commands` family/community/log_only），
  P0-11.4 主要是**前端视图投影 + Tab 切换**，不新增后端契约。
- 冻结边界零破坏：`silver_demo` 仍只消费白名单，P0-11.4 复用 ADR-0015 既有消费映射。

### 负面 / 约束
- 家属端 / 社区端是「模拟页面」，不是真实可推送的通知（真实推送归 P1）。
- 多角色视图需明确「同一份状态的不同投影」实现方式（建议：单一 `DemoAggregateState` +
  前端按角色过滤，而非每角色独立快照）—— 与 ADR-0016 §7 待决项一致，本 ADR 采纳前者。

## 5. 替代方案（Alternatives Considered）

- **方案二：真做 App/Web（后期）** —— 推迟到 P1 产品验证（真实老人 / 家庭 / 社区），
  届时家属需手机通知/App/推送/联系链路，社区需工单/权限/多老人管理/历史记录。当前不值得。
- **原「三端开发」表述** —— 易被误解为三产品，已弃用术语。

## 6. 与既有 ADR 的关系

- **ADR-0015**：本 ADR 不推翻其「单页 HTML + Vanilla JS + base64 WS」技术选型，
  仅把「三端风险闭环展示层」的术语收敛为「多角色协同闭环模拟」，并补评委视角。
- **ADR-0016 §7 三角色视角**：本 ADR 将其正式命名为 P0-11.4 并纳入阶段编号；
  ADR-0016 §7 的「角色视图投影」待决项，本 ADR 采纳「单一数据源 + 角色视图投影」。

## 7. 验收（P0-11.4 完成标准）

> **实现命名说明（2026-07-22 二审落地）**：最终 Tab 采用**阶段叙事命名**
> `[① 风险发现] [② 家属确认] [③ 社区处置]`（与 §2.3 一致，强制 左→右 故事线），
> 而非本节初稿的角色命名 `[AI风险中心] [家属端] [社区端]`。语义对应关系：
> ① 风险发现 = AI 风险中心（全量视图）、② 家属确认 = 家属端、③ 社区处置 = 社区端。

- 单 Dashboard 顶部 Tabs：`[① 风险发现] [② 家属确认] [③ 社区处置]`，切换无刷新、共享同一运行态。
- ① 风险发现（AI 风险中心）：行为轨迹 + 风险卡（人话原因 + 触发规则）+ 建议动作；显示全部预警。
- ② 家属确认（家属端）：收到银龄盾提醒（时间 / 风险原因 / 单一按钮「已确认」→ family_handled）。
- ③ 社区处置（社区端）：待处理任务（老人 / 风险等级 / 原因 / 单一按钮「完成核验」→ community_done）。
- 三个视图消费同一 `DemoAggregateState`，零新增后端契约；`test_freeze_boundary` 仍绿。

> **实现状态（PR #51）**：以上验收全部达成。方案 A「每视图单一按钮」替代初稿的多按钮
> （避免「三按钮同效」穿帮）；反馈闭环 `sendAction → WS feedback → store.upsert →
> 广播 state_update` 已由 `scripts/e2e_validate_demo.py` 单次点击回写断言覆盖（12/12）。
