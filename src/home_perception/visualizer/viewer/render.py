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
from home_perception.visualizer.viewer.audio_source import resolve_audio_source
from home_perception.visualizer.viewer.case_presentation import (
    CasePresentationDescriptor,
    build_default_case_presentation,
)
from home_perception.visualizer.viewer.media_source import resolve_media_source

if TYPE_CHECKING:  # 仅类型标注
    from pathlib import Path

    from home_perception.visualizer.schema.evidence import ScenarioEvidence

# P0-1 行动闭环面板：Live WS 客户端资产文件名（render_case_viewer 内联注入）。
_LIVE_ACTIONS_FILENAME = "live_actions.js"
# P0 evidence_delta 增量投影客户端（Owner 2026-08-17 拍板）：浏览器只渲染、不推理。
_LIVE_STREAM_FILENAME = "live_stream.js"
# P1-B 叙事分幕客户端（Artifact Story Replay）：浏览器只读服务端派生分幕，不推理。
_STORY_REPLAY_FILENAME = "story_replay.js"

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
# AC-7 + Owner 2026-08-16 产品级约束：案例角标固定文案（强化既有 provenance 设计）。
# Live 卡右上角显示「● LIVE · REAL SENSOR」，Artifact 显示「● GOLDEN CASE · SIMULATED」，
# 二者仅 provenance 语义之差，便于评审一眼区分真实设备流与仿真闭环。
_PROVENANCE_BADGE = {
    "REAL_SENSOR": "● LIVE · REAL SENSOR",
    "SIMULATED": "● GOLDEN CASE · SIMULATED",
    "FIXTURE": "● FIXTURE · 固定测试素材",
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

# P1/P10（评审整改）：场景标题人话化——从 projection 已有事实派生一句"发生了什么"，
# 不新增事实（事件类型/风险/动作全部来自 scenario 字段 + 复用 renderer 翻译表）。
# 纯展示层派生：sid 等工程标识降级为 muted 脚注，首屏只留可读叙事。
def _scenario_headline(scenario: ScenarioEvidence) -> str:
    """场景一句话叙事（P1：10 秒看懂"发生了什么"）。

    从 projection 字段派生：事件类型（_EVENT_ZH）→ 风险（risk_levels 首项）→
    系统行动（command_types / recommended_actions 首项）。**不编造**：字段缺失则
    跳过对应片段；全部缺失回退场景 ID 标识。
    """
    parts: list[str] = []
    events = [e for e in scenario.get("event_types", ()) if e]
    if events:
        translated = [_R._EVENT_ZH.get(e, e) for e in events]
        parts.append("·".join(translated))
    risks = [r for r in scenario.get("risk_levels", ()) if r]
    if risks:
        parts.append(_R._translate_value(risks[0]))
    actions = [
        c for c in scenario.get("command_types", ())
        or scenario.get("recommended_actions", ()) if c
    ]
    if actions:
        parts.append(_R._translate_value(actions[0]))
    if not parts:
        return _R._esc(scenario.get("scenario_id", "unknown"))
    return " → ".join(parts)


def _render_ci_badge(descriptor: CasePresentationDescriptor) -> str:
    """P0-4.2：CI 受控生成可信徽章。

    仅当 ``descriptor.generated_by == "ci"`` 时渲染"本案例由 CI 受控生成"可信标识，
    并附 ``case_id`` 与 ``renderer_version``（与 demo/ 包 manifest 锁版本对齐）。

    确定性铁律（t1）：**不含任何墙钟 / 随机值**——全部来自 descriptor 纯展示元数据，
    同一次生成两次渲染逐字节一致。人工手动生成（generated_by="manual"，默认）不显示此徽章。
    """
    if descriptor.get("generated_by") != "ci":
        return ""
    cid = _R._esc(descriptor.get("case_id", "unknown"))
    rv = _R._esc(descriptor.get("renderer_version", ""))
    return (
        f'<div class="ci-badge">本案例由 CI 受控生成 · case_id={cid} · renderer=v{rv}'
        f'<span class="ci-badge-sub">（可信 artifact · 可溯源 · 不持媒体字节）</span></div>'
    )


def _render_provenance_banner(scenario: ScenarioEvidence) -> str:
    """AC-7：每个案例视图显式呈现 provenance_kind 及文案（一等视觉，绝默认隐藏）。

    Owner 2026-08-16：角标文案固定为「● LIVE · REAL SENSOR」（Live）/「● GOLDEN CASE ·
    SIMULATED」（Artifact），强化真实设备流与仿真闭环的区分（VM-13 6 MUST #4）。
    """
    kinds = {n["provenance_kind"] for n in scenario["timeline"]}
    if len(kinds) == 1:
        k = next(iter(kinds))
        badge = (
            f"<span class='prov-badge {_PROVENANCE_CLASS.get(k, '')}'>"
            f"{_R._esc(_PROVENANCE_BADGE.get(k, k))}</span>"
        )
    else:
        badge = (
            "<span class='prov-badge'>MIXED</span> "
            + " · ".join(_R._esc(_PROVENANCE_BADGE.get(k, k)) for k in sorted(kinds))
        )
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
    """干预派发回执卡（P1 · VM-1 派生自 scenario.intervention_dispatch；VM-9 守诚实边界）。

    展示：① 逐指令「真实派发类型 → 目标人类接收方 → 期望闭环状态」；② 闭环可达性陈述
    （干预触达**可定义的人类接收方**——家属 / 社区，可闭合到具体责任人）；③ 诚实注记——
    运行态未产出送达遥测，本回执**不声称 60s 内送达**、不含任何时延 / SLA 测量
    （AC-12 绝不编造送达或时延）。空派发 → 返回诚实空卡，不编造回执。
    """
    dispatch = scenario.get("intervention_dispatch") or ()
    if not dispatch:
        return (
            "<div class='card action-card'>"
            "<div class='card-title'>干预派发回执</div>"
            "<div class='muted'>本场景未派发任何干预指令（仅感知与记录）。</div>"
            "</div>"
        )

    rows: list[str] = []
    for d in dispatch:
        ct = _R._esc(d["command_type"])
        role = _R._esc(d["target_role"])
        closure = d.get("closure_expectation") or ""
        if closure:
            closure_html = (
                f"<span class='receipt-closure'>待确认闭环：{_R._esc(closure)}</span>"
            )
        else:
            closure_html = (
                "<span class='receipt-closure muted'>无外部接收方（仅系统记录）</span>"
            )
        rows.append(
            f"<li class='receipt-row'>"
            f"<span class='receipt-cmd'>{ct}</span>"
            f"<span class='receipt-arrow'>→</span>"
            f"<span class='receipt-role'>{role}</span>"
            f"{closure_html}</li>"
        )

    closure_rows = [d for d in dispatch if d.get("closure_expectation")]
    closure_list_html = ""
    if closure_rows:
        items = "".join(
            f"<li>{_R._esc(d['target_role'])}"
            f"（{_R._esc(d['command_type'])} → 期望闭环 "
            f"<code>{_R._esc(d['closure_expectation'])}</code>）</li>"
            for d in closure_rows
        )
        closure_list_html = (
            "<div class='receipt-closure-list'>本案例期望的闭环确认："
            f"<ul>{items}</ul></div>"
        )

    return f"""
      <div class='card action-card'>
        <div class='card-title'>干预派发回执</div>
        <ul class='receipt-list'>
          {''.join(rows)}
        </ul>
        {closure_list_html}
        <div class='receipt-reach'>闭环可达性：上述指令派发至<span class='receipt-reach-target'>可定义的人类接收方</span>（家属 / 社区），干预闭环可闭合到具体责任人。</div>
        <div class='receipt-note muted'>诚实边界：运行态未产出送达遥测（送达时间 / 接收确认 / 时延）。本回执仅表征「已派发 + 目标接收方 + 待确认闭环」，<strong>不声称 60s 内送达</strong>，亦不含任何时延 / SLA 测量。</div>
      </div>"""


# ---------------------------------------------------------------------------
# P0-2 Case Time 主轴（产品化总原则 §3 · 事件标记 + 游标）
# ---------------------------------------------------------------------------


def _render_case_time_tracks(scenario: ScenarioEvidence) -> str:
    """Case Time 主轴：证据时间轴上的事件标记（音频 Lane + 记忆 Lane，双轨视图）。

    - 数据：``scenario.case_time_tracks``（loader 投影，相对最早证据 T0；prior 历史
      不进主轴——那是背景，不是当下；VM-10 不伪造媒体对齐）；
    - 布局：统一时间轴内分 **音频 Lane（🔊，上）** 与 **记忆 Lane（🧠，下）** 两行，
      共享同一游标与回放；记忆因此自然嵌进统一时间轴（P1：Memory Timeline 嵌入
      Case Time），而非孤立面板。零新数据（VM-1：仅已有 case_time_tracks 的展示编排）；
    - 交互：点击标记 → ``window.__caseTime(sid, kind, label, time)``——移动游标 +
      联动（audio → 播放对应样本/高亮卡片；memory → 滚动记忆面板）；
    - P0-3 Evidence Replay：标记带 data-time/data-kind/data-label（JS 播放器遍历
      触发）；主轴播放按钮 → ``window.__caseTimeReplay(sid)``（证据时间自动回放：
      游标推进 + 事件按序涌现，独立于媒体时间，诚实不伪造媒体对齐）；
      所有标记仍置于同一 ``#case-time-track-{sid}``（单 track / 单 cursor /
      querySelectorAll('.case-time-mark') 契约不变），仅以 CSS 上下两行区分 Lane；
    - 无事件标记 → 返回空串（AC-12 不编造）；无音频/记忆场景零成本。
    """
    tracks = scenario.get("case_time_tracks") or ()
    if not tracks:
        return ""
    sid = scenario["scenario_id"]
    sid_html = _R._esc(sid)
    max_time = max((float(t["time"]) for t in tracks), default=0.0) or 1.0
    audio_marks: list[str] = []
    memory_marks: list[str] = []
    for t in tracks:
        time = float(t["time"])
        pct = min(time / max_time * 100.0, 100.0)
        kind = str(t["kind"])
        is_audio = kind == "audio"
        cls = "mark-audio" if is_audio else "mark-memory"
        marker = "🔊" if is_audio else "🧠"
        # 缺陷 #3 修复（Gate 4E-2 交互验收实证）：data-label 必须用 HTML 属性层转义
        # （_esc / html.escape），不能用 JS 层 _esc_js（json.dumps 产出带引号字符串，
        # 嵌入 " 定界的 HTML 属性会被提前终结 → onclick 截断 → 点击抛 SyntaxError）。
        # onclick 不内联 label（data-driven）：运行时从元素 data-label 读取，零引号冲突。
        label_attr = _R._esc(str(t["label"]))
        mark = (
            f'<span class="case-time-mark {cls}" style="left:{pct:.1f}%" '
            f'data-time="{time:.3f}" data-kind="{_R._esc(kind)}" '
            f'data-label="{label_attr}" '
            f'onclick="window.__caseTime(\'{sid_html}\',\'{_R._esc(kind)}\','
            f'this.getAttribute(\'data-label\'),{time:.3f})" '
            f'title="{time:.1f}s · {kind} · {label_attr}">{marker}</span>'
        )
        (audio_marks if is_audio else memory_marks).append(mark)
    # 双 Lane 标签：仅在该类事件存在时显示（避免空行标签噪音）。
    lane_tags = ""
    if audio_marks:
        lane_tags += '<span class="lane-tag lane-tag-audio">音频</span>'
    if memory_marks:
        lane_tags += '<span class="lane-tag lane-tag-memory">记忆</span>'
    # 所有标记置于同一 track（P0-3 回放 JS 契约），CSS 上下两行区分 Lane。
    all_marks = audio_marks + memory_marks
    return f"""
    <div class="case-time" id="case-time-{sid_html}">
      <div class="case-time-axis">
        <div class="lane-tags">{lane_tags}</div>
        <div class="case-time-track" id="case-time-track-{sid_html}" data-max="{max_time:.3f}">
          <span class="case-time-cursor" id="case-time-cursor-{sid_html}"></span>
          {''.join(all_marks)}
        </div>
      </div>
      <div class="case-time-meta">
        <button type="button" class="rp-btn case-time-play" id="case-time-play-{sid_html}"
                onclick="window.__caseTimeReplay('{sid_html}')"
                title="证据时间回放（事件按 Case Time 涌现）">▶</button>
        <span class="muted">Case Time（证据时间轴 · 0~{max_time:.1f}s）— 回放 = 事件按序涌现；媒体时间≠证据时间（VM-10）</span>
      </div>
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
    - ``<video>`` 仅当源为 ``ArtifactVideoSource`` 且含 ``video_url``（原生控件播放；
      P1 整改：加 ``autoplay muted playsinline``，10 秒内即可看到画面）；
    - 绑定文案（源类型 + ref）**降级为脚注**（方案 3），不再占主轴 C 位；
    - P11 整改：脚注的 ``source_kind`` / ``ref`` 从**已解析的 media_manifest** 读取
      （manifest 与 prepare_case_media 登记的 ``ArtifactVideoSource`` 一致），而非
      ``descriptor.media_binding`` 默认值（SyntheticFrameSource + 占位 ref，可能与
      实际媒体矛盾）；无 manifest 时才显示"无媒体绑定"；
    - 注入 ``media-manifest-{sid}`` 数据岛（manifest 的 frame_template 已叠加
      ``media_base_url``），供前端 MediaPlayer 消费。

    ``descriptor`` 参数保留：VM-11 纯展示编排的签名契约（测试直接调用本函数），
    脚注事实以 manifest 为准（P11）。
    """
    sid = scenario["scenario_id"]
    sid_html = _R._esc(sid)

    # 防御（评审 #9）：media_base_url 经协议黑名单校验（与 _safe_media_src 同契约）。
    # 正常来自 os.path.relpath（本地相对路径，无 scheme），此处仅防御畸形 / 不可控来源
    # （如 javascript: 之类伪协议），避免拼接后形成隐式 XSS 面。
    if _url_scheme(media_base_url) and _url_scheme(media_base_url) not in _ALLOWED_URL_SCHEMES:
        media_base_url = ""

    canvas_fallback = (
        f'<canvas id="case-video-canvas-{sid_html}" class="case-video-canvas" '
        f'width="640" height="360"></canvas>'
    )

    # 防御（评审 #9）：media_base_url 经协议黑名单校验（与 _safe_media_src 同契约）。
    # 正常来自 os.path.relpath（本地相对路径，无 scheme），此处仅防御畸形 / 不可控来源
    # （如 javascript: 之类伪协议），避免拼接后形成隐式 XSS 面。
    if _url_scheme(media_base_url) and _url_scheme(media_base_url) not in _ALLOWED_URL_SCHEMES:
        media_base_url = ""

    canvas_fallback = (
        f'<canvas id="case-video-canvas-{sid_html}" class="case-video-canvas" '
        f'width="640" height="360"></canvas>'
    )

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
        # 路径穿越防护（评审 #5，fail-closed）：media_base_url / video_url 不得含 ".."。
        if ".." in media_base_url or ".." in raw_vurl:
            media_area = canvas_fallback
        else:
            vurl = raw_vurl
            if media_base_url and not _url_scheme(raw_vurl):
                vurl = media_base_url.rstrip("/") + "/" + raw_vurl.lstrip("/")
            safe_src = _safe_media_src(vurl)
            media_area = (
                # P5（评审整改）：给 <video> 加场景专属 id，media.js 据此桥接
                # timeupdate → Evidence Timeline 定位（真实视频播放驱动证据同步）。
                # P1（autoplay）：加 autoplay muted playsinline——10 秒内即可看到画面，
                # muted+playsinline 保证移动端/自动播放策略不拦截。
                f'<video class="case-video-el" id="case-video-el-{sid_html}" '
                f'controls autoplay muted playsinline preload="metadata" '
                f'src="{_R._esc(safe_src)}"></video>'
                if safe_src
                else canvas_fallback
            )
    else:
        media_area = canvas_fallback

    # 绑定文案降级为脚注（方案 3）：不再占主轴 C 位。
    # 媒体缺失（Media Source Adapter 未解析到 manifest）→ 不展示孤儿 ref，标注"无媒体绑定"
    # （评审 R1-#4：ref 只在确有媒体资产时才对应真实绑定，否则误导）。
    if media_manifest is None:
        binding_footnote = (
            '<p class="muted case-video-binding">无媒体绑定（Media Source Adapter 未解析到媒体资产；'
            '控制条仍可驱动纯 UI 进度与 Evidence Timeline）</p>'
        )
    else:
        # P11 整改：source_kind/ref 从已解析的 media_manifest 读取（真实值），而非
        # descriptor 默认 binding——prepare_case_media 只写了 manifest、未更新
        # descriptor.media_binding，若读 descriptor 会显示 SyntheticFrameSource/
        # 占位 ref，与实际 ArtifactVideoSource 矛盾。ref 取 manifest 的真实媒体定位
        # （ArtifactVideoSource→video_url；SyntheticFrameSource→frame_template；
        # 字节仍由 Adapter 经 ref 解析，不进 View Model）。
        src_kind = _R._esc(str(media_manifest.get("source_kind", "")))
        ref = _R._esc(
            str(
                media_manifest.get("video_url")
                or media_manifest.get("frame_template")
                or ""
            )
        )
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

    case_time = _render_case_time_tracks(scenario)

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

    # P1-A：Live 实时感知状态容器（仅 live 模式注入；由 live_stream.js 经 WS perception_delta
    # 填充。浏览器只渲染服务端投影的结构化检测子集，零推理——VM-1/VM-9）。
    live_perception = ""
    if descriptor.get("live_ws_path"):
        live_perception = (
            f'<div class="live-perception" id="live-perception-{sid_html}" '
            f'data-scenario="{sid_html}"></div>'
        )

    return f"""
    <section class="fs-panel" id="fs-case-video-{sid_html}">
      <h3 class="view-anchor">Case Video（主轴）</h3>
      <div class="case-video">{media_area}{live_perception}{media_timeline}{case_time}{binding_footnote}</div>
      {manifest_island}
      <p class="muted">Case Video 叙事结构：{' → '.join(_CASE_VIDEO_NARRATIVE)}（VM-12 · 产品主视频，关联叙事而非分析回放）</p>
    </section>"""


# ---------------------------------------------------------------------------
# P1 Acoustic State（telephone_risk · 声学状态变化，非诈骗判定 · VM-1/VM-9）
# ---------------------------------------------------------------------------


def _render_acoustic_state_card(scenario: ScenarioEvidence) -> str:
    """声学状态变化叙事卡（P0-1 Acoustic State · telephone_risk · VM-1/VM-9 合规）。

    数据来源（全部来自既有 audio_evidence 投影字段，VM-1 纯派生）：
    - kind（含 ``audio_telephone_persistent`` 触发本卡）、labels、related_visual_ref；
    - P0-1 新增声学状态字段（golden telephone_risk 声明式声学状态机透传，NotRequired）：
      ``acoustic_state_change``（状态机）、``voice_stress_score``、``f0_delta`` /
      ``speech_rate_delta`` / ``energy_delta``。字段缺失即不展示（AC-12 绝不编造）。

    绝不推导 STRESS / 诈骗 / 当事人心理（VM-9 无 ASR/LLM）：``voice_stress_score`` 仅作为
    可观测声学指标呈现，状态机标签（NORMAL→…→STRESS）是**声学状态**枚举，非心理/语义判定。

    触发：``audio_evidence`` 含 telephone 类（``audio_telephone_persistent``）才渲染；
    非电话场景不注入（VM-11 不新增无关事实）。无音频证据 → 返回空串（AC-12）。
    """
    audio = scenario.get("audio_evidence") or ()
    if not audio:
        return ""  # AC-12：无音频证据不渲染声学状态卡
    kinds = {str(a.get("kind", "")) for a in audio}
    if "audio_telephone_persistent" not in kinds:
        return ""  # 非电话场景：音频感知卡已陈述事实，不注入额外声学状态叙事

    # --- 真实声学状态信号（P0-1：从既有投影字段纯派生，缺失即不展示）---
    state_changes = [
        a["acoustic_state_change"] for a in audio if a.get("acoustic_state_change")
    ]
    voice_stress = [
        a["voice_stress_score"]
        for a in audio
        if isinstance(a.get("voice_stress_score"), (int, float))
    ]
    delta_items: list[tuple[str, float]] = []
    for a in audio:
        for label, key in (
            ("F0", "f0_delta"),
            ("语速", "speech_rate_delta"),
            ("能量", "energy_delta"),
        ):
            v = a.get(key)
            if isinstance(v, (int, float)):
                delta_items.append((label, float(v)))

    # 声学状态是否真发生变化（状态机含多相跃迁）→ 驱动「变化」叙事；旧式 kind 偏离兜底。
    def _is_change(s: str) -> bool:
        return "->" in s or "\u2192" in s

    changed = any(_is_change(s) for s in state_changes)
    kind_dev = any(k in ("audio_voice_raised", "audio_speech_rapid") for k in kinds)
    has_deviation = bool(state_changes) or bool(voice_stress) or bool(delta_items) or kind_dev
    stable = (
        bool(state_changes)
        and not changed
        and not kind_dev
        and not voice_stress
        and not delta_items
    )

    # 真实跨模态关联：任一音频节点携带 related_visual_ref → 视觉 + 音频相互支持。
    cross_modal = any(a.get("related_visual_ref") for a in audio)
    # 决策取向（来自推荐动作，不新增事实）：升级/社区任务 → 提高关注；否则持续观察。
    recs = scenario.get("recommended_actions") or ()
    cmds = scenario.get("command_types") or ()
    if any(r == "ESCALATE_COMMUNITY" for r in recs) or any(
        c == "CREATE_COMMUNITY_TASK" for c in cmds
    ):
        decision = "提高关注 / 升级社区协同处置"
    else:
        decision = "持续观察"

    # 导语：优先陈述真实声学状态机；其次旧式偏离；稳定态单列；最次无偏离。
    state_html = (
        _R._esc(" \u2192 ".join(s.replace("->", "\u2192") for s in state_changes))
        if state_changes
        else ""
    )
    if stable:
        lead_html = _R._esc(
            "通话进行中，系统持续听到电话声音；声学状态稳定（未检测到状态跃迁）。"
            "这是声学状态的事实记录。"
        )
    elif changed or state_changes:
        lead_html = (
            _R._esc("通话进行中，系统持续听到电话声音；声学状态随通话推进发生变化（")
            + state_html
            + _R._esc("）。这是声学状态变化的事实记录。")
        )
    elif has_deviation:
        lead_html = _R._esc(
            "通话进行中，系统持续听到电话声音，并检测到声学偏离"
            "（音高 / 能量等可观测信号变化）。这是声学状态变化的事实记录。"
        )
    else:
        lead_html = _R._esc(
            "通话进行中，系统持续听到电话声音。这是声学状态变化的事实记录。"
        )

    # 量化指标行（仅当真实声学字段存在时呈现，杜绝硬编码「音高/能量等」）。
    metrics_html = ""
    if voice_stress or delta_items:
        parts: list[str] = []
        if voice_stress:
            parts.append(f"voice_stress_score = {float(voice_stress[0]):.2f}")
        for label, val in delta_items:
            parts.append(f"{label} \u0394={val:.2f}")
        metrics_html = (
            '<p class="acoustic-state-note">可观测声学指标（仅描述信号，不做语义判定）：'
            + _R._esc("；".join(parts))
            + "。</p>"
        )

    cross_html = ""
    if cross_modal:
        cross_html = (
            '<p class="acoustic-state-note">视觉通话交互 + 音频声学偏离 相互支持'
            "（CROSS_MODAL: SUPPORTS）→ 决策：" + _R._esc(decision) + "。</p>"
        )
    disclaimer = (
        "声学状态变化仅描述可观测信号；系统不调用 ASR / LLM，"
        "不推导当事人心理或诈骗判定（VM-9）。"
    )
    return f"""
    <div class="acoustic-state">
      <div class="acoustic-state-head">声学状态变化（非诈骗判定）</div>
      <p class="acoustic-state-lead">{lead_html}</p>
      {metrics_html}
      {cross_html}
      <p class="acoustic-state-note muted">{_R._esc(disclaimer)}</p>
    </div>"""


# ---------------------------------------------------------------------------
# 音频感知首屏面板（音频 E2E P0：让用户真正理解"系统听到了什么"）
# ---------------------------------------------------------------------------


def _render_audio_perception(
    scenario: ScenarioEvidence,
    audio_manifest: dict | None,
    audio_base_url: str,
) -> str:
    """音频感知首屏面板分发器（Gate 3 产品拍板 2026-08-16）。

    - Live（``mode == "live"``，provenance=REAL_SENSOR）→ 渲染**实时摘要**
      （"此刻传感器检测到什么"），完整 event / provenance / 原始技术细节留在 details 区；
    - Artifact（SIMULATED）→ 渲染完整"系统听到了什么"卡片（既有 P0 行为，不变）。

    两者均遵守：``audio_evidence`` 空 → 返回空串（AC-12 绝不编造面板）；
    证据与媒体严格分离（可播放样本仅在 details 区由独立 Audio Source Adapter 绑定呈现）。
    """
    audio = scenario.get("audio_evidence") or ()
    if not audio:
        return ""  # AC-12：无音频证据不渲染首屏面板
    if str(scenario.get("mode", "")) == "live":
        return _render_live_audio_summary(scenario, audio_manifest, audio_base_url)
    return _render_audio_perception_full(scenario, audio_manifest, audio_base_url)


def _render_live_audio_summary(
    scenario: ScenarioEvidence,
    audio_manifest: dict | None,  # 与 full 同签名；manifest 仅 details 区消费
    audio_base_url: str,
) -> str:
    """Live 首屏实时音频摘要。

    只陈述"此刻系统检测到了哪些声学事件 + 时间跨度 + 真实传感器来源"，不展开逐条技术字段
    （score / confidence / labels / provenance 由 details 区 ``_render_audio_evidence`` 承载）。

    AC-12 / 6 MUST fail-closed：只读 ``audio_evidence`` 事实字段，绝不渲染 url / 媒体字节；
    来源标注来自节点 ``provenance_kind``（Live=REAL_SENSOR），此处仅作展示，不跨模态补语义、
    不伪造事件。
    """
    audio = scenario.get("audio_evidence") or ()
    if not audio:
        return ""  # AC-12
    t0 = min(float(a.get("timestamp", 0.0)) for a in audio)
    tmax = max(float(a.get("timestamp", 0.0)) for a in audio)
    # 检测到的声学类别（去重保序）。
    seen: list[str] = []
    for a in audio:
        k = str(a.get("kind", ""))
        if k and k not in seen:
            seen.append(k)
    items = "".join(
        f'<li><span class="audio-marker">{_R._MODALITY_MARKER["AUDIO"]}</span>'
        f'<span class="detected-kind">{_R._esc(_R._translate_audio_kind(k))}</span></li>'
        for k in seen
    )
    span = f"{t0 - t0:.1f} — {tmax - t0:.1f}s"
    return f"""
    <section class="fs-panel" id="fs-audio-{_R._esc(scenario['scenario_id'])}">
      <h3 class="view-anchor">现在系统听到了什么（实时音频感知）</h3>
      <div class="audio-perception live-audio-summary">
        <ul class="detected-list">{items}</ul>
        <div class="live-audio-meta muted">
          <span class="live-audio-span">时间跨度 {_R._esc(span)}</span>
          <span class="live-audio-count">· 事件 {len(audio)}</span>
          <span class="audio-source">Source · REAL SENSOR</span>
        </div>
      </div>
      <p class="muted">实时摘要；事件明细、provenance 与原始技术字段见下方「详细证据」。</p>
    </section>"""


def _render_audio_perception_full(
    scenario: ScenarioEvidence,
    audio_manifest: dict | None,
    audio_base_url: str,
) -> str:
    """音频感知首屏完整卡片（Artifact / Golden Case，provenance=SIMULATED；既有 P0 行为不变）。

    无音频证据（``audio_evidence`` 空）→ 返回空串（AC-12：绝不编造面板）。

    人话化卡片（相对时间 + 中文类别 + score/confidence），形如：
      ``22.4s 🔊 持续电话声音  score 0.90 · confidence 0.92``

    **证据与媒体严格分离**（VM-9 / VM-10 / AC-11）：
    - ``audio_evidence`` 不含任何 url / 媒体字节（loader 投影保证）→ 只渲染事实字段；
    - 可播放样本**仅当** ``audio_manifest``（独立 Audio Source Adapter 绑定）命中该 kind
      时才渲染 ``<audio controls>``；无绑定则不渲染、不编造（诚实降级）。
    """
    audio = scenario.get("audio_evidence") or ()
    if not audio:
        return ""  # AC-12：无音频证据不渲染首屏面板

    # 相对时间：以该场景最早音频时间戳为 T0（媒体时间 ≠ 证据时间，VM-10；此处用证据相对时间）。
    t0 = min(float(a.get("timestamp", 0.0)) for a in audio)
    cards: list[str] = []
    for a in audio:
        ts = float(a.get("timestamp", 0.0))
        rel = ts - t0
        kind = str(a.get("kind", ""))
        kind_zh = _R._translate_audio_kind(kind)  # 含「中文（原始枚举）」
        score = float(a.get("score") or 0.0)
        conf = float(a.get("confidence") or 0.0)
        labels = " · ".join(_R._esc(str(v)) for v in (a.get("labels") or ()))
        # 可播放样本：仅当独立音频绑定命中该 kind（严格分离，绝不读 audio_evidence 内 url）。
        play_ctrl = ""
        audio_el_id = ""
        if audio_manifest:
            rel_url = audio_manifest.get("files", {}).get(kind)
            if rel_url:
                # 相对 url 叠加 audio_base_url（与媒体同契约）。
                url = rel_url
                if audio_base_url and not _url_scheme(rel_url):
                    url = audio_base_url.rstrip("/") + "/" + rel_url.lstrip("/")
                # 路径穿越防护（评审 #5 风格）：组合 URL 含 ".." 则不渲染播放控件
                # （诚实降级，绝不拼接可能越界的样本 URL）。
                if ".." not in url:
                    safe = _safe_media_src(url)
                    if safe:
                        # P0-3：<audio> 带确定性 id（audio-<kind>）+ data-kind ——
                        # Evidence Timeline 音频节点（data-kind）点击 → JS 播放对应样本轨。
                        audio_el_id = f"audio-{_R._esc(kind)}"
                        play_ctrl = (
                            f'<div class="audio-play">'
                            f'<audio id="{audio_el_id}" data-kind="{_R._esc(kind)}" '
                            f'controls preload="none" src="{_R._esc(safe)}"></audio>'
                            f'<span class="muted">样本声音（合成素材，非原始录音）</span></div>'
                        )
        cards.append(
            f"""
            <div class="audio-card" data-kind="{_R._esc(kind)}">
              <div class="audio-card-head">
                <span class="tl-step">{rel:.1f}s</span>
                <span class="audio-marker">{_R._MODALITY_MARKER["AUDIO"]}</span>
                <span class="audio-kind">{_R._esc(kind_zh)}</span>
              </div>
              <div class="audio-meta muted">
                score {score:.2f} · confidence {conf:.2f}
                {(" · " + labels) if labels else ""}
              </div>
              {play_ctrl}
            </div>"""
        )
    acoustic_state = _render_acoustic_state_card(scenario)
    return f"""
    <section class="fs-panel" id="fs-audio-{_R._esc(scenario['scenario_id'])}">
      <h3 class="view-anchor">系统听到了什么（音频感知）</h3>
      {acoustic_state}
      <div class="audio-perception">{''.join(cards)}</div>
      <p class="muted">音频为感知层证据（kind/score/confidence），非语义判定；样本声音为合成素材，仅供示意。</p>
    </section>"""


# ---------------------------------------------------------------------------
# G0-3/G0-2 记忆时间线面板（历史 Episodes · 决策引用可证）
# ---------------------------------------------------------------------------


def _render_memory_timeline(scenario: ScenarioEvidence) -> str:
    """记忆时间线（repeated_visit 等历史记忆场景 · VM-1 只投影不生成）。

    - 数据：``scenario.memory_episodes``（loader 从 canonical memory_episodes 投影，
      prior=历史预置 / 本次会话=运行期落库；AC-12 无明细恒 ()）；
    - 呈现：每 episode 卡片（record_id / prior 标记 / timestamp / summary / 风险 /
      建议 / reason_summary）+ 决策引用脚注（Decision Trace historical_record_ids 可证）；
    - 无 ASR / LLM / 判定（VM-9）：只展示事实。
    """
    mem = scenario.get("memory_episodes") or ()
    if not mem:
        return ""
    sid_html = _R._esc(scenario["scenario_id"])
    cards: list[str] = []
    for ep in mem:
        ep_id = _R._esc(ep["record_id"])
        tag = "历史预置" if ep["prior"] else "本次会话"
        risk = _R._translate_value(ep.get("risk_level", "")) or "—"
        action = _R._translate_value(ep.get("recommended_action", "")) or "—"
        summary = _R._esc(ep.get("summary", ""))
        reasons = "、".join(
            _R._translate_value(r) for r in (ep.get("reason_summary") or ())
        ) or "—"
        cards.append(
            f"""
        <div class="mem-ep{' mem-ep-prior' if ep['prior'] else ''}">
          <div class="mem-ep-head">
            <span class="mem-ep-id">{ep_id}</span>
            <span class="mem-ep-tag">{tag}</span>
            <span class="mem-ep-time">{_R._esc(ep['timestamp'])}</span>
          </div>
          <div class="mem-ep-body">{summary} · 风险 {risk} · 建议 {action}</div>
          <div class="mem-ep-reasons">依据：{reasons}</div>
        </div>"""
        )
    return f"""
    <section class="fs-panel" id="fs-memory-timeline-{sid_html}">
      <h3 class="view-anchor">记忆时间线（历史 Episodes · 决策引用可证）</h3>
      <div class="mem-timeline">
        {''.join(cards)}
      </div>
      <p class="muted">历史 Episode 来自 canonical memory_episodes 事实投影（prior=历史预置 / 本次会话=运行期落库）；当前决策是否引用了历史由 Decision Trace.historical_record_ids 可证。</p>
    </section>"""


# ---------------------------------------------------------------------------
# P0-1 行动闭环面板（人类处置闭环 · Live 专属交互）
# ---------------------------------------------------------------------------


def _live_actions_inline() -> str:
    """内联 P0-1 Live WS 客户端（缺失时降级为空串——面板静态可读、无交互，不崩）。"""
    p = _R._ASSETS_DIR / _LIVE_ACTIONS_FILENAME
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def _live_stream_inline() -> str:
    """内联 P0 evidence_delta 增量投影客户端（缺失时降级为空串——页面保持快照模式，不崩）。

    仅 Live 模式注入（descriptor 带 live_ws_path）；Artifact/旗舰模式不注入（无实时流语义）。
    """
    p = _R._ASSETS_DIR / _LIVE_STREAM_FILENAME
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def _story_replay_inline() -> str:
    """内联 P1-B 叙事分幕客户端（缺失时降级为空串——章节导航静态可读、无聚焦，不崩）。

    仅 Artifact 模式注入（descriptor 无 live_ws_path）；Live 模式不注入（实时流无完整故事）。
    """
    p = _R._ASSETS_DIR / _STORY_REPLAY_FILENAME
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def _render_action_closure(
    scenario: ScenarioEvidence,
    descriptor: CasePresentationDescriptor,
) -> str:
    """P0-1 人类处置闭环面板（Live 专属交互；Artifact 模式不渲染此面板）。

    边界（P0-1 设计铁律）：
    - 按钮 / 状态徽章是 **UI / Workflow 态**（VM-11：不进 CasePresentationDescriptor 事实字段，
      不进 EvidenceProjection）——由 ``live_actions.js`` 经 WS snapshot/state_update 驱动；
    - 「完成处置」的 Resolution **事实**由后端 state.py 状态机 → ProjectionAccumulator 投影为
      Evidence Timeline 的 ACTION 节点（只读证据），前端绝不宣布行动成功；
    - 无 ASR / LLM（VM-9 不变）。
    """
    sid = scenario["scenario_id"]
    sid_html = _R._esc(sid)
    ws_path = _R._esc(str(descriptor.get("live_ws_path", "/ws")))
    return f"""
    <section class="fs-panel" id="fs-action-closure-{sid_html}">
      <h3 class="view-anchor">行动闭环（家属 / 社区协同处置）</h3>
      <div class="closure-panel" id="closure-{sid_html}" data-ws-path="{ws_path}" data-scenario="{sid_html}">
        <div class="closure-warning">暂无待处置警告</div>
        <div class="closure-grid">
          <div class="closure-role">
            <div class="closure-role-title">家属端</div>
            <div class="closure-status" id="closure-family-status-{sid_html}">—</div>
            <div class="closure-actions">
              <button class="rp-btn closure-btn" data-operator="family" data-action="acknowledge" id="closure-family-ack-{sid_html}">我知道了</button>
              <button class="rp-btn closure-btn" data-operator="family" data-action="notify_community" id="closure-family-notify-{sid_html}">通知社区</button>
            </div>
          </div>
          <div class="closure-role">
            <div class="closure-role-title">社区端</div>
            <div class="closure-status" id="closure-community-status-{sid_html}">—</div>
            <div class="closure-actions">
              <button class="rp-btn closure-btn" data-operator="community" data-action="accept" id="closure-community-accept-{sid_html}">接受任务</button>
              <button class="rp-btn closure-btn" data-operator="community" data-action="complete" id="closure-community-complete-{sid_html}">完成处置</button>
            </div>
          </div>
        </div>
      </div>
      <p class="muted">按钮与状态为实时工作流态（UI/Workflow，不进 EvidenceProjection）；「完成处置」后产生的 Resolution 事实由后端投影为 Evidence Timeline 的 ACTION 节点（只读证据）。</p>
    </section>"""


# ---------------------------------------------------------------------------
# 单场景组装（首屏叙事 + 折叠详细证据）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P0-1 Case Header（一级架构元素 · 产品化总原则 §2）
# ---------------------------------------------------------------------------


def _render_case_header(scenario: ScenarioEvidence) -> str:
    """Case Header：命题一句话 + ▶ Play Case（产品化总原则一级架构元素）。

    - ``This case demonstrates:`` 来自场景声明 ``product_question``（VM-1 只投影既有
      声明；缺省空 → 不渲染命题行，仅 case 名，向后兼容）；
    - ``▶ Play Case``：滚动到 Case Video 面板 + 触发 MediaPlayer 播放（无播放器
      no-op 降级）；
    - 技术噪音（sid/fingerprint/gate）不在 Header——退 Audit Details（§1 信息层级）。
    """
    sid = scenario["scenario_id"]
    sid_html = _R._esc(sid)
    prop = scenario.get("product_question") or ""
    prop_html = ""
    if prop:
        prop_html = (
            f'<p class="case-header-prop">This case demonstrates: '
            f'{_R._esc(prop)}</p>'
        )
    return f"""
    <header class="case-header">
      <div class="case-header-main">
        <span class="case-header-title">{_R._esc(sid.upper())}</span>
        {prop_html}
      </div>
      <button type="button" class="case-play" onclick="window.__playCase('{sid_html}')">▶ Play Case</button>
    </header>"""


# P0-4：负向能力声明 → 人类可读标签（展示层纯映射，无业务逻辑；VM-1）。
_SUPPRESS_REASON_LABELS: dict[str, str] = {
    "no_trigger_events": "无触发事件：系统观测到正常环境，没有理由生成风险事件（真阴性 TN）",
    "all_suppressed_normal": "全部证据指向常规行为：已判定为正常模式并保持克制（未误报）",
    "unroutable_event_type": "事件类型不可路由：观测到但无对应处理通道，已安全忽略",
}


def _render_suppression_reason(scenario: ScenarioEvidence) -> str:
    """Suppression Reason Card（P0-4 负向能力卡）：解释"系统为什么没报警"。

    仅当场景声明了 suppress_reasons（benign / ambiguous 等负向能力场景）时渲染；
    空元组 → 返回空串（不渲染卡片，VM-1 不伪造）。数据来自场景 meta 诚实声明，
    非运行时检测后抑制。这是差异化最强的产品能力——把"没报警"从默认误读为
    "漏报"，扭转成"系统主动说明为何保持沉默"。

    - 标题："为什么没有报警"
    - 每条 reason：枚举值 → 人类可读标签（未知值回退为原值，不编造）
    """
    reasons = scenario.get("suppress_reasons") or ()
    if not reasons:
        return ""
    items: list[str] = []
    for r in reasons:
        label = _SUPPRESS_REASON_LABELS.get(r, r)
        items.append(
            f'<li><span class="suppress-tag">{_R._esc(r)}</span>'
            f'<span class="suppress-text">{_R._esc(label)}</span></li>'
        )
    return f"""
    <div class="suppress-reason">
      <div class="suppress-head">为什么没有报警</div>
      <ul class="suppress-list">{''.join(items)}</ul>
    </div>"""


# P1-1 场景差异化叙事：结果自适应一句话"叙事带"（产品化总原则 §0：开始删除信息、
# 用一句结果类型声明替代散落工程细节）。纯 VM-1 投影——完全依赖 ScenarioEvidence 既有
# 字段（recommended_actions / command_types / suppress_reasons），零新数据生成。
# 派生优先级（信号最强者主导）：
#   1. suppress_reasons 非空 → suppressed（负向能力：系统有意保持沉默，真阴性 TN）
#   2. ESCALATE_COMMUNITY / CREATE_COMMUNITY_TASK → high_risk（升级处置）
#   3. NOTIFY_FAMILY / SEND_FAMILY_MESSAGE → repeated_visit（记忆驱动通知家属）
#   4. MONITOR（recommended） → monitor（持续观察，未达升级阈值）
#   5. 其余 → none（不渲染任何带，VM-1 不编造）
_NARRATIVE_KIND_LABELS: dict[str, tuple[str, str]] = {
    "suppressed": (
        "未触发风险（真阴性）",
        "系统观测到正常环境，主动保持沉默——这是有意的负向能力，而非漏报。",
    ),
    "high_risk": ("高风险处置", "系统识别高风险并升级至社区协同处置。"),
    "repeated_visit": ("记忆驱动升级", "系统结合历史记忆重新评估风险，并通知家属。"),
    "monitor": ("持续观察", "系统持续观察，当前未达升级阈值。"),
}

_ESCALATE_ACTIONS = ("ESCALATE_COMMUNITY",)
_ESCALATE_COMMANDS = ("CREATE_COMMUNITY_TASK",)
_NOTIFY_ACTIONS = ("NOTIFY_FAMILY",)
_NOTIFY_COMMANDS = ("SEND_FAMILY_MESSAGE",)
_MONITOR_ACTIONS = ("MONITOR",)


def _derive_narrative_kind(scenario: ScenarioEvidence) -> str | None:
    """从既有字段派生主导结果类型（叙事带种类）；无法归类返回 None（不渲染带）。

    纯展示层派生（VM-1）：不读任何 runtime/检测字段，只用场景声明的事实投影。
    优先级：suppressed > high_risk > repeated_visit > monitor > none。
    """
    if scenario.get("suppress_reasons"):
        return "suppressed"
    recs = scenario.get("recommended_actions") or ()
    cmds = scenario.get("command_types") or ()
    if any(a in _ESCALATE_ACTIONS for a in recs) or any(
        c in _ESCALATE_COMMANDS for c in cmds
    ):
        return "high_risk"
    if any(a in _NOTIFY_ACTIONS for a in recs) or any(
        c in _NOTIFY_COMMANDS for c in cmds
    ):
        return "repeated_visit"
    if any(a in _MONITOR_ACTIONS for a in recs):
        return "monitor"
    return None


def _render_narrative_band(scenario: ScenarioEvidence) -> str:
    """叙事带：场景结果类型的一句话 hero 声明（P1-1 差异化叙事）。

    置于 Case Header 之后、Suppression 卡之前，作为首屏"这是一起什么性质的案例"的
    最高层级答案。仅用既有字段派生（VM-1）；kind=None → 返回空串（不渲染，VM-1 不编造）。
    与 Suppression 卡分工：带=结果类型一行总览，卡=为何沉默的明细（互补不重复）。
    """
    kind = _derive_narrative_kind(scenario)
    if kind is None:
        return ""
    label, text = _NARRATIVE_KIND_LABELS[kind]
    return f"""
    <div class="narrative-band sev-{kind}">
      <span class="nb-kind">{_R._esc(label)}</span>
      <span class="nb-text">{_R._esc(text)}</span>
    </div>"""


def _render_story_nav(scenario: ScenarioEvidence) -> str:
    """P1-B 叙事分幕导航（Artifact-only Story Replay）。

    从 EvidenceProjection 派生分幕（``build_story_chapters``，事实驱动、省略空幕），渲染
    章节按钮 + 当前幕叙述文案。按钮自带 ``data-start/end/refs/copy``（服务端派生），
    前端 ``story_replay.js`` 只读这些属性做点击聚焦（``__Replay.seek`` + 高亮 focus refs），
    **绝不自己生成"这一幕代表风险升级"**（VM-1 / VM-9）。无叙事（空幕）→ 空串。
    """
    from home_perception.visualizer.viewer.story_chapters import build_story_chapters

    chapters = build_story_chapters(scenario)
    if not chapters:
        return ""
    sid = scenario["scenario_id"]
    sid_html = _R._esc(sid)
    buttons: list[str] = []
    for ch in chapters:
        refs = "|".join(_R._esc(str(r)) for r in ch.get("focus_refs", ()))
        buttons.append(
            f'<button type="button" class="story-chapter" '
            f'data-start="{ch["start_idx"]}" data-end="{ch["end_idx"]}" '
            f'data-refs="{refs}" data-copy="{_R._esc(ch["display_copy"])}">'
            f'{_R._esc(ch["label"])}</button>'
        )
    first_copy = _R._esc(chapters[0]["display_copy"])
    return f"""
    <div class="story-nav">
      <div class="story-chapters" id="story-chapters-{sid_html}" data-scenario="{sid_html}">
        {''.join(buttons)}
      </div>
      <div class="story-copy muted" id="story-copy-{sid_html}">{first_copy}</div>
    </div>"""


def _render_scenario_case(
    scenario: ScenarioEvidence,
    descriptor: CasePresentationDescriptor,
    panels: tuple[str, ...],
    media_manifest: dict | None,
    media_base_url: str,
    audio_manifest: dict | None = None,
    audio_base_url: str = "",
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
    # duration 优先取真实媒体 manifest.duration_sec（P5：真实视频按自身时长驱动 Evidence 映射，
    # 避免 descriptor 默认 60s 与 3.75s 真实视频错位）；无媒体时回退 descriptor 纯 UI 进度。
    # fps 留 0 → MediaPlayer 回退到 manifest.fps（有媒体时）。
    if media_manifest and media_manifest.get("duration_sec"):
        duration = float(media_manifest["duration_sec"])
    else:
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
                f'<h3 class="view-anchor">为什么值得关注（点击卡片 / 播放可重放推理链）</h3>'
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
        elif p == "audio_perception":
            panel_html.append(
                _render_audio_perception(scenario, audio_manifest, audio_base_url)
            )
        elif p == "action_closure":
            # P0-1：人类处置闭环面板（Live 专属；Artifact 模式面板列表不含此项 → 不渲染）。
            panel_html.append(_render_action_closure(scenario, descriptor))
        elif p == "memory_timeline":
            # G0-3/G0-2：记忆时间线（历史记忆场景，descriptor 注入；无明细返回空串）。
            panel_html.append(_render_memory_timeline(scenario))
        # 未知面板名静默忽略（前向兼容，不崩）

    # 详细证据（二级视图，折叠，不在首屏同屏，AC-16）
    # P1（首屏清理）：场景技术信息（sid / mode / frames）并入底部详细证据区，
    # 首屏只留叙事标题（_scenario_headline），不再出现工程噪音行。
    details = f"""
      <section class="fs-panel" id="fs-details-{sid_html}">
        <details>
          <summary>详细证据（音频 / Graph / Fingerprint / Gate）</summary>
          <p class="muted">场景标识：<code>{sid_html}</code> · mode={_R._esc(scenario['mode'])} · frames={scenario['n_frames']}</p>
          {_R._render_audio_evidence(scenario)}
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
        {_scenario_headline(scenario)}
      </h2>
      {_render_case_header(scenario)}
      {_render_narrative_band(scenario)}
      {_render_story_nav(scenario)}
      {_render_suppression_reason(scenario)}
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
    audio_base_dir: str | Path | None = None,
    audio_base_url: str = "",
    live_video_manifest: dict | None = None,
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
        audio_base_dir: artifact 根目录（内含 ``{sid}/audio/``）。提供则经 Audio Source
            Adapter 只读解析每场景可播放音频样本 manifest（音频 E2E）；``None`` → 无绑定
            音频样本（首屏音频面板只显示证据事实，不渲染播放控件，严格分离不编造）。
        audio_base_url: 从 HTML 到 ``audio_base_dir`` 的相对 URL 前缀；默认 ``""``。
        live_video_manifest: P1-C1 · Live 源视频 manifest（Host 层注入）。非 ``None`` 时
            每个场景统一用此 manifest（``source_kind=ArtifactVideoSource`` + ``video_url``
            指向网关伺服端点），使 ``<video>`` 播放源 mp4 替代 canvas 黑屏；浏览器自解码，
            非 JPEG over WS（不破单一事实源，VM-1/VM-9）。``None`` → 走 media_base_dir
            只读解析（Artifact/旗舰路径）。

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
    if live_video_manifest is not None:
        # P1-C1：Live 源视频 manifest（Host 层注入，video_url 指向 /media 伺服端点），
        # 让 <video> 播放源 mp4（替代 canvas 黑屏）；浏览器自解码，非 JPEG over WS。
        for s in scenarios:
            media_manifests[s["scenario_id"]] = live_video_manifest
    elif media_base_dir is not None:
        for s in scenarios:
            media_manifests[s["scenario_id"]] = resolve_media_source(
                media_base_dir, s["scenario_id"], mb["source_kind"]
            )

    # 音频 E2E：经 Audio Source Adapter 只读解析每场景可播放样本 manifest（与媒体严格分离）。
    # audio_base_dir 与 media_base_dir 通常同为 artifacts 根（音频/媒体都挂在 {sid}/ 下）。
    audio_manifests: dict[str, dict | None] = {}
    if audio_base_dir is not None:
        for s in scenarios:
            audio_manifests[s["scenario_id"]] = resolve_audio_source(
                audio_base_dir, s["scenario_id"]
            )

    scenario_blocks: list[str] = []
    graph_blocks: list[str] = []
    for s in scenarios:
        html_block, js_block = _render_scenario_case(
            s,
            descriptor,
            panels,
            media_manifests.get(s["scenario_id"]),
            media_base_url,
            audio_manifests.get(s["scenario_id"]),
            audio_base_url,
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

    # P0-4.2：CI 受控生成可信徽章（确定性，无墙钟）。
    ci_badge = _render_ci_badge(descriptor)

    replay_data_tags, replay_trace_data_tags, replay_inits = _build_replay_wiring(scenarios)

    echarts = _R._echarts_inline()
    replay_js = _R._replay_inline()
    media_js = _R._media_inline()
    # P0-1：行动闭环面板存在（Live 模式）时注入 Live WS 客户端；Artifact 模式无此面板 → 不注入。
    live_actions_js = _live_actions_inline() if "action_closure" in panels else ""
    # P0 evidence_delta 增量投影：仅 Live 模式（descriptor 带 live_ws_path）注入；
    # Artifact/旗舰模式无实时流语义 → 不注入（快照模式零成本）。
    live_stream_js = _live_stream_inline() if descriptor.get("live_ws_path") else ""
    # P1-B 叙事分幕客户端：仅 Artifact 模式（无 live_ws_path）注入；Live 不注入
    # （实时流无完整故事，两个时间语义不混）。无分幕场景 bind 时 no-op 零成本。
    story_replay_js = _story_replay_inline() if not descriptor.get("live_ws_path") else ""
    # P0-3：任一场景有真实音频证据时注入 AudioSync（音频轨 ↔ 证据时间线联动）；
    # 无音频场景 → 不注入（零成本降级）。audio_perception 面板在默认面板列表恒存在，
    # 但无 audio_evidence 时面板渲染为空串——以证据为准，避免空页面带无用引擎。
    has_audio_evidence = any(
        sc.get("audio_evidence") for sc in scenarios
    )
    audio_sync_js = _R._audio_sync_inline() if has_audio_evidence else ""

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
  /* P0-4.2：CI 受控生成可信徽章（绿底，强调"可信 artifact · 可溯源"） */
  .ci-badge {{ background:#e6f7ee; border:1px solid #2e9e6b; color:#15583b;
               border-radius:8px; padding:10px 16px; margin:12px 0; font-size:14px;
               font-weight:600; }}
  .ci-badge-sub {{ font-weight:400; font-size:12px; color:#3a7a5d; margin-left:6px; }}
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
  /* P1 干预派发回执卡 */
  .receipt-list {{ list-style:none; margin:8px 0; padding:0; }}
  .receipt-row {{ display:flex; gap:8px; align-items:baseline; flex-wrap:wrap;
                 padding:6px 0; border-top:1px solid #e3eefb; }}
  .receipt-row:first-child {{ border-top:none; }}
  .receipt-cmd {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px;
                 background:#dcebfb; color:#1c4f7c; border-radius:6px; padding:1px 8px;
                 white-space:nowrap; }}
  .receipt-arrow {{ color:#8a94a6; }}
  .receipt-role {{ font-weight:600; color:#2b3a4a; }}
  .receipt-closure {{ font-size:13px; color:#15583b; margin-left:4px; }}
  .receipt-closure-list {{ margin:8px 0; font-size:13px; color:#2b3a4a; }}
  .receipt-closure-list ul {{ margin:4px 0 0; padding-left:18px; }}
  .receipt-closure-list code {{ font-size:12px; background:#eef6ff; color:#1c4f7c;
                               border-radius:4px; padding:0 4px; }}
  .receipt-reach {{ margin-top:8px; font-size:13px; color:#1c4f7c; }}
  .receipt-reach-target {{ font-weight:700; }}
  .receipt-note {{ margin-top:6px; font-size:12px; line-height:1.5; }}
  /* 音频感知首屏面板（音频 E2E P0） */
  .audio-perception {{ display:flex; flex-direction:column; gap:10px; margin:8px 0; }}
  .audio-card {{ background:#fdf2f8; border:1px solid #f3c9de; border-left:4px solid #c2408a;
                 border-radius:8px; padding:10px 14px; }}
  .audio-card-head {{ display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }}
  .audio-marker {{ font-size:15px; }}
  .audio-kind {{ font-size:15px; font-weight:700; color:#a12c6e; }}
  .audio-meta {{ margin-top:2px; }}
  .audio-play {{ margin-top:8px; display:flex; gap:10px; align-items:center; }}
  .audio-play audio {{ height:34px; }}
  /* Gate 3：Live 首屏实时音频摘要（"此刻传感器检测到什么"；技术细节在 details） */
  .live-audio-summary {{ margin:8px 0; }}
  .detected-list {{ list-style:none; margin:4px 0; padding:0; display:flex; flex-direction:column; gap:4px; }}
  .detected-list li {{ display:flex; gap:8px; align-items:baseline; }}
  .detected-kind {{ font-size:15px; font-weight:700; color:#a12c6e; }}
  .live-audio-meta {{ display:flex; gap:10px; flex-wrap:wrap; align-items:baseline; margin-top:6px; }}
  .audio-source {{ font-weight:600; color:#2e9e6b; }}
  /* P1 Acoustic State（telephone_risk · 声学状态变化，非诈骗判定，VM-9） */
  .acoustic-state {{ background:#fff7ed; border:1px solid #f4d3a8; border-left:4px solid #d9842f;
                    border-radius:8px; padding:10px 14px; margin:4px 0 12px; }}
  .acoustic-state-head {{ font-weight:700; color:#8a4b12; margin-bottom:4px; }}
  .acoustic-state-lead {{ margin:0 0 6px; color:#5a3a1e; }}
  .acoustic-state-note {{ margin:2px 0 0; }}

  /* P0-4：负向能力卡（"为什么没有报警"）——差异化最强能力，首屏可见 */
  .suppress-reason {{ background:#eef6ff; border:1px solid #cfe3fb; border-left:4px solid #4a90d9;
                      border-radius:8px; padding:12px 16px; margin:12px 0; }}
  .suppress-head {{ font-weight:700; color:#1c4f7c; margin-bottom:6px; }}
  .suppress-list {{ list-style:none; margin:0; padding:0; }}
  .suppress-list li {{ display:flex; gap:10px; align-items:baseline; padding:6px 0;
                       border-top:1px solid #e3eefb; }}
  .suppress-list li:first-child {{ border-top:none; }}
  .suppress-tag {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:11px;
                  background:#dcebfb; color:#1c4f7c; border-radius:6px; padding:1px 8px;
                  white-space:nowrap; }}
  .suppress-text {{ font-size:13px; color:#2b3a4a; }}

  /* P1-1：叙事带（结果自适应一句话 hero）——差异化叙事首屏锚点 */
  .narrative-band {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap;
                    border-radius:8px; padding:12px 16px; margin:12px 0;
                    border:1px solid #e3e8ee; border-left:4px solid #8a94a6; background:#f7f9fc; }}
  .nb-kind {{ font-weight:700; font-size:13px; border-radius:6px; padding:2px 10px;
              background:#e9edf3; color:#3b4a5a; white-space:nowrap; }}
  .nb-text {{ font-size:14px; color:#2b3a4a; }}
  .narrative-band.sev-suppressed {{ border-left-color:#2e9e6b; background:#eefaf3; }}
  .narrative-band.sev-suppressed .nb-kind {{ background:#d7f0e2; color:#1e7a4f; }}
  .narrative-band.sev-high_risk {{ border-left-color:#d64541; background:#fdf0ef; }}
  .narrative-band.sev-high_risk .nb-kind {{ background:#f7d6d4; color:#b0332f; }}
  .narrative-band.sev-repeated_visit {{ border-left-color:#e0a030; background:#fdf7ec; }}
  .narrative-band.sev-repeated_visit .nb-kind {{ background:#f7e7c8; color:#9a6b13; }}
  .narrative-band.sev-monitor {{ border-left-color:#4a90d9; background:#eef6ff; }}
  .narrative-band.sev-monitor .nb-kind {{ background:#dcebfb; color:#1c4f7c; }}

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
  /* Case Time 主轴：音频 + 记忆 双 Lane（P1 · VM-1 纯展示编排，零新数据） */
  .case-time {{ margin:8px 0; }}
  .case-time-axis {{ display:grid; grid-template-columns:42px 1fr; grid-template-rows:24px 24px;
                    gap:4px; align-items:center; }}
  .lane-tags {{ grid-column:1; grid-row:1 / span 2; display:flex; flex-direction:column;
               justify-content:space-around; }}
  .lane-tag {{ font-size:11px; color:#5b6b7b; line-height:1; white-space:nowrap; }}
  .case-time-track {{ grid-column:2; grid-row:1 / span 2; position:relative; height:52px;
                     background:#f7f9fc; border:1px solid #e3e8ee; border-radius:6px; }}
  .case-time-mark {{ position:absolute; transform:translateX(-50%); display:inline-flex;
                    align-items:center; justify-content:center; width:22px; height:18px;
                    border-radius:50%; font-size:12px; cursor:pointer; z-index:2;
                    box-shadow:0 0 0 1px #fff; }}
  .mark-audio {{ top:4px; background:#e8f1fb; border:1px solid #4a90d9; }}
  .mark-memory {{ top:30px; background:#fdf0e3; border:1px solid #e0922f; }}
  .case-time-mark-active {{ outline:2px solid #2e9e6b; }}
  .case-time-cursor {{ position:absolute; top:0; bottom:0; left:0; width:2px;
                      background:#4a90d9; z-index:1; transition:left .15s; }}
  .case-time-meta {{ display:flex; gap:8px; align-items:center; margin-top:6px; }}
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
  <h1>银龄盾 · 安全案例回放</h1>
  <p class="subtitle">一次运行，看懂一起安全案例：发生了什么 → 为什么值得关注 → 系统做了什么</p>
  {ci_badge}
  <p class="prov-note">{_R._esc(prov_note)}</p>
  {''.join(scenario_blocks)}
  <details class="glossary">
    <summary>技术信息与术语对照（点开查看）</summary>
    <ul>
      <li><code>case_id</code> — {_R._esc(descriptor['case_id'])}（ADR-0036 统一 Case Viewer · 单一 EvidenceProjection View Model）</li>
      <li><code>generated_at</code> — {_R._esc(meta.get('generated_at', '(unknown)'))}</li>
      <li><code>scenarios</code> — {meta.get('scenario_count', 0)} · 数据源: ADR-0034 IntegrationReport artifact（只读投影，禁 synthetic node）</li>
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
{audio_sync_js}
</script>
<script>
{live_actions_js}
</script>
<script>
{live_stream_js}
</script>
<script>
{story_replay_js}
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
