"""ADR-0036 VM-13 Phase C · 报告层音频投影单元测试（不依赖 cv2 运行时）。

验证 ``LoopArtifactSummary.from_run`` 将真实音频符号投影为 ``audio_*`` 前缀键字典，并经
``assert_desensitized`` 通过（脱敏守卫对 ``"score"`` 是精确匹配禁止键，故必须用
``audio_score`` 前缀键规避，否则落盘 fail-closed）。同时验证 ``IntegrationReport.canonical_dict``
自然携带 ``audio_evidence`` 且写盘守卫放行。

本文件刻意用鸭子类型 ``SimpleNamespace`` 伪造 ``IntegrationRunResult``，避免触发
``runner._assemble`` 的 cv2 运行时重链（report 模块本身 stdlib-friendly，import 不拉 cv2）。
"""

from __future__ import annotations

from types import SimpleNamespace

from home_perception.analysis.decision_sink import assert_desensitized
from home_perception.integration.loop.report import (
    IntegrationReport,
    LoopArtifactSummary,
)

_AUDIO = {
    "audio_timestamp": 1752952800.0,
    "audio_kind": "audio_telephone_persistent",
    "audio_score": 0.9,
    "audio_confidence": 0.9,
    "audio_labels": ["telephone"],
    "audio_source_segment_ids": ["seg-0"],
}


class _FakeAudio:
    """鸭子类型伪造 AudioPerceptionEvent（只暴露 from_run 读取的属性）。"""

    def __init__(self, ts, kind, score, conf, labels, segs):
        self.timestamp = ts
        self.kind = SimpleNamespace(value=kind)
        self.score = score
        self.confidence = conf
        self.labels = labels
        self.source_segment_ids = segs


def _fake_result(*, audio=()):
    return SimpleNamespace(
        perception_events=(),
        warnings=(),
        commands=(),
        sink_commands=(),
        decision_traces=(),
        episodes=(),
        cross_modal_links=(),
        audio_perception_events=tuple(audio),
    )


def test_loop_summary_projects_audio_events():
    """真实音频符号 → audio_* 前缀键字典，字段严格对齐 AudioEvidenceNode。"""
    result = _fake_result(
        audio=[
            _FakeAudio(
                1752952800.0, "audio_telephone_persistent", 0.9, 0.9,
                ["telephone"], ["seg-0"],
            )
        ]
    )
    summary = LoopArtifactSummary.from_run(result)
    assert len(summary.audio_events) == 1
    assert summary.audio_events[0] == _AUDIO


def test_loop_summary_audio_absent_when_no_audio():
    """未声明音频场景 → audio_events 恒 ()（AC-12 绝不编造）。"""
    summary = LoopArtifactSummary.from_run(_fake_result())
    assert summary.audio_events == ()


def test_loop_summary_audio_passes_desensitization():
    """audio_* 前缀键通过脱敏守卫（不放行裸 "score"，assert_desensitized 不抛）。"""
    result = _fake_result(
        audio=[
            _FakeAudio(
                1752952800.0, "audio_telephone_persistent", 0.9, 0.9,
                ["telephone"], ["seg-0"],
            )
        ]
    )
    summary = LoopArtifactSummary.from_run(result)
    payload = {
        "scenario_id": "sw_t1",
        "ok": True,
        "mode": "frames",
        "n_frames": 10,
        "scenario_fingerprint": "fp",
        "stages": [],
        "artifacts": summary.to_dict(),
    }
    assert_desensitized(payload)  # 不抛 = 通过


def test_integration_report_canonical_includes_audio_evidence():
    """IntegrationReport.canonical_dict 经 audio_evidence 自然包含，且写盘守卫通过。"""
    result = _fake_result(
        audio=[
            _FakeAudio(
                1752952800.0, "audio_telephone_persistent", 0.9, 0.9,
                ["telephone"], ["seg-0"],
            )
        ]
    )
    report = IntegrationReport(
        scenario_id="sw_t1",
        ok=True,
        mode="frames",
        n_frames=10,
        scenario_fingerprint="fp",
        artifacts=LoopArtifactSummary.from_run(result),
    )
    cd = report.canonical_dict()
    assert cd["artifacts"]["audio_evidence"] == [_AUDIO]
    # 落盘守卫（与 write_canonical_report 同款）必须放行
    assert_desensitized(cd)
