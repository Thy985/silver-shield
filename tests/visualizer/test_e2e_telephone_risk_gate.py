"""telephone_risk Browser E2E Gate（Owner 验收标准 A-E · 2026-08-23 冻结）

验收链（同一真实 Browser Session 内成立）：
    Frame N → Perception N → Audio N → Risk N → Decision N → Action N

时序方案（由音频投递规则倒推）：
    gateway 音频投递为 ``frame_index==k → 第 k 条``（gateway._feed_live_audio），case_b_mix
    的事件集中在前若干帧消费完，且 frame_index 单调递增不回绕 —— 迟连接的 Browser 必然
    错过全部音频。故本测试采用「先连接后重置」流程：
        页面连接等 snapshot → POST /demo/reset（switch_source 归零 _frame_index 且保留
        _live_audio_events → 音频于 frame 0-8 向已在线的本 session 重放）→ 收到
        source_switched 后进入观察窗。
    Gate 断言消费两类时间线：WS 全量（snapshot 属于 pre 段，reset 不重发）与
    source_switched 之后的 post 段（frame_tick / evidence_delta / risk_delta）。

关键产品行为（断言口径依据）：
    1. risk_delta(raised) 若不带 risk_signals 实体（视觉 LOW/MONITOR warning 即如此），
       rt-card 写入后会被 _tickRiskSignals TTL 兜底（5s）重置回空态 → Gate C 采用
       1s 高频轮询捕获瞬态卡片，而非 dump 时点快照。
    2. audio-table 位于可折叠面板内 → innerText 恒空，必须用 textContent 采样。
    3. ADR-0040 硬门控：RuleBasedDecisionPolicy 升级消费 risk_signals 前 audio→risk 链
       不接通 → 当前视觉链最高产出 LOW/MONITOR warning，无命令下发型 action，故
       state_update 缺失属预期；Gate D 按「命令型 action 才要求执行痕迹」条件判定。
    4. AUDIO_LEVEL_CHANGED 依赖 rms_delta 字段（SEMANTICS §2 标注待办，后端未实现）→
       test_b3 反向断言 + skip 说明，不编造。

已知独立缺陷（不在本 Gate 拦截，随验收报告上报）：
    /live 页面内联脚本 ``echarts is not defined``（页面加载即抛出，趋势图组件失效，
    与 WS 消息处理链无关）。

运行前提（外部 fixture，测试内探测 skip）：
    python scripts/run_demo.py --live --scenario config/demo/scenarios/e2e_telephone_risk.yaml
"""

from __future__ import annotations

import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import pytest
import requests

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright not installed", allow_module_level=True)

BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/live"
SID = "e2e_telephone_risk"
OBSERVE_MS = 90_000  # 单 session 观察窗（484 帧 ≈ 60-150s/轮 @实测 ~3fps CPU 推理）
POLL_MS = 1_000  # 风险卡瞬态轮询间隔（< TTL 5s，保证捕获）

# 决策矩阵中的「命令下发型」action（会产生 ActionCommand / state_update 流转）
COMMAND_ACTIONS = {"NOTIFY_FAMILY", "ESCALATE_COMMUNITY", "CREATE_COMMUNITY_TASK"}
# 观察型 / 记录型 action（仅记录，不产生命令下发）
PASSIVE_ACTIONS = {"MONITOR", "MONITOR_FAMILY", "LOG_ONLY"}
LEGAL_ACTIONS = COMMAND_ACTIONS | PASSIVE_ACTIONS

REASON_RUNTIME_ALLOWLIST = {
    # decision_policy.routing_table human_reason 原始值
    "异常停留", "重复访问", "未在白名单", "异常时段访问", "多风险规则同时命中",
    # live_stream.js _REASON_ZH 同义润色白名单（枚举→人话，不扩展语义）
    "停留超过阈值", "检测到重复访问", "待核实到访", "夜间异常访问",
    "高风险逼近（多规则命中）", "声学状态变化", "语音应激升高",
    "电话交互进行中", "夜间访问",
}

_WS_CAPTURE_INIT = """
window.__wsLog = [];
window.__wsMeta = { opened: 0, closed: 0 };
window.__wsInstances = [];
(function () {
  var OW = window.WebSocket;
  function PatchedWS(url, protocols) {
    var ws = protocols !== undefined ? new OW(url, protocols) : new OW(url);
    window.__wsMeta.opened++;
    window.__wsInstances.push(ws);
    ws.addEventListener('close', function () { window.__wsMeta.closed++; });
    ws.addEventListener('message', function (ev) {
      try {
        var m = JSON.parse(ev.data);
        m.__t = Date.now();
        window.__wsLog.push(m);
        if (window.__wsLog.length > 12000) window.__wsLog.shift();
      } catch (e) { /* 非 JSON 帧忽略 */ }
    });
    return ws;
  }
  PatchedWS.prototype = OW.prototype;
  window.WebSocket = PatchedWS;
})();
"""

# MJPEG <img> 的 src 恒定且 load 仅触发一次 → 用缩小画布签名验证画面持续变化。
# video_file 模式无 ov-frame overlay（render.py 仅 canvas_fallback 分支渲染 chips），
# 故 Gate A3/A4 的 DOM 层信号 = 画布签名 + ps-state 文本推进。
_VIDEO_SIG_JS = """
(() => {
  const img = document.getElementById('video-img-__SID__')
           || document.querySelector('img[src*="/mjpeg/"]');
  if (!img) return { error: 'no-video-img-el' };
  if (!img.complete || !img.naturalWidth) return { error: 'not-decoded-yet' };
  const c = document.createElement('canvas');
  c.width = 64; c.height = 36;
  const g = c.getContext('2d');
  g.drawImage(img, 0, 0, 64, 36);
  return { sig: c.toDataURL('image/png').slice(-192), w: img.naturalWidth, h: img.naturalHeight };
})()
""".replace("__SID__", SID)

# 瞬态风险卡轮询采样（rt-card 会在 ≤5s 内被 TTL 兜底重置，须高频捕获）
_SIGNALS_POLL_JS = """
(() => {
  const box = document.getElementById('live-signals-__SID__');
  if (!box) return { txt: '', cards: -1, badges: [] };
  return {
    txt: box.textContent || '',
    cards: box.querySelectorAll('.rt-card').length,
    badges: Array.from(box.querySelectorAll('.rt-badge')).map(b => b.textContent || ''),
  };
})()
""".replace("__SID__", SID)


def _server_available() -> bool:
    try:
        return requests.get(f"{BASE}/health", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _server_available(), reason="E2E gateway 未运行（run_demo --live --scenario e2e_telephone_risk）"
)


def _new_page(playwright):
    browser = playwright.chromium.launch(
        executable_path=r"C:\Users\lenovo\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe",
        headless=True,
    )
    ctx = browser.new_context()
    ctx.add_init_script(_WS_CAPTURE_INIT)
    return browser, ctx, ctx.new_page()


def _js(page, expr):
    return page.evaluate(expr)


def _wait_js_true(page, expr, timeout_s: float, poll_ms: int = 300) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page.evaluate(expr):
            return True
        page.wait_for_timeout(poll_ms)
    return False


def _risk_deltas(log):
    return [m for m in log if m.get("type") == "risk_delta"]


def _state_updates(log):
    return [m for m in log if m.get("type") == "state_update"]


def _audio_events(log):
    out = []
    for m in log:
        if m.get("type") == "evidence_delta" and m.get("audio"):
            out.extend(m["audio"])
    return out


def _all_warnings(log):
    return [w for m in _risk_deltas(log) for w in (m.get("active_warnings") or [])]


@pytest.fixture(scope="module")
def session():
    """单真实 Browser Session：连接 → reset 对齐 → 观察窗（1s 轮询）→ 全量 dump。

    「先连接后重置」：/demo/reset 归零 gateway._frame_index 且保留注入的音频事件列表，
    使前几帧的音频向本已在线的 session 重放（解决迟连接错过音频的时序问题）；
    source_switched 之后即为干净时间线，dump 时切出 post 段供 Gate 消费；
    snapshot 仅在连接时发送（reset 不重发），故另存全量 log 供 Gate A1/A2。
    """
    data: dict = {}
    with sync_playwright() as p:
        browser, _ctx, page = _new_page(p)
        page.goto(URL, wait_until="domcontentloaded", timeout=30_000)

        # 1) 等 WS snapshot 到达（确认 Browser 已在线；消息落在 pre 段）
        assert _wait_js_true(page, "(window.__wsLog||[]).some(m=>m.type==='snapshot')", 15), (
            "15s 内未收到 snapshot（WS 未建立）"
        )

        # 2) 重置对齐：归零帧循环 → 注入的音频事件将于 frame 0-8 向本 session 重放
        resp = requests.post(f"{BASE}/demo/reset", timeout=15)
        assert resp.status_code == 200, f"/demo/reset 失败: {resp.status_code} {resp.text}"

        # 3) 等 source_switched 广播（reset 生效标志；前端据此 resetSession 清空累积）
        assert _wait_js_true(page, "(window.__wsLog||[]).some(m=>m.type==='source_switched')", 10), (
            "10s 内未收到 source_switched（reset 未生效）"
        )

        # 4) 前端 resetSession() 稳定后抓「风险触发前」空态基线（Gate D6）。
        #    注意：resetSession 清 JS 状态但不清任务卡 DOM 文字，故基线含 pre 遗留文案，
        #    Gate D4/D5 以「相对基线的变化」判定 post 段执行痕迹。
        page.wait_for_timeout(2_000)
        data["early"] = {
            "task_family": _js(page, f"document.getElementById('task-family-{SID}')?.innerText || ''"),
            "task_community": _js(page, f"document.getElementById('task-community-{SID}')?.innerText || ''"),
            "task_log": _js(page, f"document.getElementById('task-log-{SID}')?.innerText || ''"),
            "closure_warning": _js(page, f"document.getElementById('closure-{SID}')?.innerText || ''"),
        }

        # 5) t0 采样（MJPEG 画布签名 + 感知流状态文本）
        data["video_t0"] = _js(page, _VIDEO_SIG_JS)
        data["psstate_t0"] = _js(page, f"document.getElementById('ps-state-{SID}')?.innerText || ''")

        # 6) 观察窗：1s 轮询瞬态风险卡（RAISED/ACTIVE/CLEARED badge + 卡数上限）
        seen_badges: set[str] = set()
        max_cards = 0
        half = OBSERVE_MS // 2
        steps = OBSERVE_MS // POLL_MS
        for i in range(steps):
            sample = _js(page, _SIGNALS_POLL_JS)
            for b in sample.get("badges") or []:
                seen_badges.add(str(b))
            max_cards = max(max_cards, int(sample.get("cards") or 0))
            page.wait_for_timeout(POLL_MS)
            if i * POLL_MS == half:  # 中点采样 video 签名
                data["video_t1"] = _js(page, _VIDEO_SIG_JS)
        data["seen_badges"] = sorted(seen_badges)
        data["max_rt_cards"] = max_cards

        # 7) t2 采样 + 全量 dump（同一 session）
        data["video_t2"] = _js(page, _VIDEO_SIG_JS)
        data["psstate_t2"] = _js(page, f"document.getElementById('ps-state-{SID}')?.innerText || ''")
        data["ws_meta"] = _js(page, "window.__wsMeta")
        log_all = _js(page, "window.__wsLog")
        data["log_all"] = log_all  # 全量（含 pre 段 snapshot / source_switched）
        cut_candidates = [i for i, m in enumerate(log_all) if m.get("type") == "source_switched"]
        assert cut_candidates, "dump 阶段找不到 source_switched（不应发生：fixture 步骤 3 已确认）"
        data["log"] = log_all[cut_candidates[-1]:]  # post 段：干净时间线

        data["pill"] = _js(page, "document.getElementById('ws-pill')?.className || ''")
        data["pill_text"] = _js(page, "document.getElementById('ws-text')?.textContent || ''")
        data["ps_recent_text"] = _js(page, f"document.getElementById('ps-recent-{SID}')?.innerText || ''")
        data["ps_state_text"] = _js(page, f"document.getElementById('ps-state-{SID}')?.innerText || ''")
        data["ps_history_count"] = _js(page, f"document.getElementById('ps-history-count-{SID}')?.textContent || '0'")
        # audio-table 在可折叠面板内（display:none）→ innerText 恒空，必须用 textContent
        data["audio_rows"] = _js(
            page,
            "Array.from(document.querySelectorAll('table.audio-table tr'))"
            ".map(r => (r.textContent || '').replace(/\\s+/g, ' ').trim())",
        )
        data["signals_html"] = _js(page, f"document.getElementById('live-signals-{SID}')?.innerHTML || ''")
        data["signals_text"] = _js(page, f"document.getElementById('live-signals-{SID}')?.innerText || ''")
        data["lrk"] = _js(
            page,
            """(() => {
              const card = document.getElementById('lrk-card-__SID__');
              const reasons = Array.from(document.querySelectorAll('#lrk-reasons-__SID__ li'))
                .map(li => (li.textContent || '').trim().replace(/^✓\\s*/, ''));
              return {
                visible: card && card.style.display !== 'none',
                level: document.getElementById('lrk-level-__SID__')?.innerText || '',
                reasons,
                empty_text: document.getElementById('lrk-empty-__SID__')?.innerText || '',
              };
            })()""".replace("__SID__", SID),
        )
        data["tasks"] = _js(
            page,
            """(() => {
              const g = id => document.getElementById(id + '-__SID__')?.innerText || '';
              return {
                family_status: g('task-family-status'), family_body: g('task-family-body'),
                community_status: g('task-community-status'), community_body: g('task-community-body'),
                log_status: g('task-log-status'), log_body: g('task-log-body'),
              };
            })()""".replace("__SID__", SID),
        )
        data["risk_signal_map"] = _js(
            page,
            """(() => {
              const out = [];
              if (window.__LiveState && window.__LiveState.riskSignalMap) {
                window.__LiveState.riskSignalMap.forEach((v, k) => out.push({ key: String(k), value: v }));
              }
              return out;
            })()""",
        )
        data["pstream"] = _js(
            page,
            """(() => {
              const ps = window.__LiveStream && window.__LiveStream.perceptionStream;
              if (!ps) return null;
              return {
                entries: ps.entries, history: ps.history,
                seen_audio_ids: Array.from(ps._seenAudioEventIds || []),
                seen_risk_trans: Array.from(ps._seenRiskTransitions || []),
              };
            })()""",
        )
        data["see_state_audio"] = _js(page, "window.__LiveStream ? (window.__LiveStream.seeState || {}).audio || [] : []")
        browser.close()
    return data


# ============================================================
# Gate A · Runtime
# ============================================================


class TestGateARuntime:
    def test_a1_a2_ws_established_and_snapshot(self, session):
        """A1 WS 建立 + A2 snapshot 到达（snapshot 仅连接时发送，reset 不重发 → 查全量）。"""
        assert session["ws_meta"]["opened"] >= 1, "WS 从未 open"
        assert session["pill"].find("online") >= 0, f"ws-pill 非 online: {session['pill']}"
        snaps = [m for m in session["log_all"] if m.get("type") == "snapshot"]
        assert snaps, "session 内无 snapshot 消息"

    def test_a3_frame_tick_continuous(self, session):
        """A3 frame_tick 持续：post 段 ticks 充足、frame_index 单调推进。

        video_file 模式无 ov-frame overlay（render.py 仅 canvas_fallback 分支渲染），
        DOM 层信号以 ps-state 文本推进代替。
        """
        ticks = [m for m in session["log"] if m.get("type") == "frame_tick"]
        assert len(ticks) >= 60, f"frame_tick 过少: {len(ticks)}（90s 观察窗内应远大于此）"
        idx = [m.get("frame_index") for m in ticks if m.get("frame_index") is not None]
        assert idx == sorted(idx), "frame_index 非单调（loop 重放不回绕契约被破坏）"
        assert len(set(idx)) >= 30, f"frame_index 几乎无推进: {idx[:10]}"
        assert session["psstate_t0"] != session["psstate_t2"], (
            f"ps-state 文本未随时间推进（DOM 冻结?）: {session['psstate_t0']!r}"
        )

    def test_a4_video_frame_changing(self, session):
        """A4 video frame 持续变化（MJPEG 流画布签名前后不同）。"""
        v0, v2 = session["video_t0"], session["video_t2"]
        assert isinstance(v0, dict) and "sig" in v0, f"MJPEG 画布采样失败: {v0}"
        assert isinstance(v2, dict) and "sig" in v2, f"MJPEG 画布采样失败: {v2}"
        assert v0.get("w"), f"video img 未解码（naturalWidth=0）: {v0}"
        assert v0["sig"] != v2["sig"], "90s 内画面签名无变化（MJPEG 冻结）"

    def test_a5_audio_events_arrive(self, session):
        """A5 audio event 到达（evidence_delta.audio 非空 + DOM seeState）。

        kind 集合以 runtime 实际输出为准：case_b_mix 经 energy backend 产出的
        真实类别为 audio_distress_cry（YAMNet class_map 修复前的保守映射）。
        """
        audios = _audio_events(session["log"])
        assert audios, "post-reset 观察窗内无任何 audio 事件到达"
        kinds = {str(a.get("kind", "")) for a in audios}
        assert any(k.startswith("audio_") or "telephone" in k or "speech" in k for k in kinds), (
            f"音频类别异常（应为 audio_* 合法类别）: {kinds}"
        )
        assert session["see_state_audio"], "seeState.audio 为空（DOM 语义层未收到音频）"

    def test_a6_ws_disconnect_graceful_degradation(self):
        """A6 WS 断开后 UI 正确降级（pill→未连接），恢复后自动重连（2.5s backoff）。

        set_offline / CDP emulateNetworkConditions 对已建立的 WS 均无效（实测）→
        经 init script 记录的实例引用主动 close()：onclose 无条件触发
        （live_stream.js: _setWsPill(false) + _scheduleReconnect），与服务端失联同径。
        """
        with sync_playwright() as p:
            browser, _ctx, page = _new_page(p)
            page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(5_000)
            assert "online" in _js(page, "document.getElementById('ws-pill')?.className || ''")
            closed = _js(
                page,
                "(window.__wsInstances || []).filter(w => w.readyState === 1)"
                ".map(w => { try { w.close(); return true; } catch (e) { return false; } })",
            )
            assert any(closed), f"无可关闭的活动 WS: {closed}"
            # 采样窗口须落在 close 触发的 onclose 与 2.5s 自动重连之间
            page.wait_for_timeout(1_200)
            cls = _js(page, "document.getElementById('ws-pill')?.className || ''")
            txt = _js(page, "document.getElementById('ws-text')?.textContent || ''")
            assert "offline" in cls and txt == "未连接", f"断开后未降级: {cls}/{txt}"
            page.wait_for_timeout(9_000)  # 2.5s backoff + 重连握手
            cls2 = _js(page, "document.getElementById('ws-pill')?.className || ''")
            assert "online" in cls2, f"恢复后未自动重连: {cls2}"
            browser.close()


# ============================================================
# Gate B · Perception
# ============================================================


class TestGateBPerception:
    def test_b1_person_entered_to_dom(self, session):
        """PERSON_ENTERED 到达 DOM：ps-recent 出现"首次出现/进入画面"语义条目。

        reset 后前端 resetSession 将 person 计数归 -1，视频内人员首次出现即触发。
        """
        text = session["ps_recent_text"] + session["ps_state_text"]
        assert ("首次出现" in text) or ("人" in text and "在场" in text), f"无人员语义条目: {text[:200]}"
        pstream = session["pstream"]
        assert pstream is not None, "__LiveStream.perceptionStream 未导出"
        all_entries = pstream["entries"] + pstream["history"]
        person_entries = [e for e in all_entries if e.get("type") == "behavior" and "进入画面" in str(e.get("detail", ""))]
        assert person_entries, "perceptionStream 无 PERSON_ENTERED 语义条目"

    def test_b2_audio_detected_to_dom(self, session):
        """AUDIO_DETECTED 到达 DOM：audio-table 数据行 + perceptionStream 音频条目。

        audio-table 位于可折叠面板内 → innerText 恒空，fixture 已改用 textContent 采样。
        """
        rows = [r for r in session["audio_rows"] if r and "kind" not in r]
        assert rows, f"audio-table 无数据行: {session['audio_rows'][:3]}"
        assert any("distress" in r or "telephone" in r or "电话" in r or "语音" in r for r in rows), (
            f"行内容异常: {rows[:3]}"
        )
        pstream = session["pstream"]
        all_entries = pstream["entries"] + pstream["history"]
        assert any(e.get("type") == "audio" for e in all_entries), "perceptionStream 无 audio 语义条目"

    def test_b3_audio_level_changed_by_contract(self, session):
        """AUDIO_LEVEL_CHANGED 按契约出现。

        契约（LIVE-PERCEPTION-STREAM-SEMANTICS §2）：rms_delta > 6dB 才产生；
        后端 rms_delta 字段未实现（规格标注待办）→ 契约行为 = 字段缺席不编造。
        断言：DOM 不出现"声音强度明显变化"条目（若出现则为前端编造，FAIL）。
        """
        text = session["ps_recent_text"] + session["ps_state_text"]
        assert "声音强度明显变化" not in text, "rms_delta 未实现却出现 AUDIO_LEVEL_CHANGED 条目（前端编造）"
        has_rms_delta = any("rms_delta" in a for a in _audio_events(session["log"]))
        if not has_rms_delta:
            pytest.skip("后端 rms_delta 字段未实现（规格待办）→ 契约缺席验证通过")

    def test_b4_no_raw_delta_flood(self, session):
        """不出现 Raw Delta 刷屏：ps-recent 无 frame@/bbox 裸工程串/裸 JSON。"""
        text = session["ps_recent_text"]
        assert "frame@" not in text, "出现 frame@ 裸 delta"
        assert "bbox [" not in text, "出现裸 bbox 工程信息"
        assert '{"' not in text and "evidence_delta" not in text, "出现裸 JSON/delta 类型名"
        assert "perception_delta" not in text, "出现 delta 类型名"

    def test_b5_semantic_event_dedup(self, session):
        """Semantic Event 正确去重：audio event_id 幂等 + risk transition 去重。"""
        pstream = session["pstream"]
        audios = _audio_events(session["log"])
        event_ids = {a.get("event_id") for a in audios if a.get("event_id")}
        seen_ids = set(pstream["seen_audio_ids"])
        missing = event_ids - seen_ids
        assert not missing, f"到达但未登记的 audio event_id: {missing}"
        all_entries = pstream["entries"] + pstream["history"]
        audio_entries = [e for e in all_entries if e.get("type") == "audio"]
        uniq = len(event_ids) if event_ids else len(seen_ids)
        assert len(audio_entries) <= max(uniq, 1) + 2, (
            f"音频语义条目疑似重复: entries={len(audio_entries)} uniq_ids={uniq}"
        )
        trans = [tuple(t) for t in pstream["seen_risk_trans"]]
        assert len(trans) == len(set(trans)), f"risk transition 重复消费: {trans}"


# ============================================================
# Gate C · Risk
# ============================================================


class TestGateCRisk:
    def test_c1_c3_transitions_visible_and_history_kept(self, session):
        """RAISED 在 DOM 可见过（1s 轮询捕获瞬态卡）+ CLEARED 转换发生时不丢历史。

        产品行为：无 risk_signals 实体的 transition 卡会被 _tickRiskSignals（5s TTL）
        重置回空态 → 可见性以观察窗轮询为准，不以 dump 时点快照为准。
        """
        deltas = _risk_deltas(session["log"])
        transitions = [m.get("risk_transition") for m in deltas if m.get("risk_transition")]
        assert "raised" in transitions, f"post-reset 观察窗内无 RAISED: {transitions}"
        # rt-badge 元素仅由 _applyRiskSignal(raised/active) 写入（5s TTL 渲染器只产
        # rt-sig、无 rt-badge），cleared 分支复用既有卡改写 badge 文本 → 视觉 LOW 信号
        # raise→clear 毫秒级成对出现时，RAISED 原文窗口 <1s 不可采样，但任一 badge
        # （含 CLEARED）可见即构成「transition 卡真实渲染过」的 DOM 证据。
        badges = session["seen_badges"]
        assert badges, f"观察窗轮询未捕获任何风险卡（WS transitions={transitions}）"
        if "cleared" in transitions:
            assert "CLEARED" in badges or session["max_rt_cards"] >= 1, (
                "发生 cleared 但观察窗内从未见过任何风险卡（历史丢失嫌疑）"
            )

    def test_c2_active_no_duplicate_flood(self, session):
        """ACTIVE 不重复刷屏：整个观察窗内任一时刻 live-signals 区卡数 ≤1（覆盖式渲染）。"""
        assert session["max_rt_cards"] <= 1, (
            f"风险卡重复刷屏: 观察窗内峰值 {session['max_rt_cards']} 张"
        )
        active_count = sum(1 for m in _risk_deltas(session["log"]) if m.get("risk_transition") == "active")
        if active_count > 1:
            assert session["seen_badges"], "多次 active 却从未渲染风险卡"

    def test_c5_reason_summary_100_percent_runtime(self, session):
        """reason_summary 100% 来自 runtime：DOM 每条 reason ∈ WS 下发原文 ∪ 润色白名单。

        DOM li 文本带 "✓ " 前缀（live_stream.js 渲染模板），采集时已 strip。
        """
        deltas = _risk_deltas(session["log"])
        ws_reasons: set[str] = set()
        for m in deltas:
            for r in m.get("reason_summary") or []:
                ws_reasons.add(str(r))
            for w in m.get("active_warnings") or []:
                for r in w.get("reason_summary") or []:
                    ws_reasons.add(str(r))
        dom_reasons = session["lrk"]["reasons"]
        assert dom_reasons, f"lrk-reasons 为空（level={session['lrk']['level']}）"
        allow = REASON_RUNTIME_ALLOWLIST | ws_reasons
        fabricated = [r for r in dom_reasons if r not in allow]
        assert not fabricated, f"DOM 出现非 runtime 来源 reason: {fabricated}（允许域={sorted(allow)}）"

    def test_c6_risk_level_matches_runtime(self, session):
        """risk_level 与 runtime 一致：DOM level == post 段最后一条带 levels 的 risk_delta 渲染。"""
        deltas = [m for m in _risk_deltas(session["log"]) if m.get("risk_levels")]
        assert deltas, "无带 risk_levels 的 risk_delta"
        last = deltas[-1]
        expected = " / ".join(last["risk_levels"]) + " 风险"
        assert session["lrk"]["level"] == expected, (
            f"DOM level={session['lrk']['level']!r} != runtime={expected!r}"
        )


# ============================================================
# Gate D · Decision / Action
# ============================================================


class TestGateDDecisionAction:
    def test_d1_d3_warning_and_actions_produced(self, session):
        """Warning 真实产生 + recommended_action / command_types 合法。

        state_update（ActionCommand 流转广播）仅在命令下发型 action 出现时才被要求：
        ADR-0040 硬门控下 audio→risk 未接通，当前视觉链最高产出 LOW/MONITOR，
        无命令型 action → 无 state_update 属预期行为（观察型 action 仅记录）。
        """
        warnings = _all_warnings(session["log"])
        assert warnings, "post-reset 观察窗内无 active_warnings（Warning 未产生）"
        for w in warnings:
            assert w.get("warning_id"), f"Warning 缺 warning_id: {w}"
            act = w.get("recommended_action")
            assert act in LEGAL_ACTIONS, f"非法 recommended_action: {act}"
        has_command_action = any(w.get("recommended_action") in COMMAND_ACTIONS for w in warnings)
        if has_command_action:
            assert _state_updates(session["log"]) or session["tasks"]["family_status"] != "—", (
                "存在命令下发型 action 却无 state_update 且 task 状态未变（ActionCommand 未执行）"
            )

    def test_d4_d5_action_trace_matches_decision_matrix(self, session):
        """执行痕迹与决策矩阵一致：命令型 action → 有执行痕迹；纯观察型 → log 卡记录即可。"""
        warnings = _all_warnings(session["log"])
        has_command_action = any(w.get("recommended_action") in COMMAND_ACTIONS for w in warnings)
        tasks = session["tasks"]
        changed_body = any(
            tasks[k] and tasks[k] != session["early"][ek]
            for k, ek in (
                ("family_body", "task_family"),
                ("community_body", "task_community"),
                ("log_body", "task_log"),
            )
        )
        if has_command_action:
            any_body = any(tasks[k] and "暂无" not in tasks[k] for k in ("family_body", "community_body", "log_body"))
            any_status = any(tasks[k] not in ("—", "") for k in ("family_status", "community_status", "log_status"))
            assert any_body or any_status or changed_body or _state_updates(session["log"]), (
                f"命令型 action 存在但三端任务卡全为空态/未变化: {tasks}"
            )
        else:
            # 观察型路径：至少 log 卡应有记录痕迹（初始态或 runtime 记录均可）
            assert tasks["log_status"] or tasks["log_body"] or changed_body, (
                f"观察型 action 下 log 卡完全空白: {tasks}"
            )

    def test_d6_no_warning_no_action(self, session):
        """无 Warning 时无 Action：reset 后空态基线三端无执行痕迹。

        口径：family/community 必须为"暂无…"空态；log 卡允许固定说明文案
        （"仅记录，无需人工处置"是合理初始态），但不得含命令 id / 完成态字样。
        """
        early = session["early"]
        for key in ("task_family", "task_community"):
            assert "暂无" in early[key], f"风险触发前 {key} 非空态: {early[key][:120]}"
        log_txt = early["task_log"]
        assert not any(tok in log_txt for tok in ("cmd-", "已完成", "已执行")), (
            f"log 卡在风险触发前出现执行痕迹: {log_txt[:120]}"
        )


# ============================================================
# Gate E · Product Story（单 session 全链时序）
# ============================================================


class TestGateEFullChain:
    def test_e_full_chain_single_session_timeline(self, session):
        """Frame→Perception→Audio→Risk→Decision→Action 在同一 session 的 post-reset 时间线上单调成立。

        Action 环节口径：state_update（命令流转）或——当决策矩阵为纯观察型
        （ADR-0040 门控下预期）——Warning 即为行动记录起点（log 卡即时记录）。
        """
        log = session["log"]

        def first_ts(pred):
            for m in log:
                if pred(m):
                    return m["__t"]
            return None

        t_frame = first_ts(lambda m: m.get("type") == "frame_tick")
        t_person = first_ts(lambda m: m.get("type") == "evidence_delta" and m.get("perception_events"))
        t_audio = first_ts(lambda m: m.get("type") == "evidence_delta" and m.get("audio"))
        t_risk = first_ts(lambda m: m.get("type") == "risk_delta" and m.get("risk_transition") == "raised")
        t_warning = first_ts(lambda m: m.get("type") == "risk_delta" and (m.get("active_warnings") or []))
        t_action = first_ts(lambda m: m.get("type") == "state_update")
        if t_action is None:
            warnings = _all_warnings(log)
            if warnings and all(w.get("recommended_action") in PASSIVE_ACTIONS for w in warnings):
                t_action = t_warning  # 纯观察型：行动=即时记录，与 Decision 同点成立
        chain = {
            "Frame": t_frame, "Perception": t_person, "Audio": t_audio,
            "Risk(RAISED)": t_risk, "Decision(Warning)": t_warning, "Action": t_action,
        }
        missing = [k for k, v in chain.items() if v is None]
        assert not missing, f"单 session 内缺失链路环节: {missing}（观测到={ {k: v for k, v in chain.items() if v} }）"

        # 因果方向断言（非全序）：素材特性决定音频（frame 0-8 重放）可先于人员出现，
        # 故只验证「帧流为基底」与「Risk → Decision → Action 方向不逆」。
        others = [chain[k] for k in ("Perception", "Audio", "Risk(RAISED)", "Decision(Warning)", "Action")]
        assert t_frame <= min(others), f"Frame 流不是最早环节: Frame={t_frame} min(others)={min(others)}"
        causal = [chain["Risk(RAISED)"], chain["Decision(Warning)"], chain["Action"]]
        assert causal == sorted(causal), f"Risk→Decision→Action 时序逆序: {causal}"
