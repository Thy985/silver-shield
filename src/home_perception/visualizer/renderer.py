"""ADR-0035 D4 · Renderer：EvidenceProjection → 自包含单页 HTML。

**渲染层只消费 ``EvidenceProjection``**（D2 硬规则 1：不直接读 artifact JSON）。

设计（ADR-0035 D4/D6/D7/D8）：
- **自包含**：ECharts 从 ``assets/echarts.min.js`` 内联进 HTML（零外部网络依赖，
  artifact 上传后浏览器直开）；
- **确定性**：HTML 不含当前时间/随机数（D8：同 projection 两次渲染逐字节一致）；
- **脱敏**：只渲染 projection 白名单字段（D7：无路径 / 无设备序列号 / 无 PII）；
- **四视图**：Timeline（CSS 垂直时间轴）/ Decision Explanation（卡片）/
  Cross Modal Graph（ECharts graph，links>0 时）/ Fingerprint-Gate（表格）；
- **自解释层**（D1.5 补丁）：每场景「一句话结论」先行、全局仿真横幅、
  术语对照表（中英）、stage/事件/决策值中文翻译、图例——**只翻译、不编造**，
  翻译表是纯展示常量，翻译不到的值回退原文；
- 视图块带稳定 id 锚点（``timeline-<sid>`` 视图锚点 / ``timeline-list-<sid>`` 重放目标 ul /
  ``decision-<sid>`` / ``graph-<sid>`` / ``gate-<sid>``），供验收测试断言。

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
_REPLAY_FILENAME = "replay.js"

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
    "evidence": ("观测证据 Observation Evidence", "#378ADD"),
    "reasoning": ("决策推理 Decision Reasoning", "#7F77DD"),
    "outcome": ("决策结论 Decision Outcome", "#D85A30"),
}

# ---------------------------------------------------------------------------
# 自解释层（D1.5 补丁）：把 machine 枚举翻译成人话，**只翻译、不新增事实**。
# 纯展示层常量——不改变证据内容，翻译不到的值回退原文（fail-open 于展示层）。
# ---------------------------------------------------------------------------

# stage → 中文注释（追加式：保留英文标识，供测试/审计继续引用原文）。
_STAGE_ZH = {
    "perception": "perception 感知",
    "decision": "decision 决策",
    "notification": "notification 通知",
    "memory": "memory 记忆",
    "cross_modal": "cross_modal 跨模态",
    "observability": "observability 可观测",
}

# D2.2 Causal Highlight：timeline 的 stage 级时间轴 与 Evidence Graph 的实体级
# 因果图 之间唯一的桥接键（二者无共享 id）。timeline 每个节点按 stage 映射到一个
# graph 实体类别，play/点击 step 时高亮该类别的 graph 节点（及其因果边）。
# 键集合与 _STAGE_COLOR / _STAGE_ZH 严格一致；值集合与 _CAT_TYPES（graph 类别）一致。
_STAGE_TO_GRAPH_CATEGORY = {
    "perception": "Event",
    "decision": "Decision",
    "notification": "Action",
    "memory": "Episode",
    "cross_modal": "Link",
    "observability": "Scenario",
}

# 感知事件枚举 → 通俗中文（与 ADR-0031/0034 语义一致，仅翻译枚举值）。
_EVENT_ZH = {
    "abnormal_dwell": "异常停留",
    "elderly_dwell": "老人停留异常",
    "fall": "跌倒",
    "abnormal_audio": "异常声音",
}

# 决策值（reasoning/outcome 枚举）→ 通俗中文。
_VALUE_ZH = {
    "WARN": "需关注（WARN）",
    "SUPPRESS": "已抑制（SUPPRESS）",
    "LOW": "低风险（LOW）",
    "HIGH": "高风险（HIGH）",
    "NOTIFY_FAMILY": "通知家属（NOTIFY_FAMILY）",
    "LOG_ONLY": "仅记录（LOG_ONLY）",
    "MONITOR": "持续关注（MONITOR）",
    "CREATE_COMMUNITY_TASK": "创建社区任务（CREATE_COMMUNITY_TASK）",
}

# 全局仿真横幅（醒目提示，防把演示数据误读为真实报警）。
_SIM_BANNER = (
    "<div class='sim-banner'>注意：本页为仿真（SIMULATED）演示数据——"
    "全部事件由测试场景生成，非真实设备实时报警。</div>"
)

# 页面底部术语对照表（中英对照，供非技术读者查阅）。
_GLOSSARY = [
    ("SIMULATED", "仿真数据（由测试场景生成，非真实设备）"),
    ("provenance / source", "证据出处（每条结论可追溯到的原始数据位置）"),
    ("Fingerprint", "数据指纹（同一输入必产出同一指纹，用于校验版本一致性）"),
    ("Gate verdict", "门禁判定（整体是否通过验收）"),
    ("blocking", "阻断级（该项不通过则整体不通过）"),
    ("degraded", "降级（部分非关键项未达标但未阻断）"),
    ("observed_from", "由……观测到（事件来自哪个感知源）"),
    ("caused_by", "由……导致（决策由哪个事件触发）"),
    ("triggered", "触发（动作由哪个决策引发）"),
    ("stored_as", "存入（结果如何写入记忆）"),
    ("supports", "佐证（跨模态证据之间的相互印证）"),
    ("Decision / Episode / Link", "决策 / 记忆片段 / 关联"),
]


def _esc(value: object) -> str:
    return html.escape(str(value))


def _esc_js(sid: str) -> str:
    """JS 字符串层转义（评审 R2-#6）：json.dumps 自动转义引号/反斜杠/换行。

    HTML 层用 ``_esc``（html.escape），JS 层必须用 JSON 字符串语义——
    同一 sid 在两层的转义策略不同，禁止混用。
    """
    return json.dumps(sid)


def _sanitize_for_js(s: str) -> str:
    """HTML ``<script>`` 解析期安全清洗（评审 R4-安全）：

    浏览器在解析 ``<script>`` 内容时按字面 ``</script`` 终结，无论它出现在
    JS 字符串还是 ``<script type="application/json">`` 的数据里。``json.dumps``
    只转义引号/反斜杠，不碰 ``</``，故这里把 ``</`` 改写成 ``<\\/``——
    HTML 解析期不再命中脚本终结，``JSON.parse`` 又能把 ``\\/`` 还原成 ``/``，
    实现「嵌入安全 + 解码无损」双赢。
    """
    return s.replace("</", "<\\/")


def _echarts_inline() -> str:
    """内联 ECharts（缺失时降级为空串——图视图显示降级提示而非崩溃）。"""
    p = _ASSETS_DIR / _ECHARTS_FILENAME
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def _replay_inline() -> str:
    """内联 D2.1 Replay 引擎（缺失时降级为空串——控制条不绑定，时间轴仍静态可读）。"""
    p = _ASSETS_DIR / _REPLAY_FILENAME
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 视图块
# ---------------------------------------------------------------------------


def _render_timeline(scenario: ScenarioEvidence) -> str:
    """D2.1 Replay：Timeline 改为「控制条 + 数据驱动 DOM 骨架」。

    控制条按钮（id 带 scenario_id 后缀）由 vendored ``replay.js`` 绑定；每个节点带
    ``data-idx`` 供 replay.js 高亮。节点文本仍由 projection 数据渲染（只翻译、不编造，
    同 D1.5 纪律）。replay.js 缺失时控制条按钮无效、时间轴仍静态可读（降级不崩溃）。
    """
    nodes = scenario["timeline"]
    sid_html = _esc(scenario["scenario_id"])
    if not nodes:
        return "<p class='muted'>无时间轴节点（artifact 无 stage 数据）</p>"
    items = []
    for idx, node in enumerate(nodes):
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
            <li class="tl-item" data-step="{_esc(node['timestamp'])}" data-idx="{idx}">
              <span class="tl-dot" style="background:{color}"></span>
              <div class="tl-body">
                <div class="tl-head">
                  <span class="tl-step">{_esc(node['timestamp'])}</span>
                  <span class="tl-stage" style="color:{color}">{_esc(_STAGE_ZH.get(node['stage'], node['stage']))}</span>
                  <span class="tl-kind">{_esc(kind)}</span>
                  <span class="tl-verdict {verdict_class}">{_esc(node['summary'])}</span>
                </div>
                <div class="tl-meta muted">
                  provenance: {_esc(node['provenance_kind'])} · source: {_esc(node['ref'])}
                </div>
              </div>
            </li>"""
        )
    bar = f"""
      <div class="replay-bar" role="group" aria-label="重放控制">
        <button id="rp-reset-{sid_html}" class="rp-btn" title="重置">⏮</button>
        <button id="rp-prev-{sid_html}" class="rp-btn" title="上一步">◀</button>
        <button id="rp-toggle-{sid_html}" class="rp-btn rp-toggle" title="播放/暂停">▶</button>
        <button id="rp-next-{sid_html}" class="rp-btn" title="下一步">▶▶</button>
        <span class="rp-progress-wrap"><span id="rp-progress-{sid_html}" class="rp-progress"></span></span>
        <span id="rp-progress-label-{sid_html}" class="rp-progress-label">0 / 0</span>
        <label class="rp-speed-label">速度
          <select id="rp-speed-{sid_html}" class="rp-speed">
            <option value="1" selected>1x</option>
            <option value="2">2x</option>
            <option value="4">4x</option>
          </select>
        </label>
      </div>"""
    return bar + f"<ul class='timeline' id='timeline-list-{sid_html}'>{''.join(items)}</ul>"


def _translate_value(v: str) -> str:
    """决策值翻译：整体命中直接翻译；逗号分隔枚举（如 ``MONITOR, NOTIFY_FAMILY``）
    逐项拆分翻译；均未命中回退原文（fail-open 于展示层，不编造）。"""
    if v in _VALUE_ZH:
        return _VALUE_ZH[v]
    parts = [p.strip() for p in v.split(",") if p.strip()]
    if len(parts) > 1 and all(p in _VALUE_ZH for p in parts):
        return "、".join(_VALUE_ZH[p] for p in parts)
    return v


def _render_conclusion(scenario: ScenarioEvidence) -> str:
    """自解释层：一句话结论行（先给结论，再给证据）。

    数据驱动拼接，**不编造**：观测/判定/处置各片段全部来自
    ``decision_evidence`` 的已有值 + 纯展示翻译表；无证据 → 不渲染。
    outcome 片段按 ref 区分「建议动作（recommended_actions）」与
    「实际命令（command_types）」——如实呈现"推荐通知家属但实际仅记录"
    这类契约细节，防非技术读者误读。
    """
    evidence = scenario["decision_evidence"]
    if not evidence:
        return ""
    obs: list[str] = []
    reason: list[str] = []
    recommended: list[str] = []
    commands: list[str] = []
    other_outcome: list[str] = []
    for item in evidence:
        v = str(item["value"])
        kind = item["kind"]
        ref = str(item.get("ref", ""))
        if kind == "evidence":
            obs.append(f"{_EVENT_ZH.get(v, v)}（{v}）" if v in _EVENT_ZH else v)
        elif kind == "reasoning":
            reason.append(_translate_value(v))
        elif "recommended_actions" in ref:
            recommended.append(_translate_value(v))
        elif "command_types" in ref:
            commands.append(_translate_value(v))
        else:
            other_outcome.append(_translate_value(v))
    segs: list[str] = []
    if obs:
        segs.append(f"检测到 {_esc('、'.join(obs))}")
    if reason:
        segs.append(f"判定 {_esc('、'.join(reason))}")
    if recommended:
        segs.append(f"建议动作 {_esc('、'.join(recommended))}")
    if commands:
        segs.append(f"实际命令 {_esc('、'.join(commands))}")
    if other_outcome:
        segs.append(_esc('、'.join(other_outcome)))
    if not segs:
        # 观测与判定均空、outcome 为纯说明文案（如 benign 场景）→ 直接呈现原文。
        return f"<div class='conclusion'>结论：{_esc(evidence[0]['value'])}</div>"
    return "<div class='conclusion'>结论：" + " → ".join(segs) + "</div>"


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
              <div class="dc-value">{_esc(_translate_value(item['value']))}</div>
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
      <p class="muted">Evidence Graph（因果链图）：Scenario 场景 → Event 事件
        （observed_from 由…观测到）→ Decision 决策（caused_by 由…导致）→
        Action 动作（triggered 触发）→ Episode 记忆片段（stored_as 存入）→
        Link 关联（supports 佐证）。点击节点查看详情（数据来源 + 真实性标注）。</p>"""

    sid = scenario["scenario_id"]
    sid_js = _esc_js(f"graph-{sid}")  # 主图容器 id = graph-{sid}（评审 R2-#6 JS 转义）
    sid_scen_js = _esc_js(sid)        # 场景 id：供 window.__Replay.get(sid) 取回 replay 实例
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
        emphasis: {{ focus: 'adjacency' }},
        categories: {json.dumps(categories, ensure_ascii=False)},
        data: {json.dumps(nodes, ensure_ascii=False)},
        links: {json.dumps(edges, ensure_ascii=False)},
        label: {{show: true, position: 'bottom'}},
        force: {{repulsion: 220, edgeLength: 110}}
      }}]
    }});
    // D2.2 Causal Highlight：timeline step ↔ Evidence Graph 实体类别联动高亮。
    // 复用 D2.1 的 onStep()（经 window.__Replay.linkHighlight 订阅）：时间轴播放/点击
    // step 变更时，高亮 graph 中对应实体类别（Event/Decision/…）的节点及其因果边；
    // 反向：点击 graph 节点 → seek 时间轴到该类别对应 step。fail-closed：缺 replay
    // 实例 / 缺 linkHighlight 时静默跳过（图仍静态可读 + 可 hover 高亮，不崩）。
    var rp = (window.__Replay && window.__Replay.get({sid_scen_js})) || null;
    var catToStep = {{}};
    if (rp && rp.nodes) {{
      for (var ci = 0; ci < rp.nodes.length; ci++) {{
        var cc = rp.nodes[ci].category;
        if (cc && !(cc in catToStep)) catToStep[cc] = ci;
      }}
    }}
    function highlightCategory(cat) {{
      if (!chart) return;
      chart.dispatchAction({{type: 'downplay', seriesIndex: 0}});
      if (!cat) return;
      for (var ni = 0; ni < {len(nodes)}; ni++) {{
        if (nodes[ni].ntype === cat) chart.dispatchAction({{type: 'highlight', seriesIndex: 0, dataIndex: ni}});
      }}
    }}
    if (rp && window.__Replay && window.__Replay.linkHighlight) {{
      window.__Replay.linkHighlight({sid_scen_js}, function (cat) {{
        highlightCategory(cat);
      }});
      // 初始态：高亮当前 step（index=0）对应类别，与时间轴初始高亮一致。
      var cur = rp.nodes[rp.index];
      highlightCategory(cur ? cur.category : null);
    }}
    chart.on('click', function (p) {{
      if (!p || p.dataType !== 'node' || !rp) return;
      var cat = p.data && p.data.ntype;
      if (!cat) return;
      var idx = catToStep[cat];
      if (idx == null) return;
      rp.seek(idx);
      if (rp.listEl) {{
        var li = rp.listEl.querySelector('.tl-item[data-idx="' + idx + '"]');
        if (li) li.scrollIntoView({{behavior: 'smooth', block: 'center'}});
      }}
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
            f"<tr><td>{_esc(_STAGE_ZH.get(verdict['name'], verdict['name']))}</td>"
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

      {_render_conclusion(scenario)}

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
    replay_js = _replay_inline()
    # D2.1：每场景 timeline 数据内联为 ``<script type="application/json">`` 数据岛，
    # 交由 vendored replay.js 在客户端 JSON.parse 驱动重放。
    # - 数据岛隔离：timeline 字符串字段里的 ``</script`` 会提前终结脚本，
    #   故经 ``_sanitize_for_js`` 把 ``</`` 改写为 ``<\\/``（JSON.parse 能无损还原）；
    # - 数据来自 projection（确定性），初始态固定（index=0/暂停）→ 同 artifact
    #   两次渲染逐字节一致（D8）；
    # - sid 在 JS 上下文必须经 json.dumps（``_esc_js``），HTML 属性层用 ``_esc``，
    #   两层转义策略不同、禁止混用（评审 R2-#6 / R4-安全）。
    replay_data_tags = "\n".join(
        '<script type="application/json" id="replay-data-{sid}">{data}</script>'.format(
            sid=_esc(s["scenario_id"]),
            data=_sanitize_for_js(
                json.dumps(
                    # D2.2：为每个 timeline 节点附加 stage→graph 类别桥接键，
                    # 供 replay.js onStep→graph 联动高亮（stage 级时间轴 与
                    # 实体级因果图 的桥接；未知 stage 落 null，高亮路径 fail-open）。
                    [
                        {**dict(n), "category": _STAGE_TO_GRAPH_CATEGORY.get(n["stage"])}
                        for n in s["timeline"]
                    ],
                    ensure_ascii=False,
                )
            ),
        )
        for s in scenarios
    )
    # 仅在 replay 引擎存在时才发 init 调用：replay.js 缺失（降级路径）时不绑定，
    # 控制条仍静态可读、页面加载不抛 ReferenceError（评审 R4-测试缺口 3 / 降级纪律）。
    replay_inits = (
        "\n".join(
            "window.__Replay.init({});".format(_esc_js(s["scenario_id"]))
            for s in scenarios
        )
        if replay_js else ""
    )

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
  .sim-banner {{ background:#fff7e6; border:1px solid #f0c36d; color:#7a5a00;
                 border-radius:8px; padding:10px 16px; margin:12px 0;
                 font-size:14px; font-weight:600; }}
  .conclusion {{ background:#eef6ff; border-left:4px solid #4a90d9;
                 border-radius:6px; padding:10px 14px; margin:12px 0;
                 font-size:15px; line-height:1.6; }}
  .glossary {{ margin:24px 0 8px; background:#fff; border:1px solid #e3e8ee;
               border-radius:8px; padding:10px 16px; }}
  .glossary summary {{ cursor:pointer; font-weight:600; color:#3b4a5a; }}
  .glossary ul {{ margin:8px 0 0; padding-left:20px; font-size:13px; }}
  .glossary li {{ margin:3px 0; }}
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
  /* D2.1 Replay 控制条 + 高亮 */
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
  code {{ background:#eef2f7; border-radius:4px; padding:1px 5px; font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Runtime Evidence Explorer</h1>
  <p class="muted">SilverShield · ADR-0035 Evidence Presentation Layer · 运行证据探索器</p>
  {_SIM_BANNER}
  <div class="meta-card">
    generated_at: <code>{_esc(meta.get('generated_at', '(unknown)'))}</code> ·
    scenarios: {meta.get('scenario_count', 0)} ·
    数据源: ADR-0034 IntegrationReport artifact（只读投影，禁 synthetic node）
  </div>
  {''.join(scenario_blocks)}
  <details class="glossary">
    <summary>术语对照表（点开查看）</summary>
    <ul>
      {''.join(f'<li><code>{_esc(k)}</code> — {_esc(v)}</li>' for k, v in _GLOSSARY)}
    </ul>
  </details>
</div>
{replay_data_tags}
<script>
{echarts}
</script>
<script>
{replay_js}
</script>
<script>
{replay_inits}
</script>
<script>
{graph_script}
</script>
</body>
</html>
"""


__all__ = ["render_projection"]
