"""ADR-0036 Slice A · Case Viewer 渲染器：EvidenceProjection → 自包含 HTML。

复用 renderer（D1 Explorer）的展示构建块（``_render_timeline`` / ``_render_decision`` /
``_render_evidence_graph`` / ``_render_gate`` 等）保证 **AC-2 语义一致**，并叠加 Slice A
专属的产品主轴（Case Viewer 首屏叙事）：Case Video（Media Timeline）+ 当前风险 + 为什么 +
系统行动 + 统一 Evidence Timeline + （折叠）详细证据。

纪律（对齐 ADR-0035 D4 + ADR-0036 不变式）：
- **VM-1（唯一 View Model）**：所有业务展示状态由 ``EvidenceProjection`` 派生，无第二份
  事实模型（不定义 riskData/decisionData/timelineData/audioData/audioState 等）；
- **VM-3**：本模块只 import ``visualizer`` 内部（renderer / schema / case_presentation），
  不 import ``silver_demo`` / 生产 runtime；
- **VM-10 / AC-14（双时间轴 + Case Time）**：Media Timeline（媒体播放进度，纯 UI）与
  Evidence Timeline（事实语义）是两类关注，经 Case Time 映射同步，**不混为同一数组/状态**；
- **AC-7（Provenance 一等视觉）**：``provenance_kind`` 在每个案例视图显式呈现
  （程序化场景·可复现 / 真实传感器·实时数据 / 固定测试素材·非实时）；
- **AC-9（统一时间轴）**：本 slice 无音频维度（AC-1c），不出现视频/音频/决策三套独立轴；
- **AC-16（首屏叙事）**：Case Video → 当前风险 → 为什么 → 系统行动 → Evidence Timeline
  → 详细证据（折叠）；
- 复用 renderer 的内联 JS 顺序铁律：echarts → replay_js(定义) → replay_inits(init) →
  graph/media IIFE；``global.__Replay = {`` 整篇恰好注入 1 次；
- 确定性 / 脱敏 / fail-closed 同 D1（同输入两次渲染逐字节一致，无当前时间/随机数）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from home_perception.visualizer import renderer as _R
from home_perception.visualizer.schema.evidence import EvidenceProjection
from home_perception.visualizer.viewer.case_presentation import (
    CasePresentationDescriptor,
    build_default_case_presentation,
)
from home_perception.visualizer.viewer.media_source import resolve_media_source

if TYPE_CHECKING:  # 仅类型标注
    from pathlib import Path

    from home_perception.visualizer.schema.evidence import ScenarioEvidence

# Case Video 叙事结构（D-CaseVideo · VM-12）：产品主视频，非 Analysis Video。
_CASE_VIDEO_NARRATIVE = (
    "Context",
    "Incident",
    "Risk Escalation",
    "AI Perception",
    "Intervention",
    "Outcome",
)

# AC-7 provenance 文案映射（一等视觉，绝默认隐藏）。
_PROVENANCE_TEXT = {
    "SIMULATED": "程序化场景 · 可复现",
    "REAL_SENSOR": "真实传感器 · 实时数据",
    "FIXTURE": "固定测试素材 · 非实时",
}
_PROVENANCE_CLASS = {
    "SIMULATED": "prov-simulated",
    "REAL_SENSOR": "prov-real-sensor",
    "FIXTURE": "prov-fixture",
}

# Media Timeline（Case Time）JS 注入说明（Slice A.1）：
# - 引擎 ``window.__MediaPlayer`` 由 renderer._media_inline() 内联（媒体资产
#   assets/media.js），仅定义一次；
# - 每场景一段 init 调用 ``window.__MediaPlayer.init(sid, canvas, manifest, opts)``，
#   位于 replay_inits **之后**（保证 __Replay 实例已注册）；
# - 纪律（VM-10/AC-14）：Media 进度（MediaPlayer 主时钟）驱动 Evidence Timeline
#   （replay 实例）定位，二者是独立 DOM/状态，不合并为同一数组。


# 媒体源 URL 协议黑名单（评审 R2-#3，与前端 media.js _safeUrl 一致）：
# ``<video src>`` / 帧 URL 只放行 http(s) 绝对 URL 与相对路径；拒
# ``javascript:``/``data:``/``file:`` 等伪协议，避免 XSS / 本地文件读取。
# 不引入 urllib，保持 visualizer 的 stdlib-only AST 契约（_STDLIB_TOP 未含 urllib）。
_ALLOWED_URL_SCHEMES = ("http", "https")


def _url_scheme(url: str) -> str:
    """返回 URL 的 scheme（小写）；无显式 scheme（相对路径等）返回空串。

    仅按 RFC 3986 前缀识别：以字母开头、后接字母数字/'+'/'-'/'.'、终于 ':'。
    """
    if not url or not url[0].isascii() or not url[0].isalpha():
        return ""
    for i, ch in enumerate(url):
        if ch == ":":
            return url[:i].lower()
        if not (ch.isascii() and (ch.isalnum() or ch in "+-.")):
            return ""
    return ""


def _safe_media_src(url: str) -> str:
    """返回经协议黑名单校验的媒体 URL；非法 scheme 返回空串（fail-closed，不注入）。"""
    if not url:
        return ""
    scheme = _url_scheme(url)
    if scheme and scheme not in _ALLOWED_URL_SCHEMES:
        return ""
    return url


# ---------------------------------------------------------------------------
# 派生展示卡片（VM-1：全部来自 projection，无第二份事实模型）
# ---------------------------------------------------------------------------


def _render_provenance_banner(scenario: ScenarioEvidence) -> str:
    """AC-7：每个案例视图显式呈现 provenance_kind 及文案（一等视觉，绝默认隐藏）。"""
    kinds = {n["provenance_kind"] for n in scenario["timeline"]}
    if len(kinds) == 1:
        k = next(iter(kinds))
        text = _PROVENANCE_TEXT.get(k, k)
        badge = (
            f"<span class='prov-badge {_PROVENANCE_CLASS.get(k, '')}'>{_R._esc(k)}</span>"
            f" {_R._esc(text)}"
        )
    else:
        text = " · ".join(_PROVENANCE_TEXT.get(k, k) for k in sorted(kinds))
        badge = f"<span class='prov-badge'>MIXED</span> {_R._esc(text)}"
    return f"<div class='prov-banner'>provenance: {badge}</div>"


def _render_current_risk(scenario: ScenarioEvidence) -> str:
    """当前风险卡片（派生展示：来自 risk_levels / trace_outcome_kinds，VM-1）。"""
    risk_str = (
        "、".join(_R._translate_value(r) for r in scenario["risk_levels"]) or "—"
    )
    outcome_str = (
        "、".join(_R._translate_value(o) for o in scenario["trace_outcome_kinds"]) or "—"
    )
    return f"""
      <div class='card risk-card'>
        <div class='card-title'>当前风险</div>
        <div class='risk-level'>{_R._esc(risk_str)}</div>
        <div class='muted'>决策结论：{_R._esc(outcome_str)}</div>
      </div>"""


def _render_action(scenario: ScenarioEvidence) -> str:
    """系统行动卡片（派生展示：来自 command_types / recommended_actions，VM-1）。"""
    cmd_str = (
        "、".join(_R._translate_value(c) for c in scenario["command_types"]) or "—"
    )
    rec_str = (
        "、".join(_R._translate_value(c) for c in scenario["recommended_actions"]) or "—"
    )
    return f"""
      <div class='card action-card'>
        <div class='card-title'>系统行动</div>
        <div>实际命令：{_R._esc(cmd_str)}</div>
        <div class='muted'>建议动作：{_R._esc(rec_str)}</div>
      </div>"""


def _render_case_video(
    scenario: ScenarioEvidence,
    descriptor: CasePresentationDescriptor,
    media_manifest: dict | None,
    media_base_url: str,
) -> str:
    """Case Video 主轴（D-CaseVideo · VM-12）+ Media Timeline（Case Time，VM-10/AC-14）。

    Slice A.1 改造（对齐用户决策）：
    - 媒体字节不进 View Model（VM-10/AC-11）：经 Media Source Adapter 解析的
      ``media_manifest`` 只持 ref/template/count，画布用 ``<canvas>`` 实时绘制帧，
      **绝不** base64 内联 660 帧；
    - ``<video>`` 仅当源为 ``ArtifactVideoSource`` 且含 ``video_url``（原生控件播放）；
    - 绑定文案（源类型 + ref）**降级为脚注**（方案 3），不再占主轴 C 位；
    - 注入 ``media-manifest-{sid}`` 数据岛（manifest 的 frame_template 已叠加
      ``media_base_url``），供前端 MediaPlayer 消费。
    """
    sid = scenario["scenario_id"]
    sid_html = _R._esc(sid)
    mb = descriptor["media_binding"]
    src_kind = _R._esc(mb["source_kind"])
    ref = _R._esc(mb["ref"])

    # 媒体区：ArtifactVideoSource 用原生 <video>；其余（SyntheticFrameSource / 无媒体）
    # 用 canvas 播放器（MediaPlayer 主时钟驱动）。绝不占位空框。
    if (
        media_manifest
        and media_manifest.get("source_kind") == "ArtifactVideoSource"
        and media_manifest.get("video_url")
    ):
        # Slice D（AC-6）：导出 case.mp4 以相对 media base 的 URL 注册（如
        # "{sid}/media/{sid}__v1/case.mp4"）。相对 URL 须叠加 media_base_url 形成最终
        # 可解析地址（与 frame_template 同契约）；绝对 URL（http/https）原样透传。
        raw_vurl = media_manifest["video_url"]
        vurl = raw_vurl
        if media_base_url and not _url_scheme(raw_vurl):
            vurl = media_base_url.rstrip("/") + "/" + raw_vurl.lstrip("/")
        media_area = (
            f'<video class="case-video-el" controls preload="metadata" '
            f'src="{_R._esc(_safe_media_src(vurl))}"></video>'
        )
    else:
        media_area = (
            f'<canvas id="case-video-canvas-{sid_html}" class="case-video-canvas" '
            f'width="640" height="360"></canvas>'
        )

    # 绑定文案降级为脚注（方案 3）：不再占主轴 C 位。
    # 媒体缺失（Media Source Adapter 未解析到 manifest）→ 不展示孤儿 ref，标注"无媒体绑定"
    # （评审 R1-#4：ref 只在确有媒体资产时才对应真实绑定，否则误导）。
    if media_manifest is None:
        binding_footnote = (
            '<p class="muted case-video-binding">无媒体绑定（Media Source Adapter 未解析到媒体资产；'
            '控制条仍可驱动纯 UI 进度与 Evidence Timeline）</p>'
        )
    else:
        binding_footnote = (
            f'<p class="muted case-video-binding">媒体源绑定：<code>{src_kind}</code> · '
            f'ref={ref}（字节由 Media Source Adapter 经 ref 解析，不进 View Model）</p>'
        )

    media_timeline = f"""
        <div class="media-timeline" id="media-timeline-{sid_html}">
          <button class="rp-btn media-play" id="media-play-{sid_html}" title="播放/暂停">▶</button>
          <span class="rp-progress-wrap"><span class="media-progress" id="media-progress-{sid_html}"></span></span>
          <span class="media-time-label" id="media-time-label-{sid_html}">0.0s / --</span>
          <span class="muted">Media Timeline（Case Time 纯展示轴，经映射驱动 Evidence Timeline）</span>
        </div>"""

    manifest_island = ""
    if media_manifest:
        # 相对 media base 的帧模板 → 叠加 media_base_url 形成最终可解析 URL。
        tpl = media_manifest["frame_template"]
        if media_base_url:
            tpl = media_base_url.rstrip("/") + "/" + tpl.lstrip("/")
        inj = dict(media_manifest)
        inj["frame_template"] = tpl
        manifest_island = (
            f'<script type="application/json" id="media-manifest-{sid_html}">'
            f'{_R._sanitize_for_js(json.dumps(inj, ensure_ascii=False))}</script>'
        )

    return f"""
    <section class="fs-panel" id="fs-case-video-{sid_html}">
      <h3 class="view-anchor">Case Video（主轴）</h3>
      <div class="case-video">{media_area}{media_timeline}{binding_footnote}</div>
      {manifest_island}
      <p class="muted">Case Video 叙事结构：{' → '.join(_CASE_VIDEO_NARRATIVE)}（VM-12 · 产品主视频，关联叙事而非分析回放）</p>
    </section>"""


# ---------------------------------------------------------------------------
# 单场景组装（首屏叙事 + 折叠详细证据）
# ---------------------------------------------------------------------------


def _render_scenario_case(
    scenario: ScenarioEvidence,
    descriptor: CasePresentationDescriptor,
    panels: tuple[str, ...],
    media_manifest: dict | None,
    media_base_url: str,
) -> tuple[str, str]:
    """单场景 HTML + 该场景的 graph/media JS（一次遍历产出两块，评审 R3-#3 对称）。"""
    sid = scenario["scenario_id"]
    sid_html = _R._esc(sid)
    status = "PASS" if scenario["ok"] else "FAIL"
    status_class = "ok" if scenario["ok"] else "fail"
    # 图 IIFE（主 Evidence Graph + Cross Modal 子图）
    g_html, g_js = _R._render_evidence_graph(scenario)
    cm_html, cm_js = _R._render_graph(scenario)

    # 每场景 MediaPlayer init 调用（引擎 window.__MediaPlayer 由 _media_inline 全局注入一次）。
    # duration 取 descriptor time_mapping（无媒体时仍驱动纯 UI 进度 + Evidence 同步）；
    # fps 留 0 → MediaPlayer 回退到 manifest.fps（有媒体时）。
    duration = descriptor["time_mapping"]["media_duration_s"]
    media_init = (
        "(function(){"
        'var cv=document.getElementById("case-video-canvas-' + sid_html + '");'
        'var mm=document.getElementById("media-manifest-' + sid_html + '");'
        "var manifest=(mm&&mm.textContent)?JSON.parse(mm.textContent):null;"
        "window.__MediaPlayer.init("
        + _R._esc_js(sid)
        + ",cv,manifest,"
        + '{"duration":' + str(duration) + ',"fps":0});'
        "})();"
    )

    # 首屏面板（按编排顺序，AC-16）
    panel_html: list[str] = []
    for p in panels:
        if p == "case_video":
            panel_html.append(
                _render_case_video(scenario, descriptor, media_manifest, media_base_url)
            )
        elif p == "current_risk":
            panel_html.append(
                f'<section class="fs-panel" id="fs-current-risk-{sid_html}">'
                f'<h3 class="view-anchor">当前风险</h3>'
                f"{_render_current_risk(scenario)}</section>"
            )
        elif p == "why":
            panel_html.append(
                f'<section class="fs-panel" id="fs-why-{sid_html}">'
                f'<h3 class="view-anchor">为什么（Decision Explanation · 可重放）</h3>'
                f"{_R._render_decision(scenario)}</section>"
            )
        elif p == "action":
            panel_html.append(
                f'<section class="fs-panel" id="fs-action-{sid_html}">'
                f'<h3 class="view-anchor">系统行动</h3>'
                f"{_render_action(scenario)}</section>"
            )
        elif p == "evidence_timeline":
            panel_html.append(
                f'<section class="fs-panel" id="fs-evidence-timeline-{sid_html}">'
                f'<h3 class="view-anchor">统一 Evidence Timeline（发生了什么）</h3>'
                f"{_R._render_timeline(scenario)}</section>"
            )
        # 未知面板名静默忽略（前向兼容，不崩）

    # 详细证据（二级视图，折叠，不在首屏同屏，AC-16）
    details = f"""
      <section class="fs-panel" id="fs-details-{sid_html}">
        <details>
          <summary>详细证据（Graph / Fingerprint / Gate）</summary>
          <h3 class="view-anchor">Evidence Graph（因果链）</h3>
          {g_html}
          <h3 class="view-anchor">Cross Modal Graph（supports 子图）</h3>
          {cm_html}
          <h3 class="view-anchor">Fingerprint / Gate</h3>
          {_R._render_gate(scenario)}
        </details>
      </section>"""

    html_block = f"""
    <section class="scenario">
      <h2 class="scenario-title">
        <span class="badge {status_class}">{status}</span>
        <code>{sid_html}</code>
        <span class="muted">mode={_R._esc(scenario['mode'])} · frames={scenario['n_frames']}</span>
      </h2>
      {_render_provenance_banner(scenario)}
      {''.join(panel_html)}
      {details}
    </section>"""
    js_block = "\n".join(x for x in (g_js, cm_js, media_init) if x).strip()
    return html_block, js_block


# ---------------------------------------------------------------------------
# replay 接线（复用 renderer 纪律：数据岛 + init 调用，与 _render_timeline/_render_decision 契约一致）
# ---------------------------------------------------------------------------


def _build_replay_wiring(scenarios: tuple[ScenarioEvidence, ...]) -> tuple[str, str, str]:
    """生成 replay 数据岛 + init 调用（与 renderer.render_projection 同契约）。

    保证 ``_render_timeline``（timeline 轨道）与 ``_render_decision``（trace 轨道）能取到
    数据岛与 replay 实例。category 桥接键来自 renderer 常量（防漂移）。
    """
    replay_data_tags = "\n".join(
        '<script type="application/json" id="replay-data-{sid}">{data}</script>'.format(
            sid=_R._esc(s["scenario_id"]),
            data=_R._sanitize_for_js(
                json.dumps(
                    [
                        {**dict(n), "category": _R._STAGE_TO_GRAPH_CATEGORY.get(n["stage"])}
                        for n in s["timeline"]
                    ],
                    ensure_ascii=False,
                )
            ),
        )
        for s in scenarios
    )
    replay_trace_data_tags = "\n".join(
        '<script type="application/json" id="replay-trace-data-{sid}">{data}</script>'.format(
            sid=_R._esc(s["scenario_id"]),
            data=_R._sanitize_for_js(
                json.dumps(
                    [
                        {**dict(item), "category": _R._DECISION_KIND_TO_GRAPH_CATEGORY.get(item["kind"])}
                        for item in s["decision_evidence"]
                    ],
                    ensure_ascii=False,
                )
            ),
        )
        for s in scenarios
        if s.get("decision_evidence")
    )
    replay_inits = (
        "\n".join(
            "window.__Replay.init({});".format(_R._esc_js(s["scenario_id"]))
            + (
                "\nwindow.__Replay.init({}, 'trace');".format(_R._esc_js(s["scenario_id"]))
                if s.get("decision_evidence") else ""
            )
            for s in scenarios
        )
        if _R._replay_inline() else ""
    )
    return replay_data_tags, replay_trace_data_tags, replay_inits


# ---------------------------------------------------------------------------
# 顶层渲染
# ---------------------------------------------------------------------------


def render_case_viewer(
    projection: EvidenceProjection,
    descriptor: CasePresentationDescriptor | None = None,
    *,
    media_base_dir: str | Path | None = None,
    media_base_url: str = "",
) -> str:
    """EvidenceProjection → 自包含 Case Viewer HTML（确定性，fail-closed）。

    Args:
        projection: 唯一事实源（VM-1）。
        descriptor: 纯展示编排（VM-11）；缺省则派生默认编排。
        media_base_dir: artifact 根目录（内含 ``{sid}/media/``）。提供则经 Media
            Source Adapter 只读解析每场景媒体 manifest（Slice A.1）；``None`` → 无媒体
            （画布留空，仅纯 UI 进度 + Evidence 同步）。
        media_base_url: 从 HTML 到 ``media_base_dir`` 的相对 URL 前缀（供浏览器解析帧
            文件）；默认 ``""``（HTML 与 artifact 同目录）。

    Raises:
        ValueError: projection 结构非法（缺场景 / 场景数超上限）。
    """
    scenarios = projection.get("scenarios")
    if not isinstance(scenarios, tuple) or not scenarios:
        raise ValueError("EvidenceProjection.scenarios 为空或非法（fail-closed）")
    if len(scenarios) > 128:
        raise ValueError(f"EvidenceProjection.scenarios 数量超上限（{len(scenarios)}>128，fail-closed）")

    meta = projection.get("meta", {})
    if descriptor is None:
        descriptor = build_default_case_presentation(projection)  # 默认纯展示编排
    panels = descriptor["first_screen_layout"]["panels"]

    # Slice A.1：经 Media Source Adapter 只读解析每场景媒体 manifest（绝不生成帧）。
    mb = descriptor["media_binding"]
    media_manifests: dict[str, dict | None] = {}
    if media_base_dir is not None:
        for s in scenarios:
            media_manifests[s["scenario_id"]] = resolve_media_source(
                media_base_dir, s["scenario_id"], mb["source_kind"]
            )

    scenario_blocks: list[str] = []
    graph_blocks: list[str] = []
    for s in scenarios:
        html_block, js_block = _render_scenario_case(
            s, descriptor, panels, media_manifests.get(s["scenario_id"]), media_base_url
        )
        scenario_blocks.append(html_block)
        if js_block:
            graph_blocks.append(js_block)
    graph_script = "\n".join(graph_blocks)

    # Provenance 脚注文案：由 _PROVENANCE_TEXT 单一来源派生（评审 R2-#2：消除与
    # HTML/CSS 三处文案漂移；改 provenance 文案只动 _PROVENANCE_TEXT 一处）。
    prov_note = (
        "Provenance 一等视觉（AC-7）："
        + " / ".join(f"{k}→{v}" for k, v in _PROVENANCE_TEXT.items())
    )

    replay_data_tags, replay_trace_data_tags, replay_inits = _build_replay_wiring(scenarios)

    echarts = _R._echarts_inline()
    replay_js = _R._replay_inline()
    media_js = _R._media_inline()

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>SilverShield Case Viewer — {_R._esc(descriptor['title'])}</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;
         background:#f7f8fa; color:#1c2733; margin:0; padding:24px; }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2.scenario-title {{ font-size: 17px; margin: 28px 0 8px; }}
  h3.view-anchor {{ font-size: 14px; color:#3b4a5a; border-left: 4px solid #4a90d9;
                    padding-left: 8px; margin: 20px 0 10px; }}
  .muted {{ color:#6b7a8a; font-size: 12px; }}
  .subtitle {{ color:#3b4a5a; font-weight:600; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:10px;
            font-size:12px; color:#fff; }}
  .badge.ok {{ background:#2e9e6b; }}
  .badge.fail {{ background:#d64541; }}
  .meta-card {{ background:#fff; border:1px solid #e3e8ee; border-radius:8px;
                padding:12px 16px; margin:12px 0; }}
  .prov-note {{ background:#fff7e6; border:1px solid #f0c36d; color:#7a5a00;
                border-radius:8px; padding:10px 16px; margin:12px 0; font-size:13px; }}
  .scenario {{ background:#fff; border:1px solid #e3e8ee; border-radius:10px;
               padding:16px 20px; margin:20px 0; }}
  .fs-panel {{ margin:16px 0; }}
  /* Provenance（AC-7 一等视觉） */
  .prov-banner {{ background:#eef6ff; border:1px solid #cfe3fb; border-left:4px solid #4a90d9;
                  border-radius:6px; padding:8px 14px; margin:12px 0; font-size:13px; }}
  .prov-badge {{ display:inline-block; padding:1px 8px; border-radius:8px; font-size:11px;
                 color:#fff; font-weight:600; }}
  .prov-simulated {{ background:#7b5cd6; }}
  .prov-real-sensor {{ background:#2e9e6b; }}
  .prov-fixture {{ background:#8a8a8a; }}
  /* 派生卡片（风险 / 行动，VM-1 纯展示） */
  .card {{ background:#f4f7fb; border:1px solid #e3e8ee; border-radius:8px; padding:12px 16px; }}
  .card-title {{ font-weight:600; color:#3b4a5a; margin-bottom:6px; }}
  .risk-level {{ font-size:16px; font-weight:700; color:#d64541; }}
  .risk-card {{ border-left:4px solid #d64541; }}
  .action-card {{ border-left:4px solid #2e9e6b; }}
  /* Case Video（主轴）+ Media Timeline（Case Time，VM-10/AC-14） */
  .case-video {{ background:#0e1726; border-radius:8px; padding:14px; margin:8px 0; }}
  .case-video-el {{ width:100%; border-radius:6px; display:block; background:#000; }}
  .case-video-canvas {{ width:100%; max-width:640px; aspect-ratio:16/9; border-radius:6px;
                        display:block; background:#000; margin:0 auto; }}
  .media-timeline {{ display:flex; gap:8px; align-items:center; margin:10px 0 4px;
                     background:#f0f4f9; border:1px solid #e3e8ee; border-radius:8px;
                     padding:8px 12px; }}
  .media-play {{ cursor:pointer; border:1px solid #cdd6e0; background:#fff; border-radius:6px;
                padding:4px 10px; font-size:14px; line-height:1; }}
  .media-progress {{ display:block; height:100%; width:0; background:#7b5cd6; transition:width .2s; }}
  /* Timeline（复用 renderer） */
  .timeline {{ list-style:none; margin:0; padding:0 0 0 18px; border-left:2px solid #d8dee6; }}
  .tl-item {{ position:relative; margin:10px 0; }}
  .tl-dot {{ position:absolute; left:-25px; top:4px; width:12px; height:12px;
             border-radius:50%; border:2px solid #fff; box-shadow:0 0 0 1px #ccc; }}
  .tl-body {{ }}
  .tl-head {{ display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }}
  .tl-step {{ font-family:monospace; color:#4a90d9; font-weight:600; }}
  .tl-stage {{ font-weight:600; }}
  .tl-kind {{ font-size:12px; background:#eef2f7; border-radius:4px; padding:0 6px; }}
  .tl-verdict {{ font-size:13px; }}
  .tl-verdict.node-pass {{ color:#2e9e6b; }}
  .tl-verdict.node-fail {{ color:#d64541; }}
  .tl-verdict.node-neutral {{ color:#3b4a5a; }}
  .tl-meta {{ margin-top:2px; }}
  /* Decision cards（trace 轨道，复用 renderer） */
  .dc-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
              gap:10px; }}
  .dc-card {{ background:#f4f7fb; border:1px solid #e3e8ee; border-radius:8px; padding:10px 12px; }}
  .dc-label {{ font-size:12px; font-weight:600; }}
  .dc-value {{ margin-top:4px; font-size:14px; }}
  /* Graph（复用 renderer） */
  .graph-box {{ border:1px solid #e3e8ee; border-radius:8px; background:#fbfcfe; }}
  /* Gate（复用 renderer） */
  .gate-table {{ border-collapse:collapse; margin:8px 0; width:100%; }}
  .gate-table th, .gate-table td {{ border:1px solid #e3e8ee; padding:6px 10px;
                                     text-align:left; font-size:13px; }}
  .gate-table th {{ background:#f0f4f9; }}
  /* Replay 控制条（复用 renderer，双轨道 rp-*/rp-trace-*） */
  .replay-bar {{ display:flex; gap:8px; align-items:center; margin:8px 0 14px;
                 background:#f0f4f9; border:1px solid #e3e8ee; border-radius:8px; padding:8px 12px; }}
  .rp-btn {{ cursor:pointer; border:1px solid #cdd6e0; background:#fff; border-radius:6px;
             padding:4px 10px; font-size:14px; line-height:1; }}
  .rp-btn:hover {{ background:#e8f0fa; }}
  .rp-toggle {{ font-weight:700; min-width:38px; }}
  .rp-progress-wrap {{ flex:1; height:8px; background:#dde4ec; border-radius:4px; overflow:hidden; }}
  .rp-progress {{ display:block; height:100%; width:0; background:#4a90d9; transition:width .25s; }}
  .rp-progress-label {{ font-size:12px; color:#3b4a5a; font-family:monospace; }}
  .rp-speed-label {{ font-size:12px; color:#3b4a5a; }}
  .rp-speed {{ font-size:12px; }}
  .timeline .tl-item {{ transition: background .25s, opacity .25s; opacity:.55; }}
  .timeline .tl-item.played {{ opacity:1; }}
  .timeline .tl-item.played > .tl-body {{ background:#f4f8fd; border-radius:6px; }}
  .timeline .tl-item.active {{ opacity:1; }}
  .timeline .tl-item.active > .tl-body {{ background:#fff7e6; border-radius:6px; }}
  .timeline .tl-item.active .tl-dot {{ box-shadow:0 0 0 3px #f0c36d; }}
  .trace-list {{ list-style:none; margin:10px 0 0; padding:0; }}
  .trace-list .dc-card {{ transition: background .25s, opacity .25s, border-color .25s; opacity:.55; }}
  .trace-list .dc-card.played {{ opacity:1; }}
  .trace-list .dc-card.active {{ opacity:1; background:#fff7e6; border-color:#f0c36d;
                       box-shadow:0 0 0 3px #f0c36d; cursor:pointer; }}
  .glossary {{ margin:24px 0 8px; background:#fff; border:1px solid #e3e8ee;
               border-radius:8px; padding:10px 16px; }}
  .glossary summary {{ cursor:pointer; font-weight:600; color:#3b4a5a; }}
  .glossary ul {{ margin:8px 0 0; padding-left:20px; font-size:13px; }}
  .glossary li {{ margin:3px 0; }}
  code {{ background:#eef2f7; border-radius:4px; padding:1px 5px; font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>SilverShield Case Viewer</h1>
  <p class="muted">ADR-0036 · 统一 Case Viewer（Artifact Mode · Slice A）· 单一 EvidenceProjection View Model</p>
  <div class="meta-card">
    generated_at: <code>{_R._esc(meta.get('generated_at', '(unknown)'))}</code> ·
    scenarios: {meta.get('scenario_count', 0)} ·
    case_id: <code>{_R._esc(descriptor['case_id'])}</code> ·
    数据源: ADR-0034 IntegrationReport artifact（只读投影，禁 synthetic node）
  </div>
  <p class="prov-note">{_R._esc(prov_note)}</p>
  {''.join(scenario_blocks)}
  <details class="glossary">
    <summary>术语对照表（点开查看）</summary>
    <ul>
      {''.join(f'<li><code>{_R._esc(k)}</code> — {_R._esc(v)}</li>' for k, v in _R._GLOSSARY)}
    </ul>
  </details>
</div>
{replay_data_tags}
{replay_trace_data_tags}
<script>
{echarts}
</script>
<script>
{replay_js}
</script>
<script>
{media_js}
</script>
<script>
{_R._guard_script_close(replay_inits)}
</script>
<script>
{_R._guard_script_close(graph_script)}
</script>
</body>
</html>
"""


__all__ = ["render_case_viewer"]
