"""D0 · DOM Product Contract —— 产品表面契约测试套件（Gate D · 故意打红）。

SSOT：``docs/reports/DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md`` v3.2 §3.2。

定位（收尾路径步骤 D）：本套件描述产品表面的**应然契约**，在当前未修复的产品上运行
**预期产生 FAIL**；FAIL 明细即 E 阶段（仅修 render.py / live_stream.js 产品表面，
测试零改动）的修复清单。硬门禁条款（Owner）：不得通过缩小查询范围、改变断言目标、
弱化黑名单或跳过实际可见节点获得 PASS。

两维度查询分离（§3.2.5 防假绿核心）：
- 维度① ``document.body.innerText``：用户可见文本主断言面（工程语义黑名单统一扫描）；
- 维度② ``querySelectorAll("[data-debug-only]")``：逐节点隔离验证（五判据 OR 合规；
  aria-hidden 仅影响辅助技术，不构成视觉不可见；canvas 像素不在任何文本查询范围）。

轮询等待纪律（§3.2.5 实现约束）：断言前轮询等待目标 DOM 条件出现（带超时上限），
禁止裸 sleep 作为唯一同步手段——这是"盲等假绿"教训的直接对策。

运行前提（外部 fixture，模块级探测 skip）：
    python scripts/run_demo.py --live --scenario config/demo/scenarios/product_story_risk.yaml
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
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
SID = "product_story_risk"

CHROMIUM_PATH = (
    r"C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe"
)

# ---------------------------------------------------------------------------
# 轮询等待预算（§3.2.5：条件轮询 + 超时上限）
# ---------------------------------------------------------------------------
POLL_INTERVAL_MS = 800
FIRST_FRAME_TIMEOUT_MS = 30_000
PERCEPTION_DATA_TIMEOUT_MS = 60_000
BEHAVIOR_DATA_TIMEOUT_MS = 95_000
AUDIO_EVENT_TIMEOUT_MS = 50_000
AUDIO_STALE_TIMEOUT_MS = 60_000
AUDIO_QUIET_WINDOW_MS = 20_000

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

# 维度①全量规则表（正则①+ 字面量②③逐条独立断言用）。
_ALL_DIM1_RULES: tuple[tuple[str, str, str], ...] = (
    *(("field", rid, pat) for rid, pat in ENGINEERING_FIELD_PATTERNS),
    *(("component", name, re.escape(name)) for name in INTERNAL_COMPONENT_NAMES),
    *(("concept", name, re.escape(name)) for name in INTERNAL_CONCEPT_TERMS),
)

# V-05 容器级模式（§3.2.1 V-05 自带判据：不含 ``conf <数值>`` / ``bbox [<数值>``）。
PERCEPTION_FIELD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("perception_conf_value", r"conf\s+\d"),
    ("perception_bbox_bracket", r"bbox\s*\["),
)
# AU-02：裸 score 数值（孤立两位小数，排除时间戳 00:15 / 一位小数 12.3s）。
_BARE_SCORE_RE = r"(?<![\d:.])\d\.\d{2}(?!\d)"
# AU-05b：裸音频枚举 kind（如 audio_telephone_persistent）。
_RAW_AUDIO_ENUM_RE = r"\baudio_[a-z_]+\b"
# AU-05：五类 AudioKind→中文映射回归保护（live_stream.js _AUDIO_KIND_ZH 冻结值）。
_AUDIO_KIND_ZH_LABELS: tuple[str, ...] = (
    "音高升高",
    "语速加快",
    "声学异常活动",
    "持续电话声",
    "其他声学异常",
)
# AU-09：distress_cry 已知误识别声明（Owner 裁决 2026-08-24；与 live_stream.js 同文冻结）。
_DISTRESS_CAUTION_NOTE = "当前版本该类别存在正常电话语音误识别，暂不作为风险升级依据"

# ---------------------------------------------------------------------------
# AU-03 显示上限契约：N 是产品配置事实（SSOT：默认 10/20 由实现自定，不写入契约）。
# 此处数值仅为当前实现的配置快照；UI 调整 N 时只需更新本组常量，断言语义
# （visible_rows ≤ configured_display_limit）零改动。
# ---------------------------------------------------------------------------
PS_VISIBLE_LIMIT = 10
PS_HISTORY_RENDER_LIMIT = 10
BEHAVIOR_TIMELINE_LIMIT = 20
TIMELINE_RUNTIME_LIMIT = 30
CASE_TIME_MARK_LIMIT = 120  # AU-07b：Case Time 刻度宽松上界（轨道宽度决定，防异常增殖）
BODY_NODE_HARD_BOUND = 8_000
BODY_NODES_JITTER_TOLERANCE = 8


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
    "config/demo/scenarios/product_story_risk.yaml",
)


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
    """§3.2.5 维度② 五判据（满足任一即视觉不可见）。

    注意：``aria-hidden`` 仅影响辅助技术，不构成视觉不可见，故不参与本判定。
    """
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


# ---------------------------------------------------------------------------
# 页内 JS 模板（统一 __SID__ 占位替换；谓词供轮询，快照供断言）
# ---------------------------------------------------------------------------
_FIRST_FRAME_JS = """
(() => {
  const img = document.getElementById('video-img-__SID__');
  if (img && img.naturalWidth > 0) return true;
  const t = document.getElementById('ov-time-__SID__');
  return !!(t && t.textContent && t.textContent !== '00:00');
})()
""".replace("__SID__", SID)

_PERCEPTION_ROW_JS = f"!!document.querySelector('#live-perception-{SID} li')"
_BEHAVIOR_ROW_JS = f"!!document.querySelector('#behavior-timeline-{SID} .tl-item')"

_AUDIO_HEALTH_JS = """
(() => {
  const card = document.getElementById('audio-sensor-__SID__');
  return card ? (card.getAttribute('data-audio-health') || '') : '';
})()
""".replace("__SID__", SID)

# AU-08：prov-banner 模态声明行出现合成回放标注（服务端渲染，reload 后可读）。
_PROV_SYNTHETIC_JS = (
    "document.body.innerText.indexOf('合成回放 (SYNTHETIC_REPLAY)') >= 0"
)

_CANVAS_JS = """
(() => {
  const c = document.getElementById('waveform-canvas-__SID__');
  if (!c) return { exists: false };
  let nonBg = -1;
  try {
    const ctx = c.getContext('2d');
    if (!ctx) return { exists: true, w: c.width, h: c.height, nonBgSamples: -2 };
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    nonBg = 0;
    for (let i = 0; i < d.length; i += 16) {
      if (!(d[i] === 30 && d[i + 1] === 41 && d[i + 2] === 59)) nonBg++;
    }
  } catch (e) {
    nonBg = -3;
  }
  return { exists: true, w: c.width, h: c.height, nonBgSamples: nonBg };
})()
""".replace("__SID__", SID)

_COUNTS_BODY_JS = """
  const g = (id) => document.getElementById(id);
  const tl = g('timeline-list-__SID__') || document.querySelector('.timeline');
  let runtimeLi = -1;
  if (tl) {
    runtimeLi = Array.from(tl.querySelectorAll('li.tl-item[data-ref]')).filter(
      (li) => ((li.getAttribute('data-ref') || '').indexOf('golden://') !== 0)
    ).length;
  }
  return {
    audioTableRows: document.querySelectorAll('table.audio-table tr').length,
    psRecentEntries: document.querySelectorAll('#ps-recent-__SID__ .ps-entry').length,
    psHistoryRendered: document.querySelectorAll('#ps-history-list-__SID__ .ps-entry').length,
    behaviorItems: document.querySelectorAll('#behavior-timeline-__SID__ .tl-item').length,
    timelineRuntimeLi: runtimeLi,
    caseTimeMarks: document.querySelectorAll('#case-time-track-__SID__ .case-time-mark').length,
    bodyNodes: document.querySelectorAll('*').length,
  };
""".replace("__SID__", SID)

_COUNTS_JS = "(() => {" + _COUNTS_BODY_JS + "})()"

_SNAPSHOT_JS = """
(() => {
  const g = (id) => document.getElementById(id);
  const vpW = window.innerWidth;
  const vpH = window.innerHeight;
  const probe = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
      hasDebugAttr: el.hasAttribute('data-debug-only'),
      display: cs.display,
      visibility: cs.visibility,
      opacity: parseFloat(cs.opacity || '1'),
      rect: {
        left: r.left, top: r.top, right: r.right, bottom: r.bottom,
        width: r.width, height: r.height,
      },
      text: el.innerText || '',
      vpW: vpW,
      vpH: vpH,
    };
  };
  const tlUl = document.querySelector('.timeline');
  const moreBtn = tlUl ? tlUl.parentNode.querySelector('.tl-more-toggle') : null;
  const psRecent = g('ps-recent-__SID__');
  const behavior = g('behavior-timeline-__SID__');
  const counts = (() => {__COUNTS_BODY__})();
  return {
    bodyText: document.body.innerText,
    demoStat: probe(g('demo-stat-__SID__')),
    ovFrame: probe(g('ov-frame-__SID__')),
    ovTime: probe(g('ov-time-__SID__')),
    dsSession: probe(g('ds-session-__SID__')),
    binding: probe(document.querySelector('.case-video-binding')),
    perception: probe(g('live-perception-__SID__')),
    sensorAudio: probe(g('audio-sensor-__SID__')),
    psRecentText: psRecent ? (psRecent.innerText || '') : '',
    behaviorText: behavior ? (behavior.innerText || '') : '',
    moreBtnText: moreBtn ? (moreBtn.textContent || '') : '',
    debugNodes: Array.from(document.querySelectorAll('[data-debug-only]')).map((el, i) => {
      const p = probe(el);
      p.key = el.id || (el.tagName + '#' + i);
      return p;
    }),
    counts: counts,
  };
})()
""".replace("__COUNTS_BODY__", _COUNTS_BODY_JS).replace("__SID__", SID)


def _poll_until(page: Any, js_predicate: str, timeout_ms: int) -> bool:
    """轮询等待页内 JS 谓词为真（带超时上限）；超时返回 False 由调用方决定语义。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            matched = bool(page.evaluate(js_predicate))
        except Exception:  # noqa: BLE001 — 导航竞态期间 evaluate 可能抛错，按未命中重试
            matched = False
        if matched:
            return True
        page.wait_for_timeout(POLL_INTERVAL_MS)
    return False


def _capture_snapshot(page: Any) -> dict[str, Any]:
    return dict(page.evaluate(_SNAPSHOT_JS))


# ---------------------------------------------------------------------------
# Fixtures（复用既有验收测试基础设施模式：外部 server + 单浏览器 session）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def _browser():
    """单真实 Browser Session（headless Chromium，1440x900 viewport）。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM_PATH, headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        yield page
        browser.close()


@pytest.fixture(scope="module")
def contract_risk(_browser):
    """risk 主场景观察快照：reset → 首帧 → 条件轮询（感知/行为数据涌现）→ 采集。"""
    page = _browser
    requests.post(f"{BASE}/demo/reset", timeout=15)
    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
    first_frame = _poll_until(page, _FIRST_FRAME_JS, FIRST_FRAME_TIMEOUT_MS)
    perception_ready = _poll_until(page, _PERCEPTION_ROW_JS, PERCEPTION_DATA_TIMEOUT_MS)
    behavior_ready = _poll_until(page, _BEHAVIOR_ROW_JS, BEHAVIOR_DATA_TIMEOUT_MS)
    page.wait_for_timeout(1_500)
    return {
        "page": page,
        "first_frame": first_frame,
        "perception_ready": perception_ready,
        "behavior_ready": behavior_ready,
        "snapshot": _capture_snapshot(page),
    }


@pytest.fixture(scope="module")
def audio_lifecycle(_browser):
    """音频生命周期观察：三态轨迹 + 播完后静音窗计数（AU-04 / AU-06 / AU-07 / AU-03c）。"""
    page = _browser
    requests.post(f"{BASE}/demo/reset", timeout=15)
    page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
    _poll_until(page, _FIRST_FRAME_JS, FIRST_FRAME_TIMEOUT_MS)

    states: list[str] = []
    saw_recent = False
    canvas_info: dict[str, Any] | None = None
    deadline = time.monotonic() + (AUDIO_EVENT_TIMEOUT_MS + AUDIO_STALE_TIMEOUT_MS) / 1000.0
    while time.monotonic() < deadline:
        state = str(page.evaluate(_AUDIO_HEALTH_JS) or "")
        if state and (not states or states[-1] != state):
            states.append(state)
        if state == "RECENT_EVENT":
            saw_recent = True
            if canvas_info is None:
                canvas_info = dict(page.evaluate(_CANVAS_JS))
        if saw_recent and state == "NO_RECENT_EVENT":
            break
        page.wait_for_timeout(POLL_INTERVAL_MS)

    # SSOT v4.0 T2 fix：音频时间线有间隔（如 12→20s = 8s gap），recent window
    # 可能在事件间误判 NO_RECENT_EVENT。等音频行稳定（12s 无变化，超过最大间隔）
    # 才取 baseline，确保所有事件已投递，避免 AU-07a 误报"replay 结束后仍在增长"。
    prev_rows = -1
    stable_count = 0
    for _ in range(20):
        page.wait_for_timeout(1_000)
        rows = int(page.evaluate("document.querySelectorAll('table.audio-table tr').length"))
        if rows == prev_rows and rows > 0:
            stable_count += 1
            if stable_count >= 12:
                break
        else:
            stable_count = 0
        prev_rows = rows

    baseline = dict(page.evaluate(_COUNTS_JS))
    samples: list[dict[str, Any]] = []
    for _ in range(AUDIO_QUIET_WINDOW_MS // 2_000):
        page.wait_for_timeout(2_000)
        samples.append(dict(page.evaluate(_COUNTS_JS)))
    return {
        "states": states,
        "saw_recent": saw_recent,
        "canvas_info": canvas_info,
        "baseline": baseline,
        "samples": samples,
    }


# ===========================================================================
# V 断言（§3.2.1 · 缺陷锚点 A1–A6）
# ===========================================================================
class TestVContractSurface:
    """双模隔离与人话化契约（product mode 为默认态）。"""

    def test_v01_demo_stat_dual_mode_isolated(self, contract_risk):
        """V-01（A1）：Demo 状态面板必须带 data-debug-only 且 product mode 下不可见。"""
        info = contract_risk["snapshot"]["demoStat"]
        if info is None:
            return
        detail = (
            f"display={info['display']} visibility={info['visibility']} "
            f"opacity={info['opacity']} rect={info['rect']}"
        )
        assert info["hasDebugAttr"], f"A1/V-01 demo-stat 缺少 data-debug-only 标记（{detail}）"
        assert _is_invisible_per_dim2(info), f"A1/V-01 product mode 下 demo-stat 可见（{detail}）"

    def test_v02_overlay_chips_dual_mode_isolated(self, contract_risk):
        """V-02（A2）：overlay chips（ov-frame-* / ov-time-*）同 V-01 双模隔离。"""
        snap = contract_risk["snapshot"]
        for chip_key, chip_desc in (("ovFrame", "ov-frame"), ("ovTime", "ov-time")):
            info = snap[chip_key]
            if info is None:
                continue
            detail = f"display={info['display']} hasDebugAttr={info['hasDebugAttr']}"
            assert info["hasDebugAttr"] and _is_invisible_per_dim2(info), (
                f"A2/V-02 overlay chip {chip_desc} 未双模隔离（{detail}）"
            )

    def test_v03_media_binding_humanized(self, contract_risk):
        """V-03（A3）：媒体源绑定行人话化，或不含内部组件名，或整行 data-debug-only 隔离。"""
        info = contract_risk["snapshot"]["binding"]
        if info is None:
            return
        hits = [name for name in INTERNAL_COMPONENT_NAMES if name in info["text"]]
        isolated = info["hasDebugAttr"] and _is_invisible_per_dim2(info)
        assert not hits or isolated, (
            f"A3/V-03 媒体源绑定行暴露内部组件名 {hits}：'{info['text'][:200]}'"
        )

    def test_v04_internal_terms_absent_from_visible_text(self, contract_risk):
        """V-04：内部概念术语清零（并入 §3.2.3 黑名单②③统一扫描用户可见文本）。"""
        body = contract_risk["snapshot"]["bodyText"]
        rules = [
            *((name, re.escape(name)) for name in INTERNAL_COMPONENT_NAMES),
            *((name, re.escape(name)) for name in INTERNAL_CONCEPT_TERMS),
        ]
        hits = _scan_hits(body, rules)
        assert not hits, f"V-04 用户可见文本命中内部命名/术语：{_fmt_hits(hits)}"

    def test_v05_perception_entries_humanized(self, contract_risk):
        """V-05（A5）：感知条目不含 conf/bbox 工程字段，以人话描述替代。"""
        if not contract_risk["perception_ready"]:
            pytest.skip("观察窗内无视觉检测条目（人物未入镜或检测流未通），V-05 无法评估")
        info = contract_risk["snapshot"]["perception"]
        hits = _scan_hits(info["text"], list(PERCEPTION_FIELD_PATTERNS))
        assert not hits, f"A5/V-05 感知条目暴露工程字段（应人话化）：{_fmt_hits(hits)}"

    def test_v06_session_timer_isolated(self, contract_risk):
        """V-06（A6）：Session 计时器（ds-session-*）同 V-01 双模隔离。"""
        info = contract_risk["snapshot"]["dsSession"]
        if info is None:
            return
        detail = f"display={info['display']} hasDebugAttr={info['hasDebugAttr']}"
        assert info["hasDebugAttr"] and _is_invisible_per_dim2(info), (
            f"A6/V-06 Session 计时器未双模隔离（{detail}）"
        )


# ===========================================================================
# 维度① 工程语义黑名单逐条断言（§3.2.3 · 用户可见文本主断言面）
# ===========================================================================
class TestBlacklistDim1BodyText:
    """每条黑名单规则独立成测（只增不减；命中输出明细上下文）。"""

    @pytest.mark.parametrize(
        ("group", "rule_id", "pattern"),
        _ALL_DIM1_RULES,
        ids=[f"{g}:{rid}" for g, rid, _p in _ALL_DIM1_RULES],
    )
    def test_dim1_body_text_rule(self, contract_risk, group: str, rule_id: str, pattern: str):
        hits = _scan_hits(contract_risk["snapshot"]["bodyText"], [(rule_id, pattern)])
        assert not hits, f"维度① innerText 命中黑名单[{group}/{rule_id}]：{_fmt_hits(hits)}"


# ===========================================================================
# 维度② Debug 元素隔离断言（§3.2.5 · 防"忘了 hidden / 藏一半"）
# ===========================================================================
class TestBlacklistDim2DebugIsolation:
    def test_dim2_debug_only_nodes_all_invisible(self, contract_risk):
        """集合为空直接 PASS；非空则逐节点验证五判据（OR）至少一项成立。"""
        nodes = contract_risk["snapshot"]["debugNodes"]
        offenders = [str(n["key"]) for n in nodes if not _is_invisible_per_dim2(n)]
        assert not offenders, f"维度② data-debug-only 节点在 product mode 可见：{offenders}"


# ===========================================================================
# AU 断言（§3.2.2 · 缺陷锚点 B1–B5/B7）
# ===========================================================================
class TestAuPerceptionSurfaces:
    def test_au01_audio_table_rows_humanized(self, contract_risk):
        """AU-01（B1）：audio-table 行不含 score=/conf=（表位于「详细证据」折叠区）。"""
        rows = contract_risk["snapshot"]["counts"]["audioTableRows"]
        if rows == 0:
            # 表不存在时无暴露面 → 合规（当前实测表存在且含行，本分支为防回归兜底）。
            return
        table_text = str(contract_risk["page"].evaluate(
            "(document.querySelector('table.audio-table')||{}).textContent||''"
        ))
        hits = _scan_hits(table_text, list(ENGINEERING_FIELD_PATTERNS))
        assert not hits, (
            "B1/AU-01 audio-table 行暴露 score=/conf= 工程字段"
            f"（双源：renderer._render_audio_evidence + live_stream.js _buildAudioRow）："
            f"{_fmt_hits(hits)}"
        )

    def test_au02_no_bare_score_in_stream_entries(self, contract_risk):
        """AU-02（B2）：感知流/行为时间线条目不含裸 score 数值。"""
        if not contract_risk["behavior_ready"]:
            pytest.skip("观察窗内行为时间线无条目，AU-02 无法评估（数据缺口，如实上报）")
        snap = contract_risk["snapshot"]
        surfaces = (("感知流", snap["psRecentText"]), ("行为时间线", snap["behaviorText"]))
        for surface_name, text in surfaces:
            hits = _scan_hits(text, [("bare_score_decimal", _BARE_SCORE_RE)])
            assert not hits, f"B2/AU-02 {surface_name}出现裸 score 数值：{_fmt_hits(hits)}"

    def test_au03a_visible_rows_within_configured_limit(self, contract_risk):
        """AU-03 不变量①：各动态面可见行数 ≤ 配置显示上限（N 为配置事实快照）。"""
        counts = contract_risk["snapshot"]["counts"]
        assert counts["psRecentEntries"] <= PS_VISIBLE_LIMIT, (
            f"AU-03① 感知流可见条目 {counts['psRecentEntries']} > 上限 {PS_VISIBLE_LIMIT}"
        )
        assert counts["psHistoryRendered"] <= PS_HISTORY_RENDER_LIMIT, (
            f"AU-03① 历史感知渲染条目 {counts['psHistoryRendered']} > 上限 {PS_HISTORY_RENDER_LIMIT}"
        )
        assert counts["behaviorItems"] <= BEHAVIOR_TIMELINE_LIMIT, (
            f"AU-03① 行为时间线条目 {counts['behaviorItems']} > 上限 {BEHAVIOR_TIMELINE_LIMIT}"
        )
        if counts["timelineRuntimeLi"] >= 0:
            assert counts["timelineRuntimeLi"] <= TIMELINE_RUNTIME_LIMIT, (
                f"AU-03① 时间线 runtime 节点 {counts['timelineRuntimeLi']} > 上限 "
                f"{TIMELINE_RUNTIME_LIMIT}"
            )

    def test_au03b_fold_accounting_conserved(self, contract_risk):
        """AU-03 不变量②：折叠账目守恒 collapsed_count = total_count − visible_count。"""
        snap = contract_risk["snapshot"]
        btn_text = snap["moreBtnText"]
        if not btn_text:
            return
        m = re.search(r"已折叠\s*(\d+)\s*条\s*/\s*共\s*(\d+)\s*条", btn_text)
        if m is None:
            return
        hidden, total = int(m.group(1)), int(m.group(2))
        visible = snap["counts"]["timelineRuntimeLi"]
        assert total - hidden == visible, (
            f"AU-03② 折叠账目不守恒：共 {total} / 折叠 {hidden} / DOM 可见 {visible}"
        )

    def test_au03c_loop_bounded_dom_nodes(self, audio_lifecycle):
        """AU-03 不变量③（B3/B7）：静音观察窗内 DOM 节点总数有界（防无限累积面）。"""
        all_counts = [audio_lifecycle["baseline"], *audio_lifecycle["samples"]]
        node_counts = [int(c["bodyNodes"]) for c in all_counts]
        peak = max(node_counts)
        assert peak <= BODY_NODE_HARD_BOUND, (
            f"B3/AU-03③ DOM 节点总数 {peak} 超绝对上界 {BODY_NODE_HARD_BOUND}"
        )
        drift = max(node_counts) - min(node_counts)
        assert drift <= 50, f"B3/B7/AU-03③ 静音窗内 DOM 节点漂移 {drift}（疑似无上限累积面）"

    def test_au05_human_labels_regression_guard(self, contract_risk):
        """AU-05：distress/telephone 中文标签仍正确渲染（防 E 阶段修复误伤映射表）。"""
        body = contract_risk["snapshot"]["bodyText"]
        rendered = [zh for zh in _AUDIO_KIND_ZH_LABELS if zh in body]
        assert rendered, (
            "AU-05 页面未见任何五类声学中文标签"
            f"（{_AUDIO_KIND_ZH_LABELS}）；若音频确已播放，说明 AudioKind→中文映射回归被破坏"
        )

    def test_au05b_audio_sensor_kinds_humanized(self, contract_risk):
        """AU-05b（新发现登记）：AUDIO SENSOR 卡 Kinds detected 行不得暴露裸 audio_* 枚举。"""
        info = contract_risk["snapshot"]["sensorAudio"]
        if info is None:
            return
        hits = _scan_hits(info["text"], [("raw_audio_enum", _RAW_AUDIO_ENUM_RE)])
        assert not hits, (
            f"新增发现·AU-05b AUDIO SENSOR 卡暴露裸枚举 kind（应经中文映射人话化）："
            f"{_fmt_hits(hits)}"
        )

    def test_au08_modality_provenance_visible(self, contract_risk):
        """AU-08 · Provenance 显性化（Owner 裁决 2026-08-24）：模态来源声明常显。

        Simulation 与真实推理必须一眼可分：观看者无需交互即可读到
        视觉源 / 音频语义源 / 风险判定 三段派生声明（禁止藏在折叠区）。
        product_story_risk 应然态：视觉=实时推理、音频语义=合成回放（synthetic_replay
        注入经 live_adapter 投影为 FIXTURE）、风险判定=runtime-computed。
        """
        page = contract_risk["page"]
        mounted = bool(page.evaluate("!!document.querySelector('.prov-modality')"))
        assert mounted, "AU-08 .prov-modality 声明行未挂载"
        # prov-banner 为服务端一次性渲染：先轮询确认音频证据确已摄入（audio-table
        # 行涌现），再 reload 使 banner 反映含 FIXTURE 投影的最新 provenance。
        audio_ready = _poll_until(
            page,
            "document.querySelectorAll('table.audio-table tr').length > 0",
            AUDIO_EVENT_TIMEOUT_MS,
        )
        assert audio_ready, (
            f"AU-08 观察窗 {AUDIO_EVENT_TIMEOUT_MS}ms 内无任何 audio-table 行涌现"
            "（replay 注入未生效，无法验证合成回放声明）"
        )
        page.reload(wait_until="domcontentloaded", timeout=30_000)
        synthetic_ready = _poll_until(page, _PROV_SYNTHETIC_JS, FIRST_FRAME_TIMEOUT_MS)
        assert synthetic_ready, (
            "AU-08 reload 后 prov-banner 未见「合成回放 (SYNTHETIC_REPLAY)」"
            "（provenance 显性化回归：Simulation 与真实推理不可区分即违契约）"
        )
        body = str(page.evaluate("document.body.innerText"))
        for required in ("视觉源: 实时推理 (REAL_RUNTIME_VIDEO)", "风险判定: runtime-computed"):
            assert required in body, f"AU-08 prov-banner 缺少模态声明片段「{required}」"

    def test_au09_distress_cry_semantic_downgrade_guard(self):
        """AU-09 · distress_cry 语义降级守护（Owner 处置矩阵 2026-08-24 · v4.0 收紧）。

        该类别对正常电话语音存在稳定误报（H-5，六组素材实证：含 F-1 benign
        normal_call fixture，2026-08-24 T1 证伪实验复现 distress_cry×7）：允许保留
        感知输出与中文映射，但禁止任何「哭腔/哭诉/求助」语义断言口吻——主标签统一
        「声学异常活动(当前算法判定)」+ 已知误识别声明；不作为风险升级依据。
        product_story fixture 无 distress_cry 事件，DOM 无法自然触发该路径，
        故静态守护 live_stream.js 特判逻辑，防未来回归删除。
        """
        js_path = Path(__file__).resolve().parents[2] / (
            "src/home_perception/visualizer/assets/live_stream.js"
        )
        src = js_path.read_text(encoding="utf-8")
        assert "_AUDIO_KIND_CAUTION" in src, "AU-09 降级类别集合缺失"
        assert "audio_distress_cry: true" in src, "AU-09 distress_cry 未登记为降级类别"
        assert "audio_distress_cry: '声学异常活动'" in src, (
            "AU-09 js 主标签未降级为「声学异常活动」（哭腔/求助类断言口吻回归）"
        )
        assert "kz + '(当前算法判定)'" in src, "AU-09 感知流降级框架文案缺失"
        assert _DISTRESS_CAUTION_NOTE in src, "AU-09 已知误识别声明文案缺失"
        # 服务端静态渲染路径（首屏摘要 / sensor 卡 / audio-table 均经 renderer 映射）同纪律。
        py_path = Path(__file__).resolve().parents[2] / (
            "src/home_perception/visualizer/renderer.py"
        )
        rsrc = py_path.read_text(encoding="utf-8")
        assert "声学异常活动(当前算法判定)" in rsrc, (
            "AU-09 renderer 映射未对 distress_cry 语义降级（确定性断言口吻回归）"
        )
        assert "audio-caution-note" in rsrc, "AU-09 audio-table 已知误识别脚注缺失"

    def test_au10_audio_event_locator_positions_only(self, contract_risk):
        """AU-10 · 声学事件定位条（SSOT v4.0 T3 · Owner 裁决）。

        波形/时间轴只标注「声学事件的检测时刻位置」，绝不构成语义背书：
        ① locator 与 audio-table 数据行数一致；② 红线文案在场；
        ③ locator 区域黑名单：不得出现哭诉/哭腔/求助类语义词。
        """
        page = contract_risk["page"]
        ready = _poll_until(
            page,
            "document.querySelectorAll('table.audio-table tr').length > 0",
            AUDIO_EVENT_TIMEOUT_MS,
        )
        assert ready, (
            f"AU-10 观察窗 {AUDIO_EVENT_TIMEOUT_MS}ms 内 audio-table 无行涌现"
            "（replay 注入未生效，无法验证定位条）"
        )
        # locator 为服务端静态渲染（details 折叠区内）：与 prov-banner 同对策，
        # 待音频摄入齐全后 reload 取最新服务端渲染，再展开 details 断言。
        page.reload(wait_until="domcontentloaded", timeout=30_000)
        expanded = page.evaluate(
            """() => {
              const t = document.querySelector('table.audio-table');
              if (!t) return false;
              const d = t.closest('details');
              if (d) d.open = true;
              const loc = document.querySelector('.audio-event-locator');
              return !!loc;
            }"""
        )
        assert expanded, (
            "AU-10 详细证据展开后未见 .audio-event-locator"
            "（定位条渲染缺失或未挂接 audio-table 上方）"
        )
        result = page.evaluate(
            """() => {
              const loc = document.querySelector('.audio-event-locator');
              const rows = document.querySelectorAll('table.audio-table tr').length - 1;
              const txt = loc.innerText || '';
              return {
                dots: loc.querySelectorAll('circle').length,
                rows: rows,
                text: txt,
              };
            }"""
        )
        assert result["dots"] == result["rows"], (
            f"AU-10 定位点数 {result['dots']} ≠ audio-table 数据行数 {result['rows']}"
            "（事件与可视化位置不一致即违契约）"
        )
        assert "不代表对声音语义的判定结论" in result["text"], (
            "AU-10 定位条缺少「非语义判定」红线文案"
        )
        for banned in ("哭诉", "哭腔", "求助"):
            assert banned not in result["text"], (
                f"AU-10 定位条出现语义背书词汇「{banned}」"
                "（波形只可标检测时刻，不得包装成信号解释）"
            )


class TestAuAudioLifecycle:
    def test_au04_audio_health_three_state_machine(self, audio_lifecycle):
        """AU-04（B5）：Audio Health 三态状态机（SPEC §2.4 契约补齐的真测试缺口）。"""
        states = audio_lifecycle["states"]
        assert states, "未采集到任何 Audio Health 状态"
        legal = {"RECENT_EVENT", "NO_RECENT_EVENT", "UNAVAILABLE"}
        unexpected = set(states) - legal
        assert not unexpected, f"AU-04 出现非法健康态：{unexpected}（序列 {states}）"
        if not audio_lifecycle["saw_recent"]:
            pytest.skip(f"观察窗内无音频事件（RECENT_EVENT 未出现），三态转换无法评估：{states}")
        assert "NO_RECENT_EVENT" in states, f"AU-04 状态序列缺少 NO_RECENT_EVENT：{states}"
        assert states[-1] == "NO_RECENT_EVENT", (
            f"AU-04 音频停息 5s 后应回落 NO_RECENT_EVENT，实测末态 {states[-1]}（序列 {states}）"
        )

    def test_au06_rms_canvas_exists_with_samples(self, audio_lifecycle):
        """AU-06（B4 DOM 侧拆层）：canvas 存在 + 尺寸 > 0 + 曾绘入样本（合理性归 D2）。"""
        canvas = audio_lifecycle["canvas_info"]
        if canvas is None:
            pytest.skip("音频事件未出现，canvas 样本检查无从进行")
        assert canvas["exists"], (
            "B4/AU-06 RMS 波形 canvas 不存在于 DOM（已知根因：product_story_risk 未注册 "
            "_SCENARIO_SURFACES 音频 Surface，has_audio_surface()=False 门控了渲染）"
        )
        assert canvas["w"] > 0 and canvas["h"] > 0, (
            f"B4/AU-06 canvas 尺寸非法：{canvas['w']}x{canvas['h']}"
        )
        assert int(canvas["nonBgSamples"]) > 0, (
            "B4/AU-06 canvas 存在但从未绘入任何 RMS 样本（evidence_delta.rms_window 未到达）"
        )

    def test_au07a_audio_bounded_after_replay_ends(self, audio_lifecycle):
        """AU-07a · Audio boundedness（v3.4 Owner 裁决拆分 · 保留不豁免）。

        语义修正记录：原 AU-07 把「音频生命周期」与「Runtime 生命周期」混在一个
        断言里——静音窗内视觉帧节点合法增长被误判为 FAIL（F-4 探针铁证：增长
        100% 来自 live://frame/N）。现拆分为 a/b：本条只守**音频生命周期封闭性**
        ——audio_replay 声明时间线结束后，audio-derived DOM evidence
        （audio-table 行）不得继续生成。混合感知面（ps/behavior）的运行期上界
        由 AU-03①②③ 与 AU-07b 分层把守。
        """
        baseline = audio_lifecycle["baseline"]
        assert baseline, "未采集到静音基线计数"
        samples = audio_lifecycle["samples"]
        assert samples, "静音观察窗未采样（fixture 异常）"
        for idx, sample in enumerate(samples, start=1):
            after, before = int(sample["audioTableRows"]), int(baseline["audioTableRows"])
            assert after == before, (
                f"AU-07a replay 结束后 audio-derived evidence 仍在增长："
                f"audioTableRows {before} → {after}（sample#{idx}）"
            )

    def test_au07b_visual_surface_bounded_during_runtime(self, audio_lifecycle):
        """AU-07b · Runtime visual boundedness（v3.4 Owner 裁决拆分）。

        视频持续播放期间，视觉 frame/event DOM **自身**必须有界（渲染层裁剪上限
        生效），而非「随播放时长线性无界」。本条与音频生命周期解耦：live://frame/N
        的合法推进不计入 AU-07a，但其渲染总量必须被封顶。
        """
        samples = audio_lifecycle["samples"]
        assert samples, "运行期观察窗未采样（fixture 异常）"
        for idx, sample in enumerate(samples, start=1):
            tl = int(sample["timelineRuntimeLi"])
            assert tl <= TIMELINE_RUNTIME_LIMIT, (
                f"AU-07b 运行期视觉时间线节点 {tl} > 渲染上限 {TIMELINE_RUNTIME_LIMIT}"
                f"（sample#{idx}；裁剪机制失效）"
            )
            marks = int(sample["caseTimeMarks"])
            assert marks <= CASE_TIME_MARK_LIMIT, (
                f"AU-07b Case Time 刻度 {marks} > 上限 {CASE_TIME_MARK_LIMIT}（sample#{idx}）"
            )