"""ADR-0035 D3 · Evidence Projection 的类型契约（visualizer 自建，不 import 生产类）。

本模块是 ``visualizer/`` 的**投影目标类型**（D2 EvidenceProjection 的形态）：
只定义 TypedDict / 常量，无任何生产代码引用——``visualizer`` 在 import 图中是
死胡同叶子（AST 契约测试守护：不 import runtime/evaluation/integration/memory）。

类型语义（对齐 ADR-0035 §1）：
- ``TimelineNode``：时间轴节点（stage 级摘要，无真实时间戳时用确定性 step 锚点）；
- ``DecisionEvidence``：Decision Explanation 视图的一步解释（证据 → 规则 → 策略 → 结论）；
- ``FingerprintPair``：两枚闭环指纹（D7 指纹归因视图）；
- ``ScenarioEvidence``：单场景的完整投影（四视图共享，D5 Evidence Graph 的 D1 形态）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:  # 仅类型标注：graph.py 依赖本模块的 ProvenanceKind，避免运行期循环
    from home_perception.visualizer.schema.graph import EvidenceGraph

# provenance_kind 闭集（D2 硬规则 4 / D7b）：真实性标注，防"合成当真实"。
# D1 数据源为 ADR-0034 仿真闭环 artifact → SIMULATED；真实设备接入后由 loader 填 REAL_SENSOR。
ProvenanceKind = Literal["REAL_SENSOR", "SIMULATED", "FIXTURE"]

# 统一时间轴 modality 判别（AC-9：VISION/AUDIO/DECISION/ACTION/MEMORY/CROSS_MODAL 交错呈现，
# 不得三套独立时间轴）。本枚举为视觉层展示用途，与 core/event.py 的 EvidenceModality
# （小写 vision/audio…）**不共用**——视觉层只认投影后的字符串，不得 import 生产枚举（VM-3）。
# 含 OBSERVABILITY（ADR-0034 闭环确含 observability stage，loader 须能投影其 modality）。
TimelineModality = Literal[
    "VISION", "AUDIO", "DECISION", "ACTION", "MEMORY", "CROSS_MODAL", "OBSERVABILITY"
]

# 时间轴 stage 序（canonical 无真实时间戳，D2 缺失粒度降级：用确定性 stage 序锚点）。
TIMELINE_STAGE_ORDER: tuple[str, ...] = (
    "perception",
    "decision",
    "notification",
    "memory",
    "cross_modal",
    "observability",
)


class TimelineNode(TypedDict):
    """时间轴节点（D2：ref 必填、provenance_kind 必填、禁 synthetic）。

    ``verdict`` 是结构化判定（``PASS`` / ``FAIL`` / ``INFO``），渲染层据此着色——
    **禁止**子串匹配 summary 推断状态（评审 #4：文本含 "PASS" 会误着色）。
    """

    timestamp: str  # 确定性 step 锚点（如 "S1".."S6"），非伪造墙钟
    stage: str
    type: str  # 节点类型（stage 判定 / count 摘要 / frame / audio / session）
    summary: str
    verdict: Literal["PASS", "FAIL", "INFO"]  # stage=FAIL/PASS；count=INFO
    modality: TimelineModality  # AC-9 统一时间轴：节点须带 modality 判别
    provenance_kind: ProvenanceKind
    ref: str  # 溯源：<artifact 文件名>#<记录定位>（D8 Evidence provenance）
    # ADR-0036 Phase 2（多模态消费）：可选跨模态视觉 ref（可选高亮目标）。
    # 仅当真实跨模态关联派生（音频节点 → 关联视觉节点）时由 loader 投影产出；
    # 缺失即未投影，绝不占位编造（对齐 AudioEvidenceNode.related_visual_ref）。
    related_visual_ref: NotRequired[str]


class StageVerdict(TypedDict):
    """gate 逐 stage 判定（Gate 视图 + Timeline 节点来源）。"""

    name: str
    passed: bool
    severity: str
    failure_code: str | None


class FingerprintPair(TypedDict):
    """两枚闭环指纹（D7：expectation ⊂ loop 语义在 artifact 层已固化）。"""

    expectation_fingerprint: str
    loop_fingerprint: str


class DecisionEvidence(TypedDict):
    """Decision Explanation 视图的一步（Observation → Reasoning → Outcome）。

    评审 R3-#19：kind 收紧为 loader 实际产出的三分组闭集（D1.5 起不再产出
    rule/policy——renderer 的 _DECISION_KINDS 已同步删除）。
    """

    kind: Literal["evidence", "reasoning", "outcome"]
    label: str
    value: str
    ref: str  # 溯源必填（D8）


class Counts(TypedDict):
    """闭环 artifacts 计数（canonical.artifacts.counts 白名单投影，D7 脱敏）。"""

    perception_events: int
    warnings: int
    commands: int
    sink_commands: int
    decision_traces: int
    episodes: int
    cross_modal_links: int


class ScenarioEvidence(TypedDict):
    """单场景的完整投影（D1 四视图的共享输入，D5 Evidence Graph 的节点骨架）。"""

    scenario_id: str
    ok: bool
    mode: str
    n_frames: int
    scenario_fingerprint: str
    # P0-1（产品化总原则）：产品命题一句话（This case demonstrates）。场景声明静态
    # 元数据，经 canonical → loader 投影；缺省空 = 展示层不渲染命题（向后兼容）。
    product_question: str
    counts: Counts
    event_types: tuple[str, ...]
    risk_levels: tuple[str, ...]
    recommended_actions: tuple[str, ...]
    command_types: tuple[str, ...]
    trace_outcome_kinds: tuple[str, ...]
    suppress_reasons: tuple[str, ...]
    episode_action_command_types: tuple[str, ...]
    timeline: tuple[TimelineNode, ...]
    decision_evidence: tuple[DecisionEvidence, ...]
    # ADR-0036 Slice C（VM-13 Phase B/C 预置）：音频证据节点。
    # AC-12 落地门槛：本字段仅在真实音频证据进入 canonical artifact 后（Phase C）由
    # loader 投影产出；Phase A/B 恒为 ``()``，绝不编造（VM-9 无 ASR/LLM、无
    # text/transcript/FORBIDDEN_AUDIO_FIELDS）。Live Adapter（Phase B）同理恒 ``()``，
    # 仅时间轴增量合并 AUDIO modality 节点。
    audio_evidence: tuple[AudioEvidenceNode, ...]
    # G0-3/G0-2：记忆时间线节点（prior 历史 + 本次会话 episodes，canonical memory_episodes
    # 投影）。无 memory 明细时恒 ``()``（AC-12 不编造）。
    memory_episodes: tuple[MemoryEpisodeNode, ...]
    gate: tuple[StageVerdict, ...]
    gate_passed: bool
    gate_degraded: bool
    # ADR-0036 Slice B（VM-13 Phase A）：Live / 实时模式无集成 Gate 与两枚闭环指纹
    # （expectation/loop 由 ADR-0034 仿真闭环产出，真实传感器流不产出）→ 必须显式
    # ``None``（AC-8 禁伪造），**不得**填占位空串或假指纹。artifact 路径（loader）仍恒
    # 产出 ``FingerprintPair``，仅 live 路径置 ``None``。
    fingerprints: FingerprintPair | None
    refs: tuple[str, ...]  # 本项目所有节点的 ref 汇总（验收：provenance 可溯源）
    # D1.5（D5 实体化）：Evidence Graph 为四视图的共享底层结构
    # （Timeline = Graph+timestamp / Decision = Graph+decision subtree /
    #  Cross Modal = Graph+supports edges）。
    # 评审 R3-#18：**D1.5+ 必填**——调用方必须从 loader 的 EvidenceProjection
    # 输出消费（loader 恒产出 graph），禁止手写构造 ScenarioEvidence 缺该字段。
    graph: EvidenceGraph  # 类型见下方 forward ref（TYPE_CHECKING 导入）


class ProjectionMeta(TypedDict):
    """投影元数据（来自 summary，白名单投影：只取生成时间，不取路径）。"""

    generated_at: str
    scenario_count: int


class AudioAcoustics(TypedDict):
    """声学特征（可选，派生自 ``AudioSegmentEvent``，非语义判定）。"""

    vad_ratio: float
    rms: float
    speech_rate: float


class MemoryEpisodeNode(TypedDict):
    """记忆时间线节点（G0-3/G0-2 黄金案例 · Memory Timeline 展示的事实源）。

    由 loader 从 canonical ``memory_episodes`` 投影（EpisodicRecord 确定性投影）：
    - ``record_id``：episode 唯一标识（prior 前缀 ``ep-prior-*`` = 历史预置；
      运行时 ``ep-<event_id>`` = 本次会话）；
    - ``timestamp`` / ``risk_level`` / ``recommended_action`` / ``summary`` /
      ``reason_summary``：历史 episode 的决策侧语义（供"历史模式重复 → 风险升级"展示）；
    - ``command_types``：该 episode 派生的 ActionCommand 类型（空 = 未派发）；
    - ``prior``：是否为预置历史（G0-3 prior_episodes），区别于本次会话运行期落库。
    AC-12：canonical 无 memory 明细时恒 ``()``，绝不编造。
    """

    record_id: str
    timestamp: str
    risk_level: str
    recommended_action: str
    summary: str
    reason_summary: tuple[str, ...]
    command_types: tuple[str, ...]
    prior: bool


class AudioEvidenceNode(TypedDict):
    """音频证据节点（ADR-0036 · VM-13 Phase C 由 loader 投影产出；Phase A/B 恒 ``()``）。

    字段严格来自真实音频符号（AC-10 / 附录 A），**绝不**出现 ``text`` / ``transcript`` /
    ``FORBIDDEN_AUDIO_FIELDS``（fraud_result/verdict/is_fraud/…）/ 媒体字节
    （raw_audio/mp4/wav）。Case Viewer 执行期间无 ASR/LLM（VM-9），音频只产 perception，
    不产语义判定；媒体字节由 Media Source Adapter 经 ``ref`` 解析（VM-10 / AC-11）。
    """

    timestamp: str                      # ← AudioPerceptionEvent.timestamp（Unix 秒）
    kind: str                           # ← AudioPerceptionKind.value（五值）
    score: float                        # ← .score (0~1)，规则强度（非诈骗概率）
    confidence: float                   # ← .confidence (0~1)，检测可信度
    labels: tuple[str, ...]             # ← .labels / .scored_labels（声学标签透传）
    source_segment_ids: tuple[str, ...]  # ← .source_segment_ids
    ref: str                            # ← trace artifact 定位
    provenance_kind: ProvenanceKind      # REAL_SENSOR / SIMULATED / FIXTURE
    # 以下为可选字段（NotRequired：缺失即未投影，绝不占位编造）
    acoustics: NotRequired[AudioAcoustics]   # 可选声学特征
    signal_category: NotRequired[str]        # 可选证据分类（如 COMMUNICATION）
    related_visual_ref: NotRequired[str]     # 可选跨模态视觉 ref（CrossModalLink 派生）


class EvidenceProjection(TypedDict):
    """D2 投影契约的顶层产物（loader 唯一输出，renderer 唯一输入）。"""

    meta: ProjectionMeta
    scenarios: tuple[ScenarioEvidence, ...]


__all__ = [
    "TIMELINE_STAGE_ORDER",
    "AudioAcoustics",
    "AudioEvidenceNode",
    "Counts",
    "DecisionEvidence",
    "EvidenceProjection",
    "FingerprintPair",
    "ProjectionMeta",
    "ProvenanceKind",
    "ScenarioEvidence",
    "StageVerdict",
    "TimelineModality",
    "TimelineNode",
]
