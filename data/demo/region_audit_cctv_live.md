# 各区域真实运行态审计（CCTV 真实视频，P0-11.4 当前分支）

> 审计方法：网关 `http://127.0.0.1:8765` 实时运行，喂入真实 `data/demo/CCTV_Surveillance_Final.mp4`（484 帧，循环）。
> 直接抓取 WebSocket 广播的真实 `frame` view-model（97 帧 + 500 帧分布 + 专项抓 active_warnings 稀疏度）+ 读 `gateway.py`/`FrameResult`/`bridge.py`/`index.html` 渲染代码。
> 真实数据：500 帧中 280 帧有检测（max 2）、仅 6 帧有 visitor_event、仅 5 帧有 perception_event（98%+ 为空）；3 个告警均为 LOW / NOTIFY_FAMILY 推荐 / LOG_ONLY 实际执行 / CONFIRMED。

> **⚠️ 本次纠正（2026-07-21 15:xx）**：用户反馈"风险解释卡片根本没正常渲染"。核查确认——**上一版审计把区域③ 判为"✅ 工作良好"是误判**（基于静态读代码，未验运行态）。真实情况是 **③ 风险卡几乎不渲染、④ 闭环卡片同样闪现**，共享同一根因：网关每帧广播的 `active_warnings` 是**当前帧瞬时值**（98%+ 为空），前端**逐帧无条件覆盖**且无跨帧保活。详见 ③ 节与末尾根因。

## ① 实时视频 —— ✅ 正常工作
- 真实做了什么：base64 JPEG 逐帧渲染（284 帧解码、帧循环正常）；覆盖层 `帧/模拟时间/检测数/访客事件数` 全部由真实数据填充（det 多数 1–2、ve 有人进出时跳变）。视频是**真实录像帧**，非合成。
- 不足 / 缺口：
  - **没有检测框叠加**：`FrameResult` 只暴露 `n_detections`（计数），**没有 bbox 坐标字段**（pipeline.py:113,318）。前端只能显示"检测 N"，无法在画面上画框。要画框需改冻结 `FrameResult` 契约 —— 属冻结层改动，P0-11 MVP 不应做。
  - 覆盖层"访客事件"只在 enter/leave 瞬间非零，大部分时间显示 0（非 bug，是数据本身稀疏）。

## ② AI 时间线 —— ⚠️ 实质性空转（最大缺口）
- 真实做了什么：渲染逻辑读 `view.perception_events`（`renderTimeline`，index.html:557）。**但真实 `perception_events` 98%+ 为 null**——pipeline 只在风险触发那几帧才把 `abnormal_dwell` 等塞进去（pipeline.py:304,320 几乎恒为 `[]`）。
- 结果：时间线 99% 时间显示"无触发事件"，**真正连续的 AI 活动（YOLO 检测、访客 enter/leave）根本没进区域②**。标题写"PerceptionEvent 流"但实际几乎无流。
- 根因：前端把"AI 时间线"绑定到稀疏的 `perception_events`，而检测/访客是高频、风险事件是低频——两者都该在时间线呈现，当前只接了低频那份。
- 修复方向（需决策，见下）：把 visitor_event（enter/leave）+ 检测活跃帧也纳入区域②；或明确区域② = "风险事件时间线"并改名。

## ③ 风险解释卡片 —— ❌ 实质性不渲染（前次审计误判，本次纠正）

> **纠正**：上一版审计写"✅ 工作良好"是**基于静态读代码的错误结论**——`renderRisks` 函数写法完全正确，只要拿到数据就能渲染。但在**真实运行态**下它几乎永远拿不到数据，所以用户看到的是"根本没渲染出来"。

- **真实现象**：区域③ 绝大多数时间显示"当前无活跃风险"，风险卡只在告警触发的那一两帧**闪现**后立即被清空——肉眼几乎捕捉不到，等同于"没渲染"。
- **根因（数据时序，非渲染逻辑）**：
  - 网关 `gateway.py:183` 每帧执行 `active_warnings = collect_active_warnings(view["warnings"])`。
  - `view["warnings"]` 是**当前帧**的告警——pipeline 只在 WarningEvent **触发的那一帧**把它放进 `view["warnings"]`，其余 **98.9% 的帧恒为 `[]`**（**实测**：连续抓 800 帧，仅 **9 帧**带 `active_warnings` = **1.1%**，9 个不同 warning_id，命中帧号 4067/4204/4223/4285/4344/4551/4688/4707/4769，彼此间隔几十到上百帧）。
  - `collect_active_warnings`（bridge.py）只是**对单帧列表做过滤，不做跨帧累积**。
  - 前端 `handle()`（index.html:541）每帧 `state.activeWarnings = msg.active_warnings || []` **无条件覆盖** → 98%+ 的帧被覆盖成空数组 → `renderRisks`（index.html:641-644）走 `if (!list.length)` 分支显示"当前无活跃风险"。
  - **本质**：这是与区域②**同一类 bug**——高价值但低频的事件被绑到"逐帧覆盖"的展示状态上，没有累积/保活机制。
- **连带影响（重要）**：区域④ 的**任务卡也由 `state.activeWarnings` 驱动**（`renderClosure` index.html:691 `var list = state.activeWarnings`），所以**区域④ 的卡片同样随每帧闪现消失**（`commandMap` 虽累积了命令，但卡片外壳由 activeWarnings 决定是否显示）。修好区域③ 的数据保活，区域④ 卡片持久化问题一并解决。
- **修复方向**（纯前端，不碰冻结层，与区域④ `commandMap` 累积一致）：
  - 前端新增 `warningMap`（按 `warning_id` 跨帧累积告警），收到非空 `active_warnings` 时 upsert，`status` 变为 `RESOLVED`/`REJECTED` 时移除；`renderRisks`/`renderClosure` 改读 `warningMap` 的聚合列表而非当前帧的 `state.activeWarnings`。
  - 可选加保活 TTL/上限，避免长 loop 无限堆积。
- **其余不足**（次要，修完保活后才看得到）：
  - `evidence` 字段**恒为空**（warning.to_dict 未填证据）→ 证据 chip 永不出现。
  - 推荐 `NOTIFY_FAMILY` 但真实动作仅 `LOG_ONLY`，卡片建议与真实执行不一致（见④根因）。

## ④ 行动闭环 —— 🟡 半工作 + 卡片同样闪现（受③ 根因连带）
- 真实做了什么：按 `warning_id` 跨帧累积 `commandMap`，三端任务卡与区域③联动。3 个告警的 **LOG_ONLY 任务卡**可渲染（payload：风险等级+原因）。stepper/上行按钮/路由面板(payload 预览)均就位。路由面板计数用 `allRouted()`（跨帧累积）**能稳定显示**，不受闪现影响。
- **连带缺口（与③ 同根因）**：任务卡外壳 `renderClosure` 读 `state.activeWarnings`（index.html:691），与③ 一样 98%+ 帧为空 → **闭环卡片同样闪现即消失**。修③ 的 `warningMap` 保活后一并解决。
- 不足（关键）：
  - **家属端 / 社区端任务卡恒为占位**（"本风险未触发家属通知"）——因为当前全是 LOW→`MONITOR`→只路由 `LOG_ONLY`，`SEND_FAMILY_MESSAGE`/`CREATE_COMMUNITY_TASK` 从未产生。这是**数据驱动缺口**：P0-11.5 调阈值（repeat_visit_count 3→2）触发 HIGH 后会自动填充，**前端无需改**。
  - **建议 vs 真实不一致**：卡片显示"建议：通知家属核实"，但同时 LOG_ONLY 卡显示"已记入"——用户会困惑"到底通知了没"。根因：决策策略推荐 `NOTIFY_FAMILY`，但 dispatcher 降级为 `LOG_ONLY`（无真实 executor），warning 还被自动 `CONFIRMED`。属架构/策略层，非前端。
  - `stateMap`（`DemoStateStore` 快照）恒为 `{}`——因为无人点按钮触发 `upsert`；stepper 永远停"待处理"。闭环交互是**真功能但无数据驱动**（全部自动 CONFIRMED 后没人需要手动确认）。

## ⑤ 架构图 —— ✅ 静态正确
- 真实做了什么：SVG 展示冻结消费边界（home_perception → gateway → dashboard 5 区域）。纯静态，无数据依赖。
- 不足：文案仍写"CAVIAR fixture (jpg 帧)"，与当前真实 MP4 输入不符； declarations 仍说"CAVIAR 公开 fixture"。属 stale 文案，建议 P0-11.5 顺手改。

---

## 缺口优先级（供决策，已按本次纠正重排）
| 区域 | 状态 | 缺口 | 修复归属 | 是否冻结构架 |
|---|---|---|---|---|
| ③ | ❌**不渲染** | active_warnings 逐帧覆盖→风险卡闪现即消失 | **前端加 `warningMap` 按 warning_id 保活**（同 ④ commandMap 思路） | 否（纯前端） |
| ④ | 🟡+闪现 | 卡片外壳同读 activeWarnings→同样闪现 | 同③（修③ 一并解决卡片持久化） | 否（纯前端） |
| ② | ⚠️空转 | 时间线几乎恒空（同类根因：低频事件逐帧覆盖） | 前端把 visitor/检测纳入 **或** 明确为风险时间线 | 否（前端+展示语义） |
| ① | ✅ | 无检测框 | 需改 FrameResult（不建议 MVP 做） | 是（冻结层） |
| ③ | 次要 | evidence 恒空 | warning.to_dict 填证据 | 否（冻结层但低风险） |
| ④ | 🟡 | 家属/社区卡恒占位 | P0-11.5 调阈值触发 HIGH（自动填充） | 否（数据驱动） |
| ④ | 🟡 | 建议≠实际动作 | 决策/dispatch 策略层 | 否（策略层） |
| ⑤ | ✅ | 文案写 CAVIAR | 改静态文案 | 否 |

**结论纠正**：上一版把区域③ 判为"✅ 工作良好"是**误判**（静态读代码得出，未验运行态）。真实情况是 **③ 几乎不渲染、④ 卡片同样闪现**，二者共享同一根因——**低频高价值事件（告警）被绑到逐帧覆盖的展示状态上，缺跨帧保活**。这是当前**最该立刻修**的问题（用户体验最差、纯前端可解、一次修好 ③④ 两个区域），优先级高于区域② 空转。区域② 是同类问题的另一实例（低频 perception_events 逐帧覆盖），可同批处理。

### 一句话根因
> 网关每帧广播的 `active_warnings` / `perception_events` 是**当前帧瞬时值**（98%+ 为空），前端却用它**无条件覆盖**展示状态，没有像区域④ `commandMap` 那样按 `warning_id` 跨帧累积/保活 → 风险卡与闭环卡"闪一下就没"。
