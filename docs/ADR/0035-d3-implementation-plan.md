# ADR-0035 D3 · 落地实现设计文档（Owner Decision Record · v7 · 决策已认可 · 待主 ADR 冻结后实现）

> **性质**：本文件是 ADR-0035 正文预留的「非冻结件 Implementation Plan」子文档（参见 ADR-0035 §6 实施切片 + 工作记忆约定 `docs/ADR/0035-implementation-plan.md`）。  
> **状态**：**Owner Decision Record · v7**——D3-1 ~ D3-11 经 Owner 逐项评审全部认可，并新增 D3-12（共 12 项决策）；本文件从「Design Proposal」进入「可实现架构冻结前」的 **Owner Decision Record** 阶段：决策已锁定，仅待主 ADR-0035 正文冻结，AI 即进入实现（独立 PR + 评审）。本文档仍只设计、不实现。  
> **不改动**：ADR-0035 正文（仍 Proposed）、不触碰生产 runtime / 验证判定 / 基线文件。  
> **依据**：所有复用接口均取自当前代码真实签名（见 §1 盘点），非凭空设计。  
> **v6 变更（相对 v5 · 对应 Owner 评审 1.1–1.4 + 2.1–2.6）**：① **§3 新增 `NarrativePlan` / `ReasoningStep` 正式 Pydantic schema**——字段严格仅为 `intent: Literal[...]` / `reasoning_chain: list[ReasoningStep]` / `audience_question: str` / `audience: str`，**明确禁 `text` / `sentence` / `narration` 字段**；四个既有 schema（`ShotSpec` / `Storyboard` / `VisualElement` / `VisualSceneGraph`）全部加 `model_config = ConfigDict(extra="forbid")` + docstring 列禁用字段，使 §2.4.1 层边界契约**机械可 enforcement**（CI/review 可据此打回越界）；② **`compile_narrative` 重命名为 `instantiate_narrative_template`**（与 `NarrativeTemplateCompiler` 实体对齐，§2.2/§2.8/§7 同步）；③ **§1 行号核查**：`provider.py:52` 实为 `class EdgeTTSProvider`（`synthesize` 在 `:57`）、`scenario.py` 的 `audio` 字段实为 `:159`（非 `:99`）；补「行号已对 origin/main HEAD 核验」声明 + 漂移同步协议；④ **§2.0 阶段计数括号注明**「8 = 1 投影(EvidenceProjection) + 1 中间数据(EvidenceGraph) + 6 新建 D3 阶段」；`audio.wav` 显式标 `【D3-B only】`；⑤ **§2.4 末尾补「层边界契约见 §2.4.1」指引**、**§2.3 Storyboard 示例补 (节选) 对齐 canonical 5-shot**；⑥ **§10 开头补「升级 Planner 仍受 §0.3 红线约束」重申**；⑦ **解决 ★★★ D3-1 与主 ADR §3 边界冲突（方案 a）**——主 ADR §3 增补 D3 纯消费 import `validation`/`audio` 固定 allowlist 例外 + changelog；本文件 §9 D3-1 转「已决」、§8 验收第 7 条同步。

> **v7 变更（相对 v6 · 对应 Owner 整体认可进入 Decision Record + 4 项锁定 + 新增 D3-12）**：① **D3-1 单向依赖约束锁定**——在「纯消费 import」例外上明确依赖方向：允许 `visualizer.video → validation.simulation.renderer` 与 `visualizer.video → audio.tts`，**禁止** `validation → visualizer.video` 与 `audio → visualizer.video`；并确立「`visualizer/video` 对 `validation`/`audio` 的依赖属于 **presentation adapter dependency**，不得成为业务依赖」（主 ADR §3 同步）；② **D3-3 ffmpeg 非核心降级**——明确 ffmpeg 不是 pipeline 核心，缺失时降级为 `video.mp4` + `audio.wav` + `warning.json`，而非 pipeline fail；③ **D3-7 受控字体资源抽象**——措辞由「新增轻量依赖 + 字体资产」改为「引入受控字体资源（controlled font asset）」，落点 `visualizer/video/assets/fonts/NotoSansCJK-Regular.ttf`，经 `FontRegistry → Rasterizer` 抽象，业务代码不直接加载字体路径；④ **D3-9 正式废弃 Planner 命名**——`NarrativePlanner` 列为废弃别名，v1 规范名 `NarrativeTemplateCompiler`，未来升级沿用该规范名；⑤ **新增 D3-12 Evidence ownership boundary**——D3 不拥有 `EvidenceGraph`，只拥有 Projection View，可缓存 `NarrativePlan`/`Storyboard`/`VisualSceneGraph`，**禁止新建 `EvidenceNode`/`EvidenceEdge`**（防视频有事实、系统无事实的审计破坏）；⑥ **§9 决策表全 12 项转「已决」**，进入 Decision Record 阶段。

---

## 0. 定位与范围（v4 收紧）

### 0.1 核心定位：Evidence Story Compiler（证据到叙事视频编译器）

| 项          | 内容                                                                                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| **D3 是什么** | **Evidence Story Compiler**——把「机器运行证据（artifact）」**编译**成「面向人类理解的案例分析视频（evidence case video）」；本质是 **Explainability Compiler（可解释性编译器）** |
| **不是什么**   | ❌ **不是「把 `evidence_explorer.html` 录屏成 MP4」**（那只是截图+录屏，D2 已能交互，毫无增量）；❌ 不是单纯的「视频导出器（video exporter）」；❌ **不是「小型 AI 视频生成平台」**（见 §0.3 红线） |
| 输入         | `artifact`（机器证据：IntegrationReport / DecisionTrace / Episode / CrossModalLink / Frames，经 `EvidenceProjection` 投影为 `ScenarioEvidence`） |
| 输出         | `case.mp4`（人类叙事案例视频）+ `storyboard.yaml`（分镜，可审计）+ `provenance.json`（生成溯源）[+ `audio.wav` 仅 D3-B]              |
| 复用资产       | ADR-0032 `frames` 通道（`render_frames`） + ADR-0027 audio tts 确定性合成栈                                                                    |
| 技术栈        | Python + OpenCV + Pillow + SVG renderer + ffmpeg + TTS(可选) —— **非 Web 栈**（无 React/Vue/D3.js）；接近「数据驱动视频 / 科普视频生成」路线                   |
| 明令不做       | ❌ 真实录像 ❌ 实时 ❌ 照片级/扩散视频 ❌ 接 CI 门禁 ❌ 引入新重依赖 ❌ 用 LLM 生成叙事（保持离线确定性）❌ 录屏式 MP4 ❌ 音效/配乐（D3-C deferred）                    |

**真正困难的部分**：D3 的核心不是「怎么生成 mp4」，而是「如何把机器证据转换成人类理解路径」。这对应若干成熟领域，D3 的本质接近 **Explainability Compiler**：

```
事故分析：  raw logs        → incident timeline → root cause analysis → postmortem report
科研解释：  experiment data → observation       → hypothesis           → explanation
自动驾驶：  sensor logs     → scene understand  → decision explanation → scenario replay
SilverShield D3： artifact  → EvidenceGraph    → NarrativePlan        → Storyboard → VisualScene → Case Video
```

即：**artifact → 理解证据结构 → 规划解释路径 → 生成分镜 → 生成视觉语言 → 视频**。D3 是 SilverShield 第一次具备「把机器运行过程自动解释成人类案例」的能力——但 v1 只在确定性映射范围内落地（见 §10）。

### 0.2 三个表达层（D1/D2/D3 不是递进 UI，而是三种不同表达）

| 层                              | 解决的问题            | 产物                                 | 受众                   |
| ------------------------------ | ---------------- | ---------------------------------- | -------------------- |
| **D1 Evidence Explorer**       | 我想**查**证据（探索）    | 自包含 HTML Explorer（artifact → 人类探索） | 能开网页的审查者             |
| **D2 Replay Engine**           | 我想**调试过程**（时间交互） | 浏览器内重放动画（artifact → 时间交互）          | 能开网页的审查者             |
| **D3 Evidence Story Compiler** | 我要**让别人理解**（叙事）  | Case Video（artifact → 人类叙事）        | 无法开网页的人（评委/投资人/终端用户） |

三者消费**同一份 `EvidenceGraph`**（ADR-0035 D5 统一抽象），是不同表达层，不是 UI 递进。

### 0.3 Scope Guardrails（v1 红线 · 防「小型 AI 视频生成平台」膨胀）

D3 是**编译器**，不是平台。v1 明令**不做**以下任何一项（实现时碰线即视为偏离，需回 Owner 复议）：

| 红线 | 说明 |
| --- | --- |
| ❌ 多轨音频工作站 | 不做音效库 / 配乐 / 混音台；D3-B 仅单轨旁白，音效/配乐递延至 D3-C 且默认不做 |
| ❌ 在线/自由叙事 | 不调用 LLM 写解说词、不生成证据外文本；叙事由**固定模板映射**生成 |
| ❌ 交互式时间线编辑器 | 不提供 GUI 剪辑/拖拽/实时预览；产出物是一次性编译结果 case.mp4 |
| ❌ 多格式/多分辨率导出 | 不提供 webm/gif/4k/竖屏等多目标；v1 仅 1 种 mp4 规格 |
| ❌ 真实媒体/实时 | 不接摄像头、不录真实录像、不实时 |
| ❌ Web 栈 / 服务器 | 不引 React/Vue/D3.js/FastAPI；纯 Python 离线编译 |
| ❌ 反哺 runtime | D3 只读 artifact 投影，绝不反向写 EvidenceGraph 或影响生产行为（ADR-0035 D5 派生模型边界） |

**判定标准**：若某需求使 `visualizer/video/` 出现「音频工程 / 编辑器 / 多格式 / 在线生成」类模块，即已越线。

---

## 1. 复用面盘点（Grounding · 基于真实代码签名）

| 复用对象                        | 真实签名（当前代码）                                                                                                                  | D3 管线阶段                           |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| **EvidenceProjection（已落地）** | `visualizer/loader.py:173` `_build_decision_evidence` → `ScenarioEvidence`（`decision_evidence` / `graph` / timeline events） | 阶段 1–2（D3 直接消费，零重实现）              |
| **EvidenceGraph（已落地）**      | `visualizer/schema/evidence.py` `EvidenceGraph`（nodes + edges，ntype ∈ {Event,Decision,Action,Episode,Link,Scenario}）        | 阶段 2（**事实层**单一真相源）                  |
| 视觉帧生成                       | `validation/simulation/renderer.py:38` `render_frames(scenario) -> list[np.ndarray]`                                        | VisualComposer 的 background layer |
| 静音 MP4 写出                   | `validation/simulation/renderer.py:70` `export_mp4(scenario, frames, path, fps)`                                            | VideoMuxer 参考实现                   |
| 场景编译                        | `validation/scenario/compiler.py:27` `ScenarioCompiler.compile(scenario, mode='frames')`                                    | 提供 `frames` + `audio_events`      |
| 在线 TTS                      | `audio/tts/provider.py:52`（`class EdgeTTSProvider`）/ `:57`（`def synthesize(text, voice, rate, pitch, out_path)`）                          | AudioComposer（仅 D3-B 旁白）              |
| TTS 配方合成                    | `audio/tts/generator.py:165` `generate_scenario(sc, out_dir, provider, fixtures_root)`                                      | AudioComposer（仅 D3-B 旁白）              |
| 既有范式                        | `scenarios/audio/*.yaml` + `scripts/run_audio_scenarios.py`                                                                 | demo 脚本约定对齐                       |
| 落盘排除                        | `.gitignore:45 *.mp4`、`:68 out/`、`:72 generated/`                                                                           | case.mp4 天然不入库                    |

**关键事实**：

- `EvidenceProjection` 与 `EvidenceGraph` **已在 D1/D2 落地**（loader + schema），D3 直接复用，**绝不重新建模事实层**——管线阶段 1–2，且保证与 D1/D2 视觉/高亮类别一致（`_STAGE_TO_GRAPH_CATEGORY` / `_DECISION_KIND_TO_GRAPH_CATEGORY` 同常量）。
- `validation.Scenario.audio: list[AudioEventSpec]`（`scenario.py:159`）是**音频感知事件声明**（cross-modal 关联用，非旁白文本）；`audio/tts/generator.py:29` 的 `Scenario` 是**独立 TTS 配方模型**。D3 旁白走 TTS 配方路径。
- `cv2.freetype` 在 opencv-python 4.13 **未编译** → `cv2.putText` **不渲染中文**（见 §9 D3-7）。

> **行号核验声明（v6 新增 · 对应 Owner 评审 1.3）**：上表所有 `文件:行号` 引用均已对照 **origin/main HEAD（`1db45df`）** 于 2026-08-12 实测核验（loader:173 / renderer:38,70 / compiler:27 / generator:165 均命中；provider.py:52 实为 `class EdgeTTSProvider`，`synthesize` 在 :57；scenario.py 的 `audio` 字段在 :159）。**复用结论（复用对象与真实签名语义）以代码为准，行号仅为定位锚点**；若实现期代码已漂移导致行号失效，**仅修正本表行号**并在 §8 修订记录 v6 条目登记，不得因此改动复用结论或动摇设计。

---

## 2. 架构（8 阶段管线 + 落点 · 8 = 1 投影(EvidenceProjection) + 1 中间数据(EvidenceGraph) + 6 新建 D3 阶段）

### 2.0 · 管线总览（强制 Storyboard + VisualScene 双中间层）

```
artifact (ScenarioEvidence = IntegrationReport+DecisionTrace+Episode+CrossModalLink+Frames 的投影)
   │
   │  ── 阶段 1–2：复用已落地（D1/D2）──
   ↓ EvidenceProjection   (visualizer/loader — 已落地)
   ↓ EvidenceGraph        (visualizer/schema EvidenceGraph — 已落地，事实层单一真相源)
   │
   │  ── 阶段 3–8：D3 新建 ──
   ↓ NarrativePlan        (NarrativeTemplateCompiler：EvidenceGraph + ScenarioTemplate → 解释策略模板实例化；非规则引擎、非写故事)
   ↓ Storyboard           (分镜：何时播、给谁看、引用哪些证据 —— 时间维)
   ↓ VisualScene          (VisualSceneGraph：每 shot 如何空间排布 —— 空间维/表达层)
   ↓ VisualComposer       (SVG 矢量信息层 ┐
                              ├→ 合流 Frame Stream → 逐 shot 帧序列)
   ↓            Caption 文本层 ┘
   ↓ VideoMuxer           (OpenCV 写无声 mp4；D3-B 时 ffmpeg 合成旁白)
   │
generated/demo_videos/<scenario_id>__v<ver>/
   ├── case.mp4
   ├── storyboard.yaml        ← 分镜伴生（可审计：叙事是否忠于证据）
   ├── provenance.json        ← 生成溯源（scenario_id+seed+fingerprint+各阶段输入哈希）
   └── audio.wav             ← 【D3-B only】（旁白；音效递延 D3-C，默认不做）
```

> **两个中间层缺一不可**（电影工业类比）：导演不会「摄像机 → 成片」，而是「剧本 → 分镜 → 拍摄计划 → 素材 → 剪辑」。D3 同理——
> - 直接把 `decision_evidence` 数组逐条拍成画面 → 「高级日志播放器」（frame + 字幕 + 水印 + JSON 字段）。
> - `NarrativePlan → Storyboard` 是把事实**重排为因果弧线**的「剧本→分镜」步骤；
> - `VisualScene` 则是把分镜**翻译为空间视觉语言**的「拍摄计划」步骤。
> 三者（Plan/Storyboard/VisualScene）共同是 D3 区别于「录屏」的根本。

### 2.1 · EvidenceProjection + EvidenceGraph（阶段 1–2 · 复用已落地）

- 复用 `visualizer/loader._build_decision_evidence`，输入 scenario artifact → `ScenarioEvidence`（含 `decision`、`decision_evidence`、`graph`、timeline events）。
- `graph`（`EvidenceGraph`）即**事实层**单一真相源；节点带 `ntype` 与 `ref`（溯源到源 artifact），边带 `relation`。
- **纪律**：下游三阶段（Plan/Storyboard/VisualScene）只允许**引用**图节点（`evidence_refs`），不允许**发明**节点（fail-closed，与 D1/D2 同源同纪律——禁 synthetic node）。

### 2.2 · NarrativeTemplateCompiler（阶段 3 · 模板实例化 · 非规则引擎、非 Planner）

**目标**：把 `EvidenceGraph` 的**事实结构** + **场景类别模板** 编译成 **NarrativePlan（解释策略）**。

**v5 关键收紧——v1 不是 Planner，是 Template Compiler**：

- ❌ **不叫 Planner、不是规则引擎**：若 v1 写成 `if visitor_repeat: add_detection_scene()` / `if confidence_high: add_reasoning_scene()` 这类「复杂 if-else 系统」，它就退化成了规则引擎——既难维护、又随场景类别爆炸，违背「三层分离」初衷，也违背 §0.3 红线。
- ✅ **正确形态**：v1 是 **模板编译器**——`EvidenceGraph + ScenarioTemplate = NarrativePlan`。场景类别（elderly_dwell / fraud / …）各对应一份**固定 `ScenarioTemplate`**（声明 canonical shot 序列 + 每 shot 引用哪类 evidence ref），编译器只做**按模板填充 ref**，不产生任何分支决策逻辑。

```yaml
# narrative/templates.py 内声明（示例：elderly_warning_case_v1）
template: elderly_warning_case_v1
shots:
  - { name: context,   kind: environment,       ref_kinds: [scenario_meta] }
  - { name: detection, kind: detection_overlay, ref_kinds: [perception_event, track_id] }
  - { name: reasoning, kind: reasoning,         ref_kinds: [decision_evidence, decision_node] }
  - { name: decision,  kind: decision,          ref_kinds: [action_node, command_type] }
  - { name: closure,   kind: closure,           ref_kinds: [action_landed, episode] }
# 编译器按此模板 + EvidenceGraph 实际节点，填充每 shot 的 evidence_refs
```

**NarrativePlan 职责（解释策略，非文本）**：只决定「解释顺序与意图」——`intent` / `reasoning_chain` / `audience_question`。它**只编排证据，不撰写任何自然语言句子**（句子由下游 `storyboard/generator` 从证据值 + 文案常量填充）。

```python
def instantiate_narrative_template(evidence: ScenarioEvidence, template: ScenarioTemplate) -> NarrativePlan:
    """纯函数 · 确定性 · 模板实例化。
    从 evidence.graph 取节点，按 template.shots 的 ref_kinds 填充每 shot 的
    evidence_refs；只决定顺序与意图（intent/reasoning_chain/audience_question），
    不产出任何自然语言文本、不调用 LLM、不生成图外节点、不写 if-else 分支决策。
    """
```

**NarrativePlan 结构（解释策略，非文本）**：

```yaml
narrative_plan:                          # 对应 §3 NarrativePlan schema（extra="forbid"）
  intent: explain_risk_decision          # 解释意图枚举（见 §3）
  reasoning_chain:                       # list[ReasoningStep]：由图拓扑 + 模板推导，非自由叙事
    - { step_kind: observation,   ref: event_abnormal_dwell_001 }   # 引用 Event 节点（图实证）
    - { step_kind: interpretation, ref: rule_abnormal_dwell }       # 引用规则/统计结论（图实证）
    - { step_kind: policy_match,  ref: policy_abnormal_dwell_v3 }   # 引用 policy 节点（图实证）
    - { step_kind: decision,      ref: decision_warn_001 }          # 引用 Decision 节点（图实证）
  audience_question: "为什么系统认为需要关注？"  # 本片要回答的观众疑问（模板常量，非生成）
  # audience: general                    # 可选，默认 general
```

> 注意：`reasoning_chain` 每一步都**指向 EvidenceGraph 中的真实节点/边**；编译器只做「挑选 + 排序 + 按模板填 ref」，不创造任何图外内容。这是「编排证据」与「撰写故事」的根本分野——前者可审计、可复现，后者不可控。

**canonical 5-shot 弧线（模板默认产出）**：

| scene       | 目的（purpose）          | 典型 evidence_refs                                           |
| ----------- | -------------------- | ---------------------------------------------------------- |
| `context`   | 建立环境（家门口·时间窗·脱敏角色标签） | scenario 元信息                                               |
| `detection` | 展示异常行为发现（停留/重复来访/接近） | perception 类 Event 节点 + track_id                           |
| `reasoning` | 解释为什么报警（决策追溯）        | decision_evidence（evidence/reasoning/outcome）+ Decision 节点 |
| `decision`  | 展示风险判断与动作            | Action 节点 + command_type                                   |
| `closure`   | 展示闭环完成（通知/协同确认）      | Action 落地 + Episode 创建                                     |

> 若 `evidence.graph` 含 cross_modal 边，则在 `reasoning` 后插入可选 `cross_modal` scene。所有 scene 的 `evidence_refs` 必须能在 graph 解析（fail-closed）。
>
> **未来升级路径（不写入 v1）**：若某场景类别确实需要「智能规划」（而非固定模板），再将 `Template Compiler` 升级为 `Planner`——但**管线形状不变**（Template/Planner → Storyboard → VisualScene → Video），仅替换阶段 3 内部实现，且仍受 §0.3 红线约束（禁 LLM 自由生成叙事）。

### 2.3 · StoryboardGenerator（阶段 4 · 分镜 · 时间维 · 强制中间层）

**目标**：NarrativePlan → `Storyboard`（分镜 YAML）。这是电影工业的「分镜」产物，是 D3 可审计性的核心——**叙事是否忠于证据，看 storyboard 即可**。

- **自动产出**：`generate_storyboard(plan, evidence) -> Storyboard`（默认，确定性零作者成本）。
- **作者覆盖**：赛前精修 demo 可用 `visualizer/video/scenarios/<demo_id>.yaml` 覆盖（YAML 声明式，对齐 ADR-0032 Scenario 单一真相源思想）。
- **解析纪律**：override 的 `evidence_refs` 必须能在 `evidence.graph` 解析；解析失败 → 抛错（fail-closed），绝不静默拍平或发明节点。
- **audience 维度（v4 新增）**：Storyboard 顶层带 `audience` 字段（默认 `general`；可声明 `judges` / `investors` / `family` 等）。**同一 EvidenceGraph，不同 audience → 不同 Storyboard**（不同目的/疑问侧重/证据取舍，但引用的证据节点始终来自同一图，绝不发明）。每 shot 另带 `audience_need`（该镜头要满足观众什么信息需求）。
- **层边界纪律（见 §2.4.1）**：Storyboard 是**语义层**，只承载时间/意图/受众，**禁止携带任何空间/视觉字段**（`x` / `y` / `color` / `layout` / `font` / `shape`）——这些属于 VisualSceneGraph（表达层）。
- **产物伴生**：Storyboard 同时写为 `storyboard.yaml` 落盘（见 §6 输出布局）。

```yaml
# 同一张图，judges 受众的故事板（节选 · 仅展示 detection 单 shot 如何带 audience_need；
#   完整 5-shot 弧线 context→detection→reasoning→decision→closure(+cross_modal) 见 §2.2 canonical 表）
storyboard:
  audience: judges
  shots:
    - name: detection
      purpose: 证明检测能力可靠
      audience_need: "评委需要知道异常在哪里、如何被稳定发现"
      evidence_refs: [event_abnormal_dwell_001]
    # ...
```

### 2.4 · VisualScene（阶段 5 · NEW · 表达层 / 空间维）

**目标**：Storyboard（时间维：何时、给谁） → `VisualSceneGraph`（空间维：每 shot 如何视觉排布）。**这是 v4 最关键的新增层**。

**为什么必须独立（语义分离）**：

- `EvidenceGraph` 是**事实结构**：`Decision ─supports→ Event`，表达的是「谁支撑谁」的因果/数据关系。
- `VisualSceneGraph` 是**视觉语言**：表达的是「画面怎么摆」——例如：
  ```
  左边:  摄像头发现异常（Event 节点 → 屏幕左区 + 检测框）
  中间:  风险分析（Decision 节点 → 屏幕中区 + WARN 徽章）
  右边:  通知家属（Action 节点 → 屏幕右区 + 消息图标）
  红色箭头: 原因链（EvidenceGraph 的 supports 边 → 屏幕上的有向箭头）
  ```
  二者语义不同：**事实 ≠ 表达**。若让 EvidenceGraph 直接控制 SVG 坐标，会把「数据关系」与「版面设计」耦合，既难维护也难针对受众调整视觉重心。故插入 `VisualScene` 作为**专用表达层**，由它消费 NarrativePlan 的 `reasoning_chain` 顺序与 Storyboard 的 `evidence_refs`，产出**每 shot 的空间布局**。

**VisualSceneGraph 结构（表达层中间表示）**：

```yaml
visual_scene:
  shot: reasoning
  layout:
    - element: { ref: event_abnormal_dwell_001, region: left,   glyph: detection_box }
    - element: { ref: decision_warn_001,        region: center, glyph: warn_badge }
    - element: { ref: action_notify_001,        region: right,  glyph: message_icon }
  arrows:                                  # 由 EvidenceGraph 边映射而来（非发明）
    - from: event_abnormal_dwell_001
      to:   decision_warn_001
      style: causal_red
```

- `ref` 仍指向 EvidenceGraph 节点（fail-closed 解析）；`region` / `glyph` / `arrow.style` 是**纯视觉编排**。
- 自动产出：`design_visual_scene(storyboard, evidence) -> VisualSceneGraph`（确定性模板：reasoning shot 默认左-中-右三栏 + 红色因果箭头）。
- 作者覆盖：`visualizer/video/scenarios/<demo_id>.yaml` 可声明 `visual_override`（仅调 region/glyph/箭头样式，不得引入图外 ref）。
- **层边界纪律（见 §2.4.1）**：VisualSceneGraph 是**表达层**，只承载空间排布，**禁止携带任何解释语义字段**（`why` / `purpose` / `audience_need` / `explanation_order`）——这些属于 Storyboard（语义层）。

> **层边界契约（Layer Boundary Contract）见 §2.4.1**——Storyboard（语义层）与 VisualSceneGraph（表达层）的字段集禁区与唯一合法耦合（`ref` 链）在该节以表格硬锁定；§3 的 schema 以 `model_config = ConfigDict(extra="forbid")` 机械 enforcement（见 §3 + Owner 评审 2.6）。

### 2.4.1 · Layer Boundary Contract（Storyboard 语义层 ↔ VisualSceneGraph 表达层 · 防职责污染）

两层**都必要**（§9 D3-11 已锁定独立），但职责必须严格切分，否则几年后必然互相侵入。契约如下：

| 维度 | **Storyboard（语义层 · 时间/意图）** | **VisualSceneGraph（表达层 · 空间/视觉）** |
| --- | --- | --- |
| 负责 | 何时播（`duration_s`）、给谁看（`audience` / `audience_need`）、引用哪些证据（`evidence_refs`）、解释意图（`purpose`） | 画面怎么摆（`region`）、用什么图形（`glyph`）、箭头样式（`arrows[].style`） |
| **禁止字段** | ❌ `x` `y` `color` `layout` `font` `shape`（纯空间/视觉属性） | ❌ `why` `purpose` `audience_need` `explanation_order`（纯意图/语义属性） |
| 合法耦合 | 仅通过 `evidence_refs` 指向 `EvidenceGraph` 节点 | 仅通过 `ref` ⊆ `Storyboard.evidence_refs` 反向依附 |

> **铁律**：Storyboard 不携带任何像素级布局；VisualSceneGraph 不携带任何「为什么/给谁」的解释语义。二者唯一的合法耦合点是 **`ref` 链**：`VisualSceneGraph.ref` ⊆ `Storyboard.evidence_refs` ⊆ `EvidenceGraph.nodes`（见 §8 结构级验收断言）。
> **为何如此严格**：这正是 D3-11 的核心论据——同一张 `EvidenceGraph`，给**评委**需要 risk explanation 视觉重心、给**家属**需要 human story 视觉重心、给**工程师**需要 debug trace 视觉重心。事实一样、视觉不同。没有独立的 VisualSceneGraph，你只能复制 renderer；有了 `EvidenceGraph → Storyboard(audience) → VisualSceneGraph → Renderer`，才是正确的受众扩展点。越界即视为污染，需在 review 中打回。

### 2.5 · VisualComposer + Renderer 分支（阶段 6 · 光栅合成 → Frame Stream）

**目标**：逐 shot 把 `VisualSceneGraph` 渲染成帧序列。两个并行渲染分支合流：

```
VisualSceneGraph
   ├── SVG Renderer   → 矢量信息层 RGBA（Evidence Graph / Decision Trace / Timeline / Risk Score 的节点-箭头-徽章）
   └── Caption        → 文本层 RGBA（字幕条 / 镜头目的提示，Pillow 绘制，中文见 D3-7）
          ↓  alpha 合成到 BGR 背景帧（render_frames 输出）
          ↓
       Frame Stream（逐 shot 帧序列）
```

**构图 z-order 五层**（背景走 OpenCV，信息图层走 SVG 矢量）：

```
Scene
 ├── background layer   render_frames 输出的 BGR 摄像头帧（ADR-0032，零改写）
 ├── evidence layer     D3 叠加：bbox / track 轨迹 / SVG 节点关系图
 ├── annotation layer   检测标签、风险徽章（WARN/HIGH）、当前证据高亮
 ├── text layer         字幕条（caption）/ 镜头目的提示（Pillow 绘制，中文见 D3-7）
 └── provenance layer   强制水印 + provenance 角标（fail-closed 不可关，见 §6）
```

**信息图层用 SVG 矢量**（非全部 Pillow，用户明令）：Evidence Graph / Decision Trace / Timeline / Risk Score 这类「科技感 UI、节点关系、箭头、高亮」用 **SVG / SVG-like JSON** 建模，再光栅化叠加（见 §4）。例如 reasoning shot 的一帧：

```
+--------------------------------+
| SilverShield Demo · 程序化合成  |  ← provenance 层（强制角标）
|        [camera frame]          |  ← background 层（render_frames BGR）
|   +---------+                  |
|   | visitor |  ← track 轨迹     |  ← evidence 层（SVG 矢量叠加）
|   +---------+                  |
|  Visitor-B → abnormal_dwell → WARN → NOTIFY_FAMILY   ← annotation 层（SVG 决策追溯链）
| -----------------------------  |  ← text 层（字幕条）
| AI检测: abnormal_dwell 风险:WARN|
+--------------------------------+
```

### 2.6 · AudioComposer（阶段 7 · 声音合成 · D3-B 可选 · 三级拆分）

**目标**：生成声音轨道。**音频分三级，v1 只做前两级，绝不一次设计全**（防范围膨胀）：

| 级别 | 内容 | 默认 | 说明 |
| --- | --- | --- | --- |
| **D3-A** | Video + Subtitle + Vector Explanation | ✅ 是 | 纯视觉 + 字幕（captions-only），完全离线、确定性、零网络 |
| **D3-B** | + Narration | ❌ 选（`--with-audio`） | 旁白（EdgeTTS 合成 wav）；音画合成；**音频不可复现**、需网络 |
| **D3-C** | + Music / SFX | ❌ **递延，默认不做** | 配乐/音效属「宣传价值」非「解释价值」，边界模糊，**v1 不做**；仅当 Owner 单独裁决才补 |

- **Video Track**：VisualComposer 产出（D3-A 即有）。
- **Narration Track**：旁白（`--with-audio` 时由 `audio.tts` EdgeTTS 合成 wav，D3-B）。
- **Subtitle Track**：字幕（与 text 层同源，可抽离为 `.srt`/内嵌）。
- **Sound Effect / Music Track**：**不在 v1 设计范围内**（D3-C deferred）。

### 2.7 · VideoMuxer（阶段 8 · 合成写盘）

- 默认：OpenCV `VideoWriter` 写无声 mp4（复用 `export_mp4` 同款 fourcc 逻辑，在 D3 自有 writer 内）。
- 若 AudioComposer 产出音轨（仅 D3-B）：`ffmpeg` 合成 video+audio+subtitle → final.mp4（§5 D3-3 降级：ffmpeg 缺失则降级「无声 mp4 + 旁白 wav 双文件」+ 告警）。

### 2.8 · 落点（`src/home_perception/visualizer/video/` · 子包布局防膨胀）

```text
visualizer/video/
├── __init__.py
├── spec.py              # CaseVideoSpec / NarrationCue（pydantic，CLI 入参）
├── compiler.py          # pipeline orchestration（8 阶段驱动：从 adapter 到 muxer）
├── evidence/
│   └── adapter.py       # EvidenceProjection adapter（复用 loader，包装成 D3 入口）
├── narrative/
│   ├── templates.py     # ScenarioTemplate 声明（elderly_warning_case_v1 等固定模板，见 §2.2）
│   └── compiler.py      # instantiate_narrative_template(evidence, template) -> NarrativePlan（模板实例化，非规则引擎）
├── storyboard/
│   ├── schema.py        # Storyboard / ShotSpec（语义层 schema；禁 x/y/color/layout/font/shape）
│   └── generator.py     # generate_storyboard(plan, evidence, audience) -> Storyboard + YAML I/O
├── scene/
│   ├── schema.py        # VisualSceneGraph / VisualElement（表达层 schema；禁 why/purpose/audience_need/explanation_order）
│   └── designer.py      # design_visual_scene(storyboard, evidence) -> VisualSceneGraph
├── render/
│   ├── svg.py           # SVG/SVG-like 场景建模（节点/边/徽章，ref 来自 VisualSceneGraph）
│   ├── rasterizer.py    # VectorScene -> RGBA（Pillow-first / cairosvg 可选；经 FontRegistry 取字形）
│   ├── font_registry.py # FontRegistry：受控字体资源注册/解析（D3-7），业务代码不直接加载字体路径
│   ├── caption.py       # Caption 文本层（Pillow 绘制，经 FontRegistry 取 CJK 字形，见 D3-7）
│   └── composer.py      # Frame 合成（alpha 叠加到 BGR 背景帧）
├── audio/
│   └── composer.py      # 仅旁白 TTS（D3-B）；音效/配乐不在此（D3-C deferred）
├── mux/
│   └── muxer.py         # ffmpeg 合成 video+audio+subtitle；降级双文件
├── scenarios/           # 作者故事板 YAML（storyboard_override + visual_override），如 elderly_dwell_warning.yaml
└── assets/
    └── fonts/
        └── NotoSansCJK-Regular.ttf   # 受控字体资源（D3-7 · controlled font asset；FontRegistry 加载，不进业务代码路径）

scripts/generate_case_video.py   # CLI 入口（替代 generate_demo_video，呼应 case video）
```

> 子包化而非单大文件（`visual_composer.py` / `storyboard.py` / `visual_scene.py` 平铺）的理由：随场景类别/受众模板增长，平铺会膨胀成难以维护的大模块；按 `evidence / narrative / storyboard / scene / render / audio / mux` 切分，每层单一职责、可独立测试、可独立扩展（符合 §2.4.1 的层边界）。

**零行为变化纪律**：不改动 `render_frames` / `export_mp4` / `audio.tts` / `visualizer/loader` —— D3 仅在自有子包内做投影消费、构图与写盘；`render_frames` 输出仅作 background layer 消费。

---

## 3. Narrative / Storyboard Schema（叙事层声明式 schema）

对齐 ADR-0032 `Scenario` 的「单一真相源 + YAML 声明」思想，Storyboard 也是**声明式 YAML**（未来「AI 宣传视频工厂」的基础素材）：

```python
from pydantic import BaseModel, ConfigDict, Literal

# ── 语义层 A：解释策略（NarrativeTemplateCompiler 产出 · 非文本 · 见 §2.2）──
class ReasoningStep(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 字段集硬锁，禁越界增字段
    step_kind: Literal["observation", "interpretation", "policy_match",
                       "decision", "closure", "cross_modal"]   # 步骤语义类别（来自模板，非自由生成）
    ref: str                                    # 指向 EvidenceGraph 节点/边 id（fail-closed 解析）

class NarrativePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 字段集硬锁：严禁 text/sentence/narration
    intent: Literal["explain_risk_decision", "explain_false_positive",
                    "explain_normal_case", "explain_cross_modal"]   # 解释意图枚举
    reasoning_chain: list[ReasoningStep]        # 解释顺序（图拓扑+模板推导，非自由叙事）
    audience_question: str                      # 本片要回答的观众疑问（模板常量，非生成）
    audience: str = "general"                   # 受众维度（general/judges/investors/family...）
    # 禁用字段（机械可 enforcement）：text / sentence / narration 及任何自然语言文本字段
    # —— NarrativePlan 只编排证据节点，句子由下游 storyboard/generator 从证据值+文案常量填充

# ── 语义层 B：分镜（StoryboardGenerator 产出 · 时间维 · 语义层）──
#    禁携带空间/视觉字段：x / y / color / layout / font / shape（见 §2.4.1）
class ShotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 语义层：禁 x/y/color/layout/font/shape
    name: str                                   # context/detection/reasoning/decision/closure(+cross_modal)
    kind: Literal["environment","detection_overlay",
                  "reasoning","decision","cross_modal","closure"]
    duration_s: float
    purpose: str                                # 人类可读叙事意图（可解释性元数据）
    audience_need: str = ""                     # 该镜头要满足观众什么信息需求
    evidence_refs: list[str] = []               # 指向 EvidenceGraph 节点 id（每镜头可审计）
    narration: list[str] = []                   # 字幕/旁白逐句（由模板从证据值填充，非自由文本）

class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 语义层：禁 x/y/color/layout/font/shape
    demo_id: str
    title_zh: str
    scenario_ref: str                           # 关联 validation Scenario YAML（事实层来源）
    audience: str = "general"                   # 受众维度（general/judges/investors/family...）
    shots: list[ShotSpec]
    version: int = 1

# ── 表达层：视觉场景图（VisualSceneDesigner 产出 · 空间维 · 表达层）──
#    禁携带解释语义字段：why / purpose / audience_need / explanation_order（见 §2.4.1）
class VisualElement(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 表达层：禁 why/purpose/audience_need/explanation_order
    ref: str                                    # 指向 EvidenceGraph 节点（fail-closed）
    region: Literal["left","center","right","full"]
    glyph: Literal["detection_box","warn_badge","message_icon","timeline","risk_score"]

class VisualSceneGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 表达层：禁 why/purpose/audience_need/explanation_order
    shot: str
    layout: list[VisualElement]
    arrows: list[dict] = []                     # from/to 指向 evidence_refs；style 仅视觉
```

- **层边界（见 §2.4.1）**：`ShotSpec`/`Storyboard`（语义层）schema **不得**新增 `x`/`y`/`color`/`layout`/`font`/`shape` 字段；`VisualElement`/`VisualSceneGraph`（表达层）schema **不得**新增 `why`/`purpose`/`audience_need`/`explanation_order` 字段；`NarrativePlan` **严禁** `text`/`sentence`/`narration` 自然语言文本字段。上述字段集已由各 schema 的 `model_config = ConfigDict(extra="forbid")` **机械锁定**——越界即 `ValidationError`，review/CI 可据此打回（对应 Owner 评审 2.6）。

---

## 4. Visual Language / SVG Strategy（矢量信息图层）

明确信息图层的建模与光栅化策略，避免「用 Pillow 硬画复杂 UI」的坑：

1. **建模**：信息图层（Evidence Graph / Decision Trace / Timeline / Risk Score）以 **SVG（或 SVG-like JSON）** 描述（节点 `{id,x,y,shape,label,color}`、边 `{from,to,marker}`、徽章 `{text,box}`）——它是 D3 内部声明式中间表示，与 D2 ECharts 图共享同一份 `evidence.graph` 数据，仅渲染目标不同（屏幕 DOM vs 视频帧 RGBA）。**Mapping 边界**：SVG 节点/边的 `ref` 来自 `VisualSceneGraph`（→ 最终来自 EvidenceGraph），`VisualSceneGraph` 是唯一允许把「事实」翻译成「版面」的地方（见 §2.4）。
2. **光栅化（Pillow-first，确定性）**：`rasterizer.py` 用 `PIL.ImageDraw` 把矢量场景绘成 RGBA（矩形/线/圆/三角箭头 marker/文本），再 alpha 合成到 BGR 帧。**零新原生依赖、确定性**。
3. **箭头 / marker**：Pillow 无原生 arrow marker → `rasterizer` 自绘三角形箭头（按边方向计算顶点），保证 reasoning shot 的「节点-箭头-WARN 矩形」因果链可读。
4. **cairosvg 升级路径（可选）**：真 SVG 保真（曲线/渐变/自动定向 marker）可换 `cairosvg` 渲染同一份 SVG；但属优化项，**默认不引**，需 Owner 另行裁决（守 ADR-0032 §3 #8 零新重依赖）。
5. **动画**：reasoning shot 内部按帧推进「当前证据高亮」扫过 trace 节点（由 shot 内帧索引确定性驱动），复用 D2 replay「step through evidence」思想，但烘焙为帧序列。
6. **确定性**：矢量叠加是纯函数（给定 shot 状态 → 唯一 RGBA），不引入随机/时间。

---

## 5. 音频策略（三级拆分 · 见 §2.6）

### D3-2 · 离线字幕 vs 在线 TTS（关键权衡）

- `EdgeTTSProvider` 依赖 **Microsoft Edge TTS 在线 API**（需网络、音波形随服务变化）。
- **D3-A（默认）**：**纯视觉 + 字幕**（captions-only），完全离线、确定性、零新依赖。
- **D3-B（增强）**：启用 TTS 旁白（在线），音画合成；视觉确定性保留，但**音频不可复现**且需网络。
- **建议**：默认 D3-A，`--with-audio` 启用 D3-B。
- **D3-C（音效/配乐）**：**v1 不做**，仅当 Owner 单独裁决补入（属宣传价值，非解释价值，边界模糊，易触发 §0.3 红线）。

### D3-3 · 音视频封装（OpenCV 不写音频 · ffmpeg 非 pipeline 核心）

- **原则**：`VideoMuxer` 的封装能力**不是 pipeline 核心**——D3 的核心产物是「解释视频（确定性帧序列）」，媒体封装只是出口步骤；ffmpeg 缺失**不得导致 pipeline fail**。
- OpenCV `VideoWriter` 仅能写无声视频。封装音频走：
  - **(a) 系统 `ffmpeg` CLI**（无新 Py 依赖；但 CI/边缘可能无 ffmpeg）→ 缺失时**优雅降级**为「无声 mp4 + 旁白 wav 双文件」+ `warning.json` 说明降级原因。
  - (b) imageio-ffmpeg（捆绑 ffmpeg，新依赖，违反 ADR-0032 §3 #8 零新依赖）→ **否决**。
- **降级契约（伪代码，fail-soft 非 fail-hard）**：

```python
def mux(video_frames, audio_track=None) -> MuxResult:
    if ffmpeg_available() and audio_track is not None:
        return MuxResult(final_mp4=ffmpeg_mux(video_frames, audio_track))   # 视频+音频 → mp4
    # 降级：pipeline 不失败，核心产物照常交付
    result = MuxResult(video_mp4=write_silent_mp4(video_frames))
    if audio_track is not None:
        result.audio_wav = write_wav(audio_track)        # 旁白 wav 双文件
        result.warning = WarningInfo(
            code="FFMPEG_MISSING",
            message="ffmpeg 不可用，已降级为 video.mp4 + audio.wav 双文件交付（非 pipeline 失败）",
        )
    return result
```

- **建议**：D3-B 走 (a)，ffmpeg 缺失按上述降级契约交付 `video.mp4` + `audio.wav` + `warning.json`，而非让 pipeline 失败。

---

## 6. 确定性边界、强制叠加与输出布局

### D3-4 · 脱敏 + provenance 强制叠加（不可关）

- 每帧强制（fail-closed，不可配置关闭）：
  1. `程序化合成 · 非真实录像` 水印（防误当真实监控录像，呼应 ADR-0035 非目标）；
  2. provenance 角标（`scenario_id` + `seed` + `generator.fingerprint`）；
  3. 角色标签脱敏（D7b：`Visitor-B` / `Resident-A`，禁真实姓名/设备序列号/家庭地址）。
- 来源：ADR-0035 D7b + D8 + ADR-0032 T2（无真实媒体）。

### D3-5 · 视觉确定性（验收口径）

- **视觉帧**：`render_frames` 已确定性（ADR-0032 T1，受 `generator.fingerprint` 版本锁定）。
- **叙事层**：`instantiate_narrative_template` 纯函数 → 同 evidence 同 NarrativePlan；`generate_storyboard` 纯函数 → 同 plan 同 Storyboard；`design_visual_scene` 纯函数 → 同 storyboard 同 VisualSceneGraph；构图/矢量叠加纯函数 → 同 shot 状态同帧。
- **字幕/水印**：确定性绘制。
- **音频（D3-B）**：不可复现（TTS 在线）。
- **验收仅断言视觉确定性**（逐帧 `np.array_equal` / 字节一致）。

### D3-6 · 产物落盘与命名

- 目录：`generated/demo_videos/<scenario_id>__v<ver>/`（已被 `.gitignore` 排除 `*.mp4`/`out/`/`generated/`，不入库）。
- 伴生文件（**默认一并产出，供审计**）：
  - `case.mp4` —— 叙事案例视频（视觉确定性）；
  - `storyboard.yaml` —— 分镜（证明叙事忠于证据，含 `audience` / `audience_need`）；
  - `provenance.json` —— 生成溯源（`scenario_id` + `seed` + `generator.fingerprint` + 各阶段输入哈希）；
  - `audio.wav` —— 仅 D3-B（旁白；音效递延 D3-C，默认不产出）。
- 作者故事板 YAML（`visualizer/video/scenarios/*.yaml`）**入库可版本化**。

---

## 7. 实施切片（建议小步，独立 PR + Owner 评审）

- **Slice D3-A（叙事骨架 + 纯视觉案例视频 · 默认路径 · 零音频）**：  
  `spec.py` + `compiler.py`（8 阶段编排）+ `evidence/adapter.py`（复用 loader）+ `narrative/templates.py` + `narrative/compiler.py`（`compile_narrative` 模板实例化，非规则引擎）+ `storyboard/schema.py` + `storyboard/generator.py`（`generate_storyboard` + audience 维度 + YAML I/O）+ `scene/schema.py` + `scene/designer.py`（`design_visual_scene` 表达层）+ `render/svg.py` + `render/rasterizer.py` + `render/caption.py` + `render/composer.py` + `render/overlay.py` + `mux/muxer.py` + `scripts/generate_case_video.py` + 1 个作者故事板（`visualizer/video/scenarios/elderly_dwell_warning.yaml`）+ 测试：
  - **叙事层（非文本 · 模板实例化）**：`instantiate_narrative_template` 产出 canonical 5-shot，`reasoning_chain` 每一步均能在 graph 解析、`intent` 来自枚举；**断言不含任何 if-else 分支决策逻辑**（非规则引擎）；
  - **Storyboard 强制中间层 + audience**：`generate_storyboard` 产出 yaml 且含 `audience`/`audience_need`，可 round-trip 解析为同一 Storyboard；不同 `audience` 产出不同 storyboard 但 `evidence_refs` 始终来自同一图；schema 不含空间字段（层边界契约）；
  - **VisualScene 表达层**：`design_visual_scene` 产出含 `region`/`glyph`/`arrows`，`ref` 全部可解析（fail-closed：伪造 ref 必须报错）；schema 不含语义字段（层边界契约）；
  - **视觉确定性**（同输入 → 逐帧 `np.array_equal`）；
  - **字幕内容命中**（caption 出现在正确 shot 区间）；
  - **矢量叠加**：reasoning shot 含 Decision Trace 节点+箭头（结构断言，非像素）；
  - **水印/provenance 存在性** + **脱敏断言**（产物不含 PII/真实路径/设备序列号）；
  - **零 import 边界 AST 测试**（visualizer/video 不 import runtime/evaluation/integration/memory）。
- **Slice D3-B（音频增强 opt-in）**：  
  `audio/composer.py` + ffmpeg mux + D3-3 降级路径 + 测试（网络/ffmpeg 依赖按 CI 跳过或仅结构断言）。
- **Slice D3-C（可选 · 递延 · 默认不做）**：仅当 Owner 单独裁决需要音效/配乐时执行；否则永不开工（守 §0.3 红线）。
- **cairosvg 升级（可选）**：仅当 Owner 裁决需要真 SVG 保真时执行，引入 `cairosvg` 替换 Pillow-first 光栅化；默认不做。

每片独立 PR + `ruff check src tests` + 全量 `pytest` 零回归（D9）。

---

## 8. 验收标准（草案 · 自 ADR-0035 第 7 节派生）

1. **复用契约**：D3 仅调用 `render_frames`/`export_mp4`/`audio.tts`/`visualizer.loader`，不重写帧渲染（零行为变化）。
2. **叙事层存在且可解释**：每个 shot 有 `purpose` + `audience_need` + `evidence_refs`；`evidence_refs` 全部能在 `evidence.graph` 解析；无「裸 JSON 字段」镜头；`storyboard.yaml` 伴生产出。
3. **三层分离可验证**：NarrativePlan（由 NarrativeTemplateCompiler 产出）只含 `intent`/`reasoning_chain`/`audience_question`（无自由文本句子、无 if-else 分支决策）；Storyboard 与 VisualSceneGraph 分文件/分 schema，且后者 `ref` 全部能在前者 `evidence_refs` 解析；两 schema 字段集分别遵守 §2.4.1 层边界（语义层无空间字段、表达层无语义字段）。
4. **零新重依赖**：音频走既有 `edge-tts`（dev 依赖）+ 系统 `ffmpeg`；若 D3-7 选 (a) 则仅新增轻量 `Pillow`；cairosvg 不默认引入；**无音效/配乐/多格式依赖**。
5. **视觉确定性（帧级）**：同 scenario 两次生成 `case.mp4` 视觉逐帧 `np.array_equal` 一致（指纹版本锁定）。
6. **脱敏 + provenance**：每帧含水印+角标；角色标签脱敏；产物不含 PII/真实路径/设备序列号。
7. **零 import 边界（含单向依赖锁定）**：AST 测试**双向**确认——(a) `visualizer/video` 不 import `runtime/evaluation/integration/memory`；(b) `validation`/`audio` 纯消费例外已见**主 ADR §3 授权条款**（D3-1 已决），仅 `visualizer.video → validation.simulation.renderer` 与 `visualizer.video → audio.tts` 单向存在，**不存在任何** `validation → visualizer.video` / `audio → visualizer.video` 反向 import（presentation adapter dependency，不得成为业务依赖）；(c) `visualizer/video` 仅在 `narrative/evidence/audio` 子包内 import `validation`/`audio` 既有栈，且仅读取、不触发验证判定、不反向依赖生产决策。
8. **非目标守住**：无真实录像、无实时、不入库 mp4、不接 CI 门禁、不改 production 行为、非「HTML 录屏」、无音效/配乐、无 Web 栈。
9. **结构级一致性（artifact-level · 防「逻辑正确但视频错」）**：逐帧一致**不足以**证明叙事忠于证据，必须额外断言：
   - **Story consistency**：`Storyboard.evidence_refs` 的每个 id 必须 ∈ `EvidenceGraph.nodes`（断言 ⊆）；否则即便逐帧一致，叙事已偏离证据。
   - **Scene consistency**：`VisualSceneGraph` 的每个 `ref` 必须 ∈ 对应 shot 的 `Storyboard.evidence_refs`（断言 ⊆）——表达层不得引用语义层未引用的证据。
   - **Frame provenance**：逐帧强制水印/角标**必须包含 `scenario_id`**（字符串命中断言），防止「逻辑正确但 provenance 漏打」。
   - **Duration consistency**：`sum(shot.duration_s)` 必须等于生成视频时长（±1 帧容差）；防止时间轴错乱（逻辑对、但视频少拍/多拍）。
10. **文档**：本设计评审定稿后，D3 实现 PR 描述附合成视频产物清单 / 截图 / `storyboard.yaml` 快照。
11. **证据所有权边界（D3-12）**：D3 不得新建 `EvidenceNode` / `EvidenceEdge`——证据事实的唯一真相源在 `validation`/`runtime` 既有栈；D3 仅经 `evidence/adapter.py` 做只读 Projection View（可缓存 `NarrativePlan`/`Storyboard`/`VisualSceneGraph`）。验收：静态/结构断言确认 D3 源文件**无 `EvidenceNode`/`EvidenceEdge` 构造调用**，所有证据引用均来自投影输入，杜绝「视频里有事实、系统里没事实」的审计破坏。

---

## 9. Owner Decision Record（D3-1 ~ D3-12 · 共 12 项 · 全部已决）

| #                | 决策点                                         | 推荐                                                                                           | 影响落点/依赖                                      |
| ---------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **D3-1（v6 已决 · v7 锁定单向依赖）** | 导入边界（visualizer 能否 import validation/audio） | **已授权（纯消费）** + **单向依赖约束**：允许 `visualizer.video → validation.simulation.renderer` 与 `visualizer.video → audio.tts`；**禁止** `validation → visualizer.video`、`audio → visualizer.video`。该依赖性质属「**presentation adapter dependency**，不得成为业务依赖」——`visualizer/video` 只在自有子包内读取 `validation`/`audio` 既有栈（`render_frames`/`export_mp4`/`audio.tts`），不触发验证判定、不反向依赖生产决策、不引入运行期副作用；主 ADR §3 已增补对应例外 + 单向约束条款 | 决定 D3 落 `visualizer/video/` |
| **D3-2（已决）**     | 音频默认策略                                      | 默认 D3-A 纯视觉字幕，`--with-audio` 在线 TTS（D3-B）；D3-C 音效/配乐默认不做                                         | 决定是否走网络                                      |
| **D3-3（v7 锁定非核心降级）** | ffmpeg 封装缺失降级 | **ffmpeg 不是 pipeline 核心**：缺失时 `VideoMuxer` 降级为 `video.mp4` + `audio.wav` + `warning.json`（fail-soft，非 pipeline fail）；有 ffmpeg 才合成 `final.mp4` | 无新依赖（系统 ffmpeg；imageio-ffmpeg 否决） |
| **D3-7（v7 锁定字体资源抽象）** | 中文叠加字体 | **引入受控字体资源（controlled font asset）**：落点 `visualizer/video/assets/fonts/NotoSansCJK-Regular.ttf`，经 `FontRegistry → Rasterizer` 抽象——业务代码（caption/rasterizer）**不直接加载字体路径**，统一向 `FontRegistry` 取字形，由 `Rasterizer` 负责光栅化（防 caption.py/renderer.py/overlay.py 各自散加载字体） | 受控字体资源（非 Py 依赖；Pillow 既已存在则零新依赖） |
| **D3-8（v3 强化 · 已决）**  | 矢量信息图层光栅化默认方案                               | 信息层**用 SVG/矢量场景描述建模**（非全部 Pillow）；MVP 用 **Pillow-first** 光栅化（确定性、零原生依赖），`cairosvg` 仅作可选高保真后端 | 决定 cairosvg 是否必要 / 是否引入                     |
| **D3-9（v3 已决 · v5 强化 · v7 正式废弃 Planner 命名）** | 叙事生成方式 | **固定模板映射，禁 LLM 自由发挥**；v5 明确为「模板实例化、非规则引擎、非 Planner」；**v7 正式废弃 `NarrativePlanner` 别名**——v1 规范名锁定为 `NarrativeTemplateCompiler`，未来若升级为智能规划仍沿用该规范名（不复兴 Planner 命名） | NarrativeTemplateCompiler 实现约束 + 命名冻结 |
| **D3-10（v3 新增 · 已决）** | 伴生文件默认产出                                | 默认产出 `storyboard.yaml` + `provenance.json`（审计必需）                                         | 输出布局                                         |
| **D3-11（v5 已决 · Owner 确认）** | VisualSceneGraph 是否独立成层              | **必须独立**（EvidenceGraph 不直接控制 SVG；表达层与事实层分离，是「同图多受众不同视觉」的正确扩展点，见 §2.4.1） | 落点 `scene/schema.py` + `scene/designer.py`；Storyboard/VisualScene 职责由 §2.4.1 契约锁定 |
| **D3-12（v7 新增 · Owner 认可）** | Evidence ownership boundary（证据所有权边界） | **D3 不拥有 `EvidenceGraph`，只拥有 Projection View**：D3 内部可缓存 `NarrativePlan` / `Storyboard` / `VisualSceneGraph`，但**禁止新建 `EvidenceNode` / `EvidenceEdge`**——否则会出现「视频里有事实、系统里没事实」的审计破坏。证据事实的唯一真相源始终在 `validation`/`runtime` 既有栈，D3 仅经 EvidenceProjection 做只读投影消费 | 落点 `evidence/adapter.py`（Projection View）；§8 验收新增「无新 EvidenceNode/Edge」断言 |

> **全部 12 项决策已决（Owner Decision Record）**：D3-1（导入边界 + v7 单向依赖锁定）/ D3-2（音频默认）/ D3-3（ffmpeg 非核心降级）/ D3-7（受控字体资源抽象）/ D3-8（矢量光栅化默认）/ D3-9（固定模板 + v7 正式废弃 Planner 命名）/ D3-10（伴生文件）/ D3-11（VisualSceneGraph 独立）/ **D3-12（Evidence ownership boundary，v7 新增）**。  
> 本文件已从「Design Proposal / 待裁决」升级为 **Owner Decision Record**：决策已锁定，仅待主 ADR-0035 正文冻结，AI 即进入实现（独立 PR + Owner 评审）。  
> 仍保留的「实现期开放选择」（非架构裁决，不阻塞冻结）：D3-8 的 cairosvg 是否引入、D3-7 的具体字体子集范围——均属实现细节，按既定默认（Pillow-first / NotoSansCJK 子集）执行即可。

---

## 10. 未来方向（v4 降级 · 仅模块边界预留，非路线图承诺）

> **红线重申（对应 Owner 评审 2.4 · 与 §0.3 同）**：无论未来是否将阶段 3 升级为智能规划，**始终受 §0.3 红线约束——禁 LLM 自由生成叙事，叙事只能在既有模板空间内做选择**（选模板 / 选 shot 顺序 / 选 audience），绝不允许模型脱离模板自由撰写解说词或编造证据外文本。升级只是「在模板空间内做更智能的选择」，不改变「确定性映射」与「离线」本质。
>
> **命名澄清（D3-9 v7 正式废弃 Planner）**：下文「升级为 Planner」中的 **`Planner` 是已废弃的别名**（历史上曾称 `NarrativePlanner`，v5 已更名为 `NarrativeTemplateCompiler`）；v1 规范名锁定为 `NarrativeTemplateCompiler`，**任何未来升级沿用该规范名，不复活 Planner 命名**，以免与「搜索/推理/优化/决策/LLM Agent」等暗示混淆——它本质仍是「EvidenceGraph + Template → NarrativePlan」的确定性映射。

D3 跑通后，其 `NarrativeTemplateCompiler → Storyboard → VisualScene` 三段纯函数链路在**模块边界**上为「叙事层」预留了扩展位：

- 当前版本（v1）**仅实现确定性模板映射**：`EvidenceGraph →（ScenarioTemplate 模板实例化）→ NarrativePlan → Storyboard → VisualSceneGraph → Video`。不引入 LLM、不在线生成、不反哺 runtime。
- **升级路径（不写入 v1）**：若某场景类别确需「智能规划」而非固定模板，可将阶段 3 的 `NarrativeTemplateCompiler` 内部升级为更智能的选择逻辑——但**管线形状不变**（X → Storyboard → VisualScene → Video，X = Template Compiler 或未来的智能规划实现），仅替换阶段 3 内部实现，且仍受 §0.3 红线约束（禁 LLM 自由生成叙事）；**规范名仍为 `NarrativeTemplateCompiler`**。
- 若未来需要更丰富的表达（多受众模板库、可解释性报告导出、事故复盘视频等），可在**不改变管线形状与模块边界**的前提下，仅扩展 `narrative/` / `storyboard/` / `scene/` 内部的模板与 schema，**受 §0.3 红线约束**（不演变为交互式平台/在线生成服务）。
- 本设计**不承诺**任何具体未来子系统（如 AI Scientist / 自动研究报告 / 自动事故视频）；上述仅描述「边界预留」这一工程事实。
