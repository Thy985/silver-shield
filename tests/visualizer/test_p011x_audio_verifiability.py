"""P0-11.x Live Audio Verifiability 契约测试（R1-R10）。

验证 ``telephone_risk`` 的音频证据链从"工程上真实"推进到"产品上可验证"：
评委打开 /live 后能看到音频传感器存在、听到音频、点击时间点回放对应声音、
看到 Runtime 随之产生 Evidence。

覆盖项：
- R1：Live + 有 audio → <audio> 存在 + ACTIVE
- R2：Live + 无 audio → 不渲染空播放器
- R3：Artifact + 有 audio → 既有播放不回归
- R4：Artifact + 无 audio → 不显示空播放器
- R5：audio_evidence=[] → 不能伪造 ACTIVE
- R6：用户可听见（需 gateway e2e → skip）
- R7：声音和事件对应（case_time → seek 位置，manifest 数据岛 + clamp 逻辑）
- R8：刷新后仍可信（provenance details 4 行证据链来源）
- R9：无音频场景 → IDLE / hidden
- R10：Runtime 与 Source 一致（REAL_SENSOR evidence → sensor ACTIVE + provenance ACTIVE）

不依赖 torch/cv2（纯 stdlib + 投影契约 fixture），可在 torch-free 环境跑。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.artifact_source import load_case_presentation
from home_perception.visualizer.viewer.live_adapter import (
    ProjectionAccumulator,
    build_live_presentation,
)
from home_perception.visualizer.viewer.render import (
    _render_audio_sensor_status,
    _render_provenance_banner,
    _render_provenance_details,
)

from .conftest import make_artifacts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frame(frame_index: int, *, risk_levels=()) -> dict:
    return {
        "frame_index": frame_index,
        "n_detections": 1,
        "n_visitor_events": 0,
        "perception_events": [],
        "warnings": [
            {"risk_level": rl, "recommended_action": "MONITOR"} for rl in risk_levels
        ],
        "commands": [],
    }


def _audio_event(
    *,
    kind: str = "audio_telephone_persistent",
    timestamp: float = 1752952800.0,
    score: float = 0.9,
    confidence: float = 0.88,
    labels: tuple[str, ...] = ("telephone",),
    source_segment_ids: tuple[str, ...] = ("seg-0",),
    event_id: str | None = None,
) -> dict:
    d: dict = {
        "kind": kind,
        "timestamp": timestamp,
        "score": score,
        "confidence": confidence,
        "labels": list(labels),
        "source_segment_ids": list(source_segment_ids),
    }
    if event_id is not None:
        d["event_id"] = event_id
    return d


def _live_html_with_audio() -> str:
    """Live HTML：1 帧 + 1 条 REAL_SENSOR 音频感知 → render_case_viewer。"""
    acc = ProjectionAccumulator("p011x_t")
    acc.ingest(_frame(0, risk_levels=("HIGH",)))
    acc.ingest_audio(_audio_event())
    proj, desc = build_live_presentation(
        acc.to_evidence_projection(), live_ws_path="/ws"
    )
    return render_case_viewer(proj, desc, live_frame_stream=True)


def _live_html_no_audio() -> str:
    """Live HTML：1 帧、无音频感知 → render_case_viewer。"""
    acc = ProjectionAccumulator("p011x_no_audio")
    acc.ingest(_frame(0, risk_levels=("LOW",)))
    proj, desc = build_live_presentation(
        acc.to_evidence_projection(), live_ws_path="/ws"
    )
    return render_case_viewer(proj, desc, live_frame_stream=True)


_AUDIO_ARTIFACT = [
    {
        "audio_timestamp": 1752952800.0,
        "audio_kind": "audio_telephone_persistent",
        "audio_score": 0.9,
        "audio_confidence": 0.9,
        "audio_labels": ["telephone"],
        "audio_source_segment_ids": ["seg-0"],
    },
]


def _write_audio_manifest(artifacts_dir: Path, scenario_id: str) -> None:
    audio_dir = artifacts_dir / scenario_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "audio_telephone_persistent.wav").write_bytes(b"RIFFxxxx")
    manifest = {
        "source_kind": "AudioFileSource",
        "files": {
            "audio_telephone_persistent": f"{scenario_id}/audio/audio_telephone_persistent.wav",
        },
        "tracks": [
            {
                "id": "audio_telephone_persistent",
                "kind": "audio_telephone_persistent",
                "url": f"{scenario_id}/audio/audio_telephone_persistent.wav",
                "start_time": 0.0,
                "end_time": None,
                "provenance_kind": "SIMULATED",
            },
        ],
    }
    (audio_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def _artifact_html_with_audio(tmp_path) -> str:
    canon = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO_ARTIFACT)
    _write_audio_manifest(canon, "sw_t1")
    proj, desc = load_case_presentation(canon)
    return render_case_viewer(proj, desc, audio_base_dir=canon, audio_base_url="./")


def _artifact_html_no_audio(tmp_path) -> str:
    canon = make_artifacts(tmp_path / "b")
    proj, desc = load_case_presentation(canon)
    return render_case_viewer(proj, desc)


def _scenario_with_audio(
    *,
    provenance: str = "REAL_SENSOR",
    audio_provenance: str = "REAL_SENSOR",
    has_audio: bool = True,
) -> dict:
    """最小 ScenarioEvidence：timeline + 可选 audio_evidence。"""
    scenario: dict = {
        "scenario_id": "test_sid",
        "timeline": [
            {"provenance_kind": provenance, "timestamp": "F1", "ref": "r1"},
        ],
        "audio_evidence": [],
        "decision_evidence": [],
    }
    if has_audio:
        scenario["audio_evidence"] = [
            {
                "timestamp": "1752952800.0",
                "kind": "audio_telephone_persistent",
                "score": 0.9,
                "confidence": 0.88,
                "labels": ("telephone",),
                "source_segment_ids": ("seg-0",),
                "ref": "live://audio/0",
                "provenance_kind": audio_provenance,
            },
        ]
    return scenario


# ---------------------------------------------------------------------------
# R1: Live + 有 audio → <audio> 存在 + ACTIVE
# ---------------------------------------------------------------------------


def test_r1_live_with_audio_has_audio_element_and_active():
    """R1：Live 模式 + 有 REAL_SENSOR 音频 → <audio> 控件存在 + AUDIO SENSOR ACTIVE。"""
    html = _live_html_with_audio()
    assert "<audio" in html, "Live 模式应渲染 <audio> 播放控件"
    assert "AUDIO SENSOR" in html, "应渲染 AUDIO SENSOR 卡"
    assert "ACTIVE" in html, "REAL_SENSOR audio_evidence → ACTIVE"
    assert "audio-active" in html, "ACTIVE 状态 CSS class"


# ---------------------------------------------------------------------------
# R2: Live + 无 audio → 不渲染空播放器
# ---------------------------------------------------------------------------


def test_r2_live_no_audio_no_empty_player():
    """R2：Live 模式 + 无音频 → 不渲染空 <audio> 播放器。"""
    html = _live_html_no_audio()
    assert "AUDIO SENSOR" in html, "AUDIO SENSOR 卡仍渲染（IDLE 态）"
    assert "IDLE" in html, "无音频 → IDLE"
    assert "audio-idle" in html, "IDLE 状态 CSS class"


# ---------------------------------------------------------------------------
# R3: Artifact + 有 audio → 既有播放不回归
# ---------------------------------------------------------------------------


def test_r3_artifact_with_audio_no_regression(tmp_path):
    """R3：Artifact 模式 + 有音频 → <audio> 控件 + data-kind 不回归。"""
    html = _artifact_html_with_audio(tmp_path)
    assert "<audio" in html, "Artifact 模式应渲染 <audio>"
    assert 'data-kind="audio_telephone_persistent"' in html, "data-kind 不回归"
    assert "__AudioSync" in html, "AudioSync 引擎注入不回归"


# ---------------------------------------------------------------------------
# R4: Artifact + 无 audio → 不显示空播放器
# ---------------------------------------------------------------------------


def test_r4_artifact_no_audio_no_empty_player(tmp_path):
    """R4：Artifact 模式 + 无音频 → 不显示空 <audio> 播放器。"""
    html = _artifact_html_no_audio(tmp_path)
    assert "<audio" not in html, "无音频 → 不渲染 <audio>"
    assert "__AudioSync" not in html, "无音频 → 不注入 AudioSync"


# ---------------------------------------------------------------------------
# R5: audio_evidence=[] → 不能伪造 ACTIVE
# ---------------------------------------------------------------------------


def test_r5_empty_audio_evidence_no_fake_active():
    """R5：audio_evidence=[] → IDLE，绝不伪造 ACTIVE。"""
    scenario = _scenario_with_audio(has_audio=False)
    html = _render_audio_sensor_status(scenario)
    assert "IDLE" in html
    assert 'data-status="idle"' in html
    assert "audio-idle" in html
    assert 'data-status="active"' not in html
    assert "audio-active" not in html


def test_r5_simulated_audio_evidence_not_active():
    """R5b：audio_evidence 仅含 SIMULATED → IDLE（ACTIVE 仅 REAL_SENSOR）。"""
    scenario = _scenario_with_audio(audio_provenance="SIMULATED")
    html = _render_audio_sensor_status(scenario)
    assert "IDLE" in html
    assert 'data-status="idle"' in html
    assert 'data-status="active"' not in html


# ---------------------------------------------------------------------------
# R6: 用户可听见（需 gateway e2e）
# ---------------------------------------------------------------------------


def test_r6_user_can_hear_audio():
    """R6：用户可听见音频（需 gateway e2e 验证 <audio> 可播放）。"""
    pytest.skip("R6 需 gateway e2e（Playwright + 音频设备），单元测试不覆盖")


# ---------------------------------------------------------------------------
# R7: 声音和事件对应（case_time → seek 位置）
# ---------------------------------------------------------------------------


def test_r7_manifest_data_island_injected():
    """R7a：Live 模式注入 manifest 数据岛 <script type=application/json id=audio-manifest-*>。"""
    html = _live_html_with_audio()
    assert 'type="application/json"' in html, "manifest 数据岛 type"
    assert "audio-manifest-" in html, "manifest 数据岛 id 前缀"


def test_r7_audio_sync_js_has_clamp_logic():
    """R7b：audio_sync.js 含双边 clamp 公式 max(0, min(..., duration))。"""
    js_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "home_perception"
        / "visualizer"
        / "assets"
        / "audio_sync.js"
    )
    js = js_path.read_text(encoding="utf-8")
    assert "Math.max(0" in js, "clamp 下界"
    assert "Math.min(" in js, "clamp 上界"
    assert "duration" in js, "clamp 使用 duration"
    assert "loadedmetadata" in js, "loadedmetadata 前置条件"
    assert "readyState" in js, "readyState 检查"


def test_r7_timeline_audio_node_has_case_time():
    """R7c：Live timeline AUDIO 节点含 case_time 字段（seek 锚点）。"""
    acc = ProjectionAccumulator("p011x_r7c")
    acc.ingest(_frame(0))
    acc.ingest_audio(_audio_event(timestamp=1752952800.0))
    proj, desc = build_live_presentation(acc.to_evidence_projection())
    audio_nodes = [
        n for s in proj["scenarios"] for n in s["timeline"]
        if n.get("modality") == "AUDIO"
    ]
    assert len(audio_nodes) > 0, "应有 AUDIO timeline 节点"
    for node in audio_nodes:
        assert "case_time" in node, f"AUDIO 节点缺 case_time: {node}"
        assert isinstance(node["case_time"], float), "case_time 应为 float"


# ---------------------------------------------------------------------------
# R8: 刷新后仍可信（provenance details 4 行证据链来源）
# ---------------------------------------------------------------------------


def test_r8_provenance_details_has_four_rows():
    """R8：provenance details 展开 → 4 行（视频源/音频源/感知事件/决策证据）。"""
    scenario = _scenario_with_audio()
    html = _render_provenance_details(scenario)
    assert "证据链来源" in html
    assert "视频源" in html
    assert "音频源" in html
    assert "感知事件" in html
    assert "决策证据" in html


def test_r8_provenance_details_in_banner():
    """R8b：provenance banner 内嵌 <details>。"""
    scenario = _scenario_with_audio()
    html = _render_provenance_banner(scenario)
    assert "<details" in html
    assert "证据链来源" in html


def test_r8_provenance_details_active_with_real_sensor():
    """R8c：REAL_SENSOR timeline + audio_evidence → 感知事件 ACTIVE。"""
    scenario = _scenario_with_audio(provenance="REAL_SENSOR", audio_provenance="REAL_SENSOR")
    html = _render_provenance_details(scenario)
    assert "ACTIVE" in html
    assert "prov-detail-active" in html
    assert "Runtime 已产出" in html


def test_r8_provenance_details_idle_without_real_sensor():
    """R8d：SIMULATED timeline → 感知事件 IDLE。"""
    scenario = _scenario_with_audio(provenance="SIMULATED", audio_provenance="SIMULATED")
    html = _render_provenance_details(scenario)
    assert "IDLE" in html
    assert "prov-detail-idle" in html


def test_r8_provenance_details_no_audio_explicit():
    """R8e：无音频 → 音频源行显式标注"无音频证据"。"""
    scenario = _scenario_with_audio(has_audio=False)
    html = _render_provenance_details(scenario)
    assert "无音频证据" in html


# ---------------------------------------------------------------------------
# R9: 无音频场景 → IDLE / hidden
# ---------------------------------------------------------------------------


def test_r9_no_audio_sensor_idle():
    """R9：无音频 → AUDIO SENSOR 卡 IDLE 态。"""
    scenario = _scenario_with_audio(has_audio=False)
    html = _render_audio_sensor_status(scenario)
    assert "IDLE" in html
    assert "audio-idle" in html
    assert 'data-status="idle"' in html


def test_r9_no_audio_kinds_dash():
    """R9b：无音频 → Kinds detected 显示 —（不编造）。"""
    scenario = _scenario_with_audio(has_audio=False)
    html = _render_audio_sensor_status(scenario)
    assert "—" in html, "无音频 kinds → —"


# ---------------------------------------------------------------------------
# R10: Runtime 与 Source 一致
# ---------------------------------------------------------------------------


def test_r10_real_sensor_evidence_sensor_active():
    """R10a：REAL_SENSOR audio_evidence → AUDIO SENSOR ACTIVE。"""
    scenario = _scenario_with_audio(audio_provenance="REAL_SENSOR")
    html = _render_audio_sensor_status(scenario)
    assert "ACTIVE" in html
    assert "audio-active" in html


def test_r10_real_sensor_evidence_provenance_active():
    """R10b：REAL_SENSOR timeline → provenance details 感知事件 ACTIVE。"""
    scenario = _scenario_with_audio(provenance="REAL_SENSOR")
    html = _render_provenance_details(scenario)
    assert "prov-detail-active" in html


def test_r10_simulated_evidence_consistent_idle():
    """R10c：SIMULATED audio_evidence → SENSOR IDLE + provenance 感知 IDLE（一致）。"""
    scenario = _scenario_with_audio(
        provenance="SIMULATED", audio_provenance="SIMULATED"
    )
    sensor_html = _render_audio_sensor_status(scenario)
    details_html = _render_provenance_details(scenario)
    assert "IDLE" in sensor_html
    assert 'data-status="idle"' in sensor_html
    assert "IDLE" in details_html
    assert 'data-status="active"' not in sensor_html
    assert "prov-detail-idle" in details_html


def test_r10_live_html_sensor_and_provenance_consistent():
    """R10d：Live HTML 中 AUDIO SENSOR ACTIVE ↔ provenance details 感知事件 ACTIVE。"""
    html = _live_html_with_audio()
    sensor_active = "audio-active" in html
    provenance_active = "prov-detail-active" in html
    assert sensor_active == provenance_active, (
        f"SENSOR active={sensor_active} != provenance active={provenance_active}"
    )
    assert sensor_active, "Live + REAL_SENSOR audio → 两处都应 ACTIVE"