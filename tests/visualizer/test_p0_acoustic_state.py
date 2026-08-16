"""P0 Acoustic State（telephone_risk）真实验收测试。

P0-1 数据鸿沟修复后：声学状态机 / voice_stress_score / deltas 经 loader 投影进入
``audio_evidence``，渲染卡须真实呈现这些**可观测量值**，而非硬编码「音高 / 能量等」。

P0-2 golden E2E：真实读取 ``data/golden/telephone_risk/manifest.yaml``（设计契约真源），
把其声明式声学状态机映射进 canonical ``audio_*`` 契约，经**真实 loader + 真实 renderer**
全链路验证：状态机数值入卡、voice_stress_score 入卡、跨模态 SUPPORTS 行在生产管线形状下产出。

红线（VM-9 / VM-1 / AC-12）：仅描述可观测信号，绝不推导 STRESS / 诈骗 / 当事人心理；
字段缺失即不展示，绝不占位编造。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.artifact_source import load_case_presentation
from home_perception.visualizer.viewer.render import _render_acoustic_state_card

from .conftest import make_artifacts

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN_MANIFEST = (
    _REPO_ROOT / "data" / "golden" / "telephone_risk" / "manifest.yaml"
)


def _scenario(audio_evidence, recommended_actions=(), command_types=()):
    return {
        "scenario_id": "sw_t1",
        "ok": True,
        "audio_evidence": tuple(audio_evidence),
        "recommended_actions": tuple(recommended_actions),
        "command_types": tuple(command_types),
    }


# ---------------------------------------------------------------------------
# 单元测试：真实声学量值入卡（P0-1 数据鸿沟修复）
# ---------------------------------------------------------------------------


def test_shows_real_acoustic_state_machine_and_score():
    """golden case_b 声学状态机 + voice_stress_score + deltas 真实入卡。"""
    node = {
        "kind": "audio_telephone_persistent",
        "score": 0.85,
        "confidence": 0.88,
        "labels": ["telephone_interaction", "speech"],
        "acoustic_state_change": "NORMAL -> ATTENTION -> AROUSAL -> STRESS",
        "voice_stress_score": 0.72,
        "f0_delta": 0.24,
        "speech_rate_delta": 0.29,
        "energy_delta": 0.23,
    }
    html = _render_acoustic_state_card(_scenario([node]))
    # 状态机逐相呈现（不再硬编码「音高/能量等」）。
    assert "NORMAL → ATTENTION → AROUSAL → STRESS" in html
    # 量化指标真实呈现。
    assert "voice_stress_score = 0.72" in html
    assert "F0 Δ=0.24" in html
    assert "语速 Δ=0.29" in html
    assert "能量 Δ=0.23" in html
    # 不再出现含糊的「音高 / 能量等可观测信号变化」措辞。
    assert "音高 / 能量等可观测信号变化" not in html


def test_stable_state_no_change_claim():
    """单一稳定态（NORMAL）：标题保留，陈述「稳定」，不声称偏离 / STRESS 跃迁。"""
    node = {
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.9,
        "labels": ["telephone"],
        "acoustic_state_change": "NORMAL",
    }
    html = _render_acoustic_state_card(_scenario([node]))
    assert "稳定" in html
    assert "STRESS" not in html  # 稳定态不出现 STRESS
    assert "并检测到声学偏离" not in html


def test_no_fraud_or_psychology_inference_with_real_state():
    """含真实声学状态机（含 STRESS 标签）时，仍不推导当事人心理 / 诈骗。

    STRESS 仅作为**声学状态枚举**出现；卡不得声称「老人恐惧 / 判定诈骗」。
    """
    node = {
        "kind": "audio_telephone_persistent",
        "score": 0.85,
        "confidence": 0.88,
        "labels": ["telephone_interaction"],
        "acoustic_state_change": "NORMAL -> ATTENTION -> AROUSAL -> STRESS",
        "voice_stress_score": 0.72,
    }
    html = _render_acoustic_state_card(_scenario([node]))
    # 红线：诈骗仅以否定式免责声明出现，绝不作为结论。
    assert "不推导当事人心理或诈骗判定" in html
    assert "老人" not in html
    # 不得出现对当事人的语义/心理判定结论。
    for forbidden in ("判定为诈骗", "老人恐惧", "确认诈骗", "诈骗发生"):
        assert forbidden not in html


def test_cross_modal_supports_with_real_acoustic_ref():
    """真实跨模态关联（audio_related_visual_ref）→ SUPPORTS → 持续观察。"""
    node = {
        "kind": "audio_telephone_persistent",
        "score": 0.85,
        "confidence": 0.88,
        "labels": ["telephone_interaction"],
        "acoustic_state_change": "NORMAL -> ATTENTION -> AROUSAL -> STRESS",
        "related_visual_ref": "visual:phone_interaction:seg-0",
    }
    html = _render_acoustic_state_card(_scenario([node]))
    assert "CROSS_MODAL: SUPPORTS" in html
    assert "持续观察" in html
    assert "visual:phone_interaction:seg-0" not in html  # 不泄露内部 ref 原值


# ---------------------------------------------------------------------------
# P0-2 golden E2E：真实 manifest → canonical 契约 → loader → renderer 全链路
# ---------------------------------------------------------------------------


def _parse_audio_list(items):
    """manifest evidence.audio 为「标量标签 + 单键映射」混合列表 → 拆为 (k/v 字典, 标签列表)。"""
    out: dict = {}
    labels: list[str] = []
    for it in items:
        if isinstance(it, str):
            labels.append(it)
        elif isinstance(it, dict):
            for k, v in it.items():
                out[k] = v
    return out, labels


def _golden_audio_node(variant: dict) -> dict:
    """把 golden manifest 的声明式声学证据映射进 canonical ``audio_*`` 契约。"""
    ev = variant["evidence"]["audio"]
    out, _ = _parse_audio_list(ev)
    cross = variant["evidence"].get("cross_modal", {}) or {}
    # 声学状态机：优先 acoustic_state_change，否则 voice_state（稳定态）。
    state = out.get("acoustic_state_change") or out.get("voice_state")
    node = {
        "audio_timestamp": 12.5,
        "audio_kind": "audio_telephone_persistent",
        "audio_score": 0.85,
        "audio_confidence": 0.88,
        "audio_labels": ["telephone_interaction", "speech"],
        "audio_source_segment_ids": ["seg-audio"],
    }
    if state:
        node["audio_acoustic_state_change"] = state
    if "voice_stress_score" in out:
        node["audio_voice_stress_score"] = out["voice_stress_score"]
    for gkey, ckey in (
        ("f0_delta", "audio_f0_delta"),
        ("speech_rate_delta", "audio_speech_rate_delta"),
        ("energy_delta", "audio_energy_delta"),
    ):
        if gkey in out:
            node[ckey] = out[gkey]
    if cross.get("vision_ref"):
        # 真实跨模态关联：视觉 phone_interaction ↔ 音频声学偏离。
        node["audio_related_visual_ref"] = f"visual:{cross['vision_ref']}:seg-0"
    return node


def _golden_variant(case_id: str) -> dict:
    with open(_GOLDEN_MANIFEST, encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)
    for variant in manifest["variants"]:
        if variant["id"] == case_id:
            return variant
    raise AssertionError(f"golden manifest 缺少 variant {case_id!r}")


def test_golden_e2e_case_b_carries_acoustic_state_and_supports(tmp_path: Path):
    """golden case_b 经真实 loader+renderer：状态机 / 0.72 / deltas / SUPPORTS 全入卡。"""
    node = _golden_audio_node(_golden_variant("case_b"))
    d = make_artifacts(tmp_path / "g", audio_evidence=[node])
    projection, descriptor = load_case_presentation(d)
    html = render_case_viewer(projection, descriptor, audio_base_dir=None)
    # 状态机逐相真实呈现。
    assert "NORMAL → ATTENTION → AROUSAL → STRESS" in html
    # 量化声学指标真实呈现（设计契约 §6 事实层）。
    assert "voice_stress_score = 0.72" in html
    assert "F0 Δ=0.24" in html
    assert "语速 Δ=0.29" in html
    assert "能量 Δ=0.23" in html
    # 跨模态诚实综合（视觉通话 + 音频声学偏离 → SUPPORTS）。
    assert "CROSS_MODAL: SUPPORTS" in html
    # 红线免责声明在场。
    assert "不推导当事人心理或诈骗判定" in html


def test_golden_e2e_case_a_stable_no_fraud(tmp_path: Path):
    """golden case_a（稳定态 NORMAL）：卡陈述稳定，不声称偏离 / 诈骗判定。"""
    node = _golden_audio_node(_golden_variant("case_a"))
    d = make_artifacts(tmp_path / "g", audio_evidence=[node])
    projection, descriptor = load_case_presentation(d)
    html = render_case_viewer(projection, descriptor, audio_base_dir=None)
    assert "稳定" in html
    assert "STRESS" not in html  # case_a 稳定态无 STRESS 跃迁
    assert "CROSS_MODAL: SUPPORTS" in html  # cross_modal 声明仍由真实关联驱动
    assert "不推导当事人心理或诈骗判定" in html
