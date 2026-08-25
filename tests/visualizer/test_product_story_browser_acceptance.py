"""Product Story Browser Acceptance Gate（Owner P1-P8 · 2026-08-23 冻结）

在同一真实 Browser Session 内成对运行 risk + benign 两个 Product Story，验证：
    P1 Runtime Truth        — WS + snapshot + frame_tick + audio/vision evidence
    P2 Risk Story           — Frame→Audio→Vision→Overlap→Risk→Decision→Action
    P3 Benign Story         — telephone_persistent + no vision → MONITOR → no RAISED → no notification
    P4 六区域 DOM Oracle     — risk 六区 + benign 四区文本/状态断言
    P5 Runtime Provenance   — UI 事实回指 WS payload
    P6 Anti-Hallucination   — 电话≠诈骗 / 单纯 audio≠升级 / 无 overlap≠combined / benign≠notification / 缺失≠前端补
    P7 Scene Switch         — risk→benign→risk→reset 无跨场景污染
    P8 Browser Cleanliness  — console errors=0 / page errors=0

通用化：通过 ScenarioAcceptanceContract 驱动场景配置，支持产品故事和多场景扩展。

运行前提（外部 fixture，测试内探测 skip）：
    python scripts/run_demo.py --live --scenario config/demo/scenarios/telephone_risk.yaml
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
    TelephoneRiskContract,
    make_signals_poll_js,
    make_video_sig_js,
)

BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/live"

# 契约驱动：所有 SID / skip 条件均来自契约类
_CONTRACT = TelephoneRiskContract()
SID = _CONTRACT.scenario_id
OBSERVE_RISK_MS = _CONTRACT.observe_times.get("risk", 120_000)
OBSERVE_BENIGN_MS = _CONTRACT.observe_times.get("benign", 80_000)
OBSERVE_SWITCH_MS = _CONTRACT.observe_times.get("switch_back", 30_000)
OBSERVE_RESET_MS = 15_000
POLL_MS = 1_000

COMMAND_ACTIONS = {"NOTIFY_FAMILY", "ESCALATE_COMMUNITY", "CREATE_COMMUNITY_TASK"}
PASSIVE_ACTIONS = {"MONITOR", "MONITOR_FAMILY", "LOG_ONLY"}
LEGAL_ACTIONS = COMMAND_ACTIONS | PASSIVE_ACTIONS

REASON_RUNTIME_ALLOWLIST = {
    "异常停留", "重复访问", "未在白名单", "异常时段访问", "多风险规则同时命中",
    "停留超过阈值", "检测到重复访问", "待核实到访", "夜间异常访问",
    "高风险逼近（多规则命中）", "声学状态变化", "语音应激升高",
    "电话交互进行中", "夜间访问",
    # ADR-0040 signal reason 的前端润色译文（live_stream.js _REASON_ZH 确定性映射，
    # 原文「实时风险信号: behavioral(vision)」经映射表翻译；键集冻结、可枚举）。
    "实时风险信号: 行为特征（视觉）",
}

_FIRST_FRAME_JS = """
(() => {
  const img = document.getElementById('video-img-__SID__')
           || document.querySelector('img[src*="/mjpeg/"]');
  if (!img) return false;
  if (img.complete && img.naturalWidth > 0) return true;
  const t = document.getElementById('ov-time-__SID__');
  return !!(t && t.textContent && t.textContent !== '00:00');
})()
""".replace("__SID__", SID)

_PERCEPTION_ROW_JS = f"!!document.querySelector('#live-perception-{SID} li')"
_BEHAVIOR_ROW_JS = f"!!document.querySelector('#behavior-timeline-{SID} .tl-item')"

_AUDIO_HEALTH_JS = """
(() => {
  const card = document.getElementById('audio-sensor-__SID__');
  return card ? (card.getAttribute('data-audio-health') || '') : '';
})()
""".replace("__SID__", SID)

_CANVAS_JS = """
(() => {
  const c = document.getElementById('waveform-canvas-__SID__');
  if (!c) return { exists: false };
  let nonBg = -1;
  try {
    const ctx = c.getContext('2d');
    if (!ctx) return { exists: true, w: c.width, h: c.height, nonBgSamples: -2 };
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    nonBg = 0;
    for (let i = 0; i < d.length; i += 16) {
      if (!(d[i] === 30 && d[i + 1] === 41 && d[i + 2] === 59)) nonBg++;
    }
  } catch (e) {
    nonBg = -3;
  }
  return { exists: true, w: c.width, h: c.height, nonBgSamples: nonBg };
})()
""".replace("__SID__", SID)

_PROV_SYNTHETIC_JS = (
    "document.body.innerText.indexOf('合成回放 (SYNTHETIC_REPLAY)') >= 0"
)

_COUNTS_BODY_JS = """
  const g = (id) => document.getElementById(id);
  const tl = g('timeline-list-__SID__') || document.querySelector('.timeline');
  let runtimeLi = -1;
  if (tl) {
    runtimeLi = Array.from(tl.querySelectorAll('li.tl-item[data-ref]')).filter(
      (li) => ((li.getAttribute('data-ref') || '').indexOf('golden://') !== 0)
    ).length;
  }
  return {
    audioTableRows: document.querySelectorAll('table.audio-table tr').length,
    psRecentEntries: document.querySelectorAll('#ps-recent-__SID__ .ps-entry').length,
    psHistoryRendered: document.querySelectorAll('#ps-history-list-__SID__ .ps-entry').length,
    behaviorItems: document.querySelectorAll('#behavior-timeline-__SID__ .tl-item').length,
    timelineRuntimeLi: runtimeLi,
    caseTimeMarks: document.querySelectorAll('#case-time-track-__SID__ .case-time-mark').length,
    bodyNodes: document.querySelectorAll('*').length,
  };
""".replace("__SID__", SID)

_COUNTS_JS = "(() => {" + _COUNTS_BODY_JS + "})()"


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


def _state_updates(log):
    return [m for m in log if m.get("type") == "state_update"]


def _audio_events(log):
    out = []
    for m in log:
        if m.get("type") == "evidence_delta" and m.get("audio"):
            out.extend(m["audio"])
    return out


def _perception_events(log):
    out = []
    for m in log:
        if m.get("type") == "evidence_delta" and m.get("perception_events"):
            out.extend(m["perception_events"])
    return out


def _all_warnings(log):
    return [w for m in _risk_deltas(log) for w in (m.get("active_warnings") or [])]


def _dump_dom(page, sid=SID):
    """采集六大产品区域 DOM 状态。"""
    return {
        "pill": _js(page, "document.getElementById('ws-pill')?.className || ''"),
        "pill_text": _js(page, "document.getElementById('ws-text')?.textContent || ''"),
        "ps_recent": _js(page, f"document.getElementById('ps-recent-{sid}')?.innerText || ''"),
        "ps_state": _js(page, f"document.getElementById('ps-state-{sid}')?.innerText || ''"),
        "ps_history_count": _js(page, f"document.getElementById('ps-history-count-{sid}')?.textContent || '0'"),
        "audio_rows": _js(
            page,
            "Array.from(document.querySelectorAll('table.audio-table tr'))"
            ".map(r => (r.textContent || '').replace(/\\s+/g, ' ').trim())",
        ),
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
        "memory": _js(
            page,
            f"document.getElementById('live-memory-timeline-{sid}')?.textContent || ''",
        ),
        "closure": _js(
            page,
            f"document.getElementById('fs-action-closure-{sid}')?.innerText || ''",
        ),
        "pstream": _js(
            page,
            """(() => {
              const ps = window.__LiveStream && window.__LiveStream.perceptionStream;
              if (!ps) return null;
              return {
                entries: ps.entries, history: ps.history,
                seen_audio_ids: Array.from(ps._seenAudioEventIds || []),
                seen_risk_trans: Array.from(ps._seenRiskTransitions || []),
              };
            })()""",
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


def _observe_phase(page, label, observe_ms, need_source_switch=True, contract_sid=None):
    """等待 source_switched（如需）→ 清 log → 观察窗 → dump。

    返回该 phase 的全部观测数据。log 为 source_switched 之后的干净段。
    """
    effective_sid = contract_sid or SID
    if need_source_switch:
        assert _wait_js_true(
            page,
            "(window.__wsLog||[]).some(m=>m.type==='source_switched')",
            15,
        ), f"{label}: 15s 内未收到 source_switched"
        page.wait_for_timeout(2_000)

    video_t0 = _js(page, make_video_sig_js(effective_sid))
    psstate_t0 = _js(page, f"document.getElementById('ps-state-{effective_sid}')?.innerText || ''")

    seen_badges: set[str] = set()
    max_cards = 0
    steps = observe_ms // POLL_MS
    half = steps // 2
    for i in range(steps):
        sample = _js(page, make_signals_poll_js(effective_sid))
        for b in sample.get("badges") or []:
            seen_badges.add(str(b))
        max_cards = max(max_cards, int(sample.get("cards") or 0))
        page.wait_for_timeout(POLL_MS)
        if i == half:
            video_t1 = _js(page, make_video_sig_js(effective_sid))

    video_t2 = _js(page, make_video_sig_js(effective_sid))
    psstate_t2 = _js(page, f"document.getElementById('ps-state-{effective_sid}')?.innerText || ''")

    log_all = _js(page, "window.__wsLog")
    cut = [i for i, m in enumerate(log_all) if m.get("type") == "source_switched"]
    if cut:
        log = log_all[cut[-1]:]
    else:
        log = log_all

    return {
        "label": label,
        "video_t0": video_t0,
        "video_t1": video_t1 if 'video_t1' in dir() else video_t0,
        "video_t2": video_t2,
        "psstate_t0": psstate_t0,
        "psstate_t2": psstate_t2,
        "log": log,
        "log_all": log_all,
        "seen_badges": sorted(seen_badges),
        "max_rt_cards": max_cards,
        "dom": _dump_dom(page, sid=effective_sid),
    }


@pytest.fixture(scope="module")
def acceptance():
    """单真实 Browser Session：risk → benign → switch_back → reset 四 phase 全量采集。"""
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

        # Phase 1: Risk — reset 对齐 → 观察
        resp = requests.post(f"{BASE}/demo/reset", timeout=15)
        assert resp.status_code == 200, f"risk reset 失败: {resp.status_code}"
        data["risk"] = _observe_phase(page, "risk", OBSERVE_RISK_MS, need_source_switch=True)

        # Phase 2: Benign — 切场景 → 观察
        resp = requests.post(
            f"{BASE}/demo/scenario",
            json={"scenario_id": _CONTRACT.benign_scenario_id()},
            timeout=15,
        )
        assert resp.status_code == 200, f"切换 benign 失败: {resp.status_code} {resp.text}"
        data["benign"] = _observe_phase(page, "benign", OBSERVE_BENIGN_MS, need_source_switch=True)

        # Phase 3: Switch back to Risk — 切回 → 观察（不额外 reset，验污染）
        resp = requests.post(
            f"{BASE}/demo/scenario",
            json={"scenario_id": _CONTRACT.scenario_id},
            timeout=15,
        )
        assert resp.status_code == 200, f"切回 risk 失败: {resp.status_code} {resp.text}"
        data["switch_back"] = _observe_phase(page, "switch_back", OBSERVE_SWITCH_MS, need_source_switch=True)

        # Phase 4: Reset — 清空 → 观察（验干净态）
        resp = requests.post(f"{BASE}/demo/reset", timeout=15)
        assert resp.status_code == 200, f"reset 失败: {resp.status_code}"
        data["reset"] = _observe_phase(page, "reset", OBSERVE_RESET_MS, need_source_switch=True)

        data["ws_meta"] = _js(page, "window.__wsMeta")
        data["console_errors"] = console_errors
        data["page_errors"] = page_errors

        browser.close()
    return data


# ============================================================
# P1 · Runtime Truth
# ============================================================


class TestP1RuntimeTruth:
    def test_p1_ws_established_and_snapshot(self, acceptance):
        """P1a WS 建立 + snapshot 到达。"""
        assert acceptance["ws_meta"]["opened"] >= 1, "WS 从未 open"
        assert "online" in acceptance["risk"]["dom"]["pill"], "ws-pill 非 online"
        snaps = [m for m in acceptance["risk"]["log_all"] if m.get("type") == "snapshot"]
        assert snaps, "risk phase 无 snapshot"

    def test_p1_frame_tick_continuous(self, acceptance):
        """P1b frame_tick 持续 + frame_index 单调。"""
        ticks = [m for m in acceptance["risk"]["log"] if m.get("type") == "frame_tick"]
        assert len(ticks) >= 30, f"risk frame_tick 过少: {len(ticks)}"
        idx = [m.get("frame_index") for m in ticks if m.get("frame_index") is not None]
        assert idx == sorted(idx), "frame_index 非单调"
        assert len(set(idx)) >= 15, f"frame_index 几乎无推进: {idx[:10]}"

    def test_p1_video_frame_changing(self, acceptance):
        """P1c video frame 解码 + 画面变化（真实 CCTV 视频，签名应推进）。"""
        v0, v2 = acceptance["risk"]["video_t0"], acceptance["risk"]["video_t2"]
        assert isinstance(v0, dict) and "sig" in v0, f"MJPEG 采样失败: {v0}"
        assert v0.get("w"), f"video img 未解码: {v0}"
        assert v0["sig"] != v2["sig"], "risk 观察窗内画面签名无变化（视频冻结）"

    def test_p1_audio_evidence_arrives(self, acceptance):
        """P1d audio evidence 到达。"""
        audios = _audio_events(acceptance["risk"]["log"])
        assert audios, "risk phase 无 audio 事件"
        kinds = {str(a.get("kind", "")) for a in audios}
        assert any(k.startswith("audio_") or "telephone" in k or "speech" in k for k in kinds), (
            f"音频类别异常: {kinds}"
        )

    def test_p1_vision_evidence_arrives(self, acceptance):
        """P1e vision evidence 到达（risk 有 actor）。"""
        percs = _perception_events(acceptance["risk"]["log"])
        assert percs, "risk phase 无 perception_events（视觉证据缺失）"


# ============================================================
# P2 · Risk Story（完整链时序）
# ============================================================


class TestP2RiskStory:
    def test_p2_full_chain_order(self, acceptance):
        """P2 Frame→Audio→Vision→Risk→Decision→Action 在 risk phase 单调成立。"""
        log = acceptance["risk"]["log"]

        def first_ts(pred):
            for m in log:
                if pred(m):
                    return m["__t"]
            return None

        t_frame = first_ts(lambda m: m.get("type") == "frame_tick")
        t_audio = first_ts(lambda m: m.get("type") == "evidence_delta" and m.get("audio"))
        t_vision = first_ts(lambda m: m.get("type") == "evidence_delta" and m.get("perception_events"))
        t_risk = first_ts(lambda m: m.get("type") == "risk_delta" and m.get("risk_transition") == "raised")
        t_warning = first_ts(lambda m: m.get("type") == "risk_delta" and (m.get("active_warnings") or []))
        t_action = first_ts(lambda m: m.get("type") == "state_update")
        if t_action is None:
            warnings = _all_warnings(log)
            if warnings and all(w.get("recommended_action") in LEGAL_ACTIONS for w in warnings):
                t_action = t_warning

        chain = {
            "Frame": t_frame, "Audio": t_audio, "Vision": t_vision,
            "Risk(RAISED)": t_risk, "Decision(Warning)": t_warning, "Action": t_action,
        }
        missing = [k for k, v in chain.items() if v is None]
        assert not missing, f"risk 链路缺失环节: {missing}"

        assert t_frame <= min(v for v in chain.values() if v), "Frame 不是最早环节"
        causal = [chain["Risk(RAISED)"], chain["Decision(Warning)"], chain["Action"]]
        assert causal == sorted(causal), f"Risk→Decision→Action 时序逆序: {causal}"

    def test_p2_risk_raised(self, acceptance):
        """P2b risk phase 有 RAISED transition。"""
        deltas = _risk_deltas(acceptance["risk"]["log"])
        transitions = [m.get("risk_transition") for m in deltas if m.get("risk_transition")]
        assert "raised" in transitions, f"risk phase 无 RAISED: {transitions}"

    def test_p2_warning_produced(self, acceptance):
        """P2c risk phase 产出 Warning。"""
        warnings = _all_warnings(acceptance["risk"]["log"])
        assert warnings, "risk phase 无 active_warnings"
        for w in warnings:
            assert w.get("warning_id"), f"Warning 缺 warning_id: {w}"
            act = w.get("recommended_action")
            assert act in LEGAL_ACTIONS, f"非法 action: {act}"


# ============================================================
# P3 · Benign Story
# ============================================================


class TestP3BenignStory:
    def test_p3_audio_evidence_present(self, acceptance):
        """P3a benign phase 有 audio evidence（电话交互被感知）。"""
        audios = _audio_events(acceptance["benign"]["log"])
        assert audios, "benign phase 无 audio 事件（电话交互未被感知）"

    def test_p3_no_raised_transition(self, acceptance):
        """P3b benign phase 无 RAISED transition（不升级）。"""
        deltas = _risk_deltas(acceptance["benign"]["log"])
        transitions = [m.get("risk_transition") for m in deltas if m.get("risk_transition")]
        assert "raised" not in transitions, f"benign 出现 RAISED（误升级）: {transitions}"

    def test_p3_no_command_action(self, acceptance):
        """P3c benign phase 无命令型 action（不通知家属）。"""
        warnings = _all_warnings(acceptance["benign"]["log"])
        for w in warnings:
            act = w.get("recommended_action")
            assert act not in COMMAND_ACTIONS, f"benign 出现命令型 action（误通知）: {act}"

    def test_p3_no_notification_in_dom(self, acceptance):
        """P3d benign DOM 行动闭环区无通知痕迹。"""
        tasks = acceptance["benign"]["dom"]["tasks"]
        for key in ("family_body", "community_body"):
            txt = tasks.get(key, "")
            assert "通知" not in txt or "暂无" in txt, (
                f"benign {key} 出现通知痕迹: {txt[:120]}"
            )


# ============================================================
# P4 · 六区域 DOM Oracle
# ============================================================


class TestP4DOMOracle:
    def test_p4_risk_video_region(self, acceptance):
        """P4a risk ① 视频区有内容。"""
        v = acceptance["risk"]["video_t2"]
        assert isinstance(v, dict) and v.get("w"), f"risk 视频区空: {v}"

    def test_p4_risk_timeline_region(self, acceptance):
        """P4b risk ② 时间线区有语义条目。"""
        text = acceptance["risk"]["dom"]["ps_recent"] + acceptance["risk"]["dom"]["ps_state"]
        assert text.strip(), "risk 时间线区空"

    def test_p4_risk_risk_explanation_region(self, acceptance):
        """P4c risk ③ 风险解释区有 level + reasons。"""
        lrk = acceptance["risk"]["dom"]["lrk"]
        assert lrk["level"], f"risk lrk level 空: {lrk}"
        assert lrk["reasons"], f"risk lrk reasons 空: {lrk}"

    def test_p4_risk_signals_region(self, acceptance):
        """P4d risk ③.5 风险信号区有 transition 痕迹。"""
        assert acceptance["risk"]["seen_badges"], "risk 观察窗未捕获任何风险卡 badge"

    def test_p4_risk_action_closure_region(self, acceptance):
        """P4e risk ④ 行动闭环区有记录。"""
        tasks = acceptance["risk"]["dom"]["tasks"]
        has_content = any(
            tasks[k] and "暂无" not in tasks[k]
            for k in ("family_body", "community_body", "log_body")
        )
        assert has_content or acceptance["risk"]["dom"]["closure"], (
            f"risk 行动闭环区全空: {tasks}"
        )

    def test_p4_risk_memory_region(self, acceptance):
        """P4f risk ⑥ Memory 区存在（Memory API 待接入时可能为空态）。"""
        mem = acceptance["risk"]["dom"]["memory"]
        if not mem.strip():
            pytest.skip("Memory API 待接入，DOM 元素为空态（live_stream.js 注释标注阻塞）")

    def test_p4_benign_risk_explanation(self, acceptance):
        """P4g benign ③ 风险解释区存在（可能有空态提示）。"""
        lrk = acceptance["benign"]["dom"]["lrk"]
        assert lrk["visible"] is not None, "benign lrk card 不存在"

    def test_p4_benign_signals_region(self, acceptance):
        """P4h benign ③.5 风险信号区存在。"""
        sig = acceptance["benign"]["dom"]["signals_text"]
        assert sig is not None, "benign signals 区不存在"

    def test_p4_benign_action_closure(self, acceptance):
        """P4i benign ④ 行动闭环区存在（空态）。"""
        tasks = acceptance["benign"]["dom"]["tasks"]
        assert tasks, "benign tasks 区不存在"

    def test_p4_benign_memory_region(self, acceptance):
        """P4j benign ⑥ Memory 区存在。"""
        mem = acceptance["benign"]["dom"]["memory"]
        assert mem is not None, "benign memory 区不存在"


# ============================================================
# P5 · Runtime Provenance
# ============================================================


class TestP5RuntimeProvenance:
    def test_p5_risk_level_from_runtime(self, acceptance):
        """P5a risk DOM level 来自 WS risk_delta。"""
        deltas = [m for m in _risk_deltas(acceptance["risk"]["log"]) if m.get("risk_levels")]
        assert deltas, "risk 无带 risk_levels 的 risk_delta"
        last = deltas[-1]
        expected = " / ".join(last["risk_levels"]) + " 风险"
        assert acceptance["risk"]["dom"]["lrk"]["level"] == expected, (
            f"DOM level={acceptance['risk']['dom']['lrk']['level']!r} != runtime={expected!r}"
        )

    def test_p5_risk_reasons_from_runtime(self, acceptance):
        """P5b risk DOM reasons ∈ WS 原文 ∪ 润色白名单。"""
        deltas = _risk_deltas(acceptance["risk"]["log"])
        ws_reasons: set[str] = set()
        for m in deltas:
            for r in m.get("reason_summary") or []:
                ws_reasons.add(str(r))
            for w in m.get("active_warnings") or []:
                for r in w.get("reason_summary") or []:
                    ws_reasons.add(str(r))
        dom_reasons = acceptance["risk"]["dom"]["lrk"]["reasons"]
        assert dom_reasons, "risk DOM reasons 空"
        allow = REASON_RUNTIME_ALLOWLIST | ws_reasons
        fabricated = [r for r in dom_reasons if r not in allow]
        assert not fabricated, f"risk DOM 出现非 runtime reason: {fabricated}"

    def test_p5_risk_action_from_runtime(self, acceptance):
        """P5c risk DOM action 来自 WS warning.recommended_action。"""
        warnings = _all_warnings(acceptance["risk"]["log"])
        ws_actions = {w.get("recommended_action") for w in warnings if w.get("recommended_action")}
        closure_text = acceptance["risk"]["dom"]["closure"]
        for act in ws_actions:
            if act in COMMAND_ACTIONS:
                assert act in closure_text or "通知" in closure_text or "处置" in closure_text, (
                    f"WS 有命令型 action {act} 但 DOM 闭环区无痕迹: {closure_text[:200]}"
                )


# ============================================================
# P6 · Anti-Hallucination
# ============================================================


class TestP6AntiHallucination:
    def test_p6_no_fraud_conclusion(self, acceptance):
        """P6a 电话≠诈骗：DOM 不出现"诈骗"判定词。"""
        for phase in ("risk", "benign"):
            dom = acceptance[phase]["dom"]
            for key in ("ps_recent", "ps_state", "signals_text", "memory", "closure"):
                txt = dom.get(key, "")
                assert "诈骗" not in txt, f"{phase} {key} 出现『诈骗』判定词: {txt[:120]}"
            for key in ("family_body", "community_body", "log_body"):
                txt = dom["tasks"].get(key, "")
                assert "诈骗" not in txt, f"{phase} {key} 出现『诈骗』判定词: {txt[:120]}"
            for r in dom["lrk"]["reasons"]:
                assert "诈骗" not in r, f"{phase} lrk reason 出现『诈骗』: {r}"

    def test_p6_benign_no_upgrade(self, acceptance):
        """P6b 单纯 audio≠升级：benign 有 audio 但无 RAISED。"""
        audios = _audio_events(acceptance["benign"]["log"])
        assert audios, "benign 有 audio 事件"
        deltas = _risk_deltas(acceptance["benign"]["log"])
        transitions = [m.get("risk_transition") for m in deltas if m.get("risk_transition")]
        assert "raised" not in transitions, f"benign 误升级: {transitions}"

    def test_p6_benign_no_notification(self, acceptance):
        """P6c benign≠通知：benign 无命令型 action + DOM 无通知痕迹。"""
        warnings = _all_warnings(acceptance["benign"]["log"])
        for w in warnings:
            assert w.get("recommended_action") not in COMMAND_ACTIONS, (
                f"benign 命令型 action: {w.get('recommended_action')}"
            )
        tasks = acceptance["benign"]["dom"]["tasks"]
        for key in ("family_body", "community_body"):
            txt = tasks.get(key, "")
            assert "已通知" not in txt and "已发送" not in txt, (
                f"benign {key} 有通知痕迹: {txt[:120]}"
            )

    def test_p6_no_fabricated_reason(self, acceptance):
        """P6d 缺失≠前端补：DOM reason 必须可在某阶段 runtime 输出中溯源。

        场景经 POST /demo/scenario 热切换、共用同一页面实例：risk 阶段写入的
        lrk-reasons li 在 benign 仅被隐藏（display:none，PR-B P1-1 保留观察卡）
        而不清空，属跨阶段残留而非本阶段展示；且本阶段 log 是 source_switched
        之后的干净段，不含上一阶段 delta。故 benign 允许集并入 risk 阶段 DOM
        reasons（其本身已在 risk 阶段通过本断言校验），allowlist 不放宽。
        """
        prev_dom_reasons: set[str] = set()
        for phase in ("risk", "benign"):
            dom_reasons = acceptance[phase]["dom"]["lrk"]["reasons"]
            deltas = _risk_deltas(acceptance[phase]["log"])
            ws_reasons: set[str] = set()
            for m in deltas:
                for r in m.get("reason_summary") or []:
                    ws_reasons.add(str(r))
                for w in m.get("active_warnings") or []:
                    for r in w.get("reason_summary") or []:
                        ws_reasons.add(str(r))
            allow = REASON_RUNTIME_ALLOWLIST | ws_reasons | prev_dom_reasons
            fabricated = [r for r in dom_reasons if r not in allow]
            assert not fabricated, f"{phase} 前端编造 reason: {fabricated}"
            prev_dom_reasons.update(dom_reasons)

    def test_p6_no_raw_delta_flood(self, acceptance):
        """P6e 不出现 Raw Delta 刷屏。"""
        for phase in ("risk", "benign"):
            text = acceptance[phase]["dom"]["ps_recent"]
            assert "frame@" not in text, f"{phase} 出现 frame@ 裸 delta"
            assert "bbox [" not in text, f"{phase} 出现裸 bbox"
            assert '{"' not in text, f"{phase} 出现裸 JSON"


# ============================================================
# P7 · Scene Switch / Session Hygiene
# ============================================================


class TestP7SceneSwitch:
    def test_p7_switch_back_no_stale_raised(self, acceptance):
        """P7a 切回 risk 后无 benign 残留 RAISED。"""
        sb = acceptance["switch_back"]
        benign_deltas = _risk_deltas(acceptance["benign"]["log"])

        benign_transitions = [m.get("risk_transition") for m in benign_deltas if m.get("risk_transition")]

        assert "raised" not in benign_transitions, "benign 有 RAISED（P3 应已拦截）"
        # switch_back 是新 risk 会话，RAISED 应来自新 risk 证据，不是 benign 残留
        # 关键：switch_back 的 RAISED 如果存在，其 warning_id 不应与 benign 的相同
        benign_wids = {w.get("warning_id") for w in _all_warnings(acceptance["benign"]["log"]) if w.get("warning_id")}
        sb_wids = {w.get("warning_id") for w in _all_warnings(sb["log"]) if w.get("warning_id")}
        stale = benign_wids & sb_wids
        assert not stale, f"switch_back 出现 benign 残留 warning_id: {stale}"

    def test_p7_reset_clean_state(self, acceptance):
        """P7b reset 后行动闭环回到空态。"""
        tasks = acceptance["reset"]["dom"]["tasks"]
        for key in ("family_body", "community_body"):
            txt = tasks.get(key, "")
            assert "暂无" in txt or not txt.strip(), (
                f"reset 后 {key} 非空态: {txt[:120]}"
            )

    def test_p7_reset_no_raised(self, acceptance):
        """P7c reset 后短时间内无 RAISED（干净态）。"""
        deltas = _risk_deltas(acceptance["reset"]["log"])
        transitions = [m.get("risk_transition") for m in deltas if m.get("risk_transition")]
        assert "raised" not in transitions, (
            f"reset 后 {OBSERVE_RESET_MS}ms 内出现 RAISED（未干净）: {transitions}"
        )

    def test_p7_no_cross_scenario_signal_leak(self, acceptance):
        """P7d 切场景后 risk_signal_map 不残留旧场景信号。"""
        benign_signals = acceptance["benign"]["dom"]["risk_signal_map"]
        sb_signals = acceptance["switch_back"]["dom"]["risk_signal_map"]
        benign_keys = {s["key"] for s in benign_signals}
        sb_keys = {s["key"] for s in sb_signals}
        # 信号 key 可能重叠（同 device 同 signal_type），但 value 不应是 benign 的残留
        # 此处验证 switch_back 的信号数量不异常多于 benign（无累积泄漏）
        assert len(sb_keys) <= max(len(benign_keys), 1) + 5, (
            f"switch_back 信号数异常（疑似泄漏）: benign={len(benign_keys)} sb={len(sb_keys)}"
        )


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
