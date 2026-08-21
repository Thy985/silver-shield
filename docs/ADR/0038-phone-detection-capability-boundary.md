# ADR-0038: Phone Detection 能力边界确认与 Evidence Contract 调整

- 状态：Proposed
- 日期：2026-08-21
- 决策者：Owner
- 相关：
  - `LIVE-PRODUCT-CAPABILITY-MATRIX.md`（Runtime Capability → Product Surface 盘点）
  - `scripts/phone_benchmark/PHONE_FEASIBILITY_BENCHMARK_REPORT.md`（30 帧 Benchmark 结果）
  - `dataset/telephone_risk/manifest.yaml`（Evidence Contract 定义）
  - ADR-0001（感知模块只产事实不裁决）、ADR-0026（音频感知链路）

---

## 背景（Context）

### 问题陈述

原 `telephone_risk` 场景 Evidence Contract 将 `phone_interaction` 定义为 `required` 证据类型，预期通过 YOLO11n/s 的 `cell phone` 类别检出手机交互行为，作为 `telephone_interaction` 的视觉佐证，并触发 CrossModalLink（VISION + AUDIO 跨模态关联）。

### 实测数据

**Phone Feasibility Benchmark（2026-08-21）**：

| 方法 | Recall | Precision | F1 | TP | FP | FN |
|------|--------|-----------|-----|----|----|-----|
| YOLO11n (480) | 0.000 | 0.000 | 0.000 | 0 | 0 | 28 |
| YOLO11s (480) | 0.000 | 0.000 | 0.000 | 0 | 0 | 28 |
| YOLO11n (640) | 0.000 | 0.000 | 0.000 | 0 | 0 | 28 |
| ROI + YOLO11n | 0.000 | 0.000 | 0.000 | 0 | 0 | 28 |

**Ground Truth**（AI 视觉标注，Claude vision model）：
- 总帧数：30 帧（均匀采样自 `case_b_vision_audio.mp4`，1920×1080 @ 30fps）
- 含手机帧数：28 帧（93.3%）
- 平均手机尺寸：~15×35 像素（< 0.05% 画面面积）
- 不含手机帧数：2 帧（frame_0293, frame_0308，人物未持机）

**根因分析**：
1. **目标尺寸过小**：手机在 1920×1080 画面中仅 ~15×35 px，长宽比 < 0.1%
2. **YOLO11n/s COCO 预训练模型局限**：COCO 数据集中手机目标尺寸远大于实际场景
3. **多分辨率 sweep 无效**：416/480/640 三档分辨率均无改善（说明不是预处理问题）
4. **ROI 二阶段检测无效**：即使裁剪 Person ROI 区域，仍无法检出

### 影响范围

- **Evidence Contract**：`phone_interaction` 从 `required` 降为 `optional_supporting`
- **Cross-modal Link**：因 `phone_interaction` 缺失，`CrossModalLinkBuilder` 无法建边（当前 `cross_modal_links=0`）
- **Risk Pipeline**：**不受影响** — Audio → Risk 独立路径完整可用（`AUDIO_TELEPHONE_PERSISTENT` + `AUDIO_DISTRESS_CRY` → `RISK_SIGNAL` → `LOW` → `LOG_ONLY`）
- **UI 展示**：无 phone-specific UI 元素需移除（已设计为 optional）

---

## 决策（Decision）

**降级 `phone_interaction` 为 `optional_supporting` 证据类型，不阻断 Risk Pipeline。**

具体调整：

1. **Evidence Contract 修改**（已完成）：
   ```yaml
   evidence_contract:
     required:
       audio:
         - telephone_interaction      # 必需：电话交互是核心触发
         - acoustic_state_change      # 必需：声学状态变化是风险信号的核心
         - voice_stress_score         # 必需：声学压力指标量化
     optional_supporting:
       vision:
         - phone_interaction          # 降为可选：视觉 phone 检出则增强信号
       cross_modal:
         - SUPPORTS                   # 降为可选：跨模态关联增强信号
   ```

2. **Audio → Risk 独立路径确认**（已完成）：
   ```yaml
   acoustic_evidence_contribution:
     primary_path:
       - step: 1
         evidence: telephone_interaction
         audio_kind: "AUDIO_TELEPHONE_PERSISTENT"
         semantic: "电话交互进行中（窄带 + 持续语音活动）"
         required: true
       - step: 2
         evidence: acoustic_state_change
         semantic: "声学状态跃迁（NORMAL → ATTENTION → AROUSAL → STRESS）"
         required: true
       - step: 3
         evidence: voice_stress_elevated
         audio_kind: "AUDIO_DISTRESS_CRY 或 AUDIO_VOICE_RAISED"
         semantic: "声学压力升高（哭诉/高声）"
         required: false
   ```

3. **AudioRule 阈值调整**（已完成）：
   - `telephone_rate`: 0.8 → 3.0（放宽：窄带 + 语音活动即判定）
   - `cry_min_rate`: 1.5 → 3.5（提高：避免与电话误判）

4. **Live Runtime 验证**（已完成）：
   - Person Detection: 449 次 ✅
   - Audio 事件: 9 个（AUDIO_TELEPHONE_PERSISTENT + AUDIO_DISTRESS_CRY）✅
   - Risk Transition: RAISED ✅
   - Decision: LOW → MONITOR ✅
   - Action: LOG_ONLY ✅
   - Phone Detection: 0（符合预期，降级为 optional）
   - Cross-modal Links: 0（因 phone_interaction 缺失，符合预期）

---

## 动机（Rationale）

### 为什么不重新训练 Phone Detector？

1. **边缘 CPU 约束**（AGENTS.md §4.1）：
   - 当前 YOLO11n 推理时延 ~15ms/frame @ 480px
   - 专用小目标检测模型（如 YOLOv8-small object / RetinaNet）推理时延预计 >30ms
   - 超过边缘设备 CPU 预算（目标 <50ms/frame 含全流程）

2. **数据获取成本**：
   - 需要标注 1000+ 含手机的人像帧（手机尺寸 <0.05% 画面面积）
   - 域偏移风险：实验室标注数据 ≠ 真实家庭监控场景

3. **产品价值 vs 工程成本**：
   - Phone Detection 是 optional supporting evidence，不是核心风险信号
   - 核心风险信号来自 Audio（声学状态变化），不依赖视觉 phone 检出
   - 投入资源训练 Phone Detector 的 ROI 低

### 为什么接受 Cross-modal Links=0？

1. **Phase 0 MVP 边界**（AGENTS.md §10）：
   - 当前阶段优先满足"风险信号可观测 + 处置闭环"
   - Cross-modal 关联是 v2 增强功能（ADR-0019 Phase 2）

2. **证据链完整性不受影响**：
   - Audio → Risk 主路径独立成立
   - Evidence Contract 明确声明 `phone_interaction` 为 optional
   - `decision_detail` 已注明"Does NOT directly indicate fraud"

3. **UI 降级策略**：
   - 若 `cross_modal_links=0`，UI 显示"暂无跨模态关联"占位文案
   - 避免空白误导用户以为系统故障

---

## 后果（Consequences）

### 正面后果

1. **Evidence Contract 与现实对齐**：
   - `required` 字段均为实际可用能力
   - `optional_supporting` 明确标注能力边界

2. **Risk Pipeline 完整性保留**：
   - Audio → Risk 独立路径完整可用
   - Live Runtime 验收通过（B 级：5/7 维度通过）

3. **产品叙事清晰**：
   - `decision_detail` 强调"声学状态变化 → 风险信号，非诈骗判定"
   - 符合 ADR-0001 模块边界铁律

### 负面后果

1. **Cross-modal 叙事不完整**：
   - 当前无法展示"视觉看到人 + 音频听到电话 + 跨模态关联"的完整故事
   - 需在 Demo 演示中说明 Phone Detection 能力边界

2. **未来扩展成本**：
   - 若 Phase 1 决定引入专用 Phone Detector，需重新设计 CrossModalLink 建边逻辑
   - 建议预留 `phone_detection_confidence` 字段（当前为 boolean 存在/不存在）

3. **文档同步滞后**：
   - `docs/07_event_schema.md` 需更新 EvidenceItem 类型说明
   - `docs/06_api_contract.md` 需更新 CrossModalLink 可选性声明

### 需承担的技术债

1. **Phone Detection 能力缺口**：
   - 登记为 P2 技术债（Phase 1 评估是否引入专用模型）
   - 若未来引入，需重新跑 Benchmark + 域适配测试

2. **Cross-modal 关联阻塞**：
   - 当前 `CrossModalLinkBuilder` 因 `phone_interaction` 缺失无法建边
   - 需确认 Phase 1 是否保留此依赖关系

---

## 替代方案（Alternatives）

| 方案 | 描述 | 否决原因 |
|------|------|---------|
| **A1: 重新训练 Phone Detector** | 收集 1000+ 标注帧，训练 YOLOv8n 小目标检测模型 | 违反边缘 CPU 预算；数据获取成本高；ROI 低（optional evidence） |
| **A2: 引入专用小目标检测模型** | 使用 RetinaNet / FCOS 等专门针对小目标的检测器 | 推理时延 >30ms，超过边缘设备预算；需重新验证域适应性 |
| **A3: 保持 phone_interaction 为 required** | 强行要求 Phone Detection，否则 Risk Pipeline 不触发 | 违反事实（当前能力不可用）；导致 Demo 失败；违反 ADR-0001 只产事实原则 |
| **A4: 用 Audio 推断 Phone Interaction** | 检测到 `AUDIO_TELEPHONE_PERSISTENT` 即标记 `phone_interaction=true` | 偷换概念（audio 推断 ≠ visual detection）；违反模块边界铁律；破坏证据链可审计性 |
| **B（采纳）: 降级为 optional_supporting** | 承认能力边界，调整 Evidence Contract，Audio → Risk 独立路径 | 符合实测数据；保留核心风险信号；产品叙事清晰 |

---

## 附录：Benchmark 原始数据

### 关键帧 Ground Truth（部分示例）

| Frame Index | 含手机 | BBox (x1,y1,x2,y2) | 尺寸 (px) | 面积占比 |
|-------------|--------|---------------------|-----------|----------|
| 0 | ✅ | [352, 300, 365, 337] | 13×37 | 0.03% |
| 15 | ✅ | [350, 298, 364, 336] | 14×38 | 0.03% |
| 293 | ❌ | — | — | — |
| 324 | ✅ | [449, 188, 467, 222] | 18×34 | 0.05% |

### 全量 Benchmark 报告

详见 `scripts/phone_benchmark/PHONE_FEASIBILITY_BENCHMARK_REPORT.md`

---

**报告生成时间**：2026-08-21  
**决策人**：Owner（待审批）  
**状态**：Proposed → 待 Owner Accepted