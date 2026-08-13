"""ADR-0035 D3 · 案例视频 CLI 退出码单测（评审缺口 #10）。

scripts/generate_case_video.py 退出码契约：
- 成功 → 0 且写出 case.mp4
- --with-audio（D3-B 未落地）→ fail-closed 退出 1（不静默产出无声片）
- 场景不存在 → KeyError → 退出 1
- resolution 解析失败 → argparse → 退出 2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

from generate_case_video import main

_ARTIFACT_DIR = _REPO / "artifacts" / "adr0034_integration"
_SCENARIO = "sw_adr0034_elderly_dwell"


def _args(tmp_path: Path, extra=None) -> list[str]:
    base = [
        "--artifact-dir", str(_ARTIFACT_DIR),
        "--scenario-id", _SCENARIO,
        "--output-dir", str(tmp_path),
        "--resolution", "160x90",
    ]
    if extra:
        base += extra
    return base


def test_cli_success_exit_0(tmp_path: Path):
    rc = main(_args(tmp_path))
    assert rc == 0
    case_mp4 = tmp_path / f"{_SCENARIO}__v1" / "case.mp4"
    assert case_mp4.exists() and case_mp4.stat().st_size > 0


def test_cli_provenance_g2_required_keys(tmp_path: Path):
    """验收 G2：provenance.json 必须含 scenario_id / generator_version /
    input_hash / template_version 四键（且非空）。"""
    rc = main(_args(tmp_path))
    assert rc == 0
    prov = tmp_path / f"{_SCENARIO}__v1" / "provenance.json"
    assert prov.exists(), "provenance.json 未产出"
    data = json.loads(prov.read_text(encoding="utf-8"))
    for key in ("scenario_id", "generator_version", "input_hash", "template_version"):
        assert data.get(key), f"provenance 缺 G2 必含键（或为空）: {key}"
    assert data["scenario_id"] == _SCENARIO


def test_cli_with_audio_exit_1(tmp_path: Path):
    rc = main(_args(tmp_path, extra=["--with-audio"]))
    assert rc == 1
    # fail-closed：不得写出无声 mp4 冒充有声片
    case_mp4 = tmp_path / f"{_SCENARIO}__v1" / "case.mp4"
    assert not case_mp4.exists()


def test_cli_with_audio_verbose_exit_1(tmp_path: Path):
    # --verbose 不得让异常逃逸出 main()（仍可被单测捕获退出码）
    rc = main(_args(tmp_path, extra=["--with-audio", "--verbose"]))
    assert rc == 1


def test_cli_missing_scenario_exit_1(tmp_path: Path):
    rc = main([
        "--artifact-dir", str(_ARTIFACT_DIR),
        "--scenario-id", "sw_no_such_scenario",
        "--output-dir", str(tmp_path),
        "--resolution", "160x90",
    ])
    assert rc == 1


def test_cli_bad_resolution_exit_2(tmp_path: Path):
    rc = main([
        "--artifact-dir", str(_ARTIFACT_DIR),
        "--scenario-id", _SCENARIO,
        "--output-dir", str(tmp_path),
        "--resolution", "not-a-resolution",
    ])
    assert rc == 2
