"""ADR-0035 D4 · Renderer：EvidenceProjection → 自包含单页 HTML。

**渲染层只消费 ``EvidenceProjection``**（D2 硬规则 1：不直接读 artifact JSON）。

设计（ADR-0035 D4/D6/D7/D8）：
- **自包含**：ECharts 从 ``assets/echarts.min.js`` 内联进 HTML（零外部网络依赖，
  artifact 上传后浏览器直开）；
- **确定性**：HTML 不含当前时间/随机数（D8：同 projection 两次渲染逐字节一致）；
- **脱敏**：只渲染 projection 白名单字段（D7：无路径 / 无设备序列号 / 无 PII）；
- **四视图**：Timeline（CSS 垂直时间轴）/ Decision Explanation（卡片）/
  Cross Modal Graph（ECharts graph，links>0 时）/ Fingerprint-Gate（表格）；
- 视图块带稳定 id 锚点（``timeline-<sid>`` / ``decision-<sid>`` / ``graph-<sid>`` /
  ``gate-<sid>``），供验收测试断言。

本模块只依赖 stdlib（html / pathlib），**不 import 任何生产/验证代码**（D3 AST 契约）。
"""

from __future__ import annotations

import html
from pathlib import Path

from home_perception.visualizer.schema.evidence import (
    EvidenceProjection,
    ScenarioEvidence,
)

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_ECHARTS_FILENAME = "echarts.min.js"

# 时间轴配色（浅色主题，stage → 色值）。
_STAGE_COLOR = {
    "perception": "#4a90d9",
    "decision": "#d97b29",
    "notification": "#7b5cd6",
    "memory": "#2e9e6b",
    "cross_modal": "#c2408a",
    "observability": "#8a8a8a",
}


def _esc(value: object) -> str:
    return html.escape(str(value))


def _echarts_inline() -> str:
    """内联 ECharts（缺失时降级为空串——图视图显示降级提示而非崩溃）。"""
    p = _ASSETS_DIR / _ECHARTS_FILENAME
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 视图块
# ---------------------------------------------------------------------------


def _render_timeline(scenario: ScenarioEvidence) -> str:
    nodes = scenario["timeline"]
    if not nodes:
        return "<p class='muted'>无时间轴节点（artifact 无 stage 数据）</p>"
    items = []
    for node in nodes:
        color = _STAGE_COLOR.get(node["stage"], "#666666")
        kind = node["type"]
        # 结构化 verdict 着色（评审 #4：不靠 summary 子串匹配）
        verdict_class = {
            "PASS": "node-pass",
            "FAIL": "node-fail",
            "INFO": "node-neutral",
        }.get(node["verdict"], "node-neutral")
        items.append(
            f"""
            <li class="tl-item">
              <span class="tl-dot" style="background:{color}"></span>
              <div class="tl-body">
                <div class="tl-head">
                  <span class="tl-step">{_esc(node['timestamp'])}</span>
                  <span class="tl-stage" style="color:{color}">{_esc(node['stage'])}</span>
                  <span class="tl-kind">{_esc(kind)}</span>
                  <span class="tl-verdict {verdict_class}">{_esc(node['summary'])}</span>
                </div>
                <div class="tl-meta muted">
                  provenance: {_esc(node['provenance_kind'])} · source: {_esc(node['ref'])}
                </div>
              </div>
            </li>"""
        )
    return f"<ul class='timeline'>{''.join(items)}</ul>"


def _render_decision(scenario: ScenarioEvidence) -> str:
    evidence = scenario["decision_evidence"]
    if not evidence:
        return "<p class='muted'>无决策证据</p>"
    kinds = {
        "evidence": ("检测证据", "#4a90d9"),
        "rule": ("规则", "#7b5cd6"),
        "policy": ("策略", "#2e9e6b"),
        "outcome": ("决策结果", "#d97b29"),
        "action": ("动作", "#c2408a"),
    }
    cards = []
    for item in evidence:
        label, color = kinds.get(item["kind"], (item["kind"], "#666666"))
        cards.append(
            f"""
            <div class="dc-card">
              <div class="dc-label" style="color:{color}">{_esc(label)}</div>
              <div class="dc-value">{_esc(item['value'])}</div>
              <div class="tl-meta muted">source: {_esc(item['ref'])}</div>
            </div>"""
        )
    return (
        "<p class='subtitle'>为什么报警？</p>"
        f"<div class='dc-grid'>{''.join(cards)}</div>"
    )


def _render_graph(scenario: ScenarioEvidence) -> str:
    n_links = scenario["counts"]["cross_modal_links"]
    n_episodes = scenario["counts"]["episodes"]
    if n_links <= 0:
        return (
            "<p class='muted'>无跨模态关联（cross_modal_links=0）。"
            "图视图按 D2 缺失粒度降级：仅显示计数摘要。</p>"
        )
    if n_episodes <= 0:
        # 数据异常组合（link 必有 episode 支撑）：禁 synthetic——不画空节点（评审 #2）
        return (
            "<p class='muted'>cross_modal_links&gt;0 但 episodes=0（数据异常，fail-closed 降级）。"
            f"仅显示 link 计数：{n_links}。</p>"
        )
    # 从真实 counts 投影（D1 canonical 无 link 级 detail → 节点用计数摘要，不捏造 id）
    graph_id = f"graph-{scenario['scenario_id']}"
    return f"""
      <div id="{graph_id}" class="graph-box" style="height:320px"
           data-links="{n_links}" data-episodes="{n_episodes}"></div>
      <p class="muted">图由真实 counts 投影：{n_episodes} 个 episode 节点 ·
        {n_links} 条 supports 关联（D1 降级：canonical 无 link 级 detail，
        不渲染 episode_id 等未落盘字段）。</p>"""


def _render_gate(scenario: ScenarioEvidence) -> str:
    fp = scenario["fingerprints"]
    rows = []
    for verdict in scenario["gate"]:
        mark = "✅" if verdict["passed"] else f"❌ {verdict['failure_code']}"
        rows.append(
            f"<tr><td>{_esc(verdict['name'])}</td>"
            f"<td>{_esc(verdict['severity'])}</td>"
            f"<td>{mark}</td></tr>"
        )
    status: str
    if not scenario["gate_passed"]:
        status = "FAIL"
    elif scenario["gate_degraded"]:
        status = "PASS (degraded)"
    else:
        status = "PASS"
    return f"""
    <table class="gate-table">
      <tr><th>Fingerprint / Gate</th><th></th></tr>
      <tr><td>expectation_fingerprint</td><td><code>{_esc(fp['expectation_fingerprint'][:16])}…</code></td></tr>
      <tr><td>loop_fingerprint</td><td><code>{_esc(fp['loop_fingerprint'][:16])}…</code></td></tr>
      <tr><td>scenario_fingerprint</td><td><code>{_esc(scenario['scenario_fingerprint'][:16])}…</code></td></tr>
      <tr><td>Gate verdict</td><td><b>{status}</b> (degraded={scenario['gate_degraded']})</td></tr>
    </table>
    <table class="gate-table">
      <tr><th>stage</th><th>severity</th><th>verdict</th></tr>
      {''.join(rows)}
    </table>"""


def _render_scenario(scenario: ScenarioEvidence) -> str:
    status = "PASS" if scenario["ok"] else "FAIL"
    status_class = "ok" if scenario["ok"] else "fail"
    return f"""
    <section class="scenario">
      <h2 class="scenario-title">
        <span class="badge {status_class}">{status}</span>
        <code>{_esc(scenario['scenario_id'])}</code>
        <span class="muted">mode={_esc(scenario['mode'])} · frames={scenario['n_frames']}</span>
      </h2>

      <h3 id="timeline-{_esc(scenario['scenario_id'])}" class="view-anchor">① Scenario Replay Timeline</h3>
      {_render_timeline(scenario)}

      <h3 id="decision-{_esc(scenario['scenario_id'])}" class="view-anchor">② Decision Explanation</h3>
      {_render_decision(scenario)}

      <h3 id="graph-{_esc(scenario['scenario_id'])}" class="view-anchor">③ Cross Modal Graph</h3>
      {_render_graph(scenario)}

      <h3 id="gate-{_esc(scenario['scenario_id'])}" class="view-anchor">④ Fingerprint / Gate</h3>
      {_render_gate(scenario)}
    </section>"""


def _render_graph_script(projection: EvidenceProjection) -> str:
    """为有跨模态关联的场景生成 ECharts graph 初始化脚本（数据全部来自 projection）。

    与 ``_render_graph`` 同守卫：n_links<=0 或 n_episodes<=0 不生成脚本（禁 synthetic，评审 #2）。
    节点 = 真实 episode 计数；边 = 真实 link 计数与节点容量的较小值（画满为止，
    多余 link 数已在 ``data-links`` 属性与降级文案中如实标注，评审 #9）。
    """
    init_blocks = []
    for scenario in projection["scenarios"]:
        n_links = scenario["counts"]["cross_modal_links"]
        n_episodes = scenario["counts"]["episodes"]
        if n_links <= 0 or n_episodes <= 0:
            continue
        sid = scenario["scenario_id"]
        nodes = [
            {"id": f"ep-{i}", "name": f"Episode #{i + 1}", "symbolSize": 48,
             "category": "episode"}
            for i in range(n_episodes)
        ]
        # 边数 = min(link 计数, 节点可容纳的支撑边数)——如实反映，不伪造多余边
        max_edges = max(n_episodes - 1, 1)
        edges = [
            {"source": "ep-0", "target": f"ep-{j}", "value": "supports"}
            for j in range(1, n_episodes)
        ][: min(n_links, max_edges)]
        init_blocks.append(
            f"""
  (function() {{
    var dom = document.getElementById('graph-{sid}');
    if (!dom) return;
    var chart = echarts.init(dom);
    chart.setOption({{
      tooltip: {{}},
      legend: [{{data: ['episode']}}],
      series: [{{
        type: 'graph', layout: 'force', roam: true,
        categories: [{{name: 'episode'}}],
        data: {nodes},
        links: {edges},
        label: {{show: true, position: 'bottom'}},
        force: {{repulsion: 200}}
      }}]
    }});
  }})();"""
        )
    return "\n".join(init_blocks)


# ---------------------------------------------------------------------------
# 顶层渲染
# ---------------------------------------------------------------------------


def render_projection(projection: EvidenceProjection) -> str:
    """EvidenceProjection → 自包含 HTML（确定性：同输入两次渲染逐字节一致）。

    Raises:
        ValueError: projection 结构非法（缺场景 / 场景字段缺失）——renderer 也 fail-closed。
    """
    scenarios = projection.get("scenarios")
    if not isinstance(scenarios, tuple) or not scenarios:
        raise ValueError("EvidenceProjection.scenarios 为空或非法（fail-closed）")
    meta = projection.get("meta", {})
    scenario_blocks = "".join(_render_scenario(s) for s in scenarios)
    graph_script = _render_graph_script(projection)
    echarts = _echarts_inline()

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Runtime Evidence Explorer — SilverShield</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;
         background:#f7f8fa; color:#1c2733; margin:0; padding:24px; }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2.scenario-title {{ font-size: 17px; margin: 28px 0 8px; }}
  h3.view-anchor {{ font-size: 14px; color:#3b4a5a; border-left: 4px solid #4a90d9;
                    padding-left: 8px; margin: 20px 0 10px; }}
  .subtitle {{ color:#3b4a5a; font-weight:600; }}
  .muted {{ color:#6b7a8a; font-size: 12px; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:10px;
            font-size:12px; color:#fff; }}
  .badge.ok {{ background:#2e9e6b; }}
  .badge.fail {{ background:#d64541; }}
  .meta-card {{ background:#fff; border:1px solid #e3e8ee; border-radius:8px;
                padding:12px 16px; margin:12px 0; }}
  .scenario {{ background:#fff; border:1px solid #e3e8ee; border-radius:10px;
               padding:16px 20px; margin:20px 0; }}
  /* Timeline */
  .timeline {{ list-style:none; margin:0; padding:0 0 0 18px;
               border-left:2px solid #d8dee6; }}
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
  /* Decision cards */
  .dc-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
              gap:10px; }}
  .dc-card {{ background:#f4f7fb; border:1px solid #e3e8ee; border-radius:8px;
              padding:10px 12px; }}
  .dc-label {{ font-size:12px; font-weight:600; }}
  .dc-value {{ margin-top:4px; font-size:14px; }}
  /* Graph */
  .graph-box {{ border:1px solid #e3e8ee; border-radius:8px; background:#fbfcfe; }}
  /* Gate */
  .gate-table {{ border-collapse:collapse; margin:8px 0; width:100%; }}
  .gate-table th, .gate-table td {{ border:1px solid #e3e8ee; padding:6px 10px;
                                     text-align:left; font-size:13px; }}
  .gate-table th {{ background:#f0f4f9; }}
  code {{ background:#eef2f7; border-radius:4px; padding:1px 5px; font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Runtime Evidence Explorer</h1>
  <p class="muted">SilverShield · ADR-0035 Evidence Presentation Layer · 运行证据探索器</p>
  <div class="meta-card">
    generated_at: <code>{_esc(meta.get('generated_at', '(unknown)'))}</code> ·
    scenarios: {meta.get('scenario_count', 0)} ·
    数据源: ADR-0034 IntegrationReport artifact（只读投影，禁 synthetic node）
  </div>
  {scenario_blocks}
</div>
<script>
{echarts}
</script>
<script>
{graph_script}
</script>
</body>
</html>
"""


__all__ = ["render_projection"]
