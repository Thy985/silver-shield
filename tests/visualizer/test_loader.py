"""ADR-0035 D2 · 投影契约测试：fail-closed / ref / provenance_kind / 禁 synthetic / 脱敏。

对应验收：6（Evidence 完整性）、7（Evidence provenance）、10（Projection compatibility）
+ D2 硬规则 1–4。
"""

from __future__ import annotations

import pytest

from home_perception.visualizer import EvidenceProjectionError, load_evidence_projection
from home_perception.visualizer.loader import SUMMARY_FILENAME

from .conftest import make_artifacts


def test_projection_success_shape(artifacts_dir):
    """合法 artifact 集 → 完整投影（meta + 场景 + 四视图输入齐全）。"""
    proj = load_evidence_projection(artifacts_dir)
    assert proj["meta"]["scenario_count"] == 1
    assert proj["meta"]["generated_at"] == "2026-08-12T00:00:00+00:00"
    scn = proj["scenarios"][0]
    assert scn["scenario_id"] == "sw_t1"
    assert scn["ok"] is True
    assert scn["gate_passed"] is True and scn["gate_degraded"] is False
    assert scn["fingerprints"]["expectation_fingerprint"] == "e" * 64
    assert scn["fingerprints"]["loop_fingerprint"] == "f" * 64
    # 6 个 stage 判定节点 + 5 个 count 节点（perception/decision/notification/memory/cross_modal）
    assert len(scn["timeline"]) == 6 + 5
    assert len(scn["gate"]) == 6


def test_projection_fail_closed_missing_gate_file(tmp_path):
    """缺 gate.json → fail-closed 抛错（绝不产空白投影，验收 10）。"""
    d = make_artifacts(tmp_path / "a", drop_file="sw_t1.gate.json")
    with pytest.raises(EvidenceProjectionError, match="gate.json"):
        load_evidence_projection(d)


def test_projection_fail_closed_missing_fingerprints_file(tmp_path):
    """缺 fingerprints.json → fail-closed（验收 10）。"""
    d = make_artifacts(tmp_path / "a", drop_file="sw_t1.fingerprints.json")
    with pytest.raises(EvidenceProjectionError, match="fingerprints"):
        load_evidence_projection(d)


def test_projection_fail_closed_missing_summary(tmp_path):
    """缺 summary.json → fail-closed（验收 10）。"""
    d = make_artifacts(tmp_path / "a", drop_file=SUMMARY_FILENAME)
    with pytest.raises(EvidenceProjectionError, match="summary"):
        load_evidence_projection(d)


def test_projection_fail_closed_dropped_stages_field(tmp_path):
    """canonical 删 stages（schema 演化模拟）→ fail-closed（验收 10 / D2b）。"""
    d = make_artifacts(tmp_path / "a", drop_field=("sw_t1", "stages"))
    with pytest.raises(EvidenceProjectionError, match="stages"):
        load_evidence_projection(d)


def test_projection_fail_closed_dropped_loop_fingerprint(tmp_path):
    """fingerprints 缺 loop_fingerprint → fail-closed（验收 10 / D2b）。"""
    d = make_artifacts(tmp_path / "a")
    import json

    fp = d / "sw_t1.fingerprints.json"
    data = json.loads(fp.read_text(encoding="utf-8"))
    del data["loop_fingerprint"]
    fp.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvidenceProjectionError, match="loop_fingerprint"):
        load_evidence_projection(d)


def test_every_node_has_ref(artifacts_dir):
    """Evidence provenance：所有节点 ref 非空且指向 canonical artifact（验收 7）。"""
    scn = load_evidence_projection(artifacts_dir)["scenarios"][0]
    assert scn["refs"], "必须有节点 ref"
    for ref in scn["refs"]:
        assert ref.startswith("sw_t1.canonical.json#"), f"ref 必须溯源到 canonical：{ref}"
    for node in scn["timeline"]:
        assert node["ref"].startswith("sw_t1.canonical.json#"), node["ref"]
    for item in scn["decision_evidence"]:
        assert item["ref"].startswith("sw_t1.canonical.json#"), item["ref"]


def test_every_node_has_provenance_kind(artifacts_dir):
    """provenance_kind 必填：D1 全部 SIMULATED（仿真闭环 artifact，D2 硬规则 4）。"""
    scn = load_evidence_projection(artifacts_dir)["scenarios"][0]
    for node in scn["timeline"]:
        assert node["provenance_kind"] == "SIMULATED", node
    assert all(n["provenance_kind"] in ("REAL_SENSOR", "SIMULATED", "FIXTURE")
               for n in scn["timeline"])


def test_projection_fail_closed_malformed_json(tmp_path):
    """坏 JSON（json.loads 异常路径）→ fail-closed（评审 #11）。"""
    d = make_artifacts(tmp_path / "a")
    (d / "sw_t1.canonical.json").write_text("{not-valid-json", encoding="utf-8")
    with pytest.raises(EvidenceProjectionError, match="解析失败"):
        load_evidence_projection(d)


def test_projection_unicode_scenario_id(tmp_path):
    """unicode / 非 ASCII scenario_id 正常投影（脱敏场景，评审 #11）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_场景_α",))
    scn = load_evidence_projection(d)["scenarios"][0]
    assert scn["scenario_id"] == "sw_场景_α"
    assert scn["timeline"]  # 时间轴照常投影


def test_projection_provenance_kind_closure(artifacts_dir):
    """provenance_kind 闭集：loader 产出值 ∈ {REAL_SENSOR, SIMULATED, FIXTURE}（评审 #11）。

    当前 D1 数据源恒为仿真闭环 → SIMULATED；REAL_SENSOR/FIXTURE 为 schema 声明
    的合法值（未来真实设备/夹具接入时由 loader 填充），渲染层按 Literal 接受。
    """
    from home_perception.visualizer.schema.evidence import ProvenanceKind

    scn = load_evidence_projection(artifacts_dir)["scenarios"][0]
    kinds = {n["provenance_kind"] for n in scn["timeline"]}
    assert kinds <= set(ProvenanceKind.__args__), kinds
    assert kinds == {"SIMULATED"}


def test_projection_fail_closed_negative_frames(tmp_path):
    """n_frames 负数（语义约束）→ fail-closed（评审 #12）。"""
    d = make_artifacts(tmp_path / "a")
    import json

    canon = d / "sw_t1.canonical.json"
    data = json.loads(canon.read_text(encoding="utf-8"))
    data["n_frames"] = -1
    canon.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvidenceProjectionError, match="n_frames"):
        load_evidence_projection(d)


def test_no_synthetic_nodes(artifacts_dir):
    """Evidence 完整性：节点数与 artifact 真实字段一一对应，禁止人为追加（验收 6）。

    变异验证：若 loader 被改成"额外 append 一个 Frame 节点"，stage 计数断言必红。
    """
    scn = load_evidence_projection(artifacts_dir)["scenarios"][0]
    stage_nodes = [n for n in scn["timeline"] if n["type"] == "stage"]
    assert len(stage_nodes) == 6, "stage 节点数必须等于 canonical.stages 长度（禁 synthetic）"
    count_nodes = [n for n in scn["timeline"] if n["type"] == "count"]
    # canonical counts 只有 5 个 stage 关联计数键被投影（cross_modal=1 也投影）
    assert len(count_nodes) == 5
    # 所有节点 type ∈ {stage, count}（没有凭空捏造的 frame/detection 节点）
    assert {n["type"] for n in scn["timeline"]} <= {"stage", "count"}


def test_projection_desensitized(artifacts_dir):
    """脱敏：投影不含路径类字段（scenarios_dir / canonical_report / .yaml），D7。"""
    proj = load_evidence_projection(artifacts_dir)
    assert "scenarios_dir" not in proj["meta"]
    text = str(proj)
    assert "canonical_report" not in text
    assert ".yaml" not in text
    assert "D:" not in text and "C:" not in text
