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
import json
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

# Decision Explanation 卡片类型 → (展示标签, 色值)（评审 R2-#10：模块级常量）。
# D1.5 三分组语义：Observation Evidence（检测）→ Decision Reasoning（推理）→
# Decision Outcome（结论）——WARN 是推理结果而非检测证据（修正混排）。
# 评审 R3-#2：loader 自 D1.5 起只产出 evidence/reasoning/outcome 三种 kind，
# rule/policy 已不可达，删除防阅读歧义。
_DECISION_KINDS = {
    "evidence": ("Observation Evidence", "#378ADD"),
    "reasoning": ("Decision Reasoning", "#7F77DD"),
    "outcome": ("Decision Outcome", "#D85A30"),
}


def _esc(value: object) -> str:
    return html.escape(str(value))


def _esc_js(sid: str) -> str:
    """JS 字符串层转义（评审 R2-#6）：json.dumps 自动转义引号/反斜杠/换行。

    HTML 层用 ``_esc``（html.escape），JS 层必须用 JSON 字符串语义——
    同一 sid 在两层的转义策略不同，禁止混用。
    """
    return json.dumps(sid)


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
    cards = []
    for item in evidence:
        # 评审 R3-#20：schema 已收紧 kind 为三分组闭集（evidence/reasoning/outcome），
        # 兜底路径理论上不可达——保留为"灰色卡片（容错）"，仅防 loader/renderer
        # 契约漂移（与 fail-closed 主路径互补，不掩盖漂移：漂移时会显示原始 kind 灰卡）。
        label, color = _DECISION_KINDS.get(item["kind"], (item["kind"], "#666666"))
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


def _render_evidence_graph(scenario: ScenarioEvidence) -> tuple[str, str]:
    """D1.5 主视图：Evidence Graph 因果链（ECharts graph）→ (html_block, js_init)。

    - 节点/边全部来自 projection 的 ``graph``（loader 已带 ref/provenance_kind）；
    - tooltip 展开 provenance（ref / provenance_kind），边显示关系类型 + ref；
    - 无节点（artifact 空）→ 降级提示（禁 synthetic）；
    - 评审 R3-#3：html 与 js 单函数返回，与 Cross Modal 子图（``_render_graph``）
      对称——顶层只遍历 scenarios 一次，两个函数不再各自重复遍历。
    """
    graph = scenario["graph"]
    if not graph["nodes"]:
        return "<p class='muted'>无证据图节点（artifact 无数据，降级）</p>", ""
    sid_html = _esc(scenario["scenario_id"])  # HTML 属性层转义（评审 R2-#6/#12）
    html_block = f"""
      <div id="graph-{sid_html}" class="graph-box" style="height:420px"
           data-nodes="{len(graph['nodes'])}" data-edges="{len(graph['edges'])}"></div>
      <p class="muted">Evidence Graph：Scenario → Event（observed_from）→ Decision
        （caused_by）→ Action（triggered）→ Episode（stored_as）→ Link（supports）。
        点击节点查看 provenance（ref 溯源 + 真实性标注）。</p>"""

    sid = scenario["scenario_id"]
    sid_js = _esc_js(f"graph-{sid}")  # 主图容器 id = graph-{sid}（评审 R2-#6 JS 转义）
    # 评审 R3-#7：ECharts node.category 必须是 categories 数组的**索引**（字符串
    # 会被解释为 NaN 落到默认色）——显式索引映射 + 保留 ntype 供 tooltip 显示。
    _CAT_TYPES = ("Scenario", "Event", "Decision", "Action", "Episode", "Link")
    cat_index = {t: i for i, t in enumerate(_CAT_TYPES)}
    nodes = [
        {
            "id": n["id"], "name": n["label"],
            "category": cat_index.get(n["type"], 0),
            "ntype": n["type"],  # 供 tooltip 显示节点类型（category 已是索引）
            "symbolSize": {"Scenario": 60, "Event": 48, "Decision": 48,
                           "Action": 48, "Episode": 56, "Link": 56}.get(n["type"], 44),
            "ref": n["ref"], "provenance_kind": n["provenance_kind"],
        }
        for n in graph["nodes"]
    ]
    edges = [
        # 评审 R3-#6（真 Bug）：edge 必须带 ref——tooltip 的 p.data.ref 依赖它，
        # 缺字段会渲染 "undefined"。
        {
            "source": e["source"], "target": e["target"],
            "label": {"show": True, "formatter": e["type"]},
            "ref": e["ref"],
        }
        for e in graph["edges"]
    ]
    categories = [{"name": t} for t in _CAT_TYPES]
    # 评审 R3-#11：nodes/edges/categories 用 json.dumps 输出——label/ref 等
    # 来自 artifact 的字符串被自动 JSON 转义（防未来接入用户输入时 JS 注入），
    # 且保证 JS 端是合法 JSON 字面量（不再依赖 Python repr 的巧合合法性）。
    js_block = f"""
  (function() {{
    var dom = document.getElementById({sid_js});
    if (!dom) return;
    var chart = echarts.init(dom);
    chart.setOption({{
      tooltip: {{
        formatter: function (p) {{
          if (p.dataType === 'edge') return p.data.label.formatter + ' · ' + p.data.ref;
          var d = p.data;
          return '<b>' + d.name + '</b><br/>type: ' + d.ntype +
            '<br/>provenance: ' + d.provenance_kind + '<br/>source: ' + d.ref;
        }}
      }},
      legend: [{{data: {json.dumps(categories, ensure_ascii=False)}}}],
      series: [{{
        type: 'graph', layout: 'force', roam: true,
        categories: {json.dumps(categories, ensure_ascii=False)},
        data: {json.dumps(nodes, ensure_ascii=False)},
        links: {json.dumps(edges, ensure_ascii=False)},
        label: {{show: true, position: 'bottom'}},
        force: {{repulsion: 220, edgeLength: 110}}
      }}]
    }});
  }})();"""
    return html_block, js_block


def _render_graph(scenario: ScenarioEvidence) -> tuple[str, str]:
    """Cross Modal 子图（Evidence Graph 的 supports 视角）→ (html_block, js_init)。

    与 ``_render_evidence_graph`` 对称（评审 R3-#3）：html 与 js 单函数返回，
    顶层只遍历一次。同守卫：n_links<=0 或 n_episodes<=0 返回降级提示（禁
    synthetic，评审 #2）。节点 = 真实 episode 计数；边 = 真实 link 计数与
    节点容量的较小值（画满为止，多余 link 数已在 ``data-links`` 属性与
    降级文案中如实标注，评审 #9）。
    """
    n_links = scenario["counts"]["cross_modal_links"]
    n_episodes = scenario["counts"]["episodes"]
    if n_links <= 0:
        return (
            "<p class='muted'>无跨模态关联（cross_modal_links=0）。图视图按 D2 缺失粒度降级：仅显示计数摘要。</p>",
            "",
        )
    if n_episodes <= 0:
        # 数据异常组合（link 必有 episode 支撑）：禁 synthetic——不画空节点（评审 #2）
        return (
            (
                "<p class='muted'>cross_modal_links&gt;0 但 episodes=0（数据异常，fail-closed 降级）。"
                f"仅显示 link 计数：{n_links}。</p>"
            ),
            "",
        )
    # 从真实 counts 投影（D1 canonical 无 link 级 detail → 节点用计数摘要，不捏造 id）
    sid_html = _esc(scenario["scenario_id"])  # HTML 属性层转义（评审 R2-#6/#12：
    # html.escape 默认转义 & < > " ——引号安全，与 _render_evidence_graph 同纪律）
    html_block = f"""
      <div id="crossmodal-{sid_html}" class="graph-box" style="height:320px"
           data-links="{n_links}" data-episodes="{n_episodes}"></div>
      <p class="muted">Cross Modal 子图（Evidence Graph 的 supports 视角）：{n_episodes} 个 episode
        节点 · {n_links} 条 supports 关联。D1 降级：canonical 无 link 级 detail
        （confidence/time_overlap 未落盘，不渲染），完整关系见 Memory 层。</p>"""

    sid = scenario["scenario_id"]
    sid_js = _esc_js(f"crossmodal-{sid}")  # Cross Modal 子图容器（评审 R2-#6 JS 转义）
    # 节点 = 真实 episode 计数；每个可视化节点带溯源元数据（评审 R2-#3 方案 A）：
    # provenance_kind="SIMULATED" + ref 指向 counts（与 timeline 节点同语义，
    # 防"合成的可视化节点无证据视角"——episode_id 等未落盘字段仍不渲染）。
    nodes = [
        {"id": f"ep-{i}", "name": f"Episode #{i + 1}", "symbolSize": 48,
         "category": "episode",
         "provenance_kind": "SIMULATED",
         "ref": f"{sid}.canonical.json#artifacts.counts.episodes"}
        for i in range(n_episodes)
    ]
    # 边数 = min(link 计数, 节点可容纳的支撑边数)——如实反映，不伪造多余边
    max_edges = max(n_episodes - 1, 1)
    edges = [
        {"source": "ep-0", "target": f"ep-{j}", "value": "supports"}
        for j in range(1, n_episodes)
    ][: min(n_links, max_edges)]
    # 评审 R3-#11：json.dumps 输出（ref 含 sid，来自文件名，JSON 转义防注入）
    js_block = f"""
  (function() {{
    var dom = document.getElementById({sid_js});
    if (!dom) return;
    var chart = echarts.init(dom);
    chart.setOption({{
      tooltip: {{}},
      legend: [{{data: ['episode']}}],
      series: [{{
        type: 'graph', layout: 'force', roam: true,
        categories: [{{name: 'episode'}}],
        data: {json.dumps(nodes, ensure_ascii=False)},
        links: {json.dumps(edges, ensure_ascii=False)},
        label: {{show: true, position: 'bottom'}},
        force: {{repulsion: 200}}
      }}]
    }});
  }})();"""
    return html_block, js_block


def _render_gate(scenario: ScenarioEvidence) -> str:
    fp = scenario["fingerprints"]
    rows = []
    for verdict in scenario["gate"]:
        # failure_code 与 name/severity 同纪律走 _esc（评审 R2-#5）
        mark = "✅" if verdict["passed"] else f"❌ {_esc(verdict['failure_code'] or '')}"
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


def _render_scenario(scenario: ScenarioEvidence) -> tuple[str, str]:
    """单场景 HTML + 该场景的图初始化 JS（评审 R3-#3：一次遍历产出两块）。"""
    status = "PASS" if scenario["ok"] else "FAIL"
    status_class = "ok" if scenario["ok"] else "fail"
    graph_html, graph_js = _render_evidence_graph(scenario)
    cm_html, cm_js = _render_graph(scenario)
    html_block = f"""
    <section class="scenario">
      <h2 class="scenario-title">
        <span class="badge {status_class}">{status}</span>
        <code>{_esc(scenario['scenario_id'])}</code>
        <span class="muted">mode={_esc(scenario['mode'])} · frames={scenario['n_frames']}</span>
      </h2>

      <h3 id="view-graph-{_esc(scenario['scenario_id'])}" class="view-anchor">① Evidence Graph（因果链）</h3>
      {graph_html}

      <h3 id="timeline-{_esc(scenario['scenario_id'])}" class="view-anchor">② Scenario Replay Timeline</h3>
      {_render_timeline(scenario)}

      <h3 id="decision-{_esc(scenario['scenario_id'])}" class="view-anchor">③ Decision Explanation（为什么报警）</h3>
      {_render_decision(scenario)}

      <h3 id="view-crossmodal-{_esc(scenario['scenario_id'])}" class="view-anchor">④ Cross Modal Graph（supports 子图）</h3>
      {cm_html}

      <h3 id="gate-{_esc(scenario['scenario_id'])}" class="view-anchor">⑤ Fingerprint / Gate</h3>
      {_render_gate(scenario)}
    </section>"""
    return html_block, f"{graph_js}\n{cm_js}".strip()


# ---------------------------------------------------------------------------
# 顶层渲染
# ---------------------------------------------------------------------------


def render_projection(projection: EvidenceProjection) -> str:
    """EvidenceProjection → 自包含 HTML（确定性：同输入两次渲染逐字节一致）。

    Raises:
        ValueError: projection 结构非法（缺场景 / 场景字段缺失 / 场景数超上限）
            ——renderer 也 fail-closed。
    """
    scenarios = projection.get("scenarios")
    if not isinstance(scenarios, tuple) or not scenarios:
        raise ValueError("EvidenceProjection.scenarios 为空或非法（fail-closed）")
    # 评审 R3-#10：IIFE 脚本随场景数线性增长——超上限直接 fail-closed 拒绝
    # （正常 artifact 场景集 ≤ 几十个，128 是宽松护栏，防异常巨型输入撑爆 HTML）。
    if len(scenarios) > 128:
        raise ValueError(f"EvidenceProjection.scenarios 数量超上限（{len(scenarios)}>128，fail-closed）")
    meta = projection.get("meta", {})
    scenario_blocks: list[str] = []
    graph_blocks: list[str] = []
    for s in scenarios:
        html_block, js_block = _render_scenario(s)
        scenario_blocks.append(html_block)
        if js_block:
            graph_blocks.append(js_block)
    graph_script = "\n".join(graph_blocks)
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
  {''.join(scenario_blocks)}
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
