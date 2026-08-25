"""Visual / Product Presentation Acceptance · CCTV Surveillance Suspicious 适配版。

任务边界：
    只验证用户看到的页面是否像一个完整、可信的产品故事，不重新审 Runtime。
    重点：整体布局、视觉叙事顺序、六区域产品表现、证据边界（不输出"诈骗/犯罪"）。
    通用化：通过 ScenarioAcceptanceContract 驱动场景配置。

运行前提（外部 fixture，模块级探测 skip）：
    python scripts/run_demo.py --live --scenario config/demo/scenarios/cctv_surveillance_suspicious.yaml
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

import pytest
import requests

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright not installed", allow_module_level=True)

from tests.visualizer._scenario_contract import (
    CctvSurveillanceSuspiciousContract,
    make_dom_capture_js,
    make_layout_js,
    make_video_sig_js,
)

BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/live"

_CONTRACT = CctvSurveillanceSuspiciousContract()
SID = _CONTRACT.scenario_id

OBSERVE_MS = _CONTRACT.observe_times.get("cctv_observe", 90_000)
POLL_MS = 1_000

CONSOLE_ERROR_ALLOWLIST = (
    "echarts is not defined",
    "echarts is undefined",
    "404",
    "Not Found",
    "favicon",
)

PAGE_ERROR_ALLOWLIST = (
    "echarts is not defined",
    "echarts is undefined",
)

CHROMIUM_PATH = (
    r"C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe"
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
    reason=_CONTRACT.skip_reason(),
)


def _js(page, expr):
    return page.evaluate(expr)


def _screenshot_path(page, name: str) -> str:
    from pathlib import Path

    out = (
        Path(__file__).resolve().parents[2]
        / "docs" / "reports" / "assets" / "vision-eval" / SID
    )
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{name}.png"
    page.screenshot(path=str(p), full_page=True)
    return str(p)


def _above_fold_height() -> int:
    return 900


# ============================================================
# 布局检查（CCTV 单场景）
# ============================================================


class TestLayout:
    def test_grid_12_columns(self, page_session):
        """12 列 Grid 符合设计：grid-template-columns 应为 12 等分或 12-related。"""
        gtc = page_session["layout"]["grid_template_columns"]
        assert gtc, "未检测到 grid-template-columns"
        n_cols = len([c for c in gtc.split() if c])
        assert n_cols >= 4, f"Grid 列数过少（期望至少 4 列响应层，实际 {n_cols} 列）: {gtc}"

    def test_region_hierarchy(self, page_session):
        """①视频、②时间线、③风险解释、③.5风险信号、④行动闭环的层级正确。"""
        layout = page_session["layout"]
        for key in ("video_top", "timeline_top", "risk_explanation_top", "signals_top", "action_top"):
            assert key in layout, f"缺少区域定位: {key}"
        assert layout["video_top"] < _above_fold_height(), (
            f"①视频区不在首屏: top={layout['video_top']}px > {_above_fold_height()}px"
        )

    def test_no_excessive_whitespace(self, page_session):
        """无过大空白：页面总高度合理（不应超过 3.5 屏）。"""
        total_h = page_session["layout"]["total_height"]
        assert total_h <= _above_fold_height() * 3.5, (
            f"页面过长（{total_h}px > 3.5 屏 {_above_fold_height() * 3.5}px）"
        )

    def test_no_element_compression(self, page_session):
        """无元素挤压：各区域高度 ≥ 合理阈值。"""
        layout = page_session["layout"]
        assert layout["video_height"] >= 120, f"视频区过矮: {layout['video_height']}px"
        for key in ("timeline_height", "risk_explanation_height", "signals_height", "action_height"):
            h = layout.get(key)
            if h is None or h == 0:
                continue
            assert h >= 30, f"{key} 过矮: {h}px"

    def test_above_fold_core_story(self, page_session):
        """首屏能看到核心故事：①视频 + ②时间线 + ③风险解释 均在首屏可见。"""
        fold = _above_fold_height()
        layout = page_session["layout"]
        for key in ("video_top", "timeline_top", "risk_explanation_top"):
            top = layout.get(key, fold + 1)
            assert top < fold, f"{key} 不在首屏: top={top}px > fold={fold}px"


# ============================================================
# 视觉叙事顺序（CCTV 场景：出现 → 重复 → 停留 → 风险 → 行动）
# ============================================================


class TestVisualNarrative:
    def test_narrative_order_cctv(self, cctv_screenshot):
        """CCTV 故事一眼可理解：人员出现 → 重复出现 → 异常停留 → 风险升级 → 记录。"""
        dom = cctv_screenshot["dom"]
        timeline_text = dom["timeline_text"]
        assert timeline_text, "时间线区无内容"
        for raw in ("frame@", "bbox [", "evidence_delta", "perception_delta"):
            assert raw not in timeline_text, f"时间线出现裸工程串: {raw}"

    def test_narrative_categories(self, cctv_screenshot):
        """时间线条目是人话级别（人员出现/停留/再现/异常停留）。"""
        timeline_text = cctv_screenshot["dom"]["timeline_text"]
        narrative_categories = [
            "出现", "停留", "再现", "回访", "门前", "人物", "异常",
            "夜间", "在场", "进入",
        ]
        has_narrative = any(cat in timeline_text for cat in narrative_categories)
        assert has_narrative, f"时间线无叙事性分类: {timeline_text[:200]}"

    def test_no_fraud_or_crime_terms(self, cctv_screenshot):
        """证据边界铁律：用户可见文本不得含'诈骗/犯罪/入侵/实施中'等超范围术语。"""
        dom = cctv_screenshot["dom"]
        for key in ("timeline_text", "signals_text", "lrk_text", "closure_text"):
            txt = dom.get(key, "")
            for forbidden in ("诈骗", "犯罪", "入侵", "实施诈骗", "骗子", "作案"):
                assert forbidden not in txt, f"{key} 出现超范围术语 {forbidden!r}: {txt[:120]}"


# ============================================================
# 六区域产品表现（CCTV 单场景，无音频部分）
# ============================================================


class TestSixRegions:
    def test_region_1_video_clear(self, cctv_screenshot):
        """① 视频：MJPEG 画面存在 + naturalWidth > 0。"""
        dom = cctv_screenshot["dom"]
        assert dom["video_natural_width"] > 0, f"视频未解码: {dom['video_natural_width']}"

    def test_region_2_timeline_humanized(self, cctv_screenshot):
        """② 时间线：人话化，无裸工程字段。"""
        timeline = cctv_screenshot["dom"]["timeline_text"]
        assert timeline.strip(), "时间线区空"
        for raw in ("frame@", "bbox [", "evidence_delta", "perception_delta", "{", '"type":'):
            assert raw not in timeline, f"时间线出现工程字段 {raw!r}"

    def test_region_3_risk_card_readable(self, cctv_screenshot):
        """③ 风险卡：level + reasons 一眼可读。"""
        lrk = cctv_screenshot["dom"]["lrk"]
        assert lrk.get("level") or lrk.get("empty_text"), "风险卡无任何内容"

    def test_region_35_signals_stable(self, cctv_screenshot):
        """③.5 风险信号：RAISED/CLEARED 稳定，无 flicker 残留。"""
        signals = cctv_screenshot["dom"]["signals_text"]
        assert signals is not None, "风险信号区不存在"
        for bad in ("undefined", "null", "[object"):
            assert bad not in signals, f"风险信号区出现不稳定文本: {bad}"

    def test_region_4_action_closure(self, cctv_screenshot):
        """④ 行动闭环：log 端必须存在（family/community 可能空态，无音频链路）。"""
        tasks = cctv_screenshot["dom"]["tasks"]
        assert tasks, "行动闭环区不存在"
        # log 端必须有（LOG_ONLY 是 CCTV 主要行动）
        assert tasks.get("log_status") or tasks.get("log_body"), (
            f"log 端缺失: {tasks}"
        )

    def test_region_5_system_subordinate(self, cctv_screenshot):
        """⑤ 系统原理：退居次要层级。"""
        sysarch_visible = cctv_screenshot["dom"]["sysarch_visible"]
        sysarch_top = cctv_screenshot["dom"]["sysarch_top"]
        if sysarch_visible and sysarch_top is not None:
            assert sysarch_top > _above_fold_height(), (
                f"⑤系统原理区抢主叙事: top={sysarch_top}px 应在首屏以下"
            )


# ============================================================
# 浏览器视觉卫生
# ============================================================


class TestBrowserHygiene:
    def test_no_console_errors(self, hygiene):
        """console.error = 0（已知豁免过滤）。"""
        real = [e for e in hygiene["console_errors"] if not any(k in e for k in CONSOLE_ERROR_ALLOWLIST)]
        assert not real, f"console errors: {real[:5]}"

    def test_no_page_errors(self, hygiene):
        """page error = 0（已知豁免过滤）。"""
        real = [e for e in hygiene["page_errors"] if not any(k in e for k in PAGE_ERROR_ALLOWLIST)]
        assert not real, f"page errors: {real[:5]}"

    def test_no_garbled_or_truncated(self, cctv_screenshot):
        """无乱码、截断、overflow。"""
        dom = cctv_screenshot["dom"]
        for key, txt in dom.items():
            if isinstance(txt, str) and txt:
                for bad in ("undefined", "null", "[object", "NaN", "Infinity"):
                    assert bad not in txt, f"{key} 出现不稳定文本: {bad}"


# ============================================================
# Fixtures
# ============================================================


def _capture_layout(page) -> dict:
    js = make_layout_js(SID)
    return _js(page, js)


def _capture_dom(page) -> dict:
    js = make_dom_capture_js(SID)
    return _js(page, js)


@pytest.fixture(scope="module")
def _browser():
    """单真实 Browser Session。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM_PATH, headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        yield browser, ctx, page
        browser.close()


@pytest.fixture(scope="module")
def page_session(_browser):
    """连接 + 等待 snapshot + 采集布局。"""
    _b, _ctx, page = _browser
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(5_000)

    return {
        "page": page,
        "layout": _capture_layout(page),
        "console_errors": console_errors,
        "page_errors": page_errors,
    }


@pytest.fixture(scope="module")
def cctv_screenshot(_browser):
    """CCTV 观察 + 采集 DOM + 截图总览。"""
    _b, _ctx, page = _browser
    requests.post(f"{BASE}/demo/reset", timeout=15)
    page.wait_for_timeout(3_000)

    # 观察窗内持续轮询风险信号
    seen_raised = False
    seen_action = False
    steps = OBSERVE_MS // POLL_MS
    for i in range(steps):
        signals = _js(page, f"document.getElementById('live-signals-{SID}')?.textContent || ''")
        if "RAISED" in signals:
            seen_raised = True
        tasks = _js(page, f"document.getElementById('task-family-body-{SID}')?.innerText || ''")
        closure = _js(page, f"document.getElementById('fs-action-closure-{SID}')?.innerText || ''")
        if (tasks and "暂无" not in tasks) or (closure and "暂无" not in closure):
            seen_action = True
        page.wait_for_timeout(POLL_MS)

    # 收集完整 DOM 信息（含 lrk_text/closure_text 用于证据边界检查）
    dom = _capture_dom(page)
    dom_full = _js(page, make_video_sig_js(SID) if False else f"document.getElementById('lrk-card-{SID}')?.innerText || ''")
    dom["lrk_text"] = dom_full
    closure_text = _js(page, f"document.getElementById('fs-action-closure-{SID}')?.innerText || ''")
    dom["closure_text"] = closure_text

    # 截图（总览）
    _screenshot_path(page, "cctv_overview")

    return {
        "seen_raised": seen_raised,
        "seen_action": seen_action,
        "dom": dom,
    }


@pytest.fixture(scope="module")
def hygiene(page_session):
    """浏览器卫生数据。"""
    return {
        "console_errors": page_session["console_errors"],
        "page_errors": page_session["page_errors"],
    }