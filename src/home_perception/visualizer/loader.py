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
    AudioEvidenceNode,
    CaseTimeTrack,
    Counts,
    DecisionEvidence,
    EvidenceProjection,
    FingerprintPair,
    InterventionDispatch,
    MemoryEpisodeNode,
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


def _str_field(data: dict, key: str, owner: str) -> str:
    """canonical 顶层字符串字段（P0-1 product_question）：缺键/非 str → 空串（向后兼容）。

    与 ``_str_tuple`` 的 fail-closed 不同：product_question 是**可选展示元数据**
    （旧 artifact 无此键），缺失不抛错、给空串（展示层据此不渲染命题）。
    """
    value = data.get(key)
    if not isinstance(value, str):
        return ""
    return value


def _str_tuple_field(data: dict, key: str, owner: str) -> tuple[str, ...]:
    """canonical 顶层 str 列表字段（P0-4 suppress_reasons）：缺键/非 list → 空元组（向后兼容）。

    与 ``_str_tuple`` 的 fail-closed 不同：suppress_reasons 是**可选展示元数据**
    （旧 artifact 无此键，且既有 conftest 未声明），缺失不抛错、给空元组——
    展示层据此不渲染负向能力卡。值必须是 str 列表，否则退化为空元组（宽松容错，
    不影响主链路，符合 VM-1「缺失不伪造」）。
    """
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return ()
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

    # Link ← (relationship) ← Episode。
    # P0-3.1：真实关联边优先（artifacts.cross_modal_links 为真实对象列表）→ 逐条建 Link
    # 节点 + 按 relationship 类型建边（SUPPORTS→supports / CO_OCCURS→co_occurs，EdgeType
    # 闭集已扩展）；旧 artifact 仅含计数（无此键或空）时降级为单条 counts 摘要节点
    # （"N links"，ref 指向 counts），绝不伪造真边（D2 禁 synthetic）。
    real_links = artifacts.get("cross_modal_links")
    has_real_links = (
        isinstance(real_links, list)
        and len(real_links) > 0
        and all(isinstance(d, dict) for d in real_links)
    )
    if has_real_links:
        for i, link in enumerate(real_links):
            rel = link.get("relationship")
            if not isinstance(rel, str) or not rel:
                raise EvidenceProjectionError(
                    f"{scenario_id}.cross_modal_links[{i}].relationship 缺失/非 str（fail-closed）"
                )
            edge_type = "supports" if rel == "supports" else "co_occurs"
            _node(
                f"link-{i}", "Link",
                f"{rel} · {link.get('link_id')}",
                f"artifacts.cross_modal_links[{i}]",
            )
            if counts["episodes"] > 0:
                _edge(
                    episode_id, f"link-{i}", edge_type,
                    f"artifacts.cross_modal_links[{i}]",
                )
    elif counts["cross_modal_links"] > 0 and counts["episodes"] > 0:
        _node(
            "links", "Link", f"{counts['cross_modal_links']} links",
            "artifacts.counts.cross_modal_links",
        )
        _edge(episode_id, "links", "supports", "artifacts.counts.cross_modal_links")

    return EvidenceGraph(
        scenario_id=scenario_id, nodes=tuple(nodes), edges=tuple(edges)
    )


def _build_audio_evidence(
    artifacts: dict, scenario_id: str, owner: str
) -> tuple[AudioEvidenceNode, ...]:
    """ADR-0036 VM-13 Phase C：从 canonical ``artifacts.audio_evidence`` 投影真实音频证据。

    仅当真实音频符号已进入 canonical（由 ``IntegrationRunner`` 经 ``synth.audio_events``
    投影、report 落盘为 ``audio_*`` 前缀键）才非空；其余恒 ``()``（AC-12：绝不编造）。
    字段逐个强校验（fail-closed，缺字段 / 类型错误 → 拒绝，不兜底占位），与 ``_require``
    风格一致。键名统一 ``audio_*`` 前缀，规避脱敏禁止键 ``"score"`` 精确匹配。

    映射（canonical ``audio_*`` 键 → ``AudioEvidenceNode`` 字段）：
      audio_timestamp            → timestamp (str, Unix 秒)
      audio_kind                → kind (AudioPerceptionKind.value)
      audio_score              → score (0~1，规则强度非诈骗概率)
      audio_confidence         → confidence (0~1，检测可信度)
      audio_labels             → labels
      audio_source_segment_ids → source_segment_ids
      ref                      → ``<sid>.canonical.json#artifacts.audio_evidence[i]``
      provenance_kind          → SIMULATED（D1 artifact 路径恒仿真闭环）
    """
    raw = artifacts.get("audio_evidence")
    if raw is None:
        return ()  # 无音频符号：Phase A/B 与未声明音频场景恒空（AC-12）
    if not isinstance(raw, list):
        raise EvidenceProjectionError(
            f"{owner}.artifacts.audio_evidence 结构非法（fail-closed）"
        )
    nodes: list[AudioEvidenceNode] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise EvidenceProjectionError(
                f"{owner}.artifacts.audio_evidence[{i}] 非对象（fail-closed）"
            )
        timestamp = entry.get("audio_timestamp")
        kind = entry.get("audio_kind")
        score = entry.get("audio_score")
        confidence = entry.get("audio_confidence")
        labels = entry.get("audio_labels")
        segments = entry.get("audio_source_segment_ids")
        # 可选跨模态视觉 ref（Phase 2：音频节点 → 关联视觉节点）。缺失即未投影，绝不占位
        # 编造；非 str 即拒绝（fail-closed，与 loader 其余字段校验一致）。
        related = entry.get("audio_related_visual_ref")
        if related is not None and not isinstance(related, str):
            raise EvidenceProjectionError(
                f"{owner}.audio_evidence[{i}].audio_related_visual_ref 非 str（fail-closed）"
            )
        # 逐个字段强校验（fail-closed：缺字段 / 类型错误即拒绝，不兜底填占位）
        if not isinstance(timestamp, (int, float)):
            raise EvidenceProjectionError(
                f"{owner}.audio_evidence[{i}].audio_timestamp 缺失/非数值（fail-closed）"
            )
        if not isinstance(kind, str) or not kind:
            raise EvidenceProjectionError(
                f"{owner}.audio_evidence[{i}].audio_kind 缺失/非 str（fail-closed）"
            )
        if not isinstance(score, (int, float)):
            raise EvidenceProjectionError(
                f"{owner}.audio_evidence[{i}].audio_score 缺失/非数值（fail-closed）"
            )
        if not isinstance(confidence, (int, float)):
            raise EvidenceProjectionError(
                f"{owner}.audio_evidence[{i}].audio_confidence 缺失/非数值（fail-closed）"
            )
        if not isinstance(labels, list) or not all(isinstance(v, str) for v in labels):
            raise EvidenceProjectionError(
                f"{owner}.audio_evidence[{i}].audio_labels 非 str 列表（fail-closed）"
            )
        if not isinstance(segments, list) or not all(
            isinstance(v, str) for v in segments
        ):
            raise EvidenceProjectionError(
                f"{owner}.audio_evidence[{i}].audio_source_segment_ids 非 str 列表（fail-closed）"
            )
        node = AudioEvidenceNode(
            timestamp=str(float(timestamp)),
            kind=kind,
            score=float(score),
            confidence=float(confidence),
            labels=tuple(labels),
            source_segment_ids=tuple(segments),
            ref=f"{scenario_id}.canonical.json#artifacts.audio_evidence[{i}]",
            provenance_kind="SIMULATED",
        )
        if related is not None:
            node["related_visual_ref"] = related
        nodes.append(node)
    return tuple(nodes)


def _build_memory_episodes(
    artifacts: dict, scenario_id: str, owner: str
) -> tuple[MemoryEpisodeNode, ...]:
    """G0-3/G0-2：从 canonical ``artifacts.memory_episodes`` 投影记忆时间线节点。

    仅当 canonical 含 memory 明细（report 落盘 ``memory_*`` 前缀键）才非空；其余恒
    ``()``（AC-12：绝不编造）。字段逐个强校验（fail-closed），键名 ``memory_*`` 前缀
    规避脱敏禁止键（"score"/"decision" 精确匹配）。

    映射（canonical ``memory_*`` 键 → ``MemoryEpisodeNode`` 字段）：
      memory_record_id          → record_id (str，prior 前缀 ep-prior-* = 历史)
      memory_timestamp          → timestamp (str ISO)
      memory_risk_level         → risk_level
      memory_recommended_action → recommended_action
      memory_summary            → summary
      memory_reason_summary     → reason_summary (str 列表)
      memory_command_types      → command_types (str 列表)
      memory_prior              → prior (bool)
    """
    raw = artifacts.get("memory_episodes")
    if raw is None:
        return ()  # 旧 artifact 无 memory 明细（AC-12：不编造）
    if not isinstance(raw, list):
        raise EvidenceProjectionError(f"{owner}.artifacts.memory_episodes 结构非法（fail-closed）")
    nodes: list[MemoryEpisodeNode] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvidenceProjectionError(
                f"{owner}.artifacts.memory_episodes[{i}] 非对象（fail-closed）"
            )
        record_id = item.get("memory_record_id")
        ts = item.get("memory_timestamp")
        risk = item.get("memory_risk_level", "")
        action = item.get("memory_recommended_action", "")
        summary = item.get("memory_summary", "")
        reasons = item.get("memory_reason_summary", [])
        commands = item.get("memory_command_types", [])
        prior = item.get("memory_prior", False)
        if not isinstance(record_id, str) or not record_id:
            raise EvidenceProjectionError(
                f"{owner}.artifacts.memory_episodes[{i}].memory_record_id 缺失/非 str（fail-closed）"
            )
        if not isinstance(ts, str) or not ts:
            raise EvidenceProjectionError(
                f"{owner}.artifacts.memory_episodes[{i}].memory_timestamp 缺失/非 str（fail-closed）"
            )
        if not isinstance(reasons, list) or not all(isinstance(v, str) for v in reasons):
            raise EvidenceProjectionError(
                f"{owner}.artifacts.memory_episodes[{i}].memory_reason_summary 非 str 列表（fail-closed）"
            )
        if not isinstance(commands, list) or not all(isinstance(v, str) for v in commands):
            raise EvidenceProjectionError(
                f"{owner}.artifacts.memory_episodes[{i}].memory_command_types 非 str 列表（fail-closed）"
            )
        nodes.append(
            MemoryEpisodeNode(
                record_id=record_id,
                timestamp=ts,
                risk_level=str(risk),
                recommended_action=str(action),
                summary=str(summary),
                reason_summary=tuple(reasons),
                command_types=tuple(commands),
                prior=bool(prior),
            )
        )
    return tuple(nodes)


def _build_case_time_tracks(
    audio_evidence: tuple[AudioEvidenceNode, ...],
    memory_episodes: tuple[MemoryEpisodeNode, ...],
    scenario_id: str,
) -> tuple[CaseTimeTrack, ...]:
    """P0-2：Case Time 主轴事件标记（相对最早证据 T0，确定性排序）。

    - 音频轨：``audio_evidence`` timestamp（float Unix 秒）；
    - 记忆：``memory_episodes`` 中**非 prior**（本次会话）的 timestamp（ISO → Unix 秒）；
      prior 历史是背景（3 days ago/yesterday），**不进当前 Case Time 主轴**（VM-10）；
    - T0 = 最早音频时间（音频存在时），否则最早非 prior 记忆时间；无事件 → 恒 ``()``；
    - 相对时间为负的事件（早于 T0，属历史背景）→ 丢弃（诚实，不伪造当下时刻）；
    - 排序 ``(time, kind, ref)`` 确定性（同 seed 两次运行一致）。
    """
    events: list[tuple[float, str, str, str]] = []  # (time, kind, ref, label)
    for a in audio_evidence:
        try:
            ts = float(a.get("timestamp", 0.0))
        except (TypeError, ValueError):
            continue
        events.append(
            (ts, "audio", a.get("ref", ""), str(a.get("kind", "audio")))
        )
    for m in memory_episodes:
        if m.get("prior"):
            continue  # prior 历史不进当前 Case Time 主轴
        raw = m.get("timestamp", "")
        try:
            from datetime import datetime

            ts = datetime.fromisoformat(str(raw)).timestamp()
        except (TypeError, ValueError):
            continue
        label = str(m.get("summary", ""))[:24]
        events.append((ts, "memory", "", label or str(m.get("record_id", ""))))

    if not events:
        return ()
    t0 = min(ts for ts, *_ in events)
    tracks: list[CaseTimeTrack] = []
    for ts, kind, ref, label in events:
        rel = ts - t0
        if rel < 0:
            continue  # 早于 T0（历史背景）→ 不标记
        tracks.append(
            CaseTimeTrack(time=round(rel, 3), kind=kind, ref=ref, label=label)
        )
    tracks.sort(key=lambda t: (t["time"], t["kind"], t["ref"], t["label"]))
    return tuple(tracks)


def _build_cross_modal_timeline_nodes(
    artifacts: dict, scenario_id: str
) -> tuple[TimelineNode, ...]:
    """ADR-0027 D5（P0-3.1）：真实跨模态关联边 → 统一时间轴的 ``CROSS_MODAL`` 节点。

    仅当 ``artifacts.cross_modal_links`` 为真实对象列表（``list[dict]`` 且非空）才投影；
    旧 artifact 仅含计数（无此键或空）时返回 ``()`` —— 此时统一时间轴的 cross_modal stage
    仍由 ``_build_timeline`` 产出一条 count 摘要节点（"cross-modal links: N"），不伪造真边
    （D2 禁 synthetic）。每个节点携带 ``ref`` 溯源到 ``#artifacts.cross_modal_links[i]``，
    ``provenance_kind=SIMULATED``（D1 恒仿真闭环）。

    字段逐个强校验（fail-closed：缺 ``relationship`` / ``link_id`` / ``created_at`` 或类型
    非法即拒绝，不兜底占位），与 ``_build_audio_evidence`` 风格一致。
    """
    raw = artifacts.get("cross_modal_links")
    if not isinstance(raw, list) or not raw:
        return ()
    nodes: list[TimelineNode] = []
    for i, link in enumerate(raw):
        if not isinstance(link, dict):
            raise EvidenceProjectionError(
                f"{scenario_id}.artifacts.cross_modal_links[{i}] 非对象（fail-closed）"
            )
        rel = link.get("relationship")
        link_id = link.get("link_id")
        created_at = link.get("created_at")
        episode_ids = link.get("episode_ids") or []
        if not isinstance(rel, str) or not rel:
            raise EvidenceProjectionError(
                f"{scenario_id}.cross_modal_links[{i}].relationship 缺失/非 str（fail-closed）"
            )
        if not isinstance(link_id, str) or not link_id:
            raise EvidenceProjectionError(
                f"{scenario_id}.cross_modal_links[{i}].link_id 缺失/非 str（fail-closed）"
            )
        if not isinstance(created_at, str):
            raise EvidenceProjectionError(
                f"{scenario_id}.cross_modal_links[{i}].created_at 缺失/非 str（fail-closed）"
            )
        nodes.append(
            TimelineNode(
                timestamp=created_at,
                stage="cross_modal",
                type="link",
                summary=f"跨模态关联：{rel} · {len(episode_ids)} episodes · {link_id}",
                verdict="INFO",
                modality="CROSS_MODAL",
                provenance_kind="SIMULATED",
                ref=f"{scenario_id}.canonical.json#artifacts.cross_modal_links[{i}]",
            )
        )
    return tuple(nodes)


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


# P1（干预回执 + 闭环可达性）：command_types → 派发回执映射（VM-1 纯派生，零编造）。
# 语义对齐 golden 闭环契约：SEND_FAMILY_MESSAGE → 家属 / family_handled；
# CREATE_COMMUNITY_TASK → 社区 / community_done；LOG_ONLY → 仅系统记录（无外部接收方）。
# 注意：本映射是 loader 展示派生层（visualizer 死胡同叶子），不得 import 生产 action 包
# （VM-3 / D3 AST 契约）；运行时若新增第 4 类 command_type，未命中映射 → 目标角色退化为
# 「未知接收方」、无期望闭环，仍以原始枚举呈现（fail-closed，不静默丢弃）。
_INTERVENTION_TARGET_ROLE: dict[str, str] = {
    "SEND_FAMILY_MESSAGE": "家属",
    "CREATE_COMMUNITY_TASK": "社区",
    "LOG_ONLY": "系统（仅记录）",
}
_INTERVENTION_CLOSURE: dict[str, str] = {
    "SEND_FAMILY_MESSAGE": "family_handled",
    "CREATE_COMMUNITY_TASK": "community_done",
    # LOG_ONLY：无外部接收方，无期望闭环状态（空串，不伪造）。
}


def _build_intervention_dispatch(
    command_types: tuple[str, ...],
) -> tuple[InterventionDispatch, ...]:
    """从真实 command_types 派生干预派发回执（VM-1 纯派生，AC-12 不编造）。

    去重保序（按首次出现顺序）；未知类型保留原始枚举（fail-closed，不丢弃）。
    空输入 → ``()``（不渲染回执卡，渲染层据空态呈现诚实空卡）。
    """
    if not command_types:
        return ()
    seen: set[str] = set()
    rows: list[InterventionDispatch] = []
    for ct in command_types:
        if ct in seen:
            continue
        seen.add(ct)
        rows.append(
            InterventionDispatch(
                command_type=ct,
                target_role=_INTERVENTION_TARGET_ROLE.get(ct, "未知接收方"),
                closure_expectation=_INTERVENTION_CLOSURE.get(ct, ""),
            )
        )
    return tuple(rows)


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
    timeline_nodes = list(_build_timeline(canonical, scenario_id, counts))
    decision_evidence = _build_decision_evidence(canonical, scenario_id)
    refs = tuple(n["ref"] for n in timeline_nodes) + tuple(e["ref"] for e in decision_evidence)

    artifacts = canonical.get("artifacts")
    if not isinstance(artifacts, dict):
        raise EvidenceProjectionError(f"{owner}.artifacts 缺失（fail-closed）")
    graph = _build_evidence_graph(canonical, scenario_id, artifacts, counts)
    refs += tuple(n["ref"] for n in graph["nodes"]) + tuple(
        e["ref"] for e in graph["edges"]
    )

    # ADR-0036 VM-13 Phase C：真实音频证据并入「统一 Evidence Timeline」——
    # 作为 AUDIO modality 节点与 VISION/DECISION/... 同台（非孤立区块）。ref 指向
    # canonical.audio_evidence[i] 可回溯；provenance_kind=SIMULATED（D1 恒仿真闭环）；
    # 不调用 ASR/LLM、不生成音频（VM-9）。无音频场景恒不追加（AC-12 绝不编造）。
    audio_evidence = _build_audio_evidence(artifacts, scenario_id, owner)
    memory_nodes = _build_memory_episodes(artifacts, scenario_id, owner)
    for a in audio_evidence:
        # ADR-0036 Phase 2（多模态消费）：在主时间轴直接富化音频细节（kind/强度/置信/标签/段），
        # 不必依赖分离表格即可消费；related_visual_ref 派生自真实跨模态关联（缺则恒不携带）。
        detail = f"音频感知：{a['kind']} · 强度 {a['score']:.2f} · 置信 {a['confidence']:.2f}"
        if a["labels"]:
            detail += f" · 标签 {', '.join(a['labels'])}"
        if a["source_segment_ids"]:
            detail += f" · 段 {', '.join(a['source_segment_ids'])}"
        node = TimelineNode(
            timestamp=a["timestamp"],
            stage="perception",
            type=a["kind"],
            summary=detail,
            verdict="INFO",
            modality="AUDIO",
            provenance_kind=a["provenance_kind"],
            ref=a["ref"],
        )
        related = a.get("related_visual_ref")
        if related is not None:
            node["related_visual_ref"] = related
        timeline_nodes.append(node)
    refs += tuple(a["ref"] for a in audio_evidence)

    # ADR-0027 D5（P0-3.1）：真实跨模态关联边（来自 canonical.artifacts.cross_modal_links）
    # 作为 CROSS_MODAL modality 节点并入「统一 Evidence Timeline」——与 AUDIO/VISION/… 同台
    # （非孤立区块），ref 指向 canonical.cross_modal_links[i] 可回溯；provenance_kind=SIMULATED
    # （D1 恒仿真闭环）。旧 artifact 仅含计数时无真实 link → 本调用返回 ``()``，不伪造真节点。
    cross_modal_nodes = _build_cross_modal_timeline_nodes(artifacts, scenario_id)
    for n in cross_modal_nodes:
        timeline_nodes.append(n)
        refs += (n["ref"],)

    return ScenarioEvidence(
        scenario_id=scenario_id,
        ok=ok,
        mode=mode,
        n_frames=n_frames,
        scenario_fingerprint=scenario_fingerprint,
        # P0-1：产品命题一句话（canonical 顶层投影；旧 artifact 无此键 → 空，向后兼容）。
        product_question=_str_field(canonical, "product_question", owner),
        counts=counts,
        event_types=_str_tuple(artifacts, "event_types", owner),
        risk_levels=_str_tuple(artifacts, "risk_levels", owner),
        recommended_actions=_str_tuple(artifacts, "recommended_actions", owner),
        command_types=_str_tuple(artifacts, "command_types", owner),
        # P1（干预回执 + 闭环可达性）：从真实 command_types 派生派发回执（VM-1 纯派生，
        # 不新增事实；VM-9 诚实边界：不含送达/时延/SLA——全仓库无该遥测）。
        intervention_dispatch=_build_intervention_dispatch(
            _str_tuple(artifacts, "command_types", owner)
        ),
        trace_outcome_kinds=_str_tuple(artifacts, "trace_outcome_kinds", owner),
        # P0-4：负向能力声明（canonical 顶层投影；旧 artifact 无此键 → 空元组，向后兼容）。
        # 与 product_question 同构，来自场景 meta 声明的诚实负向能力事实（非运行时抑制）。
        suppress_reasons=_str_tuple_field(canonical, "suppress_reasons", owner),
        episode_action_command_types=_str_tuple(
            artifacts, "episode_action_command_types", owner
        ),
        timeline=tuple(timeline_nodes),
        decision_evidence=decision_evidence,
        audio_evidence=audio_evidence,
        memory_episodes=memory_nodes,
        # P0-2：Case Time 主轴事件标记（音频轨 + 本次会话记忆，相对 T0；prior 不进）。
        case_time_tracks=_build_case_time_tracks(
            audio_evidence, memory_nodes, scenario_id
        ),
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
