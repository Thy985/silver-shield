"""P1-B story_chapters 分幕派生契约测试（Artifact Story Replay）。

覆盖：
- 事实驱动分幕：perception/decision/notification/memory → incident/decision/closure/risk；
- 省略空幕（无对应事实的幕不编造，AC-12）；
- context 幕：timeline 首个节点非叙事四 stage 时派生（开场锚）；
- focus_refs 绑定真实节点 ref + display_copy 只陈述事实（不判定风险）。

不依赖 torch/cv2（纯 stdlib + 投影契约 fixture）。
"""

from __future__ import annotations

from home_perception.visualizer.viewer.story_chapters import build_story_chapters


def _node(stage: str, idx: int) -> dict:
    return {
        "timestamp": f"S{idx + 1}",
        "stage": stage,
        "type": "stage",
        "summary": f"stage {stage}",
        "verdict": "INFO",
        "modality": "VISION",
        "provenance_kind": "SIMULATED",
        "ref": f"sw_t1.canonical.json#stages[{idx}]",
    }


def _scenario(*stages: str, **kw) -> dict:
    timeline = tuple(_node(s, i) for i, s in enumerate(stages))
    return {
        "scenario_id": "sw_t1",
        "timeline": timeline,
        "decision_evidence": kw.get("decision_evidence", ()),
        "audio_evidence": kw.get("audio_evidence", ()),
        "memory_episodes": kw.get("memory_episodes", ()),
        "intervention_dispatch": kw.get("intervention_dispatch", ()),
        "counts": kw.get(
            "counts",
            {"perception_events": 2, "decision_traces": 1, "commands": 1},
        ),
    }


def test_build_story_chapters_incident_decision_closure():
    """perception+decision+notification → incident/decision/closure 三幕（无 context/risk）。"""
    chapters = build_story_chapters(_scenario("perception", "decision", "notification"))
    ids = [c["chapter_id"] for c in chapters]
    assert ids == ["incident", "decision", "closure"]
    # incident 幕绑定 perception 节点 + counts 事实陈述
    assert chapters[0]["start_idx"] == 0
    assert "2 个感知事件" in chapters[0]["display_copy"]
    assert chapters[0]["focus_refs"] == ("sw_t1.canonical.json#stages[0]",)
    # decision 幕
    assert chapters[1]["start_idx"] == 1
    assert "1 条决策轨迹" in chapters[1]["display_copy"]
    # closure 幕
    assert chapters[2]["start_idx"] == 2
    assert "1 条指令" in chapters[2]["display_copy"]


def test_build_story_chapters_memory_risk():
    """memory stage → risk 幕（历史叠加）。"""
    chapters = build_story_chapters(_scenario("memory", "decision"))
    ids = [c["chapter_id"] for c in chapters]
    assert ids == ["risk", "decision"]


def test_build_story_chapters_context_when_non_stage_first():
    """timeline 首个节点非叙事四 stage（如 observability）→ context 幕（开场锚）。"""
    chapters = build_story_chapters(_scenario("observability", "perception"))
    ids = [c["chapter_id"] for c in chapters]
    assert ids[0] == "context"
    assert ids[1] == "incident"


def test_build_story_chapters_empty_timeline():
    """空 timeline → 空元组（无叙事，AC-12 不编造）。"""
    assert build_story_chapters({"scenario_id": "x", "timeline": ()}) == ()
    assert build_story_chapters("not-a-dict") == ()


def test_build_story_chapters_focus_refs_bind_real_nodes():
    """focus_refs 绑定 audio_evidence + memory_episodes 的真实 ref（不编造）。"""
    audio = [{"kind": "audio_voice_raised", "ref": "sw_t1.canonical.json#audio[0]", "timestamp": 1.0,
              "score": 0.8, "confidence": 0.9, "labels": ["speech"], "source_segment_ids": ["seg-1"]}]
    mem = [{"memory_record_id": "ep-1", "ref": "sw_t1.canonical.json#episodes[0]"}]
    chapters = build_story_chapters(
        _scenario("perception", "memory", audio_evidence=audio, memory_episodes=mem)
    )
    incident = next(c for c in chapters if c["chapter_id"] == "incident")
    assert "sw_t1.canonical.json#audio[0]" in incident["focus_refs"]
    risk = next(c for c in chapters if c["chapter_id"] == "risk")
    assert "sw_t1.canonical.json#episodes[0]" in risk["focus_refs"]


def test_render_story_nav_renders_chapters():
    """_render_story_nav：渲染章节按钮（data-start/refs/copy 服务端派生）+ 叙述文案；
    无叙事 → 空串。"""
    from home_perception.visualizer.viewer.render import _render_story_nav

    html = _render_story_nav(_scenario("perception", "decision", "notification"))
    assert "story-chapters" in html
    assert "story-chapter" in html
    assert "data-refs=" in html
    assert "data-copy=" in html
    assert "story-copy" in html
    # 无叙事（空 timeline）→ 空串
    assert _render_story_nav({"scenario_id": "x", "timeline": ()}) == ""
