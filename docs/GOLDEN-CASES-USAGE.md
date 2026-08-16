# 黄金案例集 · 资产接入清单 + 数据利用方案 + 计划清单

- **状态**：执行中（G0-1 资产统一已完成，G0-3 代码实现进行中）
- **日期**：2026-08-16
- **决策者**：Owner
- **相关**：docs/DESIGN-golden-scenario-set.md（数据准备文档 v2）/ docs/DESIGN-golden-case-viewer.md（展示层设计）/ docs/DESIGN-demo-v2-product-restore.md（P0 产品链）

---

## 1. 资产盘点（data/golden/ · 已统一目录 + 整体 gitignore）

> 4 个黄金 case 资产已从 `contest/` 迁移并统一为 `data/golden/<case>/`（`.gitignore` 新增 `data/golden/` 整体忽略：视频 `*.mp4` 本就全局忽略，整体忽略覆盖 wav/yaml/png/脚本，均不入库）。

```
data/golden/
├── stranger_visit/      # 首访异常停留（final 33.3s）
│   ├── manifest.yaml    # ✅ 新增（统一 schema v1.0）
│   ├── output/stranger_visit_final.mp4 + with_audio
│   ├── audio/ (8 wav) + audio_mix/stranger_visit_mix.wav
│   └── reference/ (AI 参考图)
├── repeated_visit/      # 历史记忆让风险升级
│   ├── manifest.yaml    # ✅ 补齐（memory_ref 蓝本）
│   ├── episodes/ ep_001/002/003.mp4 + output/ ep_*_final.mp4 + demo 28s
│   ├── audio/ + audio_mix/
│   └── reference/
├── telephone_risk/      # 视听共同判断（守"非诈骗"边界）
│   ├── manifest.yaml    # ✅ 补齐
│   ├── output/ case_a_vision_only + case_b_vision_audio + demo 31s
│   ├── audio/ (四层) + audio_mix/
│   └── reference/
└── evidence_insufficient/  # 证据不足不误报
    ├── manifest.yaml    # ✅ 补齐
    ├── output/ act_a/b/c_final + demo 22s + video/ raw
    ├── audio/ + audio_mix/
    └── reference/
```

**资产规模**：4 case · 视频产物 21 个（1920x1080/30fps 达标）· 合成音频 38 个 wav · 参考图多张 · 生产脚本（ci_audio_factory.py / cctv_post*.py）· 落地执行文档 4 份（Reference Card 冻结视觉规格）。

---

## 2. 统一 Manifest Schema（v1.0 · 已补齐 4 个 case）

每个 `manifest.yaml` 现含：

| 段 | 内容 | 现状 |
| --- | --- | --- |
| `schema_version / case` | 统一标识 | ✅ 4/4 |
| `product_question / engineering_assertion` | 产品命题 / 工程断言 | ✅ 4/4 |
| `media.video` | 视觉轨 ref / time_origin / duration | ✅ |
| `media.alignment` | `mode` / `event_time_origin` / `tolerance_ms` / `event_windows` | ✅ 4/4（telephone 含声学变化 6s 锚点；stranger 含门铃 14.2s 锚点；repeated/evidence 骨架 + `TODO(帧级校准)`） |
| `media.audio` | 音频轨 id / uri / start_time / end_time / provenance_kind=SIMULATED | ✅ stranger/telephone 明确 |
| `segments / episodes / acts / variants` | 时间真相源 + 决策 + evidence + memory_ref | ✅ 保留用户原结构 |
| `expected` | 产品行为契约（decision/action/workflow/resolution） | ✅ 4/4（推断值标 `TODO(Owner 校准)`） |

**待办（G0-1 收尾）**：`event_windows` 帧级校准（repeated/evidence）；`expected.risk_level` 等推断值待 Owner 校准。

---

## 3. 接入清单（case → 资产 → CI fixture → 产品轨）

| Case | 视觉轨（Media） | 音频轨（Media） | 决策/Expected（Evidence） | CI fixture 需求（G0-2） | 产品轨（P0） |
| --- | --- | --- | --- | --- | --- |
| stranger_visit | `output/stranger_visit_final.mp4` | doorbell(14.2s 锚点)+脚步+环境 | MONITOR/LOW · LOG_ONLY · 不误报 | `golden_stranger_visit.yaml`（abnormal_dwell，无 prior） | Case Viewer 主轴 + 音频轨 |
| repeated_visit | `output/ep_001/002/003_final.mp4`（三幕） | 脚步声/门铃/环境 | ep_003 NOTIFY_FAMILY（memory_ref ep_001/002） | `golden_repeated_visit.yaml`（**prior_episodes 3 日** + 决策升级） | 记忆可视化 + 决策升级 |
| telephone_risk | `output/case_a_vision_only` + `case_b_vision_audio` | 四层（正常→应激 6s 变化） | case_a LOW / case_b RISK_SIGNAL（**非诈骗**） | `golden_telephone_risk.yaml`（视觉+音频+跨模态） | 视听共同判断 + 跨模态 |
| evidence_insufficient | `output/act_a/b/c_final.mp4`（三幕） | 三幕环境音 | 三幕 NOT_TRIGGERED/MONITOR · 不误报 | `golden_evidence_insufficient.yaml`（低置信/遮挡/背光） | 不误报展示 |
| benign（复用） | `Delivery_Courier_Final.mp4` | — | 不报警 · LOG_ONLY | `golden_benign.yaml` | 基线 |
| high_risk（复用/改造） | `CCTV_Surveillance_Final.mp4` | 可选 | HIGH → ESCALATE_COMMUNITY + 闭环 | `golden_high_risk.yaml`（含 workflow/resolution） | 闭环处置 |

**接入路径**（数据 → CI → 产品）：
```
data/golden/<case>/（源资产）
  ├── output/*.mp4  → prepare_case_media 映射 → {artifacts}/{sid}/media/case.mp4（Case Video 主轴）
  ├── audio/*.wav   → prepare_case_audio 映射 + media_tracks 时间绑定（P0-3）→ 音频轨
  ├── manifest.yaml → 时间真相源/决策/alignment → golden CI fixtures（G0-2）
  └── prior/memory_ref → G0-3 prior_episodes 代码实现 → Memory Runtime → 决策检索
```

---

## 4. 数据利用方案

### 4.1 视觉轨（Media）
- **Case Video 主轴**：`prepare_case_media` 增加 golden case 映射（`golden_*` → `data/golden/<case>/output/*.mp4`），复制为 `{sid}/media/case.mp4`（ArtifactVideoSource），Case Viewer 呈现真实画面；
- 多幕 case（repeated/evidence）：每幕一个 `{sid}/media/` 子资产，经 `media_alignment` 对齐。

### 4.2 音频轨（Media，与视觉并行）
- `prepare_case_audio` 增加 golden 音频轨（`{sid}/audio/*.wav`）+ `media_tracks` 时间绑定（start/end_time 来自 manifest.media.audio）→ P0-3 Case Time 同步播放（点击时间点 → video seek + audio play + 证据高亮）。

### 4.3 时间真相源 / 决策（Evidence 蓝本）
- manifest 的 `segments/episodes/acts/variants`（时间戳 + decision + evidence + memory_ref）→ **转化为 golden CI fixtures**（G0-2）：独立 `golden/` 目录，不改动 ADR-0034 fixtures；
- `expected`（产品行为契约）→ Integration Gate 断言（Expected Outcome）。

### 4.4 历史记忆（G0-3 · 已实现）
- **repeated_visit 的 `memory_ref [ep_001, ep_002]` 是 `prior_episodes` 的落地蓝本**：3 days ago / 1 day ago / today → `golden_repeated_visit.yaml` 的 `memory.prior_episodes`（含 event_time/semantic_signature/risk_level）→ Memory Runtime 预置 → 决策检索引用（已实现：scenario `prior_episodes` → runner `_seed_prior_episodes` → `DecisionEngine(memory_store)` → `RuleBasedDecisionPolicy(memory_aware=True)` 历史升级 → `trace.historical_record_ids` 填充；验收：Decision Trace 证明引用了历史 Episode）。

### 4.5 对齐契约（media_alignment）
- 帧级校准 `event_windows` 后，接入校验器：Case Viewer / 验收断言超 `tolerance_ms` 即 fail-closed（防"测试资产错位"）。

### 4.6 其它
- `reference/` 参考图：AI 复现基准 / 文档插图；
- `*_with_audio.mp4` / `*_demo.mp4`：演示 / 宣传素材；
- `ci_audio_factory.py`：音频轨的确定性再生脚本（保留在资产内，CI 可调用）。

---

## 5. 计划清单

### ✅ 已完成
- [x] G0-1 `contest/` → `data/golden/`（统一目录 + `.gitignore` 整体忽略）
- [x] G0-1 `stranger_visit/manifest.yaml` 新增 + 4 个 manifest 补齐统一契约（schema/命题/alignment/expected）
- [x] G0-1 接入清单 + 利用方案 + 计划（本文档）
- [x] G0-1 展示层设计（docs/DESIGN-golden-case-viewer.md）
- [x] **G0-3 历史记忆预置代码**（scenario prior_episodes → runner 预置 → DecisionEngine 检索 → policy memory_aware 升级 → trace.historical_record_ids；契约测试 9 条）

### 🔲 待办（按优先级）

| # | 任务 | 类型 | 依赖 |
| --- | --- | --- | --- |
| 1 | **G0-2 golden CI fixtures**（6 case yaml：prior_episodes / media_alignment 引用 / expected） | fixtures | G0-3 |
| 2 | **G0-3 接入 golden fixture 端到端**（repeated_visit 跑通：prior 预置 → 决策升级 → trace 引用） | 集成 | 1 |
| 3 | **media_alignment 帧级校准**（repeated/evidence 的 event_windows 按帧标定） | 数据 | 需逐帧看视频 |
| 4 | **prepare_case_media / prepare_case_audio 映射接入**（golden_* → artifact） | 代码 | G0-2 |
| 5 | **G0-4 CI 生产**（build_trusted_case 消费 golden fixtures → 全断言） | CI | 1/3 |
| 6 | **Expected Outcome 校准**（risk_level 等推断值 → Owner 确认） | 数据 | 待 Owner |
| 7 | **P0-3 media_tracks**（音频轨时间绑定 → Case Time 同步播放） | 产品链 | 4 |
| 8 | **Memory Timeline 展示组件**（repeated_visit 记忆可视化，展示层 §3.1） | 产品链 | G0-3 |

---

## 6. 一句话总结

> **黄金案例源资产已统一到 `data/golden/`（4 case · 视频+音频+manifest 全齐，已整体 gitignore）；G0-3 历史记忆预置代码已完成（prior_episodes → Memory Runtime → 决策检索引用 → trace 可证）；下一步 G0-2 golden CI fixtures + 端到端跑通 repeated_visit。**
