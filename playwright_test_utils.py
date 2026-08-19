"""Reusable Playwright test utilities for golden case E2E acceptance.

Provides common fixtures, helpers, and assertions for testing Live Viewer scenarios.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable
from playwright.sync_api import Page, Browser, sync_playwright

# Default configuration
DEFAULT_URL = "http://127.0.0.1:8765/live"
DEFAULT_TIMEOUT = 30000
DEFAULT_WAIT_MS = 5000


def find_browser_executable() -> str | None:
    """Find a suitable Chromium/Chrome executable on Windows."""
    candidates = [
        # Playwright bundled
        r'C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe',
        # System Chrome
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        # Edge (Chromium-based)
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    ]
    for c in candidates:
        p = Path(c)
        if p.exists():
            return str(p)
    return None  # Let Playwright use its bundled Chromium


BROWSER_PATH = find_browser_executable()


def launch_browser(p, headless: bool = True) -> Browser:
    """Launch browser with fallback to bundled Chromium."""
    if BROWSER_PATH:
        return p.chromium.launch(executable_path=BROWSER_PATH, headless=headless)
    return p.chromium.launch(headless=headless)


class LiveViewerTest:
    """Base class for Live Viewer E2E tests."""

    def __init__(
        self,
        scenario_id: str,
        url: str = DEFAULT_URL,
        headless: bool = True,
        wait_ms: int = DEFAULT_WAIT_MS,
    ):
        self.scenario_id = scenario_id
        self.url = url
        self.headless = headless
        self.wait_ms = wait_ms
        self._browser: Browser | None = None
        self._page: Page | None = None

    def __enter__(self) -> "LiveViewerTest":
        self._playwright = sync_playwright().start()
        self._browser = launch_browser(self._playwright, headless=self.headless)
        self._page = self._browser.new_page()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Test not started - use 'with LiveViewerTest(...) as test:'")
        return self._page

    def goto(self, wait_until: str = "networkidle") -> None:
        """Navigate to the live viewer URL."""
        self.page.goto(self.url, wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
        self.page.wait_for_timeout(self.wait_ms)

    def wait_for_frames(self, extra_ms: int = 5000) -> None:
        """Wait for video frames to start streaming."""
        self.page.wait_for_timeout(self.wait_ms + extra_ms)

    # --- Assertion Helpers ---

    def assert_live_state_initialized(self) -> dict:
        """Verify __LiveState is initialized and return it."""
        ls = self.page.evaluate("typeof __LiveState")
        assert ls == "object", "__LiveState should be defined"
        return self.page.evaluate("__LiveState")

    def assert_video_streaming(self) -> str:
        """Verify video element receives JPEG base64 frames."""
        img = self.page.locator(f"#video-img-{self.scenario_id}")
        assert img.count() == 1, "Video img element missing"
        src = img.evaluate("el => el.src")
        assert src.startswith("data:image/jpeg;base64,"), f"Expected JPEG data URI, got: {src[:80]}"
        assert len(src) > 10000, f"Base64 data too small: {len(src)} chars"

        # Placeholder should be hidden
        ph = self.page.locator(f"#video-ph-{self.scenario_id}")
        display = ph.evaluate("el => el.style.display")
        assert display == "none", f"Placeholder should be hidden, got display={display}"
        return src

    def assert_overlay_chips_updating(self) -> dict[str, str]:
        """Verify overlay chips (frame, case time, detections, visitor events) are updating."""
        frame_idx = self.page.locator(f"#ov-frame-{self.scenario_id}").inner_text()
        case_time = self.page.locator(f"#ov-time-{self.scenario_id}").inner_text()
        det_count = self.page.locator(f"#ov-det-{self.scenario_id}").inner_text()
        ve_count = self.page.locator(f"#ov-ve-{self.scenario_id}").inner_text()

        assert frame_idx != "–", f"Frame index not updating: {frame_idx}"
        assert case_time != "00:00", f"Case Time not updating: {case_time}"

        return {
            "frame_index": frame_idx,
            "case_time": case_time,
            "detections": det_count,
            "visitor_events": ve_count,
        }

    def assert_region_exists(self, region: str, selector: str) -> None:
        """Assert a cognitive region exists."""
        assert self.page.locator(selector).count() == 1, f"Region {region} missing: {selector}"

    def assert_all_six_regions(self) -> None:
        """Assert all 6 cognitive regions exist."""
        self.assert_region_exists("① 实时画面", f"#video-img-{self.scenario_id}")
        self.assert_region_exists("② AI 正在理解 - behavior", f"#behavior-timeline-{self.scenario_id}")
        self.assert_region_exists("② AI 正在理解 - acoustic", f"#acoustic-state-panel-{self.scenario_id}")
        self.assert_region_exists("③ 为什么值得关注", f"#lrk-card-{self.scenario_id}")
        self.assert_region_exists("⑤ AI 做了什么 - family", f"#task-family-{self.scenario_id}")
        self.assert_region_exists("⑤ AI 做了什么 - community", f"#task-community-{self.scenario_id}")
        self.assert_region_exists("⑤ AI 做了什么 - log", f"#task-log-{self.scenario_id}")
        self.assert_region_exists("⑥ 历史上下文", f"#memory-context-{self.scenario_id}")
        self.assert_region_exists("⑤ 系统原理", f"#lv-sysarch-{self.scenario_id}")

    def assert_provenance_banner(self, expected_text: str = "LIVE") -> str:
        """Verify provenance banner shows correct text."""
        banner = self.page.locator(".provenance-banner")
        assert banner.count() == 1, "Provenance banner missing"
        text = banner.inner_text()
        assert expected_text in text, f"Expected '{expected_text}' in banner, got: {text}"
        return text

    def assert_tab_switching(self) -> None:
        """Verify all 3 tabs can be switched without WS reconnect."""
        tabs = self.page.locator(f"#role-tabs-{self.scenario_id} .tab")
        assert tabs.count() == 3, "Expected 3 tabs"

        views = [
            ("① 风险发现", f"#view-discover-{self.scenario_id}"),
            ("② 家属确认", f"#view-family-{self.scenario_id}"),
            ("③ 社区处置", f"#view-community-{self.scenario_id}"),
        ]

        for i, (name, selector) in enumerate(views):
            tabs.nth(i).click()
            self.page.wait_for_timeout(500)
            assert self.page.locator(selector).count() == 1, f"Tab {name} view not visible"

    def take_screenshot(self, name: str, full_page: bool = True) -> str:
        """Take a screenshot for visual regression."""
        path = f"test_screenshots/{name}_{self.scenario_id}.png"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=path, full_page=full_page)
        return path


# --- Common Test Functions ---

def run_test(
    test_func: Callable[[LiveViewerTest], None],
    scenario_id: str,
    **kwargs,
) -> None:
    """Run a test function with proper setup/teardown."""
    with LiveViewerTest(scenario_id, **kwargs) as test:
        test.goto()
        test_func(test)


# --- Region-Specific Assertions ---

def assert_acoustic_state_panel(test: LiveViewerTest) -> dict:
    """Assert acoustic state panel renders correctly."""
    panel = test.page.locator(f"#acoustic-state-panel-{test.scenario_id}")
    assert panel.count() == 1, "Acoustic state panel missing"

    phases = panel.locator(".acoustic-phase")
    phase_count = phases.count()

    stress_score = panel.locator(".voice-stress-score")
    score_text = stress_score.first.inner_text() if stress_score.count() > 0 else None

    return {
        "phase_count": phase_count,
        "voice_stress_score": score_text,
    }


def assert_behavior_timeline(test: LiveViewerTest) -> dict:
    """Assert behavior timeline renders."""
    timeline = test.page.locator(f"#behavior-timeline-{test.scenario_id}")
    assert timeline.count() == 1, "Behavior timeline missing"

    items = timeline.locator(".tl-item")
    empty = timeline.locator(".tl-empty")
    item_count = items.count()

    return {
        "item_count": item_count,
        "has_empty_state": empty.count() > 0,
    }


def assert_lrk_card(test: LiveViewerTest) -> dict:
    """Assert LRK (Why) card renders."""
    card = test.page.locator(f"#lrk-card-{test.scenario_id}")
    assert card.count() == 1, "LRK card missing"

    level = test.page.locator(f"#lrk-level-{test.scenario_id}")
    reasons = test.page.locator(f"#lrk-reasons-{test.scenario_id}")
    empty = test.page.locator(f"#lrk-empty-{test.scenario_id}")

    reason_count = 0
    reason_texts = []
    if reasons.count() > 0:
        reason_items = reasons.locator("li")
        reason_count = reason_items.count()
        reason_texts = [reason_items.nth(i).inner_text() for i in range(reason_count)]

    return {
        "has_level": level.count() == 1,
        "level_text": level.inner_text() if level.count() == 1 else None,
        "reason_count": reason_count,
        "reason_texts": reason_texts,
        "shows_empty": empty.count() == 1,
    }


def assert_risk_signals(test: LiveViewerTest) -> dict:
    """Assert real-time risk signals (RAISED/CLEARED) render."""
    signals_container = test.page.locator(f"#live-signals-{test.scenario_id}")
    assert signals_container.count() == 1, "Live signals container missing"

    signals = test.page.evaluate("__LiveState.riskSignalMap") or {}
    raised = signals_container.locator(".rt-item.raised")
    cleared = signals_container.locator(".rt-item.cleared")

    return {
        "state_count": len(signals),
        "raised_rendered": raised.count(),
        "cleared_rendered": cleared.count(),
    }


def assert_action_closure(test: LiveViewerTest) -> dict:
    """Assert three-end action closure cards render."""
    results = {}
    for role in ("family", "community", "log"):
        card = test.page.locator(f"#task-{role}-{test.scenario_id}")
        status = test.page.locator(f"#task-{role}-status-{test.scenario_id}")
        body = test.page.locator(f"#task-{role}-body-{test.scenario_id}")

        results[role] = {
            "card_exists": card.count() == 1,
            "status_text": status.inner_text() if status.count() == 1 else None,
            "body_exists": body.count() == 1,
        }
    return results


def assert_audio_perception(test: LiveViewerTest) -> dict:
    """Assert audio perception panel renders."""
    ap = test.page.locator(f"#live-perception-{test.scenario_id}")
    assert ap.count() == 1, "Audio perception panel missing"

    audio_evidence = test.page.evaluate("__LiveState.audioEvidence || []") or []
    return {
        "audio_events_count": len(audio_evidence),
        "audio_events": audio_evidence,
    }


def assert_memory_context(test: LiveViewerTest) -> dict:
    """Assert memory context renders."""
    ctx = test.page.locator(f"#memory-context-{test.scenario_id}")
    assert ctx.count() == 1, "Memory context missing"

    historical = ctx.locator(".memory-layer.historical")
    current = ctx.locator(".memory-layer.current")

    historical_eps = historical.locator(".mem-ep-card")
    current_eps = current.locator(".mem-ep-card")

    evidence_link = ctx.locator(".memory-evidence-link .evidence-refs")
    refs_text = evidence_link.inner_text() if evidence_link.count() > 0 else ""

    return {
        "historical_layer_exists": historical.count() > 0,
        "current_layer_exists": current.count() > 0,
        "historical_episodes": historical_eps.count(),
        "current_episodes": current_eps.count(),
        "evidence_refs": refs_text,
    }


# --- Main entry for CLI ---

def main():
    """Run a quick smoke test if invoked directly."""
    import argparse
    parser = argparse.ArgumentParser(description="Quick smoke test for Live Viewer")
    parser.add_argument("--scenario", default="repeated_visit", help="Scenario ID")
    parser.add_argument("--url", default=DEFAULT_URL, help="Live viewer URL")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless")
    args = parser.parse_args()

    print(f"Testing {args.scenario} at {args.url}")

    def smoke_test(test: LiveViewerTest):
        test.assert_live_state_initialized()
        test.assert_video_streaming()
        test.assert_overlay_chips_updating()
        test.assert_all_six_regions()
        test.assert_provenance_banner()
        test.assert_tab_switching()
        print("✅ Smoke test PASSED")

    try:
        run_test(smoke_test, args.scenario, url=args.url, headless=args.headless)
    except Exception as e:
        print(f"❌ Smoke test FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()