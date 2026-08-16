"""音频 E2E（P0）验收测试：Case Viewer 首屏真实消费音频证据。

验收标准（用户原话）：CI 产出的多模态 Trusted Artifact，Case Viewer 能真实消费——
"而不是 JSON 中存在、UI 中看不到"。

覆盖：
- 首屏「系统听到了什么」面板可见 + 人话化（中文类别 + 相对时间 + score/confidence）；
- 独立 Audio Source 绑定命中时渲染 ``<audio controls>`` 播放样本；
- 无绑定时不渲染播放控件（证据与媒体严格分离，不编造）；
- 无 audio_evidence 场景不渲染面板（AC-12 绝不编造）；
- 折叠详细证据区仍含原始 🔊 表格（D1 视图契约兼容）。

不依赖 torch/cv2（纯 stdlib + 投影契约 fixture），可在 torch-free 环境跑。
"""

from __future__ import annotations

import json
from pathlib import Path

from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.artifact_source import load_case_presentation

from .conftest import make_artifacts

# 两个真实音频符号：telephone(相对0s) + voice_raised(相对150s)，覆盖相对时间与多类别。
_AUDIO = [
    {
        "audio_timestamp": 1752952800.0,
        "audio_kind": "audio_telephone_persistent",
        "audio_score": 0.9,
        "audio_confidence": 0.9,
        "audio_labels": ["telephone"],
        "audio_source_segment_ids": ["seg-0"],
    },
    {
        "audio_timestamp": 1752952800.0 + 150.0,
        "audio_kind": "audio_voice_raised",
        "audio_score": 0.85,
        "audio_confidence": 0.88,
        "audio_labels": ["speech"],
        "audio_source_segment_ids": ["seg-1"],
    },
]


def _projection(artifacts_dir: Path):
    return load_case_presentation(artifacts_dir)


def _write_audio_manifest(
    artifacts_dir: Path, scenario_id: str, kinds: list[str]
) -> None:
    """写一份合法的 AudioFileSource manifest（独立绑定层，与 audio_evidence 分离）。"""
    audio_dir = artifacts_dir / scenario_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    files = {k: f"{scenario_id}/audio/{k}.wav" for k in kinds}
    (audio_dir / "manifest.json").write_text(
        json.dumps({"source_kind": "AudioFileSource", "files": files}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_audio_perception_panel_visible_and_humanized(tmp_path):
    """P0 核心验收：首屏面板可见 + 人话化（中文类别 / 相对时间 / score·confidence）。"""
    d = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO)
    projection, descriptor = _projection(d)
    html = render_case_viewer(projection, descriptor, audio_base_dir=None)
    # 首屏面板标题出现（默认首屏布局已含 audio_perception）。
    assert "系统听到了什么（音频感知）" in html
    # 人话化中文类别（含原始枚举括注，供审计）。
    assert "持续电话声音" in html
    assert "高声争吵" in html
    # 相对时间：以最早音频为 T0（0.0s / 150.0s）。
    assert "0.0s" in html
    assert "150.0s" in html
    # score / confidence 人话化呈现。
    assert "score 0.90" in html and "confidence 0.90" in html
    assert "confidence 0.88" in html


def test_audio_perception_with_binding_renders_play_control(tmp_path):
    """证据与媒体分离下的可播放：绑定命中 kind → 渲染 <audio controls> 指向样本 wav。"""
    d = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO)
    _write_audio_manifest(d, "sw_t1", ["audio_telephone_persistent", "audio_voice_raised"])
    projection, descriptor = _projection(d)
    html = render_case_viewer(
        projection, descriptor, audio_base_dir=d, audio_base_url="./"
    )
    # 两个有样本 kind → 两个播放控件（P0-3：<audio> 带 id/data-kind，controls 属性仍在）。
    assert html.count('id="audio-audio_') == 2
    assert html.count('controls') >= 2
    # 样本 id（P0-3 音频轨联动键）。
    assert 'id="audio-audio_telephone_persistent"' in html
    assert 'id="audio-audio_voice_raised"' in html
    # src 指向相对样本 url（字节不进 HTML，仅 ref）。
    assert "./sw_t1/audio/audio_telephone_persistent.wav" in html
    assert "./sw_t1/audio/audio_voice_raised.wav" in html


def test_audio_perception_no_binding_no_play_control(tmp_path):
    """无绑定音频样本 → 只显示证据事实，绝不渲染播放控件（诚实降级，不编造）。"""
    d = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO)
    projection, descriptor = _projection(d)
    html = render_case_viewer(projection, descriptor, audio_base_dir=None)
    assert "系统听到了什么（音频感知）" in html  # 面板仍在（证据可见）
    assert "<audio controls" not in html  # 但没有可播放控件


def test_no_audio_evidence_no_panel(tmp_path):
    """AC-12 / VM-7：无 audio_evidence 场景不渲染音频面板（绝不编造空卡片）。"""
    d = make_artifacts(tmp_path / "a")  # 默认无音频
    projection, descriptor = _projection(d)
    html = render_case_viewer(projection, descriptor, audio_base_dir=d)
    assert "系统听到了什么（音频感知）" not in html
    assert "<audio controls" not in html


def test_audio_evidence_still_in_details_folded_table(tmp_path):
    """兼容：折叠详细证据区仍含原始 🔊 表格（D1 视图契约不被首屏面板破坏）。"""
    d = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO)
    projection, descriptor = _projection(d)
    html = render_case_viewer(projection, descriptor, audio_base_dir=None)
    assert 'id="audio-sw_t1"' in html  # 折叠区 audio_evidence 锚点
    assert "🔊" in html
    # 折叠表格仍保留原始枚举（审计用），人话化以「中文（枚举）」形式呈现。
    assert "audio_telephone_persistent" in html
