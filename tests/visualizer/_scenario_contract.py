"""Shared Scenario Acceptance Contract — multi-scenario acceptance test infrastructure.

Defines:
    - ScenarioAcceptanceContract: base class declaring scenario narrative + phases
    - Four concrete contracts: ProductStoryRisk, ProductStoryBenign,
      CctvSurveillanceSuspicious, DeliveryCourierNormal
    - Shared JS / helpers used by browser, visual, and CCTV acceptance tests

Usage:
    Import the contract you need and replace the hardcoded SID in test files.
    The contracts are designed so that each concrete class owns its phase sequence,
    observe times, and assertion hooks while sharing all infrastructure code here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

# ---------------------------------------------------------------------------
# D0 Product Surface Contract configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class D0Contract:
    """D0 产品表面契约的 scenario 级配置。

    每个测试模块实例化一个 D0Contract 并通过 fixture_factory() 注入基座 fixture。
    字段说明：
      - scenario_id: 服务端 scenario_id（用于 WS / DOM 元素定位）
      - has_audio_surface: 是否产生音频证据（控制 AU-04/05/05b/06/07a/010 跳过）
      - provenance: {"video": ..., "audio": ..., "risk": ...} 三段渲染文案
                   测试只验证 DOM 包含这些字符串，不关心具体场景语义
      - skip_reason: skipif 消息
      - observe_first_frame_ms: 首帧等待超时（默认 30s）
      - observe_perception_ms: 感知数据等待超时（默认 60s）
      - observe_behavior_ms: 行为数据等待超时（默认 95s）
    """
    scenario_id: str = ""
    has_audio_surface: bool = True
    provenance: dict[str, str] = field(default_factory=dict)
    skip_reason: str = ""
    observe_first_frame_ms: int = 30_000
    observe_perception_ms: int = 60_000
    observe_behavior_ms: int = 95_000

# ---------------------------------------------------------------------------
# JS templates (use __SID__ placeholder replaced at instantiation)
# ---------------------------------------------------------------------------

WS_CAPTURE_INIT = """
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
        if (window.__wsLog.length > 20000) window.__wsLog.shift();
      } catch (e) { /* 非 JSON 帧忽略 */ }
    });
    return ws;
  }
  PatchedWS.prototype = OW.prototype;
  window.WebSocket = PatchedWS;
})();
"""


def make_video_sig_js(sid: str) -> str:
    return f"""\
(() => {{
  const img = document.getElementById('video-img-{sid}')
           || document.querySelector('img[src*="/mjpeg/"]');
  if (!img) return {{ error: 'no-video-img-el' }};
  if (!img.complete || !img.naturalWidth) return {{ error: 'not-decoded-yet' }};
  const c = document.createElement('canvas');
  c.width = 64; c.height = 36;
  const g = c.getContext('2d');
  g.drawImage(img, 0, 0, 64, 36);
  return {{ sig: c.toDataURL('image/png').slice(-192), w: img.naturalWidth, h: img.naturalHeight }};
}})()
"""


def make_signals_poll_js(sid: str) -> str:
    return f"""\
(() => {{
  const box = document.getElementById('live-signals-{sid}');
  if (!box) return {{ txt: '', cards: -1, badges: [] }};
  return {{
    txt: box.textContent || '',
    cards: box.querySelectorAll('.rt-card').length,
    badges: Array.from(box.querySelectorAll('.rt-badge')).map(b => b.textContent || ''),
  }};
}})()
"""


def make_layout_js(sid: str) -> str:
    return f"""\
(() => {{
  const main = document.querySelector('main') || document.body;
  const grid = main.querySelector('.grid, .live-grid, [class*="grid"], main > div') || main;
  const gtc = getComputedStyle(grid).gridTemplateColumns || '';
  const findEl = id => document.getElementById(id);
  const findTop = id => {{
    const el = findEl(id);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return Math.round(r.top + window.scrollY);
  }};
  const findH = id => {{
    const el = findEl(id);
    if (!el) return null;
    return Math.round(el.getBoundingClientRect().height);
  }};
  const totalH = Math.max(
    document.body.scrollHeight,
    document.documentElement.scrollHeight
  );
  const sysarch = document.getElementById('lv-sysarch-{sid}');
  return {{
    grid_template_columns: gtc,
    total_height: totalH,
    video_top: findTop('video-img-{sid}'),
    video_height: findH('video-img-{sid}'),
    timeline_top: findTop('behavior-timeline-{sid}') ?? findTop('perception-stream-{sid}'),
    timeline_height: findH('behavior-timeline-{sid}') ?? findH('perception-stream-{sid}'),
    risk_explanation_top: findTop('lrk-card-{sid}'),
    risk_explanation_height: findH('lrk-card-{sid}'),
    signals_top: findTop('live-signals-{sid}'),
    signals_height: findH('live-signals-{sid}'),
    action_top: findTop('fs-action-closure-{sid}'),
    action_height: findH('fs-action-closure-{sid}'),
    sysarch_visible: sysarch ? sysarch.offsetParent !== null : false,
    sysarch_top: sysarch ? Math.round(sysarch.getBoundingClientRect().top + window.scrollY) : null,
  }};
}})()
"""


def make_dom_capture_js(sid: str) -> str:
    return f"""\
(() => {{
  const findText = id => document.getElementById(id)?.innerText || '';
  const findTextContent = id => document.getElementById(id)?.textContent || '';
  const lrk = (() => {{
    const card = document.getElementById('lrk-card-{sid}');
    const reasons = Array.from(document.querySelectorAll('#lrk-reasons-{sid} li'))
      .map(li => (li.textContent || '').trim().replace(/^✓\\\\s*/, ''));
    return {{
      visible: card ? card.style.display !== 'none' : false,
      level: document.getElementById('lrk-level-{sid}')?.innerText || '',
      reasons,
      empty_text: document.getElementById('lrk-empty-{sid}')?.innerText || '',
    }};
  }})();
  const tasks = (() => {{
    const g = id => document.getElementById(id + '-{sid}')?.innerText || '';
    return {{
      family_status: g('task-family-status'),
      family_body: g('task-family-body'),
      community_status: g('task-community-status'),
      community_body: g('task-community-body'),
      log_status: g('task-log-status'),
      log_body: g('task-log-body'),
    }};
  }})();
  const riskMap = (() => {{
    const out = [];
    if (window.__LiveState && window.__LiveState.riskSignalMap) {{
      window.__LiveState.riskSignalMap.forEach((v, k) => out.push({{ key: String(k), value: v }}));
    }}
    return out;
  }})();
  const sysarch = document.getElementById('lv-sysarch-{sid}');
  const sysarchTop = sysarch ? Math.round(sysarch.getBoundingClientRect().top + window.scrollY) : null;
  return {{
    video_natural_width: document.getElementById('video-img-{sid}')?.naturalWidth || 0,
    timeline_text: findTextContent('behavior-timeline-{sid}') || findText('perception-stream-{sid}'),
    signals_text: findText('live-signals-{sid}'),
    lrk,
    tasks,
    risk_signal_map: riskMap,
    sysarch_visible: sysarch ? sysarch.offsetParent !== null : false,
    sysarch_top: sysarchTop,
  }};
}})()
"""


def make_full_dom_js(sid: str) -> str:
    """Full DOM dump including memory, closure, perception stream, risk signal map."""
    return f"""\
(() => {{
  const g = (id) => document.getElementById(id);
  const findTextContent = id => document.getElementById(id)?.textContent || '';
  const lrk = (() => {{
    const card = document.getElementById('lrk-card-{sid}');
    const reasons = Array.from(document.querySelectorAll('#lrk-reasons-{sid} li'))
      .map(li => (li.textContent || '').trim().replace(/^✓\\\\s*/, ''));
    return {{
      visible: card ? card.style.display !== 'none' : false,
      level: document.getElementById('lrk-level-{sid}')?.innerText || '',
      reasons,
      empty_text: document.getElementById('lrk-empty-{sid}')?.innerText || '',
    }};
  }})();
  const tasks = (() => {{
    const g2 = id => document.getElementById(id + '-{sid}')?.innerText || '';
    return {{
      family_status: g2('task-family-status'),
      family_body: g2('task-family-body'),
      community_status: g2('task-community-status'),
      community_body: g2('task-community-body'),
      log_status: g2('task-log-status'),
      log_body: g2('task-log-body'),
    }};
  }})();
  const riskMap = (() => {{
    const out = [];
    if (window.__LiveState && window.__LiveState.riskSignalMap) {{
      window.__LiveState.riskSignalMap.forEach((v, k) => out.push({{ key: String(k), value: v }}));
    }}
    return out;
  }})();
  const ps = window.__LiveStream && window.__LiveStream.perceptionStream;
  const sysarch = document.getElementById('lv-sysarch-{sid}');
  const sysarchTop = sysarch ? Math.round(sysarch.getBoundingClientRect().top + window.scrollY) : null;
  return {{
    pill: g('ws-pill')?.className || '',
    pill_text: g('ws-text')?.textContent || '',
    ps_recent: g('ps-recent-{sid}')?.innerText || '',
    ps_state: g('ps-state-{sid}')?.innerText || '',
    ps_history_count: g('ps-history-count-{sid}')?.textContent || '0',
    audio_rows: Array.from(document.querySelectorAll('table.audio-table tr'))
      .map(r => (r.textContent || '').replace(/\\\\s+/g, ' ').trim()),
    signals_text: g('live-signals-{sid}')?.innerText || '',
    lrk: {{
      visible: lrk.visible,
      level: lrk.level,
      reasons: lrk.reasons,
      empty_text: lrk.empty_text,
    }},
    tasks,
    memory: g('live-memory-timeline-{sid}')?.textContent || '',
    closure: g('fs-action-closure-{sid}')?.innerText || '',
    pstream: ps ? {{
      entries: ps.entries,
      history: ps.history,
      seen_audio_ids: Array.from(ps._seenAudioEventIds || []),
      seen_risk_trans: Array.from(ps._seenRiskTransitions || []),
    }} : null,
    risk_signal_map: riskMap,
    sysarch_visible: sysarch ? sysarch.offsetParent !== null : false,
    sysarch_top: sysarchTop,
  }};
}})()
"""


# ---------------------------------------------------------------------------
# Phase definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseSpec:
    """Single observation phase within a multi-phase acceptance run."""
    name: str
    observe_ms: int
    need_source_switch: bool = True


# ---------------------------------------------------------------------------
# Contract base class
# ---------------------------------------------------------------------------


@dataclass
class ScenarioAcceptanceContract:
    """Declares a scenario's narrative, phase sequence, and observation budgets.

    Concrete subclasses own:
      - scenario_id: server-side scenario_id string
      - skip_reason: message shown when server is not running this scenario
      - phases: ordered list of PhaseSpec
      - observe_times: overrides per-phase (used by visual screenshots)
      - has_audio_surface: True if the scenario produces audio evidence
      - expected_phases: ordered phase names for the runner to execute
      - skip_assertions(phase_name: str) -> list[str]: returns assertion class prefixes to skip
    """
    scenario_id: str = ""
    narrative: str = ""
    phases: list[PhaseSpec] = field(default_factory=list)
    observe_times: dict[str, int] = field(default_factory=dict)
    has_audio_surface: bool = True
    # Assertion hooks: which Test* classes to skip for this scenario
    _skip_assertions: list[str] = field(default_factory=list)

    def get_phase(self, name: str) -> PhaseSpec | None:
        for p in self.phases:
            if p.name == name:
                return p
        return None

    def skip_reason(self) -> str:
        return (
            f"需先启动: python scripts/run_demo.py --live "
            f"--scenario config/demo/scenarios/{self.scenario_id}.yaml"
        )

    def should_skip_audio_assertions(self, phase: str) -> bool:
        """Return True if audio-dependent assertions should be skipped for this phase."""
        return not self.has_audio_surface

    def phase_order(self) -> list[str]:
        return [p.name for p in self.phases]


# ---------------------------------------------------------------------------
# Telephone risk contracts（重命名自 product_story_risk · 2026-08-25）
#
# 历史：原 ProductStoryRiskContract / ProductStoryBenignContract 已于 2026-08-25 重命名
# 为 TelephoneRiskContract / TelephoneRiskBenignContract（场景身份迁移，详见
# docs/design/architecture/SCENARIO-RENAME-CONFLICTS-2026-08-25.md §3 D1）。
# telephone_risk_benign 仍是 telephone_risk 的 internal acceptance fixture（决策 C3 选 A），
# **不**进入 Product Scenario Registry 白名单（白名单只有 telephone_risk / cctv / delivery 三项）。
# ---------------------------------------------------------------------------


class TelephoneRiskContract(ScenarioAcceptanceContract):
    """telephone_risk + telephone_risk_benign four-phase acceptance contract.

    重命名历史：曾以 ProductStoryRiskContract 命名（2026-08-25 迁移）；场景身份已冻结为
    「电话交互 + 异常视觉 → 多模态风险」（更贴近电话风险产品语义，不再叫"product story"）。
    """

    # Product Scenario Registry 属性（D2 决策：放 Contract；场景应该证明的最终产品结论）。
    # RAISED：电话 + 视觉联合触发，门窗报警 + 家属通知 + 社区上报。
    expected_product_result: ClassVar[str] = "RAISED"

    def __init__(self) -> None:
        super().__init__(
            scenario_id="telephone_risk",
            narrative="电话持续交互 + 异常视觉 → 多模态风险 → 通知家属 + 社区上报",
            phases=[
                PhaseSpec("risk", observe_ms=120_000, need_source_switch=True),
                PhaseSpec("benign", observe_ms=80_000, need_source_switch=True),
                PhaseSpec("switch_back", observe_ms=30_000, need_source_switch=True),
                PhaseSpec("reset", observe_ms=15_000, need_source_switch=True),
            ],
            observe_times={
                "risk": 90_000,
                "benign": 60_000,
                "switch_back": 30_000,
            },
            has_audio_surface=True,
            _skip_assertions=[],
        )
        self.d0 = D0Contract(
            scenario_id="telephone_risk",
            has_audio_surface=True,
            provenance={
                # 2026-08-25 修复：telephone_risk 命中 GOLDEN_CASES → inject_golden_evidence
                # 追加 provenance_kind=SIMULATED 的 pre-event 节点 → timeline kinds 变为
                # {REAL_SENSOR, SIMULATED} → _modality_provenance_values 走 "混合来源" 分支。
                # 同步 base 上 ProductStoryRiskContract 不在 GOLDEN_CASES，渲染路径仍为
                # "实时推理 / 合成回放"；rename 后 telephone_risk 走新路径，文案变更合理。
                "video": "混合来源：REAL_SENSOR · SIMULATED",
                "audio": "混合来源：SIMULATED",
                "risk": "runtime-computed",
            },
            skip_reason=(
                "需先启动: python scripts/run_demo.py --live "
                "--scenario config/demo/scenarios/telephone_risk.yaml"
            ),
        )

    def benign_scenario_id(self) -> str:
        return "telephone_risk_benign"


class TelephoneRiskBenignContract(TelephoneRiskContract):
    """Standalone benign-side validation (no risk+benign switch required).

    重命名历史：曾以 ProductStoryBenignContract 命名（2026-08-25）。
    属于 telephone_risk 的 internal acceptance fixture，**不**进入 Product Scenario Registry。
    """

    def __init__(self) -> None:
        super().__init__()
        self.scenario_id = "telephone_risk_benign"
        self._skip_assertions = ["TestP2RiskStory", "TestP7SceneSwitch"]
        self.d0 = D0Contract(
            scenario_id="telephone_risk_benign",
            has_audio_surface=True,
            provenance={
                "video": "无视觉轨",
                "audio": "合成回放 (SYNTHETIC_REPLAY)",
                "risk": "runtime-computed",
            },
            skip_reason=(
                "需先启动: python scripts/run_demo.py --live "
                "--scenario config/demo/scenarios/telephone_risk_benign.yaml"
            ),
        )


# ---------------------------------------------------------------------------
# CCTV surveillance contract
# ---------------------------------------------------------------------------


class CctvSurveillanceSuspiciousContract(ScenarioAcceptanceContract):
    """cctv_surveillance_suspicious single-phase acceptance contract.

    Narrative (frozen):
        夜间人员出现 → 重复出现 → 异常停留 → 视觉风险信号 → RiskSignal → WARN → LOG_ONLY

    Key differences from telephone_risk:
        - No audio surface (CCTV video has no sound)
        - Single phase (no benign/benign switch)
        - Expected max risk level: WARN (not HIGH)
        - repeat_visit_count override: 2
    """

    # Product Scenario Registry 属性（D2 决策）：WARN = 夜间异常升级但不到 HIGH，
    # 仅观察记录（LOG_ONLY），不通知家属、不创建社区任务（AU-11 守护）。
    expected_product_result: ClassVar[str] = "WARN"

    def __init__(self) -> None:
        super().__init__(
            scenario_id="cctv_surveillance_suspicious",
            narrative="夜间反复出现 + 异常停留 → 视觉风险信号 → WARN/LOG_ONLY（无音频）",
            phases=[
                PhaseSpec("cctv_observe", observe_ms=120_000, need_source_switch=True),
            ],
            observe_times={
                "cctv_observe": 90_000,
            },
            has_audio_surface=False,
            _skip_assertions=[
                "TestP1AudioEvidenceArrives",
                "TestP2RiskStory",
                "TestP3BenignStory",
                "TestP6AntiHallucination",
                "TestP7SceneSwitch",
            ],
        )
        self.d0 = D0Contract(
            scenario_id="cctv_surveillance_suspicious",
            has_audio_surface=False,
            provenance={
                "video": "实时推理 (REAL_RUNTIME_VIDEO)",
                "audio": "无音频轨",
                "risk": "runtime-computed",
            },
            skip_reason=(
                "需先启动: python scripts/run_demo.py --live "
                "--scenario config/demo/scenarios/cctv_surveillance_suspicious.yaml"
            ),
        )


# ---------------------------------------------------------------------------
# Delivery courier normal contract（与 cctv_surveillance_suspicious 形成
# 「正常 vs 异常」对照基线，验证系统"看到人 ≠ 报警"的克制能力）
# ---------------------------------------------------------------------------


class DeliveryCourierNormalContract(ScenarioAcceptanceContract):
    """delivery_courier_normal single-phase acceptance contract.

    Narrative (frozen):
        白天正常单次来访 → 系统识别为普通来访 → 至多 visit_normal / 微弱异常 → LOG_ONLY / MONITOR
        （与 cctv_surveillance_suspicious 形成「正常 vs 异常」对照基线）

    Key properties:
        - No audio surface (delivery_courier video has no sound)
        - Single phase (no benign/switch_back)
        - Expected max risk level: LOW (no upgrade, system stays restrained)
        - Daytime (start_time 14:00 UTC) → OddHourRule does NOT trigger
        - Single visit → repeat_visit rule does NOT trigger
    """

    # Product Scenario Registry 属性（D2 决策）：MONITOR = 白天正常快递到访系统克制不升级，
    # 仅记录（LOG_ONLY），验证「看到人 ≠ 报警」的对照基线。
    expected_product_result: ClassVar[str] = "MONITOR"

    def __init__(self) -> None:
        super().__init__(
            scenario_id="delivery_courier_normal",
            narrative="白天单次正常来访 → visit_normal / MONITOR（系统克制，不升级）",
            phases=[
                PhaseSpec("courier_observe", observe_ms=90_000, need_source_switch=True),
            ],
            observe_times={
                "courier_observe": 60_000,
            },
            has_audio_surface=False,
            _skip_assertions=[
                "TestP1AudioEvidenceArrives",
                "TestP2RiskStory",
                "TestP3BenignStory",
                "TestP6AntiHallucination",
                "TestP7SceneSwitch",
            ],
        )
        self.d0 = D0Contract(
            scenario_id="delivery_courier_normal",
            has_audio_surface=False,
            provenance={
                "video": "实时推理 (REAL_RUNTIME_VIDEO)",
                "audio": "无音频轨",
                "risk": "runtime-computed",
            },
            skip_reason=(
                "需先启动: python scripts/run_demo.py --live "
                "--scenario config/demo/scenarios/delivery_courier_normal.yaml"
            ),
        )
