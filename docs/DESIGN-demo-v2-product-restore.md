# 第二代 Demo 产品能力恢复 · 设计方案与落地方案

- **状态**：规划（Owner 已拍板方向并审阅 v2；本文档为**主实施蓝图**）
- **日期**：2026-08-15（v2：Owner 4 处边界修正 + P0 Overall Gate + PR 拆分）
- **决策者**：Owner
- **文档定位**：**"第二代 Demo 产品能力恢复计划"**，不是 ADR。分工：ADR-0036 负责统一语义和架构边界；本文档负责**把第一代 Demo 已证明有价值的能力重新落回第二代产品**（产品能力恢复，非新架构）。
- **相关**：ADR-0015（P0-11 Demo 架构）/ ADR-0017（协同闭环 Demo 范围）/ ADR-0036（统一 Case Viewer）/ MEMORY.md「战略方向重定向（2026-08-14）」

---

## 0. 摘要

**下一步不是"继续做 Case Viewer"，而是把第一代 Demo 已经证明有价值的「实时视频 + 人类协同处置 + 故事节奏」重新接回第二代 Case Viewer 的统一语义层。**

当前 Case Viewer 在"行为理解 / 风险解释"（评委三问 Q1/Q2）上已超过初版（多模态、音频、统一时间线、可信 artifact），但 **Q3（行动闭环）退化**了：只展示"系统行动"结果卡片，没有人介入确认。核心根因是收敛过程中（`463a34d` 收敛 /live 到统一 Case Viewer、删除 legacy dashboard）**把第一代已验证的产品能力（三端协同 + 上行交互 + 实时帧流）当作"第二套事实模型"一并删除，却只保留了证据展示层**。

关键认知修正：

> **UI State ≠ Evidence**——交互 UI 不是"第二套证据"，可以也应该存在；但交互状态不得进入 `EvidenceProjection`，交互产生的事实结果（如"社区完成处置"）应最终回灌证据链。

---

## 1. 问题定义

### 1.1 现状证据（基于代码核查，2026-08-15）

| 初版能力 | 现状 |
| --- | --- |
| 三端协同视图（AI 风险中心 / 家属端 / 社区端） | ❌ 前端 UI 已删（legacy dashboard），**未在 Case Viewer 重建** |
| 上行交互按钮（我知道了 / 通知社区 / 接受 / 完成） | ❌ 前端删除；后端 `state.py` 状态机 + `ws.py` action 上行**仍在**，成孤儿能力（无 UI 消费） |
| 实时逐帧视频 + YOLO 检测框 | ❌ 旗舰模式 `assembled=false` 纯静态；`LiveFrameSource` 声明存在但**未实现**（`resolve_media_source` 对 `LiveFrameSource` 恒返回 `None`） |
| 5 分钟闭环故事脚本 | ❌ `DEMO-SCRIPT-P0-11-5b.md` 仍是文档，无 Story 编排层 |
| 风险解释（reason_summary + trigger 下钻） | ✅ 保留（Case Viewer"为什么"卡片） |
| 行为理解（轨迹 / 时长 / 重复） | ✅ 保留（统一 Evidence Timeline） |

### 1.2 当前 Case Viewer 新增（初版没有）

- ✅ 音频感知（首屏"系统听到了什么" + 可播放样本）——初版完全没有
- ✅ 可信 artifact（CI 受控生成徽章 / 指纹 / Gate / provenance）
- ✅ 统一 `EvidenceProjection` View Model（VM-1，无第二事实模型）+ 确定性 / 可复现
- ✅ 真实视频主轴（`ArtifactVideoSource` case.mp4）

### 1.3 为什么"没超过初版"

当前 Case Viewer 能回答评委三问的 Q1/Q2，**但 Q3 答不了**——评委无法在页面上完成"AI 发现风险 → 通知家属 → 家属确认 → 社区处置 → 闭环完成"这个互动过程。产品只剩"AI 发现了问题，然后 AI 自己宣布行动成功"。

---

## 2. 设计原则（四条铁律）

### 2.1 交互状态 ≠ 证据（UI State / Workflow State 不进 `EvidenceProjection`）

```
业务事实     → EvidenceProjection（VM-1 唯一 View Model）
用户互动状态 → UI State / Workflow State（浏览器内存 + gateway 会话态）
```

"我知道了 / 通知社区 / 接受 / 完成"按钮、`pending → family_handled → community_done` 状态机——这些是 **UI / 工作流状态**，不是第二套证据，可以自由存在于前端与网关会话态。

### 2.2 交互产生的事实结果，最终进入证据链——**Projection 不回写**

```
家属点击「认识」      → UI / Interaction State（不进证据）
家属点击「通知社区」  → Command / Workflow（gateway 会话态）
社区点击「接受」      → 真实工作流状态（state.py）
社区点击「完成」      → Resolution Fact（事实源产生新事实）
                           ↓
                    Runtime / Workflow Event Stream
                           ↓
                    Live Adapter（accumulator 摄入新事实）
                           ↓
                    重新构造当前 EvidenceProjection
```

**硬约束（VM-6）**：`EvidenceProjection` 是只读派生模型，不是权威运行态——**绝不直接 mutate Projection**。
正确路径是"事实源产生新事实 → 事件流 → Live Adapter → **重新投影**"（`ProjectionAccumulator` 摄入
resolution 事实事件 → `to_evidence_projection()` 重新构造，VM-8 幂等保持）。前端绝不自行宣布行动成功，
成功由状态机事实驱动。这样既不破坏 VM-1/VM-6，又让交互闭环可审计、可溯源。

### 2.3 检测框是展示层对 Runtime 输出的投影

前端**绝不重跑 YOLO**。检测框数据来自 `FrameResult.detections`（Runtime 输出）→ `live_adapter` 投影为 overlay 数据 → 前端 canvas 绘制。不形成第二个 runtime，VM-1 不破。

### 2.4 Story 是案例编排层，不是新的业务事实层

Story Script 驱动"什么节拍展示什么、何时切场景、给评委什么引导"，从 `EvidenceProjection` 派生展示节奏，**不新增任何事实**。前端禁止用 `setTimeout` 硬编码故事。

---

## 3. 目标架构

### 3.1 三轨对齐（Case Media Track）

```
                Case
                 │
       ┌─────────┼─────────┐
       ↓         ↓         ↓
     Video     Audio    Evidence
       │         │         │
       └─────────┼─────────┘
                 ↓
             Case Time
                 ↓
            Case Viewer
                 ↓
       Risk → Decision → Action
                 ↓
          Human Action（Family ↔ Community）
                 ↓
              Resolution
```

- **Media 与 Evidence 严格分离**：`case.mp4` / `phone_ring.wav` 是 **Media**（资产）；`AudioPerceptionEvent` / `PerceptionEvent` 是 **Evidence**（感知结果）。**有 `phone_ring.wav` ≠ 检测成功**。
- **Case Time 对齐**：媒体轨与证据轨通过时间戳对齐（点击 `18.4s` → video seek + audio play + 证据时间线高亮）。

### 3.2 CI 与 Live 共用同一套 UI（可信案例生产线）

```
Scenario（video spec + audio spec）
   ↓
Media Generator（确定性合成：case.mp4 + {kind}.wav）
   ↓
Vision Runtime + Audio Runtime
   ↓
EvidenceProjection
   ↓
Benchmark / Integration Gate
   ↓
Trusted Case Artifact
   ↓
Case Viewer（Replay）
```
与
```
Live Runtime
   ↓
FrameResult / AudioEvidence（实时累积）
   ↓
同一 EvidenceProjection / 同一 Case Viewer
```

CI 不是独立工程玩具，而是**产品的可信案例生产线**；Replay 与 Live 最终使用同一套 UI。

---

## 4. 现状盘点（已有 vs 缺失）

| 能力 | 已有（可直接复用） | 缺失（需开发） |
| --- | --- | --- |
| 三端状态机 | `silver_demo/state.py`（`pending→family_handled→community_done`） | 前端三端交互面板 |
| WS 上行 | `silver_demo/ws.py`（`type=action` → `operator`/`action` → 状态机） | 无 UI 消费；resolution 回灌证据链 |
| 场景音频资产声明 | `scenario.yaml` 的 `audio:`（kind/timestamp/score/confidence）**已存在** | 媒体轨时间绑定（start/end time） |
| 可播放音频样本 | `prepare_case_audio.py` + `{sid}/audio/manifest.json`（AudioFileSource） | case 级 `media_tracks` manifest（video/audio 轨时间对齐） |
| 音频证据投影 | `AudioEvidenceNode`（无 url）+ loader 投影 | **Live 模式**音频投影（`_LIVE_PANELS` 缺 `audio_perception`） |
| 实时帧源 | `LiveFrameSource` 类型声明 | **运行时实现**（当前 `resolve_media_source` 返回 `None`） |
| 真实视频主轴 | `prepare_case_media.py` + `ArtifactVideoSource` case.mp4 | 检测框 overlay；live 帧流视频面 |
| 故事脚本 | `DEMO-SCRIPT-P0-11-5b.md`（文档） | Story 编排层（yaml beats → 驱动器） |
| Trusted Case Factory | `build_trusted_case.py`（步骤 5.5 音频 / 5.6 媒体） | 媒体↔证据时间对齐 manifest 产出 |

---

## 5. 分阶段落地方案

> 实施顺序：**P0-1 → P0-2 → P0-3 → P1**，然后才是 P2。**不做**：❌ D3、❌ 更多 Fingerprint、❌ 更多 Evidence Graph、❌ 再写一个验证 ADR、❌ ASR（第一阶段）。

### P0-1 恢复"人类处置闭环"（最高优先 · 恢复 Q3）

**目标**：评委能在页面上完成"发现风险 → 通知家属 → 家属确认 → 社区处置 → 闭环"。

**设计**：

```
┌──────────────────────────────────────────────┐
│ 妈妈当前状态：需要关注                        │
│                                              │
│ [案例视频]                                    │
│                                              │
│ 风险：高  原因：异常停留 + 音频异常           │
│                                              │
│ 家属端   [我知道了] [通知社区]                │
│ 社区端   状态：待接单  [接受任务] [完成处置]   │
└──────────────────────────────────────────────┘
```

- **数据流**（复用现有后端，零改动决策逻辑）：
  ```
  Case Viewer 按钮 → WS 上行 {type:action, operator, action}
      → ws.py → state.py 状态机 → 状态快照下行 → 前端渲染（UI / Workflow 态）
  ```
- **Resolution Fact（2.2 铁律 · Projection 不回写）**：状态到达终态 `community_done` 时，
  gateway 把 resolution 事实作为**新事件** feed 进 `ProjectionAccumulator`（`ingest_resolution`，
  不是 mutate projection）→ `to_evidence_projection()` **重新构造** → Resolution 节点
  （timeline，modality=ACTION，provenance=REAL_SENSOR）出现在同一 Case Viewer。前端不宣布成功，
  成功由状态机事实驱动。
- **旗舰模式**：只读展示 resolution 结果（artifact 有 Resolution 节点时 Evidence Timeline 呈现），不渲染交互按钮（Live 专属交互）。

**改动点**：
| 文件 | 改动 |
| --- | --- |
| `viewer/render.py` | 新增 `action_closure` 面板（live 注入时渲染家属/社区按钮 + 状态徽章；VM-11 合规：按钮/状态是 UI/Workflow 态，非事实） |
| `viewer/live_adapter.py` | `ProjectionAccumulator.ingest_resolution(fact)` 新事实事件 + `_build_timeline` 投影 Resolution 节点；`_LIVE_PANELS` 加 `action_closure`；descriptor 加 `live_ws_path` 纯展示元数据 |
| `silver_demo/gateway.py` | 帧循环把 `WarningEvent.warning_id` upsert 进 store（前端有可操作目标）；action 翻转到达终态 → `ingest_resolution` |
| `assets/live_actions.js`（新） | WS 客户端：连 ws → 收 snapshot/state_update 更新徽章；按钮 → 发 `{type:action, warning_id, operator, action}` 上行 |

**验收（DoD）**：评委点击全流程可走通（认识→通知社区→接受→完成）；页面状态徽章随状态机翻转；完成处置后 Evidence Timeline 出现 Resolution 节点（带 provenance）；无前端自行宣布成功；ruff + 契约测试绿。

### P0-2 恢复实时视频帧 + 检测框（"活着的画面"）

**目标**：Live 模式视频在动 + 检测框在动 + Track ID 在变 + 事件在产生。

**设计**：
- **`LiveFrameSource` 只消费媒体帧**（Owner 修正 2）——`FrameResult` 双路径严格分开，共享 `Case Time`：
  ```
  FrameResult
   ├── Media path   → LiveFrameSource（frame bytes / encoded frame / timestamp）→ Case Viewer 视频面
   └── Evidence path → LiveAdapter（detections / events / warnings / decision）→ EvidenceProjection
  ```
  `LiveFrameSource` **绝不**处理 Detection / Event（否则 Media 与 Evidence 又混）。
- **检测框 = 展示层投影**（2.3 铁律）：`FrameResult.detections` → `live_adapter` 投影为 overlay 数据岛 → 前端 canvas 叠加绘制（框 + Track ID + 类别），**不重跑 YOLO**。
- 帧流与 Evidence 累积并行：视频帧驱动视觉体验，`ProjectionAccumulator` 照常累积 Evidence。

**改动点**：
| 文件 | 改动 |
| --- | --- |
| `viewer/media_source.py` / `resolve_media_source` | `LiveFrameSource` 从返回 `None` 改为 live 帧源注入（**只消费媒体帧**，只读 gateway 提供的帧流） |
| `viewer/live_adapter.py` | 投影 detections → overlay 数据（框/Track ID/类别，Evidence path 专用） |
| `viewer/render.py` + `assets/media.js` | live 视频面：帧刷新 + 检测框 overlay 渲染 |

**验收（DoD）**：`/live` 打开即见连续帧（画面在动）；检测框随时间移动、Track ID 变化；无第二个 YOLO 运行时（断言前端无模型推理）；契约测试绿。

### P0-3 把 Audio 真正接进 Live 案例（Case Media Track 并行）

**目标**：音频作为**与视频并行的 Media Track**，经 Case Time 与 `EvidenceProjection` 对齐；Live Case Viewer 真实消费 `AudioEvidence`。

**设计**：
- **不改旧视频本体**：`case.mp4` 保留为视觉轨；音频轨独立（`{sid}/audio/{kind}.wav`，确定性合成，`SIMULATED`）。
- **音频媒体 / 音频证据严格二分**（Owner 修正 3）：`phone_ring.wav` 是 **Media**，`AudioPerceptionEvent` 是 **Evidence**——
  `wav 存在 ≠ 检测成功`。`audio_evidence`（EvidenceProjection 内）**不存 `audio_asset_url`**（保持 ADR-0036 纪律）：
  ```
  EvidenceProjection → evidence ref / timestamp
  MediaSource         → audio asset（Media Source Adapter 按 ref / manifest 找到音频）
  ```
  这样真实音频 / 模拟音频 / 录音文件将来都能统一经 Adapter 解析。
- **Media Track manifest（时间钉死）**——新增 case 级 `media_tracks.yaml`（或并入 `{sid}/media/manifest.json` + `{sid}/audio/manifest.json` 之上）：
  ```yaml
  case_id: sw_adr0034_audio_e2e
  media:
    video:
      uri: media/case.mp4
    audio:
      - id: audio_001
        uri: audio/audio_telephone_persistent.wav
        start_time: 18.4      # Case Time 秒（由 scenario.audio.timestamp - 场景 T0 推导）
        end_time: 24.8
        provenance_kind: SIMULATED
      - id: audio_002
        uri: audio/audio_voice_raised.wav
        start_time: 168.4
        end_time: 176.0
        provenance_kind: SIMULATED
  ```
  时间推导：`scenario.audio[i].timestamp`（Unix 秒）− 场景最早时间戳（T0）= Case Time 偏移（与首屏音频面板的相对时间同源）。
- **Case Viewer 播放器同步**（用户示例交互）：点击时间点 `18.4s` →
  ```
  Case Time = 18.4
  → Video seek(18.4)
  → Audio play(phone_ring.wav, offset=0)
  → Evidence Timeline 高亮 audio_001
  ```
- **Media ≠ Evidence**：`phone_ring.wav` 存在不代表检测成功；检测成功由 `AudioPerceptionEvent`（Evidence）驱动——两者都进 artifact，但语义分开。
- **Live 模式吃 AudioEvidence**：`_LIVE_PANELS` 补 `audio_perception`（与旗舰一致）；`live_adapter` 投影 AUDIO 节点（现有 `ingest_audio` 已支持 Phase B）。同一案例呈现：👁 异常停留 / 🔊 持续电话声音 / 🧠 风险升级 / 📢 通知家属。
- **无需 ASR**：第一阶段证明"AI 能听见什么"（持续电话 / 声音强度异常 / 呼救高声），不证明"AI 理解骗子说了什么"（那是第二阶段：Audio → ASR → Transcript → Semantic Analysis）。确定性音频资产反而更适合 CI：同一 Scenario → 同一 WAV → 同一 Audio Runtime → 可重复验证。

**改动点**：
| 文件 | 改动 |
| --- | --- |
| `scripts/prepare_case_audio.py` | 产出 audio track 时间绑定（start/end_time 由 scenario.audio 推导） |
| `viewer/audio_source.py` | 扩展：解析 audio track 时间元数据（fail-closed） |
| `viewer/render.py` + `assets/media.js` | Case Time 同步播放器（seek/play/高亮）；旗舰与 Live 同 UI |
| `viewer/live_adapter.py` | `_LIVE_PANELS` 补 `audio_perception`；AUDIO 节点投影（已有 ingest_audio） |
| `scripts/build_trusted_case.py` | 步骤 5.5 升级：写 time-bound track manifest |

**验收（DoD）**：点击 18.4s → 视频 seek + 电话 wav 播放 + 证据时间线高亮；CI artifact 与 Live 渲染同 UI；无 ASR（断言无 transcript 字段）；ruff + 契约测试绿。

### P1 恢复 5 分钟 Demo Story（Story 编排层）

**目标**：恢复故事节奏（Case State → Event → Risk Escalation → Decision → Family Interaction → Community Interaction → Resolution）。

**设计**：
- **Story 不驱动 Runtime**（Owner 修正 4）——Story 只决定"怎么讲"，不控制系统事实：
  ```
  Scenario / Runtime → 真实发生的 Evidence
                          ↓
                         Story → 决定怎么讲（编排展示节奏）
  ```
  故事**不得**调用 `/demo/scenario` 去改 Runtime 本身（否则"故事为了好看改变事实"）。
  若 Demo 确需切场景，由**独立 Demo Orchestrator** 控制 Scenario 输入（它改变的是输入源，
  不是业务状态）；Story 与 Runtime 之间只读。
- **Story 是案例编排层**（2.4 铁律）：结构化 Story Script（yaml beats：节拍 / 展示什么 / 切换哪个场景 / 引导语），从 `EvidenceProjection` 派生展示节奏，不新增事实。
- 场景差异化：`stranger_visit.yaml` / `elderly_dwell.yaml` / `telephone_scam.yaml` / `benign.yaml` 各有剧本（现有 scenario 资产已覆盖语义，Story 只是编排）。

**改动点**：新增 `scenario.story` 声明（或独立 `stories/*.yaml`）+ 独立 Demo Orchestrator（仅控制 Scenario 输入）+ Case Viewer 节拍引导条；复用 `DEMO-SCRIPT-P0-11-5b.md` 的内容资产。

**验收（DoD）**：5 分钟故事可完整讲完（含 P0-1 闭环）；不同场景有不同剧本；前端无 `setTimeout` 硬编码故事；Story 不触碰 Runtime 业务状态（断言无故事→/demo/scenario 调用）；契约测试绿。

### P0 Overall Gate（三个 P0 的总验收 · Owner 修正）

三个 P0 全部完成后，必须**从头到尾在同一个 Case Viewer 页面**跑通：

```
Live Video → YOLO Detection → Behavior Event → Audio Event
   → Risk → Decision → 通知家属 → 家属确认 → 通知社区
   → 社区接受 → 社区完成 → Resolution
```

**验收**：评委只看到一个页面（`/` → 一个完整案例），**不需要跳转** `/live` → dashboard → audio page → evidence page → community page。这才是本轮工作的真正总验收。

### P2 后续（不阻塞本轮）

- 60 秒陌生人测试（真实摄像头演示）
- 隐私 / 权限 / 同意
- 可访问性
- 结果回流 / 数据飞轮

---

## 6. 边界与不变式（红线）

- **VM-1**：交互状态（UI/Workflow）不进 `EvidenceProjection`；只有事实结果（Resolution 节点）回灌。无第二份 risk/decision/timeline/audioData。
- **VM-11**：`CasePresentationDescriptor` 仍只承载展示编排；交互按钮/状态是 UI 态，不进入 descriptor 事实字段（AC-13 守卫不变）。
- **检测框 = 投影**：前端不重跑 YOLO；`LiveFrameSource` 是展示层对 Runtime 输出的投影。
- **冻结边界**：`visualizer` 不 import 生产 runtime；D8 豁免（`integration/loop/` allowlist）不变；感知层不产 fraud/suspect 判定。
- **诚实声明**：音频资产 = 确定性合成（SIMULATED），页面标注"样本声音（合成素材，非原始录音）"；`wav 存在 ≠ 检测成功`（Media ≠ Evidence）。
- **Story 不造事实**：Story 只编排展示节奏，证据全部来自 `EvidenceProjection`。
- **不做清单**：❌ D3 继续、❌ 更多 Fingerprint、❌ 更多 Evidence Graph、❌ 再写验证 ADR、❌ ASR（第一阶段）、❌ 给旧视频强行 mux 音轨。

---

## 7. 实施顺序与依赖（PR 拆分）

**每个 PR 一个明确产品增量，不做大规模架构改造**：

| PR | 范围 | 一句话验收 |
| --- | --- | --- |
| **PR 1 = P0-1** | 人类处置闭环（家属确认 → 通知社区 → 社区接受 → 社区完成）；**不碰** Live 视频 / Audio | 一个评委可完整走完人工处置闭环（同一页面） |
| **PR 2 = P0-2** | `LiveFrameSource`（仅媒体帧）+ Detection Overlay（Evidence path） | `/live` 画面真正动起来，YOLO 框 + Track ID 同步变化 |
| **PR 3 = P0-3** | Audio Media（并行轨）+ Audio Evidence + Case Time 对齐 | 同一案例视频在播、声音能听、AUDIO Evidence 同步出现、CrossModal 正确 |
| **PR 4 = P1** | 5 分钟 Story 编排层（Story 不驱动 Runtime，独立 Orchestrator 控制输入） | 故事可完整讲完，不同场景不同剧本 |

依赖：P0-3 依赖 P0-2 的 live 视频面（Case Time 同步播放器复用）；P1 依赖 P0-1（故事含处置闭环节拍）。每个 PR 独立 Conventional commit + Owner review，CI 门禁照旧。

---

## 8. 验收总览（DoD 汇总）

| 阶段 | 一句话验收 |
| --- | --- |
| P0-1 | 评委完成"通知家属 → 家属确认 → 社区处置"全流程，页面状态随状态机翻转，完成处置后证据时间线出现 Resolution 节点 |
| P0-2 | /live 画面在动 + 检测框/Track ID 在动，前端无第二 YOLO，LiveFrameSource 只消费媒体帧 |
| P0-3 | 点击 18.4s → 视频 seek + 电话 wav 播放 + 证据高亮；CI 与 Live 同 UI；无 ASR；audio_evidence 无 url |
| P1 | 5 分钟故事可完整讲完，不同场景不同剧本，无 setTimeout 硬编码，Story 不驱动 Runtime |
| **P0 Overall Gate** | **从头到尾（Live Video → … → Resolution）全在同一个 Case Viewer 页面完成，评委无需跳转** |
| P2 | 60 秒陌生人 / 隐私 / 可访问性 / 数据飞轮 |

---

## 9. 一句话总结

> **下一步不是"继续做 Case Viewer"，而是把第一代 Demo 已经证明有价值的「实时视频、人类协同处置、故事节奏」重新接回第二代 Case Viewer 的统一语义层。**
> 完成这一步，SilverShield 才从"可信工程 Demo"变回"有产品价值的实时守护 Demo"——CI 是产品的可信案例生产线，不是独立工程玩具。
