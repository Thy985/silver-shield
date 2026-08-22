# Live 产品现状与设计差距报告（2026-08-22）

> **写给 Owner 自己的内部审视。** 当初设计 Live 时画的那张图是对的，但现实是只实现了一半。
> 这份文档不是来粉饰的，是来**认账**的——并把接下来怎么走讲清楚。

---

## 0. 一句话结论

| 维度 | 状态 | 评语 |
|------|------|------|
| **架构总览（§5 12 列 Grid + 阶段叙事 tabs）** | 90% | 骨架对了，今天修了视频框位置（92×21 → 603×339） |
| **事实架构（EvidenceProjection + delta 流）** | 100% | ADR-0014 冻结、未倒退 |
| **真实 Runtime（视频帧 / 检测 / 风险信号 / 行动命令）** | 70% | 视频✓ 音频✓ 检测✓ 风险信号❌ 行动命令❌ |
| **Live UI 规范（§2 表 6 区域 + chips + ? 人话卡 + 信号 + Memory）** | 35% | 6 区域都有雏形，但 ③.5 风险信号 / ⑥ Memory / ① overlay chips 缺失，③ 人话卡未做 |
| **「事实 vs Golden 隔离」** | 100% | Live Adapter fail-closed，无伪造 |
| **可靠性（WS 断开 / 场景切换 / Fixture 缺失）** | 40% | 切换到 cctv ✓，切回 telephone ❌（fixture 缺失），WS 断开无降级 |
| **产品体验（"5 分钟讲完一个完整故事"）** | 50% | 视频在动、感知流涌现、人话卡缺失、信号静默——只能讲一半 |

**一句话：架构骨架是对的（这是当初设计时最值得骄傲的部分），但中间层（Live Adapter 把 runtime 投影到 UI）实现得支离破碎，所以"看到的事实"和"应该看到的事实"之间有缺口。**

---

## 1. 当初的设计是什么（一页纸摘要）

依据 `docs/design/golden-case/DESIGN-live-product-ui-restore.md`（Owner 2026-08-17 批准进入实施）：

**0. 一句话（§0）：** 用旧 Demo 的产品信息架构（阶段叙事 tabs + 6 区域）+ 新 Runtime 的真实事实流（EvidenceProjection + delta），重新构成 Live 产品。

**5. 布局方案（§5）：**
```
┌───────────────────────────────┬─────────────────────────┐
│ ① 实时视频（帧流 + chips）      │ ③ 风险解释卡片（? 人话）   │
│   （8 列）                     │   （4 列）               │
├───────────────────────────────┼─────────────────────────┤
│ ② AI 行为时间线（8 列）         │ ③.5 实时风险信号（4 列）  │
├───────────────────────────────┼─────────────────────────┤
│ ④ 行动闭环（8 列）             │ ⑥ Memory Context（4 列）  │
├───────────────────────────────┴─────────────────────────┤
│ ⑤ 系统原理（How it works · 折叠，次级，不抢主叙事）          │
└─────────────────────────────────────────────────────────────┘
```

**8.2 真实运行断言（最高优先级 · Owner 锁死）：**
> 必须有一条 `Frame N → Perception N → Risk N → Decision N → Action N` 的同帧链路在真实 `/live` 上可见。

**9. 落地计划：**
- PR-A：产品骨架 + 阶段叙事 tabs + 12 列 Grid + Live frame container
- PR-B：③ 风险人话卡 + **`risk_transition` 服务端状态机** + human-readable timeline
- PR-C：④ 行动轻量摘要 + ⑥ Memory（开发/展示态分文案）+ ⑤ 系统原理 + ① overlay chips
- PR-D：真人真机验收（§8.2 真实运行断言）

---

## 2. 现实是什么（实测结果）

依据 `artifacts/telephone_risk_acceptance/acceptance_report.md`（TelephoneRiskBrowserProductAcceptance · Playwright 真实浏览器 + WS 监听 + DOM 探针 + 14 张截图）。

### 2.1 ✅ 已落地的事实（Runtime 真值）

| 事实层 | 证据 | 评语 |
|--------|------|------|
| WS 连接稳定 | 814 条 payload，开 2 / 断 0 / console error 1 / page error 1 | 链路通 |
| frame_tick 持续 | 178 条，frame_index 0→112 | 视觉在动 |
| evidence_delta 持续 | 178 条 | runtime 在跑 |
| perception_delta 持续 | 148 条 | 视觉感知在跑 |
| 真实 bbox | `[529, 259, 860, 821]` conf=0.744 | 真实检测 |
| person_present 真值 | count=1, duration_s=2.88→31.8 | CURRENT STATE 是真值 |
| Audio Runtime 推送 | 9 unique events, provenance=REAL_SENSOR | 真实音频链路 |
| 感知流语义去重 | 148 frames → 1 entry（148:1） | 不刷 F127 |
| LIVE badge 真值 | 当前 runtime 状态 | "不是预渲染快照" |
| 「为什么相信」按钮 | `why-believe-link` 存在可点击 | L5 Trust 入口在 |
| Golden/Runtime 分离 | 黄金标记与 runtime 数据物理分离 | 没污染 |

### 2.2 ❌ 真实运行断言（§8.2）没跑通的部分

| 断言 | 期望 | 实际 | 评语 |
|------|------|------|------|
| Frame 持续变化 | frame_tick 单调递增 | ✓ 0→112 | OK |
| Perception 随 Frame | perception_delta.frame_index 对齐 | ✓ 帧对齐 | OK |
| **Risk 随 Evidence** | risk_transition 由服务端状态机产生 | **❌ 0 RAISED, 2 CLEARED, reason_summary=[]** | **链路断裂** |
| **Action 随 Decision** | decision/action 经 evidence_delta | **❌ 0 warning, 0 command** | **链路断裂** |

**结论：链路断在「Risk → Decision → Action」三段。** 这是为什么「5 分钟讲完一个完整故事」讲不出来的根因——故事讲到一半就静音了。

### 2.3 🔴 发现的 Bug（按严重度）

#### P0 阻塞 / 故事讲不通
| Bug | 证据 | 影响 |
|-----|------|------|
| **`narrative_mode='neutral'` 而非 audio_first** | DOM 属性值 | 场景描述与 UI 状态不符 |
| **audio_runtime 事件未投影到 DOM** | WS 9 unique audio events，DOM audio_table 显示 server-side 预渲染 golden 行 | "AI 听到的声音"用户看不到 |
| **Risk 链路空** | 0 RAISED, 0 warning, 0 command | 故事讲一半就静音 |
| **WS 断开 UI 不降级** | 关闭 ws 后 LIVE badge 仍显示 LIVE | 用户可能误以为系统在观察 |
| **echarts is not defined** | 1 page error | 图表不显示 |

#### P1 体验降级
| Bug | 证据 | 影响 |
|-----|------|------|
| 场景切换后 audio_table 未清空 | 切到 cctv 后电话场景 audio 行残留 | 跨场景污染 |
| 切回 telephone_risk 失败 | 422 (CAVIAR fixture 缺失) | fixture 缺失是基础设施问题 |
| Console error（422 响应） | 1 次 | 干净度 |
| timeline 节点文案工程化（"frame 123: 2 检测"） | DESIGN-live-product-ui-restore §P0-3 | 不是人话 |
| risk_signal setTimeout 后清空 DOM | DESIGN-golden-case-live-product §P0-5 | 信号闪烁 |
| ③.5 实时风险信号独立 section 缺失 | 规范 §4.6 | 无 RAISED/CLEARED 跃迁感 |
| ⑥ Memory Context 独立 section 缺失 | 规范 §4.9 | 不展示历史 |
| ③ 风险解释卡无人话原因 + 触发 chips + 强度条 | 规范 §4.5 | 不像产品 |
| ④ 行动轻量摘要「家属?已通知 / 社区—未升级」缺失 | 规范 §4.7 | 不闭环 |

#### P2 细节
| Bug | 证据 | 影响 |
|-----|------|------|
| lv-now 高度 1825px（waveform + manifest 撑高） | DOM 探针 | 页面过长 |
| CAVIAR fixture 缺失 | `tests/fixtures/doorway` 不存在 | 部分场景回切失败 |
| echo "P2-1" 电话短时长倒计时 | DESIGN-golden-case-live-product §P2-1 | UX |

---

## 3. 与设计文档的差距表

### 3.1 对照 DESIGN-live-product-ui-restore.md（Owner 批准规范）

| § | 区域 | 规范要求 | 现状 | 差距 |
|---|------|---------|------|------|
| §4.1 | header | 标题 + 真实性声明 + WS pill | 标题在，无 WS pill，无真实性声明 | ⚠️ 部分缺失 |
| §4.2 | tabs | ①风险发现 ②家属确认 ③社区处置（默认①，可直切）| ✓ live-tabs.js 已做 | ✅ |
| §4.3 | ① 实时视频 | 8 列 + chips（帧/Case Time/检测/访客事件）| 8 列✓（刚修），chips 缺「访客事件」 | ⚠️ 部分缺 |
| §4.4 | ② AI 时间线 | 8 列 + 人话节点 | lv-perception 4 列，主要是感知流，timeline 节点工程化 | ❌ 大幅差距 |
| §4.5 | ③ 风险卡 | 4 列 + ? 人话原因 + 触发 chips + 强度条 + 建议 | lv-why 8 列，lrk-card 结构简陋（无 ? 列表、无 chips、无强度条）| ❌ 大幅差距 |
| §4.6 | ③.5 风险信号 | 4 列 + **服务端 risk_transition 状态机**（raised/cleared/active）| 在 lv-action 内嵌 | ❌ 完全缺 |
| §4.7 | ④ 行动 | 8 列 + 轻量摘要「家属?已通知 / 社区—未升级」| lv-action 12 列，按钮有，状态摘要无 | ⚠️ 部分缺 |
| §4.8 | ⑤ 系统原理 | 12 列折叠 | ✓ lv-sysarch 12 列折叠 | ✅ |
| §4.9 | ⑥ Memory Context | 4 列 + 展示态文案「暂无历史事件可供引用」| 缺失独立 section | ❌ 完全缺 |
| §4.10 | toast | 右下角飞入 | ✓ LP-4 已做 | ✅ |
| **§5** | **布局** | **12 列 Grid + 阶段叙事 tabs + 左 8/右 4 三层堆叠** | **左 8/右 4 三层结构有，③.5/⑥ 缺失** | **⚠️ 60%** |
| **§8.2** | **同帧链路断言** | **Frame N → Perception N → Risk N → Decision N → Action N 全链路** | **断在 Risk→Decision→Action** | **❌ 50%** |

### 3.2 对照 DESIGN-golden-case-live-product.md

| Gap ID | 描述 | 优先级 | 状态 |
|--------|------|--------|------|
| Gap-1 | Golden Case 缺 scenario YAML 入口 | P0 | ❌ |
| Gap-2 | source.py 不支持 IEEE_FLOAT | P0 | ❌ |
| Gap-3 | YAMNet class_names 缺失 → telephone 错认为 distress_cry | P0 | ❌ |
| Gap-4 | UI provenance banner "REAL_SENSOR" 误导 | P0 | ❌ |
| Gap-5 | risk_signal setTimeout 闪烁 | P0 | ❌ |
| Gap-6 | timeline 节点文案工程化 | P0 | ❌ |
| Gap-7 | 声学状态变化无专属区域 | P1 | ❌ |
| Gap-8 | Tab ②/③ 显示工程字段 | P1 | ❌ |
| Gap-9 | UI 区域顺序与 5 认知不吻合 | P1 | ⚠️ 部分修（今天修了视频框） |

### 3.3 对照 ADR 列表

| ADR | 内容 | 现状 |
|-----|------|------|
| ADR-0014 | 三级冻结（架构/契约/事实架构）| ✓ 未倒退 |
| ADR-0015 | P0-11 Demo 架构 | ✓ |
| ADR-0016 | Demo Runtime 生命周期 | ✓ Reset/Switch |
| ADR-0017 | Role-based Workflow | ⚠️ Tab ②/③ 仍显示工程字段 |
| ADR-0021 | Realtime RiskStream | ⚠️ 状态机未上线（risk_transition 缺） |
| ADR-0026 | 音频感知链路 | ⚠️ runtime 通了，UI 未投影 |
| ADR-0035 | Runtime Evidence Explorer | ⚠️ lv-sysarch 折叠有，但 §4.6 风险信号无独立 section |
| ADR-0036 | 统一 Case Viewer | ✓ 入口收敛 |

---

## 4. 根因分析（为什么会有这么多缺口）

**Owner 自问自答，写下来以免回避：**

1. **架构认知与执行节奏脱节。** 设计时把骨架想清楚了（§5 布局、§8.2 链路断言），但实施时按 P0-11 Demo 节奏走——先把"Live 能跑"当 MVP 验收，"讲故事"留到 P0-11.5b。结果 MVP 跑通后，§8.2 链路断言里 Risk→Decision→Action 这段就**一直停留在 console log 里**（log 显示 0 warning 0 command），没人把它搬到 UI。

2. **layout 被低估了。** 12 列 Grid + 阶段叙事 tabs 是设计美学，但 CSS 实操时只想着「能跑」就 OK。sensor-pair 双列 + lv-now span 4 这样的 CSS 违规能活到现在，是因为没人用 Playwright 真实打开 `/live` 看过一眼。今天是第一次打开。

3. **③.5 / ⑥ 等独立 section 是 P0 阻断项，没在 PR 拆分里单独拎出来。** §9 PR 拆分里只有 PR-A/B/C/D，C 涵盖了 ⑥ Memory，但从未被排期——因为 P0-11.5b "5 分钟 Demo 剧本"先用了 closure-panel 顶替 ⑥，等剧本做完就忘了补 §4.6 风险信号。

4. **audio 链分阶段推进但阶段间断档。** ADR-0026 + ADR-0036 VM-13 Phase A → B → C 是分阶段落地的，Phase A 视觉 Live + Phase B 音频 Live。Phase B 落地（runtime audio event 进入 projection），但 **runtime audio event → DOM 的最后一公里没走完**——Live Adapter 产出了，但 live_stream.js 没消费。

5. **fixture 缺失是基础设施债。** CAVIAR doorway 帧缺失、YAMNet class_names 缺失、telephone_risk raw audio IEEE_FLOAT 不支持——这 3 个是阻塞 audio 链真实跑通的基础设施债。它们都在 `tests/fixtures/manifest.yaml` 里登记，但从未拉过。

**总结一句：** 设计阶段画图很美（§5/§8.2/§4.6/§4.9），实施阶段按 MVP 节奏推进（能跑就行），验收阶段没开真实浏览器（直到今天）。三者之间的 gap 就是这份报告的全部内容。

---

## 5. 接下来的方向（按 PR 拆分）

> **前提：不破冻结架构（ADR-0014）**——产品层增量，不动事实架构。

### 5.1 立即修复（本周内，1-2 天）

| ID | 任务 | 文件 | 优先级 | 状态 |
|----|------|------|--------|------|
| **FIX-1** | lv-now 视频 92×21 → 至少 320×180；sensor-pair 单列堆叠 | `render.py:2518-2525` | P0 | ✅ 已修 |
| **FIX-2** | narrative_mode 投影到 DOM（live_telephone_risk → audio_first） | `live_adapter.py` 或 `live_stream.js` | P0 | ❌ |
| **FIX-3** | audio_runtime 事件投影到 DOM audio-table（替换/补充 golden 预渲染） | `live_stream.js:_applyDelta` | P0 | ❌ |
| **FIX-4** | WS 断开 UI 降级（OFFLINE pill + 文本） | `live_stream.js:ws.onclose` | P0 | ❌ |
| **FIX-5** | echarts 加载失败 | `<script>` 标签顺序 | P1 | ❌ |

### 5.2 PR-A：产品骨架收紧（设计规范执行 · 2-3 天）

按 `DESIGN-live-product-ui-restore §9` 顺序：

- **PR-A.1** — header WS pill + 数据真实性声明（§4.1）
- **PR-A.2** — ① 实时视频 overlay chips（帧/Case Time/检测/访客事件 4 个 chip，§4.3）
- **PR-A.3** — ② ② AI 行为时间线节点人话化（§4.4 + Gap-6）

### 5.3 PR-B：风险人话 + 风险信号服务端状态机（核心 · 4-5 天）

> 这是 §8.2 真实运行断言里断掉的部分。

- **PR-B.1** — 服务端 `risk_transition` 状态机（§4.6 · ADR-0030 边界）
  - `ProjectionAccumulator` 维护 `_risk_state: {raised|cleared|active|none}`
  - 状态转移条件（依据当前 risk_levels 集合 vs 上一帧）：
    - `none → 有 risk_levels` → `raised`
    - `有 risk_levels → none`（且上一态 active）→ `cleared`
    - `有 risk_levels → 有 risk_levels` → `active`
    - 首连无风险 → 不推
- **PR-B.2** — ③ 风险卡 ? 人话原因 + 触发 chips + 强度条（§4.5）
  - reason_summary → ? 列表（同义映射，禁语义扩展）
  - trigger_events → chips（当前未投影 → 待 Live Adapter 接通）
  - perception_score → 强度条（当前未投影 → 待 Live Adapter 接通）
- **PR-B.3** — ③.5 实时风险信号 section（§4.6）
  - 服务端 risk_transition → DOM 卡片（RAISED 红 / CLEARED 灰，不闪烁）
  - 修复 risk_signal setTimeout 清空 bug（Gap-5 / §P0-2）
- **PR-B.4** — audio → risk 链路贯通（修 live_audio.yaml + realtime_risk.decision_enabled=true 或新增 audio rule）

### 5.4 PR-C：行动 + Memory + 系统原理（3-4 天）

- **PR-C.1** — ④ 行动轻量摘要「家属?已通知 / 社区—未升级」（§4.7）
- **PR-C.2** — ⑥ Memory Context 独立 section（§4.9 · ADR-0025）
  - 开发态：Not connected / 展示态：暂无历史事件可供引用
  - 不接入 runtime 时绝不编造 visitor profile（AC-12）
- **PR-C.3** — ⑤ 系统原理（折叠）— §4.8 已基本完成，仅微调

### 5.5 PR-D：真人真机验收（§8.2 真实运行断言 · 1-2 天）

依据 `DESIGN-live-product-ui-restore §8.2`：

```
Frame N → Perception N → Risk N → Decision N → Action N 同帧链路
```

验收项：
1. Frame 持续变化（已有）
2. Perception 随 Frame（已有）
3. Risk 随 Evidence（**PR-B.1 修**）
4. Action 随 Decision（**PR-B.4 修**）

### 5.6 基础设施债（并行 · 1-2 天）

| ID | 任务 | 来源 |
|----|------|------|
| INFRA-1 | 拉 CAVIAR doorway fixture（`python tests/fixtures/download_fixtures.py`） | 切回 telephone 失败根因 |
| INFRA-2 | source.py 支持 IEEE_FLOAT (audio_format=3) | Gap-2 |
| INFRA-3 | YAMNet class_names 拉取 | Gap-3 |
| INFRA-4 | UI provenance banner 修订（"REAL_SENSOR" → "受控演示输入，非 7×24 实时设备"） | Gap-4 |

---

## 6. 优先级矩阵（总览）

| 优先级 | 项目 | 数量 | 工时 |
|--------|------|------|------|
| **P0 必修** | FIX-1~5 + PR-B.1~4 | 9 项 | ~7 天 |
| **P1 应修** | PR-A.1~3 + PR-C.1~3 | 6 项 | ~5 天 |
| **P2 可选** | 基础设施债 + UX 细节 | 4 项 | ~2 天 |
| **验收门** | PR-D（§8.2 真实运行断言） | 1 项 | ~1 天 |
| **总计** | — | 20 项 | ~15 天 |

---

## 7. 给自己的反思

1. **设计要写在能验证的地方。** §8.2 真实运行断言如果写在 `docs/design/...` 里没人看，应该写在 `tests/` 里——加一个 `test_e2e_real_runtime_link.py`，每次 PR 跑一次，发现断点立即报警。今天 Browser Acceptance 报告就是这种性质的产物，应该**常态化**。

2. **layout 不能只靠"看起来对"。** CSS 数字（92×21）只有真实浏览器才能测出来。下次 stage gate 应加一项："Playwright 真实打开 `/live`，目测 + 截图 + DOM 探针 + WS 监听"。

3. **MVP 跑通 ≠ 产品讲通。** P0-11.5b「5 分钟 Demo 剧本」是用 closure-panel 顶替 ③ 风险卡 / ③.5 信号 / ⑥ Memory 的临时拼凑。剧本能跑，但产品层是假的。要诚实承认：「闭环能力」和「产品信息架构」是两个独立维度，前者 M2 周完成，后者到现在还没补齐。

4. **fixture 是基础设施，不是 PR 收尾。** INFRA-1/2/3 阻塞真实 audio 链路跑通，应该和 PR-B.4 平行排期，而不是等它最后才补。

5. **不要再问"当初设计得挺好"——要问"当初设计是给谁跑的"。** 是给评委 5 分钟看的（那 closure-panel 凑合也行），还是给真实用户日常用的（那 §4.6 风险信号 / §4.9 Memory 必须独立 section）。现在承认：要走到后者，下一轮 PR-B + PR-C 是必经之路。

---

## 8. 附录

- **本次验收报告**：`artifacts/telephone_risk_acceptance/acceptance_report.md`
- **本次修复 diff**：`render.py:2518-2525`（lv-now span 4→8, sensor-pair 1fr 1fr→1fr, lv-perception span 8→4, lv-why span 4→8）
- **核心设计文档**：
  - `docs/design/golden-case/DESIGN-live-product-ui-restore.md`（Owner 批准规范）
  - `docs/design/golden-case/DESIGN-golden-case-live-product.md`（修复 Gap 清单）
  - `docs/design/golden-case/ADR-0036-supplement-golden-case-adapter.md`（VM-13 Phase A/B/C 切分）
- **关联 ADR**：0014（冻结）/ 0015（P0-11 Demo）/ 0016（Runtime 生命周期）/ 0021（Realtime RiskStream）/ 0026（音频链路）/ 0030（决策边界）/ 0036（统一 Case Viewer）
- **阶段路线**：`docs/08_roadmap.md` §8.4 v2 演进 + §8.5 Memory 实施进度