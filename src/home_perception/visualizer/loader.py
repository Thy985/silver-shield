"""ADR-0035 D2 · 数据投影契约：IntegrationArtifact → EvidenceProjection。

**loader 是渲染层唯一数据入口**（D2 硬规则 1）：renderer 只能消费本模块产出的
``EvidenceProjection``，禁止直接 ``json.load`` 后自由拼装。

契约（D2 / D2b 硬规则）：
- **fail-closed**：artifact 缺失 / 关键字段缺失 → 抛 ``EvidenceProjectionError``，
  绝不产出空白投影（D2b Schema Evolution Fail-Closed；验收 10）；
- **ref 必填**：每个节点携带 ``ref``（``<artifact 文件名>#<记录定位>``），无 ref
  不投影（验收 7）；
- **禁 synthetic node**：只投影 artifact 真实存在的字段；缺失粒度降级为 stage
  摘要节点，绝不拼装（验收 6）；
- **provenance_kind 必填**：D1 数据源为 ADR-0034 仿真闭环 → ``SIMULATED``
  （D2 硬规则 4 / D7b）；
- **脱敏**：白名单投影——只取 scenario_id / 版本 / 判定 / 计数，**不投影路径**
  （``summary.scenarios_dir`` / ``entry.path`` / ``canonical_report`` 一律丢弃）。

本模块只依赖 stdlib（json / pathlib），**不 import 任何生产/验证代码**（D3 AST 契约）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from home_perception.visualizer.schema.evidence import (
    Counts,
    DecisionEvidence,
    EvidenceProjection,
    FingerprintPair,
    ProjectionMeta,
    ScenarioEvidence,
    StageVerdict,
    TimelineModality,
    TimelineNode,
)
from home_perception.visualizer.schema.graph import (
    EvidenceGraph,
    EvidenceGraphEdge,
    EvidenceGraphNode,
)

# 与 run_integration_validation.py 的产物命名对称（单一命名来源）。
SUMMARY_FILENAME = "adr0034_summary.json"
GATE_SUFFIX = ".gate.json"
FINGERPRINTS_SUFFIX = ".fingerprints.json"
CANONICAL_SUFFIX = ".canonical.json"

# stage 名 → (counts 键, 展示标签)。模块级常量（评审 #5：避免循环内重复构造）。
STAGE_COUNT_HINTS: dict[str, tuple[str, str]] = {
    "perception": ("perception_events", "perception events"),
    "decision": ("decision_traces", "decision traces"),
    "notification": ("commands", "commands"),
    "memory": ("episodes", "episodes"),
    "cross_modal": ("cross_modal_links", "cross-modal links"),
}

# stage → 统一时间轴 modality（AC-9：每个时间轴节点须带 modality 判别，交错误现）。
# 覆盖 ADR-0034 闭环全部 stage（含 observability，见 validator 的 StageName Literal）。
_STAGE_MODALITY: dict[str, TimelineModality] = {
    "perception": "VISION",
    "decision": "DECISION",
    "notification": "ACTION",
    "memory": "MEMORY",
    "cross_modal": "CROSS_MODAL",
    "observability": "OBSERVABILITY",
}


def _modality_for_stage(name: str) -> TimelineModality:
    """stage 名 → 统一时间轴 modality；未知 stage 落 OBSERVABILITY（meta 兜底，不污染语义）。"""
    return _STAGE_MODALITY.get(name, "OBSERVABILITY")


class EvidenceProjectionError(ValueError):
    """投影契约违规（fail-closed：缺字段 / 结构非法 → 拒绝生成，不产空白）。"""


def _require(data: dict, key: str, owner: str) -> object:
    if key not in data:
        raise EvidenceProjectionError(f"{owner} 缺关键字段 {key!r}（fail-closed）")
    return data[key]


def _require_path(directory: Path, filename: str, owner: str) -> Path:
    p = directory / filename
    if not p.exists():
        raise EvidenceProjectionError(f"{owner} 缺 artifact：{p.name}（fail-closed）")
    return p


def _load_json(path: Path, owner: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceProjectionError(f"{owner} artifact 解析失败：{path.name} ({exc})") from exc
    if not isinstance(data, dict):
        raise EvidenceProjectionError(f"{owner} artifact 顶层必须是对象：{path.name}")
    return data


def _counts_from(canonical: dict, owner: str) -> Counts:
    artifacts = _require(canonical, "artifacts", owner)
    if not isinstance(artifacts, dict):
        raise EvidenceProjectionError(f"{owner}.artifacts 结构非法（fail-closed）")
    counts = artifacts.get("counts")
    if not isinstance(counts, dict):
        raise EvidenceProjectionError(f"{owner}.artifacts.counts 缺失（fail-closed）")
    required = (
        "perception_events",
        "warnings",
        "commands",
        "sink_commands",
        "decision_traces",
        "episodes",
        "cross_modal_links",
    )
    out: dict[str, int] = {}
    for key in required:
        value = counts.get(key)
        if not isinstance(value, int):
            raise EvidenceProjectionError(f"{owner}.counts.{key} 缺失或非 int（fail-closed）")
        out[key] = value
    return Counts(**out)


def _str_tuple(data: dict, key: str, owner: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise EvidenceProjectionError(f"{owner}.{key} 缺失或非 str 列表（fail-closed）")
    return tuple(value)


def _build_timeline(canonical: dict, scenario_id: str, counts: Counts) -> tuple[TimelineNode, ...]:
    """从 canonical 投影 stage 级时间轴（D2 缺失粒度降级：无帧级 → stage 摘要）。

    ``counts`` 已由 ``_counts_from`` 强校验（7 键必填 + int，fail-closed），
    这里直接索引取值（评审 #7：不重复容忍缺失/非 int）。
    """
    stages = canonical.get("stages")
    if not isinstance(stages, list):
        raise EvidenceProjectionError(f"{scenario_id}.stages 缺失（fail-closed）")
    nodes: list[TimelineNode] = []
    for idx, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise EvidenceProjectionError(f"{scenario_id}.stages[{idx}] 结构非法")
        name = stage.get("name")
        if not isinstance(name, str):
            raise EvidenceProjectionError(f"{scenario_id}.stages[{idx}].name 缺失")
        passed = stage.get("passed")
        if not isinstance(passed, bool):
            raise EvidenceProjectionError(f"{scenario_id}.stages[{idx}].passed 缺失")
        failure_code = stage.get("failure_code")
        # stage 判定（真实字段，非拼装）；verdict 结构化供渲染层着色（评审 #4）
        verdict: Literal["PASS", "FAIL", "INFO"] = "PASS" if passed else "FAIL"
        nodes.append(
            TimelineNode(
                timestamp=f"S{idx + 1}",
                stage=name,
                type="stage",
                summary=(
                    f"stage `{name}` PASS"
                    if passed
                    else f"stage `{name}` FAIL({failure_code})"
                ),
                verdict=verdict,
                modality=_modality_for_stage(name),
                provenance_kind="SIMULATED",
                ref=f"{scenario_id}.canonical.json#stages[{idx}]",
            )
        )
        # 该 stage 关联的 artifacts 计数（counts 已强校验，直接索引）
        if name in STAGE_COUNT_HINTS:
            count_key, label = STAGE_COUNT_HINTS[name]
            value = counts[count_key]
            nodes.append(
                TimelineNode(
                    timestamp=f"S{idx + 1}.1",
                    stage=name,
                    type="count",
                    summary=f"{label}: {value}",
                    verdict="INFO",
                    modality=_modality_for_stage(name),
                    provenance_kind="SIMULATED",
                    ref=f"{scenario_id}.canonical.json#artifacts.counts.{count_key}",
                )
            )
    return tuple(nodes)


def _build_decision_evidence(canonical: dict, scenario_id: str) -> tuple[DecisionEvidence, ...]:
    """Decision Explanation 视图：从 canonical 白名单字段投影（检测证据→决策→动作）。

    缺失粒度（ADR-0031 五 bundle 原始 trace）不在 D1 canonical artifact 内 →
    降级为 canonical 已有字段的摘要解释（不捏造 bundle 细节）。
    """
    artifacts = canonical.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise EvidenceProjectionError(f"{scenario_id}.artifacts 缺失（fail-closed）")
    evidence: list[DecisionEvidence] = []

    def _add(kind: str, label: str, value: object, ref: str) -> None:
        evidence.append(
            DecisionEvidence(
                kind=kind,
                label=label,
                value=str(value) if value not in (None, []) else "(无)",
                ref=f"{scenario_id}.canonical.json#{ref}",
            )
        )

    # 白名单字段投影（评审 R2-#4）：全部走 _str_tuple 强校验（非空 str 列表），
    # 缺字段视为空（该维度无证据，降级不捏造）；非 str 元素 → fail-closed。
    # 语义分组（D1.5）：Observation Evidence（检测证据）→ Decision Reasoning
    # （推理依据：trace outcome + 风险级别）→ Decision Outcome（结论：动作）。
    def _add_joined(key: str, kind: str, label: str, *, optional: bool = True) -> None:
        try:
            values = _str_tuple(artifacts, key, scenario_id)
        except EvidenceProjectionError:
            if optional and key not in artifacts:
                return  # 字段缺失 = 该维度无证据（可选），不报错
            raise
        if values:
            _add(kind, label, ", ".join(values), f"artifacts.{key}")

    _add_joined("event_types", "evidence", "Observation · 检测证据（事件类型）")
    _add_joined("trace_outcome_kinds", "reasoning", "Reasoning · 决策结果（trace outcome）")
    _add_joined("risk_levels", "reasoning", "Reasoning · 风险级别")
    _add_joined("recommended_actions", "outcome", "Outcome · 推荐动作")
    _add_joined("command_types", "outcome", "Outcome · 已执行命令")
    _add_joined("suppress_reasons", "outcome", "Outcome · 抑制原因")
    if not evidence:
        # 无任何决策证据字段（benign 空闭环）→ 降级摘要，非捏造
        _add("outcome", "决策证据", "(闭环无事件/警告——benign 场景预期)", "artifacts.counts")
    return tuple(evidence)


def _build_evidence_graph(
    canonical: dict,
    scenario_id: str,
    artifacts: dict,
    counts: Counts,
) -> EvidenceGraph:
    """D1.5（D5 实体化）：从 canonical 投影**因果链图**（Scenario→Event→Decision→
    Action→Episode→Link）。

    - 节点只投影真实字段（event_types / trace_outcome_kinds / recommended_actions
      的真实值 + counts 摘要），缺失 → 不建节点（禁 synthetic）；
    - 每个节点/边携带 ``ref``（溯源到 canonical 具体字段）与 ``provenance_kind``；
    - 边类型闭集（observed_from / caused_by / triggered / supports / stored_as）。
    """
    nodes: list[EvidenceGraphNode] = []
    edges: list[EvidenceGraphEdge] = []
    canon_ref = f"{scenario_id}.canonical.json"

    # 节点 id 约定：当前为**单 scenario 渲染**（每场景独立容器 + 独立 ECharts 实例），
    # 节点 id 不带 scenario 前缀（如 "scn" / "event-0"）；若未来支持多 scenario
    # 同图合并渲染，节点 id 需加 f"{scenario_id}-" 前缀防撞名（评审 R3-#9）。
    def _node(nid: str, ntype: str, label: str, ref: str) -> None:
        nodes.append(
            EvidenceGraphNode(
                id=nid, type=ntype, label=label, ref=f"{canon_ref}#{ref}",
                provenance_kind="SIMULATED",
            )
        )

    def _edge(source: str, target: str, etype: str, ref: str) -> None:
        edges.append(
            EvidenceGraphEdge(
                source=source, target=target, type=etype, ref=f"{canon_ref}#{ref}"
            )
        )

    # 因果链公共投影模板（评审 R3-#5：折叠五段近似重复为 3 行调用）。
    # 数据源 = artifacts.<key> 列表（走 _str_tuple 强校验，语义与
    # _build_decision_evidence 完全统一——评审 R3-#1：缺字段 = 该层无证据，
    # 非 str 元素 → EvidenceProjectionError 而非 TypeError）。
    # 返回本层节点 id 列表；prev_ids 为空 → 本层不建节点（防孤立节点，
    # 评审 R3-#8：无事件支撑的 Decision 不投影）。
    def _project_chain(
        key: str,
        ntype: str,
        prefix: str,
        edge_type: str,
        prev_ids: tuple[str, ...],
    ) -> list[str]:
        values = _str_tuple(artifacts, key, scenario_id)
        ids: list[str] = []
        for i, value in enumerate(values):
            nid = f"{prefix}-{i}"
            _node(nid, ntype, value, f"artifacts.{key}[{i}]")
            ids.append(nid)
            for prev in prev_ids:
                _edge(prev, nid, edge_type, f"artifacts.{key}[{i}]")
        return ids

    # Scenario 锚点
    _node("scn", "Scenario", scenario_id, "scenario_id")

    # Event ← observed_from ← Scenario（无前置依赖，仅依赖自身数据）
    event_ids = _project_chain("event_types", "Event", "event", "observed_from", ("scn",))

    # Decision ← caused_by ← Event（无事件 → 决策无因，不建节点——防孤立）
    decision_ids: list[str] = []
    if event_ids:
        decision_ids = _project_chain(
            "trace_outcome_kinds", "Decision", "decision", "caused_by", tuple(event_ids)
        )

    # Action ← triggered ← Decision（无决策 → 动作无因，不建节点——防孤立）
    action_ids: list[str] = []
    if decision_ids:
        action_ids = _project_chain(
            "recommended_actions", "Action", "action", "triggered", tuple(decision_ids)
        )

    # Episode ← stored_as ← Action（仅 counts 摘要；episode_id 未落盘不渲染）
    episode_id = "episodes"
    if counts["episodes"] > 0:
        _node(
            episode_id, "Episode", f"{counts['episodes']} episodes",
            "artifacts.counts.episodes",
        )
        for action_id in action_ids:
            _edge(action_id, episode_id, "stored_as", "artifacts.counts.episodes")

    # Link ← supports ← Episode（仅 counts 摘要）
    if counts["cross_modal_links"] > 0 and counts["episodes"] > 0:
        _node(
            "links", "Link", f"{counts['cross_modal_links']} links",
            "artifacts.counts.cross_modal_links",
        )
        _edge(episode_id, "links", "supports", "artifacts.counts.cross_modal_links")

    return EvidenceGraph(
        scenario_id=scenario_id, nodes=tuple(nodes), edges=tuple(edges)
    )


def _build_gate(
    scenario_id: str,
    gate_data: dict,
    owner: str,
) -> tuple[tuple[StageVerdict, ...], bool, bool]:
    verdicts_raw = _require(gate_data, "verdicts", owner)
    if not isinstance(verdicts_raw, list):
        raise EvidenceProjectionError(f"{owner}.verdicts 结构非法（fail-closed）")
    verdicts: list[StageVerdict] = []
    for idx, v in enumerate(verdicts_raw):
        if not isinstance(v, dict):
            raise EvidenceProjectionError(f"{owner}.verdicts[{idx}] 结构非法")
        name = _require(v, "name", f"{owner}.verdicts[{idx}]")
        passed = _require(v, "passed", f"{owner}.verdicts[{idx}]")
        severity = _require(v, "severity", f"{owner}.verdicts[{idx}]")
        if not isinstance(name, str) or not isinstance(passed, bool) or not isinstance(severity, str):
            raise EvidenceProjectionError(f"{owner}.verdicts[{idx}] 字段类型非法（fail-closed）")
        # failure_code 类型校验（评审 R2-#9）：str | None，否则 fail-closed——
        # 防 schema 演化引入 dict/int 等破坏渲染层 f-string 语义。
        failure_code = v.get("failure_code")
        if not isinstance(failure_code, (str, type(None))):
            raise EvidenceProjectionError(
                f"{owner}.verdicts[{idx}].failure_code 必须是 str|None（fail-closed）"
            )
        verdicts.append(
            StageVerdict(
                name=name,
                passed=passed,
                severity=severity,
                failure_code=failure_code,
            )
        )
    passed = _require(gate_data, "passed", owner)
    degraded = _require(gate_data, "degraded", owner)
    if not isinstance(passed, bool) or not isinstance(degraded, bool):
        raise EvidenceProjectionError(f"{owner}.passed/degraded 非 bool（fail-closed）")
    return tuple(verdicts), passed, degraded


def _build_fingerprints(scenario_id: str, fp_data: dict, owner: str) -> FingerprintPair:
    expectation = _require(fp_data, "expectation_fingerprint", owner)
    loop = _require(fp_data, "loop_fingerprint", owner)
    if not isinstance(expectation, str) or not expectation or not isinstance(loop, str) or not loop:
        raise EvidenceProjectionError(f"{owner} 指纹为空（fail-closed）")
    return FingerprintPair(expectation_fingerprint=expectation, loop_fingerprint=loop)


def _project_scenario(directory: Path, scenario_id: str, summary_entry: dict) -> ScenarioEvidence:
    owner = f"scenario[{scenario_id}]"
    canonical = _load_json(_require_path(directory, f"{scenario_id}{CANONICAL_SUFFIX}", owner), owner)
    gate = _load_json(_require_path(directory, f"{scenario_id}{GATE_SUFFIX}", owner), owner)
    fingerprints = _load_json(
        _require_path(directory, f"{scenario_id}{FINGERPRINTS_SUFFIX}", owner), owner
    )

    # canonical 关键字段（fail-closed）
    ok = _require(canonical, "ok", owner)
    mode = _require(canonical, "mode", owner)
    n_frames = _require(canonical, "n_frames", owner)
    scenario_fingerprint = _require(canonical, "scenario_fingerprint", owner)
    if not isinstance(ok, bool) or not isinstance(mode, str):
        raise EvidenceProjectionError(f"{owner} canonical 字段类型非法（fail-closed）")
    if not isinstance(n_frames, int) or n_frames < 0:
        # 语义约束（评审 #12）：帧数必须非负——负数 = 数据异常，fail-closed
        raise EvidenceProjectionError(f"{owner}.n_frames 必须是非负 int（fail-closed）")
    if not isinstance(scenario_fingerprint, str) or not scenario_fingerprint:
        raise EvidenceProjectionError(f"{owner}.scenario_fingerprint 为空（fail-closed）")

    verdicts, gate_passed, gate_degraded = _build_gate(scenario_id, gate, owner)
    counts = _counts_from(canonical, owner)
    timeline = _build_timeline(canonical, scenario_id, counts)
    decision_evidence = _build_decision_evidence(canonical, scenario_id)
    refs = tuple(n["ref"] for n in timeline) + tuple(e["ref"] for e in decision_evidence)

    artifacts = canonical.get("artifacts")
    if not isinstance(artifacts, dict):
        raise EvidenceProjectionError(f"{owner}.artifacts 缺失（fail-closed）")
    graph = _build_evidence_graph(canonical, scenario_id, artifacts, counts)
    refs += tuple(n["ref"] for n in graph["nodes"]) + tuple(
        e["ref"] for e in graph["edges"]
    )

    return ScenarioEvidence(
        scenario_id=scenario_id,
        ok=ok,
        mode=mode,
        n_frames=n_frames,
        scenario_fingerprint=scenario_fingerprint,
        counts=counts,
        event_types=_str_tuple(artifacts, "event_types", owner),
        risk_levels=_str_tuple(artifacts, "risk_levels", owner),
        recommended_actions=_str_tuple(artifacts, "recommended_actions", owner),
        command_types=_str_tuple(artifacts, "command_types", owner),
        trace_outcome_kinds=_str_tuple(artifacts, "trace_outcome_kinds", owner),
        suppress_reasons=_str_tuple(artifacts, "suppress_reasons", owner),
        episode_action_command_types=_str_tuple(
            artifacts, "episode_action_command_types", owner
        ),
        timeline=timeline,
        decision_evidence=decision_evidence,
        # ADR-0036 Slice C（AC-12）：loader 在 Phase C 才投影真实音频；Phase A/B 恒 ``()``，
        # 仅补契约默认值防 TypedDict 缺键（不扩展投影逻辑、不编造音频证据）。
        audio_evidence=(),
        gate=verdicts,
        gate_passed=gate_passed,
        gate_degraded=gate_degraded,
        fingerprints=_build_fingerprints(scenario_id, fingerprints, owner),
        refs=refs,
        graph=graph,
    )


def load_evidence_projection(directory: str | Path) -> EvidenceProjection:
    """D2 投影入口：目录内全部场景 → EvidenceProjection（fail-closed）。

    Args:
        directory: ``artifacts/adr0034_integration/`` 类目录（含 summary + 每场景
            canonical/gate/fingerprints）。

    Raises:
        EvidenceProjectionError: 目录缺失 / summary 缺失 / 任一场景 artifact 不全。
        FileNotFoundError: 目录不存在。
    """
    d = Path(directory).resolve()
    if not d.is_dir():
        raise FileNotFoundError(f"artifact 目录不存在：{d}")
    summary_path = _require_path(d, SUMMARY_FILENAME, "summary")
    summary = _load_json(summary_path, "summary")
    scenarios_raw = _require(summary, "scenarios", "summary")
    if not isinstance(scenarios_raw, list):
        raise EvidenceProjectionError("summary.scenarios 结构非法（fail-closed）")

    scenarios: list[ScenarioEvidence] = []
    for entry in scenarios_raw:
        if not isinstance(entry, dict):
            raise EvidenceProjectionError("summary.scenarios 含非对象条目（fail-closed）")
        scenario_id = entry.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise EvidenceProjectionError("summary.scenarios 条目缺 scenario_id（fail-closed）")
        scenarios.append(_project_scenario(d, scenario_id, entry))

    # 元数据白名单投影：只取生成时间 + 场景数（不投影 scenarios_dir/path——D7 脱敏）
    # generated_at 属 summary 必填字段，缺失即 fail-closed（评审 #6：不静默降级）。
    generated_at = _require(summary, "generated_at", "summary")
    if not isinstance(generated_at, str):
        raise EvidenceProjectionError("summary.generated_at 非 str（fail-closed）")
    return EvidenceProjection(
        meta=ProjectionMeta(generated_at=generated_at, scenario_count=len(scenarios)),
        scenarios=tuple(scenarios),
    )


__all__ = [
    "CANONICAL_SUFFIX",
    "FINGERPRINTS_SUFFIX",
    "GATE_SUFFIX",
    "STAGE_COUNT_HINTS",
    "SUMMARY_FILENAME",
    "EvidenceProjectionError",
    "load_evidence_projection",
]
