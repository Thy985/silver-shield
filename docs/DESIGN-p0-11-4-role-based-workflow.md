# P0-11.4 协同闭环模拟 · 三视图设计文档

> 状态：**Approved**（Owner 二审收敛 2026-07-22） · 范围：**多角色协同闭环模拟（Role-based Workflow Demo）**
> 对应 ADR：[ADR-0017](../ADR/0017-p0-11-role-based-workflow-demo.md) · 上游：[ADR-0015](../ADR/0015-p0-11-demo-architecture.md) 冻结合约

## 1. 背景与目标

P0-11 走完 P0-11.3.5（Demo 运行时生命周期 + 状态面板）后，事实层已能稳定产出
HIGH + family + community 闭环命令（[PR #50](https://github.com/Thy985/silver-shield/pull/50)）。
P0-11.4 解决"**展示层**"——让同一份 DemoAggregateState 被**三个角色视角**共享消费，
形成可视化、可点选的协同闭环。

**核心命题**：风险被发现后，**信息正确分发** + **人类介入形成闭环**。
评委三问：① 风险被看到？ ② 风险有解释？ ③ 发现后有行动？

**非目标**（明确不做，归 P1）：
- 真实家属 App / 社区 Web（独立前端）
- 用户体系、登录、权限
- 推送通道、短信网关、MQTT 真实落地
- 多设备、多住户、多社区
- React/Vue 等前端框架（保持原生 HTML/CSS/JS，零依赖）

## 2. 现状盘点（已就绪，零新增后端契约）

| 能力 | 实现位置 | 说明 |
|---|---|---|
| 服务端权威聚合状态 | `silver_demo.state.DemoAggregateState` | 累积 warning/behavior/command，提供 `snapshot()` / `meta()` / `ingest()` |
| 帧循环 + WS 广播 | `silver_demo.gateway.run_loop` → `bridge.frame_result_to_view` | 每帧 ingest + state_update 广播 |
| 首连 snapshot | `silver_demo.gateway._on_ws_connect` | 新连接收历史 |
| 三桶路由 | `silver_demo.bridge.route_commands` | 按 ActionCommand.type 分 family / community / log_only |
| 反馈闭环 | `silver_demo.ws.handle_upstream` | sendAction → store.upsert → 广播 state_update(pending/family_handled/community_done) |
| 状态面板 | `dashboard.renderStatus` | Source/Frame/Loop/Pipeline/Last Warning/Session |

**结论**：P0-11.4 是**纯视图重组**——把区域④的 family/community 桶提升为独立 Tab，
视频①/架构图⑤仅留 Tab ①（风险发现）。**零新增后端、零新增 home_perception import。**

## 3. 目标 UI：三视图 Tab（阶段叙事）

### 3.1 Tab 命名（**阶段，不是角色**）

```
┌─① 风险发现─┬─② 家属确认─┬─③ 社区处置─┐
│  风险卡片   │  家属通知   │  社区任务   │
│  视频窗口   │  「已确认」  │「完成核验」 │
│  状态面板   │            │            │
└────────────┴────────────┴────────────┘
       ↑ 同一 warning_id 流过
```

**关键决策**：Tabs 命名为**阶段**（发现→确认→处置），不是**角色**（AI中心/家属端/社区端）。
- 阶段叙事天然引导评委左→右点完三步 = "我跟着走完了一个完整处置"
- 角色叙事让人问"为什么我要切来切去"
- 切换 Tab **不重连/不重订 WS**（同一 WS 推送，前端按角色过滤消费）

### 3.2 按钮方案 A：**每视图单按钮**

| Tab | 按钮文案 | 触发命令 | store 状态翻转 |
|---|---|---|---|
| ① 风险发现 | （无按钮，纯观察） | — | — |
| ② 家属确认 | 「已确认」 | `sendAction` type=family | `family_handled` |
| ③ 社区处置 | 「完成核验」 | `sendAction` type=community | `community_done` |

**为什么单按钮**：规避"三按钮点哪个结果都一样（family_handled/community_done）"的演示穿帮。
多动作差异化（联系老人 / 通知社区 / 派单）留 P1。

## 4. 数据映射

### 4.1 前端 state 消费同一份 `DemoAggregateState.snapshot()`

```text
snapshot()
  ├─ meta()           → 状态面板（所有 Tab 共享顶部）
  ├─ active_warnings  → ① 风险发现 卡片列表
  ├─ perception_events→ ① 风险发现 行为时间线
  ├─ all_warnings     → ① 风险发现 历史
  ├─ routed.family[]  → ② 家属确认 列表
  ├─ routed.community[]→③ 社区处置 列表
  └─ routed.log_only[]→ ① 风险发现 折叠区（仅显示，不操作）
```

**警告 ID 透传**：`warning_id` 在三视图中保持一致——这是验收的关键契约。
评委点开①的风险卡 → 切到②时，该 warning 对应的 family 命令**自动出现在②列表**。

### 4.2 反馈单向流转

```
用户点「已确认」(②)
  → sendAction(type=family, warning_id=w_xxx)
  → WS 上行
  → handle_upstream → store.upsert(command_id, status=family_handled)
  → 广播 state_update（仅 status 字段变化）
  → 前端 ② 按钮置灰 / 状态变 "已处理"
  → ① 风险卡片的 status 联动更新
```

**约束**：feedback 单向（用户操作→store→广播），不重发历史 snapshot。

## 5. 实施计划

### 5.1 文件改动清单

| 文件 | 改动 | 性质 |
|---|---|---|
| `silver_demo/dashboard/index.html` | 加 Tab 容器（3 视图 + 切换控件） | 视图 |
| `silver_demo/dashboard/styles.css` | Tab 样式 + 视图布局 | 视图 |
| `silver_demo/dashboard/dashboard.js` | 拆 render 为 `renderTab(role)`；Tab 切换不重订 WS | 视图 |
| `tests/demo/test_dashboard_p0_11_4.py`（已有骨架，扩） | WS 契约 + snapshot 恢复 + feedback 流转 | 测试 |
| `docs/DESIGN-p0-11-4-role-based-workflow.md` | **本文档** | 文档 |

### 5.2 关键 JS 改动（dashboard.js）

```javascript
// 状态消费：同一 WS，同一 state
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "snapshot" || msg.type === "state_update") {
    Object.assign(state, msg.data);
    renderAllTabs();   // 三个 Tab 都重渲染
  }
  if (msg.type === "frame") renderVideo(msg.data);
};

function renderAllTabs() {
  renderTab1_Discover();   // 风险卡 + 视频 + 状态
  renderTab2_Family();     // family 桶 + 「已确认」
  renderTab3_Community();  // community 桶 + 「完成核验」
}

function switchTab(idx) {
  document.querySelectorAll(".tab").forEach((t, i) =>
    t.classList.toggle("active", i === idx)
  );
  // 不重连 WS、不重订——数据是同一份
}
```

### 5.3 关键 HTML 改动（index.html）

```html
<nav class="tabs">
  <button class="tab active" data-idx="0">① 风险发现</button>
  <button class="tab" data-idx="1">② 家属确认</button>
  <button class="tab" data-idx="2">③ 社区处置</button>
</nav>

<section class="view view-discover"> ... 视频 + 风险卡 + 行为时间线 ... </section>
<section class="view view-family"   hidden> ... family 桶 + 「已确认」 ... </section>
<section class="view view-community" hidden> ... community 桶 + 「完成核验」 ... </section>
```

## 6. 测试策略（无浏览器，pytest 守数据契约）

### 6.1 WS 数据契约（tests/demo/test_dashboard_p0_11_4.py）

| 测试 | 断言 |
|---|---|
| `test_routed_commands_three_buckets_arrive` | snapshot 包含 routed.family / routed.community / routed.log_only 三桶 |
| `test_same_warning_id_across_three_views` | 同一 warning 在 family/community 桶的 warning_id 字段一致 |
| `test_snapshot_recovery_after_reconnect` | 晚连恢复历史，routed 桶非空 |
| `test_feedback_unidirectional` | sendAction(已确认) → 仅 status 字段变化，不重发 snapshot |
| `test_tab_switch_no_ws_reconnect` | （前端手测/JS 静态检查）切 Tab 不调 new WebSocket() |

### 6.2 JS 语法

```bash
node --check silver_demo/dashboard/dashboard.js
```

### 6.3 冻结合规

`tests/demo/test_freeze_boundary.py` 持续守：`silver_demo` 的 home_perception.* import
仍仅来自白名单子模块（本次零新增 import，预期直接通过）。

## 7. 冻结合规（ADR-0015 / 0017）

- **零新增** `home_perception.*` import
- **零新增**后端契约（`WarningEvent` / `ActionCommand` / `FrameResult` 仅消费，不改）
- 桥接层 `bridge.route_commands` 不变（已有三桶）
- WS 协议不变（snapshot / frame / state_update / source_switched / session_reset）
- Dashboard 静态资源（HTML/CSS/JS）由 `silver_demo.gateway` 现有 StaticFiles 托管

## 8. 验收

| 验收项 | 测量方法 |
|---|---|
| 同一 warning_id 流过三视图 | pytest `test_same_warning_id_across_three_views` |
| 三桶在 snapshot 中到达 | pytest `test_routed_commands_three_buckets_arrive` |
| 切 Tab 不重连/不重订 WS | 浏览器 DevTools Network + pytest 静态检查 |
| ② 点「已确认」→ store 状态 family_handled → ① 联动 | pytest `test_feedback_unidirectional` + 手测 |
| ③ 点「完成核验」→ community_done → 闭环标记 | pytest + 手测 |
| 冻结合规不破 | `tests/demo/test_freeze_boundary.py` 持续 ✅ |
| 阶段叙事（评委左→右走完三步） | 5 分钟手测 / P0-11.5b 剧本 |

## 9. 决策记录（ADR-style）

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| D1 | Tabs = 阶段（发现/确认/处置），非角色 | AI中心/家属端/社区端 | 阶段叙事强制评委走剧情；角色叙事像"三个网页" |
| D2 | 每视图单按钮 | ② 三按钮（已读/已联系/已派单） | 防同效穿帮；多动作差异化留 P1 |
| D3 | 零新增后端、零新增 import | 改 bridge / 新加 WS 消息 | 事实层已稳，展示层是视图重组 |
| D4 | 切 Tab 不重订 WS | 切 Tab 重连 | 同一份 state 多角色共享 = 协同系统而非多端 |
| D5 | 5b 拆 5a/5b，先 5a 再 4 | 先 4 再 5a | 无稳定 HIGH → 家属/社区页空壳，5a 是真正瓶颈 |
| D6 | native HTML/CSS/JS | React/Vue | 零依赖、零构建；评审可控；改动范围小 |

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| dashboard.js 现有逻辑与 Tab 拆分冲突 | 先读 `frame_result_to_view` + 现有 render，diff 化重构 |
| 测试环境无浏览器，UI 行为难验 | pytest 守 WS 契约；JS `node --check` 守语法；手测留 5b 剧本 |
| 评委网络抖动导致 snapshot 漏收 | 已有首连 snapshot（P0-11.3.5）+ 反馈单向流转 |
| 演示中切 Tab 太频繁 | 阶段命名引导左→右；按钮置灰后无操作可点 |

## 11. 与上下游的关系

- **上游**：P0-11.3.5（DemoAggregateState 单一事实源 + WS snapshot）—— **已完成**
- **下游**：P0-11.5b（5 分钟剧本：固定切 Tab 时机 + 点按钮时机 + 口播）—— **待 5a/4 完成后做**
- **路线**：4 → 5b → 停（不扩 App/ReID/RTSP/voice）
