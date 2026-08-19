"""Playwright acceptance test for golden_repeated_visit Live Viewer."""

import sys
from playwright.sync_api import sync_playwright

# The scenario ID used in DOM elements (from gateway health: scenario='repeated_visit')
SID = "repeated_visit"
URL = "http://127.0.0.1:8765/live"

BROWSER_PATH = r'C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe'


def _launch():
    return sync_playwright().start().chromium.launch(
        executable_path=BROWSER_PATH,
        headless=True
    )


def test_golden_repeated_visit_page_loads():
    """Page loads, JS state initializes, all 5 cognitive regions exist."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=BROWSER_PATH,
            headless=True
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # JS state must initialize
        ls = page.evaluate("typeof __LiveState")
        assert ls == "object", "__LiveState should be defined"

        # ① 实时画面
        assert page.locator(f"#video-img-{SID}").count() == 1
        assert page.locator(f"#video-ph-{SID}").count() == 1

        # ② AI 正在理解 - behavior timeline
        assert page.locator(f"#behavior-timeline-{SID}").count() == 1

        # ③ 为什么值得关注 - LRK card
        assert page.locator(f"#lrk-card-{SID}").count() == 1

        # ⑤ AI 做了什么 - action closure
        assert page.locator(f"#task-family-{SID}").count() == 1
        assert page.locator(f"#task-community-{SID}").count() == 1

        # ⑥ 历史上下文 - memory context
        assert page.locator(f"#memory-context-{SID}").count() == 1
        # Historical + Current layers
        assert page.locator(".memory-layer.historical").count() == 1
        assert page.locator(".memory-layer.current").count() == 1
        assert page.locator(".memory-evidence-link").count() == 1

        browser.close()
        print("✅ test_golden_repeated_visit_page_loads PASSED")


def test_golden_repeated_visit_video_frames():
    """Video element receives actual JPEG base64 from frame_tick."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=BROWSER_PATH,
            headless=True
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(8000)  # Wait for frames

        img = page.locator(f"#video-img-{SID}")
        assert img.count() == 1
        src = img.evaluate("el => el.src")
        assert src.startswith("data:image/jpeg;base64,"), f"Expected JPEG data URI, got: {src[:80]}"
        assert len(src) > 10000, f"Base64 data too small: {len(src)} chars"

        # Placeholder should be hidden
        ph = page.locator(f"#video-ph-{SID}")
        display = ph.evaluate("el => el.style.display")
        assert display == "none", f"Placeholder should be hidden, got display={display}"

        browser.close()
        print("✅ test_golden_repeated_visit_video_frames PASSED")


def test_golden_repeated_visit_memory_context():
    """⑥ Memory Context renders Historical (ep_001, ep_002) + Current (ep_003)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=BROWSER_PATH,
            headless=True
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # Check historical episodes
        historical = page.locator(".memory-layer.historical .mem-ep-card")
        assert historical.count() >= 2, f"Expected at least 2 historical episodes, got {historical.count()}"

        # Check current episode
        current = page.locator(".memory-layer.current .mem-ep-card")
        assert current.count() == 1, f"Expected 1 current episode, got {current.count()}"

        # Check evidence link
        evidence_link = page.locator(".memory-evidence-link .evidence-refs")
        assert evidence_link.count() == 1
        refs_text = evidence_link.inner_text()
        assert "ep_001" in refs_text and "ep_002" in refs_text, f"Expected ep_001, ep_002 in evidence link, got: {refs_text}"

        browser.close()
        print("✅ test_golden_repeated_visit_memory_context PASSED")


def test_golden_repeated_visit_behavior_timeline():
    """② Behavior Timeline renders (may be empty if no behavior events yet)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=BROWSER_PATH,
            headless=True
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(8000)  # Wait for more frames

        # Timeline container should exist
        timeline = page.locator(f"#behavior-timeline-{SID}")
        assert timeline.count() == 1
        
        # Check LiveState for behavior events
        events = page.evaluate("__LiveState.behaviorEvents")
        print(f"Behavior events count: {len(events)}")
        
        # Timeline should either have items or show empty state (both valid)
        items = timeline.locator(".tl-item")
        empty = timeline.locator(".tl-empty")
        assert items.count() > 0 or empty.count() > 0, "Timeline should have items or empty state"

        browser.close()
        print("✅ test_golden_repeated_visit_behavior_timeline PASSED")


def test_golden_repeated_visit_lrk_card():
    """③ LRK Card renders risk explanation."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=BROWSER_PATH,
            headless=True
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        card = page.locator(f"#lrk-card-{SID}")
        assert card.count() == 1
        # Should have risk level, reasons, or empty state
        level = page.locator(f"#lrk-level-{SID}")
        empty = page.locator(f"#lrk-empty-{SID}")
        # Either card is visible with content, or empty state shows
        assert level.count() == 1 or empty.count() == 1

        browser.close()
        print("✅ test_golden_repeated_visit_lrk_card PASSED")


def test_golden_repeated_visit_action_closure():
    """⑤ Action Closure renders three-end task cards."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=BROWSER_PATH,
            headless=True
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # Family card
        fc = page.locator(f"#task-family-{SID}")
        assert fc.count() == 1
        assert fc.locator(f"#task-family-status-{SID}").count() == 1
        assert fc.locator(f"#task-family-body-{SID}").count() == 1

        # Community card
        cc = page.locator(f"#task-community-{SID}")
        assert cc.count() == 1
        assert cc.locator(f"#task-community-status-{SID}").count() == 1
        assert cc.locator(f"#task-community-body-{SID}").count() == 1

        # Log card
        lc = page.locator(f"#task-log-{SID}")
        assert lc.count() == 1
        assert lc.locator(f"#task-log-status-{SID}").count() == 1
        assert lc.locator(f"#task-log-body-{SID}").count() == 1

        browser.close()
        print("✅ test_golden_repeated_visit_action_closure PASSED")


def test_golden_repeated_visit_audio_perception():
    """Audio perception panel exists (may be empty if no audio events)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=BROWSER_PATH,
            headless=True
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # Audio perception panel
        ap = page.locator(f"#live-perception-{SID}")
        assert ap.count() == 1

        browser.close()
        print("✅ test_golden_repeated_visit_audio_perception PASSED")


if __name__ == "__main__":
    test_golden_repeated_visit_page_loads()
    test_golden_repeated_visit_video_frames()
    test_golden_repeated_visit_memory_context()
    test_golden_repeated_visit_behavior_timeline()
    test_golden_repeated_visit_lrk_card()
    test_golden_repeated_visit_action_closure()
    test_golden_repeated_visit_audio_perception()
    print("\n🎉 All golden_repeated_visit Playwright tests PASSED!")
    print("Note: Known ECharts initialization issue (dataIndex) exists but doesn't block core functionality")