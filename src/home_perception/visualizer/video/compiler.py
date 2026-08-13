"""ADR-0035 D3 · 8 阶段管线编排（generate_case_video）。

驱动：EvidenceProjection → EvidenceGraph（复用 loader）→ NarrativePlan（模板实例化）
→ Storyboard（分镜）→ VisualSceneGraph（表达层）→ 逐 shot 渲染（SVG 矢量 + 字幕 + 强制叠加）
→ VideoMuxer 写无声 mp4。全程只读 artifact 投影（D3-12），不新建 EvidenceNode/Edge。

产物（§6）：``case.mp4`` + ``storyboard.yaml`` + ``provenance.json``（供审计）。
结构级一致性（§8 验收 9）：Story/Scene/Duration/Frame-provenance 断言强制（fail-closed）。

见设计文档 §2（8 阶段管线）、§6（产物布局）、§8 验收 9、§9 D3-12。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import NamedTuple

import numpy as np
import yaml

from home_perception.visualizer.video.evidence.adapter import (
    BackgroundProvider,
    SyntheticBackgroundProvider,
    load_scenario_evidence,
)
from home_perception.visualizer.video.mux.muxer import MuxResult, mux
from home_perception.visualizer.video.narrative.compiler import instantiate_narrative_template
from home_perception.visualizer.video.narrative.templates import (
    ScenarioTemplate,
    template_for_evidence,
)
from home_perception.visualizer.video.render.caption import render_caption
from home_perception.visualizer.video.render.composer import compose_frame
from home_perception.visualizer.video.render.font_registry import FontRegistry
from home_perception.visualizer.video.render.overlay import (
    provenance_text,
    render_provenance_layer,
    shorten_label,
)
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
    """读取作者故事板覆盖 YAML（缺文件 → None；坏 YAML → fail-closed）。

    YAML 解析失败必须显式报错：静默吞掉会让「精修覆盖悄悄失效」，而 demo 仍能跑出
    一版默认片子——这是最难发现的一类回归（§8 验收 9 的纪律精神）。
    """
    path = _SCENARIOS_DIR / f"{scenario_id}.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"作者故事板 YAML 解析失败（fail-closed）：{path} · {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError(f"作者故事板 YAML 顶层须为映射，实际为 {type(data).__name__}：{path}")
    return data


def _build_provider(spec: CaseVideoSpec, evidence: dict) -> BackgroundProvider:
    if spec.background == "validation":
        # 可选复用 ADR-0032 render_frames（D3-1 单向例外）。
        # 默认 D3-A 不提供 Scenario 对象；调用方若需 validation 背景须另行注入。
        raise NotImplementedError(
            "background='validation' 需注入 validation.Scenario；D3-A 默认 synthetic"
        )
    return SyntheticBackgroundProvider()


def _assert_audio_boundary(spec: CaseVideoSpec) -> None:
    """D3-A 边界（fail-closed）：``with_audio=True`` 尚无实现，绝不静默产出无声片。

    该字段此前只被 CLI 解析、无任何消费者，``--with-audio`` 会静默得到无声 mp4。
    音频合成属 D3-B（AudioComposer + ffmpeg mux + D3-3 降级）。
    """
    if spec.with_audio:
        raise NotImplementedError(
            "with_audio=True 需 D3-B 旁白/音频合成（AudioComposer + ffmpeg mux）；"
            "本切片(D3-A)仅交付无声 mp4，故拒绝静默降级"
        )


def _provenance_fields(spec: CaseVideoSpec, evidence: dict) -> tuple[int, str]:
    """provenance 两要素（seed / fingerprint）的**唯一**推导处。

    渲染、伴生文件、结构断言三处共用，避免降级规则在多处漂移。
    ``spec.seed is None`` → 确定性降级为 0（投影层不含 seed，见 ``CaseVideoSpec.seed``）。
    """
    seed = spec.seed if spec.seed is not None else 0
    fingerprint = evidence.get("scenario_fingerprint") or "unknown"
    return seed, fingerprint


# D3-A Evidence Story Compiler 生成器标识（provenance.generator_version）。
# 与包版本绑定，保证每次产出可追溯到具体的生成器实现。
ADR0035_D3A_GENERATOR_TAG = "ADR-0035-D3-A"


def _resolve_generator_version() -> str:
    """provenance.generator_version：D3-A 生成器版本标识。

    经 ``importlib.metadata`` 读取已安装包版本（**不** import ``home_perception`` 包体，
    避免破坏 visualizer → 业务包的 import 边界，见 test_ast_contract）。版本缺失时
    fail-closed（不静默降级为空串）。
    """
    try:
        pkg_version = importlib.metadata.version("home_perception")
    except importlib.metadata.PackageNotFoundError as exc:
        raise AssertionError("无法解析生成器版本（home_perception 包未安装）") from exc
    if not pkg_version:
        raise AssertionError("无法解析生成器版本（home_perception 版本为空）")
    return f"{ADR0035_D3A_GENERATOR_TAG}@{pkg_version}"


def _evidence_hash(evidence: dict) -> str:
    """provenance.input_hash：输入（ScenarioEvidence 投影）的稳定指纹。

    同一输入必产生同一哈希（确定性）；序列化失败即 fail-closed 报错，
    不静默降级为常量。用于验收 G2「provenance 必须存在 input_hash」。
    """
    try:
        canonical = json.dumps(evidence, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"输入证据不可序列化（provenance.input_hash 计算失败）：{exc}") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Prepared(NamedTuple):
    """阶段 1–5 的共享产物（两条入口的唯一装配点）。"""

    evidence: dict
    template: ScenarioTemplate
    storyboard: Storyboard
    visual_scenes: dict


def _build_storyboard_and_scenes(spec: CaseVideoSpec) -> _Prepared:
    """阶段 1–5：证据投影 → 模板 → NarrativePlan → Storyboard → VisualSceneGraph。

    ``render_case_frames`` 与 ``generate_case_video`` **共用**本函数。此前两者各自
    重复了同样的前 5 阶段装配（含作者覆盖读取），任一侧改动都会造成
    「预览帧」与「落盘产物」不一致的静默漂移。
    """
    evidence = load_scenario_evidence(spec.artifact_dir, spec.scenario_id)  # D3-12 只读
    template = template_for_evidence(evidence, spec.template_name)
    plan = instantiate_narrative_template(evidence, template, audience=spec.audience)

    author = _load_author_override(spec.scenario_id)
    storyboard = generate_storyboard(
        plan,
        evidence,
        template,
        audience=spec.audience,
        override=(author or {}).get("storyboard"),
    )
    visual_scenes = design_visual_scene(storyboard, evidence, (author or {}).get("visual_override"))
    return _Prepared(evidence, template, storyboard, visual_scenes)


def render_case_frames(spec: CaseVideoSpec) -> list[np.ndarray]:
    """完整管线「阶段 1–7」：返回逐帧 BGR 序列（确定性、零音频）。

    供测试断言视觉确定性，以及 D3-B 预览复用。写盘（阶段 8）不在此内。
    """
    _assert_audio_boundary(spec)
    prepared = _build_storyboard_and_scenes(spec)
    return _render_frames(spec, prepared.evidence, prepared.storyboard, prepared.visual_scenes)


def _narration_line(narration: list[str], frame_index: int, n_frames: int) -> str:
    """把 shot 的 narration 数组按帧序均匀铺开（末帧必命中末条）。

    旧式 ``i * len // n_frames`` 在 ``n_frames >> len(narration)`` 时会让最后一条
    字幕停留过长（例如 10 帧 / 3 条 → 4:3:3 且末帧未必落到末条）。除以
    ``n_frames - 1`` 后，末帧索引恒 ``== len``（由 ``min`` 收敛到末条），
    分布更均匀且「最后一句一定说完」。
    """
    idx = frame_index * len(narration) // max(1, n_frames - 1)
    return narration[min(len(narration) - 1, idx)]


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
    seed, fingerprint = _provenance_fields(spec, evidence)
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
            caption_rgba = render_caption(
                _narration_line(narration, i, n_frames), width, caption_h, registry
            )
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
    _assert_audio_boundary(spec)

    # 阶段 1–5（与 render_case_frames 共用同一装配，杜绝漂移）。
    evidence, template, storyboard, visual_scenes = _build_storyboard_and_scenes(spec)

    # 阶段 6–7：逐 shot 渲染帧序列（确定性）。
    frames = _render_frames(spec, evidence, storyboard, visual_scenes)
    seed, fingerprint = _provenance_fields(spec, evidence)
    generator_version = _resolve_generator_version()
    input_hash = _evidence_hash(evidence)

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
        # 验收 G2 必备四键（scenario_id / generator_version / input_hash / template_version）。
        "scenario_id": spec.scenario_id,
        "generator_version": generator_version,
        "input_hash": input_hash,
        "template_version": template.name,
        # 诊断/审计辅助字段。
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

    # Frame provenance：走真实渲染路径断言（§8 验收 9）。
    _assert_frame_provenance(spec, evidence)


def _assert_frame_provenance(spec: CaseVideoSpec, evidence: dict) -> None:
    """Frame provenance（§8 验收 9）：角标必须**真的**把 scenario_id 渲进像素。

    旧实现在本函数内用 f-string 拼出角标串、再断言该串含 ``scenario_id`` —— 同义反复
    （tautology）：断言恒成立，抓不到「overlay 实际没把 scenario_id 画上去」的回归。
    现改为三段真实校验：

    1. **共享格式化契约**：角标文本取自 overlay 的唯一格式化函数 ``provenance_text``
       （渲染同源）。一旦格式串丢掉 scenario_id，此处立即失败；
    2. **确有绘制**：真实渲染叠加层，断言右下角标区域存在非透明像素；
    3. **敏感性**：换一个 scenario_id 渲染，像素必须变化——证明角标像素真的编码了
       场景标识，而非画了个与场景无关的固定水印。
    """
    seed, fingerprint = _provenance_fields(spec, evidence)
    text = provenance_text(spec.scenario_id, seed, fingerprint)
    if not spec.scenario_id or spec.scenario_id not in text:
        raise AssertionError(
            f"Frame provenance 失败：角标格式串未含 scenario_id（text={text!r}）"
        )

    width, height = spec.resolution
    registry = FontRegistry()
    layer = np.array(
        render_provenance_layer(width, height, spec.scenario_id, seed, fingerprint, registry)
    )
    corner_alpha = layer[height // 2 :, width // 2 :, 3]
    if not corner_alpha.any():
        raise AssertionError("Frame provenance 失败：叠加层右下角标区域无可见像素（角标未绘制）")

    mutated = np.array(
        render_provenance_layer(
            width, height, f"{spec.scenario_id}__mutated", seed, fingerprint, registry
        )
    )
    if np.array_equal(layer, mutated):
        raise AssertionError(
            "Frame provenance 失败：叠加层像素与 scenario_id 无关（角标未真正编码场景标识）"
        )


__all__ = ["CaseVideoResult", "generate_case_video", "render_case_frames"]
