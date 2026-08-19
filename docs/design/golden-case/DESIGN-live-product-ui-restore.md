# Live 产品 UI 完整恢复 · 设计文档（以初版 Demo 为 UI 蓝图）

- **状态**：**Owner 已批准进入实施**（产品方向 9/10 · 架构一致性 8.5/10 · 实施可行性 9/10；附 4 点实施前锁死约束，已落入本文档）
- **日期**：2026-08-17
- **决策者**：Owner
- **文档定位**：`DESIGN-demo-v2-product-restore.md`（08-15）的**延续与补全**——前者聚焦"闭环能力恢复"（评委三问 Q3），本文档聚焦"**完整产品 UI 结构恢复**"（初版 6 区域 + 阶段叙事 tabs + toast）。
- **UI 蓝图**：初版 demo（commit `056221a` 的 `dashboard/index.html`，2970 行）
- **相关**：ADR-0015（P0-11 Demo 架构）/ ADR-0036（统一 Case Viewer）/ MEMORY.md「Phase Live-Product」

---

## 0. 一句话结论（Owner 冻结）

> **初版 Demo 提供产品信息架构（产品设计可恢复），当前 EvidenceProjection 提供事实来源（事实架构不能倒退）。**
> 用旧 Demo 的产品信息架构 + 新 Runtime 的真实事实流，重新构成 Live 产品。

```
ADR-0015    → 产品结构 / 交互叙事
ADR-0036    → 事实边界 / Projection / provenance
Live Runtime → 实时输入 / perception / risk / decision / action
                ↓
          EvidenceProjection + delta
                ↓
          Live Product UI
```

这次不是再造一个"更漂亮的 Case Viewer"，而是**把真实 Runtime 已经拥有的能力，重新组织成一个人能理解的实时智能体界面**。

### 0.1 实施前必须锁死的 4 件事（Owner 评审约束）

1. **`risk_delta` 的 RAISED/CLEARED 不能由前端自行猜**——服务端明确产生 `risk_transition` 状态，前端只渲染（§4.6）。
2. **`demo_time` 不新增到 `frame_tick`**——优先复用已有 Case Time（§4.3）。
3. **Frame / Perception / Risk 必须有明确同步标识**（`frame_index` + `case_time` 双标识，红线 §6.5）。
4. **Memory 的"未接入"不作为正式 Demo 的失败感文案**（§4.9 区分开发态 / 展示态文案）。

---

## 1. 问题定义

### 1.1 之前为什么"缝缝补补"

LP-1/2/3/4 是在 ADR-0036「统一 Case Viewer」的**竖向瀑布流**上做增量：
- LP-1 恢复帧流 ✅（但没恢复 overlay chips）
- LP-2/3 加了「实时 AI 状态卡」（**新设计**，不是初版的 ③ 风险解释卡片）
- LP-4 改标题 + 加 toast ✅

结果：画面活了、AI 状态卡有了，但**初版的产品结构（角色 tabs / ③ 风险人话卡 / ③.5 实时风险信号 / ④ 三端路由计数 / ⑤ 架构图 / ⑥ Memory Context）一个都没回来**。这是「在错误的骨架（竖向瀑布流）上贴膏药」，不是「恢复产品」。

### 1.2 根因

ADR-0036「统一 Case Viewer」重构时，把初版 index.html 的**产品结构**连同**旧事实架构**一起推倒了，只保留了「证据展示层」。恢复时，我既不敢 git 恢复旧代码（怕事实架构倒退），又没把初版的产品结构当 UI 蓝图重建——结果是四不像。

---

## 2. 恢复蓝图（初版产品结构完整清单）

| # | 区域 | 初版形态（056221a） | 现状 | 处置 |
|---|------|--------------------|------|------|
| header | 顶部栏 | 标题 + Demo 数据真实性声明 + WS 连接 pill | 有标题（LP-4），无真实性声明/pill | **恢复** |
| tabs | 阶段叙事 tabs | ① 风险发现 / ② 家属确认 / ③ 社区处置（**默认停①，可直接切换，不强制顺序**） | ❌ 无 | **恢复** |
| ① | 实时视频 | `<img>` 帧流 + LIVE badge + overlay chips（帧/Case Time/检测/访客事件） | 帧流有（LP-1），chips 缺 | **补 chips** |
| ② | AI 行为时间线 | 访客行为演化（23:30 检测→23:35 停留→23:40 重复→23:45 HIGH） | timeline 有（evidence_delta） | **改人话 + 语义对齐** |
| ③ | 风险解释卡片 | ✓人话原因 + 建议动作 + 闭环状态（**trigger chips / 强度条本阶段省略，诚实不编造**） | risk_delta 有数据，但只渲染成 ai-why 一行 | **恢复 ✓ 卡片格式** |
| ③.5 | 实时风险信号 | RAISED/CLEARED（**服务端 `risk_transition` 明确产生，非前端猜空**） | ❌ 无 | **恢复** |
| ④ | 行动闭环 | 家属/社区任务卡 + **轻量状态摘要**（✓已通知 / —未升级）+ 按钮 | 按钮有（P0-1），无状态摘要 | **补轻量摘要** |
| ⑤ | 系统原理 | SVG 架构图（**降级为折叠/次级模块，不抢主叙事**） | ❌ 无 | **恢复（降级）** |
| ⑥ | Memory Context | 认知层只读 Shadow | ❌ 无 | **恢复（开发态 Not connected / 展示态"暂无历史记录"）** |
| toast | 事件涌现 | 右下角飞入 | 有（LP-4） | 已做 |

---

## 3. 事实架构铁律（红线，不倒退）

1. **数据源唯一**：`EvidenceProjection`（VM-1）+ delta 流。**绝不恢复** `bridge.frame_result_to_view` 的旧 view model。
2. **WS 协议不回退**：继续用 `frame_tick` / `evidence_delta` / `perception_delta` / `risk_delta` / `state_update`。**绝不恢复** `{"type":"frame","view":{...}}`。
3. **浏览器只渲染，零推理**（VM-9）：检测框、风险、决策全部来自服务端投影。
4. **UI State ≠ Evidence**（DESIGN-demo-v2 §2.1）：交互按钮/状态徽章是 UI/Workflow 态，不进 `EvidenceProjection`。
5. **交互事实回灌证据链**（§2.2）：处置完成 → Resolution 事实 → `ProjectionAccumulator.ingest_resolution` → 重新投影（不回写 projection）。
6. **诚实边界**（AC-12）：无风险 → MONITOR + 继续观察；无 Memory 数据 → 诚实留空 + 标注未接入；绝不编造。
7. **帧-感知-风险同步标识（红线）**：`frame_tick.frame_index` / `perception_delta.frame_index` / `risk_delta.frame_index` 三流对齐，且 `perception_delta` / `risk_delta` 均携带 `case_time`（`frame_index × frame_interval_s`）——画面 Frame N 必须与 Detection N、Risk N 同源同帧，绝不允许"画面 100 帧 / 检测 97 帧 / 风险 94 帧"的错位。

---

## 4. 逐区域设计

### 4.1 header（顶部栏）

- 标题：`银龄盾 · 居家智能守护中`（已做 LP-4）
- 副标题：`AI 正在实时理解居家状态：谁在门口 → 发生了什么 → AI 怎么判断 → 系统怎么响应`
- 恢复：WS 连接状态 pill（`未连接` / `已连接` 实时切换，live_stream.js 维护）
- 恢复：Demo 数据真实性声明（角标，点开说明"受控演示输入，非 7×24 实时设备"）

### 4.2 阶段叙事 tabs（恢复 · 核心产品结构）

```
① 风险发现（默认 active）  ② 家属确认  ③ 社区处置
```

- 语义：**产品流程导航**（发现 → 确认 → 处置），不是普通导航——这是初版最有价值的"叙事节奏"设计，解决"用户不知道先看什么"的认知问题。
- **自由度（Owner 收紧）**：**默认停在 ①，但 ②/③ 可直接点击切换，不强制左→右顺序**。避免变成"产品强迫评委看剧本"；既有叙事引导，又不牺牲产品自由度。
- 实现：三个 tab 按钮 + 视图切换（`[hidden]` 切换，同初版）。
- 视图内容：
  - **① 风险发现（主视图）**：区域 ①②③③.5④⑤⑥ 全量展示（AI 中心 + 行动 + 系统原理 + Memory）
  - **② 家属确认**：家属端任务卡（`SEND_FAMILY_MESSAGE` 命令 + `[我知道了] [通知社区]` 按钮）
  - **③ 社区处置**：社区端任务卡（`CREATE_COMMUNITY_TASK` 命令 + `[接受] [完成]` 按钮）

### 4.3 区域① 实时视频（补 overlay chips）

- 已有：`<img>` 帧流（frame_tick.frame_base64）+ LIVE badge + ov-frame
- **补 3 个 chips**（初版 `ov-time` / `ov-det` / `ov-ve`）：
  | chip | 数据源 | 说明 |
  |------|--------|------|
  | 帧 N | `frame_tick.frame_index` | 已做 |
  | Case Time | `case_time`（`frame_index × frame_interval_s`）+ 场景起始时间 → 前端格式化"23:35:12" | **复用已有 Case Time，不新增 demo_time** |
  | 检测 X | `perception_delta.detections.length` | 已做（ov-det） |
  | 访客事件 Y | `evidence_delta.counts.perception_events` | 补 |
- **Owner 收紧**：**不新增 `demo_time` 到 `frame_tick`**——避免把 Case Time / Frame Time / Wall Clock / Demo Time 混在一起，`clock.now()` 对回放/循环/测试/不同机器语义不稳定。业务时间由 `case_time`（帧序×帧间隔）+ 场景起始时间（首屏 descriptor 数据岛提供）派生，前端格式化。

### 4.4 区域② AI 行为时间线（改人话）

- 已有：`evidence_delta.timeline` → `.timeline` 节点列表
- 改进：节点 summary 用人话（"23:35 检测到访客停留异常"而非技术枚举），保留 modality 图标（👁/🔊/⚡）
- **不新增事实**：仍消费 `evidence_delta.timeline`，只改前端渲染文案映射。

### 4.5 区域③ 风险解释卡片（恢复 ✓ 格式 · 核心）

初版 risk-card 结构（对照恢复）：

```
┌─ [HIGH] 风险  · id · 生成 23:40 · [待处理] ─┐
│ 人话原因                                    │
│   ✓ 夜间访问                                 │
│   ✓ 长时间停留                               │
│   ✓ 重复出现                                 │
│ 触发规则：[夜间访问 0.82] [长停留 0.75] …     │
│ 规则命中强度 ████████░░ 0.82                 │
│ 建议：通知家属 / 升级社区                      │
└──────────────────────────────────────────────┘
```

- 数据源：`risk_delta`（risk_levels + reason_summary + recommended_actions）
- **reason_summary → ✓ 列表**（人话原因，`✓ ` 前缀 + 列表项，**无"诈骗/犯罪"字样**，VM-9）
- **语义整理 vs 语义扩展（Owner 收紧 · 红线）**：`reason_summary` **只做语义整理，不做语义扩展**——
  - ✅ 允许：`repeated_visit_detected` → `检测到重复访问`（枚举→人话，同义映射）
  - ❌ 禁止：`疑似陌生人持续尾随老人`（替模型推理，越界 VM-9）
- 触发规则 chips：初版来自 `trigger_events`（WarningEvent 字段），当前 `LiveFrame` 未投影 → **本阶段用 reason_summary 即可，trigger_events 留待后续**（诚实：不编造 trigger）
- 强度条：初版用 `perception_score`，当前未投影 → **本阶段省略强度条**（诚实：无数据不画），或后续补投影
- 建议动作：`recommended_actions` 人话映射（MONITOR→继续观察 / NOTIFY_FAMILY→通知家属 / ESCALATE_COMMUNITY→升级社区）
- 无风险时：显示"🔴 实时观察中 · 当前 N 人在场，风险尚未触发"（初版有此"消除前段空窗"设计，恢复）

### 4.6 区域③.5 实时风险信号（恢复 · RAISED/CLEARED）

- **契约（Owner 收紧 · 实施前锁死）**：**服务端 `risk_delta` 明确产生 `risk_transition` 状态，前端只渲染，绝不自行解释"空 = CLEARED"**——`risk_levels = ()` 可能因"风险解除 / 当前窗口无风险 / 服务暂无事件 / 场景初始化 / 数据流未更新"等多种原因，前端无法区分。
- 服务端语义（`ProjectionAccumulator` 维护 `_risk_state` 状态机）：
  | 服务端判定 | `risk_transition` | UI |
  |-----------|------------------|-----|
  | risk_levels 空 → 非空 | `raised` | RAISED（信号卡亮起） |
  | risk_levels 非空 → 空（且上一状态为 active） | `cleared` | CLEARED（熄卡） |
  | risk_levels 持续非空（内容变化） | `active` | 更新风险内容 |
  | 首连无风险 | 不推（无 transition） | 无信号 |
- 实现：`extract_risk_delta` 返回 `risk_transition` 字段（服务端状态机判定），前端只按 `risk_transition` 渲染 RAISED/CLEARED，不读 `risk_levels` 空/非空猜状态。
- 初版 volatile 语义（主体离场约 1~2 评估周期补发 CLEARED）由服务端状态机保证，不在前端兜底。

### 4.7 区域④ 行动闭环（补轻量状态摘要）

- 已有：家属/社区按钮 + 状态徽章（P0-1 action_closure 面板）
- **Owner 收紧：不做复杂三端路由计数**（ROI 低，评委关心"有没有通知/处置"而非"command count 是 3 还是 4"，不值得改 `evidence_delta.counts` 契约）。
- **补轻量状态摘要**（产品语义优先于工程计数）：
  ```
  行动路由
    家属  ✓ 已通知
    社区  — 未升级
  ```
  - 数据源：`state_update`（P0-1 状态机快照）+ `evidence_delta.counts.commands` 总量（已有，不细分）
  - 前端从状态机快照（`pending → family_handled → community_done`）映射成 ✓/— 状态，不新增命令类型细分计数。

### 4.8 区域⑤ 系统原理（恢复 · 降级为次级模块）

- **Owner 收紧：架构图是"可信度加分项"，不抢主叙事**——真实产品主叙事是"发生了什么 → 为什么 → 怎么办"，不是"PerceptionPipeline → FrameResult → ProjectionAccumulator"。
- 处置：改名 **「⑤ 系统原理（How it works）」**，作为**折叠/次级模块**（默认折叠，点开查看），不与实时视频/风险/行动同等视觉权重。
- 内容：静态 SVG（初版 `viewBox 0 0 960 250` 三框 + 箭头），文案更新到当前架构：
  - 框1：冻结内核（home_perception）：`PerceptionPipeline → FrameResult → WarningEvent/ActionCommand`
  - 框2：Demo Gateway（silver_demo）：`帧循环 → ProjectionAccumulator → EvidenceProjection`
  - 框3：展示层（Case Viewer）：`Live Viewer（帧流 + delta 流）`
- 纯静态展示，无实时数据，无 JS。

### 4.9 区域⑥ Memory Context（恢复 · 开发态/展示态分文案）

- 初版：visitor memory profile（只读认知视图，ADR-0025 C-4/C-6）
- **当前 live 模式未接入 Memory Runtime**（ProjectionAccumulator 无 memory 数据）→ **绝不编造 visitor profile**（AC-12）
- **Owner 收紧：区分开发态 / 展示态文案，避免"功能没做完"的失败感**：
  | 模式 | 无 memory evidence 时文案 |
  |------|--------------------------|
  | **开发模式** | `Memory Context · Not connected`（诚实暴露未接入，内部定位用） |
  | **正式展示** | `历史记忆 · 当前案例无历史事件可供引用`（强调"当前事实没有"，非"系统没接上"） |
- 前提保证：case 确实无 memory 数据时才用展示态文案；一旦接入 Memory Runtime 有数据，直接渲染真实 profile（**这是产品文案，不是事实造假**）。

### 4.10 toast

- 已做（LP-4）：新 timeline 节点 + 风险变化 → 右下角飞入
- 保留，无改动。

---

## 5. 布局方案（12 列 Grid + 阶段叙事 tabs）

复用初版的 12 列 Grid + 阶段叙事 tabs：

```
┌─────────────────────────────────────────────────────────┐
│ header：标题 + 真实性声明 + WS pill                        │
├─────────────────────────────────────────────────────────┤
│ tabs：① 风险发现 | ② 家属确认 | ③ 社区处置（默认①，可直切）  │
├───────────────────────────────┬─────────────────────────┤
│ ① 实时视频（帧流 + chips）      │ ③ 风险解释卡片（✓ 人话）   │
│   （8 列）                     │   （4 列）               │
├───────────────────────────────┼─────────────────────────┤
│ ② AI 行为时间线（8 列）         │ ③.5 实时风险信号（4 列）  │
├───────────────────────────────┼─────────────────────────┤
│ ④ 行动闭环（8 列）             │ ⑥ Memory Context（4 列）  │
├───────────────────────────────┴─────────────────────────┤
│ ⑤ 系统原理（How it works · 折叠，次级，不抢主叙事）          │
└─────────────────────────────────────────────────────────┘
```

- tab ② 家属确认 / ③ 社区处置：切换时只显示对应角色视图（`grid-column: span 12`，初版同款）。
- ⑤ 系统原理默认折叠（`<details>`），不与主叙事同权重。
- toast 固定右下角（fixed 定位）。

---

## 6. 数据流映射总表

| 区域 | 实时数据源 | 是否需 gateway/backend 改动 |
|------|-----------|-------------------|
| ① 帧流 | `frame_tick.frame_base64` | 已做 |
| ① Case Time chip | `case_time`（`frame_index × interval`）+ 场景起始时间（首屏数据岛） | **不新增 demo_time；前端派生格式化** |
| ① 访客事件 chip | `evidence_delta.counts.perception_events` | 已做（counts 已有） |
| ② 时间线 | `evidence_delta.timeline` | 已做 |
| ③ 风险卡 ✓ | `risk_delta`（reason_summary/risk_levels/recommended_actions） | 已做（LP-3） |
| ③.5 风险信号 | `risk_delta.risk_transition`（**服务端状态机判定 raised/cleared/active，非前端猜**） | **需 backend 加 `risk_transition` 状态机** |
| ④ 行动摘要 | `state_update`（状态机快照）+ `evidence_delta.counts.commands` 总量 | 已做（P0-1） |
| ④ 按钮状态 | `state_update` | 已做 |
| ⑤ 系统原理 | 静态（无实时） | 无 |
| ⑥ Memory | 无（开发态 Not connected / 展示态"暂无历史记录"） | 无 |
| toast | `evidence_delta` + `risk_delta` | 已做 |

**backend 唯一改动**：`risk_delta` 加 `risk_transition` 字段（`ProjectionAccumulator` 维护 `_risk_state` 状态机：`raised`/`cleared`/`active`，§4.6）。
**同步标识**：`frame_tick` / `perception_delta` / `risk_delta` 三流均带 `frame_index`；`perception_delta` 已有 `case_time`，**`risk_delta` 补 `case_time`**，形成 frame_index + case_time 双同步标识（红线 §7.7）。

---

## 7. 铁律（红线汇总）

1. **UI 蓝图 = 初版，事实架构 = 现状**——恢复产品结构，不恢复旧 view model / 旧 WS 协议。
2. **UI State ≠ Evidence**——按钮/状态/信号卡是展示层态，不进 `EvidenceProjection`（VM-1）。
3. **交互事实回灌**——处置完成 → Resolution 事实 → 重新投影（不回写 projection）。
4. **浏览器零推理**（VM-9）——框/风险/决策全来自服务端投影。
5. **诚实边界**（AC-12）——无数据即留空/标注，绝不编造（trigger 不编造、Memory 不编造、无风险即 MONITOR）。
6. **不新增事实层**——架构图/时间线/chips 都是已有 EvidenceProjection 的展示投影。
7. **帧-感知-风险同步标识（红线，Owner 锁死）**——`frame_index` + `case_time` 双标识贯穿 `frame_tick` / `perception_delta` / `risk_delta`，画面 Frame N 必须与 Detection N、Risk N 同源同帧。
8. **风险状态由服务端判定（红线，Owner 锁死）**——`risk_transition`（raised/cleared/active）由 `ProjectionAccumulator` 状态机产生，前端只渲染，不自行解释 `risk_levels` 空/非空。
9. **`reason_summary` 只做语义整理，不做语义扩展**——枚举→人话同义映射允许，替模型推理（"尾随""陌生人"）禁止。

---

## 8. 验收标准（DoD）

### 8.1 产品体验（真人打开 `/live`）

```
① 风险发现视图：
   - 视频在动（帧流）+ overlay chips（帧/Case Time/检测/访客事件）实时跳动
   - 时间线用人话涌现（👁 检测到人 → 🔊 持续电话声 → ⚡ 风险升级）
   - 风险卡用 ✓ 人话原因（✓夜间访问 ✓长时间停留 ✓重复出现），无"诈骗/犯罪"字样
   - 实时风险信号 RAISED/CLEARED 随服务端 risk_transition 跃迁
   - 行动闭环用轻量摘要（家属 ✓已通知 / 社区 —未升级）
   - Memory Context 展示态文案"暂无历史记录"（非"未接入"失败感）
   - ⑤ 系统原理折叠在次级模块
② 家属确认视图：直接点 tab 切换 → 家属任务卡 + [我知道了][通知社区]
③ 社区处置视图：直接点 tab 切换 → 社区任务卡 + [接受][完成]
toast：事件涌现时右下角飞入
```

### 8.2 真实运行断言（最高优先级 · Owner 锁死）

**必须有一条 `Frame N → Perception N → Risk N → Decision N → Action N` 的同帧链路在真实 `/live` 上可见**：

1. **Frame 持续变化**：`frame_tick.frame_index` 单调递增，画面持续刷新
2. **Perception 随 Frame 更新**：`perception_delta.frame_index` 与画面帧对齐，检测数随帧变化
3. **Risk 随 Evidence 更新**：`risk_delta.risk_transition` 由服务端产生（raised/cleared/active），`frame_index` 对齐
4. **Action 随 Decision 更新**：decision/action 事件经 `evidence_delta` 涌现，与风险/决策同时间轴

> 否则又会回到"页面很漂亮，但其实是首屏快照"。这是本次恢复的**最高优先级验收项**。

**红线断言**：页面无第二套 view/state 事实模型（浏览器只消费 delta 流）；无旧 `{"type":"frame","view":{...}}` 协议；无编造（trigger/Memory/风险）；无 `demo_time` 字段；`risk_transition` 由服务端产生（前端无空=cleared 推断）。

---

## 9. 落地计划（PR 拆分 · Owner 微调）

| PR | 范围 | 一句话验收 |
|----|------|-----------|
| **PR-A** | 产品骨架 + 阶段叙事 tabs（不强制顺序）+ 12 列 Grid + **Live frame container**（帧流容器占位） | 三个 tab 可直切，主视图骨架 + 帧流容器立起来 |
| **PR-B** | ③ 风险人话卡（✓ 格式）+ **`risk_transition` 服务端状态机（RAISED/CLEARED）** + **human-readable timeline** | **产品最小闭环**：风险卡 ✓ 人话 + 信号随服务端 transition 跃迁 + 时间线人话 |
| **PR-C** | ④ 行动轻量摘要 + ⑥ Memory（开发/展示态分文案）+ ⑤ 系统原理（折叠）+ ① overlay chips | 所有区域补全，一个页面完整呈现 |
| **PR-D** | 真人真机验收（LP-5，§8.2 真实运行断言） | 走通完整产品体验 + 同帧链路 |

依赖：PR-A → PR-B → PR-C 顺序（每步产品价值独立）；PR-D 在 PR-C 后。每个 PR 独立 Conventional commit + Owner review。

> 每个 PR 的产品价值独立：PR-A 立骨架、PR-B 形成"产品最小闭环"（视频 + 风险 + 时间线）、PR-C 补全次要区域。

---

## 10. 一句话总结

> **不再试图把"旧 Demo"和"新架构"二选一，而是用旧 Demo 的产品信息架构（阶段叙事 tabs + 6 区域）+ 新 Runtime 的真实事实流（EvidenceProjection + delta），重新构成 Live 产品。**
> 把真实 Runtime 已经拥有的能力，重新组织成一个人能理解的实时智能体界面。

### 10.1 产品架构（Owner 冻结）

```
LIVE PRODUCT
│
├─────────────┬─────────────┐
│             │             │
REAL-TIME     REAL-TIME     MEDIA & INTELLIGENCE
MEDIA         INTELLIGENCE  对齐（frame_index + case_time）
│             │
Frame N       Evidence N
│             │
└──────┬──────┘
       ↓
  Risk / Decision
       ↓
     Action
```

UI 对应：① 实时视频 ② AI 行为时间线 ③ 风险解释 ③.5 风险状态 ④ 行动闭环 ⑤ 系统原理 ⑥ Memory Context。
