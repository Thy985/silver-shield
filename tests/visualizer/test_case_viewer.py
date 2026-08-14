"""ADR-0036 Slice A · Case Viewer 测试：静态 AC 不变量 + 运行时 Node vm 行为级验证。

覆盖验收：AC-1/1b/1c（VM-1 第二份事实模型禁令）、AC-7（Provenance 一等视觉）、
AC-9（统一时间轴，无三套独立轴）、AC-13（VM-11 展示编排不含事实）、AC-14（VM-10 双时间轴
+ Case Time）、AC-15（VM-12 Case Video ≠ Analysis Video）、AC-16（首屏叙事层级）、
AC-2（复用 renderer 语义一致）、VM-3（viewer 不 import silver_demo / 生产）、确定性。

运行时行为级测试（graph 联动 + Case Time 同步）复用 D2.2/D2.3 的 Node vm 范式：仅字符串
断言抓不到 `ReferenceError` 等运行时错误（历史 bug），必须用真实执行验证。CI 无 node 时跳过。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from home_perception.visualizer.viewer import (
    build_default_case_presentation,
    load_case_artifact,
    load_case_descriptor,
    render_case_viewer,
)
from home_perception.visualizer.viewer.media_source import resolve_media_source
from home_perception.visualizer.viewer.render import (
    _render_case_video,
    _render_provenance_banner,
    _safe_media_src,
)

from .conftest import make_artifacts, make_media_asset

_VIEWER_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "home_perception" / "visualizer" / "viewer"


def _render(directory) -> str:
    return render_case_viewer(load_case_artifact(directory))


def _render_with_media(directory) -> str:
    """Slice A.1：带媒体资产渲染（经 Media Source Adapter 只读解析 manifest）。"""
    return render_case_viewer(
        load_case_artifact(directory),
        media_base_dir=directory,
        media_base_url="",
    )


def _load_run_case_viewer():
    """加载 scripts/run_case_viewer.py CLI 模块（非包，按需 importlib 装配）。"""
    import importlib.util

    cli_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_case_viewer.py"
    spec = importlib.util.spec_from_file_location("run_case_viewer_cli", str(cli_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-1 / AC-1b / AC-1c（VM-1）：前端不得出现第二份业务事实状态
# ---------------------------------------------------------------------------

# 这些 Token 一旦出现即代表"第二份业务事实模型"，违背 VM-1（AC-1/1b/1c）。
_FORBIDDEN_FACT_STATE_TOKENS = (
    "riskData",
    "decisionData",
    "timelineData",
    "RiskState",
    "DecisionState",
    "TimelineState",
    "GraphState",
    "AudioState",
    "AudioFactState",
)


def test_viewer_vm1_no_second_fact_model(tmp_path):
    """AC-1/1b/1c：生成的 Case Viewer HTML 不得含 riskData/decisionData/timelineData/
    RiskState/DecisionState/TimelineState/GraphState/AudioState/AudioFactState。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    for tok in _FORBIDDEN_FACT_STATE_TOKENS:
        assert tok not in html, f"VM-1 违规：HTML 含第二份事实模型 Token {tok!r}"


def test_viewer_vm11_descriptor_no_fact_fields():
    """AC-13：默认展示编排不得含 case_risk_level/case_decision/case_timeline 等事实字段。"""
    proj = {
        "meta": {"generated_at": "2026-08-14T00:00:00+00:00", "scenario_count": 1},
        "scenarios": (
            {
                "scenario_id": "sw_t1", "ok": True, "mode": "detections", "n_frames": 1,
                "scenario_fingerprint": "x", "counts": {}, "event_types": (),
                "risk_levels": (), "recommended_actions": (), "command_types": (),
                "trace_outcome_kinds": (), "suppress_reasons": (),
                "episode_action_command_types": (), "timeline": (), "decision_evidence": (),
                "gate": (), "gate_passed": True, "gate_degraded": False,
                "fingerprints": {"expectation_fingerprint": "e", "loop_fingerprint": "l"},
                "refs": (), "graph": {"nodes": (), "edges": ()},
            },
        ),
    }
    desc = build_default_case_presentation(proj)
    for forbidden in ("case_risk_level", "case_decision", "case_timeline",
                      "risk_data", "decision_data", "timeline_data",
                      "audio_data", "audio_state"):
        assert forbidden not in desc, f"VM-11 违规：descriptor 含事实字段 {forbidden!r}"


def test_viewer_ac13_descriptor_rejects_fact_fields(tmp_path):
    """AC-13 双保险：load_case_descriptor 拒绝含事实型字段的 JSON（fail-closed）。"""
    bad = tmp_path / "bad_descriptor.json"
    bad.write_text(
        json.dumps({"case_id": "sw_t1", "case_risk_level": "HIGH"}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_case_descriptor(bad)


# ---------------------------------------------------------------------------
# AC-7（Provenance 一等视觉）
# ---------------------------------------------------------------------------


def test_viewer_ac7_provenance_first_class(tmp_path):
    """AC-7：每个案例视图显式呈现 provenance_kind 及文案（程序化场景·可复现 …）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    assert "prov-banner" in html, "缺 provenance banner（AC-7 一等视觉）"
    assert "程序化场景 · 可复现" in html, "缺 SIMULATED provenance 文案"
    assert "SIMULATED" in html


# ---------------------------------------------------------------------------
# AC-9（统一时间轴，无三套独立轴）
# ---------------------------------------------------------------------------


def test_viewer_ac9_unified_timeline_no_three_axes(tmp_path):
    """AC-9：统一 Evidence Timeline 唯一（每场景一个 timeline-list），不得出现独立的
    视频/音频/决策时间轴（audio 维度本 slice 不建模，AC-1c）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1", "sw_t2"))
    html = _render(d)
    # 每个场景恰好一个统一 evidence timeline（reuse renderer：ul id=timeline-list-{sid}）
    assert html.count('timeline-list-sw_t1') == 1
    assert html.count('timeline-list-sw_t2') == 1
    # 不得出现独立的视频/音频/决策时间轴
    for forbidden_axis in ('audio-timeline', 'video-timeline', 'decision-timeline'):
        assert forbidden_axis not in html, f"AC-9 违规：出现独立轴 {forbidden_axis}"


# ---------------------------------------------------------------------------
# AC-14（VM-10 双时间轴 + Case Time）
# ---------------------------------------------------------------------------


def test_viewer_ac14_dual_timeline_case_time(tmp_path):
    """AC-14/VM-10：Media Timeline 与 Evidence Timeline 是两个独立 DOM 结构（不混为同一
    数组/状态），经 Case Time 映射同步；脚本顺序铁律：global.__Replay = { 恰好 1 次且
    在 graph/media IIFE 之前。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    sid = "sw_t1"
    # 两个独立时间轴结构并存（reuse renderer：media-timeline-{sid} / timeline-list-{sid}）
    assert f'media-timeline-{sid}' in html
    assert f'timeline-list-{sid}' in html
    assert f'media-timeline-{sid}' != f'timeline-list-{sid}'
    # Media Timeline 经 Case Time 驱动 Evidence Timeline（JS 接线）
    assert "__Replay.get(" in html
    assert "rp.seek" in html, "Case Time 未驱动 evidence timeline 定位"
    # 脚本顺序铁律：__Replay 定义唯一且在 graph/media IIFE 之前
    i_def = html.find("global.__Replay = {")
    i_init = html.find(f'window.__Replay.init("{sid}")')
    assert i_def > 0 and i_init > 0
    assert html.count("global.__Replay = {") == 1, "replay 引擎被重复注入"
    # media IIFE 初始化（window.__MediaPlayer.init）必须在 replay init 之后。
    # 注：移除死代码 var _mt=... 后，media-timeline-{sid} 仅出现在首屏 body 的 DOM 元素
    # （早于脚本块），故改用 media init 调用作为 JS 块内标记（评审 R1-#1 删死代码后的对应
    # 测试标记迁移）。注意：init 调用为 window.__MediaPlayer.init("sid",cv,...)（参数在
    # sid 之后），故只匹配到 sid 为止的前缀。
    i_media = html.find(f'window.__MediaPlayer.init("{sid}"')
    assert i_media > 0, "未找到 MediaPlayer init 调用"
    assert i_def < i_init < i_media, "脚本顺序违规：media init 须在 replay init 之后"


# ---------------------------------------------------------------------------
# AC-15（VM-12 Case Video ≠ Analysis Video）
# ---------------------------------------------------------------------------


def test_viewer_ac15_case_video_not_analysis(tmp_path):
    """AC-15/VM-12：主体验使用 Case Video（叙事结构 Context→…→Outcome），不得把
    Analysis Video 当主视频产品化。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    assert 'id="fs-case-video-sw_t1"' in html, "缺 Case Video 主轴面板"
    assert "Case Video" in html
    # 叙事结构标签存在（证明是产品主视频，非分析片）
    assert "Context" in html and "Outcome" in html
    # 不得有"Analysis Video 作为主视频"的产品化入口
    assert 'id="fs-analysis-video' not in html
    # Case Viewer 不把 Analysis Video 重新产品化（历史资产不进主体验）
    assert "Analysis Video" not in html


# ---------------------------------------------------------------------------
# AC-16（首屏叙事层级）
# ---------------------------------------------------------------------------


def test_viewer_ac16_first_screen_order(tmp_path):
    """AC-16：首屏层级顺序为 Case Video → 当前风险 → 为什么 → 系统行动 → Evidence
    Timeline → 详细证据（折叠，且位于 Evidence Timeline 之后）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    sid = "sw_t1"
    anchors = [
        f'id="fs-case-video-{sid}"',
        f'id="fs-current-risk-{sid}"',
        f'id="fs-why-{sid}"',
        f'id="fs-action-{sid}"',
        f'id="fs-evidence-timeline-{sid}"',
        f'id="fs-details-{sid}"',
    ]
    positions = [html.find(a) for a in anchors]
    assert all(p > 0 for p in positions), f"首屏锚点缺失：{anchors}"
    # 严格升序（叙事层级不被打乱）
    assert positions == sorted(positions), "首屏层级顺序违规（AC-16）"
    # 详细证据折叠在 Evidence Timeline 之后（二级视图）
    assert positions[4] < positions[5]


# ---------------------------------------------------------------------------
# AC-17（Media Rendering：真实媒体播放器，非占位）
# ---------------------------------------------------------------------------


def test_viewer_ac17_media_rendering_not_placeholder(tmp_path):
    """AC-17：带媒体资产时，Case Video 主轴渲染真实媒体播放器（canvas + MediaPlayer 引擎 +
    每场景 init + manifest 数据岛），而非占位空框；绑定文案降级为脚注（方案 3）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    make_media_asset(d, "sw_t1", frame_count=30)
    html = _render_with_media(d)
    sid = "sw_t1"
    # 真实播放器（canvas + MediaPlayer 引擎 + 每场景 init）
    assert f'id="case-video-canvas-{sid}"' in html
    assert "__MediaPlayer = {" in html
    assert "window.__MediaPlayer.init(" in html
    # 媒体 manifest 数据岛已注入（含 frame_template）
    assert f'id="media-manifest-{sid}"' in html
    assert "frame_template" in html
    # 占位空框已移除（AC-17 价值：不再"没有产品价值"）
    assert "case-video-placeholder" not in html
    # 绑定文案降级为脚注（方案 3）
    assert "case-video-binding" in html
    assert "媒体源绑定" in html


def test_viewer_ac17_no_media_canvas_no_placeholder(tmp_path):
    """AC-17 降级：无媒体资产时仍渲染 canvas 播放器（留空），绝不回退到占位空框。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    assert 'id="case-video-canvas-sw_t1"' in html
    assert "case-video-placeholder" not in html
    assert 'id="media-manifest-sw_t1"' not in html


def test_viewer_ac17_media_base_url_normalizes_separators(tmp_path):
    """AC-17 回归：CLI 把 artifact 相对 URL 归一为正斜杠，浏览器才能解析帧 URL。

    历史 bug：Windows 上 ``os.path.relpath`` 产出 ``..\\artifacts``，``PurePosixPath`` 把
    ``\\`` 当字面量字符（posix 不视其为分隔符），导致 manifest 岛 frame_template 含 ``..\\``
    无法解析（媒体帧加载失败）。单测因传 ``media_base_url=\"\"`` 未覆盖此路径，故加本回归。
    """
    cli = _load_run_case_viewer()
    art = make_artifacts(tmp_path / "artifacts", scenario_ids=("sw_t1",))
    make_media_asset(art, "sw_t1", frame_count=30)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "case_viewer.html"
    rc = cli.main(["--artifacts", str(art), "--output", str(out_file)])
    assert rc == 0, "CLI 应退出 0"
    html = out_file.read_text(encoding="utf-8")
    # 提取 media-manifest-sw_t1 数据岛 JSON
    start = html.find('id="media-manifest-sw_t1"')
    assert start != -1, "manifest 数据岛缺失"
    jstart = html.find(">", start) + 1
    jend = html.find("</script>", jstart)
    island = json.loads(html[jstart:jend])
    tpl = island["frame_template"]
    # 铁律：相对 URL 必须全正斜杠，绝不混入原生分隔符（否则媒体帧加载失败）
    assert "\\" not in tpl, f"frame_template 含反斜杠，浏览器无法解析：{tpl!r}"
    assert "sw_t1/media/frames/" in tpl, tpl
    assert tpl.endswith("{idx:06d}.png"), tpl


# ---------------------------------------------------------------------------
# AC-2（复用 renderer 语义一致）
# ---------------------------------------------------------------------------


def test_viewer_ac2_reuse_renderer_blocks(tmp_path):
    """AC-2：Case Viewer 复用 renderer 的展示构建块（graph / timeline / gate 容器 id
    与 D1 Explorer 同源），保证同一 artifact 上语义一致。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    # 复用 renderer 的统一 evidence timeline 容器
    assert 'timeline-list-sw_t1' in html
    # 复用 renderer 的 Evidence Graph 容器（graph-{sid} 由 _render_evidence_graph 产出）
    assert 'graph-sw_t1' in html
    # 复用 renderer 的 trace 轨道（为什么报警 重放）
    assert 'trace-list-sw_t1' in html
    # 复用 renderer 的 Fingerprint / Gate 视图
    assert "expectation_fingerprint" in html and "loop_fingerprint" in html
    # 派生卡片来自 projection（非第二事实模型）
    assert "当前风险" in html and "系统行动" in html


# ---------------------------------------------------------------------------
# 确定性（D8 纪律，复用到 Case Viewer）
# ---------------------------------------------------------------------------


def test_viewer_deterministic(tmp_path):
    """同 projection 两次渲染逐字节一致（D8 确定性）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html1 = _render(d)
    html2 = _render(d)
    assert html1 == html2


def test_viewer_fail_closed_empty_scenarios():
    """render_case_viewer 在 projection 无场景时 fail-closed（ValueError）。"""
    with pytest.raises(ValueError):
        render_case_viewer({"meta": {"scenario_count": 0}, "scenarios": ()})


# ---------------------------------------------------------------------------
# VM-3（viewer 不 import silver_demo / 生产 runtime）
# ---------------------------------------------------------------------------


def test_viewer_vm3_no_production_imports():
    """VM-3：viewer/ 不得 import silver_demo / 生产 runtime（AST 死胡同叶子纪律）。"""
    forbidden = (
        "silver_demo",
        "home_perception.runtime",
        "home_perception.evaluation",
        "home_perception.integration",
        "home_perception.memory",
    )
    violations: list[str] = []
    for py in _VIEWER_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for f in forbidden:
            # 仅匹配 import / from 语句行的引入；允许注释提及
            for m in re.finditer(r"^\s*(import|from)\s+(\S+)", text, re.MULTILINE):
                mod = m.group(2)
                if mod == f or mod.startswith(f + "."):
                    violations.append(f"{py.name}: {m.group(0)}")
    assert not violations, f"VM-3 违规：viewer 引入了生产符号 {violations}"


# ---------------------------------------------------------------------------
# 运行时行为级验证（Node vm + mock echarts/document）
# ---------------------------------------------------------------------------

_NODE_EXE_CAND = r"C:/Users/lenovo/.workbuddy/binaries/node/versions/22.22.2/node.exe"


def _node_exe() -> str | None:
    if os.path.exists(_NODE_EXE_CAND):
        return _NODE_EXE_CAND
    return shutil.which("node")


# 复用 D2.2/D2.3 范式：真实执行 replay.js + media.js + graph IIFE，断言联动与 Case Time。
# 关键：不执行 echarts.min.js（用 mock echarts），避免 UMD 覆盖 mock 导致断言失真。
# Slice A.1：额外 mock Image/canvas，验证 MediaPlayer（画布帧绘制 + Evidence 双向同步）。
_VIEWER_RUNTIME_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf8');
const SID = process.argv[3];

const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
// 仅选取 replay.js / media.js 引擎定义 / init 调用 / graph+media 脚本块（绝不执行 echarts 块）
const replayJs = scripts.find(s => s.includes('global.__Replay = {'));
const mediaJs = scripts.find(s => s.includes('__MediaPlayer = {'));
// replay.js 引擎块注释里有 `window.__Replay.init(`（描述性文本）→ 排除引擎块。
const replayInits = scripts.find(s => s.includes('window.__Replay.init(') && !s.includes('global.__Replay = {'));
const graphScript = scripts.find(s => s.includes('media-timeline-') || s.includes('highlightCategory') || s.includes('window.__MediaPlayer.init('));
if (!replayJs) { console.error('FAIL: 未找到 replay.js'); process.exit(1); }
if (!mediaJs) { console.error('FAIL: 未找到 media.js'); process.exit(1); }
if (!replayInits) { console.error('FAIL: 未找到 replay_inits'); process.exit(1); }
if (!graphScript) { console.error('FAIL: 未找到 graph/media 脚本块'); process.exit(1); }

function islandRaw(pre) {
  const q = html.indexOf(pre);
  const open = html.indexOf('>', q) + 1;
  const close = html.indexOf('</script>', open);
  return html.slice(open, close);
}
const tlRaw = islandRaw('id="replay-data-' + SID + '"');
let traceRaw = '';
const tp = html.indexOf('id="replay-trace-data-' + SID + '"');
if (tp !== -1) traceRaw = islandRaw('id="replay-trace-data-' + SID + '"');

const hasMedia = html.indexOf('id="media-manifest-' + SID + '"') !== -1;
let mediaRaw = '';
if (hasMedia) mediaRaw = islandRaw('id="media-manifest-' + SID + '"');

const dispatchCalls = [];
const clickHandlers = [];
function makeEl(id) {
  return {
    _id: id,
    textContent: id.indexOf('replay-data-') !== -1 ? tlRaw
               : (id.indexOf('replay-trace-data-') !== -1 ? traceRaw
                  : (id.indexOf('media-manifest-') !== -1 ? mediaRaw : '')),
    style: {},
    classList: { toggle(){}, add(){}, remove(){}, contains(){return false;} },
    getAttribute(a){ return a === 'data-idx' ? '0' : null; },
    querySelectorAll(){ return []; },
    querySelector(){ return null; },
    scrollIntoView(){},
    set onclick(f){ this._oc = f; }, get onclick(){ return this._oc; },
    set onchange(f){},
  };
}
// canvas + 2D 上下文（mock）：drawImage 计数用于 AC-18 验证"帧真实绘制"
const drawCalls = { count: 0 };
const canvasCtx = {
  drawImage(){ drawCalls.count++; },
  fillRect(){}, clearRect(){}, beginPath(){}, fill(){},
};
const canvasEl = { getContext(){ return canvasCtx; }, style:{}, width:640, height:360 };
// media 控制条 stub
const mediaPlay = {
  textContent: '▶',
  set onclick(f){ this._oc = f; }, get onclick(){ return this._oc; },
};
const mediaProgress = { style:{}, textContent:'', parentElement:null };
const mediaLabel = { textContent:'' };
// Evidence Timeline 列表容器 + 子项（供 replay.js bindTimeline 绑定 click→seek）
const nodeCount = JSON.parse(tlRaw).length;
const tlItems = [];
for (let i=0;i<nodeCount;i++){
  tlItems.push({
    _idx:i, style:{},
    classList:{ toggle(){}, add(){}, remove(){}, contains(){return false;} },
    getAttribute(a){ return a==='data-idx'? String(i): null; },
    set onclick(f){ this._oc = f; }, get onclick(){ return this._oc; },
    querySelectorAll(){ return []; }, querySelector(){ return null; },
  });
}
const timelineList = {
  querySelectorAll(sel){ return sel==='.tl-item'? tlItems : []; },
  querySelector(){ return null; },
};
const els = {};
function getEl(id) {
  if (id === 'case-video-canvas-' + SID) return canvasEl;
  if (id === 'media-play-' + SID) return mediaPlay;
  if (id === 'media-progress-' + SID) return mediaProgress;
  if (id === 'media-time-label-' + SID) return mediaLabel;
  if (id === 'timeline-list-' + SID) return timelineList;
  if (!(id in els)) els[id] = makeEl(id);
  return els[id];
}
const mockDoc = { getElementById: (id) => getEl(id) };
const echarts = {
  init() {
    return {
      setOption(){},
      on(ev, cb){ if (ev === 'click') clickHandlers.push(cb); },
      dispatchAction(a){ dispatchCalls.push(a); },
      resize(){},
    };
  },
};
// Image mock：设 src 立即触发 onload（同步，确定性），供 MediaPlayer 绘帧
function MockImage(){ this.onload=null; this.onerror=null; this._src=''; this.width=0; this.height=0; }
Object.defineProperty(MockImage.prototype, 'src', {
  set(v){ this._src=v; if (this.onload) this.onload(); },
  get(){ return this._src; },
});
// setInterval 立即同步跑足够多次（驱动 media 进度到尾，确定性验证 Case Time 同步）。
// 评审 R2-#11 后 play 步长改为 fps 同步（默认 10fps → 步长 0.1s），故需足够多 tick 才能
// 覆盖默认 media_duration_s=60s（60s/0.1s=600 步）；这里给宽裕上限，确保任意合理 duration
// 都能驱动到尾（player 自带 time>=duration 夹取，tick 富余不会越界）。
global.setInterval = (cb) => { for (let i=0;i<6000;i++) cb(); return 1; };
global.clearInterval = () => {};
const ctx = { console, setTimeout, clearTimeout, setInterval: global.setInterval,
              clearInterval: global.clearInterval, Math, JSON, isNaN, parseInt, Infinity,
              document: mockDoc, echarts, Image: MockImage };
ctx.window = ctx;
vm.createContext(ctx);
function run(label, code) {
  try { vm.runInContext(code, ctx); }
  catch (e) { console.error('FAIL ' + label + ' 抛错: ' + e.message); process.exit(1); }
}
run('replayJs', replayJs);
run('mediaJs', mediaJs);
run('replayInits', replayInits);
run('graphScript', graphScript);

// 断言 1：graph 节点 click→seek 联动已注册（D2.2 复用未被破坏）
if (clickHandlers.length < 1) { console.error('FAIL: chart.on(click) 未注册'); process.exit(1); }
// 断言 2：初始态高亮已触发 dispatchAction highlight
const initHi = dispatchCalls.filter(a => a.type === 'highlight' && a.dataIndex != null);
if (initHi.length < 1) { console.error('FAIL: 初始态未触发高亮'); process.exit(1); }
// 断言 3：双轨道独立（timeline 与 trace 互不干扰）
const rp = ctx.window.__Replay.get(SID);
const rpTrace = ctx.window.__Replay.get(SID, 'trace');
if (!rp) { console.error('FAIL: timeline 实例缺失'); process.exit(1); }
if (rpTrace && rp === rpTrace) { console.error('FAIL: 双轨道不可区分'); process.exit(1); }
// 断言 4：Case Time 同步（AC-14）—— 媒体播放驱动 evidence timeline 到末位
const lastIdx = rp.nodes.length - 1;
if (typeof mediaPlay.onclick !== 'function') { console.error('FAIL: media play 未绑定 onclick'); process.exit(1); }
mediaPlay.onclick();
if (rp.index !== lastIdx) {
  console.error('FAIL: Case Time 未驱动 evidence timeline（index=' + rp.index + ' 期望 ' + lastIdx + '）');
  process.exit(1);
}
// 断言 5（AC-18）：有媒体 → 画布真实绘帧（drawImage 调用 ≥1）
if (hasMedia && drawCalls.count < 1) {
  console.error('FAIL: 媒体未真实绘制到画布（drawImage 未调用）');
  process.exit(1);
}
// 断言 6（AC-19）：Evidence 节点点击 → 定位对应媒体帧（双向导航）
if (hasMedia) {
  const k = Math.min(3, lastIdx);
  const before = drawCalls.count;
  const item = tlItems[k];
  if (typeof item.onclick !== 'function') {
    console.error('FAIL: evidence 节点未绑定 click（双向导航缺失）'); process.exit(1);
  }
  item.onclick();  // replay seek(k) + __MediaSync.onEvidenceSeek(k) → 画对应帧
  const player = ctx.window.__MediaPlayer.get(SID);
  if (!player) { console.error('FAIL: MediaPlayer 实例缺失'); process.exit(1); }
  if (drawCalls.count <= before) {
    console.error('FAIL: Evidence 点击未触发媒体帧绘制（AC-19 断裂）'); process.exit(1);
  }
  const n = rp.nodes.length;
  const expectedFrame = (n > 1) ? Math.round(k / (n - 1) * (player.frameCount - 1)) : 0;
  if (player.currentFrame !== expectedFrame) {
    console.error('FAIL: Evidence→Media 帧映射错误（got=' + player.currentFrame + ' exp=' + expectedFrame + '）');
    process.exit(1);
  }
  if (rp.index !== k) {
    console.error('FAIL: Evidence 点击未定位 timeline（index=' + rp.index + ' 期望 ' + k + '）');
    process.exit(1);
  }
}
console.log('OK sid=' + SID + ' clicks=' + clickHandlers.length + ' initHi=' + initHi.length
  + ' caseTimeIdx=' + rp.index + ' media=' + hasMedia + ' draws=' + drawCalls.count);
"""


def test_viewer_runtime_linkage_and_case_time(tmp_path):
    """运行时行为级回归（无媒体）：graph 联动高亮 + 双轨道 + Case Time 同步（Node 真实执行，
    非字符串断言）。CI 无 node 时跳过。"""
    node = _node_exe()
    if not node:
        pytest.skip("node 不可用，跳过 Case Viewer 运行时行为级测试")

    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    html_file = tmp_path / "case_viewer.html"
    html_file.write_text(html, encoding="utf-8")
    harness = tmp_path / "viewer_runtime_harness.js"
    harness.write_text(_VIEWER_RUNTIME_HARNESS, encoding="utf-8")
    res = subprocess.run(
        [node, str(harness), str(html_file), "sw_t1"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert res.returncode == 0, (
        f"Case Viewer 运行时行为测试失败:\n{res.stdout}\n{res.stderr}"
    )


def test_viewer_runtime_media_sync(tmp_path):
    """运行时行为级回归（带媒体，AC-17/18/19）：MediaPlayer 真实绘帧 + Case Time 同步 +
    Evidence→Media 双向导航（Node 真实执行，非字符串断言）。CI 无 node 时跳过。"""
    node = _node_exe()
    if not node:
        pytest.skip("node 不可用，跳过 Case Viewer 媒体同步运行时测试")

    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    make_media_asset(d, "sw_t1", frame_count=30, fps=10.0)
    html = _render_with_media(d)
    # 校验：媒体文件确实存在且为合法 PNG（hermetic 夹具自洽）
    assert (d / "sw_t1" / "media" / "manifest.json").exists()
    assert (d / "sw_t1" / "media" / "frames" / "000000.png").exists()
    html_file = tmp_path / "case_viewer.html"
    html_file.write_text(html, encoding="utf-8")
    harness = tmp_path / "viewer_runtime_harness.js"
    harness.write_text(_VIEWER_RUNTIME_HARNESS, encoding="utf-8")
    res = subprocess.run(
        [node, str(harness), str(html_file), "sw_t1"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert res.returncode == 0, (
        f"Case Viewer 媒体同步运行时测试失败:\n{res.stdout}\n{res.stderr}"
    )


# ---------------------------------------------------------------------------
# media.js 直接单元级测试（评审 R2-#11 / #367）：_safeUrl 白名单 + 帧绘制/同步降级
# 真实 Node vm 执行 media.js（mock Image/canvas/document），非字符串断言。
# ---------------------------------------------------------------------------


_MEDIA_JS_UNIT_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const mediaPath = process.argv[2];
const code = fs.readFileSync(mediaPath, 'utf8');

// 记录最后一次 img.src 赋值，用于断言伪协议帧 URL 是否被阻断加载。
let lastSrc = '__unset__';
function MockImage() {
  this.onload = null; this.onerror = null; this._src = '';
  Object.defineProperty(this, 'src', {
    set(v) { this._src = v; lastSrc = v; if (this.onerror) this.onerror(); },
    get() { return this._src; }
  });
}
const drawCalls = { count: 0 };
const ctx2d = { drawImage() { drawCalls.count++; }, fillRect(){}, clearRect(){}, beginPath(){}, fill(){} };
const canvas = { getContext() { return ctx2d; }, style:{}, width:640, height:360 };
const mockDoc = { getElementById() { return null; } };

const ctx = {
  console, setTimeout, clearTimeout, Math, JSON, isNaN, parseInt, Infinity,
  // setInterval 仅捕获回调、不自动跑，便于断言 play 重置逻辑（不掺入 tick 副作用）。
  setInterval: function (cb) { ctx.__playCb = cb; return 1; },
  clearInterval: function () {},
  document: mockDoc, Image: MockImage,
};
ctx.window = ctx;
vm.createContext(ctx);
try { vm.runInContext(code, ctx); } catch (e) { console.error('FAIL load: ' + e.message); process.exit(1); }

const MP = ctx.window.__MediaPlayer;
function check(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exit(1); } }

// 1) 帧 URL 协议白名单（评审 R2-#3 / #11）：拒 javascript:/data:/file:，放行 http(s)/相对路径。
check(MP._safeUrl('javascript:alert(1)') === '', 'javascript: 应被拒绝');
check(MP._safeUrl('JAVASCRIPT:alert(1)') === '', '大小写 javascript: 应被拒绝');
check(MP._safeUrl('data:text/html,foo') === '', 'data: 应被拒绝');
check(MP._safeUrl('file:///etc/passwd') === '', 'file: 应被拒绝');
check(MP._safeUrl('https://h/x.png') === 'https://h/x.png', 'https 应放行');
check(MP._safeUrl('http://h/x.png') === 'http://h/x.png', 'http 应放行');
check(MP._safeUrl('/abs/x.png') === '/abs/x.png', '绝对路径应放行');
check(MP._safeUrl('./rel.png') === './rel.png', '相对路径 ./ 应放行');
check(MP._safeUrl('../rel.png') === '../rel.png', '相对路径 ../ 应放行');
check(MP._safeUrl('') === '', '空串应返回空');

// 构造播放器实例（init 会调 start → _bindControls，getElementById 返回 null 安全）。
const manifest = { source_kind:'SyntheticFrameSource', frame_count:30, fps:10,
                   duration_sec:3, frame_template:'frames/{idx:06d}.png', video_url:'' };
const p = MP.init('u1', canvas, manifest, { duration:3, fps:0 });
check(p && p.sid === 'u1', 'init 应返回实例');

// 2) seekByTime 边界夹取（t<0 → 0；t>duration → duration）。
p.seekByTime(-5, false); check(p.time === 0, '负时间应夹到 0');
p.seekByTime(999, false); check(p.time === 3, '超界时间应夹到 duration');

// 3) 伪协议帧 URL 经 _safeUrl 阻断：不设置 img.src、不绘帧（fail-closed）。
p.currentFrame = -1; drawCalls.count = 0; lastSrc = '__unset__';
p.frameTemplate = 'javascript:alert(1)';
p._drawFrame(0);
check(p.currentFrame === 0, '_drawFrame 幂等标记仍更新');
check(lastSrc === '__unset__', '伪协议帧 URL 不应加载（_safeUrl 阻断）');
check(drawCalls.count === 0, '伪协议帧 URL 不应绘帧');

// 4) seekByEvidence 退化（无 replay 实例）→ 不崩、不绘帧。
p.frameTemplate = 'frames/{idx:06d}.png';
p.currentFrame = -1; drawCalls.count = 0;
p.seekByEvidence(5);
check(p.currentFrame === -1, '无 replay 时 seekByEvidence 应退化（不绘帧）');

// 5) play 重置（time>=duration → 0）且置 playing。
p.time = 3; p.playing = false;
p.play();
check(p.time === 0, 'play 应重置 time 到 0（time>=duration）');
check(p.playing === true, 'play 应置 playing=true');

// 6) Image onerror 路径不崩（真实帧 URL 但加载失败 → 画布留空，非异常）。
p.frameTemplate = 'frames/{idx:06d}.png'; p.currentFrame = -1;
try { p._drawFrame(1); } catch (e) { console.error('FAIL: onerror 路径崩溃 ' + e.message); process.exit(1); }

// 7) _renderProgress 在 duration=0 时不崩（进度 0% + 标签仍渲染）。
p._progress = { style:{} }; p._label = { textContent:'' };
p.duration = 0; p.time = 5;
p._renderProgress();
check(p._progress.style.width === '0%', 'duration=0 时进度应为 0%');
check(p._label.textContent === '5.0s / 0.0s', 'duration=0 标签仍渲染');

console.log('OK media.js unit: safeUrl/seek/play/renderProgress 全通过');
"""


def _media_js_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "home_perception" / "visualizer" / "assets" / "media.js"


def test_media_js_unit(tmp_path):
    """media.js 直接单元级回归：_safeUrl 白名单 + 帧绘制/同步/play 重置/进度降级（Node 真实执行）。"""
    node = _node_exe()
    if not node:
        pytest.skip("node 不可用，跳过 media.js 单元级测试")
    harness = tmp_path / "media_js_unit_harness.js"
    harness.write_text(_MEDIA_JS_UNIT_HARNESS, encoding="utf-8")
    res = subprocess.run(
        [node, str(harness), str(_media_js_path())],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert res.returncode == 0, (
        f"media.js 单元级测试失败:\n{res.stdout}\n{res.stderr}"
    )


# ---------------------------------------------------------------------------
# 评审 R2-#5：load_case_descriptor media_binding shape + build_default 越界（fail-closed）
# ---------------------------------------------------------------------------


def test_load_case_descriptor_rejects_bad_source_kind(tmp_path):
    """media_binding.source_kind 非法枚举 → ValueError（下游 mb["source_kind"] 会 KeyError）。"""
    d = tmp_path / "bad.json"
    d.write_text(
        json.dumps({"media_binding": {"source_kind": "FooSource", "ref": "r"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_case_descriptor(d)


def test_load_case_descriptor_rejects_nonstr_ref(tmp_path):
    """media_binding.ref 非字符串 → ValueError（shape 校验 fail-closed）。"""
    d = tmp_path / "bad.json"
    d.write_text(
        json.dumps({"media_binding": {"source_kind": "SyntheticFrameSource", "ref": 123}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_case_descriptor(d)


def test_load_case_descriptor_rejects_malformed_binding_shape(tmp_path):
    """畸形 media_binding（缺 source_kind/ref，如 {"foo":"bar"}）→ ValueError（评审 R2-#5）。"""
    d = tmp_path / "bad.json"
    d.write_text(json.dumps({"media_binding": {"foo": "bar"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_case_descriptor(d)


def test_load_case_descriptor_valid_binding_ok(tmp_path):
    """合法 media_binding（含合法枚举 + 字符串 ref）通过校验。"""
    d = tmp_path / "ok.json"
    d.write_text(
        json.dumps(
            {"media_binding": {"source_kind": "SyntheticFrameSource", "ref": "sw_t1#media"}}
        ),
        encoding="utf-8",
    )
    desc = load_case_descriptor(d)
    assert desc["media_binding"]["source_kind"] == "SyntheticFrameSource"
    assert desc["media_binding"]["ref"] == "sw_t1#media"


def test_build_default_case_presentation_rejects_out_of_range_index():
    """scenario_index 越界 → ValueError（评审 R2-#5：原静默回退 scenario_index=0 改为 fail-closed）。"""
    proj = {
        "meta": {"scenario_count": 1},
        "scenarios": ({"scenario_id": "sw_t1"},),
    }
    with pytest.raises(ValueError):
        build_default_case_presentation(proj, scenario_index=5)
    # 边界值合法（不抛）。
    build_default_case_presentation(proj, scenario_index=0)


# ---------------------------------------------------------------------------
# 评审 R2-#1 / #2 / #3：render.py 分支覆盖（>128 / 多 provenance / video vs canvas / 未知 panel）
# ---------------------------------------------------------------------------


def test_render_rejects_too_many_scenarios():
    """scenarios > 128 → ValueError（fail-closed，评审 R3-#1）。"""
    scenarios = tuple({"scenario_id": f"s{i}"} for i in range(129))
    proj = {"meta": {"scenario_count": 129}, "scenarios": scenarios}
    with pytest.raises(ValueError):
        render_case_viewer(proj)


def test_render_multi_provenance_mixed_banner():
    """多 provenance_kind 场景 → MIXED 徽章 + 合并文案（评审 R2-#2 多 provenance）。"""
    html = _render_provenance_banner(
        {"timeline": ({"provenance_kind": "SIMULATED"}, {"provenance_kind": "REAL_SENSOR"})}
    )
    assert "MIXED" in html, "多 provenance 应呈现 MIXED 徽章"
    assert "程序化场景" in html, "SIMULATED 文案缺失"
    assert "真实传感器" in html, "REAL_SENSOR 文案缺失"


def test_render_case_video_uses_video_element_for_artifact_video():
    """ArtifactVideoSource + video_url → 原生 <video>，绝不用 canvas 占位（评审 R2-#3 视频 vs canvas）。"""
    scenario = {"scenario_id": "sw_t1"}
    descriptor = {"media_binding": {"source_kind": "SyntheticFrameSource", "ref": "r"}}
    manifest = {
        "source_kind": "ArtifactVideoSource",
        "video_url": "https://example.com/case.mp4",
        "frame_template": "x",
    }
    html = _render_case_video(scenario, descriptor, manifest, "")
    assert "<video" in html, "ArtifactVideoSource 应渲染原生 <video>"
    assert "case-video-canvas" not in html, "视频源不应回退 canvas"
    assert "https://example.com/case.mp4" in html, "video_url 应出现在 src"


def test_render_case_video_uses_canvas_when_no_media():
    """无媒体（manifest=None）→ canvas 播放器 + 标注"无媒体绑定"（评审 R2-#3 / R1-#4）。"""
    scenario = {"scenario_id": "sw_t1"}
    descriptor = {"media_binding": {"source_kind": "SyntheticFrameSource", "ref": "r"}}
    html = _render_case_video(scenario, descriptor, None, "")
    assert "case-video-canvas" in html, "无媒体应渲染 canvas 播放器"
    assert "<video" not in html, "无媒体不应渲染 <video>"
    assert "无媒体绑定" in html, "无媒体应标注'无媒体绑定'（不显示孤儿 ref）"


def test_safe_media_src_allows_http_and_relative():
    """_safe_media_src 协议黑名单：http(s) 绝对 URL 与相对路径一律放行（评审 R2-#3）。"""
    assert _safe_media_src("") == ""
    assert _safe_media_src("https://example.com/case.mp4") == "https://example.com/case.mp4"
    assert _safe_media_src("http://example.com/case.mp4") == "http://example.com/case.mp4"
    # 相对路径（含裸相对路径，无 scheme）放行——浏览器相对页面解析（与 media.js 一致）。
    assert _safe_media_src("/media/x.png") == "/media/x.png"
    assert _safe_media_src("./media/x.png") == "./media/x.png"
    assert _safe_media_src("../artifacts/sw_m1/media/frames/000000.png") == (
        "../artifacts/sw_m1/media/frames/000000.png"
    )
    assert _safe_media_src("frames/000000.png") == "frames/000000.png"


def test_safe_media_src_rejects_pseudo_schemes():
    """_safe_media_src 拒伪协议（javascript:/data:/file: 等），fail-closed 返回空（评审 R2-#3）。"""
    assert _safe_media_src("javascript:alert(1)") == ""
    assert _safe_media_src("data:image/png;base64,AAAA") == ""
    assert _safe_media_src("file:///etc/passwd") == ""
    assert _safe_media_src("ftp://host/x") == ""
    # 未知 scheme 同样拒绝（不能误当相对路径放行）。
    assert _safe_media_src("weird:thing") == ""


def test_render_unknown_panel_ignored_gracefully(tmp_path):
    """编排含未知面板名 → 静默忽略、不崩，其余面板正常渲染（评审 R3-#3 前向兼容）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    proj = load_case_artifact(d)
    desc = build_default_case_presentation(proj)
    desc["first_screen_layout"]["panels"] = ("case_video", "ghost_panel", "current_risk")
    html = render_case_viewer(proj, desc, media_base_dir=None)
    # 已知面板仍正常渲染
    assert "fs-case-video-sw_t1" in html
    assert "fs-current-risk-sw_t1" in html
    # 未知面板被忽略（不产出任何对应 section）
    assert "ghost_panel" not in html


# ---------------------------------------------------------------------------
# 评审 R2-#10 / D9：run_case_viewer CLI 退出码路径（fail-closed）
# ---------------------------------------------------------------------------


def test_cli_missing_artifacts_exit_2(tmp_path):
    """--artifacts 指向不存在目录 → 退出码 2（参数/目录错误）。"""
    cli = _load_run_case_viewer()
    rc = cli.main(["--artifacts", str(tmp_path / "nope"), "--output", str(tmp_path / "o.html")])
    assert rc == 2, "缺失 artifact 目录应退出 2"


def test_cli_descriptor_with_fact_exit_1(tmp_path):
    """--descriptor 含事实型字段 → 退出码 1（投影/展示编排契约违规，fail-closed）。"""
    cli = _load_run_case_viewer()
    art = make_artifacts(tmp_path / "artifacts", scenario_ids=("sw_t1",))
    bad = tmp_path / "bad_descriptor.json"
    bad.write_text(
        json.dumps({"case_id": "sw_t1", "case_risk_level": "HIGH"}), encoding="utf-8"
    )
    out = tmp_path / "out.html"
    rc = cli.main(["--artifacts", str(art), "--descriptor", str(bad), "--output", str(out)])
    assert rc == 1, "含事实字段的 descriptor 应退出 1"


def test_cli_render_valueerror_exit_1(tmp_path, monkeypatch):
    """render_case_viewer 抛 ValueError（scenarios>128）→ 退出码 1（第二道防御层）。

    main() 内部以 ``from ... import render_case_viewer`` 局部导入，故改 patch 真正的
    viewer 包属性（调用时取值），而非 cli 模块同名属性（会被局部导入遮蔽）。
    """
    import home_perception.visualizer.viewer as _viewer_mod

    cli = _load_run_case_viewer()
    art = make_artifacts(tmp_path / "artifacts", scenario_ids=("sw_t1",))

    def _raise_render(*a, **k):
        raise ValueError("EvidenceProjection.scenarios 数量超上限（129>128，fail-closed）")

    monkeypatch.setattr(_viewer_mod, "render_case_viewer", _raise_render)
    out = tmp_path / "out.html"
    rc = cli.main(["--artifacts", str(art), "--output", str(out)])
    assert rc == 1, "render ValueError 应退出 1"


def test_cli_cross_drive_fail_closed_exit_2(tmp_path, monkeypatch):
    """跨盘符 relpath 不可算 → fail-closed 退出码 2（绝不退化为绝对路径放行，评审 R2-#10）。"""
    cli = _load_run_case_viewer()
    art = make_artifacts(tmp_path / "artifacts", scenario_ids=("sw_t1",))

    def _raise(*a, **k):
        raise ValueError("cross-drive relpath")

    monkeypatch.setattr(cli.os.path, "relpath", _raise)
    out = tmp_path / "out.html"
    rc = cli.main(["--artifacts", str(art), "--output", str(out)])
    assert rc == 2, "跨盘符 relpath 失败应 fail-closed 退出 2"


# ---------------------------------------------------------------------------
# 评审 R3-#2：make_media_asset 自身 sanity（产出可被 Media Source Adapter 只读解析）
# ---------------------------------------------------------------------------


def test_make_media_asset_sanity_resolvable(tmp_path):
    """make_media_asset 产出的媒体资产树可被 resolve_media_source 解析为合法 MediaManifest。"""
    base = tmp_path / "media_base"
    media_dir = make_media_asset(base, "sw_t1", frame_count=30, fps=10.0)
    assert (media_dir / "manifest.json").exists()
    assert (media_dir / "frames" / "000000.png").exists()
    m = resolve_media_source(base, "sw_t1", "SyntheticFrameSource")
    assert m is not None, "resolve_media_source 应解析出 manifest"
    assert m["source_kind"] == "SyntheticFrameSource"
    assert m["frame_count"] == 30
    assert m["fps"] == 10.0
    assert m["frame_template"].endswith("{idx:06d}.png")
    # 缺媒体场景应降级为 None（不崩）
    assert resolve_media_source(base, "sw_absent", "SyntheticFrameSource") is None
