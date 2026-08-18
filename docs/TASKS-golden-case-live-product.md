# Golden Case 接 Live 产品化 — 任务清单

> **配套文档**: `docs/DESIGN-golden-case-live-product.md`
> **Owner**: AI Agent
> **状态**: 待执行（v0.1）

---

## 任务总览

| Phase | 任务 | 状态 |
|-------|------|------|
| Phase 1 | UI 修复（产品骨架） | 🔄 进行中（T1.1/T1.2 已落地） |
| Phase 2 | 技术修复（数据通路） | ⬜ |
| Phase 3 | 扩展 Golden Cases | ⬜ |
| Phase 4 | Demo Mode（待 Owner 决策） | ⬜ |

---

## Phase 1：UI 修复（Week 1）

### T1.1 — 修复 Provenance Banner 文案 [P0] ✅ 已落地（commit f367fec）

> Owner 2026-08-18 决策（DESIGN §七 Q1）：采用「● LIVE · 受控演示输入」+ 副标题方案（诚实标注）。

**文件**：`src/home_perception/visualizer/viewer/render.py:71`

**现状**：
```python
"REAL_SENSOR": "● LIVE · REAL SENSOR",
```

**改为**：
```python
"REAL_SENSOR": "● LIVE · 受控演示输入",
"REAL_SENSOR_SUBTITLE": "非 7×24 真实设备 · 演示素材",
```

并在 `_render_provenance_banner` 增补副标题（小灰字）：
```python
subtitle = _PROVENANCE_TEXT.get(k, '')
if subtitle:
    badge += f"<span class='prov-subtitle'>{_R._esc(subtitle)}</span>"
```

**CSS**：
```css
.prov-badge { display:inline-flex; align-items:center; gap:8px; }
.prov-subtitle { font-size:11px; color:#888; }
```

**验收**：
- [ ] `_render_provenance_banner("delivery_courier_normal")` 产物含 `受控演示输入`
- [ ] `_render_provenance_banner(...)` 产物含 `非 7×24 真实设备`
- [ ] Live mode (REAL_SENSOR) 显示新文案
- [ ] Artifact mode (SIMULATED) 文案不变 `● GOLDEN CASE · SIMULATED`

**测试**：
```python
# tests/visualizer/test_live_shell.py 增加
def test_live_banner_honest_demo_label():
    # 验证 Live mode 的 provenance banner 文案
    ...
```

---

### T1.2 — risk_signal 不闪烁 [P0] ✅ 已落地（commit f367fec）

**文件**：`src/home_perception/visualizer/assets/live_stream.js:707-723`

**现状**：
```js
} else if (t === 'cleared') {
  var cur = box.querySelector('.rt-card');
  if (cur) {
    cur.classList.add('cleared');
    cur.classList.remove('live');
    var badge = cur.querySelector('.rt-badge');
    if (badge) { badge.textContent = 'CLEARED'; badge.classList.remove('live'); }
    setTimeout(function () {
      box.innerHTML = '';   // ← 直接清空（闪烁根源）
      if (empty) empty.style.display = '';
    }, 1200);
  }
}
```

**改为**：
```js
} else if (t === 'cleared') {
  var cur = box.querySelector('.rt-card');
  if (cur) {
    // 状态切换：保留 DOM，加 cleared class，不自动清空
    cur.classList.remove('live');
    cur.classList.add('cleared');
    var badge = cur.querySelector('.rt-badge');
    if (badge) {
      badge.textContent = 'CLEARED';
      badge.classList.remove('live');
      badge.classList.add('cleared');
    }
    // 不 setTimeout 清空。保留"已解除"卡直到下一次 transition 覆盖
  }
}
```

**新增 CSS**（`render.py` 内嵌样式）：
```css
.rt-card.cleared { border-left-color:#94a3b8; background:#f1f5f9; }
.rt-badge.cleared { background:#94a3b8; }
```

**验收**：
- [ ] RAISED → CLEARED transition 时，DOM 不被 `setTimeout` 清空
- [ ] 视觉上能看到"已解除"卡片（绿勾 + 灰背景）
- [ ] 新的 RAISED 触发时**覆盖**旧 cleared 卡（不堆叠）

**测试**：
- Playwright: 手动触发 transition `raised → cleared`，DOM 仍有 `.rt-card` 元素

---

### T1.3 — Timeline 节点文案人话化 [P0]

**文件**：`src/home_perception/visualizer/assets/live_stream.js:298-314` `_buildTimelineNode`

**现状**：
```js
'<li class="tl-item" data-step="..." data-ref="...">'
  + '<span class="tl-dot" style="background:' + color + '"></span>'
  + '<div class="tl-body">'
  + '<div class="tl-head">'
  + '<span class="tl-step">' + _esc(n.timestamp) + '</span>'   // "F123"
  + '<span class="tl-modality" style="color:' + color + '">' + marker + ' ' + _esc(n.modality) + '</span>'
  + '<span class="tl-stage" style="color:' + color + '">' + _esc(n.stage) + '</span>'
  + '<span class="tl-kind">' + _esc(n.type) + '</span>'
  + '<span class="tl-verdict ' + verdictCls + '">' + _esc(_humanSummary(n)) + '</span>'  // ← 已有人话
```

**改为**：
```html
<li class="tl-item" data-step="12.0s" data-ref="...">
  <span class="tl-dot" style="background:#4a90d9"></span>
  <div class="tl-body">
    <div class="tl-summary">发现老人正在通话</div>  <!-- 人话首字段 -->
    <div class="tl-meta muted">12.0s · vision · perception</div>
  </div>
</li>
```

**改动**：
- 首字段改为 `tl-summary`（人话）
- 技术信息（timestamp/modality/stage/type）合并到 `tl-meta` 小灰字

**验收**：
- [ ] 每个 timeline 节点首字段是人话描述（不是 F123）
- [ ] 时间戳在 meta 区域（小灰字）
- [ ] 颜色编码保留（modality dot）

---

### T1.4 — 声学状态变化专属区域 [P1]

**文件**：`src/home_perception/visualizer/viewer/render.py` 新增 `_render_acoustic_state_panel`

**新增 HTML 区域**（放在 ② "AI 正在理解" 区域里）：
```html
<div class="acoustic-state" id="acoustic-state-{sid}" style="display:none">
  <h3>🔊 声学状态变化</h3>
  <ol class="acoustic-timeline" id="acoustic-timeline-{sid}"></ol>
</div>
```

**数据源**：`AudioPerceptionEvent.scored_labels`（tier1 YAMNet 标签 + score）
或 manifest.audio.voice_stressed.acoustic_progression（如电话 case 专门预定义）

**填充逻辑**（JS 端 `live_stream.js`）：
```js
// 当收到 audio event 时，如果 kind 是 voice_stressed 或相关，
// 提取 acoustic_progression 注入 #acoustic-timeline-{sid}
function _renderAcousticProgression(stateChanges) {
  var ul = document.getElementById('acoustic-timeline-' + sid);
  ul.innerHTML = stateChanges.map(function (s) {
    return '<li class="acoustic-phase phase-' + s.phase.toLowerCase() + '">'
      + '<span class="phase-time">' + s.time + '</span>'
      + '<span class="phase-label">' + s.label + '</span>'
      + '<span class="phase-f0">F0 ' + s.f0 + 'Hz</span>'
      + '</li>';
  }).join('');
  document.getElementById('acoustic-state-' + sid).style.display = '';
}
```

**验收**：
- [ ] `telephone_risk` 场景跑起来时，声学状态区域可见
- [ ] 显示 4 个阶段（NORMAL/ATTENTION/AROUSAL/STRESS）
- [ ] 不显示 Golden Case 工程概念（场景ID等）

---

### T1.5 — 三端 Tab 角色降维 [P1]

**文件**：`src/home_perception/visualizer/viewer/render.py:1020-1042` `_render_live_role_view`

**现状**：Tab ②/③ 显示工程字段（warning_id / device_id）

**改为**：

**Tab ② 家属确认**：
```html
<div class="role-view family-view">
  <h3>您家老人当前需要确认</h3>
  <div class="role-card">
    <div class="role-summary">AI 检测到持续电话交互</div>
    <div class="risk-pill medium">需要关注</div>
    <div class="role-actions">
      <button class="role-btn primary">我知道了</button>
      <button class="role-btn secondary">通知社区</button>
    </div>
  </div>
</div>
```

**Tab ③ 社区处置**：
```html
<div class="role-view community-view">
  <h3>工单 #abc123 · 待接受</h3>
  <div class="role-card">
    <div class="role-field"><span>地点</span>入户门</div>
    <div class="role-field"><span>风险</span>声学状态异常</div>
    <div class="role-actions">
      <button class="role-btn primary">接受</button>
      <button class="role-btn">完成</button>
    </div>
  </div>
</div>
```

**验收**：
- [ ] Tab ② 不显示 device_id / warning_id
- [ ] Tab ③ 不显示 trigger_events / perception_score（这些在 ③ 区域）
- [ ] 两个 Tab 都只显示该角色关心的 1-3 个字段

---

### T1.6 — 5 认知骨架重组 [P1]

**文件**：`src/home_perception/visualizer/viewer/render.py:1075-1236` `_render_live_skeleton`

**新 HTML 结构**：
```html
<section class="scenario live-scenario">
  {_render_provenance_banner}  <!-- 已 T1.1 修复 -->
  
  <nav class="tabs" id="role-tabs">
    <button data-view="discover">① 风险发现</button>
    <button data-view="family">② 家属确认</button>
    <button data-view="community">③ 社区处置</button>
  </nav>
  
  <div class="live-view" id="view-discover">
    <div class="live-grid">
      <!-- 5 认知骨架 -->
      <section class="region lv-now">          <!-- ① 实时画面 -->
        <h2>① 实时画面</h2>
        <div class="case-video">{video_inner}</div>
      </section>
      
      <section class="region lv-perception">   <!-- ② AI 正在理解 -->
        <h2>② AI 正在理解</h2>
        <div class="behavior-timeline" id="behavior-timeline-{sid}"></div>
        {acoustic_state_panel}                  <!-- T1.4 -->
      </section>
      
      <section class="region lv-why">          <!-- ③ 为什么值得关注 -->
        <h2>③ 为什么值得关注</h2>
        {lrk_card}                                <!-- trigger chips + 强度条 + Decision -->
      </section>
      
      <section class="region lv-action">       <!-- ④ + ⑤ 决策与行动 -->
        <h2>⑤ AI 做了什么</h2>
        {closure_body}                            <!-- cs-family / cs-community -->
        {live_signals}                           <!-- ③.5 → 移到这里 -->
      </section>
      
      <section class="region lv-history">      <!-- ⑥ 历史上下文 -->
        <h2>⑥ 历史上下文</h2>
        {memory_placeholder}
      </section>
    </div>
    
    <details class="lv-sysarch">
      <summary>系统原理（How it works）</summary>
      {sysarch_diagram}
    </details>
  </div>
</section>
```

**CSS Grid 调整**：
```css
.live-grid { display:grid; grid-template-columns: 6fr 6fr; gap:16px; }
.lv-now { grid-column: span 12; }       /* ① 全宽 */
.lv-perception { grid-column: span 8; }  /* ② + ③ */
.lv-why { grid-column: span 4; }
.lv-action { grid-column: span 12; }    /* ④ + ⑤ 全宽 */
.lv-history { grid-column: span 12; }
```

**验收**：
- [ ] 默认 view 显示 5 认知区域（顺序：① → ② → ③ → ⑤ → ⑥）
- [ ] ⑥ Memory 占位文案"本次通话暂无历史相关事件"（不再用"暂无历史事件可供引用"工程口吻）
- [ ] `lv-sysarch` 仍然折叠

---

### T1.7 — 移除电话倒计时 [P2]

**文件**：`src/home_perception/visualizer/viewer/render.py` `media_timeline` 渲染

**现状**：显示"Case Time 0~15s" + ▶ 播放按钮（line 343-348）

**改为**：去掉 ▶ 按钮 + 时间区间显示。改为仅显示当前 Case Time（实时刷新）。

**验收**：
- [ ] telephone_risk 跑起来时无"00:15 / 00:15"
- [ ] 仅有"Case Time 12.4s"实时显示

---

## Phase 2：技术修复（Week 2）

### T2.1 — 补 Golden Case Scenario YAML [P0]

**新增文件**：
- `config/demo/scenarios/golden_stranger_visit.yaml`
- `config/demo/scenarios/golden_repeated_visit.yaml`
- `config/demo/scenarios/golden_evidence_insufficient.yaml`

**参考模板**（`golden_stranger_visit.yaml`）：
```yaml
scenario_id: golden_stranger_visit
source: stranger_visit
source_type: video_file
media_path: data/golden/stranger_visit/output/stranger_visit_final.mp4
audio_path: data/golden/stranger_visit/audio_mix/stranger_visit_mix.wav
start_time: "2026-08-13T18:20:00+00:00"
frame_interval_s: 0.5
fps_target: 8
loop: true
description: |
  智能守护演示：陌生人首访异常停留
  演示目的：验证系统对弱视觉异常的关注度（不误报、不升级）
```

**验收**：
- [ ] `python scripts/run_demo.py --scenario golden_stranger_visit` 启动成功
- [ ] Gateway 装配正常（`/health` 返回 OK）
- [ ] 视频帧流 + audio event 都从 WS 推送

---

### T2.2 — source.py 支持 IEEE_FLOAT [P0]

**文件**：`src/home_perception/audio/source.py:58-83` `FileAudioSource.load`

**现状**：
```python
def load(self) -> LoadedAudio:
    with wave.open(str(self.path), "rb") as wf:
        ...
    elif sample_width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的采样宽度 {sample_width} 字节（仅支持 8/16/32-bit PCM）")
```

**改为**：
```python
def load(self) -> LoadedAudio:
    with wave.open(str(self.path), "rb") as wf:
        # ... 
        audio_format = wf.getcomptype()  # "PCM" / "IEEE_FLOAT"
    
    if audio_format == "IEEE_FLOAT":
        # 32-bit IEEE float（直接 [-1, 1]）
        data = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    elif sample_width == 4:
        # 32-bit PCM integer
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sample_width == 2:
        ...
```

**验收**：
- [ ] `telephone_risk/case_a_mix.wav`（IEEE_FLOAT）能加载
- [ ] `telephone_risk/telephone_persistent.wav`（IEEE_FLOAT）能加载
- [ ] 不破坏 PCM 加载路径

**测试**：
- `tests/runtime/test_audio_units.py` 增加 IEEE_FLOAT fixture

---

### T2.3 — YAMNet class_names 配置 [P0]

**问题**：警告 `audio.tier1.class_names_missing` → 模型输出退化为 stub，错认 telephone 为 distress_cry。

**文件**：`config/live_audio.yaml:139` `class_map_path: ""`

**修复路径**（按可用性）：
1. **方案 A（优先）**：YamNet 模型本身需要 class_names 文件（521 类英文标签）。如果项目中有 `data/models/yamnet/yamnet_class_map.csv` 或类似文件，配置 `class_map_path`。
2. **方案 B（fallback）**：在 `home_perception/audio/tagging.py` 加白名单映射：
   ```python
   # YAMNet class_id → AudioPerceptionKind 的映射（Gate 4 5 类）
   _YAMNET_TO_KIND = {
       "Telephone, telephone bell, telephone ringing, cell phone": "audio_telephone_persistent",
       "Crying, sobbing": "audio_distress_cry",
       "Speech": None,  # 不映射到具体 kind
       # ...
   }
   ```

**验收**：
- [ ] `case_b_mix.wav`（telephone 持续声）→ 产生 `audio_telephone_persistent` event
- [ ] `crying_voice.wav`（测试 fixture）→ 产生 `audio_distress_cry` event

**测试**：
- `tests/fixtures/audio/manifest.yaml` 已有 expected kind，可以做端到端对照测试

---

### T2.4 — 端到端验收脚本 [P0]

**新增**：`scripts/verify_golden_audio_live.py`

**功能**：启动 `golden_stranger_visit` / `live_telephone_risk`，通过 WS 客户端收集事件，断言：
- `audio` 事件出现
- `kind` 字段符合预期
- `score > 0.5`（manifest threshold）

**用法**：
```bash
python scripts/run_demo.py --live --scenario live_telephone_risk \
    --video data/golden/telephone_risk/video/telephone_risk_raw.mp4
# 另一个终端：
python scripts/verify_golden_audio_live.py --expected-kind audio_telephone_persistent
```

**验收**：
- [ ] telephone_risk case_b_mix → 至少 1 个 audio_telephone_persistent event
- [ ] stranger_visit → 至少 1 个 audio event（doorbell 或 footstep）

---

## Phase 3：扩展 Golden Cases（Week 3）

### T3.1 — Playwright 风险路径端到端测试 [P1]

**新增**：`tests/visualizer/test_risk_path_e2e.py`

**流程**：
```python
def test_risk_path_telephone_risk():
    page.goto(URL)
    
    # ① 等待 frame_tick（视频流启动）
    page.wait_for_function("__LiveState && __LiveState.lastFrameIndex > 5", timeout=60000)
    
    # ② 等待 risk_delta 包含 audio_telephone_persistent
    page.wait_for_function(
        "document.querySelectorAll('.tl-summary').length > 0",
        timeout=30000
    )
    
    # ③ 验证 5 认知骨架存在
    for region in ['.lv-now', '.lv-perception', '.lv-why', '.lv-action', '.lv-history']:
        assert page.locator(region).count() > 0
    
    # ④ 验证 banner 文案诚实
    assert "受控演示输入" in page.locator('.prov-banner').inner_text()
    
    # ⑤ 验证 risk_signal 不闪烁（CLEARED 后 DOM 仍存在）
    ...
```

---

### T3.2 — 接入 repeated_visit 三幕 [P1]

**目标**：让 `golden_repeated_visit` 跑起来，演示"系统记得过去"。

**难点**：当前 `act1_mix.wav` 等是 9s 短片，但 `repeated_visit_demo.mp4` 是拼接的三幕。需要：
- 新增 `media_path: data/golden/repeated_visit/output/repeated_visit_demo.mp4`
- 新增 `audio_path: data/golden/repeated_visit/audio_mix/act1_mix.wav`
- （注意：demo 视频拼接了三幕，但 audio 只用 act1——需要逐幕切换 audio 或组合三段）

**决策**：v0.1 用 `act1_mix.wav`（只显示 Act 1 = baseline）。后续接 act2/act3。

---

## Phase 4：Demo Mode（待 Owner 决策）

### T4.1 — Demo Mode 设计 [P3]

**目标**：评委模式，自动推进 case、自动高亮。

**范围**：
- URL 参数 `?demo=1` 触发
- 8 秒后自动滚动到下一认知区域
- audio 自动播放 + 音量提升
- 关键证据自动高亮

**不在本期**（除非 Owner 决策）。

---

## 测试矩阵总览

| 类型 | 文件 | 覆盖 Gap |
|------|------|---------|
| Playwright 端到端 | `tests/visualizer/test_live_acceptance.py` (现有 7 个) + 新增 `test_risk_path_e2e.py` | T1.4, T1.5, T1.6 |
| JS 契约 | `tests/visualizer/test_live_stream_js.py` | T1.2, T1.3 |
| Live Adapter | `tests/visualizer/test_live_adapter.py` | T1.1 |
| Shell/HTML | `tests/visualizer/test_live_shell.py` | T1.1, T1.5, T1.6 |
| Audio | `tests/runtime/test_audio_units.py` | T2.2, T2.3 |
| Demo 端到端 | `scripts/verify_golden_audio_live.py` | T2.4 |

---

## 执行顺序（最终建议）

```
Phase 1（Week 1）— 先做产品骨架
  Day 1: T1.1 + T1.2
  Day 2: T1.3 + T1.5
  Day 3: T1.4
  Day 4: T1.6
  Day 5: T1.7 + visual polish

Phase 2（Week 2）— 让数据跑通
  Day 1: T2.1（YAML）
  Day 2: T2.2（IEEE_FLOAT）
  Day 3-4: T2.3（class_names）
  Day 5: T2.4（端到端验证）

Phase 3（Week 3）— 扩展 Case
  Day 1-2: T3.1（E2E 测试）
  Day 3-5: T3.2（repeated_visit 接入）

Phase 4（可选）：T4.1（Demo Mode）
```

---

*任务版本：v0.1 | 配套 DESIGN-golden-case-live-product.md v0.1*