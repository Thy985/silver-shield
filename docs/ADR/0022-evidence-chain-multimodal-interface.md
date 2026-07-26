# ADR-0022: 证据链与多模态接口 · 具体设计（Concrete Design for ADR-0019）

- **状态**：Proposed
- **日期**：2026-07-26
- **范围**：v2 / 后 MVP 的**证据链与多模态接口设计**；把 ADR-0019（多模态融合）的"方向"落为可实现的证据模型 / 聚合接口 / 契约影响。当前 MVP 不实现。
- **决策者**：Owner
- **相关**：ADR-0019（多模态融合方向）、ADR-0010（WarningEvent 决策架构）、ADR-0014（三级冻结治理）、ADR-0021（实时风险流，Phase 1）、ADR-0023（身份，Phase 4）

---

## 1. 背景

已核实现状（`analysis/warning.py:128` / `analysis/perception.py:82`）：`WarningEvent.evidence` 当前已是 `List[Dict[str, Any]]`，但**全链路从未填充**（`rule_engine.py:360` 仅 `evidence=[]`）；`EvidenceCollector`/`EvidenceStorage` 是 `NotImplementedError` 桩（`evidence/clip_collector.py:29`）。音频模态完全缺失。

核心问题（比"听不懂声音"更根本）：**AI 系统不是只有结论，还需要证据链**。商业化后用户不会只问"为什么 AI 说 HIGH"，而会问"凭什么"。`WarningEvent` 必须携带可归因、可审计的证据。

---

## 2. 目标与非目标

**目标**
- 引入类型化 `EvidenceItem`（含 `modality`），让证据按模态（vision/audio/identity）并列、可加权、可解释。
- 引入 `EvidenceAggregator`（**只整理、不重推**），把多模态证据合并为 `WarningEvent.evidence_items`。
- `WarningEvent` 扩展可选 `evidence_items: List[EvidenceItem]`，原 `evidence: List[Dict]` 冻结保留。

**非目标（本 ADR 不做）**
- 不实现音频检测管道（`AudioDetector`/`AudioPipeline` 接口在 ADR-0019 已定义方向，具体实现 Phase 3）。
- 不重推风险：`EvidenceAggregator` **不**参与 `risk_level` / 行动决策，`DecisionPolicy` 仍是唯一决策中心。
- 不删除/改类型 `evidence` 字段（MINOR 新增并行）。

---

## 3. 决策（具体设计）

### 3.1 `EvidenceItem` + `Modality`（NEW，`core/event.py`，MINOR 新对象）

```python
class Modality(str, Enum):
    VISION   = "vision"
    AUDIO    = "audio"
    IDENTITY = "identity"

@dataclass
class EvidenceItem:
    modality: Modality
    kind: str                 # snapshot | clip | transcript | segment
    uri: str                  # 本地路径 / 对象存储 URL
    score: float = 0.0        # 该证据模态置信度（vision 0.7 / audio 0.9 / identity 0.8 ...）
    captured_at: float = 0.0  # 统一 UTC，解决跨模态时序对齐
    track_id: Optional[int] = None
    visitor_instance_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]: ...   # key 与既有 EvidenceRef 兼容，便于平滑迁移
```

> `modality` 字段是关键：中心可按模态加权、家属解释可区分"画面 / 声音 / 身份"。

### 3.2 `EvidenceAggregator`（NEW，`evidence/aggregator.py`）— 改名与边界

原设计中 `EvidenceFusion` 易误解为"融合后重新判断风险"。**修正**：风险已由 `DecisionPolicy` 决定，证据层只整理不推理。因此改名为 `EvidenceAggregator`，职责严格限定为**合并多模态证据项**，绝不重推风险。

```python
class EvidenceAggregator:
    def merge(self, *evidence_groups: List[EvidenceItem]) -> List[EvidenceItem]:
        """仅合并、去重、按 captured_at 排序；不修改 score 语义、不产出风险判断。"""
        ...
```

- `EvidenceCollector.collect(perception_event, frame) -> List[EvidenceItem]`：把现有 `NotImplementedError` 桩**实现**为产出视觉 `EvidenceItem`（snapshot/clip）。
- `AudioEvidenceCollector.collect(audio_event) -> List[EvidenceItem]`：产出音频 `EvidenceItem`（transcript/segment）—— **Phase 3 实现**，接口在本 ADR 定义。
- `aggregator.merge(vision, audio)` 结果挂到 `WarningEvent.evidence_items`。

> **边界纪律**：`EvidenceAggregator` 输出只进 `WarningEvent.evidence_items`；`DecisionPolicy.decide()` 的输入仍是 `PerceptionEvent`，不因证据改变。证据是"为什么"的载体，不是风险的第二来源。

### 3.3 `WarningEvent.evidence_items`（NEW 可选字段，MINOR）

```python
# analysis/warning.py
class WarningEvent:
    ...
    evidence: List[Dict[str, Any]]                       # 冻结保留（L1）
    evidence_items: List[EvidenceItem] = field(default_factory=list)  # 新增可选（MINOR）
```

原 `evidence` 字段类型/语义**不变**（红线）；`evidence_items` 并行存在，向后兼容。

### 3.4 多模态接口前瞻（接口就绪，检测延后）

ADR-0019 的"Vision / Audio 双独立感知链 + 融合"在本 ADR 落为**接口契约**：`EvidenceItem.modality` 已支持 vision/audio/identity 并列；`EvidenceAggregator` 接受任意多组证据。音频检测管道（`AudioDetector`/`AudioPipeline`）的实现放 **Phase 3**，但其产出的 `EvidenceItem` 在 Phase 2 即已能被聚合器接纳——接口不阻塞实现节奏。

### 3.5 冻结契约影响（SemVer 映射，对齐 ADR-0014）

| 改动 | 契约层级 | SemVer | 说明 |
| --- | --- | --- | --- |
| 新增 `EvidenceItem` / `Modality` | L1 新对象 | MINOR | 增量消息，ADR-0005 评审 |
| `WarningEvent.evidence_items: List[EvidenceItem]`（新增，保留 `evidence`） | L1 字段 | MINOR | 可选字段，向后兼容 |
| `EvidenceCollector` / `EvidenceAggregator` / `AudioEvidenceCollector` ABC | L2 接口 | MINOR | 增量接口，实现可替换 |
| `WarningEvent.evidence` 字段类型 / 语义 | L1 字段 | 不变 | 冻结保留 |
| `DecisionPolicy.decide` 签名 / 输入 | L2 接口 | 不变 | 证据不进入决策输入 |

**红线**：不得删除/改类型 `evidence`、不得让 `EvidenceAggregator` 重推风险、不得把音频检测混入视觉链。

### 3.6 分阶段（Phase 2：证据链）

Phase 2 只做证据体系（让系统可解释），音频检测延后：

1. `EvidenceItem` / `Modality` + contract test（`test_evidence_item_contract.py`：to_dict 与 EvidenceRef 兼容、modality 枚举闭合）。
2. 实现视觉 `EvidenceCollector`（snapshot/clip），挂 `WarningEvent.evidence_items`。
3. `EvidenceAggregator.merge` 落地（先仅 vision，接口兼容 audio）。
4. 演示层 `WarningEvent` 风险卡展示证据链（截图 + 片段 + 时间线）。

---

## 4. 动机（Rationale）

- **可信 AI 基础**：证据链让"为什么"可答，是商业化/家属解释的前提。
- **视觉链纯净**：音频独立成链 + 聚合器只合并，延续边界纪律（ADR-0019）。
- **决策边界清晰**：证据不重推风险，避免"感知/决策/证据"三层职责污染。
- **全部增量**：均为 MINOR，不破 ADR-0014。

---

## 5. 后果（Consequences）

**正面**：证据类型化、可归因、可审计；视觉链不被污染；决策层零改动；音频即插即用。

**负面 / 约束**：跨模态时序对齐需 `captured_at` 统一 UTC；视觉 `EvidenceCollector` 从桩落地有存储/过期管理成本；音频检测 Phase 3 才实现。

---

## 6. 替代方案（Alternatives）

- **`evidence` 直接改 `List[EvidenceItem]` 不保留 `evidence`**：否决。改既有字段类型为 MAJOR 破坏 L1；应新增并行（MINOR）。
- **`EvidenceFusion` 融合后重推风险**：否决。越权到决策层，破坏 `DecisionPolicy` 唯一性；改名 `EvidenceAggregator` 并限定只整理。
- **音频混入视觉管道**：否决。边界污染、测试困难（ADR-0019 已否）。

---

## 7. 与既有 ADR 的关系

- **ADR-0019**：本 ADR 是其**具体实现**——`EvidenceItem`/`EvidenceAggregator`/`evidence_items`。
- **ADR-0010**：`evidence_items` 扩展 `WarningEvent`，决策对象与决策边界不变。
- **ADR-0014**：全部 MINOR（§3.5）；`EvidenceItem` 走 ADR-0005 评审与 L2 登记。
- **ADR-0021 / ADR-0023**：实时信号（Phase 1）与身份异常（Phase 4）产出的证据，经 `modality` 并列汇入同一聚合器。
