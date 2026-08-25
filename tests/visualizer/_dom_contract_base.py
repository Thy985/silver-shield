"""Shared D0 DOM Product Contract infrastructure — scenario-adaptive base.

所有 D0 场景共享：
    - 工程语义黑名单常量（冻结；只增不减）
    - JS 模板工厂（接受 sid 参数，不硬编码）
    - 扫描/判定 helpers
    - fixture 工厂（create_dom_fixtures）
    - N/A 追踪（NA_TRACKER）

每个场景测试模块：
    1. 实例化 D0Contract（provenance / has_audio_surface 等）
    2. 调用 create_dom_fixtures(contract) 获取参数化 fixtures
    3. import TestVContractSurface / TestBlacklistDim1BodyText / ... 等通用断言类
    4. AU-08 断言使用 contract.provenance 参数化，不硬编码字符串
    5. 音频专属 AU 通过 _na_skip() 显式标记 N/A（不伪装为 PASS）
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

import pytest
import requests

from tests.visualizer._scenario_contract import D0Contract

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment,misc]

BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/live"
CHROMIUM_PATH = (
    r"C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe"
)

# ---------------------------------------------------------------------------
# §3.2.3 工程语义模式黑名单（冻结；只增不减，放宽须 Owner 书面批准）
# ---------------------------------------------------------------------------
ENGINEERING_FIELD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("frame_at", r"frame@\d+"),
    ("bbox_bracket", r"bbox\s*\["),
    ("score_eq", r"score\s*=\s*\d"),
    ("conf_eq", r"conf\s*=\s*\d"),
    ("dot_conf", r"·\s*conf\s+\d"),
)
INTERNAL_COMPONENT_NAMES: tuple[str, ...] = (
    "LiveFrameStream",
    "ArtifactVideoSource",
    "Media Source Adapter",
    "ProjectionAccumulator",
)
INTERNAL_CONCEPT_TERMS: tuple[str, ...] = (
    "Evidence Timeline",
    "Media Timeline",
    "View Model",
    "evidence_delta",
    "perception_delta",
)

_ALL_DIM1_RULES: tuple[tuple[str, str, str], ...] = (
    *(("field", rid, pat) for rid, pat in ENGINEERING_FIELD_PATTERNS),
    *(("component", name, re.escape(name)) for name in INTERNAL_COMPONENT_NAMES),
    *(("concept", name, re.escape(name)) for name in INTERNAL_CONCEPT_TERMS),
)

PERCEPTION_FIELD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("perception_conf_value", r"conf\s+\d"),
    ("perception_bbox_bracket", r"bbox\s*\["),
)
_BARE_SCORE_RE = r"(?<![\d:.])\d\.\d{2}(?!\d)"
_RAW_AUDIO_ENUM_RE = r"\baudio_[a-z_]+\b"
_AUDIO_KIND_ZH_LABELS: tuple[str, ...] = (
    "音高升高",
    "语速加快",
    "声学异常活动",
    "持续电话声",
    "其他声学异常",
)
_DISTRESS_CAUTION_NOTE = "当前版本该类别存在正常电话语音误识别，暂不作为风险升级依据"

# ---------------------------------------------------------------------------
# AU-03 显示上限契约
# ---------------------------------------------------------------------------
PS_VISIBLE_LIMIT = 10
PS_HISTORY_RENDER_LIMIT = 10
BEHAVIOR_TIMELINE_LIMIT = 20
TIMELINE_RUNTIME_LIMIT = 30
CASE_TIME_MARK_LIMIT = 120
BODY_NODE_HARD_BOUND = 8_000
BODY_NODES_JITTER_TOLERANCE = 8

# ---------------------------------------------------------------------------
# 轮询等待预算（§3.2.5）
# ---------------------------------------------------------------------------
POLL_INTERVAL_MS = 800
DEFAULT_FIRST_FRAME_TIMEOUT_MS = 30_000
DEFAULT_PERCEPTION_DATA_TIMEOUT_MS = 60_000
DEFAULT_BEHAVIOR_DATA_TIMEOUT_MS = 95_000
DEFAULT_AUDIO_EVENT_TIMEOUT_MS = 50_000
DEFAULT_AUDIO_STALE_TIMEOUT_MS = 60_000
DEFAULT_AUDIO_QUIET_WINDOW_MS = 20_000


# ---------------------------------------------------------------------------
# JS 模板工厂（接受 sid 参数，不硬编码任何场景）
# ---------------------------------------------------------------------------

def make_first_frame_js(sid: str) -> str:
    return f"""\
(() => {{
  const img = document.getElementById('video-img-{sid}');
  if (img && img.naturalWidth > 0) return true;
  const t = document.getElementById('ov-time-{sid}');
  return !!(t && t.textContent && t.textContent !== '00:00');
}})()
"""


def make_perception_row_js(sid: str) -> str:
    return f"!!document.querySelector('#live-perception-{sid} li')"


def make_behavior_row_js(sid: str) -> str:
    return f"!!document.querySelector('#behavior-timeline-{sid} .tl-item')"


def make_audio_health_js(sid: str) -> str:
    return f"""\
(() => {{
  const card = document.getElementById('audio-sensor-{sid}');
  return card ? (card.getAttribute('data-audio-health') || '') : '';
}})()
"""


def make_canvas_js(sid: str) -> str:
    return f"""\
(() => {{
  const c = document.getElementById('waveform-canvas-{sid}');
  if (!c) return {{ exists: false }};
  let nonBg = -1;
  try {{
    const ctx = c.getContext('2d');
    if (!ctx) return {{ exists: true, w: c.width, h: c.height, nonBgSamples: -2 }};
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    nonBg = 0;
    for (let i = 0; i < d.length; i += 16) {{
      if (!(d[i] === 30 && d[i + 1] === 41 && d[i + 2] === 59)) nonBg++;
    }}
  }} catch (e) {{
    nonBg = -3;
  }}
  return {{ exists: true, w: c.width, h: c.height, nonBgSamples: nonBg }};
}})()
"""


def make_counts_js(sid: str) -> str:
    body = f"""\
  const g = (id) => document.getElementById(id);
  const tl = g('timeline-list-{sid}') || document.querySelector('.timeline');
  let runtimeLi = -1;
  if (tl) {{
    runtimeLi = Array.from(tl.querySelectorAll('li.tl-item[data-ref]')).filter(
      (li) => ((li.getAttribute('data-ref') || '').indexOf('golden://') !== 0)
    ).length;
  }}
  return {{
    audioTableRows: document.querySelectorAll('table.audio-table tr').length,
    psRecentEntries: document.querySelectorAll('#ps-recent-{sid} .ps-entry').length,
    psHistoryRendered: document.querySelectorAll('#ps-history-list-{sid} .ps-entry').length,
    behaviorItems: document.querySelectorAll('#behavior-timeline-{sid} .tl-item').length,
    timelineRuntimeLi: runtimeLi,
    caseTimeMarks: document.querySelectorAll('#case-time-track-{sid} .case-time-mark').length,
    bodyNodes: document.querySelectorAll('*').length,
  }};
"""
    return f"(() => {{{body}}})()"


def make_snapshot_js(sid: str) -> str:
    counts_body = make_counts_js(sid)
    return f"""\
(() => {{
  const g = (id) => document.getElementById(id);
  const vpW = window.innerWidth;
  const vpH = window.innerHeight;
  const probe = (el) => {{
    if (!el) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {{
      hasDebugAttr: el.hasAttribute('data-debug-only'),
      display: cs.display,
      visibility: cs.visibility,
      opacity: parseFloat(cs.opacity || '1'),
      rect: {{
        left: r.left, top: r.top, right: r.right, bottom: r.bottom,
        width: r.width, height: r.height,
      }},
      text: el.innerText || '',
      vpW: vpW,
      vpH: vpH,
    }};
  }};
  const tlUl = document.querySelector('.timeline');
  const moreBtn = tlUl ? tlUl.parentNode.querySelector('.tl-more-toggle') : null;
  const psRecent = g('ps-recent-{sid}');
  const behavior = g('behavior-timeline-{sid}');
  const counts = (() => {{{counts_body}}})();
  return {{
    bodyText: document.body.innerText,
    demoStat: probe(g('demo-stat-{sid}')),
    ovFrame: probe(g('ov-frame-{sid}')),
    ovTime: probe(g('ov-time-{sid}')),
    dsSession: probe(g('ds-session-{sid}')),
    binding: probe(document.querySelector('.case-video-binding')),
    perception: probe(g('live-perception-{sid}')),
    sensorAudio: probe(g('audio-sensor-{sid}')),
    psRecentText: psRecent ? (psRecent.innerText || '') : '',
    behaviorText: behavior ? (behavior.innerText || '') : '',
    moreBtnText: moreBtn ? (moreBtn.textContent || '') : '',
    debugNodes: Array.from(document.querySelectorAll('[data-debug-only]')).map((el, i) => {{
      const p = probe(el);
      p.key = el.id || (el.tagName + '#' + i);
      return p;
    }}),
    counts: counts,
  }};
}})()
"""


def make_prov_banner_js(sid: str) -> str:
    return f"document.querySelector('.prov-modality-{sid}') !== null"


def make_prov_text_js(sid: str) -> str:
    return f"""(() => {{
      const el = document.querySelector('.prov-modality-{sid}');
      return el ? (el.innerText || '') : '';
    }})()"""


def make_locator_js(sid: str) -> str:
    return f"""\
(() => {{
  const loc = document.querySelector('.audio-event-locator-{sid}');
  if (!loc) return {{ error: 'not-found' }};
  return {{
    dots: loc.querySelectorAll('circle').length,
    text: loc.innerText || '',
  }};
}})()"""


# ---------------------------------------------------------------------------
# 扫描与判定 helpers
# ---------------------------------------------------------------------------

def _scan_hits(text: str, patterns: list[tuple[str, str]]) -> list[dict[str, str]]:
    """返回命中明细列表（规则 id / 命中串 / 前后 40 字符上下文）。"""
    hits: list[dict[str, str]] = []
    for rule_id, pattern in patterns:
        for m in re.finditer(pattern, text):
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            hits.append(
                {
                    "rule": rule_id,
                    "match": m.group(0),
                    "context": text[start:end].replace("\n", "\\n"),
                }
            )
    return hits


def _fmt_hits(hits: list[dict[str, str]]) -> str:
    return "; ".join(f"[{h['rule']}] '{h['match']}' @ …{h['context']}…" for h in hits[:8])


def _is_invisible_per_dim2(info: Mapping[str, Any]) -> bool:
    """§3.2.5 维度② 五判据（满足任一即视觉不可见）。"""
    if info["display"] == "none":
        return True
    if info["visibility"] == "hidden":
        return True
    if float(info["opacity"]) < 0.01:
        return True
    rect = info["rect"]
    if rect["width"] <= 0 or rect["height"] <= 0:
        return True
    vw, vh = float(info["vpW"]), float(info["vpH"])
    offscreen = (
        rect["left"] >= vw or rect["top"] >= vh or rect["right"] <= 0 or rect["bottom"] <= 0
    )
    return offscreen


def _poll_until(page: Any, js_predicate: str, timeout_ms: int) -> bool:
    """轮询等待页内 JS 谓词为真（带超时上限）。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            matched = bool(page.evaluate(js_predicate))
        except Exception:  # noqa: BLE001
            matched = False
        if matched:
            return True
        page.wait_for_timeout(POLL_INTERVAL_MS)
    return False


def _server_available_for(sid: str) -> bool:
    try:
        r = requests.get(f"{BASE}/health", timeout=2).json()
        current = r.get("scenario_id", "") or r.get("scenario", "")
        return current == sid
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# N/A 追踪（独立于 PASS/FAIL，明确报告哪些 AU 因无音频表面而 N/A）
# ---------------------------------------------------------------------------
_NA_TRACKER: dict[str, set[str]] = {}  # {module_name: {assertion_id, ...}}


def na_skip(assertion_id: str, reason: str = "") -> None:
    """标记某条 D0 断言为 N/A（无音频表面等场景约束），不入 PASS 也不入 FAIL。

    报告汇总在 pytest run结束后通过 pytest_terminal_summary 打印：
        D0 N/A: product_story_risk → AU-04, AU-05, ...
        D0 N/A: cctv_surveillance  → AU-04, AU-05, AU-05b, ...
    """
    import sys
    mod = sys._getframe(1).f_globals.get("__name__", "unknown")
    _NA_TRACKER.setdefault(mod, set()).add(assertion_id)
    pytest.skip(f"D0 N/A [{assertion_id}] {reason}")


def get_na_summary() -> dict[str, list[str]]:
    return {mod: sorted(ids) for mod, ids in _NA_TRACKER.items() if ids}


# ---------------------------------------------------------------------------
# 通用 pytest hooks（N/A 报告 + skipif 生成器）
# ---------------------------------------------------------------------------

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """在 pytest 报告末尾追加各模块的 D0 N/A 汇总。"""
    summary = get_na_summary()
    if not summary:
        return
    lines = ["\n" + "=" * 60, "D0 通用产品表面契约 — N/A 汇总", "=" * 60]
    for mod, ids in sorted(summary.items()):
        short = mod.split(".")[-1] if "." in mod else mod
        lines.append(f"  {short}: {', '.join(ids)}")
    lines.append("=" * 60)
    terminalreporter.write_line("\n".join(lines), sep="\n", bold=True, yellow=True)


def make_skipif(scenario_id: str, reason: str) -> pytest.MarkDecorator:
    """生成模块级 pytestmark skipif。"""
    return pytest.mark.skipif(
        not _server_available_for(scenario_id),
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Fixture 工厂
# ---------------------------------------------------------------------------

def create_dom_fixtures(contract: D0Contract):
    """返回 (browser, contract_page, audio_lifecycle) 三个 pytest fixture 对象。

    调用方式（在各测试模块中）：
        _browser, contract_page, audio_lifecycle = create_dom_fixtures(MY_CONTRACT)
    """
    sid = contract.scenario_id
    first_frame_js = make_first_frame_js(sid)
    perception_row_js = make_perception_row_js(sid)
    behavior_row_js = make_behavior_row_js(sid)
    audio_health_js = make_audio_health_js(sid)
    canvas_js = make_canvas_js(sid)
    counts_js = make_counts_js(sid)
    snapshot_js = make_snapshot_js(sid)

    first_timeout = contract.observe_first_frame_ms
    perception_timeout = contract.observe_perception_ms
    behavior_timeout = contract.observe_behavior_ms
    audio_event_timeout = DEFAULT_AUDIO_EVENT_TIMEOUT_MS
    audio_stale_timeout = DEFAULT_AUDIO_STALE_TIMEOUT_MS
    audio_quiet_window = DEFAULT_AUDIO_QUIET_WINDOW_MS

    if sync_playwright is None:
        return _no_op_fixtures()

    @pytest.fixture(scope="module")
    def _browser():
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM_PATH,
                headless=True,
            )
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            yield page
            browser.close()

    @pytest.fixture(scope="module")
    def contract_page(_browser):
        page = _browser
        requests.post(f"{BASE}/demo/reset", timeout=15)
        page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
        first_frame = _poll_until(page, first_frame_js, first_timeout)
        perception_ready = _poll_until(page, perception_row_js, perception_timeout)
        behavior_ready = _poll_until(page, behavior_row_js, behavior_timeout)
        page.wait_for_timeout(1_500)
        return {
            "page": page,
            "first_frame": first_frame,
            "perception_ready": perception_ready,
            "behavior_ready": behavior_ready,
            "snapshot": dict(page.evaluate(snapshot_js)),
        }

    @pytest.fixture(scope="module")
    def audio_lifecycle(_browser):
        if not contract.has_audio_surface:
            return {"states": [], "saw_recent": False, "canvas_info": None,
                    "baseline": {}, "samples": []}
        page = _browser
        requests.post(f"{BASE}/demo/reset", timeout=15)
        page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
        _poll_until(page, first_frame_js, first_timeout)

        states: list[str] = []
        saw_recent = False
        canvas_info: dict[str, Any] | None = None
        deadline = time.monotonic() + (audio_event_timeout + audio_stale_timeout) / 1000.0
        while time.monotonic() < deadline:
            state = str(page.evaluate(audio_health_js) or "")
            if state and (not states or states[-1] != state):
                states.append(state)
            if state == "RECENT_EVENT":
                saw_recent = True
                if canvas_info is None:
                    canvas_info = dict(page.evaluate(canvas_js))
            if saw_recent and state == "NO_RECENT_EVENT":
                break
            page.wait_for_timeout(POLL_INTERVAL_MS)

        prev_rows = -1
        stable_count = 0
        for _ in range(20):
            page.wait_for_timeout(1_000)
            rows = int(page.evaluate(
                "document.querySelectorAll('table.audio-table tr').length"
            ))
            if rows == prev_rows and rows > 0:
                stable_count += 1
                if stable_count >= 12:
                    break
            else:
                stable_count = 0
            prev_rows = rows

        baseline = dict(page.evaluate(counts_js))
        samples: list[dict[str, Any]] = []
        for _ in range(audio_quiet_window // 2_000):
            page.wait_for_timeout(2_000)
            samples.append(dict(page.evaluate(counts_js)))
        return {
            "states": states,
            "saw_recent": saw_recent,
            "canvas_info": canvas_info,
            "baseline": baseline,
            "samples": samples,
        }

    return _browser, contract_page, audio_lifecycle


def _no_op_fixtures():
    """playwright 未安装时的占位 fixtures。"""

    @pytest.fixture(scope="module")
    def _browser():
        yield None

    @pytest.fixture(scope="module")
    def contract_page(_browser):
        yield {"page": None, "first_frame": False, "perception_ready": False,
               "behavior_ready": False, "snapshot": {}}

    @pytest.fixture(scope="module")
    def audio_lifecycle(_browser):
        yield {"states": [], "saw_recent": False, "canvas_info": None,
               "baseline": {}, "samples": []}

    return _browser, contract_page, audio_lifecycle