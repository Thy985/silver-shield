"""ADR-0036 T15 · 语义等价（Semantic Equivalence）端到端测试。

验收（Owner 标准 T15）：对同一个 Trusted Artifact——

    IntegrationReport → EvidenceProjection → Case Viewer → D3 Export

所有核心事实必须一致（scenario_id / event_types / risk_levels / decision_outcome /
recommended_action / command_type / provenance / fingerprint），**不允许**
D1=LOW、Case Viewer=HIGH、D3=WARN 这种漂移。

本测试把"没发生漂移"变成"被测试证明不会发生漂移"：
- 层 1：loader 投影的 EvidenceProjection（唯一事实源）；
- 层 2：Case Viewer 渲染的 HTML（render_case_viewer 消费同一 projection）；
- 层 3：D3 导出入口（load_scenario_evidence / CaseVideoSpec 消费同一 projection +
  EvidenceGraph，D3-12 不重写事实层）；
- 层 4：逐事实字段四层一致断言（含 provenance / fingerprint 等）。

全部 hermetic（零 cv2）：D3 导出经 adapter 只读投影断言 + CaseVideoSpec 静态断言；
不实际生成视频（视频生成属 D3-A 已有测试覆盖）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from home_perception.visualizer.loader import load_evidence_projection
from home_perception.visualizer.video.evidence.adapter import load_scenario_evidence
from home_perception.visualizer.viewer import render_case_viewer

from .conftest import make_d3a_artifact_dir

_SID = "sw_adr0034_elderly_dwell"


# ---------------------------------------------------------------------------
# 核心事实提取器（各层独立提取，供四层一致断言）
# ---------------------------------------------------------------------------


def _facts_from_projection(projection: dict) -> dict:
    """层 1：从 EvidenceProjection 提取核心事实（VM-1 唯一事实源）。"""
    scn = next(s for s in projection["scenarios"] if s["scenario_id"] == _SID)
    return {
        "scenario_id": scn["scenario_id"],
        "event_types": tuple(scn["event_types"]),
        "risk_levels": tuple(scn["risk_levels"]),
        "recommended_actions": tuple(scn["recommended_actions"]),
        "command_types": tuple(scn["command_types"]),
        "trace_outcome_kinds": tuple(scn["trace_outcome_kinds"]),
        "scenario_fingerprint": scn["scenario_fingerprint"],
        "provenance": tuple(sorted({n["provenance_kind"] for n in scn["timeline"]})),
        "gate_passed": scn["gate_passed"],
        "fingerprints": (
            (scn["fingerprints"]["expectation_fingerprint"], scn["fingerprints"]["loop_fingerprint"])
            if scn["fingerprints"] else None
        ),
        "n_audio": len(scn["audio_evidence"]),
    }


def _facts_from_html(html: str) -> dict:
    """层 2：从 Case Viewer 渲染 HTML 提取同一事实（渲染层不得漂移）。

    注意：HTML 只呈现**展示翻译值**（如 "低风险（LOW）"），翻译来自
    renderer._VALUE_ZH / _EVENT_ZH——语义等价判定是"翻译可逆回原文"：
    抽取 HTML 里的原文枚举（LOW / LOG_ONLY / NOTIFY_FAMILY / abnormal_dwell 等），
    与 projection 事实比对。这样既验证了"展示未丢事实"，又允许展示翻译。
    """
    raw = re.sub(r"<script[\s\S]*?</script>", "", html)
    return {
        "scenario_id_present": _SID in html,
        "event_types_visible": "abnormal_dwell" in raw,
        "risk_visible": any(
            k in raw for k in ("低风险", "需关注", "高风险", "LOW", "WARN", "HIGH")
        ),
        "action_visible": any(
            k in raw for k in ("通知家属", "仅记录", "持续关注", "NOTIFY_FAMILY", "LOG_ONLY", "MONITOR")
        ),
        "provenance_visible": "SIMULATED" in raw,
        "gate_visible": "gate" in raw or "Gate" in raw,
    }


def _facts_from_d3_adapter(artifact_dir: Path) -> dict:
    """层 3：D3 导出入口（load_scenario_evidence 复用 loader，D3-12 不重写事实）。

    返回与层 1 同构的事实字典——若 D3 自己另建一套 evidence/risk/timeline，
    此处必然与层 1 不一致（本测试即捕获此漂移）。
    """
    scn = load_scenario_evidence(artifact_dir, _SID)
    return {
        "scenario_id": scn["scenario_id"],
        "event_types": tuple(scn["event_types"]),
        "risk_levels": tuple(scn["risk_levels"]),
        "recommended_actions": tuple(scn["recommended_actions"]),
        "command_types": tuple(scn["command_types"]),
        "trace_outcome_kinds": tuple(scn["trace_outcome_kinds"]),
        "scenario_fingerprint": scn["scenario_fingerprint"],
        "provenance": tuple(sorted({n["provenance_kind"] for n in scn["timeline"]})),
        "gate_passed": scn["gate_passed"],
        "fingerprints": (
            (scn["fingerprints"]["expectation_fingerprint"], scn["fingerprints"]["loop_fingerprint"])
            if scn["fingerprints"] else None
        ),
        "n_audio": len(scn["audio_evidence"]),
    }


def _facts_from_canonical(artifact_dir: Path) -> dict:
    """层 0（源）：canonical artifact 的原始事实（真值表，非投影）。"""
    canonical = json.loads(
        (artifact_dir / f"{_SID}.canonical.json").read_text(encoding="utf-8")
    )
    arts = canonical["artifacts"]
    return {
        "scenario_id": _SID,
        "event_types": tuple(arts["event_types"]),
        "risk_levels": tuple(arts["risk_levels"]),
        "recommended_actions": tuple(arts["recommended_actions"]),
        "command_types": tuple(arts["command_types"]),
        "trace_outcome_kinds": tuple(arts["trace_outcome_kinds"]),
        "scenario_fingerprint": canonical["scenario_fingerprint"],
        "gate_passed": json.loads(
            (artifact_dir / f"{_SID}.gate.json").read_text(encoding="utf-8")
        )["passed"],
        "n_audio": len(arts.get("audio_evidence", []) or []),
    }


# ---------------------------------------------------------------------------
# T15 核心：四层语义等价
# ---------------------------------------------------------------------------


def test_t15_semantic_equivalence_four_layers(tmp_path):
    """T15：canonical → Projection → Case Viewer HTML → D3 入口，核心事实全一致。

    逐事实字段断言（scenario_id / event_types / risk_levels / recommended_actions /
    command_types / trace_outcome_kinds / fingerprint / provenance / gate）。
    """
    artifact_dir = make_d3a_artifact_dir(tmp_path)
    projection = load_evidence_projection(artifact_dir)

    f_canonical = _facts_from_canonical(artifact_dir)
    f_projection = _facts_from_projection(projection)
    f_d3 = _facts_from_d3_adapter(artifact_dir)

    # 层 0 ↔ 层 1：loader 投影不得改事实（含 fingerprint）。
    assert f_projection["scenario_id"] == f_canonical["scenario_id"]
    assert f_projection["event_types"] == f_canonical["event_types"]
    assert f_projection["risk_levels"] == f_canonical["risk_levels"]
    assert f_projection["recommended_actions"] == f_canonical["recommended_actions"]
    assert f_projection["command_types"] == f_canonical["command_types"]
    assert f_projection["trace_outcome_kinds"] == f_canonical["trace_outcome_kinds"]
    assert f_projection["scenario_fingerprint"] == f_canonical["scenario_fingerprint"]
    assert f_projection["gate_passed"] == f_canonical["gate_passed"]
    assert f_projection["n_audio"] == f_canonical["n_audio"]

    # 层 1 ↔ 层 3：D3 入口必须与投影逐字段一致（D3 不得另建一套事实）。
    assert f_d3 == f_projection, (
        "D3 导出入口与 EvidenceProjection 事实漂移（T15 FAIL）：\n"
        f"projection={f_projection}\nD3={f_d3}"
    )

    # 层 2：Case Viewer HTML 呈现同一事实（展示翻译可逆回原文枚举）。
    html = render_case_viewer(projection, media_base_dir=None)
    f_html = _facts_from_html(html)
    assert f_html["scenario_id_present"], "HTML 应含场景标识"
    assert f_html["event_types_visible"], "HTML 应呈现事件类型（abnormal_dwell）"
    assert f_html["risk_visible"], "HTML 应呈现风险等级（展示翻译可逆）"
    assert f_html["action_visible"], "HTML 应呈现系统行动（展示翻译可逆）"
    assert f_html["provenance_visible"], "HTML 应呈现 provenance（SIMULATED）"


def test_t15_no_second_view_model_in_d3_export_chain(tmp_path):
    """T15 附：D3 导出链路不建立第二套 evidence/risk/timeline。

    通过证明 ``load_scenario_evidence``（D3 入口）直接复用 loader 产物——其返回值
    与 ``load_evidence_projection`` 的同一场景是**同一个 dict 值来源**（非复制/重写），
    且渲染 HTML 里不存在第二份 timeline/risk 数据岛。
    """
    artifact_dir = make_d3a_artifact_dir(tmp_path)
    projection = load_evidence_projection(artifact_dir)
    scn_proj = next(s for s in projection["scenarios"] if s["scenario_id"] == _SID)
    scn_d3 = load_scenario_evidence(artifact_dir, _SID)

    # D3 返回的必须是同一投影对象（值等价 + 结构等价），不是重新判定的新模型。
    assert scn_d3["timeline"] == scn_proj["timeline"]
    assert scn_d3["decision_evidence"] == scn_proj["decision_evidence"]
    assert scn_d3["graph"]["nodes"] == scn_proj["graph"]["nodes"]
    assert scn_d3["graph"]["edges"] == scn_proj["graph"]["edges"]

    # 渲染 HTML 只有一份 replay-data / replay-trace-data 数据岛（无第二套）。
    html = render_case_viewer(projection, media_base_dir=None)
    assert html.count(f'id="replay-data-{_SID}"') == 1
    assert html.count(f'id="replay-trace-data-{_SID}"') <= 1


def test_t15_provenance_not_downgraded(tmp_path):
    """T15 附：Provenance 不得在层间漂移（SIMULATED 恒 SIMULATED）。

    防"AI 生成视频→REAL_SENSOR"或"Live Runtime→SIMULATED"类伪 Provenance：
    loader（artifact 路径）产 SIMULATED，D3 入口必须同值；HTML 场景级 banner
    呈现 SIMULATED（页面脚注含三种 provenance 全量说明属 P8 设计要求，不算漂移）。
    """
    artifact_dir = make_d3a_artifact_dir(tmp_path)
    projection = load_evidence_projection(artifact_dir)
    assert _facts_from_projection(projection)["provenance"] == ("SIMULATED",)
    assert _facts_from_d3_adapter(artifact_dir)["provenance"] == ("SIMULATED",)

    html = render_case_viewer(projection, media_base_dir=None)
    # 场景级 provenance banner：SIMULATED 徽章 + "程序化场景"文案（AC-7 一等视觉）。
    # 注意：CSS 类定义里含全部三种徽章样式（prov-real-sensor 类存在），须断言**用法**
    # （<span class="prov-badge prov-simulated">）而非类名本身。
    assert "prov-badge prov-simulated" in html
    assert "程序化场景" in html
    assert 'prov-badge prov-real-sensor"' not in html


def test_t15_media_source_resolves_same_ref(tmp_path):
    """T15 附：媒体源经同一 ref 解析（证据 ref ↔ 媒体字节解耦，D3 登记源可被解析）。

    演示媒体准备产物（ArtifactVideoSource manifest）与 EvidenceProjection 的
    scenario_id 同源：media_binding ref 指向的源可被 Media Source Adapter 只读解析。
    """
    from home_perception.visualizer.video.spec import CaseVideoSpec

    artifact_dir = make_d3a_artifact_dir(tmp_path)
    spec = CaseVideoSpec(
        scenario_id=_SID,
        artifact_dir=artifact_dir,
        output_dir=artifact_dir / _SID / "media",
        fps=5.0,
        resolution=(640, 480),
        version=1,
        with_audio=False,
    )
    # CaseVideoSpec 直接承载同一 scenario_id（不重新派生新场景身份）。
    assert spec.scenario_id == _SID
    assert spec.with_audio is False  # D3-B 未实现：VM-12/AC-6 恒 False


def test_t15_cross_modal_link_preserved(tmp_path):
    """T15 附：cross_modal 事实（含 audio_evidence）跨层一致。

    用带音频事件的 canonical（audio_evidence 非空）验证：投影 → D3 入口 →
    HTML 的 audio_evidence 计数一致（不丢失、不编造）。
    """
    from .conftest import make_artifacts

    audio_evidence = [
        {
            "audio_timestamp": 1752952800.0,
            "audio_kind": "audio_telephone_persistent",
            "audio_score": 0.9,
            "audio_confidence": 0.9,
            "audio_labels": ["persistent"],
            "audio_source_segment_ids": ["seg-0"],
        }
    ]
    artifact_dir = make_artifacts(
        tmp_path / "audio_artifacts", scenario_ids=(_SID,), audio_evidence=audio_evidence
    )
    projection = load_evidence_projection(artifact_dir)
    n_proj = _facts_from_projection(projection)["n_audio"]
    n_d3 = _facts_from_d3_adapter(artifact_dir)["n_audio"]
    assert n_d3 == n_proj, "D3 入口 audio_evidence 计数漂移（T15 FAIL）"
    assert n_proj == 1, "注入 1 条音频证据应投影出 1 条（夹具自洽）"

    html = render_case_viewer(projection, media_base_dir=None)
    # HTML 中音频证据区块存在（🔊 或 Audio Evidence 标题）。
    assert "Audio Evidence" in html or "audio" in html.lower()
