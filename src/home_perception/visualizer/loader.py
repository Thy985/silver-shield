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
    TimelineNode,
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

    event_types = artifacts.get("event_types")
    if isinstance(event_types, list) and event_types:
        _add("evidence", "检测证据（事件类型）", ", ".join(event_types), "artifacts.event_types")
    trace_kinds = artifacts.get("trace_outcome_kinds")
    if isinstance(trace_kinds, list) and trace_kinds:
        _add("evidence", "决策结果（trace outcome）", ", ".join(trace_kinds), "artifacts.trace_outcome_kinds")
    risk_levels = artifacts.get("risk_levels")
    if isinstance(risk_levels, list) and risk_levels:
        _add("outcome", "风险级别", ", ".join(risk_levels), "artifacts.risk_levels")
    actions = artifacts.get("recommended_actions")
    if isinstance(actions, list) and actions:
        _add("action", "推荐动作", ", ".join(actions), "artifacts.recommended_actions")
    commands = artifacts.get("command_types")
    if isinstance(commands, list) and commands:
        _add("action", "已执行命令", ", ".join(commands), "artifacts.command_types")
    suppress = artifacts.get("suppress_reasons")
    if isinstance(suppress, list) and suppress:
        _add("outcome", "抑制原因", ", ".join(suppress), "artifacts.suppress_reasons")
    if not evidence:
        # 无任何决策证据字段（benign 空闭环）→ 降级摘要，非捏造
        _add("outcome", "决策证据", "(闭环无事件/警告——benign 场景预期)", "artifacts.counts")
    return tuple(evidence)


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
        verdicts.append(
            StageVerdict(
                name=name,
                passed=passed,
                severity=severity,
                failure_code=v.get("failure_code"),
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
        gate=verdicts,
        gate_passed=gate_passed,
        gate_degraded=gate_degraded,
        fingerprints=_build_fingerprints(scenario_id, fingerprints, owner),
        refs=refs,
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
