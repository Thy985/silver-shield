# 未来契约：模态无关 Observation 协议（Observation Contract）

> **定位**：Integration Closure（Slice D，文档冻结）产出之一——**未来契约文档**。
> **本阶段纪律（v2 核心修订）**：本文档**只定义未来接口契约**，**不改动任何当前代码**。
> **明确声明**：本阶段**不改动** `VisitorEvent` / `MemoryPolicy` / `EpisodeBuilder` / `EpisodicRecord`。
> 当前链路 `VisitorEvent → Episode → Memory` 已稳定，为未来 Audio 提前重构稳定部分是"为未来重构现在"，风险高、收益晚。
> **用途**：作为下一阶段 **Multimodal Evidence Fusion** 接入 Memory 的契约起点，而非现在的重构任务。

---

## 0. 背景与动机

当前 `MemoryPolicy.project_episode(visitor_event: VisitorEvent, warnings, actions)`（ADR-0024 `policy.py:99-118`）**强耦合视觉**：它读 `VisitorEvent` 的 `enter_time`/`leave_time`/`duration_seconds`，而这些字段天然是"视觉访客在场"语义。若未来要接入音频（"老人在厨房呼救"）、传感器（跌倒检测）、或文本（家属留言），当前投影入口无法直接消费。

但——**Memory 关心的从来是"发生了什么值得记住"，不关心来自摄像头还是麦克风**。目标一致，只是入口类型被视觉绑定了。

本契约定义模态无关的 `Observation` 协议，让下一阶段 Multimodal Evidence Fusion 能把不同模态的证据**收敛成可喂给 `project_episode` 的事件**，而**不破坏当前稳定代码**。

---

## 1. 核心原则（铁律）

1. **本阶段零代码改动**：不新增 `Observation` 类、不修改 `VisitorEvent`、不碰 `MemoryPolicy`/`EpisodeBuilder`/`EpisodicRecord` 签名与不变量（I1–I4）。
2. **契约先行，实现按新 ADR 走 Owner 评审**：真正实现时需单独立项（Multimodal / Audio 阶段 ADR），经 Owner 评审，不现在动。
3. **Memory 仍是 transformation boundary**：未来 `Observation` 收敛逻辑归 Multimodal Evidence Fusion 层，**不**让 Memory 直接消费原始多模态流、不调 LLM、不接决策。
4. **不变量不降级**：无论未来怎么聚合，`EpisodicRecord` 的 I1 幂等 / I2 单调 / I3 因果 / I4 可解释 必须保持。新模态证据须能追溯回源 `Observation` id。

---

## 2. Observation 协议（提议，待实现）

> 以下为**建议形态**，非当前代码。实现时以新 ADR 为准。

```python
# 提议形态（NOT in current code）
@dataclass
class Observation:
    observation_id: str          # 幂等键（I1 基础），跨模态唯一
    modality: str                 # "vision" | "audio" | "sensor" | "text"
    timestamp: datetime           # UTC，I3 因果性前置
    subject: Optional[str]        # 访客/说话人实例（模态内 track_id / speaker_id）
    event_type: str               # 模态内事件类型（如 "enter" / "cry" / "fall" / "message"）
    evidence: Dict[str, Any]      # 模态特有证据（bbox/confidence | 关键词/文本片段 | 加速度峰值）
    source_ref: str               # 原始流引用（frame_id / clip_id / audio_segment_id）
    confidence: float             # 模态内置信度
```

要点：

- `observation_id` 是未来 I4 可解释性的根（替代/补充当前 `source_event_ids` 的视觉耦合）。
- `modality` 用**严格白名单枚举**（非自由文本），与 `MemoryStatus`/`VisitorPresenceStatus` 闭合性基线一致（契约测试据此断言枚举不漂移）。
- `evidence` 保持模态特有、不强行统一结构——统一结构由下游 Fusion 层负责。

---

## 3. Multimodal Evidence Fusion 接入方式（提议）

```
Vision/Audio/Sensor 原始流
      │  （各模态 detector / tracker）
      ▼
Observation 流（模态无关，每个带 observation_id + modality）
      │
      ▼
Multimodal Evidence Fusion 层（NEW，未来 ADR）
      │  - 跨模态关联（同一 subject / 时间窗邻近）
      │  - 收敛为"事件"：把相关 Observations 聚成
      │    → 视觉访客在场窗口（enter/leave/duration）
      │    → 音频呼救事件
      │    → 传感器跌倒事件
      ▼
收敛后的事件（保持当前 VisitorEvent 契约，或新增等价事件类型）
      │
      ▼
MemoryPolicy.project_episode(...)   ← 入口不变，仍是当前稳定签名
      │
      ▼
EpisodicRecord（I1–I4 不变）
```

**关键约束**：Fusion 层负责把多模态 `Observation` **收敛成当前 `project_episode` 能消费的输入形态**（即保持 `VisitorEvent` 契约），从而**不触碰 Memory 内部**。这是"契约先行、实现后置"的核心——当前 `EpisodicRecord` 的字段与不变量是消费方稳定契约，未来只需在**上游**补齐多模态到事件的收敛，Memory 侧零改动。

---

## 4. 与当前代码的边界（明确不做什么）

| 项 | 本阶段 | 未来（按新 ADR） |
|---|---|---|
| `VisitorEvent` 签名 | **不改** | 可演进，但须向后兼容现有 `event_builder` 产出 |
| `MemoryPolicy.project_episode` 签名 | **不改** | 维持"消费收敛后事件"契约；多模态逻辑在 Fusion 层 |
| `EpisodeBuilder` / `DefaultEpisodeBuilder` | **不改** | 不变量 I1–I4 不变 |
| `EpisodicRecord` 字段 / 不变量 | **不改** | `evidence_refs`（v1 空）未来可填 `Observation` 引用（ADR-0022 落地后） |
| `Observation` 类 | **不新增** | 新 ADR 定义并实现 |
| Audio Pipeline | **不实现** | 下一阶段（见路线图 §6） |

---

## 5. 风险与开放问题（留给未来 ADR）

1. **跨模态 subject 关联**：视觉 `track_id` 与音频 `speaker_id` 如何关联为同一"访客"？依赖 ADR-0023 身份连续性（v1 `person_identity_id` 恒 None）。
2. **时间窗对齐**：不同模态采样率/延迟不同，Fusion 关联的时间窗阈值如何定？
3. **证据冲突**：视觉"无人"但音频"呼救" → 如何记账到同一 Episode？是否破坏 I2 单调？
4. **隐私/合规**：多模态证据（尤其音频）留存触发数据合规 ADR（ADR-0024 O7）。
5. **`reason_summary` 结构化**：当前 `compose_context` 对 `reason_summary` 文本嗅探（P3，`query.py:151-156`）；多模态接入前应把"非常规时间/呼救/跌倒"沉淀为 `EpisodicRecord` 结构化标记（tags / rule_ids），消除文本嗅探，也便于 Fusion 层直接产出结构化 reason。

---

## 6. 后续路线（顺序铁律）

```
Memory Integration Closure  ← 本阶段（含 Product Closure，不含接口重构）
        ↓
Multimodal Evidence Fusion   （视觉+音频证据融合；按本文档 observation-contract 契约接入）
        ↓
Audio Pipeline
        ↓
Agent Context Layer          （真正消费 Slice C 的 MemoryQuery.compose_context）
        ↓
Agent Reasoning
```

**顺序铁律**：先证明"系统记住过去"（本阶段），再证明"系统理解更多证据"（Multimodal/Audio），最后才"系统解释与协助决策"（Agent）。本阶段不做 Agent、不重构多模态接口。

---

## 7. 参考

- `docs/DESIGN-memory-integration-closure.md` §3.5（多模态扩展接口，本阶段不改代码）
- `docs/ADR/0024-memory-architecture.md` §3.2（MemoryPolicy transformation boundary）
- `docs/ADR/0023-identity-continuity-system.md` — 身份连续性（`person_identity_id` v1 恒 None）
- `docs/ADR/0022-evidence-chain-multimodal-interface.md` — 证据链（`evidence_refs` v1 未落地）
- `src/home_perception/memory/policy.py` — 当前 `project_episode` 契约（视觉耦合，本阶段不动）
- `src/home_perception/memory/records.py` — `EpisodicRecord` 不变量 I1–I4（未来须保持）
