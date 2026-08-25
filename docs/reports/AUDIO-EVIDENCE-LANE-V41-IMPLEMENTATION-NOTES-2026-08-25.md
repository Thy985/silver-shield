# SSOT v4.1 备忘 · Audio Evidence Lane 三层组件（presentation-only）

> **性质**：本备忘是 v4.0 SSOT 决策延伸的**实现层记录**，不修改 `docs/02_architecture.md` / `docs/08_roadmap.md` / `docs/ADR/*` 等 Owner 专属文件。
> **生效时间**：2026-08-25
> **决策依据**：本会话用户拍板（"presentation-only projection，不新增 Event/Evidence Schema，不新增 Runtime 事实，不修改 AudioPolicy"）

## 一、目标

`audio-sensor` 卡片从"空白 + 单 waveform canvas"升级为"三层组件"：
1. **layer-1 RMS waveform**：保留现有 `<canvas id="waveform-canvas-{sid}">` 单层柱状图（live_stream.js `_drawWaveform`）
2. **layer-2 Audio Evidence Lane**：新增 DOM 容器 `<div class="audio-evidence-lane-{sid}">`，markers 按 audio_kind 着色（kind→semantic_class→CSS var）
3. **layer-3 current-time cursor**：垂直线 `<div class="audio-cursor-{sid}">`，跟随 `frame_tick.case_time` 平移

## 二、架构红线（不可逾越）

| 红线 | 实现位置 | 验证手段 |
|---|---|---|
| ❌ 不动 EvidenceProjection schema | 服务端只输出已有 `audio_evidence` payload | live_adapter.py grep 无新字段 |
| ❌ 不新增 Runtime fact | 服务端 `adapt_runtime_audio` 无变更 | `_build_live_audio_events` 无改动 |
| ❌ 不改 AudioPolicy | `audio_pipeline.py` / `_AUDIO_KIND_ZH` 仅措辞收敛 | ruff 测试 |
| ❌ 不写 color 进 runtime 契约 | kind → semantic_class 走 `_AUDIO_KIND_LANE` JS 字典 → CSS `.audio-marker.kind-*` → `--lane-color-*` CSS var | 视觉主题可换，无需改 runtime |
| ✅ 颜色是 CSS variable 主题层 | `.audio-marker.kind-telephone { background: var(--lane-color-telephone, #3b82f6); }` | 主题切换测 |

## 三、纯前端 segments 派生（核心）

**入口**：`live_stream.js` `deriveAudioEvidenceSegments(events, windowStart, windowEnd)`

**算法**（按 kind 分桶 →相邻同类合并）：
1. 窗口裁剪：`case_time ∈ [windowStart, windowEnd]`，缺失/越界跳过
2. 按 kind 分桶（bucket per kind）
3. 桶内按 case_time 排序
4. 相邻同类合并：间隔 ≤ `_LANE_MIN_MARK_S`(0.4s) → 合并段（仅"观察密度"语义，不承诺"持续时长"）
5. 输出 `[{kind, semantic_class, start_pct, end_pct, score_max}]`

**窗口边界**：
- `windowEnd = max(case_time) in cache`（最近观察时刻）
- `windowStart = max(0, windowEnd - _LANE_WINDOW_S)`（左滚 16s 窗口）

**Anchor 优先级**（必须存在其一才能进 cache）：
1. `a.case_time`（timeline 节点注入，相对最早证据 T0）
2. `a.timestamp`（wav 相对起点的秒，已存在事实字段）
3. 都没有 → 跳过（fail-soft，不污染）

## 四、涉及文件

| 文件 | 改动 | 行数估算 |
|---|---|---|
| `render.py` | `_render_audio_sensor_status` 增 `lane_html` + CSS 块 | +50 行 |
| `live_stream.js` | `_audioEvidenceCache` + `deriveAudioEvidenceSegments` + `_renderAudioEvidenceLane` + `_moveAudioCursor` + WS 集成 | +200 行 |
| `viewer/scenario_config.py` | 注册 `telephone_risk_reality_check` Surface 集 | +12 行 |
| `tests/visualizer/test_audio_evidence_lane_projection.py` | 新建：16 个单测覆盖 segments 派生 + anchor fallback | +300 行 |

## 五、验收

| 验收点 | 结果 |
|---|---|
| Playwright DOM 三层检查 | ✅ waveform + markers(10) + cursor present |
| markers kind→semantic_class 映射 | ✅ 9×kind-distress-cry + 1×kind-speech-rapid |
| kind→color 走 CSS variables | ✅ `rgb(155, 89, 182)` = `--lane-color-distress` |
| cursor 跟随 frame_tick 平移 | ✅ left=100%（case_time 在窗口最右） |
| 时间刻度 5 个 tick（不累积） | ✅ 13s/17s/21s/25s/29s |
| 16 个 segments 单测 | ✅ 全绿 |
| live_adapter 已有 42 测试 | ✅ 仍全绿 |
| ruff check | ✅ All checks passed |
| Audio panel 视觉密度 | 159px → 350px（+120%） |
| `writing-mode` 横排 | ✅ 无 vertical-rl |
| "声学异常活动(当前算法判定)" 措辞 | ✅ 主标签"声学异常活动" |

## 六、未触碰

- EvidenceProjection schema
- audio_pipeline.py / 音频推理逻辑
- AudioPolicy / 决策策略
- ADR-0039~0043 任何决策
- `docs/02_architecture.md` / `docs/08_roadmap.md` / `docs/ADR/*`
- AGENTS.md / CONTRIBUTING.md

## 七、Owner 决策边界（待 A 完成后再决定）

- B（处置闭环面板优化）：本轮 Owner 已决定暂缓
- P1（首屏密度/三行 chip/空态文案）：Owner 已分类，本轮不修
- P2（历史卡片/按钮/REAL 标签）：Owner 已分类，本轮不修