"""ADR-0035 D3 · 字幕内容命中测试（§7 验收：caption 出现在正确 shot 区间）。

白盒：monkeypatch render_caption 捕获每次调用的文本，按帧序映射到 shot，
断言每帧字幕 ∈ 该 shot 的 narration（无跨 shot 泄漏），且每 shot narration 非空。

为对齐 generate_case_video 的真实输入，storyboard 由同一 artifact + 同一 override 复算。
"""

from __future__ import annotations

from pathlib import Path

from home_perception.visualizer.video import compiler as compiler_mod
from home_perception.visualizer.video.compiler import (
    _load_author_override,
    generate_case_video,
)
from home_perception.visualizer.video.evidence.adapter import load_scenario_evidence
from home_perception.visualizer.video.narrative.compiler import instantiate_narrative_template
from home_perception.visualizer.video.narrative.templates import template_for_evidence
from home_perception.visualizer.video.spec import CaseVideoSpec
from home_perception.visualizer.video.storyboard.generator import generate_storyboard


def _rebuild_storyboard(spec: CaseVideoSpec):
    evidence = load_scenario_evidence(spec.artifact_dir, spec.scenario_id)
    template = template_for_evidence(evidence, spec.template_name)
    plan = instantiate_narrative_template(evidence, template, audience=spec.audience)
    author = _load_author_override(spec.scenario_id)
    override = (author or {}).get("storyboard")
    return generate_storyboard(plan, evidence, template, audience=spec.audience, override=override)


def test_caption_hits_correct_shot_interval(tmp_path: Path, monkeypatch, built_artifact_dir):
    captured: list[str] = []
    original = compiler_mod.render_caption

    def _record(text, width, height, registry):
        captured.append(text)
        return original(text, width, height, registry)

    # compiler 经 `from …caption import render_caption` 直接绑定名字，
    # 故必须在 compiler 模块命名空间打补丁才能拦截真实调用。
    monkeypatch.setattr(compiler_mod, "render_caption", _record)

    spec = CaseVideoSpec(
        scenario_id="sw_adr0034_elderly_dwell",
        artifact_dir=built_artifact_dir,
        output_dir=tmp_path / "out",
        fps=2.0,
        resolution=(320, 180),
        version=1,
    )
    generate_case_video(spec)

    sb = _rebuild_storyboard(spec)
    fps = spec.fps
    boundaries = []
    start = 0
    for shot in sb.shots:
        n = max(1, round(shot.duration_s * fps))
        boundaries.append((start, start + n, shot))
        start += n

    assert len(captured) == start
    for idx, text in enumerate(captured):
        shot = next(b[2] for b in boundaries if b[0] <= idx < b[1])
        assert text in shot.narration, f"帧 {idx} 字幕 {text!r} 不属于 shot {shot.name!r}"


def test_every_shot_has_nonempty_narration(tmp_path: Path, built_artifact_dir):
    spec = CaseVideoSpec(
        scenario_id="sw_adr0034_elderly_dwell",
        artifact_dir=built_artifact_dir,
        output_dir=tmp_path / "out",
        fps=2.0,
        version=1,
    )
    sb = _rebuild_storyboard(spec)
    for shot in sb.shots:
        assert shot.narration, f"shot {shot.name!r} 无字幕文本"
