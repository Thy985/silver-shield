"""Gate G · G6 最终验收门：Combined Risk → Warning → Action → Browser DOM（E2E）。

Owner 冻结链的端到端验收（2026-08-23）：同一真实 Browser Session 内成立——

```
Vision Evidence + Audio Evidence（golden 素材重放）
    ↓ Temporal Link（pipeline._synthesize_combined_risk · runtime 内真实执行）
    ↓ LinkedSignalPair（FrameResult.linked_pairs）
    ↓ EvidenceStrength ESCALATE（emit_combined_signal · combined_risk 特征）
    ↓ Modality-aware Decision（DecisionInput.risk_signals + policy 最小消费）
    ↓ Warning（meta.risk_signals 捕获 audio 贡献 + reason 人话）
    ↓ Action（executor → ActionCommand / state_update）
    ↓ Browser DOM（WS risk_delta → rt-badge / CURRENT STATE / 任务卡）
```

运行前提（外部 fixture，测试内探测 skip；**必须验收态 HP 配置**）：

```
DEMO_HP_CONFIG=config/live_audio_gate_g.yaml python scripts/run_demo.py --live \
  --scenario config/demo/scenarios/gate_g_true_multimodal.yaml
```

时序方案与 A–E Gate 相同：「先连接后重置」（/demo/reset 归零 frame_index 且保留
音频事件 → 前几帧向已在线 session 重放）。验收态边界见 live_audio_gate_g.yaml
头部声明（生产默认 MONITOR ceiling 不变）。
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

BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/live"
SID = "gate_g_true_multimodal"
OBSERVE_MS = 90_000
POLL_MS = 1_000


def _server_ready() -> tuple[bool, str]:
    """gateway 必须以 Gate G 验收态配置运行（scenario 匹配才可验收）。"""
    try:
        r = requests.get(f"{BASE}/health", timeout=2)
        if r.status_code != 200:
            return False, f"health={r.status_code}"
        scn = r.json().get("scenario", "")
        if scn != SID:
            return False, (
                f"scenario 不匹配：当前 {scn!r}，G6 须以 "
                f"DEMO_HP_CONFIG=config/live_audio_gate_g.yaml + scenarios/{SID}.yaml 启动"
            )
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


_ready, _reason = _server_ready()
pytestmark = pytest.mark.skipif(not _ready, reason=f"Gate G gateway 未就绪：{_reason}")


_WS_CAPTURE_INIT = """
window.__wsLog = [];
window.__wsMeta = { opened: 0, closed: 0 };
(function () {
  var OW = window.WebSocket;
  function PatchedWS(url, protocols) {
    var ws = protocols !== undefined ? new OW(url, protocols) : new OW(url);
    window.__wsMeta.opened++;
    ws.addEventListener('message', function (ev) {
      try {
        var m = JSON.parse(ev.data);
        m.__t = Date.now();
        window.__wsLog.push(m);
        if (window.__wsLog.length > 12000) window.__wsLog.shift();
      } catch (e) {}
    });
    return ws;
  }
  PatchedWS.prototype = OW.prototype;
  window.WebSocket = PatchedWS;
})();
"""


def _new_page(playwright):
    browser = playwright.chromium.launch(
        executable_path=r"C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe",
        headless=True,
    )
    ctx = browser.new_context()
    ctx.add_init_script(_WS_CAPTURE_INIT)
    return browser, ctx, ctx.new_page()


def _wait_js_true(page, expr, timeout_s: float, poll_ms: int = 300) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page.evaluate(expr):
            return True
        page.wait_for_timeout(poll_ms)
    return False


_SIGNALS_POLL_JS = """
(() => {
  const box = document.getElementById('live-signals-__SID__');
  if (!box) return { cards: -1, badges: [], txt: '' };
  return {
    cards: box.querySelectorAll('.rt-card').length,
    badges: Array.from(box.querySelectorAll('.rt-badge')).map(b => b.textContent || ''),
    txt: (box.textContent || '').slice(0, 4000),
  };
})()
""".replace("__SID__", SID)


@pytest.fixture(scope="module")
def session():
    data: dict = {}
    with sync_playwright() as p:
        browser, _ctx, page = _new_page(p)
        page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
        assert _wait_js_true(page, "(window.__wsLog||[]).some(m=>m.type==='snapshot')", 15), (
            "15s 内未收到 snapshot"
        )
        resp = requests.post(f"{BASE}/demo/reset", timeout=15)
        assert resp.status_code == 200
        assert _wait_js_true(
            page, "(window.__wsLog||[]).some(m=>m.type==='source_switched')", 10
        ), "10s 内未收到 source_switched"

        # 空态基线（任务卡相对变化判定用）
        page.wait_for_timeout(2_000)
        data["early_tasks"] = page.evaluate(
            "['family','community','log'].map(k => document.getElementById(`task-${k}-{SID}`)?.innerText || '').join('|')".replace("{SID}", SID)
        )

        seen_badges: set[str] = set()
        steps = OBSERVE_MS // POLL_MS
        for i in range(steps):
            sample = page.evaluate(_SIGNALS_POLL_JS)
            for b in sample.get("badges") or []:
                seen_badges.add(str(b))
            page.wait_for_timeout(POLL_MS)
        data["seen_badges"] = sorted(seen_badges)

        log_all = page.evaluate("window.__wsLog")
        cut = [i for i, m in enumerate(log_all) if m.get("type") == "source_switched"]
        assert cut, "找不到 source_switched"
        data["log"] = log_all[cut[-1]:]
        data["signals_text"] = page.evaluate(
            f"document.getElementById('live-signals-{SID}')?.innerText || ''"
        )
        data["lrk"] = page.evaluate(
            """(() => {
              const g = id => document.getElementById(id + '-__SID__')?.innerText || '';
              return {
                level: g('lrk-level'), reasons: g('lrk-reasons'),
                visible: document.getElementById('lrk-card-__SID__')?.style.display !== 'none',
              };
            })()""".replace("__SID__", SID),
        )
        data["tasks_post"] = page.evaluate(
            "['family','community','log'].map(k => document.getElementById(`task-${k}-{SID}`)?.innerText || '').join('|')".replace("{SID}", SID)
        )
        browser.close()
    return data


def _risk_deltas(log):
    return [m for m in log if m.get("type") == "risk_delta"]


def _combined_ws_signals(log):
    out = []
    for m in _risk_deltas(log):
        for s in m.get("risk_signals") or []:
            if isinstance(s, dict) and (s.get("features") or {}).get("combined_risk"):
                out.append(s)
    return out


def _all_warnings(log):
    return [w for m in _risk_deltas(log) for w in (m.get("active_warnings") or [])]


# ============================================================================
# G6 · Combined Risk → Warning → Action → Browser DOM
# ============================================================================


class TestG6TrueMultimodalStory:
    def test_g6_temporal_link_reaches_browser_ws(self, session):
        """冻结链上半段：runtime Combined Risk（ESCALATE 组合信号）到达浏览器 WS。"""
        combined = _combined_ws_signals(session["log"])
        assert combined, "post 段 WS 未出现 combined_risk RiskSignal（Temporal Link/Synthesis 未贯通）"
        f = combined[0]["features"]
        assert f.get("linked_pair_level") in ("same_frame", "near_window")
        assert isinstance(f.get("link_strength"), (int, float))
        assert f.get("vision_signal_id")

    def test_g6_warning_carries_audio_contribution(self, session):
        """Decision 段：Warning 的 reason 捕获 audio 贡献（policy 最小消费投影）。"""
        warnings = _all_warnings(session["log"])
        with_audio = [
            w for w in warnings
            if any("communication(audio)" in r for r in (w.get("reason_summary") or []))
        ]
        assert with_audio, (
            "活跃 Warning 未捕获 audio 贡献（meta/risk_signals 最小消费未投影到 reason）"
        )

    def test_g6_action_evidence_visible(self, session):
        """Action 段：任务卡/状态区相对空态基线出现执行痕迹（Warning → Action 落 DOM）。"""
        early, post = session["early_tasks"], session["tasks_post"]
        changed = early != post
        has_state_update = any(m.get("type") == "state_update" for m in session["log"])
        task_log_touched = "LOG_ONLY" in post or "SENT" in post or "已" in post
        assert changed or has_state_update or task_log_touched, (
            f"Action 执行痕迹缺失：early={early!r} post={post!r} state_update={has_state_update}"
        )

    def test_g6_dom_signal_card_rendered(self, session):
        """Browser DOM 终点：风险信号卡在本 session 真实渲染过（badge 集合非空）。"""
        assert session["seen_badges"], (
            "观察窗内 rt-badge 从未可见（_applyRiskSignal 渲染链未发生）"
        )