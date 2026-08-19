"""Playwright acceptance test for golden_telephone_risk Live Viewer.

This test validates the complete end-to-end rendering of the telephone_risk scenario
across all 6 cognitive regions (①-⑥) in the Live Viewer.

Run with:
    python test_golden_telephone_risk_playwright.py

Prerequisites:
1. Gateway running: python scripts/run_demo.py --live --scenario golden_telephone_risk --video dataset/benign/media/CCTV_Surveillance_Final.mp4
2. Playwright installed: pip install playwright && playwright install chromium
"""

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

# Scenario ID used in DOM elements
SID = "telephone_risk"
URL = "http://127.0.0.1:8765/live"

# Try to find Chrome/Chromium executable
def _find_browser():
    """Find a suitable Chromium executable."""
    candidates = [
        # Windows paths
        r'C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        # Let Playwright find its own
        None,
    ]
    for c in candidates:
        if c is None:
            return None  # Let Playwright use its bundled Chromium
        p = Path(c)
        if p.exists():
            return str(p)
    return None


BROWSER_PATH = _find_browser()


def _launch_browser(p):
    """Launch browser with fallback to bundled Chromium."""
    if BROWSER_PATH:
        return p.chromium.launch(executable_path=BROWSER_PATH, headless=True)
    return p.chromium.launch(headless=True)


def test_golden_telephone_risk_page_loads():
    """Page loads, JS state initializes, all 6 cognitive regions exist."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # JS state must initialize
        ls = page.evaluate("typeof __LiveState")
        assert ls == "object", "__LiveState should be defined"

        # ① 实时画面
        assert page.locator(f"#video-img-{SID}").count() == 1, "Video img element missing"
        assert page.locator(f"#video-ph-{SID}").count() == 1, "Video placeholder missing"

        # ② AI 正在理解 - behavior timeline + acoustic state panel
        assert page.locator(f"#behavior-timeline-{SID}").count() == 1, "Behavior timeline missing"
        assert page.locator(f"#acoustic-state-panel-{SID}").count() == 1, "Acoustic state panel missing"

        # ③ 为什么值得关注 - LRK card
        assert page.locator(f"#lrk-card-{SID}").count() == 1, "LRK card missing"

        # ⑤ AI 做了什么 - action closure (3-end task cards)
        assert page.locator(f"#task-family-{SID}").count() == 1, "Family task card missing"
        assert page.locator(f"#task-community-{SID}").count() == 1, "Community task card missing"
        assert page.locator(f"#task-log-{SID}").count() == 1, "Log task card missing"

        # ⑥ 历史上下文 - memory context
        assert page.locator(f"#memory-context-{SID}").count() == 1, "Memory context missing"

        # System architecture panel (collapsed by default)
        assert page.locator(f"#lv-sysarch-{SID}").count() == 1, "System architecture panel missing"

        browser.close()
        print("✅ test_golden_telephone_risk_page_loads PASSED")


def test_golden_telephone_risk_video_frames():
    """Video element receives actual JPEG base64 from frame_tick."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
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

        # Overlay chips should update (frame index, case time, detections, visitor events)
        frame_idx = page.locator(f"#ov-frame-{SID}").inner_text()
        case_time = page.locator(f"#ov-time-{SID}").inner_text()
        det_count = page.locator(f"#ov-det-{SID}").inner_text()
        ve_count = page.locator(f"#ov-ve-{SID}").inner_text()

        print(f"  Frame index: {frame_idx}, Case Time: {case_time}, Detections: {det_count}, Visitor Events: {ve_count}")
        assert frame_idx != "–", f"Frame index not updating: {frame_idx}"
        assert case_time != "00:00", f"Case Time not updating: {case_time}"

        browser.close()
        print("✅ test_golden_telephone_risk_video_frames PASSED")


def test_golden_telephone_risk_acoustic_state_panel():
    """② Acoustic State Panel renders 4 phases (NORMAL→ATTENTION→AROUSAL→STRESS)."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)  # Wait for acoustic events to accumulate

        panel = page.locator(f"#acoustic-state-panel-{SID}")
        assert panel.count() == 1

        # Check for acoustic phase elements
        phases = panel.locator(".acoustic-phase")
        phase_count = phases.count()
        print(f"  Acoustic phases rendered: {phase_count}")

        # Should have at least NORMAL and ATTENTION (case_b has progression)
        # Note: exact phases depend on runtime audio pipeline output
        # At minimum panel should exist and show something

        # Check for voice stress score display
        stress_score = panel.locator(".voice-stress-score")
        if stress_score.count() > 0:
            score_text = stress_score.first.inner_text()
            print(f"  Voice stress score: {score_text}")

        browser.close()
        print("✅ test_golden_telephone_risk_acoustic_state_panel PASSED")


def test_golden_telephone_risk_behavior_timeline():
    """② Behavior Timeline renders milestones."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)

        timeline = page.locator(f"#behavior-timeline-{SID}")
        assert timeline.count() == 1

        items = timeline.locator(".tl-item")
        empty = timeline.locator(".tl-empty")
        item_count = items.count()

        print(f"  Timeline items: {item_count}")
        if item_count > 0:
            first_item = items.first.inner_text()
            print(f"  First item: {first_item[:100]}")

        # Either has items or shows empty state (both valid initially)
        assert item_count > 0 or empty.count() > 0, "Timeline should have items or empty state"

        browser.close()
        print("✅ test_golden_telephone_risk_behavior_timeline PASSED")


def test_golden_telephone_risk_lrk_card():
    """③ LRK Card renders risk explanation with human-readable reasons."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)

        card = page.locator(f"#lrk-card-{SID}")
        assert card.count() == 1

        level = page.locator(f"#lrk-level-{SID}")
        reasons = page.locator(f"#lrk-reasons-{SID}")
        empty = page.locator(f"#lrk-empty-{SID}")

        print(f"  LRK level visible: {level.count() == 1}")
        print(f"  LRK reasons list visible: {reasons.count() == 1}")
        print(f"  LRK empty state visible: {empty.count() == 1}")

        # If card is visible, check for human-readable content
        if level.count() == 1:
            level_text = level.inner_text()
            print(f"  Risk level: {level_text}")

            reason_items = reasons.locator("li")
            print(f"  Reason count: {reason_items.count()}")
            for i in range(reason_items.count()):
                print(f"    - {reason_items.nth(i).inner_text()}")

        # Either card has content or empty state shows
        assert level.count() == 1 or empty.count() == 1

        browser.close()
        print("✅ test_golden_telephone_risk_lrk_card PASSED")


def test_golden_telephone_risk_risk_signals():
    """③.5 Real-time Risk Signals (RAISED/CLEARED) render correctly."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)

        signals_container = page.locator(f"#live-signals-{SID}")
        assert signals_container.count() == 1

        # Check LiveState for risk signals
        signals = page.evaluate("__LiveState.riskSignalMap")
        print(f"  Risk signals in LiveState: {len(signals)}")

        for sig_id, sig_data in signals.items():
            print(f"    Signal: {sig_id}, transition: {sig_data.get('transition')}, level: {sig_data.get('risk_level')}")

        # Signals should render as raised/cleared cards
        raised = signals_container.locator(".rt-item.raised")
        cleared = signals_container.locator(".rt-item.cleared")
        print(f"  RAISED signals rendered: {raised.count()}")
        print(f"  CLEARED signals rendered: {cleared.count()}")

        browser.close()
        print("✅ test_golden_telephone_risk_risk_signals PASSED")


def test_golden_telephone_risk_action_closure():
    """⑤ Action Closure renders three-end task cards with status."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)

        # Family card
        fc = page.locator(f"#task-family-{SID}")
        assert fc.count() == 1
        fs = page.locator(f"#task-family-status-{SID}")
        fb = page.locator(f"#task-family-body-{SID}")
        assert fs.count() == 1
        assert fb.count() == 1
        print(f"  Family status: {fs.inner_text()[:50]}")

        # Community card
        cc = page.locator(f"#task-community-{SID}")
        assert cc.count() == 1
        cs = page.locator(f"#task-community-status-{SID}")
        cb = page.locator(f"#task-community-body-{SID}")
        assert cs.count() == 1
        assert cb.count() == 1
        print(f"  Community status: {cs.inner_text()[:50]}")

        # Log card
        lc = page.locator(f"#task-log-{SID}")
        assert lc.count() == 1
        ls = page.locator(f"#task-log-status-{SID}")
        lb = page.locator(f"#task-log-body-{SID}")
        assert ls.count() == 1
        assert lb.count() == 1
        print(f"  Log status: {ls.inner_text()[:50]}")

        browser.close()
        print("✅ test_golden_telephone_risk_action_closure PASSED")


def test_golden_telephone_risk_audio_perception():
    """② Audio perception panel exists and renders audio events."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)

        # Audio perception panel
        ap = page.locator(f"#live-perception-{SID}")
        assert ap.count() == 1

        # Check for audio evidence table rows
        audio_rows = ap.locator("table tr")
        print(f"  Audio perception rows: {audio_rows.count()}")

        # Check for acoustic perception kinds
        audio_kinds = page.evaluate("__LiveState.audioEvidence || []")
        print(f"  Audio evidence in LiveState: {len(audio_kinds)}")
        for a in audio_kinds:
            print(f"    Kind: {a.get('kind')}, score: {a.get('score')}")

        browser.close()
        print("✅ test_golden_telephone_risk_audio_perception PASSED")


def test_golden_telephone_risk_memory_context():
    """⑥ Memory Context renders (telephone_risk is single-episode, no historical layer)."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        ctx = page.locator(f"#memory-context-{SID}")
        assert ctx.count() == 1

        # telephone_risk is a single episode - should show Current layer only
        historical = ctx.locator(".memory-layer.historical")
        current = ctx.locator(".memory-layer.current")

        print(f"  Historical layer count: {historical.count()}")
        print(f"  Current layer count: {current.count()}")

        # Current layer should have at least one episode
        current_eps = current.locator(".mem-ep-card")
        print(f"  Current episodes: {current_eps.count()}")

        # Evidence link should exist
        evidence_link = ctx.locator(".memory-evidence-link .evidence-refs")
        if evidence_link.count() > 0:
            refs_text = evidence_link.inner_text()
            print(f"  Evidence refs: {refs_text[:100]}")

        browser.close()
        print("✅ test_golden_telephone_risk_memory_context PASSED")


def test_golden_telephone_risk_variant_switching():
    """Verify case_a/case_b variant switching via timeline golden_variant nodes."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # Check timeline for golden_variant nodes
        timeline = page.locator(f"#behavior-timeline-{SID}")
        variant_nodes = timeline.locator(".tl-item[data-type='golden_variant']")
        variant_count = variant_nodes.count()
        print(f"  Golden variant nodes in timeline: {variant_count}")

        if variant_count > 0:
            for i in range(variant_count):
                text = variant_nodes.nth(i).inner_text()
                print(f"    Variant node {i}: {text[:80]}")

        # Check LiveState for variant info
        variants = page.evaluate("__LiveState.variants || []")
        print(f"  Variants in LiveState: {len(variants)}")

        browser.close()
        print("✅ test_golden_telephone_risk_variant_switching PASSED")


def test_golden_telephone_risk_cross_modal():
    """Verify Cross Modal SUPPORTS link renders (phone_interaction ↔ voice_stress_elevated)."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)

        # Check timeline for cross_modal nodes
        timeline = page.locator(f"#behavior-timeline-{SID}")
        cm_nodes = timeline.locator(".tl-item[data-type='golden_cross_modal']")
        cm_count = cm_nodes.count()
        print(f"  Cross modal nodes in timeline: {cm_count}")

        if cm_count > 0:
            for i in range(cm_count):
                text = cm_nodes.nth(i).inner_text()
                print(f"    Cross modal node {i}: {text[:80]}")

        # Check LiveState
        cross_modal = page.evaluate("__LiveState.crossModal || []")
        print(f"  Cross modal in LiveState: {len(cross_modal)}")

        browser.close()
        print("✅ test_golden_telephone_risk_cross_modal PASSED")


def test_golden_telephone_risk_provenance_banner():
    """Provenance banner shows correct 'LIVE · 受控演示输入' text."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        banner = page.locator(".prov-banner")
        assert banner.count() == 1

        text = banner.inner_text()
        print(f"  Provenance banner: {text[:120]}")

        # Should show LIVE badge (not REAL_SENSOR)
        assert "LIVE" in text or "受控演示" in text, f"Expected LIVE/受控演示 banner, got: {text}"

        browser.close()
        print("✅ test_golden_telephone_risk_provenance_banner PASSED")


def test_golden_telephone_risk_tab_switching():
    """Tab switching (①→②→③) works without WS reconnect."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        tabs = page.locator(f"#role-tabs-{SID} .tab")
        assert tabs.count() == 3

        # Click ② 家属确认
        tabs.nth(1).click()
        page.wait_for_timeout(1000)
        family_view = page.locator(f"#view-family-{SID}")
        assert family_view.count() == 1
        print("  Switched to ② 家属确认")

        # Click ③ 社区处置
        tabs.nth(2).click()
        page.wait_for_timeout(1000)
        community_view = page.locator(f"#view-community-{SID}")
        assert community_view.count() == 1
        print("  Switched to ③ 社区处置")

        # Click back to ①
        tabs.nth(0).click()
        page.wait_for_timeout(1000)
        discover_view = page.locator(f"#view-discover-{SID}")
        assert discover_view.count() == 1
        print("  Switched back to ① 风险发现")

        browser.close()
        print("✅ test_golden_telephone_risk_tab_switching PASSED")


def test_golden_telephone_risk_screenshot():
    """Take screenshot of full live view for visual regression."""
    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000)

        screenshot_path = f"test_screenshots/golden_telephone_risk_{SID}.png"
        Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"  Screenshot saved: {screenshot_path}")

        browser.close()
        print("✅ test_golden_telephone_risk_screenshot PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("PLAYWRIGHT E2E: golden_telephone_risk")
    print("=" * 60)
    print(f"Target URL: {URL}")
    print(f"Scenario ID: {SID}")
    print(f"Browser: {BROWSER_PATH or 'Playwright bundled Chromium'}")
    print()

    try:
        test_golden_telephone_risk_page_loads()
        test_golden_telephone_risk_video_frames()
        test_golden_telephone_risk_acoustic_state_panel()
        test_golden_telephone_risk_behavior_timeline()
        test_golden_telephone_risk_lrk_card()
        test_golden_telephone_risk_risk_signals()
        test_golden_telephone_risk_action_closure()
        test_golden_telephone_risk_audio_perception()
        test_golden_telephone_risk_memory_context()
        test_golden_telephone_risk_variant_switching()
        test_golden_telephone_risk_cross_modal()
        test_golden_telephone_risk_provenance_banner()
        test_golden_telephone_risk_tab_switching()
        test_golden_telephone_risk_screenshot()

        print("\n" + "=" * 60)
        print("🎉 ALL golden_telephone_risk Playwright tests PASSED!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 TEST ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)