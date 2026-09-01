# SilverShield · Home 感知模块

> 老年人诈骗风险数字孪生与协同预警系统 —— **家庭入口实时感知**子模块

基于萤石（EZVIZ）摄像头视频流，对家庭入口区域做实时感知，生成结构化异常事件并采集风险证据，
上报至 SilverShield 中心风控引擎，支撑家庭数字孪生与协同预警。

## 当前状态（MVP Release Candidate + 多角色协同闭环 Demo 完成）

> **状态归属声明**：本节「当前状态」是项目阶段 / 交付状态的**单一事实源（SSOT）**。
> `AGENTS.md` §10、`docs/08_roadmap.md` §8.2 为本节的**投影**，须与本节保持一致；
> `docs/05_git_workflow.md` 仅就 Git 提交规范以 `AGENTS.md` 为准，不覆盖阶段状态 SSOT。
> CI 门禁 `scripts/phase_consistency_check.py` 自动锁定此一致性。

- ✅ P0-3~P0-10：检测 → 跟踪 → 事件 → 特征 → 规则 → 决策 → 行动 → 装配 全链路完成（2295 测试全绿，含 v2 模块）
- ✅ P0-10.5 架构冻结治理（ADR-0014 三级冻结 + 契约测试 + 收敛清理），架构漂移清零
- ✅ P0-10.5.3 Developer API Surface 文档层（DX）已建立（见下方「团队第一入口」）
- ✅ P0-10.5.4 仓库卫生清理完成；**Release Candidate tag `v0.1.0-mvp-rc` 已打（2026-07-20）**
- ✅ **P0-11 多角色协同闭环展示层（Demo）全阶段完成**：单 Dashboard 三视图 Tab
  （① 风险发现 / ② 家属确认 / ③ 社区处置）共享同一 `DemoAggregateState`，
  确定性 HIGH 闭环 + 5 分钟演示剧本，并经 `scripts/e2e_validate_demo.py` 真实端到端验证（12/12）——
  详见 `docs/ADR/0017` 与 `docs/DEMO-SCRIPT-P0-11-5b.md`。展示层零穿透 7 层冻结契约（ADR-0015）。
- 边界：本模块只做事实采集 + 事件生成，**不做诈骗风险判断、不输出 risk score、不调用 LLM**

### 音频风险运行时契约（ADR-0039~0043 · 2026-08-22 Owner 拍板）

> 触发：telephone_risk 全链路审计发现 audio→risk runtime 入口缺失、DecisionInput 未容纳
> 多模态信号、Projection 单覆盖语义丢失 RAISED 历史（frame0 RAISED 被 frame1 CLEARED 覆盖）。
> 经 5 问 ADR Preflight Review，Owner 逐项修订后全部拍板 Accepted。

- ✅ **ADR-0039** RuntimeFrameContext 单容器进给：`process_frame(ctx)` 与 FrameResult 对称；四字段冻结，**不预留占位模态**（Context 是扩展边界不是字段垃圾桶）
- ✅ **ADR-0040** DecisionInput.risk_signals 一等输入：结束"Audio RiskSignal 伪装 PerceptionEvent"幻觉路径；C7 **临时扩展 5→6、6 是硬顶**；policy 升级前 gateway 不接通 audio→risk 链
- ✅ **ADR-0041** SignalTemporalLinker：**冻结机制、窗口数值 TBD by acceptance data**；时钟统一（episode_start_unix 锚定）为前置
- ✅ **ADR-0042** Audio Evidence Strength 五档：**冻结等级、参数 TBD**；class_map 修复 + YAMNet 验证前 **MONITOR ceiling 硬门控**
- ✅ **ADR-0043** RiskSignal 双轨投影：状态轨（覆盖式）+ 事件轨（累积式）；payload 形状留实现设计
- 论证链：`docs/reports/ADR-PREFLIGHT-REVIEW-2026-08-22.md`（**§8 Owner 修订记录为准**）

### v2 验证体系（ADR-0032/0033/0034 · 全量验收完成，v1.0 冻结）

- ✅ **ADR-0032 场景仿真层**：声明式 `Scenario` → 两通道生成（`detections` 零模型 / `frames` OpenCV 程序化），确定性可复现（seed + numpy/opencv 版本入指纹）。
- ✅ **ADR-0033 Benchmark Harness**：感知级场景回归打分 + 回归门禁（`benchmark-baseline-bump` 治理），CI `ci-benchmark` 每 PR 门控。
- ✅ **ADR-0034 闭环集成验证（v1.0 冻结）**：`Scenario→Runtime→Memory→Decision→Notification` 完整闭环机器断言（F1–F6 失败模型 + `ActionSink` 探针 + 两枚闭环指纹）；**Phase C 生产门禁**——per-expectation severity（blocking/warning，F6 永不可降级）、`gate.passed` 决定 CI 退出码（消费 `IntegrationReport` 而非 pytest exit code）、loop 指纹基线漂移治理（`integration-baseline-bump` 标记）、F2/F3/F5 失败注入契约入 CI、T2 生产边界 AST 契约入 CI。DoD C1–C8 验收通过，详见 `docs/ADR/0034-*`。
- ✅ **CI/验证体系（四套分离 workflow）**：`ci-quality`（静态）/ `ci-test`（contract/unit/integration 三 tier + `integration-gate` job）/ `ci-benchmark`（回归门禁）/ `ci-runtime`（main 全量 AI 栈）；环境可复现（Python 3.12 + numpy 2.4.2 / opencv 4.13.0.92 钉死），失败可溯源（artifact 全量上传 + runtime provenance 报告）。详见 `.github/workflows/README.md`。

### v2 实时风险状态流（设计完成 · 未集成 · 未启用）

> **严格区分**：设计完成 ≠ 集成完成 ≠ 默认启用。

- **设计完成**：ADR-0018/0019/0020（方向）+ ADR-0021/0022/0023（具体设计）+ `docs/DESIGN-realtime-riskstream-engineering-plan.md`（工程方案）已就位。
- **工程迁移 Stage A 进行中**：`BehaviorState` / `RiskSignal` / `RecentBehaviorStore` 类型 + 契约测试已合入 main，**未接入 pipeline**（`pipeline.py` diff 为空）。
- **当前运行路径**：仍为 MVP 历史事件流（`VisitorEvent` 离场生成 → `RuleEngine` → `WarningEvent`），`realtime_risk.enabled=false` 默认关闭。
- 后续 Stage B/C/D（工程方案 §9）才逐步接入实时状态 / 信号 / 决策；详见 `docs/08_roadmap.md` §8.4 产品 Phase 1。

### v2 Memory 架构（ADR-0024 · Slices 1–6 + Stage F + Integration Closure（B/C/A/D）已合入 main）

> **状态**：ADR-0024 已 `Accepted`（2026-07-28）；Slices 1–6 + Stage F + Integration Closure（B/C/A/D）全部合入 `main`，单元测试全绿；
> **Stage F Pipeline Shadow Mode 已接线**（`runtime/pipeline.py` 接入 `InMemoryStore` + `DefaultEpisodeBuilder`，
> 由 `memory.episodic_shadow` 开关控制，**默认关闭，v1 不产 Warning**）。

- **设计（ADR-0024）**：三类记忆模型（Short-term / Episodic / Semantic）+ Memory Policy 转换边界 + 四项不变量（I1 幂等 / I2 单调 / I3 因果 / I4 可解释）。
- **工程落地（PR 已合入 main）**：
  - **Slice 1**（#77 / #78）：Memory Core 基础模型 —— `records.py`（ShortTermRecord / EpisodicRecord / SemanticAggregate）+ `MemoryPolicy` ABC（含 I1–I4 校验）。
  - **Slice 2**（#79 / #80，+ #81 文档修正）：`DefaultShortTermPolicy` 实现 `transform_short_term`（Stage A 投影）。
  - **Slice 3**（#82）：Snapshot 持久化（Stage C）+ 冷启动恢复（Stage E，解 TD-0027）—— `snapshot.py` / `cold_start.py`，由 `runtime/pipeline.py` 启动期调用。
  - **Slice 4**（#83）：`DefaultEpisodeBuilder` 实现 `project_episode`（Stage B）—— `VisitorEvent + WarningEvent + ActionCommand → EpisodicRecord`，确定性中文摘要（无 LLM）。
  - **Slice 5**（#84）：`MemoryStore` / `InMemoryStore`（Episodic 持久化后端，v1 内存 + JSON 序列化）+ `InvariantViolationError`。
  - **Stage F**（#87）：`runtime/pipeline.py`：`InMemoryStore` + `DefaultEpisodeBuilder` 影子写入（Pipeline Shadow Mode），由 `memory.episodic_shadow` 开关控制，**默认关闭，v1 不产 Warning**。
  - **Slice 6**（#88）：Memory Evaluation（压缩比 ≥100:1 / 信息保留字段校验 / Replay Test §6.7 一致性验证）+ `DefaultEpisodeBuilder` 确定性修复（warning 排序 / 重投去重）+ `MemoryStore.short_term_count()`。
  - 附带修复（#85）：移除未使用的 `TYPE_CHECKING` 导入。
  - **Integration Closure · Slice B**（#93）：闭环测试 `test_memory_closure_slice_b.py` —— Contract E2E（cached detection 驱动整链）+ 重启恢复 + 失败隔离 + Lifecycle Closure（场景 1/2/3/4）。
  - **Integration Closure · Slice C**（#91）：`memory/query.py`：`MemoryQuery.compose_context`（Product Closure，V0 边界冻结）。
  - **Integration Closure · Slice A**（#94）：`runtime/memory_hook.py`：抽出 `MemoryHook`（门控 / 容错 / metrics 语义不变，0 行为变化）。
  - **Integration Closure · Slice D**（#95）：4 份文档（`MEMORY_ARCHITECTURE.md` / `MEMORY_OPERATION_GUIDE.md` / `MEMORY_TEST_REPORT.md` / `DESIGN-observation-contract.md`）+ ADR-0024 §10.1 标注。
- **包导出**：`home_perception.memory` 现导出 `DefaultShortTermPolicy` / `DefaultEpisodeBuilder` / `MemoryStore` / `SnapshotStore` / `ColdStartCoordinator` 等（含 `episode_builder` 包级导出，Stage F 已接线）。
- **下一步**：Stage G/H Semantic 聚合器（依赖 Phase 4 ReID，v1 不实现）。
- 详见 `docs/ADR/0024-memory-architecture.md` 与 `docs/design/memory/DESIGN-memory-pipeline.md`。

## 当前执行路线（Next Steps · 防偏离导航）

> **下一个 Agent 开工前必读**：本节 + `AGENTS.md` §10.1 + `docs/reports/ADR-PREFLIGHT-REVIEW-2026-08-22.md` §8。
> 动代码前先回答：本步对应哪个 ADR？是否踩硬门控？

### 执行顺序（Owner 锁死，不可反序）

> **2026-08-25 状态更新**：步骤 1–6 已全部合入 `main`（ADR-0039~0043 实现队列完成）。
> 当前进入步骤 7–8（UI 打磨 + Browser E2E 回归）。

| 步骤 | 内容 | 依据 | 状态 |
|---|---|---|---|
| 1 | Audio Runtime Entry：RuntimeFrameContext + process_frame(ctx) 改造 | ADR-0039 | ✅ 已合入 |
| 2 | RiskSignal → Decision：DecisionInput.risk_signals 字段 + RuleBasedDecisionPolicy 升级 | ADR-0040 | ✅ 已合入 |
| 3 | 时钟统一：episode_start_unix 锚定 + SignalTemporalLinker 组件 | ADR-0041 | ✅ 已合入 |
| 4 | RealTimeAudioRiskEvaluator 接线（MONITOR ceiling 下）+ modality-aware routing | ADR-0042 | ✅ 已合入 |
| 5 | 双轨投影：ProjectionAccumulator 状态轨/事件轨扩展 | ADR-0043 | ✅ 已合入 |
| 6 | YAMNet class_map 修复 → 真实 Δt / AudioKind 分布 → 参数回填 | ADR-0037 | ✅ 已合入 |
| 7 | Browser E2E 回归（含 P0-11 12 项端到端） | docs/05 §3 | ✅ 已合入 |
| 8 | UI 打磨最后：Audio DOM / Risk Card / Narrative | LIVE-PERCEPTION-STREAM-SPEC | ✅ 已合入 |

### 硬门控（违反即返工）

1. YAMNet `class_map_path=""` 修复前：音频证据强度**封顶 MONITOR**（fallback 永不驱动 RAISE 及以上）；
2. `RuleBasedDecisionPolicy` 未升级消费 `risk_signals` 前：gateway **不接通** audio→risk 链（防假通电）;
3. **禁止**把 audio RiskSignal 翻译成视觉 event_type——`signal_adapter` 不是 audio 通路（audio_kind 不在其映射表，翻译必落幻觉兜底）；
4. 依赖方向：Temporal Alignment（Q3）在 Evidence Strength（Q4）**之前**；ESCALATE 须经 LinkedSignalPair 验证，禁 audio 单方推断视觉事实。

### 文档地图（按需取用）

| 需要什么 | 去哪 |
|---|---|
| 本轮 5 项决策的最终事实 | `docs/ADR/0039` ~ `0043`（Accepted · 含 Owner 修订） |
| 论证过程 / 候选方案对比 / Owner 修订记录 | `docs/reports/ADR-PREFLIGHT-REVIEW-2026-08-22.md`（§8 为准） |
| 审计取证（2 CLEARED payload 拆解 / audio 旁路根因 / memory 错耦合） | `docs/reports/AUDIO-RISK-RUNTIME-AUDIT-CORRECTION-2026-08-22.md` |
| ⚠️ 原审计报告 Layer 4「完全旁路」判定已被推翻 | `RUNTIME-RISK-ROOT-CAUSE-AUDIT-2026-08-22.md` 仅作历史记录，勿引用其结论 |
| telephone_risk 能力边界（phone_interaction 已降级 optional_supporting） | `docs/ADR/0038` |
| Evidence Fusion 架构母体（本轮为其 Phase 1 落地） | `docs/ADR/0019` |
| 产品现状与设计差距盘点 | `docs/reports/LIVE-PRODUCT-STATE-AND-GAP-REPORT-2026-08-22.md` |
| 决策契约本体（C1/C7 白名单黑名单机制） | `src/home_perception/analysis/decision_contract.py`（导入期 fail-closed） |

## 架构总览（团队入口）

```
External Device / Video
        │
   [稳定]     FrameSource (ABC)
        │  (timestamp, frame)
   [可替换]   YOLODetector (Detector)
        │  DetectionResult
   [可替换]   VisitorTracker
        │  List[VisitorTrack]
   [稳定]     VisitorEventBuilder
        │  VisitorEvent
   [可替换]   FeatureExtractor
        │  RiskFeature
   [可替换]   RuleEngine (4 Rule + 1 Composite + Cooldown)
        │  PerceptionEvent (5 类标签 + score)
   [可替换]   DecisionEngine + DecisionPolicy
        │  WarningEvent (risk_level + recommended_action)
   [可替换]   ActionExecutor + ActionDispatcher
        │  ActionCommand
   [稳定]     MQTTPublisher / NotificationAdapter (Protocol)
        │
   MQTT / App / Community
```

图例：**\[稳定\]** = 接口 / 装配入口（签名冻结）；**\[可替换\]** = 换实现不改契约；**\[禁止\]** = 红线（跨层跳级 / 最终判定 / 绕过 Pipeline）。

> **团队第一入口**：接 Dashboard / 新设备 / AI Agent 前，先读 [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)（公共 API 表面）与 [`docs/CONTRACTS.md`](docs/CONTRACTS.md)（冻结契约，什么不能改）。

## 快速开始

```bash
# 1. 准备环境
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置凭证与设备
cp .env.example .env            # 填写 EZVIZ_APP_KEY / EZVIZ_APP_SECRET
cp config/devices.example.yaml config/devices.yaml   # 填写设备序列号

# 3. （可选）本地 MQTT
docker compose up -d mqtt

# 4. 运行
python scripts/run.py
```

## 目录与文档

- 代码：`src/home_perception/`
- AI 协作规范：`AGENTS.md`（所有 PR 须满足）
- 设计文档：`docs/`（见 `docs/00_README.md` 索引）
- **团队第一入口（API 表面 / 冻结契约）**：`docs/API_REFERENCE.md` · `docs/CONTRACTS.md` · `docs/ARCHITECTURE.md` · `docs/CONTRIBUTING.md`
- 阶段任务与风险：`docs/08_roadmap.md`、`docs/09_risks.md`

> ⚠️ `prototypes/` 下为早期验证脚本，含真实凭证，**已被 gitignore，切勿提交**。

## YOLO 检测（P0-3）

门前异常行为感知第一阶段：萤石 1080p 流 → OpenCV 抽帧 → **显式 resize 到 640×640** → YOLO11n 推理 → 结构化 `DetectionResult`。

### 模型下载

默认模型 `yolo11n.pt`（Ultralytics 官方小模型，CPU 可跑）。首次推理时由 `ultralytics` 自动下载并缓存到 `~/.cache/ultralytics`；也可手动预取：

```bash
python - <<'PY'
from ultralytics import YOLO
YOLO("yolo11n.pt")  # 触发下载，验证可用性
PY
```

如需更好精度或 GPU，改 `config/default.yaml` 的 `detection.model`（如 `yolo11s.pt`）与 `detection.device`（`cuda:0`）。**不要扩展检测类别**——本阶段仅 `person / backpack / handbag / cell phone`（见 `AGENTS.md` §3 边界约束）。

### 推理流程

```
萤石 1080p 帧
    ↓ FrameSource 抽帧（fps_target，默认 8）
    ↓ YOLODetector：cv2.resize -> 640×640
    ↓ YOLO11n 推理（CPU）
    ↓ 检测框映射回原始帧坐标
    ↓ DetectionResult（detections + 推理耗时 + 尺寸）
```

`YOLODetector.detect(frame)` 返回 `DetectionResult`，每个 `Detection` 含 `class_id / class_name / confidence / bbox / timestamp`。检测器**不输出任何风险结论**，仅提供事实。

### 最小闭环演示

```bash
# 需要 .env 配置 EZVIZ_APP_KEY / EZVIZ_APP_SECRET，且 config/devices.yaml 有该序列号
python scripts/detect_demo.py --serial BK6415780 --duration 20 --protocol rtsp
```

脚本仅打印检测事实（类别@置信度 + 推理耗时），**不生成事件、不判风险**，用于验证"事实采集"链路。

### 性能测试

```bash
# 流质量基线（FPS / 延迟 / 重连）
python scripts/eval_stream.py --serial BK6415780 --duration 30 --protocol rtsp

# 单元/契约测试（含模型加载、空图、正常图推理、输出 Schema）
pytest tests/ -q
```

普通电脑目标：**检测 15–30 FPS**（抽帧后 YOLO 推理预算；1080p 默认降采样到 480 再推理，避免 CPU 直接跑满分辨率；精度优先场景可切 accuracy=640）。所有性能结论以实测为准（见 `docs/09_risks.md` 9.4）。

## P0-4 · 性能基准（benchmark）

量化 `萤石流 → OpenCV → YOLO → DetectionResult` 的端到端性能，为答辩与"门前踩点识别"落地提供硬数据，并为 P0-5 跟踪定基线。基准脚本**只测性能**，不生成事件、不判风险。

```bash
# 合成模式（默认，无需摄像头/网络，可复现；测纯推理+resize 开销）
python benchmark/yolo_speed.py --synthetic --duration 30 --json out/bench.json

# 调不同推理分辨率对比（--profile 对应 accuracy=640 / balanced=480 / realtime=416）
python benchmark/yolo_speed.py --synthetic --duration 15 --profile balanced
python benchmark/yolo_speed.py --synthetic --duration 15 --profile accuracy
python benchmark/yolo_speed.py --synthetic --duration 15 --profile realtime

# 实时模式（接真实萤石流，测端到端；需 .env）
python benchmark/yolo_speed.py --serial BK6415780 --duration 1800 --protocol rtsp
```

输出：Camera FPS / Inference FPS / 推理耗时(avg/p50/p95/max) / 端到端延迟 / CPU / 内存，并对照目标表自动判定达标。

### 目标（roadmap P0-4）

| 指标 | 目标 |
| --- | --- |
| 输入 FPS | 10–15 |
| YOLO 推理耗时 | < 100 ms |
| 端到端延迟 | < 300 ms |
| 连续运行 | ≥ 30 分钟 |

### 实测参考（开发机 CPU · yolo11n · 1080p 合成帧）

> 数据来自本机合成基准（`--duration 30/15`），仅供**相对比较与调参**参考；实际以目标硬件实测为准。合成模式下每帧随机生成 1080p 图约耗 16 ms，会拉低 camera_fps，**推理耗时 / Inference FPS 才是硬件代表指标**。

| imgsz | profile | 推理 avg | Inference FPS | 端到端 avg | 达标 |
| --- | --- | --- | --- | --- | --- |
| 640 | accuracy | 124 ms | 8.1 | 142 ms | ❌ 推理/FPS 未达标 |
| 480（默认） | balanced | 86 ms | 11.6 | 104 ms | ✅ 推理/端到端达标 |
| 416 | realtime | 47 ms | 21.5 | 64 ms | ✅ 全部达标（有裕量） |

**工程结论（P0-4 实测推翻原假设）**：原设计假设"YOLO11n@640 可实时"被实验推翻——纯 CPU 边缘机推理 ~124ms、未达 <100ms/≥10FPS。基于 CPU 边缘部署测试，**MVP 默认采用 `yolo11n@480`（balanced）**，在保证门前人员检测需求的同时满足实时性能约束；`640` 作 accuracy 精度模式（GPU/算力充足时），`416` 作 realtime 低延迟模式（预留算力给 Tracker/ROI/事件，小目标精度略降）。这正是工程开发区别于 PPT 方案之处。

**配置化（不写死分辨率）**：用 `imgsz_profile` 切换，而非改死 `imgsz`：

```yaml
# config/default.yaml · detection
imgsz: 480                # 默认 = balanced；显式赋值即覆盖 profile
imgsz_profile: balanced   # accuracy(640) / balanced(480) / realtime(416)
```

> GPU 不是当前阻塞点：现阶段最缺的是"从检测结果生成连续事件"（P0-5/6），而非 YOLO 精度。故不为 640 引入 GPU。配合 `fps_target=8` 抽帧，链路可稳定实时。
