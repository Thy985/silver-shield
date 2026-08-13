"""ADR-0035 D3 · 作者故事板 YAML 覆盖单测（评审缺口 #11 · fail-closed + 字段校验）。

_load_author_override：缺文件 → None；坏 YAML → RuntimeError；顶层非 dict → RuntimeError。
合法覆盖须经 generate_storyboard 解析（伪造 ref → ValueError）。
"""

from __future__ import annotations

import pytest

from home_perception.visualizer.video import compiler as compiler_mod
from home_perception.visualizer.video.narrative.compiler import instantiate_narrative_template
from home_perception.visualizer.video.narrative.templates import template_for_evidence
from home_perception.visualizer.video.storyboard.generator import generate_storyboard

from .conftest import make_evidence


def test_missing_override_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler_mod, "_SCENARIOS_DIR", tmp_path)
    assert compiler_mod._load_author_override("does_not_exist") is None


def test_bad_yaml_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler_mod, "_SCENARIOS_DIR", tmp_path)
    (tmp_path / "badscn.yaml").write_text("foo: [unclosed\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        compiler_mod._load_author_override("badscn")


def test_top_level_non_dict_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler_mod, "_SCENARIOS_DIR", tmp_path)
    (tmp_path / "listyaml.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(TypeError):
        compiler_mod._load_author_override("listyaml")


def test_real_override_loads_audience():
    # 仓库内 scenarios/sw_adr0034_elderly_dwell.yaml 为合法覆盖
    data = compiler_mod._load_author_override("sw_adr0034_elderly_dwell")
    assert data is not None
    assert data["storyboard"]["audience"] == "judges"


def test_override_fake_ref_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler_mod, "_SCENARIOS_DIR", tmp_path)
    (tmp_path / "ghost.yaml").write_text(
        "storyboard:\n  shots:\n    - name: detection\n      evidence_refs: [ghost_ref]\n",
        encoding="utf-8",
    )
    evidence = make_evidence()
    template = template_for_evidence(evidence)
    plan = instantiate_narrative_template(evidence, template)
    author = compiler_mod._load_author_override("ghost")
    with pytest.raises(ValueError):
        generate_storyboard(plan, evidence, template, override=(author or {}).get("storyboard"))
