# ADR-0019: 多模态证据融合架构（Multimodal Evidence Fusion Architecture）

- **状态**：Proposed
- **日期**：2026-07-26
- **范围**：未来架构方向（v2 / 后 MVP），**当前 MVP 不实现**；本 ADR 仅固化决策，不改动现有冻结契约。
- **决策者**：Owner
- **相关**：ADR-0010（WarningEvent.evidence 字段）、ADR-0001（只产事实）、
  `src/home_perception/analysis/warning.py`（evidence 字段）、ADR-0018（RiskSignal）、
  ADR-0003（边缘推理预算精神）

## 1. 背景（Context）

当前感知链是**纯视觉**的。但对老年人诈骗场景，**语音是主导信号**之一：冒充客服 / 冒充熟人 / 恐吓话术，往往伴随特定声学特征（急促话术、特定关键词、异常通话时段）。只看画面会漏掉最大的一类风险。

复盘时识别出一条**极易踩的反模式**：

```
VisionPipeline
    +
Audio 逻辑
    +
各种判断
```

把音频逻辑混进视觉管道，会让两条本不相关的失败模式耦合在一起：
- 视觉链被污染，单测变难，每帧成本纪律（ADR-0003 / AGENTS §4.1）被破坏；
- 音频模型升级会牵动视觉管道；
- 证据无法区分「来自画面」还是「来自声音」，中心无法按模态加权。

同时，`WarningEvent` 已经预留了 `evidence` 字段（ADR-0010 Decision 2：「取证引用 snapshot/clip URI；P0-8 不填，P0-9 行动层填」）——但当前它是 `List[Dict[str, Any]]`（非类型化的字典列表），是多模态化的天然落点。

## 2. 决策（Decision）

### 2.1 两条独立感知链
- **Vision Pipeline**：保持现有职责与边界（ADR-0001/0007），只产视觉事实 / 风险语义。
- **Audio Pipeline**：新增，独立拥有自己的模型 / 特征 / 边界，产出音频模态信号 / 事件。

### 2.2 独立 `Evidence Fusion` 阶段（不在任一管道内）
```
        Vision Pipeline
              \
               \
            Evidence Fusion
               /
              /
        Audio Pipeline
```
- 融合阶段**只合并，不重新推导**：不重算视觉特征、不重算音频特征。
- 融合产物是「带模态标记的证据集合」，喂给决策层。

### 2.3 `WarningEvent.evidence` 升级为**类型化证据列表**
由 `List[Dict[str, Any]]`（非类型化字典）升级为：
```python
evidence: List[EvidenceItem]   # EvidenceItem = {modality: "vision"|"audio"|..., uri, score, captured_at, ...}
```
- 视觉证据与音频证据并列，中心可按模态加权、家属解释可区分来源。

## 3. 动机（Rationale）

- **系统可扩展性**：加音频（乃至未来 RFID / 门磁 / 温感）只是「新一条 Pipeline + 一个融合入口」，不是重写视觉链。
- **保持视觉链纯净**：延续 ADR-0001/0007 的边界与每帧成本纪律；音频的延迟 / 边缘预算单独评估。
- **证据可归因、可审计**：每条证据带 `modality`，中心能解释「这条预警来自画面还是声音」，家属侧解释更可信。
- **复用既有字段**：`WarningEvent.evidence` 已是预留位，仅从「非类型化 `Dict[str, Any]` 列表」扩为「类型化 `List[EvidenceItem]`」，迁移成本可控。

## 4. 后果（Consequences）

### 正面
- ✅ 模态独立：视觉 / 音频可各自演进、替换、回退。
- ✅ 融合是小而可测的独立阶段。
- ✅ `WarningEvent.evidence` 变为多模态，对中心与家属解释都更丰富。

### 负面 / 约束
- ⚠️ 组件增多；融合需要清晰的 `EvidenceItem` Schema（模态枚举、uri、score、时间戳）。
- ⚠️ Audio Pipeline 需自有边缘预算评估（类比 ADR-0003 imgsz / §4.1 帧采样预算精神）。
- ⚠️ 跨模态时序对齐（画面时刻 vs 声音时刻）需明确定义。

### 后续动作
- 定义 `EvidenceItem` Schema（modality 枚举 + 引用 + score + captured_at）；
- 新开 ADR 锁定 Audio Pipeline 的模型选型与边缘推理成本；
- 配套 Contract Test 校验 `evidence` 列表结构。

## 5. 替代方案（Alternatives）

- **把音频混进视觉管道**：否决。边界污染、测试困难、两模态失败模式耦合、违反单一职责。
- **单一「智能管道」统一处理**：否决。难以扩展、难以把证据归因到具体模态。
- **维持纯视觉、不做多模态**：否决。丢掉老年人诈骗的主导信号，削弱产品核心价值。

## 6. 与既有 ADR 的关系

- **ADR-0018**：`RiskSignal` 是音频信号汇入决策的天然载体——音频 Pipeline 可产出 `RiskSignal`，经融合进入 `WarningEvent`。
- **ADR-0010**：本 ADR 把 `WarningEvent.evidence` 从「`List[Dict[str, Any]]` 非类型化字典列表」扩展为「`List[EvidenceItem]` 类型化证据列表」，字段语义升级但对象不变。
- **ADR-0001**：多模态仍只产「事实 / 证据」，不产「诈骗判定」；最终判定仍归中心。
