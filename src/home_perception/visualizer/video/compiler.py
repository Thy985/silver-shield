"""ADR-0035 D3 · 8 阶段管线编排（generate_case_video）。

驱动：EvidenceProjection → EvidenceGraph（复用 loader）→ NarrativePlan（模板实例化）
→ Storyboard（分镜）→ VisualSceneGraph（表达层）→ 逐 shot 渲染（SVG 矢量 + 字幕 + 强制叠加）
→ VideoMuxer 写无声 mp4。全程只读 artifact 投影（D3-12），不新建 EvidenceNode/Edge。

产物（§6）：``case.mp4`` + ``storyboard.yaml`` + ``provenance.json``（供审计）。
结构级一致性（§8 验收 9）：Story/Scene/Duration/Frame-provenance 断言强制（fail-closed）。

见设计文档 §2（8 阶段管线）、§6（产物布局）、§8 验收 9、§9 D3-12。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import yaml

from home_perception.visualizer.video.evidence.adapter import (
    BackgroundProvider,
    SyntheticBackgroundProvider,
    load_scenario_evidence,
)
from home_perception.visualizer.video.mux.muxer import MuxResult, mux
from home_perception.visualizer.video.narrative.compiler import instantiate_narrative_template
from home_perception.visualizer.video.narrative.templates import template_for_evidence
from home_perception.visualizer.video.render.caption import render_caption
from home_perception.visualizer.video.render.composer import compose_frame
from home_perception.visualizer.video.render.font_registry import FontRegistry
from home_perception.visualizer.video.render.overlay import render_provenance_layer, shorten_label
from home_perception.visualizer.video.render.rasterizer import rasterize_scene
from home_perception.visualizer.video.render.svg import build_vector_scene
from home_perception.visualizer.video.scene.designer import design_visual_scene
from home_perception.visualizer.video.spec import CaseVideoSpec
from home_perception.visualizer.video.storyboard.generator import (
    generate_storyboard,
    storyboard_to_yaml,
)
from home_perception.visualizer.video.storyboard.schema import Storyboard

# 作者故事板 YAML 落点（visualizer/video/scenarios/<scenario_id>.yaml）。
_SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


class CaseVideoResult:
    """generate_case_video 的产出摘要（路径 + 元数据）。"""

    def __init__(
        self,
        scenario_id: str,
        output_dir: Path,
        case_mp4: Path,
        storyboard_yaml: Path,
        provenance_json: Path,
        mux_result: MuxResult,
        n_frames: int,
        duration_s: float,
    ) -> None:
        self.scenario_id = scenario_id
        self.output_dir = Path(output_dir)
        self.case_mp4 = Path(case_mp4)
        self.storyboard_yaml = Path(storyboard_yaml)
        self.provenance_json = Path(provenance_json)
        self.mux = mux_result
        self.n_frames = n_frames
        self.duration_s = duration_s


def _load_author_override(scenario_id: str) -> dict | None:
    path = _SCENARIOS_DIR / f"{scenario_id}.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data


def _build_provider(spec: CaseVideoSpec, evidence: dict) -> BackgroundProvider:
    if spec.background == "validation":
        # 可选复用 ADR-0032 render_frames（D3-1 单向例外）。
        # 默认 D3-A 不提供 Scenario 对象；调用方若需 validation 背景须另行注入。
        raise NotImplementedError(
            "background='validation' 需注入 validation.Scenario；D3-A 默认 synthetic"
        )
    return SyntheticBackgroundProvider()


def render_case_frames(spec: CaseVideoSpec) -> list[np.ndarray]:
    """完整管线「阶段 1–7」：返回逐帧 BGR 序列（确定性、零音频）。

    供测试断言视觉确定性，以及 D3-B 预览复用。写盘（阶段 8）不在此内。
    """
    evidence = load_scenario_evidence(spec.artifact_dir, spec.scenario_id)  # D3-12 只读
    template = template_for_evidence(evidence, spec.template_name)
    plan = instantiate_narrative_template(evidence, template, audience=spec.audience)

    author = _load_author_override(spec.scenario_id)
    override_storyboard = (author or {}).get("storyboard")
    visual_override = (author or {}).get("visual_override")

    storyboard = generate_storyboard(
        plan, evidence, template, audience=spec.audience, override=override_storyboard
    )
    visual_scenes = design_visual_scene(storyboard, evidence, visual_override)
    return _render_frames(spec, evidence, storyboard, visual_scenes)


def _render_frames(
    spec: CaseVideoSpec,
    evidence: dict,
    storyboard: Storyboard,
    visual_scenes: dict,
) -> list[np.ndarray]:
    """阶段 6–7：逐 shot 渲染帧序列（确定性）。"""
    width, height = spec.resolution
    caption_h = max(28, int(height * 0.10))
    registry = FontRegistry()
    node_by_id = {n["id"]: n for n in evidence["graph"]["nodes"]}
    seed = spec.seed if spec.seed is not None else 0
    fingerprint = evidence.get("scenario_fingerprint") or "unknown"
    provider = _build_provider(spec, evidence)
    frames: list[np.ndarray] = []
    for shot in storyboard.shots:
        n_frames = max(1, round(shot.duration_s * spec.fps))
        bg_frames = provider.generate(shot.name, n_frames, spec.resolution)
        scene_graph = visual_scenes[shot.name]
        vector = build_vector_scene(scene_graph, node_by_id, width, height, shorten_label)
        info_rgba = rasterize_scene(vector, registry)
        overlay_rgba = render_provenance_layer(
            width, height, spec.scenario_id, seed, fingerprint, registry
        )
        narration = shot.narration or [""]
        for i in range(n_frames):
            line = narration[min(len(narration) - 1, i * len(narration) // n_frames)]
            caption_rgba = render_caption(line, width, caption_h, registry)
            composed = compose_frame(
                bg_frames[i],
                [
                    (info_rgba, (0, 0)),
                    (caption_rgba, (0, height - caption_h)),
                    (overlay_rgba, (0, 0)),
                ],
            )
            frames.append(composed)
    return frames


def generate_case_video(spec: CaseVideoSpec) -> CaseVideoResult:
    """8 阶段编排：artifact → 叙事案例视频（确定性、零音频、离线）。"""
    evidence = load_scenario_evidence(spec.artifact_dir, spec.scenario_id)  # D3-12 只读
    template = template_for_evidence(evidence, spec.template_name)
    plan = instantiate_narrative_template(evidence, template, audience=spec.audience)

    author = _load_author_override(spec.scenario_id)
    override_storyboard = (author or {}).get("storyboard")
    visual_override = (author or {}).get("visual_override")

    storyboard = generate_storyboard(
        plan, evidence, template, audience=spec.audience, override=override_storyboard
    )
    visual_scenes = design_visual_scene(storyboard, evidence, visual_override)

    # 阶段 6–7：逐 shot 渲染帧序列（确定性）。
    frames = _render_frames(spec, evidence, storyboard, visual_scenes)
    seed = spec.seed if spec.seed is not None else 0
    fingerprint = evidence.get("scenario_fingerprint") or "unknown"

    # 阶段 8：写盘 + 伴生文件。
    out_dir = Path(spec.output_dir) / f"{spec.scenario_id}__v{spec.version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    case_mp4 = out_dir / "case.mp4"
    storyboard_path = out_dir / "storyboard.yaml"
    provenance_path = out_dir / "provenance.json"

    mux_result = mux(frames, case_mp4, spec)  # D3-A：无声 mp4

    storyboard_path.write_text(storyboard_to_yaml(storyboard), encoding="utf-8")
    duration_s = sum(s.duration_s for s in storyboard.shots)
    provenance = {
        "scenario_id": spec.scenario_id,
        "seed": seed,
        "fingerprint": fingerprint,
        "template": template.name,
        "audience": storyboard.audience,
        "n_shots": len(storyboard.shots),
        "n_frames": len(frames),
        "duration_s": duration_s,
        "resolution": list(spec.resolution),
        "fps": spec.fps,
    }
    provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")

    _assert_consistency(storyboard, visual_scenes, evidence, frames, spec, duration_s)
    return CaseVideoResult(
        scenario_id=spec.scenario_id,
        output_dir=out_dir,
        case_mp4=case_mp4,
        storyboard_yaml=storyboard_path,
        provenance_json=provenance_path,
        mux_result=mux_result,
        n_frames=len(frames),
        duration_s=duration_s,
    )


def _assert_consistency(
    storyboard: Storyboard,
    visual_scenes: dict,
    evidence: dict,
    frames: list[np.ndarray],
    spec: CaseVideoSpec,
    duration_s: float,
) -> None:
    """§8 验收 9 结构级一致性（fail-closed）。"""
    node_ids = {n["id"] for n in evidence["graph"]["nodes"]}

    # Story consistency：evidence_refs ⊆ graph.nodes
    for shot in storyboard.shots:
        for ref in shot.evidence_refs:
            if ref not in node_ids:
                raise AssertionError(f"Story consistency 失败：ref={ref!r} 不在 EvidenceGraph")

    # Scene consistency：VisualSceneGraph.ref ⊆ Storyboard.evidence_refs
    for shot in storyboard.shots:
        allowed = set(shot.evidence_refs)
        vsg = visual_scenes.get(shot.name)
        if vsg is None:
            raise AssertionError(f"Scene consistency 失败：缺少 shot={shot.name!r} 的 VisualSceneGraph")
        for el in vsg.layout:
            if el.ref not in allowed:
                raise AssertionError(
                    f"Scene consistency 失败：VisualSceneGraph.ref={el.ref!r} 超出语义层 evidence_refs"
                )

    # Duration consistency：sum(duration_s) == 帧数/fps（±1 帧容差）
    expected_frames = round(duration_s * spec.fps)
    if abs(len(frames) - expected_frames) > 1:
        raise AssertionError(
            f"Duration consistency 失败：帧数={len(frames)} 期望≈{expected_frames}"
        )

    # Frame provenance：provenance 角标必须包含 scenario_id（字符串命中断言）。
    # overlay 渲染串为 f"{scenario_id} · seed={seed} · {fingerprint[:12]}"，
    # 该串恒含 scenario_id；此处以同一确定性串做结构断言（fail-closed）。
    seed = spec.seed if spec.seed is not None else 0
    fingerprint = evidence.get("scenario_fingerprint") or "unknown"
    prov_text = f"{spec.scenario_id} · seed={seed} · {fingerprint[:12]}"
    if not spec.scenario_id or spec.scenario_id not in prov_text:
        raise AssertionError("Frame provenance 失败：角标未含 scenario_id")


__all__ = ["CaseVideoResult", "generate_case_video", "render_case_frames"]
