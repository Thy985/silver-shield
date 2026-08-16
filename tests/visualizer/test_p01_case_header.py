"""P0-1 Case Header / Product Question 契约测试（产品化总原则 §2）。

覆盖：
- scenario yaml 解析 product_question（缺省空，向后兼容）；
- canonical 投影（IntegrationReport.build → product_question，canonical_dict 确定性含）；
- loader 投影（ScenarioEvidence.product_question；旧 canonical 无键 → 空串）；
- render Case Header（命题一句话渲染 / 无命题不渲染该行 / ▶ Play Case 按钮 /
  __playCase 引擎注入条件）；
- media.js __playCase 行为（node vm：滚动 + 播放，无播放器 no-op）。

不依赖 torch/cv2（纯 stdlib + 投影契约 fixture），可在 torch-free 环境跑。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from home_perception.visualizer.viewer import render_case_viewer
from home_perception.visualizer.viewer.artifact_source import load_case_presentation

from .conftest import make_artifacts

# ---------------------------------------------------------------------------
# 1. scenario 解析
# ---------------------------------------------------------------------------


def test_scenario_parses_product_question():
    """golden repeated_visit 声明 product_question → Scenario.meta 解析。"""
    from home_perception.validation.scenario import load_scenario

    scn = load_scenario(
        "src/home_perception/validation/fixtures/scenarios/golden/golden_repeated_visit.yaml"
    )
    assert scn.meta.product_question == "系统能使用历史事件改变当前风险判断"


def test_scenario_product_question_default_empty():
    """未声明 product_question → 空串（向后兼容）。"""
    from home_perception.validation.scenario.scenario import MetaSpec

    meta = MetaSpec(schema_version="1.0", scenario_id="sw_x", version=1)
    assert meta.product_question == ""


# ---------------------------------------------------------------------------
# 2. canonical 投影
# ---------------------------------------------------------------------------


def test_report_build_projects_product_question():
    """IntegrationReport.build：run_result.product_question → report 字段 + canonical_dict。"""
    from home_perception.integration.loop.report import IntegrationReport

    class _R:
        scenario_id = "sw_t1"
        mode = "detections"
        n_frames = 10
        fingerprint = "f"
        expectation_fingerprint = "e"
        loop_fingerprint = "l"
        product_question = "系统命题测试"
        perception_events = ()
        warnings = ()
        commands = ()
        sink_commands = ()
        decision_traces = ()
        episodes = ()
        audio_perception_events = ()
        cross_modal_links = ()

    class _V:
        scenario_id = "sw_t1"
        ok = True
        failure_codes = lambda self: ()
        stages = ()
        score = None

    report = IntegrationReport.build(_R(), _V())
    assert report.product_question == "系统命题测试"
    assert report.canonical_dict()["product_question"] == "系统命题测试"


def test_report_build_product_question_empty_default():
    """run_result 无 product_question → 空串（旧 run 形态兼容）。"""
    from home_perception.integration.loop.report import IntegrationReport

    class _R:
        scenario_id = "sw_t1"
        mode = "detections"
        n_frames = 10
        fingerprint = "f"
        expectation_fingerprint = "e"
        loop_fingerprint = "l"
        perception_events = ()
        warnings = ()
        commands = ()
        sink_commands = ()
        decision_traces = ()
        episodes = ()
        audio_perception_events = ()
        cross_modal_links = ()

    class _V:
        scenario_id = "sw_t1"
        ok = True
        failure_codes = lambda self: ()
        stages = ()
        score = None

    report = IntegrationReport.build(_R(), _V())
    assert report.product_question == ""


# ---------------------------------------------------------------------------
# 3. loader 投影
# ---------------------------------------------------------------------------


def _inject_product_question(artifacts_dir: Path, text: str) -> None:
    """往 canonical 顶层注入 product_question（模拟 CI 生产新契约）。"""
    p = artifacts_dir / "sw_t1.canonical.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["product_question"] = text
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_loader_projects_product_question(tmp_path):
    """canonical product_question → ScenarioEvidence.product_question。"""
    canon = make_artifacts(tmp_path / "a")
    _inject_product_question(canon, "系统命题一句话")
    proj = load_case_presentation(canon)[0]
    assert proj["scenarios"][0]["product_question"] == "系统命题一句话"


def test_loader_product_question_absent_empty(tmp_path):
    """旧 canonical 无 product_question → 空串（向后兼容，不崩）。"""
    canon = make_artifacts(tmp_path / "a")
    proj = load_case_presentation(canon)[0]
    assert proj["scenarios"][0]["product_question"] == ""


# ---------------------------------------------------------------------------
# 4. render Case Header
# ---------------------------------------------------------------------------


def test_render_case_header_with_proposition(tmp_path):
    """有 product_question → 渲染「This case demonstrates」+ ▶ Play Case。"""
    canon = make_artifacts(tmp_path / "a")
    _inject_product_question(canon, "系统能识别正常环境，并解释为什么没有触发")
    proj, desc = load_case_presentation(canon)
    html = render_case_viewer(proj, desc)
    assert "This case demonstrates" in html
    assert "系统能识别正常环境，并解释为什么没有触发" in html
    assert "▶ Play Case" in html
    assert "__playCase" in html  # media.js 引擎含（MediaPlayer 全局注入）


def test_render_case_header_no_proposition_no_line(tmp_path):
    """无 product_question → case-header 仍在（case 名 + Play），但命题行不渲染。"""
    canon = make_artifacts(tmp_path / "a")
    proj, desc = load_case_presentation(canon)
    html = render_case_viewer(proj, desc)
    assert 'class="case-header"' in html
    assert "This case demonstrates" not in html
    assert "▶ Play Case" in html  # Play 按钮恒渲染（case 名可播放）


# ---------------------------------------------------------------------------
# 5. media.js __playCase 行为（node vm）
# ---------------------------------------------------------------------------


def _media_source() -> str:
    from home_perception.visualizer.renderer import _media_inline

    src = _media_inline()
    assert src, "media.js 必须存在"
    return src


@pytest.mark.skipif(
    not __import__("shutil").which("node"),
    reason="CI 无 node 则跳过（html-inline-js 纪律）",
)
def test_play_case_js_scrolls_and_plays():
    """Node vm：__playCase 滚动到 video 面板 + 触发播放；无播放器 no-op。"""
    import os
    import subprocess
    import tempfile
    import textwrap

    src = _media_source()
    harness = textwrap.dedent(
        """
        const fs = require('fs');
        const videoPanel = {
          _scrolled: false,
          scrollIntoView: function() { this._scrolled = true; },
        };
        const player = {
          _played: false,
          play: function() { this._played = true; },
        };
        const doc = {
          readyState: 'complete',
          addEventListener: function(){},
          getElementById: function(id) {
            if (id === 'fs-case-video-sw_t1') return videoPanel;
            return null;
          },
        };
        global.document = doc;
        global.window = global;
        eval(fs.readFileSync(process.argv[2], 'utf-8'));
        // eval 后覆盖引擎为 mock（media.js IIFE 会重置 __MediaPlayer）。
        global.__MediaPlayer = { get: function(sid) { return sid === 'sw_t1' ? player : null; } };
        // __playCase 已由 media.js 定义；调用之。
        global.__playCase('sw_t1');
        const ok = videoPanel._scrolled && player._played;
        // 无播放器 no-op（不崩）
        global.__MediaPlayer = { get: function() { return null; } };
        let noopOk = true;
        try { global.__playCase('sw_t1'); } catch (e) { noopOk = false; }
        console.log(ok && noopOk ? 'PLAY_CASE_OK' : 'PLAY_CASE_FAIL');
        process.exit(ok && noopOk ? 0 : 1);
        """
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        harness_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        src_path = f.name
    r = subprocess.run(
        ["node", harness_path, src_path],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        check=False,
    )
    os.unlink(harness_path)
    os.unlink(src_path)
    assert r.returncode == 0, f"__playCase 行为失败: {r.stdout} {r.stderr}"
    assert "PLAY_CASE_OK" in r.stdout
