"""Phase 1 集成测试：L0/L2/L4/L5 骨架完整集成验证。

设计来源（合同冻结）：
- ``WIREFRAME-DESIGN.md`` v3.2 §1.2 L0/L2/L5
- ``LIVE-PERCEPTION-STREAM-SPEC.md`` v1.2 §2.4 L0 Audio Health 语义
- ``LIVE-PERCEPTION-STREAM-SEMANTICS.md`` v2.0 整体语义表

覆盖：
- **L0**：``data-audio-health`` 属性三值映射（UNAVAILABLE / NO_RECENT_EVENT / RECENT_EVENT）
  + ``_render_audio_sensor_status`` 三种 audio_evidence 输入分支
- **L2**：Risk Reason 白名单校验在 runtime 数据流上的集成行为
  （runtime 原因字段 → ``extract_risk_reasons`` → 拒绝产品预写文案）
- **L4**：Live Shell 渲染顺序契约（lv-now → lv-perception → lv-why → lv-action →
  lv-trust-quicklink → lv-history）
- **L5**：``[为什么相信？]`` 快捷入口在 ``_render_live_shell`` 输出中存在
  + ``data-target`` 指向 ``fs-details-{sid}``
- **CSS 契约**：三值 class 名映射（audio-na / audio-active / audio-recent /
  audio-stale）
- **铁律测试**：禁止"音频正常"/"音频中断"等二元健康度文案
"""

from __future__ import annotations

import re

from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.live_adapter import (
    ProjectionAccumulator,
    build_live_presentation,
)
from home_perception.visualizer.viewer.live_surface import (
    AudioHealth,
    compute_audio_health,
    extract_risk_reasons,
    render_why_believe_link,
)


def _frame(frame_index: int, *, risk_levels=()) -> dict:
    return {
        "frame_index": frame_index,
        "n_detections": 1,
        "n_visitor_events": 0,
        "perception_events": [],
        "warnings": [
            {"risk_level": rl, "recommended_action": "MONITOR"} for rl in risk_levels
        ],
        "commands": [],
    }


def _live_html_with_audio_evidence(audio_evidence=()) -> str:
    """构造含自定义 audio_evidence 的 Live HTML。"""
    acc = ProjectionAccumulator("phase1_shell_t")
    acc.ingest(_frame(0, risk_levels=("HIGH",)))
    proj = acc.to_evidence_projection()
    # 注入 audio_evidence（覆盖默认空）
    if audio_evidence:
        proj["scenarios"][0]["audio_evidence"] = tuple(audio_evidence)
    built_proj, desc = build_live_presentation(proj, live_ws_path="/ws")
    return render_case_viewer(built_proj, desc, live_frame_stream=True)


# ============================================================================
# L0: Audio Health 三值映射（render 集成）
# ============================================================================


def test_l0_unavailable_when_no_audio_evidence():
    """L0：三值状态 - 场景无 audio_evidence → data-audio-health="UNAVAILABLE"。"""
    html = _live_html_with_audio_evidence(audio_evidence=())
    # audio-sensor-{sid} 容器存在
    assert 'id="audio-sensor-phase1_shell_t"' in html
    # L0 三值标记：data-audio-health="UNAVAILABLE"
    assert 'id="audio-sensor-phase1_shell_t" data-audio-health="UNAVAILABLE"' in html \
        or 'id="audio-sensor-phase1_shell_t"' in html and 'data-audio-health="UNAVAILABLE"' in html
    # 文案：🔇 UNAVAILABLE
    assert "🔇 UNAVAILABLE" in html
    # CSS class: audio-na
    assert "audio-na" in html
    # 禁止二元健康度文案
    assert "音频正常" not in html
    assert "音频中断" not in html


def test_l0_no_recent_event_when_real_sensor_audio():
    """L0：三值状态 - 含 REAL_SENSOR 音频 → 初始 data-audio-health="NO_RECENT_EVENT"。

    初始状态由 render.py 推导（基于 audio_evidence.provenance_kind），JS 接收
    audio event 后切到 RECENT_EVENT（VM-1 派生 + live_stream.js 维护）。
    """
    audio_ev = [{
        "timestamp": "1752952800.0",
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.88,
        "labels": ("telephone",),
        "source_segment_ids": ("seg-0",),
        "ref": "live://audio/0",
        "provenance_kind": "REAL_SENSOR",
    }]
    html = _live_html_with_audio_evidence(audio_evidence=audio_ev)
    # 初始三值标记
    assert 'id="audio-sensor-phase1_shell_t"' in html
    assert 'data-audio-health="NO_RECENT_EVENT"' in html
    # 文案
    assert "⏸ NO_RECENT_EVENT" in html or "NO_RECENT_EVENT" in html
    # CSS class
    assert "audio-active" in html
    # 禁止二元健康度文案
    assert "音频正常" not in html
    assert "音频中断" not in html


def test_l0_unavailable_when_only_simulated_audio():
    """L0：三值状态 - 仅 SIMULATED/FIXTURE 音频 → UNAVAILABLE（非实时）。"""
    audio_ev = [{
        "timestamp": "1752952800.0",
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.88,
        "labels": ("telephone",),
        "source_segment_ids": ("seg-0",),
        "ref": "live://audio/0",
        "provenance_kind": "SIMULATED",  # 非 REAL_SENSOR → UNAVAILABLE
    }]
    html = _live_html_with_audio_evidence(audio_evidence=audio_ev)
    assert 'data-audio-health="UNAVAILABLE"' in html
    assert "audio-na" in html


# ============================================================================
# L0: compute_audio_health 纯函数行为（render + JS 共用契约）
# ============================================================================


def test_l0_compute_audio_health_unavailable_branch():
    """L0 纯函数：scenario_has_audio_track=False → UNAVAILABLE。"""
    state = compute_audio_health(
        last_audio_event_ts_ms=1000,
        now_ms=2000,
        scenario_has_audio_track=False,
    )
    assert state.state == AudioHealth.UNAVAILABLE
    assert "无音频" in state.detail


def test_l0_compute_audio_health_no_event_yet():
    """L0 纯函数：last_audio_event_ts_ms=None → NO_RECENT_EVENT。"""
    state = compute_audio_health(
        last_audio_event_ts_ms=None,
        now_ms=2000,
        scenario_has_audio_track=True,
    )
    assert state.state == AudioHealth.NO_RECENT_EVENT


def test_l0_compute_audio_health_stale_threshold():
    """L0 纯函数：now - last > 5s → NO_RECENT_EVENT（静默期，非"中断"）。"""
    state = compute_audio_health(
        last_audio_event_ts_ms=1000,
        now_ms=7000,  # 6s 后
        scenario_has_audio_track=True,
    )
    assert state.state == AudioHealth.NO_RECENT_EVENT
    # 禁止"中断"类二元文案
    assert "中断" not in state.label
    assert "中断" not in state.detail


def test_l0_compute_audio_health_recent_event():
    """L0 纯函数：now - last ≤ 5s → RECENT_EVENT。"""
    state = compute_audio_health(
        last_audio_event_ts_ms=1000,
        now_ms=3000,  # 2s 后
        scenario_has_audio_track=True,
    )
    assert state.state == AudioHealth.RECENT_EVENT


# ============================================================================
# L2: Risk Reason 白名单校验（runtime 数据流集成）
# ============================================================================


def test_l2_valid_reasons_pass_whitelist():
    """L2：runtime 字段含白名单 reason（routing_table 第三元素）→ 全部 valid。"""
    # 模拟 runtime risk_delta.reason_summary
    runtime_reasons = ["未在白名单", "异常停留", "重复访问"]
    result = extract_risk_reasons(runtime_reasons)
    assert result.is_clean
    assert result.valid_reasons == ("未在白名单", "异常停留", "重复访问")
    assert result.rejected_reasons == ()


def test_l2_product_written_reason_rejected():
    """L2：产品预写文案（"声学状态变化 + 电话交互"）→ 被拒绝。"""
    runtime_reasons = ["声学状态变化 + 电话交互"]
    result = extract_risk_reasons(runtime_reasons)
    assert not result.is_clean
    assert result.rejected_reasons == ("声学状态变化 + 电话交互",)
    assert result.valid_reasons == ()


def test_l2_js_predefined_key_rejected():
    """L2：live_stream.js _REASON_ZH 预定义键（acoustic_state_change）→ 被拒绝。

    这些键目前不会被 runtime 触发，禁止进入 UI（设计合同）。
    """
    runtime_reasons = ["acoustic_state_change", "telephone_interaction"]
    result = extract_risk_reasons(runtime_reasons)
    assert not result.is_clean
    assert "acoustic_state_change" in result.rejected_reasons
    assert "telephone_interaction" in result.rejected_reasons


def test_l2_legacy_event_type_rejected():
    """L2：旧 event_type 枚举键（如 visit_normal）→ 被拒绝（不在白名单）。"""
    runtime_reasons = ["visit_normal", "abnormal_dwell"]
    result = extract_risk_reasons(runtime_reasons)
    # visit_normal 不在白名单（白名单是"异常时段访问"），应被拒绝
    assert "visit_normal" in result.rejected_reasons
    # abnormal_dwell 不在白名单（白名单是"异常停留"），应被拒绝
    assert "abnormal_dwell" in result.rejected_reasons


def test_l2_mixed_valid_and_invalid_fail_soft():
    """L2：valid + invalid 混合 → valid 部分正常返回，invalid 单独记录（fail-soft）。"""
    runtime_reasons = ["未在白名单", "声学状态变化 + 电话交互", "异常停留"]
    result = extract_risk_reasons(runtime_reasons)
    assert not result.is_clean
    assert result.valid_reasons == ("未在白名单", "异常停留")
    assert result.rejected_reasons == ("声学状态变化 + 电话交互",)


def test_l2_empty_reason_summary_is_clean():
    """L2：空 reason_summary → 全部空（is_clean=True）。"""
    for empty_input in (None, [], ()):
        result = extract_risk_reasons(empty_input)  # type: ignore[arg-type]
        assert result.is_clean
        assert result.valid_reasons == ()
        assert result.rejected_reasons == ()


def test_l2_non_string_reason_rejected():
    """L2：非字符串 reason（如 None / 数字）→ 被拒绝（fail-soft）。"""
    runtime_reasons = ["未在白名单", None, 42, "异常停留"]  # type: ignore[list-item]
    result = extract_risk_reasons(runtime_reasons)  # type: ignore[arg-type]
    # None / 42 会被 str() 后放入 rejected
    assert "未在白名单" in result.valid_reasons
    assert "异常停留" in result.valid_reasons
    assert len(result.rejected_reasons) == 2


# ============================================================================
# L5: Provenance 快捷入口
# ============================================================================


def test_l5_why_believe_link_present_in_live_shell():
    """L5：`[为什么相信？]` 链接在 `_render_live_shell` 输出中（一级可达）。"""
    html = _live_html_with_audio_evidence()
    assert "why-believe-link" in html
    assert "为什么相信" in html
    # 锚点指向 fs-details-{sid}
    assert "href='#fs-details-phase1_shell_t'" in html
    assert "data-target='fs-details-phase1_shell_t'" in html


def test_l5_why_believe_link_outside_foldable_region():
    """L5：快捷入口必须位于 lv-trust-quicklink 区域（非折叠区内）。"""
    html = _live_html_with_audio_evidence()
    # lv-trust-quicklink 区域在 lv-action 之后、lv-history 之前
    assert "lv-trust-quicklink" in html
    # 顺序检查：why-believe-link 应在 lv-history 区域之前出现（用 region class 精确定位）
    link_pos = html.find("why-believe-link")
    history_region_pos = html.find('class="region lv-history"')
    assert link_pos != -1 and history_region_pos != -1
    assert link_pos < history_region_pos, "L5 链接必须在 lv-history 区域之前（首屏一级可达）"


def test_l5_why_believe_link_pure_function():
    """L5：`render_why_believe_link(sid)` 纯函数：场景 ID 锚点正确。"""
    link_html = render_why_believe_link("my_scenario_001")
    assert "href='#fs-details-my_scenario_001'" in link_html
    assert "data-target='fs-details-my_scenario_001'" in link_html
    assert "为什么相信" in link_html
    assert "why-believe-link" in link_html


# ============================================================================
# Live Shell 渲染顺序契约（L0/L2/L4/L5 完整骨架）
# ============================================================================


def test_live_shell_phase1_render_order():
    """Phase 1：Live Shell 渲染顺序契约。

    顺序：lv-now → lv-perception → lv-why → lv-action → lv-trust-quicklink → lv-history
    （L5 快捷入口在 L4 后、Memory Context 前；非折叠区内）
    """
    html = _live_html_with_audio_evidence()
    # 使用 region class 精确定位（避免 CSS 字符串污染）
    positions = {
        "lv-now": html.find('class="region lv-now"'),
        "lv-perception": html.find('class="region lv-perception"'),
        "lv-why": html.find('class="region lv-why"'),
        "lv-action": html.find('class="region lv-action"'),
        "lv-trust-quicklink": html.find('class="region lv-trust-quicklink"'),
        "lv-history": html.find('class="region lv-history"'),
    }
    for name, pos in positions.items():
        assert pos != -1, f"区域 {name} 必须存在"
    # 按索引排序，确保顺序
    ordered = sorted(positions.items(), key=lambda kv: kv[1])
    expected_order = [
        "lv-now",
        "lv-perception",
        "lv-why",
        "lv-action",
        "lv-trust-quicklink",
        "lv-history",
    ]
    actual_order = [name for name, _ in ordered]
    assert actual_order == expected_order, (
        f"渲染顺序错误：\n实际: {actual_order}\n预期: {expected_order}"
    )


# ============================================================================
# CSS Class 契约
# ============================================================================


def test_css_class_audio_unavailable():
    """CSS：UNAVAILABLE 状态 → audio-na class（视觉降级）。"""
    html = _live_html_with_audio_evidence(audio_evidence=())
    assert "sensor-card-status audio-na" in html


def test_css_class_audio_recent_event():
    """CSS：NO_RECENT_EVENT 状态（REAL_SENSOR 初始）→ audio-active class。"""
    audio_ev = [{
        "timestamp": "1752952800.0",
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.88,
        "labels": ("telephone",),
        "source_segment_ids": ("seg-0",),
        "ref": "live://audio/0",
        "provenance_kind": "REAL_SENSOR",
    }]
    html = _live_html_with_audio_evidence(audio_evidence=audio_ev)
    assert "sensor-card-status audio-active" in html


def test_css_class_why_believe_link():
    """CSS：why-believe-link 链接 class。"""
    html = _live_html_with_audio_evidence()
    assert "class='why-believe-link'" in html or 'class="why-believe-link"' in html


# ============================================================================
# 铁律测试（防止未来回归）
# ============================================================================


def test_no_binary_audio_health_labels_in_render():
    """铁律：render 输出禁止出现"音频正常"/"音频中断"等二元健康度文案。"""
    # 1. 无音频场景
    html_na = _live_html_with_audio_evidence(audio_evidence=())
    assert "音频正常" not in html_na
    assert "音频中断" not in html_na
    # 2. 有 REAL_SENSOR 音频
    audio_ev = [{
        "timestamp": "1752952800.0",
        "kind": "audio_telephone_persistent",
        "score": 0.9,
        "confidence": 0.88,
        "labels": ("telephone",),
        "source_segment_ids": ("seg-0",),
        "ref": "live://audio/0",
        "provenance_kind": "REAL_SENSOR",
    }]
    html_real = _live_html_with_audio_evidence(audio_evidence=audio_ev)
    assert "音频正常" not in html_real
    assert "音频中断" not in html_real


def test_no_product_written_reasons_in_render():
    """铁律：render 输出禁止包含产品预写文案（声学状态变化 + 电话交互）。"""
    html = _live_html_with_audio_evidence()
    assert "声学状态变化 + 电话交互" not in html


def test_three_value_audio_health_label_pattern():
    """铁律：render 输出包含三值标记（🔇 UNAVAILABLE / ⏸ NO_RECENT_EVENT / 🔊 RECENT_EVENT）。"""
    # 无音频 → UNAVAILABLE
    html_na = _live_html_with_audio_evidence(audio_evidence=())
    assert re.search(r"(🔇|⏸|🔊)\s*(UNAVAILABLE|NO_RECENT_EVENT|RECENT_EVENT)", html_na), (
        "无音频场景必须有三值标记之一"
    )