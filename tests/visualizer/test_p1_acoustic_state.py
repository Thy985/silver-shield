"""P1 Acoustic State（telephone_risk）验收测试：声学状态变化叙事卡。

验收核心（设计文档 §6 / §10 红线）：
- Viewer 展示「声学状态变化」，绝不推导 STRESS / 诈骗 / 当事人心理（VM-9 无 ASR/LLM）；
- 跨模态诚实呈现：视觉通话 + 音频声学偏离 → CROSS_MODAL: SUPPORTS → 持续观察，
  而非「电话 + 声音 = 诈骗」；
- 仅 telephone 类音频场景注入该卡（VM-11 不新增无关事实）；无音频 → 不渲染（AC-12）；
- 纯展示层派生（VM-1）：只用既有 audio_evidence 字段 + recommended_actions，零 runtime 改动。

不依赖 torch/cv2（纯 stdlib + 投影契约 fixture），可在 torch-free 环境跑。
"""

from __future__ import annotations

from pathlib import Path

from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.artifact_source import load_case_presentation
from home_perception.visualizer.viewer.render import _render_acoustic_state_card

from .conftest import make_artifacts

# 两个真实音频符号：telephone(相对0s) + voice_raised(相对150s)。
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


# ---------------------------------------------------------------------------
# 单元测试：_render_acoustic_state_card 纯派生逻辑
# ---------------------------------------------------------------------------


def _scenario(audio_evidence, recommended_actions=(), command_types=()):
    return {
        "scenario_id": "sw_t1",
        "ok": True,
        "audio_evidence": tuple(audio_evidence),
        "recommended_actions": tuple(recommended_actions),
        "command_types": tuple(command_types),
    }


def test_no_audio_returns_empty():
    """AC-12：无音频证据不渲染声学状态卡。"""
    assert _render_acoustic_state_card(_scenario([])) == ""


def test_non_telephone_audio_returns_empty():
    """非电话场景不注入声学状态卡（VM-11 不新增无关事实）。"""
    node = {
        "kind": "audio_speech_rapid",
        "score": 0.8,
        "confidence": 0.8,
        "labels": ["speech"],
    }
    assert _render_acoustic_state_card(_scenario([node])) == ""


def test_telephone_no_deviation_no_fraud_claim():
    """telephone 但无偏离：陈述声学状态变化，绝不推导诈骗/心理。"""
    node = {
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.9,
        "labels": ["telephone"],
    }
    html = _render_acoustic_state_card(_scenario([node]))
    assert "声学状态变化（非诈骗判定）" in html
    # 导语不声称偏离。
    assert "并检测到声学偏离" not in html
    # 红线守卫：绝不输出心理/诈骗判定。
    assert "老人" not in html
    assert "STRESS" not in html
    assert "诈骗" in html  # 仅以「不推导诈骗判定」的否定形式出现
    assert "不推导当事人心理或诈骗判定" in html


def test_telephone_with_deviation_and_cross_modal():
    """telephone + 偏离 + 真实跨模态关联：SUPPORTS → 持续观察。"""
    tel = {
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.9,
        "labels": ["telephone"],
        "related_visual_ref": "visual:phone_interaction:seg-0",
    }
    voice = {
        "kind": "audio_voice_raised",
        "score": 0.85,
        "confidence": 0.88,
        "labels": ["speech"],
    }
    html = _render_acoustic_state_card(_scenario([tel, voice]))
    assert "并检测到声学偏离" in html
    assert "CROSS_MODAL: SUPPORTS" in html
    assert "持续观察" in html  # 默认 recommended_actions=NOTIFY_FAMILY → 持续观察
    assert "visual:phone_interaction:seg-0" not in html  # 不泄露内部 ref 原值


def test_telephone_no_cross_modal_omits_supports():
    """telephone + 偏离，但无 related_visual_ref：不编造跨模态关系。"""
    tel = {
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.9,
        "labels": ["telephone"],
    }
    voice = {
        "kind": "audio_voice_raised",
        "score": 0.85,
        "confidence": 0.88,
        "labels": ["speech"],
    }
    html = _render_acoustic_state_card(_scenario([tel, voice]))
    assert "CROSS_MODAL: SUPPORTS" not in html
    assert "声学状态变化" in html


def test_escalation_decision_maps_to_elevated_attention():
    """升级推荐动作 → 决策文案「提高关注 / 升级社区协同处置」，仍不推导诈骗。

    决策文案随 CROSS_MODAL: SUPPORTS 结论呈现（设计文档 §6），故注入真实跨模态关联。
    """
    tel = {
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.9,
        "labels": ["telephone"],
        "related_visual_ref": "visual:phone_interaction:seg-0",
    }
    html = _render_acoustic_state_card(
        _scenario([tel], recommended_actions=["ESCALATE_COMMUNITY"])
    )
    assert "提高关注 / 升级社区协同处置" in html
    assert "诈骗" in html  # 仍以否定形式出现
    assert "STRESS" not in html


# ---------------------------------------------------------------------------
# 集成测试：经完整投影链路注入首屏音频面板
# ---------------------------------------------------------------------------


def _projection(artifacts_dir: Path):
    return load_case_presentation(artifacts_dir)


def test_integration_card_injected_into_audio_panel(tmp_path: Path):
    """端到端：telephone + voice_raised 投影后，声学状态卡出现在首屏音频面板内。"""
    d = make_artifacts(tmp_path / "a", audio_evidence=_AUDIO)
    projection, descriptor = _projection(d)
    html = render_case_viewer(projection, descriptor, audio_base_dir=None)
    # 卡在音频面板标题之下、音频感知卡片之上。
    assert "系统听到了什么（音频感知）" in html
    assert "声学状态变化（非诈骗判定）" in html
    # 默认场景无 related_visual_ref（fixture 未注入）→ 不出现 SUPPORTS。
    assert "CROSS_MODAL: SUPPORTS" not in html


def test_integration_cross_modal_supports_in_panel(tmp_path: Path):
    """注入 related_visual_ref 后，SUPPORTS 陈述出现在首屏。"""
    audio = [
        {**_AUDIO[0], "audio_related_visual_ref": "visual:phone_interaction:seg-0"},
        dict(_AUDIO[1]),
    ]
    d = make_artifacts(tmp_path / "a", audio_evidence=audio)
    projection, descriptor = _projection(d)
    html = render_case_viewer(projection, descriptor, audio_base_dir=None)
    assert "CROSS_MODAL: SUPPORTS" in html
    assert "持续观察" in html
