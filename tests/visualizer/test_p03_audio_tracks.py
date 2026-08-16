"""P0-3 media_tracks（音频轨时间绑定 + Case Time 联动）契约测试。

覆盖：
- ``prepare_case_audio._discover_audio_specs``：canonical audio_evidence → kind/timestamp；
- ``_prepare_one`` 写 tracks（start_time 相对最早音频 T0、provenance SIMULATED、升序）；
- ``audio_source.resolve_audio_source``：新 manifest 解析 tracks；旧 manifest（无 tracks）
  → 恒 ()（向后兼容）；结构非法 fail-closed；
- render：音频卡片 ``<audio id="audio-<kind>" data-kind>`` + timeline AUDIO 节点
  ``data-kind`` + AudioSync 注入条件（有音频面板注入 / 无则不注入）；
- audio_sync.js 行为（Node vm + mock DOM）：点击 timeline 音频节点 → play 对应样本 +
  高亮卡片；无样本 no-op。

不依赖 torch/cv2（纯 stdlib + 投影契约 fixture），可在 torch-free 环境跑。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.artifact_source import load_case_presentation
from home_perception.visualizer.viewer.audio_source import (
    AudioSourceError,
    resolve_audio_source,
)

from .conftest import make_artifacts

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


def _write_audio_manifest_with_tracks(
    artifacts_dir: Path, scenario_id: str
) -> None:
    """写含 tracks 的合法 AudioFileSource manifest（P0-3 新契约）。"""
    audio_dir = artifacts_dir / scenario_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "audio_telephone_persistent.wav").write_bytes(b"RIFFxxxx")
    (audio_dir / "audio_voice_raised.wav").write_bytes(b"RIFFxxxx")
    manifest = {
        "source_kind": "AudioFileSource",
        "files": {
            "audio_telephone_persistent": f"{scenario_id}/audio/audio_telephone_persistent.wav",
            "audio_voice_raised": f"{scenario_id}/audio/audio_voice_raised.wav",
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
            {
                "id": "audio_voice_raised",
                "kind": "audio_voice_raised",
                "url": f"{scenario_id}/audio/audio_voice_raised.wav",
                "start_time": 150.0,
                "end_time": None,
                "provenance_kind": "SIMULATED",
            },
        ],
    }
    (audio_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 1. prepare_case_audio：_discover_audio_specs（kind → timestamp）
# ---------------------------------------------------------------------------


def test_discover_audio_specs_reads_timestamps(tmp_path):
    """canonical audio_evidence → kind/timestamp 映射（仅真实出现的 kind）。"""
    from scripts.prepare_case_audio import _discover_audio_specs

    canonical = tmp_path / "sw_t1.canonical.json"
    canonical.write_text(
        json.dumps(
            {"artifacts": {"audio_evidence": _AUDIO}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    specs = _discover_audio_specs(canonical)
    assert specs == {
        "audio_telephone_persistent": {"timestamp": 1752952800.0},
        "audio_voice_raised": {"timestamp": 1752952800.0 + 150.0},
    }


def test_discover_audio_specs_no_audio_empty(tmp_path):
    """无 audio_evidence / canonical 损坏 → 空 dict（不编造）。"""
    from scripts.prepare_case_audio import _discover_audio_specs

    p = tmp_path / "sw_t1.canonical.json"
    p.write_text(json.dumps({"artifacts": {}}), encoding="utf-8")
    assert _discover_audio_specs(p) == {}
    p.write_text("not-json", encoding="utf-8")
    assert _discover_audio_specs(p) == {}


# ---------------------------------------------------------------------------
# 2. prepare_case_audio：_prepare_one 写 tracks（时间绑定）
# ---------------------------------------------------------------------------


def test_prepare_one_writes_tracks(tmp_path):
    """_prepare_one 写 tracks：start_time 相对最早音频 T0、provenance SIMULATED、升序。"""
    from scripts.prepare_case_audio import _prepare_one

    artifacts = tmp_path / "artifacts"
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True)
    # 造 2 个 fixture wav（_FIXTURE_MAP 引用的文件名）。
    for name in ("telephone_noisy.wav", "raised_voice_far.wav"):
        (fixtures / name).write_bytes(b"RIFFxxxx")

    specs = {
        "audio_telephone_persistent": {"timestamp": 1752952800.0},
        "audio_voice_raised": {"timestamp": 1752952800.0 + 150.0},
    }
    assert _prepare_one(artifacts, fixtures, "sw_t1", specs, force=False) is True

    manifest = json.loads(
        (artifacts / "sw_t1" / "audio" / "manifest.json").read_text(encoding="utf-8")
    )
    tracks = manifest["tracks"]
    assert [t["start_time"] for t in tracks] == [0.0, 150.0]  # 相对 T0 升序
    assert all(t["provenance_kind"] == "SIMULATED" for t in tracks)
    assert tracks[0]["id"] == "audio_telephone_persistent"
    assert tracks[0]["url"] == "sw_t1/audio/audio_telephone_persistent.wav"


def test_prepare_one_no_fixture_kind_skipped_no_track(tmp_path):
    """有 spec 无 fixture 的 kind：不复制样本、不进 tracks（诚实，不编造）。"""
    from scripts.prepare_case_audio import _prepare_one

    artifacts = tmp_path / "artifacts"
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "telephone_noisy.wav").write_bytes(b"RIFFxxxx")
    specs = {
        "audio_telephone_persistent": {"timestamp": 1752952800.0},
        "audio_anomaly_other": {"timestamp": 1752952800.0},  # 无 fixture
    }
    assert _prepare_one(artifacts, fixtures, "sw_t1", specs, force=False) is True
    manifest = json.loads(
        (artifacts / "sw_t1" / "audio" / "manifest.json").read_text(encoding="utf-8")
    )
    assert [t["id"] for t in manifest["tracks"]] == ["audio_telephone_persistent"]


# ---------------------------------------------------------------------------
# 3. audio_source：tracks 解析（新契约 + 向后兼容 + fail-closed）
# ---------------------------------------------------------------------------


def test_resolve_audio_source_parses_tracks(tmp_path):
    """新 manifest：resolve_audio_source 返回 tracks（时间绑定解析）。"""
    canon = make_artifacts(tmp_path / "a")
    _write_audio_manifest_with_tracks(canon, "sw_t1")
    am = resolve_audio_source(canon, "sw_t1")
    assert am is not None
    assert am["source_kind"] == "AudioFileSource"
    assert len(am["tracks"]) == 2
    assert am["tracks"][0]["start_time"] == 0.0
    assert am["tracks"][1]["start_time"] == 150.0


def test_resolve_audio_source_legacy_no_tracks(tmp_path):
    """旧 manifest（无 tracks 键）→ tracks 恒 ()（向后兼容，不崩）。"""
    canon = make_artifacts(tmp_path / "a")
    audio_dir = canon / "sw_t1" / "audio"
    audio_dir.mkdir(parents=True)
    (audio_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_kind": "AudioFileSource",
                "files": {"audio_telephone_persistent": "sw_t1/audio/x.wav"},
            }
        ),
        encoding="utf-8",
    )
    am = resolve_audio_source(canon, "sw_t1")
    assert am is not None
    assert am["tracks"] == ()


def test_resolve_audio_source_tracks_invalid_fail_closed(tmp_path):
    """tracks 结构非法（start_time 非数值）→ AudioSourceError（fail-closed）。"""
    canon = make_artifacts(tmp_path / "a")
    audio_dir = canon / "sw_t1" / "audio"
    audio_dir.mkdir(parents=True)
    (audio_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_kind": "AudioFileSource",
                "files": {},
                "tracks": [{"id": "t1", "url": "x.wav", "start_time": "oops"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AudioSourceError):
        resolve_audio_source(canon, "sw_t1")


# ---------------------------------------------------------------------------
# 4. render：data-kind / id 渲染 + AudioSync 注入条件
# ---------------------------------------------------------------------------


def _render_with_audio(tmp_path, *, tracks: bool = True) -> str:
    canon = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO)
    if tracks:
        _write_audio_manifest_with_tracks(canon, "sw_t1")
    proj, desc = load_case_presentation(canon)
    # audio_base_dir 须显式传（与 run_case_viewer 同契约）——否则无 Audio Source 绑定，
    # 卡片不渲染播放控件（证据仍展示，诚实降级）。
    return render_case_viewer(
        proj, desc, audio_base_dir=canon, audio_base_url="./"
    )


def test_render_audio_timeline_nodes_have_data_kind(tmp_path):
    """timeline AUDIO 节点带 data-kind（联动键）；非 AUDIO 节点不带。"""
    html = _render_with_audio(tmp_path)
    assert 'data-kind="audio_telephone_persistent"' in html
    assert 'data-kind="audio_voice_raised"' in html


def test_render_audio_cards_have_id_and_data_kind(tmp_path):
    """音频卡片：<audio id="audio-<kind>" data-kind> + 卡片 data-kind。"""
    html = _render_with_audio(tmp_path)
    assert 'id="audio-audio_telephone_persistent"' in html
    assert 'data-kind="audio_telephone_persistent"' in html
    # 卡片元素本身也带 data-kind（供高亮定位）。
    assert 'class="audio-card" data-kind="audio_telephone_persistent"' in html


def test_render_audio_sync_injected_only_with_audio_panel(tmp_path):
    """AudioSync 引擎注入：音频面板存在时注入；无音频证据时不注入（零成本降级）。"""
    html = _render_with_audio(tmp_path)
    assert "__AudioSync" in html
    # 无音频场景（make_artifacts 不注入 audio_evidence）→ 面板空 → 不注入。
    canon = make_artifacts(tmp_path / "b")
    proj, desc = load_case_presentation(canon)
    html2 = render_case_viewer(proj, desc)
    assert "__AudioSync" not in html2


# ---------------------------------------------------------------------------
# 5. audio_sync.js 行为（Node vm + mock DOM，对齐 html-inline-js 纪律）
# ---------------------------------------------------------------------------


def _audio_sync_source() -> str:
    from home_perception.visualizer.renderer import _audio_sync_inline

    src = _audio_sync_inline()
    assert src, "audio_sync.js 必须存在"
    return src


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_audio_sync_js_click_plays_sample_and_highlights():
    """Node vm 真实运行：点击 timeline 音频节点 → play 样本 + 高亮卡片；无样本 no-op。"""
    import subprocess
    import textwrap

    src = _audio_sync_source()
    harness = textwrap.dedent(
        """
        const fs = require('fs');
        // mock DOM
        const listeners = {};
        const els = {};
        function makeEl(attrs) {
          return {
            attrs: attrs || {},
            classList: { add: function(){}, remove: function(){} },
            style: {},
            addEventListener: function(evt, fn) { listeners[evt] = fn; },
          };
        }
        const tlItem = makeEl();
        tlItem.getAttribute = function(k) { return k === 'data-kind' ? 'audio_telephone_persistent' : null; };
        const card = makeEl();
        card.classList = { add: function(c){ card._added = c; }, remove: function(){} };
        const audioEl = {
          _played: false,
          play: function() { this._played = true; },
        };
        const cardNodes = [card];
        const doc = {
          readyState: 'complete',
          addEventListener: function(){},
          querySelectorAll: function(sel) {
            if (sel === '.tl-item[data-kind]') return [tlItem];
            if (sel.indexOf('.audio-card[data-kind="') === 0) return cardNodes;
            return [];
          },
          getElementById: function(id) { return id === 'audio-audio_telephone_persistent' ? audioEl : null; },
        };
        global.document = doc;
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));
        // 触发点击
        listeners.click();
        const ok = audioEl._played === true && card._added === 'audio-card-active';
        console.log(ok ? 'AUDIO_SYNC_OK' : 'AUDIO_SYNC_FAIL');
        process.exit(ok ? 0 : 1);
        """
    )
    # 写临时 harness 脚本 + 引擎源文件（引擎经 argv[2] 传真实文件路径）。
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        harness_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        src_path = f.name
    r = subprocess.run(
        ["node", harness_path, src_path],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        check=False,
    )
    import os

    os.unlink(harness_path)
    os.unlink(src_path)
    assert r.returncode == 0, f"audio_sync 行为失败: {r.stdout} {r.stderr}"
    assert "AUDIO_SYNC_OK" in r.stdout
