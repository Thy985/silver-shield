# 黄金案例作为 Live 产品输入：UX/产品化设计

> **Owner**: SilverShield UX/Pm
> **Status**: 设计中（v0.1）
> **承接**: AGENTS.md §1.2（产品化总原则）/ ADR-0036（统一 Case Viewer）/ DESIGN-live-product-ui-restore
> **配套**: DESIGN-demo-v2-product-restore.md / DESIGN-golden-case-viewer.md

---

## 一、核心原则

> **Golden Case 是输入资产，Live 是产品。**
>
> 评委不应意识到"系统正在播放一个 Golden Case"；
> 他应该看到的是一个**正在工作的居家智能守护系统**，
> 只是它的传感器输入是受控演示素材。

由此推出三条不变量：

1. **Live 路径绝不暴露 Golden Case 工程概念**：scenario_id（如 `golden_stranger_visit`）/ `manifest.yaml` / `expected outcome` / `CI fixture` / `fixture path` 一律不进入主界面。
2. **Provenance 准确标注**：当前 Live 默认显示 `● LIVE · REAL SENSOR`，但 Golden Case 是**受控录制的演示视频 + 合成音频**，并非实时设备流——必须诚实改文案，避免误导评委。
3. **Case Viewer (Artifact `/`) vs Live (`/live`) 保持路径分离**：前者展示"案例研究"（含 manifest / expected），后者展示"产品运行"（实时 + 演示声明）。

---

## 二、当前现状评估（修复前）

| 维度 | 现状 | 用户判断 |
|------|------|----------|
| Provenance banner | `● LIVE · REAL SENSOR`（render.py:71） | ⚠️ 误导——Golden Case 是受控录制，不是真实设备 |
| Golden Case scenario YAML | `config/demo/scenarios/` 缺 stranger_visit / repeated_visit / evidence_insufficient 入口 | ⚠️ 需要补 |
| Audio mix 格式 | `case_a_mix.wav` 是 IEEE_FLOAT (audio_format=3)，`source.py` 不支持 | ⚠️ 需要 source.py 支持 IEEE float |
| YAMNet class_names | 缺失 → 模型退化为 stub | ⚠️ 影响"加入音频后获得的新能力"叙事 |
| Live UI 区域顺序 | ①视频 ②时间线 ③风险 ③.5信号 ④行动 ⑥历史 | ⚠️ 与"5 个认知顺序"不匹配 |
| Audio 在 UI 中的位置 | 折叠区 audio-table | ⚠️ 不可见——声学状态变化是 telephone_risk 的核心叙事 |
| Risk signal UX | `setTimeout` 1.2s 后清空 DOM | ⚠️ 闪烁——应该是状态切换 |
| Timeline 节点文案 | "frame 123: 2 检测, 1 警告"（工程化） | ⚠️ 应是"AI 看到人走过来"（人话） |
| Tab 角色视图 | 显示 device_id / warning_id 等 | ⚠️ 家属应只看到"需要确认"，不暴露工程字段 |

---

## 三、设计目标：5 个认知顺序（UX 骨架）

评委打开 `/live` 后，应依次形成 5 个认知：

### ① 现在发生了什么？（实时画面）

```text
┌────────────────────────────┐
│     ● LIVE · 实时数据         │  ← 右上角 badge（修正后文案）
│ 受控演示输入 · 非 7×24 设备   │  ← 副标题（小灰字）
├────────────────────────────┤
│                            │
│      实时家庭画面            │
│   [video · live_frame]      │
│                            │
│   检测到 2 个人物 · 1 部手机  │  ← live-perception inline 概览
└────────────────────────────┘
Frame 1823 · Case Time 12.4s
```

### ② AI 看到了什么？（感知叙事）

```text
┌────────────────────────────┐
│ AI 正在如何理解              │
├────────────────────────────┤
│ 12.0s  👁 发现老人正在通话    │  ← behavior-timeline 节点
│ 12.6s  🔊 检测到持续语音      │
│ 13.2s  🔊 声学状态出现变化    │
│ 13.8s  ⚠ 状态持续未稳定       │  ← 风险信号节点（如有）
└────────────────────────────┘
```

**关键改动**：从"列出 Frame N"变成"时间锚 + 人话描述"。

### ③ 为什么值得关注？（风险解释）

```text
┌────────────────────────────┐
│ 为什么值得关注              │
├────────────────────────────┤
│ ✓ 检测到电话交互            │  ← trigger chips
│ ✓ 声学状态发生变化           │
│                             │
│ 命中强度 ▓▓▓▓░░░ 0.72       │  ← perception_score 条
│                             │
│ 风险：MEDIUM                │
│ 建议：继续观察              │  ← Decision
└────────────────────────────┘
```

### ④ 当前判断 + ⑤ AI 做了什么？（决策 + 行动）

```text
┌────────────────────────────┐
│ 最近行动                    │
├────────────────────────────┤
│ 家属  — 未触发              │  ← cs-family / cs-community
│ 社区  — 未触发              │
└────────────────────────────┘
```

或升级后：

```text
│ 家属  ✓ 已通知 · 待社区处置  │
│ 社区  — 待接受              │
└────────────────────────────┘
```

### 三端 Tab（角色聚焦）

Tab 切换后，**降维**到角色视角——只展示该角色关心的事，不暴露工程细节：

```text
Tab ② 家属确认
┌────────────────────────────┐
│ 您家老人当前需要确认        │
├────────────────────────────┤
│ AI 检测到持续电话交互       │
│ 当前风险：需要关注          │
│                             │
│      [ 我知道了 ]            │
│      [ 通知社区 ]            │
└────────────────────────────┘

Tab ③ 社区处置
┌────────────────────────────┐
│ 工单 #abc123 · 待接受       │
├────────────────────────────┤
│ 地点：入户门                │
│ 风险摘要：声学状态异常      │
│                             │
│      [ 接受 ]                │
│      [ 完成 ]                │
└────────────────────────────┘
```

---

## 四、关键 UX 修复（按优先级）

### P0-1：诚实 Provenance 标注

**问题**：当前 banner 文案 `● LIVE · REAL SENSOR` 不准确——Golden Case 是受控录制，不是真实设备。

**修复**：`render.py:71` 修改为：
```python
"REAL_SENSOR": "● LIVE · 受控演示输入",  # Live runtime demo
```

并在 `_render_provenance_banner` 副标题加：
```text
非 7×24 真实设备 · 演示素材
```

### P0-2：risk_signal UX 不闪烁

**问题**：`live_stream.js:715-718` `setTimeout` 后 `box.innerHTML = ''` —— 直接清空。

**修复方案**：保留"已解除"状态在 DOM 中，加 `.cleared` class（绿勾），不删除。新 transition 触发时**覆盖**而非追加。

```js
} else if (t === 'cleared') {
  var cur = box.querySelector('.rt-card');
  if (cur) {
    cur.classList.remove('live');
    cur.classList.add('cleared');
    var badge = cur.querySelector('.rt-badge');
    if (badge) { 
      badge.textContent = 'CLEARED'; 
      badge.classList.remove('live');
      badge.classList.add('cleared');
    }
    // ← 不再 setTimeout 清空。保留"已解除"卡直到下一次 transition
  }
}
```

### P0-3：AI 感知叙事（5 认知之 ②）

**问题**：节点文案"frame 123: 2 检测, 1 警告"工程化。

**修复**：`live_stream.js:_humanSummary` 已存在，但只渲染在 verdict 字段。改为把"人话描述"作为节点首字段：

```html
<li class="tl-item" data-step="12.0s" data-ref="...">
  <span class="tl-dot" style="background:#4a90d9"></span>
  <div class="tl-body">
    <div class="tl-head">
      <span class="tl-summary">发现老人正在通话</span>  ← 人话
      <span class="tl-meta">12.0s · vision · perception</span>  ← 技术信息（小灰字）
    </div>
  </div>
</li>
```

### P0-4：声学状态变化专属区域

**问题**：telephone_risk 的核心叙事是"声学状态变化"，但当前 audio 只在折叠区 audio-table。

**修复**：在 5 认知之 ②"AI 正在理解"区域里，**单独**展示：
```text
🔊 声学状态变化
  ├ 0-6s:    正常通话 · F0 稳定 140Hz
  ├ 6-9s:    ⚡ F0 上升至 155Hz（注意力）
  ├ 9-12.5s: ⚡ F0 上升至 170Hz（激动）
  └ 12.5-15s: ⚠ F0 上升至 176Hz（应激）
```

数据来源：`AudioPerceptionEvent.scored_labels` + manifest 中 acoustic_progression。

### P1-1：三端 Tab 降维

**问题**：当前 Tab ②/③ 显示 device_id / warning_id 等工程字段。

**修复**：
- Tab ① 全景（默认）：视频 + 5 认知骨架
- Tab ② 家属：纯对话卡，0 工程字段
- Tab ③ 社区：工单卡，**只显示** 地点 + 风险摘要 + 接受/完成

### P1-2：架构图保持折叠

**现状已正确**：`<details>` 默认折叠（render.py:1206）。保持。

### P1-3：Memory 占位优化

**现状**：`<div>🧠 历史记忆 · 当前案例无历史事件可供引用</div>`（render.py:1200）

**优化**：
```text
历史上下文
─────────────────────
本次通话暂无历史相关事件
```

或（`repeated_visit` 接入后自动）：
```text
历史上下文
─────────────────────
3 天前  首次访客    → MONITOR
昨天    再次出现    → MONITOR
今天    再次出现    → 风险升级
```

UI 结构不变，只是数据驱动。

### P2-1：电话短时长 UX

**修复**：去掉"00:15 / 00:15"倒计时，改用叙事节奏自然看完。

---

## 五、数据通路（Golden Case → Live）

### 已就位（不动）

- `silver_demo.scenarios.ScenarioConfig.audio_path`（line 56）
- `gateway.live_audio_builder` DI 钩子（gateway.py:79）
- `scripts/run_demo.py:_build_live_audio_events`（line 187）
- `gateway._feed_live_audio` 确定性投递
- `live_adapter.py:audio_result_to_live_audio` fail-closed 摄入
- `ProjectionAccumulator._ingest_audio` + `_audio_events` 持久化
- `live_adapter.py:_build_audio_evidence_live` 投影
- WS `evidence_delta.audio` + `case_time` 音频 marks
- 前端 `_applyDelta` 渲染 audio-table + AI 听到

### 需要修复（Gap）

| ID | Gap | 优先级 |
|----|-----|--------|
| Gap-1 | Golden Case 缺 scenario YAML 入口 | P0 |
| Gap-2 | `source.py` 不支持 IEEE_FLOAT (audio_format=3) | P0 |
| Gap-3 | YAMNet class_names 缺失 → telephone 错认 distress_cry | P0 |
| Gap-4 | UI provenance banner 文案"REAL_SENSOR"误导 | P0 |
| Gap-5 | risk_signal setTimeout 闪烁 | P0 |
| Gap-6 | timeline 节点文案工程化 | P0 |
| Gap-7 | 声学状态变化无专属区域 | P1 |
| Gap-8 | Tab ②/③ 显示工程字段 | P1 |
| Gap-9 | UI 区域顺序与 5 认知不匹配 | P1 |

---

## 六、推荐执行路线

### Week 1：UI 修复（产品骨架）
- Day 1-2: 修 Gap-4 / Gap-5 / Gap-6 / Gap-8
- Day 3-4: 修 Gap-7（声学状态区域）
- Day 5:  5 认知骨架重组（Gap-9）
- **周末**: UI 视觉打磨（去掉倒计时、加 subtle badge）

### Week 2：技术修复（数据通路）
- Day 1: 补 scenario YAML（Gap-1）
- Day 2: source.py 支持 IEEE_FLOAT（Gap-2）
- Day 3: YAMNet class_names（Gap-3）
- Day 4-5: 端到端验收（telephone_risk 跑通 → Audio event 是 audio_telephone_persistent）

### Week 3：扩展
- 接入 stranger_visit / repeated_visit / evidence_insufficient 三个 Golden Case
- 写 Playwright 端到端风险路径测试（上传 → 检测 → warning → 任务卡 → 按钮 → state_update → community_done）

### Week 4（可选）：Demo Mode
- 评委模式：自动推进、自动高亮
- **需要 Owner 决策**：是否本期做

---

## 七、Open Questions（需要 Owner 决策）

1. **Provenance 文案**：是否要把"● LIVE · REAL SENSOR"改成"● LIVE · 受控演示输入"？还是保留 REAL_SENSOR 但加副标题"演示素材"？
2. **Demo Mode**：本期是否要做？预算 / 价值评估？
3. **架构图位置**：继续放在折叠 details，还是改成一个独立的"系统原理"页（router 切换）？
4. **YAMNet class_names 文件**：是否存在于某处？是否需要重新生成？

---

## 八、不变量（红线）

无论后续如何改造，以下不变量必须保持：

1. **VM-1 单一事实源**：Live 只消费 `EvidenceProjection`，不引入第二事实模型。
2. **VM-3 冻结边界**：Live Adapter 不 import 任何 `home_perception` 生产包（除白名单）。
3. **VM-9 浏览器只渲染**：前端不推理、不判断风险，所有结论来自服务端。
4. **AC-7 provenance 一等视觉**：每个节点 ref 都带 provenance_kind，但 Live 不暴露 SIMULATED/GOLDEN CASE 字样（避免工程感）。
5. **模块边界铁律**：不输出 fraud/suspect/verdict 字段，audio 仅产 perception。

---

## 九、与已有文档的关系

| 文档 | 关系 |
|------|------|
| `AGENTS.md` §0 模块边界铁律 | 不变量来源 |
| `docs/06_api_contract.md` | Live Adapter 契约 |
| `docs/07_event_schema.md` | EvidenceProjection schema |
| `ADR-0036-unified-case-viewer.md` | Live + Artifact 统一架构 |
| `DESIGN-live-product-ui-restore.md` | 上版 Live UI 设计（5 区域 12 列 Grid） |
| `DESIGN-golden-case-viewer.md` | Artifact 路径展示层设计 |
| `DESIGN-demo-v2-product-restore.md` | Demo 2.0 产品化骨架 |

**本文档覆盖**：Live 路径 + Golden Case 作为输入的产品化专项设计。

---

## 附录 A：当前 Live UI 区域顺序 vs 5 认知顺序

**当前**（render.py:1134-1204）：
```
[① lv-video]              [③ lv-risk]
[② lv-timeline]           [③ .5 lv-signal]
[④ lv-closure]            [⑥ lv-memory]
```

**目标**：
```
[① 实时画面]              [② AI 正在理解]
[③ 为什么值得关注]        [④ 当前判断]
[⑤ AI 做了什么（行动）]
[⑥ 历史上下文（占位/真实）]
```

**改动点**：`_render_live_skeleton`（render.py:1075-1236）的 HTML 结构 + CSS grid。

---

## 附录 B：测试覆盖矩阵

| 类型 | 现有 | 需要补 |
|------|------|--------|
| Playwright acceptance | `tests/visualizer/test_live_acceptance.py` 7 个 | + 风险路径端到端（5 认知顺序逐项验证） |
| JS 契约 | `tests/visualizer/test_live_stream_js.py` | + behavior-timeline 节点文案 |
| Live Adapter | `tests/visualizer/test_live_adapter.py` | + provenance banner 文案断言 |
| 端到端 demo | 无 | 启动 telephone_risk → 验证 audio event 类型 / count |

---

*文档版本：v0.1 | 待 Owner 评审 §七 Open Questions*