"""H · Playwright 六张产品截图（SSOT §3.4 / §5 步骤 H · vision-eval 流水线输入）。

CCTV Surveillance Suspicious 场景适配版本。

SSOT：``docs/reports/DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md`` v3.4。

定位（收尾路径步骤 H）：驱动 Playwright 重生成**固定六张**产品截图（六区域：
视频 / 行为时间线 / 风险解释卡 / 实时风险信号 / 行动闭环 / Memory Context），
产出 md5 manifest 并校验 console/page error=0（含已知豁免）。

时序纪律：页面是**活的**（RAISED 卡可能被后续无风险帧隐藏，§7.8 契约），
故六张截图在 module fixture 内、RAISED 就绪判定后**立即连续拍摄**；测试仅对
产物与观测记录做断言，不在测试间依赖页面实时状态。

红线（§3.4）：本文件仅承担流水线两端之①——生产截图；禁止在本文件内对
Vision Judge 评分结论做任何断言（那是步骤 I 的人工/vision 验收职责）。

运行前提（外部 fixture，模块级探测 skip）：
    python scripts/run_demo.py --live --scenario config/demo/scenarios/cctv_surveillance_suspicious.yaml
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import requests

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright not installed", allow_module_level=True)


BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/live"
SID = "cctv_surveillance_suspicious"

CHROMIUM_PATH = (
    r"C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe"
)

OUT_DIR = (
    Path(__file__).resolve().parents[2]
    / "docs" / "reports" / "assets" / "vision-eval" / SID
)

POLL_INTERVAL_MS = 800
FIRST_FRAME_TIMEOUT_MS = 30_000
PERCEPTION_DATA_TIMEOUT_MS = 60_000
BEHAVIOR_DATA_TIMEOUT_MS = 95_000
RAISED_TIMEOUT_MS = 120_000

# 固定六张（§3.4 六区域；顺序即评审顺序，冻结）。
SHOTS: tuple[tuple[str, str, str], ...] = (
    ("01-video-sensor.png", f"video-sensor-{SID}", "① 视频区（sensor-card 本体）"),
    ("02-behavior-timeline.png", f"behavior-timeline-{SID}", "② 行为时间线"),
    ("03-risk-explain-card.png", f"lrk-card-{SID}", "③ 风险解释卡（RAISED 人话原因）"),
    ("04-risk-signals.png", f"live-signals-{SID}", "③.5 实时风险信号（rt-card）"),
    ("05-action-closure.png", f"fs-action-closure-{SID}", "④ 行动闭环"),
    ("06-memory-context.png", f"memory-context-{SID}", "⑥ Memory Context（如实记录，内容稀疏不否决）"),
)

# console/page error 已知豁免（消息子串匹配；只增不减，新增须注明来源）。
KNOWN_CONSOLE_EXEMPT: tuple[str, ...] = (
    # Chromium 自动请求 /favicon.ico 得 404（server 未提供该静态资源）。
    "favicon.ico",
)


def _server_available() -> bool:
    try:
        r = requests.get(f"{BASE}/health", timeout=2).json()
        sid = r.get("scenario_id", "") or r.get("scenario", "")
        return sid == SID
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _server_available(),
    reason="需先启动: python scripts/run_demo.py --live --scenario "
    "config/demo/scenarios/cctv_surveillance_suspicious.yaml",
)


def _poll_until(page: Any, js_predicate: str, timeout_ms: int) -> bool:
    """轮询等待页内 JS 谓词为真（带超时上限）；超时返回 False 由调用方决定语义。"""
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


_FIRST_FRAME_JS = """
(() => {
  const img = document.getElementById('video-img-__SID__');
  const ph = document.getElementById('video-ph-__SID__');
  if (img && img.src && img.naturalWidth > 0) return true;
  if (ph) return (ph.getAttribute('data-state') || ph.textContent || '').indexOf('等待') < 0;
  return false;
})()
""".replace("__SID__", SID)

_PERCEPTION_ROW_JS = """
(() => {
  const ps = document.getElementById('ps-recent-__SID__');
  return !!(ps && ps.querySelectorAll('.ps-entry').length);
})()
""".replace("__SID__", SID)

_BEHAVIOR_ROW_JS = """
(() => {
  const bt = document.getElementById('behavior-timeline-__SID__');
  return !!(bt && bt.querySelectorAll('.tl-item').length);
})()
""".replace("__SID__", SID)

_RAISED_JS = """
(() => {
  const card = document.getElementById('lrk-card-__SID__');
  return !!(card && card.style.display !== 'none' &&
            card.querySelectorAll('#lrk-reasons-__SID__ li').length);
})()
""".replace("__SID__", SID)

# RAISED 未达成时的诊断快照（定位卡点）。
_DIAGNOSTICS_JS = """
(() => {
  const g = (id) => document.getElementById(id);
  const psRecent = g('ps-recent-__SID__');
  const bt = g('behavior-timeline-__SID__');
  const lrkEmpty = g('lrk-empty-__SID__');
  return {
    psEntries: psRecent ? psRecent.querySelectorAll('.ps-entry').length : -1,
    behaviorItems: bt ? bt.querySelectorAll('.tl-item').length : -1,
    lrkEmptyText: lrkEmpty ? (lrkEmpty.textContent || '').trim() : 'NO_NODE',
    wsOpen: (window.__wsMeta && window.__wsMeta.opened) || 0,
  };
})()
""".replace("__SID__", SID)

# 截图前可见性预检（display/visibility/rect 三判据；不可见直接失败并带锚点名）。
_VISIBILITY_JS = """
(id => {
  const el = document.getElementById(id);
  if (!el) return { exists: false };
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  return {
    exists: true,
    display: cs.display,
    visibility: cs.visibility,
    w: r.width,
    h: r.height,
  };
})
"""


# 连拍执行顺序（易逝的 ③/③.5 最先，稳定区域垫后）。
_BURST_ORDER: tuple[str, ...] = ("03", "04", "01", "02", "05", "06")


def _shot_visible(page: Any, dom_id: str) -> bool:
    vis = dict(page.evaluate(f"({_VISIBILITY_JS})('{dom_id}')"))
    return bool(
        vis.get("exists")
        and vis["display"] != "none"
        and vis["visibility"] != "hidden"
        and float(vis["w"]) > 0
        and float(vis["h"]) > 0
    )


def _try_capture_burst(page: Any) -> list[dict[str, str]] | None:
    """RAISED 可见窗口内一次性连拍六张；任一锚点不可见则放弃本次尝试。"""
    if not bool(page.evaluate(_RAISED_JS)):
        return None
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for fname, dom_id, desc in sorted(
        SHOTS, key=lambda s: _BURST_ORDER.index(s[0][:2])
    ):
        if not _shot_visible(page, dom_id):
            return None
        try:
            locator = page.locator(f"#{dom_id}")
            locator.scroll_into_view_if_needed(timeout=5_000)
            path = OUT_DIR / fname
            locator.screenshot(path=str(path), timeout=10_000)
        except Exception:  # noqa: BLE001
            return None
        data = path.read_bytes()
        if len(data) <= 1_000:
            return None
        rows.append(
            {
                "file": fname,
                "anchor": f"#{dom_id}",
                "desc": desc,
                "bytes": str(len(data)),
                "md5": hashlib.md5(data).hexdigest(),
            }
        )
    return rows


def _write_manifest(rows: list[dict[str, str]], session: dict[str, Any]) -> Path:
    lines = [
        f"# Vision-Eval Screenshots · {SID}",
        "",
        f"- captured_at: {session['captured_at']}",
        f"- frame_index: {session['frame_index']}",
        "- viewport: 1440x900 (headless Chromium)",
        "- source: tests/visualizer/test_cctv_product_screenshots.py（SSOT §3.4 步骤 H）",
        "",
        "| file | anchor | 说明 | bytes | md5 |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: x["file"]):
        lines.append(
            f"| {r['file']} | `{r['anchor']}` | {r['desc']} | {r['bytes']} | `{r['md5']}` |"
        )
    manifest = OUT_DIR / "MANIFEST.md"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


@pytest.fixture(scope="module")
def _browser():
    """单真实 Browser Session（headless Chromium，1440x900 viewport）。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM_PATH, headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        console_errors: list[str] = []
        page_errors: list[str] = []
        responses_4xx: list[str] = []
        page.on(
            "console",
            lambda m: console_errors.append(f"{m.text} @ {m.location.get('url', '')}")
            if m.type == "error"
            else None,
        )
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on(
            "response",
            lambda r: responses_4xx.append(f"{r.status} {r.url}")
            if r.status >= 400
            else None,
        )
        yield {
            "page": page,
            "console": console_errors,
            "pageerrors": page_errors,
            "responses_4xx": responses_4xx,
        }
        browser.close()


@pytest.fixture(scope="module")
def cctv_session(_browser):
    """CCTV 主场景就绪 → RAISED 可见窗口内事件驱动连拍（活页面竞态对策）。"""
    page = _browser["page"]
    requests.post(f"{BASE}/demo/reset", timeout=15)
    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
    first_frame = _poll_until(page, _FIRST_FRAME_JS, FIRST_FRAME_TIMEOUT_MS)
    perception_ready = _poll_until(page, _PERCEPTION_ROW_JS, PERCEPTION_DATA_TIMEOUT_MS)
    behavior_ready = _poll_until(page, _BEHAVIOR_ROW_JS, BEHAVIOR_DATA_TIMEOUT_MS)
    rows: list[dict[str, str]] | None = None
    deadline = time.monotonic() + RAISED_TIMEOUT_MS / 1000.0
    while time.monotonic() < deadline:
        rows = _try_capture_burst(page)
        if rows is not None:
            break
        page.wait_for_timeout(POLL_INTERVAL_MS)
    raised_ready = rows is not None
    page.wait_for_timeout(1_000)
    session: dict[str, Any] = {
        **_browser,
        "first_frame": first_frame,
        "perception_ready": perception_ready,
        "behavior_ready": behavior_ready,
        "raised_ready": raised_ready,
    }
    if not raised_ready:
        try:
            session["diagnostics"] = dict(page.evaluate(_DIAGNOSTICS_JS))
        except Exception:  # noqa: BLE001
            session["diagnostics"] = None
    else:
        health = requests.get(f"{BASE}/health", timeout=5).json()
        session["frame_index"] = health.get("frame_index")
        session["captured_at"] = datetime.now().astimezone().strftime(
            "%Y-%m-%d %H:%M:%S %z"
        )
        session["rows"] = rows
        session["manifest"] = str(_write_manifest(rows, session))
    return session


def test_h_raised_state_reached(cctv_session):
    """六图内容前提：RAISED 可见窗口内必须完成连拍（轮询重试超时即 FAIL）。"""
    assert cctv_session["first_frame"], "首帧未到达"
    assert cctv_session["perception_ready"], "感知流数据未涌现"
    assert cctv_session["behavior_ready"], "行为时间线数据未涌现"
    assert cctv_session["raised_ready"], (
        f"RAISED 可见窗口未在 {RAISED_TIMEOUT_MS}ms 内捕获；诊断="
        f"{cctv_session.get('diagnostics')}"
    )


def test_h_six_screenshots_captured(cctv_session):
    """固定六张已落盘：文件存在 + 尺寸非零 + md5 manifest 入库。"""
    assert cctv_session["raised_ready"], "前置 RAISED 未达成，未执行拍摄"
    rows = cctv_session["rows"]
    assert len(rows) == 6, f"截图数量 {len(rows)} != 6"
    for r in rows:
        path = OUT_DIR / r["file"]
        assert path.exists(), f"{r['file']} 未落盘"
        assert path.stat().st_size == int(r["bytes"]), (
            f"{r['file']} 大小与 manifest 不一致"
        )
    assert Path(cctv_session["manifest"]).exists(), "MANIFEST.md 未落盘"


def test_h_console_page_error_zero(cctv_session):
    """console error / pageerror = 0（已知豁免子串过滤）。"""
    diag = f"4xx responses: {cctv_session['responses_4xx'][:8]}"

    def _exempt(msg: str) -> bool:
        return any(pat in msg for pat in KNOWN_CONSOLE_EXEMPT)

    console_bad = [m for m in cctv_session["console"] if not _exempt(m)]
    page_bad = [m for m in cctv_session["pageerrors"] if not _exempt(m)]
    assert not console_bad, f"console error 非零: {console_bad[:5]}; {diag}"
    assert not page_bad, f"pageerror 非零: {page_bad[:5]}; {diag}"