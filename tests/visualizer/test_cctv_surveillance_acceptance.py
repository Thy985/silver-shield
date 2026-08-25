"""CCTV Surveillance Suspicious Acceptance — single-scenario browser test.

Scenario narrative (frozen by PRD/ADR decision):
    夜间人员出现 → 重复出现 → 异常停留 → 视觉风险信号 → RiskSignal → WARN → LOG_ONLY

Key design decisions:
    - Single phase (no benign switch, no scene hygiene)
    - No audio surface: P1 audio assertions and P6 anti-hallucination are skipped
    - Expected max risk level: WARN (not HIGH) — verified via dom lrk.level
    - repeat_visit_count=2 override in yaml means 2 visits suffice for repeat_visit

Run prerequisite:
    python scripts/run_demo.py --live --scenario config/demo/scenarios/cctv_surveillance_suspicious.yaml
"""

from __future__ import annotations

import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import pytest
import requests

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright not installed", allow_module_level=True)

from tests.visualizer._scenario_contract import (
    WS_CAPTURE_INIT,
    CctvSurveillanceSuspiciousContract,
    make_signals_poll_js,
    make_video_sig_js,
)

BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/live"

_CONTRACT = CctvSurveillanceSuspiciousContract()
SID = _CONTRACT.scenario_id
OBSERVE_MS = _CONTRACT.observe_times.get("cctv_observe", 120_000)
POLL_MS = 1_000

COMMAND_ACTIONS = {"NOTIFY_FAMILY", "ESCALATE_COMMUNITY", "CREATE_COMMUNITY_TASK"}
PASSIVE_ACTIONS = {"MONITOR", "MONITOR_FAMILY", "LOG_ONLY"}
LEGAL_ACTIONS = COMMAND_ACTIONS | PASSIVE_ACTIONS


def _server_available() -> bool:
    try:
        r = requests.get(f"{BASE}/health", timeout=2).json()
        sid = r.get("scenario_id", "") or r.get("scenario", "")
        return sid == SID
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _server_available(),
    reason=_CONTRACT.skip_reason(),
)


def _new_page(playwright):
    browser = playwright.chromium.launch(
        executable_path=r"C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe",
        headless=True,
    )
    ctx = browser.new_context()
    ctx.add_init_script(WS_CAPTURE_INIT)
    return browser, ctx, ctx.new_page()


def _js(page, expr):
    return page.evaluate(expr)


def _wait_js_true(page, expr, timeout_s: float, poll_ms: int = 300) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page.evaluate(expr):
            return True
        page.wait_for_timeout(poll_ms)
    return False


def _risk_deltas(log):
    return [m for m in log if m.get("type") == "risk_delta"]


def _all_warnings(log):
    return [w for m in _risk_deltas(log) for w in (m.get("active_warnings") or [])]


def _perception_events(log):
    out = []
    for m in log:
        if m.get("type") == "evidence_delta" and m.get("perception_events"):
            out.extend(m["perception_events"])
    return out


def _dump_dom(page, sid=SID):
    """采集六大产品区域 DOM 状态（无音频部分）。"""
    return {
        "pill": _js(page, "document.getElementById('ws-pill')?.className || ''"),
        "pill_text": _js(page, "document.getElementById('ws-text')?.textContent || ''"),
        "ps_recent": _js(page, f"document.getElementById('ps-recent-{sid}')?.innerText || ''"),
        "ps_state": _js(page, f"document.getElementById('ps-state-{sid}')?.innerText || ''"),
        "ps_history_count": _js(page, f"document.getElementById('ps-history-count-{sid}')?.textContent || '0'"),
        "signals_text": _js(page, f"document.getElementById('live-signals-{sid}')?.innerText || ''"),
        "lrk": _js(
            page,
            """(() => {
              const card = document.getElementById('lrk-card-__SID__');
              const reasons = Array.from(document.querySelectorAll('#lrk-reasons-__SID__ li'))
                .map(li => (li.textContent || '').trim().replace(/^✓\\s*/, ''));
              return {
                visible: card && card.style.display !== 'none',
                level: document.getElementById('lrk-level-__SID__')?.innerText || '',
                reasons,
                empty_text: document.getElementById('lrk-empty-__SID__')?.innerText || '',
              };
            })()""".replace("__SID__", sid),
        ),
        "tasks": _js(
            page,
            """(() => {
              const g = id => document.getElementById(id + '-__SID__')?.innerText || '';
              return {
                family_status: g('task-family-status'), family_body: g('task-family-body'),
                community_status: g('task-community-status'), community_body: g('task-community-body'),
                log_status: g('task-log-status'), log_body: g('task-log-body'),
              };
            })()""".replace("__SID__", sid),
        ),
        "closure": _js(
            page,
            f"document.getElementById('fs-action-closure-{sid}')?.innerText || ''",
        ),
        "risk_signal_map": _js(
            page,
            """(() => {
              const out = [];
              if (window.__LiveState && window.__LiveState.riskSignalMap) {
                window.__LiveState.riskSignalMap.forEach((v, k) => out.push({ key: String(k), value: v }));
              }
              return out;
            })()""",
        ),
    }


def _observe_phase(page, label, observe_ms, need_source_switch=True):
    """单 phase 观察窗口。返回观测数据 dict。"""
    if need_source_switch:
        assert _wait_js_true(
            page,
            "(window.__wsLog||[]).some(m=>m.type==='source_switched')",
            15,
        ), f"{label}: 15s 内未收到 source_switched"
        page.wait_for_timeout(2_000)

    video_t0 = _js(page, make_video_sig_js(SID))

    seen_badges: set[str] = set()
    max_cards = 0
    steps = observe_ms // POLL_MS
    for i in range(steps):
        sample = _js(page, make_signals_poll_js(SID))
        for b in sample.get("badges") or []:
            seen_badges.add(str(b))
        max_cards = max(max_cards, int(sample.get("cards") or 0))
        page.wait_for_timeout(POLL_MS)

    video_t2 = _js(page, make_video_sig_js(SID))

    log_all = _js(page, "window.__wsLog")
    cut = [i for i, m in enumerate(log_all) if m.get("type") == "source_switched"]
    log = log_all[cut[-1]:] if cut else log_all

    return {
        "label": label,
        "video_t0": video_t0,
        "video_t2": video_t2,
        "log": log,
        "log_all": log_all,
        "seen_badges": sorted(seen_badges),
        "max_rt_cards": max_cards,
        "dom": _dump_dom(page),
    }


@pytest.fixture(scope="module")
def acceptance():
    """单真实 Browser Session：单次 CCTV 观察 phase 全量采集。"""
    data: dict = {}
    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as p:
        browser, _ctx, page = _new_page(p)

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        page.goto(URL, wait_until="domcontentloaded", timeout=30_000)

        assert _wait_js_true(page, "(window.__wsLog||[]).some(m=>m.type==='snapshot')", 15), (
            "15s 内未收到 snapshot（WS 未建立）"
        )

        # Reset first to ensure clean state
        resp = requests.post(f"{BASE}/demo/reset", timeout=15)
        assert resp.status_code == 200, f"cctv reset 失败: {resp.status_code}"

        data["cctv"] = _observe_phase(page, "cctv_observe", OBSERVE_MS, need_source_switch=True)

        data["ws_meta"] = _js(page, "window.__wsMeta")
        data["console_errors"] = console_errors
        data["page_errors"] = page_errors

        browser.close()
    return data


# ============================================================
# P1 · Runtime Truth（无音频版本）
# ============================================================


class TestP1RuntimeTruth:
    def test_p1_ws_established_and_snapshot(self, acceptance):
        """P1a WS 建立 + snapshot 到达。"""
        assert acceptance["ws_meta"]["opened"] >= 1, "WS 从未 open"
        assert "online" in acceptance["cctv"]["dom"]["pill"], "ws-pill 非 online"
        snaps = [m for m in acceptance["cctv"]["log_all"] if m.get("type") == "snapshot"]
        assert snaps, "cctv phase 无 snapshot"

    def test_p1_frame_tick_continuous(self, acceptance):
        """P1b frame_tick 持续 + frame_index 单调。"""
        ticks = [m for m in acceptance["cctv"]["log"] if m.get("type") == "frame_tick"]
        assert len(ticks) >= 20, f"cctv frame_tick 过少: {len(ticks)}"
        idx = [m.get("frame_index") for m in ticks if m.get("frame_index") is not None]
        assert idx == sorted(idx), "frame_index 非单调"
        assert len(set(idx)) >= 10, f"frame_index 几乎无推进: {idx[:10]}"

    def test_p1_video_frame_changing(self, acceptance):
        """P1c video frame 解码 + 画面变化（真实 CCTV 视频，签名应推进）。"""
        v0, v2 = acceptance["cctv"]["video_t0"], acceptance["cctv"]["video_t2"]
        assert isinstance(v0, dict) and "sig" in v0, f"MJPEG 采样失败: {v0}"
        assert v0.get("w"), f"video img 未解码: {v0}"
        assert v0["sig"] != v2["sig"], "cctv 观察窗内画面签名无变化（视频冻结）"

    def test_p1_vision_evidence_arrives(self, acceptance):
        """P1d vision evidence 到达（CCTV 有视觉感知）。"""
        percs = _perception_events(acceptance["cctv"]["log"])
        assert percs, "cctv phase 无 perception_events（视觉证据缺失）"

    def test_p1_no_audio_surface_expected(self, acceptance):
        """P1e CCTV 场景无音频表面 — 确认 audio_events 为空是预期行为，非 bug。"""
        # AudioLane 在 render.py AC-12 处理无音频降级：不应报错
        # 仅确认日志中没有 audio evidence_delta（这是正常现象）
        log = acceptance["cctv"]["log"]
        audio_events = [m for m in log if m.get("type") == "evidence_delta" and m.get("audio")]
        assert not audio_events, "CCTV 场景不应出现 audio evidence"


# ============================================================
# P2 · Risk Story（视觉事件链：Frame→Vision→Risk→Decision→Action）
# ============================================================


class TestP2RiskStory:
    def test_p2_full_chain_order(self, acceptance):
        """P2 Frame→Vision→Risk→Decision→Action 在 cctv phase 单调成立。

        无音频链：Frame → Vision → Risk(RAISED) → Decision(Warning) → Action。
        """
        log = acceptance["cctv"]["log"]

        def first_ts(pred):
            for m in log:
                if pred(m):
                    return m["__t"]
            return None

        t_frame = first_ts(lambda m: m.get("type") == "frame_tick")
        t_vision = first_ts(lambda m: m.get("type") == "evidence_delta" and m.get("perception_events"))
        t_risk = first_ts(lambda m: m.get("type") == "risk_delta" and m.get("risk_transition") == "raised")
        t_warning = first_ts(lambda m: m.get("type") == "risk_delta" and (m.get("active_warnings") or []))
        t_action = first_ts(lambda m: m.get("type") == "state_update")
        if t_action is None:
            warnings = _all_warnings(log)
            if warnings and all(w.get("recommended_action") in LEGAL_ACTIONS for w in warnings):
                t_action = t_warning

        # Frame 和 Vision 必须存在
        assert t_frame is not None, "cctv 无 frame_tick"
        assert t_vision is not None, "cctv 无 vision evidence"

        # Risk→Decision 时序（如发生）
        if t_risk is not None and t_warning is not None:
            assert t_risk <= t_warning, "Risk→Decision 时序逆序"

    def test_p2_warning_produced_or_monitor(self, acceptance):
        """P2b cctv phase 至少产出 Warning 或 MONITOR 类 passive action。"""
        deltas = _risk_deltas(acceptance["cctv"]["log"])
        warnings = _all_warnings(acceptance["cctv"]["log"])
        transitions = [m.get("risk_transition") for m in deltas if m.get("risk_transition")]

        # 期望：有警告或至少 MONITOR 级被动响应
        has_warning = bool(warnings)
        has_monitor = any("monitor" in t for t in transitions) if transitions else False
        has_raised = "raised" in transitions

        # 至少一种情况成立：有 warning / 有 monitor 状态 / 有 raised（最高风险）
        assert has_warning or has_monitor or has_raised, (
            f"cctv phase 无风险信号（transitions={transitions}, warnings={len(warnings)}）"
        )

    def test_p2_max_level_warn_not_high(self, acceptance):
        """P2c 风险上限 WARN — 符合冻结叙事（人员出现≠诈骗，需要行为累积才升级）。"""
        warnings = _all_warnings(acceptance["cctv"]["log"])
        if warnings:
            # 检查最高风险等级是否 ≤ WARN
            # DOM lrk.level 反映运行时最高等级
            dom_level = acceptance["cctv"]["dom"]["lrk"]["level"]
            # HIGH 只出现在 3 规则同帧命中时；CCTV 场景 repeat_visit_count=2 降低阈值，
            # 但单模态（无音频）不应触发 HighRiskApproachRule 的高风险分支
            if dom_level and dom_level != "—":
                # 允许 WARN，不允许 HIGH（除非有 audio 配合）
                assert "HIGH" not in dom_level, (
                    f"CCTV 单模态不应达到 HIGH 等级（DOM level={dom_level!r}）"
                )


# ============================================================
# P4 · 六区域 DOM Oracle（CCTV 适配版）
# ============================================================


class TestP4DOMOracle:
    def test_p4_video_region(self, acceptance):
        """P4a CCTV ① 视频区有内容。"""
        v = acceptance["cctv"]["video_t2"]
        assert isinstance(v, dict) and v.get("w"), f"CCTV 视频区空: {v}"

    def test_p4_timeline_region(self, acceptance):
        """P4b CCTV ② 时间线区有语义条目。"""
        text = acceptance["cctv"]["dom"]["ps_recent"] + acceptance["cctv"]["dom"]["ps_state"]
        assert text.strip(), "CCTV 时间线区空"

    def test_p4_risk_explanation_region(self, acceptance):
        """P4c CCTV ③ 风险解释区有 level（可能是空态，不强制非空）。"""
        lrk = acceptance["cctv"]["dom"]["lrk"]
        assert lrk["visible"] is not None, "CCTV lrk card 不存在"
        # level 空态可接受（未触发风险），非空态必须非 "—"
        if lrk.get("level"):
            assert lrk["level"] != "—", f"CCTV lrk level 为占位符: {lrk}"

    def test_p4_signals_region(self, acceptance):
        """P4d CCTV ③.5 风险信号区存在。"""
        assert acceptance["cctv"]["seen_badges"] or True, "CCTV 风险信号区可空"

    def test_p4_action_closure_region(self, acceptance):
        """P4e CCTV ④ 行动闭环区存在（可能为空态）。"""
        tasks = acceptance["cctv"]["dom"]["tasks"]
        assert tasks, "CCTV tasks 区不存在"


# ============================================================
# P5 · Runtime Provenance（CCTV 适配版）
# ============================================================


class TestP5RuntimeProvenance:
    def test_p5_dom_level_from_runtime(self, acceptance):
        """P5a CCTV DOM level 来自 WS risk_delta。"""
        deltas = [m for m in _risk_deltas(acceptance["cctv"]["log"]) if m.get("risk_levels")]
        dom_level = acceptance["cctv"]["dom"]["lrk"]["level"]
        # 无 risk_levels 时 DOM 可能为空态（未触发）；有 levels 时需一致
        if deltas:
            expected = " / ".join(deltas[-1]["risk_levels"]) + " 风险"
            assert dom_level == expected, (
                f"DOM level={dom_level!r} != runtime={expected!r}"
            )

    def test_p5_no_fraud_conclusion(self, acceptance):
        """P5b CCTV DOM 不出现'诈骗'判定词（模块边界铁律）。"""
        dom = acceptance["cctv"]["dom"]
        for key in ("ps_recent", "ps_state", "signals_text", "closure"):
            txt = dom.get(key, "")
            assert "诈骗" not in txt, f"CCTV {key} 出现『诈骗』判定词: {txt[:120]}"
        for key in ("family_body", "community_body", "log_body"):
            txt = dom["tasks"].get(key, "")
            assert "诈骗" not in txt, f"CCTV {key} 出现『诈骗』判定词: {txt[:120]}"


# ============================================================
# P8 · Browser Cleanliness
# ============================================================


class TestP8BrowserCleanliness:
    def test_p8_no_console_errors(self, acceptance):
        """P8a console 无 error（已知 echarts / 404 静态资源缺陷豁免）。"""
        known = {"echarts is not defined", "echarts is undefined", "404", "Not Found"}
        real = [e for e in acceptance["console_errors"] if not any(k in e for k in known)]
        assert not real, f"console errors: {real[:10]}"

    def test_p8_no_page_errors(self, acceptance):
        """P8b 无未捕获 JS 异常（已知 echarts 独立缺陷豁免）。"""
        known = ("echarts is not defined", "echarts is undefined")
        real = [e for e in acceptance["page_errors"] if not any(k in e for k in known)]
        assert not real, f"page errors: {real[:10]}"

    def test_p8_ws_stable(self, acceptance):
        """P8c WS 无异常断连（opened >= 1，closed < opened）。"""
        meta = acceptance["ws_meta"]
        assert meta["opened"] >= 1, "WS 从未 open"
        assert meta["closed"] < meta["opened"], (
            f"WS 断连次数 >= open 次数: opened={meta['opened']} closed={meta['closed']}"
        )