# 黄金案例集（Golden Scenario Set）· 数据准备文档 v2

- **状态**：规划（Owner 9/10 评审 + 6 处收紧已合入 v2）
- **日期**：2026-08-15（v1 → v2：prior_episodes 语义收紧 / 产品-工程命题分离 / case 改名 / media_alignment / Outcome 行为契约 / 覆盖矩阵 / CI 表述收敛）
- **决策者**：Owner
- **相关**：docs/design/golden-case/DESIGN-demo-v2-product-restore.md（P0-1~P1 产品链）/ ADR-0034（闭环集成验证）/ ADR-0036（统一 Case Viewer）

---

## 0. 摘要（v2 收敛表述）

> **数据、CI、产品媒体三者的关系（v1 已定义，v2 冻结为总原则）：**
> - **CI 定义和验证系统应该如何工作**（考题 + 判卷）；
> - **AI/真实视频负责让人理解案例**（讲题的画面）；
> - **Golden Scenario Set 把两者绑定成同一道"考题"**。

**CI 表述收敛（v2 修正）**：现有 `build_trusted_case` **足以生产和门禁现有单会话黄金场景**；Golden Scenario Set 将在此基础上**新增跨日 Memory、闭环 Outcome 等产品级考题**（跨日 Memory 未实现 / P0-1 Resolution 未完全接入 / Live Frame Source 未实现 / Audio Media 时间绑定在 P0-3）——避免"CI 已全部完成"的错觉。

**三条冻结原则（v2 新增 · 数据工程总原则）**：
```
程序化视频 = 系统可验证性    AI/真实视频 = 人类感知真实性
CI        = 考题 + 判卷       Case Viewer  = 讲题
Video     = Media            Runtime      = Evidence Producer
```

---

## 1. 目标

准备 **6 个有明确产品命题** 的黄金案例，每个 case 同时拥有完整 Case Manifest（10 要素），由 CI 统一生产、可确定性复现、可 gate 验收：

```
Case Manifest
├── 视频媒体（视觉轨）            ├── 历史 Episodes（跨日 prior —— G0-3 核心）
├── 音频媒体（并行轨）            ├── AudioPerceptionEvent
├── 当前事件（detections/visitor）├── CrossModalLink
├── Decision                      ├── Action
└── Expected Outcome（产品行为契约，v2 升级，见 §6）
```

---

## 2. 核心判定标准：CI 生成 vs AI 视频（v2 保留 + 冻结）

> **凡是"系统要证明什么"的，用 CI 确定性生成（Evidence）；凡是"人要感受到什么"的，用 AI/真实视频（Media）。**

| 判定维度 | 用 CI 确定性生成 | 用 AI / 真实视频 |
| --- | --- | --- |
| 核心目的 | 可复现、可断言、可 gate | 人感、真实画面、10 秒看懂 |
| 适用内容 | 感知事件/几何、音频感知、**历史记忆**、决策/行动、跨模态关联、Expected Outcome | 视觉媒体轨（Case Video 主轴画面） |
| 本质 | **Evidence（感知结果）** | **Media（媒体资产）** |

**为什么不用程序化视频当主视频**：程序化视频 = 系统可验证性，但人类感知真实性差（黑屏/合成感，已实测）。AI/真实视频补齐"人感"。**为什么 AI 视频不能直接成为 Evidence**：Video = Media，Runtime = Evidence Producer——检测/事件/记忆/决策不来自视频跑 YOLO，而来自 CI 场景声明（确定性、可 gate）。

---

## 3. 六个黄金 Case（v2：产品命题 / 工程命题分离 + 改名）

| Case | Product Question（产品命题） | Engineering Assertion（工程断言） | 视觉媒体轨 | 历史记忆 | 音频轨 | 决策→行动→Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| **benign** | 为什么没报警？ | TN / suppression with reason | 快递员正常到访（复用 Delivery） | — | — | 不报警 · LOG_ONLY · 无命令 |
| **stranger_visit** | 什么情况开始值得关注？ | abnormal_dwell → LOW | 陌生人徘徊（需准备） | —（首访） | 可选 | LOW/MONITOR → 仅记录 |
| **repeated_visit** | 系统真的记得过去吗？ | **prior_episodes 被检索并影响 Decision** | 同人三幕到访（需准备） | **✓ 跨日（核心）** | — | 风险升级 → NOTIFY_FAMILY |
| **telephone_risk**（v2 改名，原 telephone_scam） | 多模态为什么更有用？ | Vision + Audio → CrossModalLink | 老人手持电话（需准备） | 可选 | **✓ 持续电话（合成）** | WARN → 通知家属 |
| **high_risk** | 发现风险后真的能处理吗？ | Decision → Action → Resolution | 深夜多段到访（复用 CCTV） | ✓ 短时重复 | 可选 | HIGH → ESCALATE_COMMUNITY → 家属/社区闭环 |
| **ambiguous** | 为什么没有误报？ | evidence insufficient → SUPPRESS/MONITOR | 边缘短暂人影（需准备） | — | — | 继续观察 · LOW/MONITOR · 不误报 |

> **改名理由（v2 治理）**：`telephone_scam` → **`telephone_risk`**。模块边界铁律（AGENTS.md §0）不输出"诈骗"判定；case 名若带 "scam" 会让未来的人误以为 ground truth 是诈骗。Expected Outcome 表述为 `abnormal_dwell + audio_telephone_persistent → risk escalation`，**不是** `telephone_scam → fraud detected`。

---

## 4. `memory.prior_episodes`：不只是 fixture 字段，而是正式 Memory 检索证据（v2 语义收紧）

要证明的是：`3 days ago → yesterday → today → 同一模式重复 → 风险升级`。
`prior_episodes` **不是**"Runtime 机械塞进去的背景故事"，必须携带**时间与身份语义**，并**进入正式 Memory Runtime 被当前决策逻辑检索**：

```yaml
memory:
  prior_episodes:
    - episode_id: historical_001
      event_time: 2026-07-16T10:30:00Z      # 3 days ago（跨日，与当前会话不同日）
      modality: vision
      device_id: home_entry
      semantic_signature:
        visitor_class: visitor_b            # 身份：同一访客
        event_type: abnormal_dwell
      risk_level: low

    - episode_id: historical_002
      event_time: 2026-07-18T14:05:00Z      # yesterday
      modality: vision
      device_id: home_entry
      semantic_signature:
        visitor_class: visitor_b
        event_type: abnormal_dwell
      risk_level: low
```

**验收标准（黄金案例级）**：当前 **Decision Trace 必须能够证明本次风险判断引用了历史 Episode**（如 RepeatVisitRule/风险升级的触发依据里出现 `historical_001` / `historical_002` 的引用）。这才是真正的"记忆效果"——否则只是 fixture 里的背景故事，不算 Memory Case。

**实现定位**：G0-3 是当前最值得先实现的代码能力——scenario `memory.prior_episodes` → **正式 Memory Runtime 预置**（经 MemoryStore/EpisodicRecord 契约入模）→ 决策层检索引用。不改动现有单会话路径。

---

## 5. `media_alignment`：媒体与事件的可解释对应（v2 新增 · 防"测试资产错位"）

AI 视频只负责 Media，但黄金案例必须保证**案例媒体与 Scenario 事件"可解释对应"**，而不是"视频刚好差不多"。否则会出现"8 秒时异常停留、但视频里人 20 秒才出现"的严重 UI 错位（看起来是 bug，实际是测试资产错位）。

```yaml
media:
  video:
    ref: golden_high_risk.mp4
    time_origin: 0.0
    duration: 52.0

  alignment:
    mode: relative
    event_time_origin: scenario_start
    tolerance_ms: 250

  event_windows:
    - event_ref: ev_001        # 对应 scenario 事件/检测
      media_start: 8.2
      media_end: 15.7
    - event_ref: ev_002
      media_start: 31.0
      media_end: 46.5
```

- `event_time_origin`：事件时间基准（scenario_start）；
- `tolerance_ms`：允许的对齐容差（超差 = 测试资产错位，fail-closed）；
- `event_windows`：把关键事件钉到媒体时间窗，供 Case Viewer 与校验器断言。

---

## 6. Expected Outcome：从"结果字段"升级为"产品行为契约"（v2 升级）

Golden Case 把「输入 → 系统判断 → 行动 → 人的处理 → 最终状态」一起定义。示例（high_risk）：

```yaml
expected:
  decision:
    outcome: WARN
    risk_level: HIGH

  action:
    command_types:
      - NOTIFY_FAMILY
      - CREATE_COMMUNITY_TASK

  workflow:
    family:
      required_state: family_handled
    community:
      required_state: community_done

  resolution:
    required: true
```

- `workflow.family/community.required_state`：定义闭环该长什么样（对接 P0-1 状态机 `pending → family_handled → community_done`）；
- `resolution.required`：闭环完成是黄金案例的组成部分，不是"前端点击按钮"的附属品。

---

## 7. Golden Scenario Coverage Matrix（v2 新增 · 能力覆盖矩阵）

以后每次模型 / Runtime / 前端变化，直接回答："这次改动覆盖了哪些能力？哪些黄金案例会回归？"

| 能力 | benign | stranger | repeated | phone | high-risk | ambiguous |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| Vision | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Memory | — | — | **✓** | 可选 | ✓ | 可选 |
| Audio | — | 可选 | — | **✓** | 可选 | — |
| Cross-modal | — | — | — | **✓** | 可选 | — |
| Decision | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Action | — | — | ✓ | ✓ | **✓** | — |
| Human Workflow | — | — | — | — | **✓** | — |
| Resolution | — | — | — | — | **✓** | — |

---

## 8. 您需要准备的 AI 视频清单

### 8.1 通用规格

| 项 | 要求 |
| --- | --- |
| 格式 | MP4（H.264，浏览器直放） |
| 分辨率 | 1280x720 或 1920x1080，16:9 |
| 时长 | 20s ~ 60s（与场景/`event_windows` 对齐） |
| 视角 | CCTV 门廊/门口（俯视或平视），人物清晰、主体在画面中 |
| 音轨 | 可无声（音频轨由 CI 合成并行） |
| 风格 | 真实拍摄或 AI 生成均可（AI 注意人物跨幕一致、动作自然、无文字水印） |
| 存放 | `data/demo/`（gitignore，不入库） |

### 8.2 逐 case 清单

| Case | 建议视频 | 内容要求 | 可复用 |
| --- | --- | --- | --- |
| benign | 快递员正常到访 | 短暂出现→放下物品→离开（20~40s） | ✅ `Delivery_Courier_Final.mp4` |
| stranger_visit | 陌生人首访 | 徘徊→按门铃/张望→停留→离开（25~40s） | **需准备** |
| repeated_visit | 同人三幕到访 | 同一人三幕（各 15~20s），与 `prior_episodes` 3 日对应 | **需准备** |
| telephone_risk | 老人手持电话 | 门口/客厅长时间手持手机通话，神情专注（40~60s，画面即可） | **需准备** |
| high_risk | 深夜多段到访 | 暗光同人多段到访，末次长停留（40~60s） | 可复用/改造 `CCTV_Surveillance_Final.mp4` |
| ambiguous | 边缘短暂人影 | 画面边缘/暗处短暂人影或背对镜头（15~25s） | **需准备** |

**合计：您需新准备 4 条**（stranger_visit / repeated_visit / telephone_risk / ambiguous），复用 2 条（Delivery_Courier / CCTV_Surveillance）。

> 注：`ambiguous` 的具体形态（边缘快速通过 / 部分遮挡 / 背光剪影三幕）见展示层设计文档 §3.3 的细化；contest 资产已由 Owner 生产并统一到 `data/golden/`（见 docs/design/golden-case/GOLDEN-CASES-USAGE.md）。

### 8.3 命名与映射

- 命名：`golden_<case>.mp4`（如 `golden_telephone_risk.mp4`）→ `data/demo/`；
- 映射：`prepare_case_media` 默认映射机制为黄金 case 增加 `golden_*` 条目；
- 对齐：视频时长 / 关键事件时间经 `media_alignment.event_windows` 钉死（§5）。

---

## 9. 落地步骤与优先级（v2 明确：先数据，后产品链）

```
G0  Golden Scenario Set（定义考题）
      ↓
    CI Trusted Case Factory（确认所有案例真实生成、可 gate）
      ↓
P0  Case Viewer / Live / Audio / Workflow（消费 Golden Cases）
      ↓
P1  Story / Demo / 60s usability（围绕 Golden Cases 展示）
      ↓
P2  真实数据采集（模型训练 / 校准 / 数据飞轮）
```

**现在不是训练模型阶段，而是在定义"未来模型到底要会什么"——Golden Scenario Set 是能力的第一版"考试大纲"。**

| 步骤 | 内容 | 类型 |
| --- | --- | --- |
| **G0-1 规范** | Golden Scenario Set 规范：Case Manifest 10 要素 + `memory.prior_episodes`（§4 语义）+ `media_alignment`（§5）+ Expected Outcome 产品行为契约（§6） | 文档 + schema 契约 |
| **G0-2 fixtures** | 6 个黄金 case（独立 `golden/` 目录，不改动 ADR-0034 fixtures）：新增 repeated_visit（跨日）、ambiguous；升级 high_risk（闭环 Outcome）；telephone_scam → telephone_risk | YAML fixtures |
| **G0-3 历史记忆预置** | `memory.prior_episodes` → 正式 Memory Runtime 预置 → 决策检索引用（**当前最值得先实现的代码能力**） | runtime + 契约测试 |
| **G0-4 CI 生产** | `build_trusted_case` 消费 golden fixtures → canonical + media/audio 轨 + media_alignment + Expected Outcome 全断言 → Trusted Artifact | CI 链 |
| **G0-5 产品链** | Case Viewer 呈现黄金案例（含记忆可视化）；P0-2/P0-3/P1 共用同一套考题 | 产品链 |

### 验证标准（每 case）

- **CI**：Integration Gate PASS（感知/记忆/决策/行动/Expected Outcome 全断言，指纹确定）；
- **记忆效果**（repeated_visit）：Decision Trace 证明风险判断**引用了历史 Episode**（§4 验收）；
- **对齐**（所有 case）：`media_alignment` 超容差 → fail-closed（§5）；
- **产品**：Case Viewer 打开即见"视觉画面 + 事件 + 记忆 + 决策 + 行动 + 闭环"。

---

## 10. 一句话总结

> **CI 已经足够生产和门禁单会话考题；黄金案例集在此基础上把考题升级到产品级——跨日 Memory、闭环 Outcome、媒体-事件可解释对齐。G0-3（历史记忆预置）是当前最值得先实现的代码能力；先把 6 个 Golden Case 的 manifest/schema 定死，等 G0-4 全部能被 CI 生产出来，再做 Case Viewer 的产品恢复。**
