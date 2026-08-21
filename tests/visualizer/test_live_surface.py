"""Phase 1 单元测试：``live_surface`` 纯函数模块。

覆盖：
- L0 Audio Health 三值状态机的所有判定分支；
- Risk Reason 白名单校验（含产品预写文案拦截）；
- L5 Provenance 快捷入口 HTML 生成；
- 文案铁律：禁止"音频正常" / "声学状态变化 + 电话交互"。
"""

from __future__ import annotations

import pytest

from home_perception.visualizer.viewer.live_surface import (
    AudioHealth,
    RiskReason,
    compute_audio_health,
    extract_risk_reasons,
    render_why_believe_link,
)

# ============================================================
# L0 Audio Health 三值状态机
# ============================================================


def test_audio_health_unavailable_when_scenario_no_audio_track():
    """场景本身无音频轨 → UNAVAILABLE（优先于所有其他判定）。"""
    state = compute_audio_health(
        last_audio_event_ts_ms=1000,
        now_ms=2000,
        scenario_has_audio_track=False,
    )
    assert state.state == AudioHealth.UNAVAILABLE
    assert state.css_class == "audio-na"


def test_audio_health_no_recent_event_when_never_received():
    """从未收到 audio event（last_audio_event_ts_ms=None）→ NO_RECENT_EVENT。"""
    state = compute_audio_health(
        last_audio_event_ts_ms=None,
        now_ms=2000,
        scenario_has_audio_track=True,
    )
    assert state.state == AudioHealth.NO_RECENT_EVENT
    assert state.css_class == "audio-stale"


def test_audio_health_no_recent_event_when_stale():
    """最近事件超过 5s → NO_RECENT_EVENT（可能是静默期，不表设备离线）。"""
    state = compute_audio_health(
        last_audio_event_ts_ms=1000,
        now_ms=8000,  # 7000ms > 5000ms 阈值
        scenario_has_audio_track=True,
    )
    assert state.state == AudioHealth.NO_RECENT_EVENT
    assert "静默期" in state.detail


def test_audio_health_recent_event_when_fresh():
    """最近事件 < 5s → RECENT_EVENT。"""
    state = compute_audio_health(
        last_audio_event_ts_ms=1000,
        now_ms=2000,  # 1000ms < 5000ms 阈值
        scenario_has_audio_track=True,
    )
    assert state.state == AudioHealth.RECENT_EVENT
    assert state.css_class == "audio-recent"


def test_audio_health_unavailable_overrides_other_states():
    """UNAVAILABLE 优先级最高（即使有最近事件，硬件无音频也强制 UNAVAILABLE）。"""
    # 有最近事件但场景无音频轨
    state = compute_audio_health(
        last_audio_event_ts_ms=1000,
        now_ms=2000,
        scenario_has_audio_track=False,
    )
    assert state.state == AudioHealth.UNAVAILABLE


def test_audio_health_label_does_not_claim_normal():
    """Audio Health 文案铁律：禁止使用"音频正常"（避免伪造健康度）。"""
    for audio_state in AudioHealth:
        ui = compute_audio_health(
            last_audio_event_ts_ms=None if audio_state == AudioHealth.NO_RECENT_EVENT else 1000,
            now_ms=2000,
            scenario_has_audio_track=audio_state != AudioHealth.UNAVAILABLE,
        )
        assert "音频正常" not in ui.label
        assert "音频中断" not in ui.label


def test_audio_health_deterministic():
    """纯函数：相同输入 → 相同输出（VM-8 幂等）。"""
    args = {"last_audio_event_ts_ms": 1000, "now_ms": 2000, "scenario_has_audio_track": True}
    s1 = compute_audio_health(**args)
    s2 = compute_audio_health(**args)
    assert s1 == s2


# ============================================================
# Risk Reason 追源（白名单校验）
# ============================================================


def test_risk_reason_allowlist_contains_decision_policy_keys():
    """白名单包含所有 ``routing_table`` 第三元素（5 个核心 reason）。"""
    expected = {
        "异常停留",
        "重复访问",
        "未在白名单",
        "异常时段访问",
        "多风险规则同时命中",
    }
    actual = {r.value for r in RiskReason}
    assert actual == expected


def test_risk_reason_clean_when_all_in_allowlist():
    """所有 reason 均在白名单 → ``is_clean=True``。"""
    r = extract_risk_reasons(["未在白名单", "异常停留"])
    assert r.is_clean
    assert r.valid_reasons == ("未在白名单", "异常停留")
    assert r.rejected_reasons == ()


def test_risk_reason_rejects_product_prewritten_text():
    """产品预写文案（"声学状态变化 + 电话交互"）→ 拒绝。"""
    r = extract_risk_reasons(["声学状态变化 + 电话交互"])
    assert not r.is_clean
    assert r.rejected_reasons == ("声学状态变化 + 电话交互",)
    assert r.valid_reasons == ()


def test_risk_reason_rejects_live_stream_js_predifined_keys():
    """``live_stream.js`` ``_REASON_ZH`` 预定义键（acoustic_state_change 等）→ 拒绝。"""
    r = extract_risk_reasons(
        ["acoustic_state_change", "telephone_interaction", "voice_stress_elevated"]
    )
    assert not r.is_clean
    assert r.rejected_reasons == (
        "acoustic_state_change",
        "telephone_interaction",
        "voice_stress_elevated",
    )


def test_risk_reason_partial_clean():
    """部分 valid / 部分 rejected → 返回结果正确分类。"""
    r = extract_risk_reasons(["未在白名单", "声学状态变化", "重复访问"])
    assert not r.is_clean
    assert r.valid_reasons == ("未在白名单", "重复访问")
    assert r.rejected_reasons == ("声学状态变化",)


def test_risk_reason_empty_input():
    """空输入 / None → 返回空结果（is_clean=True）。"""
    r1 = extract_risk_reasons([])
    r2 = extract_risk_reasons(None)
    assert r1.is_clean and r2.is_clean
    assert r1.valid_reasons == () and r1.rejected_reasons == ()
    assert r2.valid_reasons == () and r2.rejected_reasons == ()


def test_risk_reason_does_not_raise_on_garbage():
    """Fail-soft：非字符串 reason（意外类型）不抛异常，归入 rejected。"""
    r = extract_risk_reasons(["未在白名单", None, 42, "异常停留"])  # type: ignore[list-item]
    assert r.valid_reasons == ("未在白名单", "异常停留")
    assert "None" in r.rejected_reasons  # str(None) == 'None'
    assert "42" in r.rejected_reasons


# ============================================================
# L5 Provenance 快捷入口
# ============================================================


def test_why_believe_link_contains_anchor():
    """L5 快捷入口含正确锚点（指向 fs-details-{sid}）。"""
    link = render_why_believe_link("sw_demo_v1")
    assert "fs-details-sw_demo_v1" in link
    assert "为什么相信" in link
    assert "🔍" in link


def test_why_believe_link_is_anchor_not_button():
    """L5 快捷入口使用 ``<a href="#...">``（浏览器原生锚点，无 JS 依赖）。"""
    link = render_why_believe_link("test_sid")
    assert "<a " in link
    assert "href='#fs-details-test_sid'" in link


def test_why_believe_link_has_css_class():
    """L5 快捷入口含 CSS class（前端样式钩子）。"""
    link = render_why_believe_link("test_sid")
    assert "why-believe-link" in link


# ============================================================
# 边界：与场景 ID 字符串兼容性
# ============================================================


@pytest.mark.parametrize("scenario_id", ["a", "test", "sw_demo_v1", "case_001"])
def test_why_believe_link_works_for_various_scenario_ids(scenario_id):
    """L5 快捷入口对各种 scenario_id 字符串均生成正确锚点。"""
    link = render_why_believe_link(scenario_id)
    assert f"fs-details-{scenario_id}" in link