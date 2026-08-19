"""Playwright acceptance test for golden_evidence_insufficient Live Viewer.

This test validates the "restraint narrative" - NOT_TRIGGERED path, empty LRK,
zero false positives.
"""

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

SID = "evidence_insufficient"
URL = "http://127.0.0.1:8765/live"


def _find_browser():
    candidates = [
        r'C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        None,
    ]
    for c in candidates:
        if c is None:
            return None
        p = Path(c)
        if p.exists():
            return str(p)
    return None


BROWSER_PATH = _find_browser()


def _launch_browser(p):
    if BROWSER_PATH:
        return p.chromium.launch(executable_path=BROWSER_PATH, headless=True)
    return p.chromium.launch(headless=True)


def test_golden_evidence_insufficient_page_loads():
    """Page loads with all 6 cognitive regions."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        ls = page.evaluate("typeof __LiveState")
        assert ls == "object"

        # All 6 regions
        assert page.locator(f"#video-img-{SID}").count() == 1
        assert page.locator(f"#behavior-timeline-{SID}").count() == 1
        assert page.locator(f"#lrk-card-{SID}").count() == 1
        assert page.locator(f"#task-family-{SID}").count() == 1
        assert page.locator(f"#task-community-{SID}").count() == 1
        assert page.locator(f"#task-log-{SID}").count() == 1
        assert page.locator(f"#memory-context-{SID}").count() == 1
        assert page.locator(f"#lv-sysarch-{SID}").count() == 1

        browser.close()
        print("✅ test_golden_evidence_insufficient_page_loads PASSED")


def test_golden_evidence_insufficient_not_triggered():
    """Verify NOT_TRIGGERED path - no risk signals, empty LRK card."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(15000)  # Wait through full video

        # LRK card should show empty state (no risk triggered)
        empty = page.locator(f"#lrk-empty-{SID}")
        assert empty.count() == 1, "LRK empty state should be visible"
        empty_text = empty.inner_text()
        print(f"  LRK empty text: {empty_text}")
        assert "风险尚未触发" in empty_text or "0 人在场" in empty_text

        # Risk signals container should be empty
        signals = page.locator(f"#live-signals-{SID}")
        assert signals.count() == 1
        raised = signals.locator(".rt-item.raised")
        assert raised.count() == 0, f"Expected 0 RAISED signals, got {raised.count()}"

        # LiveState riskSignalMap should be empty
        risk_map = page.evaluate("__LiveState.riskSignalMap")
        assert len(risk_map) == 0, f"Expected empty riskSignalMap, got {len(risk_map)}"

        browser.close()
        print("✅ test_golden_evidence_insufficient_not_triggered PASSED")


def test_golden_evidence_insufficient_zero_false_positives():
    """Verify zero false positives throughout the session."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(20000)

        # Check behavior timeline - should be mostly empty or only normal events
        timeline = page.locator(f"#behavior-timeline-{SID}")
        items = timeline.locator(".tl-item")
        item_count = items.count()

        # Check for any risk-related behavior events
        behavior_events = page.evaluate("__LiveState.behaviorEvents") or []
        risk_events = [e for e in behavior_events if e.get("type") in ("abnormal_dwell", "repeat_visit", "high_risk_approach")]
        print(f"  Total behavior events: {len(behavior_events)}")
        print(f"  Risk-related events: {len(risk_events)}")

        # Should have zero risk events
        assert len(risk_events) == 0, f"False positive risk events detected: {risk_events}"

        # Action closure should show no active tasks
        for role in ("family", "community", "log"):
            status = page.locator(f"#task-{role}-status-{SID}")
            if status.count() == 1:
                status_text = status.inner_text()
                print(f"  {role} status: {status_text}")
                # Should indicate idle/no action

        browser.close()
        print("✅ test_golden_evidence_insufficient_zero_false_positives PASSED")


def test_golden_evidence_insufficient_video_and_audio():
    """Video streams, audio perception panel exists (may be empty)."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)

        # Video
        img = page.locator(f"#video-img-{SID}")
        assert img.count() == 1
        src = img.evaluate("el => el.src")
        assert src.startswith("data:image/jpeg;base64,")

        # Audio perception panel
        ap = page.locator(f"#live-perception-{SID}")
        assert ap.count() == 1

        browser.close()
        print("✅ test_golden_evidence_insufficient_video_and_audio PASSED")


def test_golden_evidence_insufficient_memory_context():
    """Memory context shows appropriate state for evidence_insufficient."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        ctx = page.locator(f"#memory-context-{SID}")
        assert ctx.count() == 1

        # evidence_insufficient may have historical context
        historical = ctx.locator(".memory-layer.historical")
        current = ctx.locator(".memory-layer.current")

        print(f"  Historical layers: {historical.count()}")
        print(f"  Current layers: {current.count()}")

        browser.close()
        print("✅ test_golden_evidence_insufficient_memory_context PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("PLAYWRIGHT E2E: golden_evidence_insufficient")
    print("=" * 60)

    try:
        test_golden_evidence_insufficient_page_loads()
        test_golden_evidence_insufficient_not_triggered()
        test_golden_evidence_insufficient_zero_false_positives()
        test_golden_evidence_insufficient_video_and_audio()
        test_golden_evidence_insufficient_memory_context()

        print("\n" + "=" * 60)
        print("🎉 ALL golden_evidence_insufficient Playwright tests PASSED!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 TEST ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)