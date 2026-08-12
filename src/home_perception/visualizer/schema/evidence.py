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

from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:  # 仅类型标注：graph.py 依赖本模块的 ProvenanceKind，避免运行期循环
    from home_perception.visualizer.schema.graph import EvidenceGraph

# provenance_kind 闭集（D2 硬规则 4 / D7b）：真实性标注，防"合成当真实"。
# D1 数据源为 ADR-0034 仿真闭环 artifact → SIMULATED；真实设备接入后由 loader 填 REAL_SENSOR。
ProvenanceKind = Literal["REAL_SENSOR", "SIMULATED", "FIXTURE"]

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
    type: str  # 节点类型（stage 判定 / count 摘要）
    summary: str
    verdict: Literal["PASS", "FAIL", "INFO"]  # stage=FAIL/PASS；count=INFO
    provenance_kind: ProvenanceKind
    ref: str  # 溯源：<artifact 文件名>#<记录定位>（D8 Evidence provenance）


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
    """Decision Explanation 视图的一步（检测证据 → 规则 → 策略 → 决策 → 动作）。"""

    kind: str  # evidence / rule / policy / outcome / action
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
    gate: tuple[StageVerdict, ...]
    gate_passed: bool
    gate_degraded: bool
    fingerprints: FingerprintPair
    refs: tuple[str, ...]  # 本项目所有节点的 ref 汇总（验收：provenance 可溯源）
    # D1.5（D5 实体化）：Evidence Graph 为四视图的共享底层结构
    # （Timeline = Graph+timestamp / Decision = Graph+decision subtree /
    #  Cross Modal = Graph+supports edges）。
    graph: EvidenceGraph  # noqa: F821——类型见下方 forward ref


class ProjectionMeta(TypedDict):
    """投影元数据（来自 summary，白名单投影：只取生成时间，不取路径）。"""

    generated_at: str
    scenario_count: int


class EvidenceProjection(TypedDict):
    """D2 投影契约的顶层产物（loader 唯一输出，renderer 唯一输入）。"""

    meta: ProjectionMeta
    scenarios: tuple[ScenarioEvidence, ...]


__all__ = [
    "TIMELINE_STAGE_ORDER",
    "Counts",
    "DecisionEvidence",
    "EvidenceProjection",
    "FingerprintPair",
    "ProjectionMeta",
    "ProvenanceKind",
    "ScenarioEvidence",
    "StageVerdict",
    "TimelineNode",
]
