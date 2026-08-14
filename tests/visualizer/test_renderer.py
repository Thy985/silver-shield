"""ADR-0035 D4/D6/D7/D8 · 渲染测试：四视图 / 确定性 / 脱敏 / 自包含。

对应验收：1（输入契约）、2（四视图）、4（脱敏）、5（确定性）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from home_perception.visualizer import load_evidence_projection, render_projection

from .conftest import make_artifacts


def _render(artifacts_dir) -> str:
    return render_projection(load_evidence_projection(artifacts_dir))


def test_render_four_views_anchors(tmp_path):
    """四视图全覆盖：每场景含 timeline/decision/graph/gate 锚点（验收 2）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1", "sw_t2"))
    html = _render(d)
    for sid in ("sw_t1", "sw_t2"):
        assert f'id="timeline-{sid}"' in html
        assert f'id="decision-{sid}"' in html
        assert f'id="graph-{sid}"' in html
        assert f'id="gate-{sid}"' in html
    # Timeline 全链：stage 判定 + count 摘要节点存在
    assert "stage `perception` PASS" in html
    assert "perception events: 1" in html
    # Fingerprint / Gate 视图
    assert "expectation_fingerprint" in html
    assert "loop_fingerprint" in html
    # Decision Explanation
    assert "为什么报警？" in html
    assert "abnormal_dwell" in html


def test_render_deterministic(tmp_path):
    """确定性：同 projection 两次渲染逐字节一致（验收 5 / D8）。"""
    d = make_artifacts(tmp_path / "a")
    html1 = _render(d)
    html2 = _render(d)
    assert html1 == html2


def test_render_self_contained_inline_echarts(tmp_path):
    """自包含：ECharts 内联（无外部 src 引用），浏览器直开（验收 1 / D4）。"""
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    # echarts.min.js 的 Apache banner 内联进 HTML（vendored asset 的稳定标志）
    assert "Licensed to the Apache Software Foundation" in html
    assert "<script src=" not in html  # 零外部脚本标签
    assert 'src="http' not in html  # 零外部资源引用


def test_render_desensitized(tmp_path):
    """脱敏：HTML 不含路径 / PII / 设备序列号（验收 4 / D7）。

    注意：vendored echarts.min.js 是压缩代码（含 ``C:``/``D:`` 等变量冒号），
    故路径类断言只作用于**渲染正文**（``<script>`` 之前），第三方资产不参与判定。
    """
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    body = html.split("<script>")[0]  # 渲染正文（不含内联 echarts / graph 脚本）
    assert "scenarios_dir" not in html
    assert ".yaml" not in html
    assert "canonical_report" not in html
    # 路径泄漏检查（仅正文）：artifact 目录（D:/C:/tmp/...）不得进入渲染内容
    assert "D:\\" not in body and "C:" not in body and "D:/" not in body
    assert "device_id" not in html and "serial" not in html.lower()


def test_render_graph_degradation_when_no_links(tmp_path):
    """Graph 降级：cross_modal_links=0 → 降级提示而非空图（D2 缺失粒度降级）。"""
    d = make_artifacts(tmp_path / "a")
    # 覆盖 canonical：cross_modal_links=0
    import json

    canon = d / "sw_t1.canonical.json"
    data = json.loads(canon.read_text(encoding="utf-8"))
    data["artifacts"]["counts"]["cross_modal_links"] = 0
    canon.write_text(json.dumps(data), encoding="utf-8")
    html = _render(d)
    assert "无跨模态关联" in html
    assert 'data-links="0"' not in html  # graph 块不渲染（降级）


def test_render_graph_when_links_present(tmp_path):
    """Graph 渲染：cross_modal_links>0 → 图容器 + 数据属性（验收 2 的 Graph 视图）。"""
    d = make_artifacts(tmp_path / "a")  # fixture 默认 links=1
    html = _render(d)
    assert 'data-links="1"' in html
    assert "supports 关联" in html


def test_render_graph_episodes_zero_degraded(tmp_path):
    """n_episodes=0 但 n_links>0（数据异常组合）→ 降级提示，禁 synthetic 空节点（评审 #2）。"""
    d = make_artifacts(tmp_path / "a")
    import json

    canon = d / "sw_t1.canonical.json"
    data = json.loads(canon.read_text(encoding="utf-8"))
    data["artifacts"]["counts"]["episodes"] = 0
    data["artifacts"]["counts"]["cross_modal_links"] = 3
    canon.write_text(json.dumps(data), encoding="utf-8")
    html = _render(d)
    assert "episodes=0" in html  # 降级提示
    assert 'data-links="3"' not in html  # 不渲染图容器（禁 synthetic）
    assert "Episode #1" not in html  # 无凭空节点


def test_render_graph_large_episode_count_bounded(tmp_path):
    """大 episode 数（n_episodes=10000）→ HTML 体积有界、图脚本不爆炸（评审 #11）。"""
    d = make_artifacts(tmp_path / "a")
    import json

    canon = d / "sw_t1.canonical.json"
    data = json.loads(canon.read_text(encoding="utf-8"))
    data["artifacts"]["counts"]["episodes"] = 10000
    data["artifacts"]["counts"]["cross_modal_links"] = 5000
    canon.write_text(json.dumps(data), encoding="utf-8")
    html = _render(d)
    assert 'data-episodes="10000"' in html
    assert "Episode #10000" in html  # 最大节点名存在
    # 有界：~1MB echarts + 10k 节点（含 provenance_kind/ref 元数据）< 4MB
    assert len(html) < 4_000_000


def test_render_empty_projection_raises():
    """空 projection（scenarios=()）→ render_projection 抛 ValueError（fail-closed，评审 #11）。"""
    import pytest

    from home_perception.visualizer import render_projection

    with pytest.raises(ValueError, match="scenarios"):
        render_projection(
            {"meta": {"generated_at": "t", "scenario_count": 0}, "scenarios": ()}
        )


def test_render_unicode_scenario_id_safe(tmp_path):
    """unicode scenario_id 渲染不崩、HTML 转义安全（评审 #11）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_场景_α",))
    html = _render(d)
    assert "sw_场景_α" in html  # 原样呈现（scenario_id 来自 fixture，属白名单字段）


def test_render_graph_id_unique(tmp_path):
    """HTML id 唯一性：graph 容器 div 与视图标题 h3 不得共用 id（评审 R2-#2）。"""
    d = make_artifacts(tmp_path / "a")  # fixture 默认 links=1
    html = _render(d)
    # 容器 div 的 id="graph-sw_t1" 必须唯一（h3 已改 id="view-graph-sw_t1"）
    assert len(re.findall(r'id="graph-sw_t1"', html)) == 1
    assert len(re.findall(r'id="view-graph-sw_t1"', html)) == 1


def test_render_graph_nodes_have_metadata(tmp_path):
    """graph 可视化节点带 provenance_kind + ref 溯源元数据（评审 R2-#3 方案 A）。"""
    d = make_artifacts(tmp_path / "a")  # fixture：episodes=2, links=1
    html = _render(d)
    # 评审 R3-#11：nodes 走 json.dumps 输出（双引号 JSON），断言按实际格式
    assert '"provenance_kind": "SIMULATED"' in html
    assert "artifacts.counts.episodes" in html  # ref 溯源到 counts
    assert "ep-0" in html  # 节点 id（ep-0）作为 JS 节点 id 出现


def test_render_gate_failure_code_none_renders_empty(tmp_path):
    """failure_code=None → 渲染为空而非 "None"（评审 R2-#5）。"""
    d = make_artifacts(tmp_path / "a")
    import json as _json

    gate = d / "sw_t1.gate.json"
    data = _json.loads(gate.read_text(encoding="utf-8"))
    data["verdicts"][0]["passed"] = False
    data["verdicts"][0]["failure_code"] = None
    gate.write_text(_json.dumps(data), encoding="utf-8")
    html = _render(d)
    assert "❌ None" not in html
    assert "❌" in html  # 失败标记仍显示


def test_render_scenario_id_with_quotes_safe(tmp_path):
    """引号 scenario_id：HTML 层转义 + JS 层 json 转义，不破坏文档（评审 R2-#6）。

    注入向量：单引号 + 括号（Windows 文件名禁 ``"`` ``<`` 等字符）。
    json.dumps 以双引号包裹 sid，单引号在 JSON 字符串内不闭合——渲染后
    ``getElementById("sw_t1'alert(1)")`` 是完整字符串，alert( 不执行。
    """
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1'alert(1)",))
    html = _render(d)
    # JS 层：getElementById 参数是 JSON 包裹的完整字符串（主图容器 id = graph-{sid}）
    assert 'getElementById("graph-sw_t1\'alert(1)")' in html
    # 无独立 alert 语句（alert(1); 不会出现在任何位置）
    assert "alert(1);" not in html


def test_render_n_frames_zero_valid(tmp_path):
    """n_frames=0 是合法边界（非负即通过；评审 R2-#11）。"""
    d = make_artifacts(tmp_path / "a")
    canon = d / "sw_t1.canonical.json"
    data = json.loads(canon.read_text(encoding="utf-8"))
    data["n_frames"] = 0
    canon.write_text(json.dumps(data), encoding="utf-8")
    html = _render(d)
    assert "frames=0" in html


def test_render_evidence_graph_main(tmp_path):
    """D1.5 主视图：Evidence Graph 容器 + 因果链标签 + 边类型（验收 2 扩展）。"""
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    assert 'data-nodes="6"' in html  # Scenario+Event+Decision+Action+Episode+Link
    assert 'data-edges="5"' in html  # observed_from/caused_by/triggered/stored_as/supports
    # 因果链标签出现在主图说明
    assert "observed_from" in html
    assert "caused_by" in html
    assert "triggered" in html
    assert "stored_as" in html
    # 主图容器 id 唯一（graph-sw_t1 仅主图，cross modal 用 crossmodal-sw_t1）
    assert len(re.findall(r'id="graph-sw_t1"', html)) == 1
    assert len(re.findall(r'id="crossmodal-sw_t1"', html)) == 1


def test_render_decision_three_groups(tmp_path):
    """Decision Explanation 三分组语义（Observation/Reasoning/Outcome，D1.5）。

    评审 R3-#13：断言必须验证**分组归属**——abnormal_dwell（事件类型）在
    Observation 卡片、WARN（trace outcome）在 Reasoning 卡片，防回归到
    "WARN 混排为检测证据"的旧语义。
    """
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    assert "观测证据 Observation Evidence" in html
    assert "决策推理 Decision Reasoning" in html
    assert "决策结论 Decision Outcome" in html
    # 卡片结构：<div class="dc-label">...</div><div class="dc-value">...</div>
    # 同一展示组可有多个卡片（如 Reasoning 含 trace outcome + risk level）——
    # 按列表断言"组内任一卡片命中"，不用 dict 覆盖（评审 R3-#13）。
    cards = re.findall(
        r'<div class="dc-label"[^>]*>([^<]+)</div>\s*<div class="dc-value">([^<]*)</div>',
        html,
    )
    obs_values = [v.strip() for label, v in cards if label.strip() == "观测证据 Observation Evidence"]
    reason_values = [v.strip() for label, v in cards if label.strip() == "决策推理 Decision Reasoning"]
    assert any("abnormal_dwell" in v for v in obs_values), f"abnormal_dwell 应归 Observation，实际 {obs_values!r}"
    assert any("WARN" in v for v in reason_values), f"WARN 应归 Reasoning，实际 {reason_values!r}"
    assert all("WARN" not in v for v in obs_values), "WARN 不得混入检测证据（Observation）"


def test_render_evidence_graph_js_data_includes_refs(tmp_path):
    """主图 ECharts JS 数据里节点/边的 ref 真实存在（评审 R3-#16，bug #6 回归防线）。

    bug #6：edge dict 曾漏序列化 ref，tooltip 渲染 "undefined"——此测试直接
    断言 artifact 溯源 ref 字符串出现在 <script> 段的 ECharts data/links 中。
    """
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    # 节点 ref（event_types[0]）与边 ref（trace_outcome_kinds[0] 等）都进入 JS 数据
    assert "artifacts.event_types[0]" in html
    assert "artifacts.trace_outcome_kinds[0]" in html
    assert "artifacts.recommended_actions[0]" in html
    # 边 ref 必须出现在 JS 段（tooltip p.data.ref 依赖）——且不在 "undefined" 形态
    assert '"ref": "sw_t1.canonical.json#artifacts.trace_outcome_kinds[0]"' in html
    assert "p.data.ref" in html  # tooltip edge 分支仍引用 ref


def test_render_self_explanation_conclusion(tmp_path):
    """自解释层：每场景顶部有一句话结论行（先给结论，再给证据）。"""
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    # 结论行存在且数据驱动（含翻译后的通俗中文 + 原文括注）
    assert "结论：" in html
    assert "异常停留" in html and "abnormal_dwell" in html
    # 因果链片段齐全：检测到 → 判定 → 处置
    assert "检测到" in html and "判定" in html and "建议动作" in html
    # "推荐通知家属但实际仅记录"的契约细节如实呈现（防误读）
    assert "通知家属" in html and "仅记录" in html


def test_render_self_explanation_conclusion_data_driven(tmp_path):
    """自解释层：结论行完全由 artifact 数据驱动（每片段带原文括注，不编造）。"""
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    # 每个通俗词都带英文原文括注 → 证明来自数据而非硬编码
    assert "异常停留（abnormal_dwell）" in html
    assert "需关注（WARN）" in html
    assert "低风险（LOW）" in html
    assert "通知家属（NOTIFY_FAMILY）" in html
    assert "仅记录（LOG_ONLY）" in html
    # "推荐通知家属但实际仅记录"如实呈现（防误读契约细节）
    assert "建议动作" in html and "实际命令" in html


def test_render_self_explanation_sim_banner(tmp_path):
    """自解释层：全局仿真横幅（防误读为真实报警）。"""
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    assert "仿真（SIMULATED）演示数据" in html
    assert "非真实设备实时报警" in html


def test_render_self_explanation_glossary(tmp_path):
    """自解释层：底部术语对照表（中英对照）。"""
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    assert "术语对照表" in html
    assert "observed_from" in html and "由……观测到" in html
    assert "supports" in html and "佐证" in html
    assert "SIMULATED" in html


def test_render_self_explanation_stage_zh(tmp_path):
    """自解释层：timeline 与 gate 表的 stage 名带中文注释。"""
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    assert "perception 感知" in html
    assert "decision 决策" in html
    assert "notification 通知" in html
    assert "memory 记忆" in html
    assert "observability 可观测" in html


def test_render_self_explanation_decision_values_translated(tmp_path):
    """自解释层：决策卡片 value 显示通俗中文（保留原文括注）。"""
    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    # WARN/LOW/NOTIFY_FAMILY/LOG_ONLY 均带中文翻译
    assert "需关注（WARN）" in html
    assert "低风险（LOW）" in html
    assert "通知家属（NOTIFY_FAMILY）" in html
    assert "仅记录（LOG_ONLY）" in html


def test_render_self_explanation_comma_list_translation(tmp_path):
    """自解释层：逗号分隔枚举（如 recommended_actions 多值）逐项翻译。"""
    d = make_artifacts(tmp_path / "a")
    # 覆写 canonical：recommended_actions 用逗号多值枚举
    import json as _json

    p = d / "sw_t1.canonical.json"
    data = _json.loads(p.read_text(encoding="utf-8"))
    data["artifacts"]["recommended_actions"] = ["MONITOR, NOTIFY_FAMILY"]
    p.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    html = _render(d)
    # 逗号枚举被拆分翻译：持续关注（MONITOR）、通知家属（NOTIFY_FAMILY）
    assert "持续关注（MONITOR）" in html
    assert "通知家属（NOTIFY_FAMILY）" in html
    assert "建议动作 持续关注（MONITOR）、通知家属（NOTIFY_FAMILY）" in html


def test_render_replay_bar_present(tmp_path):
    """D2.1：每场景含重放控制条 DOM（按钮 id 带 sid 后缀）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    assert 'id="rp-toggle-sw_t1"' in html
    assert 'id="rp-reset-sw_t1"' in html
    assert 'id="rp-prev-sw_t1"' in html
    assert 'id="rp-next-sw_t1"' in html
    assert 'id="rp-speed-sw_t1"' in html
    assert 'id="rp-progress-sw_t1"' in html


def test_render_replay_inline_data(tmp_path):
    """D2.1：timeline 数据内联为 application/json 数据岛（init 仅传 sid，数据走 JSON 岛）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    # init 仅传 sid（数据从 replay-data 数据岛读取，与 <script> 终结隔离）
    assert 'window.__Replay.init("sw_t1");' in html
    # 数据岛存在且为 application/json
    assert 'type="application/json" id="replay-data-sw_t1"' in html
    # 内联数据含确定性 step 锚点 S1 + 真实性标注（来自 loader 投影，非拼装）
    assert '"timestamp": "S1"' in html
    assert '"provenance_kind": "SIMULATED"' in html


def test_render_replay_js_vendored(tmp_path):
    """D2.1：replay.js 已 vendored 并内联（含 __Replay 定义），零外部网络依赖。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    # replay 引擎定义内联进 HTML（global.__Replay = {...} 由 IIFE 挂载到 window）
    assert "global.__Replay = {" in html


def test_render_replay_timeline_data_idx(tmp_path):
    """D2.1：timeline 节点带 data-idx/data-step，供 replay.js 高亮（不丢溯源）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    assert 'class="tl-item"' in html
    assert 'data-idx="0"' in html
    assert 'data-step="S1"' in html
    # 溯源信息仍在（D2.4 前置：重放态下 ref 不丢）
    assert "provenance:" in html and "source:" in html


def test_render_replay_id_unique(tmp_path):
    """D2.1（评审 R4-Bug 最大可用性）：timeline 视图锚点 id 与重放目标 ul id 不撞名。

    回归：此前 _render_scenario 的 H3 锚点与 _render_timeline 的 <ul> 共用
    id="timeline-{sid}"，导致 replay.js 取到 H3 而非 <ul>，querySelectorAll
    空 -> 重放完全失效。修复后锚点为 timeline-{sid}、列表为 timeline-list-{sid}，
    各出现恰好一次。
    """
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    # 视图锚点：恰好一次
    assert len(re.findall(r'id="timeline-sw_t1"', html)) == 1
    # 重放目标 ul：恰好一次，且确为 <ul ... id='timeline-list-sw_t1'>
    # （ul 属性用单引号，H3 锚点用双引号，二者 id 不撞名）
    assert len(re.findall(r"id='timeline-list-sw_t1'", html)) == 1
    assert "<ul class='timeline' id='timeline-list-sw_t1'>" in html
    # replay.js 绑定到 list id（非 H3 锚点）
    assert "timeline-list-sw_t1" in html


def test_render_replay_xss_script_termination(tmp_path):
    """D2.1（评审 R4-安全）：timeline 字符串字段含 </script> 不得提前终结脚本。

    注入恶意 summary（fixture 注释里出现 </script> 字面量），验证：
    - HTML 中无裸 </script><img（否则脚本被切碎、后续 HTML 当 JS 跑）；
    - 转义形态 <\\/script 出现（HTML 解析期不命中脚本终结，JSON.parse 无损还原）；
    - 数据岛仍为合法 application/json，init 调用照常存在。
    """
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    proj = load_evidence_projection(d)
    for s in proj["scenarios"]:
        for n in s["timeline"]:
            n["summary"] = "</script><img src=x onerror=alert(1)>"
    html = render_projection(proj)
    # 防御：不得出现裸 </script><img（脚本提前终结）
    assert "</script><img" not in html
    # 转义形态必须出现（<\\/script）
    assert "<\\/script" in html
    # 数据岛与 init 仍正常
    assert 'type="application/json" id="replay-data-sw_t1"' in html
    assert 'window.__Replay.init("sw_t1");' in html


def test_render_replay_unicode_sid(tmp_path):
    """D2.1（评审 R4-测试缺口 2）：含 unicode 的 scenario_id 进入 JS init 不破脚本。"""
    sid = "sw_场景_α"
    d = make_artifacts(tmp_path / "a", scenario_ids=(sid,))
    html = _render(d)
    # 数据岛 id 用 HTML escape（无引号），JS 上下文 sid 用 json.dumps（带引号）
    assert 'id="replay-data-sw_场景_α"' in html
    # init 调用存在（json.dumps 把 unicode 编码为 \\uXXXX，仍是合法 JS 字符串）
    assert "window.__Replay.init(" in html
    # 数据岛解析无损：unicode sid 仍出现在数据岛 content 里
    assert sid in html


def test_render_replay_js_missing_no_crash(tmp_path):
    """D2.1（评审 R4-测试缺口 3）：replay.js 缺失时控制条仍渲染、但不绑定（降级不崩）。"""
    import home_perception.visualizer.renderer as R
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    orig = R._replay_inline
    R._replay_inline = lambda: ""  # 模拟 replay.js 缺失
    try:
        html = _render(d)
    finally:
        R._replay_inline = orig
    # 控制条按钮仍渲染（静态可读）
    assert 'id="rp-toggle-sw_t1"' in html
    # 但无 init 调用（未绑定）-> 点击不会因 __Replay 未定义而抛错
    assert "window.__Replay.init" not in html


# ---------------------------------------------------------------------------
# D2.2 Causal Highlight：timeline step ↔ Evidence Graph 类别联动高亮
# ---------------------------------------------------------------------------


def test_render_d22_stage_to_graph_category_constant():
    """D2.2：stage→graph 类别桥接表键/值与 _STAGE_* / _CAT_TYPES 严格一致。"""
    import home_perception.visualizer.renderer as R
    assert R._STAGE_TO_GRAPH_CATEGORY == {
        "perception": "Event",
        "decision": "Decision",
        "notification": "Action",
        "memory": "Episode",
        "cross_modal": "Link",
        "observability": "Scenario",
    }


def test_render_d22_island_carries_category(tmp_path):
    """D2.2：replay 数据岛每个节点携带 category（stage→graph 桥接键）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    m = re.search(
        r'<script type="application/json" id="replay-data-sw_t1">(.*?)</script>', html, re.DOTALL
    )
    assert m, "replay-data-sw_t1 数据岛缺失"
    nodes = json.loads(m.group(1))
    assert nodes, "数据岛无节点"
    for n in nodes:
        assert "category" in n, f"节点缺 category: {n}"
        assert n["category"] in (
            "Event", "Decision", "Action", "Episode", "Link", "Scenario", None
        )


def test_render_d22_graph_script_wires_highlight_and_click(tmp_path):
    """D2.2：graph 脚本订阅 linkHighlight + 高亮 + 点击节点 seek（step↔graph 双向）。"""
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    # 订阅链路：graph IIFE 经 linkHighlight 订阅 timeline step
    assert "window.__Replay.linkHighlight(" in html
    # 高亮实现：downplay + 按类别 dispatchAction highlight
    assert "function highlightCategory" in html
    assert "dispatchAction" in html
    # 反向：点击 graph 节点 → rp.seek 对应 step
    assert "chart.on('click'" in html
    assert "rp.seek" in html
    # emphasis.focus=adjacency 让高亮节点及其因果边突出
    assert "focus: 'adjacency'" in html


def test_render_d22_script_order_init_before_graph(tmp_path):
    """D2.2：replay 引擎（init）必须在 graph IIFE 之前，否则图取不到 replay 实例。

    顺序铁律：echarts → replay_js(定义) → replay_inits(init 调用) → graph_script。
    graph IIFE 内部依赖 window.__Replay.get(sid)（由 init 注册），故必须在 init 之后。
    用真实调用形态 `window.__Replay.init("sw_t1")` 排除 replay.js 注释里的同名 docstring。
    """
    d = make_artifacts(tmp_path / "a", scenario_ids=("sw_t1",))
    html = _render(d)
    i_replay_def = html.find("global.__Replay = {")          # replay_js 引擎定义
    i_init = html.find('window.__Replay.init("sw_t1")')      # 真实 init 调用
    i_graph = html.find("function highlightCategory")         # graph IIFE
    assert i_replay_def > 0 and i_init > 0 and i_graph > 0
    assert i_replay_def < i_init < i_graph, "脚本顺序违规：graph 必须在 replay init 之后"
    # 防回归（用户报告 #1）：replay_js 引擎定义 global.__Replay = { 必须整篇只注入
    # 一次。若 D2.2 与 main 合并时双侧保留导致重复注入，会出现第二次 init 覆盖
    # registry、二次 bindTimeline 重复绑定 onclick 等状态污染。此处铁律：恰好 1 次。
    assert html.count("global.__Replay = {") == 1


def test_render_d22_replay_js_link_highlight_and_bind_timeline():
    """D2.2：vendored replay.js 暴露 linkHighlight + 时间轴节点可点击 seek。"""
    import home_perception.visualizer.renderer as R
    js = R._replay_inline()
    assert "linkHighlight" in js, "replay.js 缺 linkHighlight（D2.2 订阅入口）"
    assert "bindTimeline" in js, "replay.js 缺 bindTimeline（时间轴点击 seek）"
    # linkHighlight 基于 onStep 实现（复用 D2.1 契约），回调传当前 step 的 category
    assert "onStep" in js
    assert "category" in js


# ---------------------------------------------------------------------------
# D2.2 replay.js 降级路径：行为级验证（Node 运行，CI 无 node 时跳过）
# ---------------------------------------------------------------------------

_REPLAY_DEGRADATION_HARNESS = r"""
const fs = require('fs');
const srcPath = process.argv[2];
const src = fs.readFileSync(srcPath, 'utf8');
const noop = () => {};
function makeEl() {
  let _onclick = null;
  return {
    style: {},
    set onclick(f) { _onclick = f; },
    get onclick() { return _onclick; },
    getAttribute() { return '0'; },
    querySelectorAll() { return []; },
    classList: { toggle: noop },
    textContent: '',
  };
}
const els = {};
global.document = { getElementById: (id) => (id in els ? els[id] : null) };
global.window = { document: global.document };
new Function('window', src)(global.window);
const R = global.window.__Replay;
if (!R || typeof R.linkHighlight !== 'function') {
  console.error('FAIL: __Replay.linkHighlight 未定义'); process.exit(1);
}
// 1) 实例缺失 -> 返回空句柄 {off}，回调绝不触发（fail-closed）
let called = false;
const h = R.linkHighlight('__missing__', () => { called = true; });
if (typeof h !== 'object' || typeof h.off !== 'function') {
  console.error('FAIL: linkHighlight 缺失实例时未返回 {off} 空句柄'); process.exit(1);
}
h.off();
if (called) { console.error('FAIL: 缺失实例时回调被误触发'); process.exit(1); }
// 2) init 时 timeline-list 缺失（listEl=null）-> bindTimeline 静默返回、不抛错
els['replay-data-x'] = { textContent: '[]' };
['reset','toggle','next','prev','speed','progress','progress-label'].forEach((k) => {
  els['rp-' + k + '-x'] = makeEl();
});
const inst = R.init('x');
if (!inst) { console.error('FAIL: init 返回空'); process.exit(1); }
if (inst.listEl !== null) { console.error('FAIL: 期望 listEl 为 null'); process.exit(1); }
inst.listEl = null;
inst.bindTimeline();
console.log('OK');
"""


def _node_exe() -> str | None:
    cand = r"C:/Users/lenovo/.workbuddy/binaries/node/versions/22.22.2/node.exe"
    if os.path.exists(cand):
        return cand
    return shutil.which("node")


def test_replay_js_degradation_paths(tmp_path):
    """replay.js 降级路径行为级验证（非字符串断言）：

    - linkHighlight 在 replay 实例缺失时返回 fail-closed 空句柄 {off}，回调绝不触发；
    - init 时 timeline-list 缺失（listEl=null）→ bindTimeline 静默返回、不抛错。
    需要 Node 运行时；CI 若无 node 则跳过（不影响 pytest 绿）。
    """
    import home_perception.visualizer.renderer as R

    node = _node_exe()
    if not node:
        pytest.skip("node 不可用，跳过 replay.js 行为级降级测试")
    src_file = tmp_path / "replay_src.js"
    src_file.write_text(R._replay_inline(), encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(_REPLAY_DEGRADATION_HARNESS, encoding="utf-8")
    res = subprocess.run(
        [node, str(harness), str(src_file)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert res.returncode == 0, (
        f"replay.js 降级行为测试失败:\n{res.stdout}\n{res.stderr}"
    )


# ---------------------------------------------------------------------------
# D2.2 graph 联动高亮：行为级验证（Node 真实执行 IIFE，CI 无 node 时跳过）
# ---------------------------------------------------------------------------

_D22_LINKAGE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf8');

// 收集所有 replay-data 场景 id
const pre = 'id="replay-data-';
let p = html.indexOf(pre);
const ids = [];
while (p !== -1) {
  const s = p + pre.length;
  const e = html.indexOf('"', s);
  ids.push(html.slice(s, e));
  p = html.indexOf(pre, e);
}
if (!ids.length) { console.error('FAIL: 无 replay-data 数据岛'); process.exit(1); }

// 拆出所有 <script>...</script> 块（非贪婪，匹配诊断可用实现）
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const replayJs = scripts.find(s => s.includes('global.__Replay = {'));

// 选第一个同时具备 graph IIFE（含 stepForCategory）与 init 调用的场景
let sid = null, graphScript = null, replayInits = null;
for (const cand of ids) {
  const g = scripts.find(s => s.includes('get("' + cand + '")') && s.includes('stepForCategory'));
  const i = scripts.find(s => s.includes('window.__Replay.init("' + cand + '")'));
  if (g && i) { sid = cand; graphScript = g; replayInits = i; break; }
}
if (!sid) { console.error('FAIL: 无具备 graph IIFE 的场景'); process.exit(1); }

// 取该场景的 replay-data JSON
const pre2 = 'id="replay-data-' + sid + '"';
const t0 = html.indexOf(pre2) + pre2.length;
const open = html.indexOf('>', t0) + 1;
const close = html.indexOf('</script>', open);
const replayData = html.slice(open, close);

const dispatchCalls = [];
const clickHandlers = [];
function mockEl(id) {
  return {
    _id: id,
    textContent: id.indexOf('replay-data-') !== -1 ? replayData : '',
    style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    getAttribute(a) { return a === 'data-idx' ? '0' : null; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    scrollIntoView() {},
    set onclick(f) {}, set onchange(f) {},
  };
}
const mockDoc = { getElementById: (id) => mockEl(id) };
const echarts = {
  init() {
    return {
      setOption() {},
      on(ev, cb) { if (ev === 'click') clickHandlers.push(cb); },
      dispatchAction(a) { dispatchCalls.push(a); },
      resize() {},
    };
  },
};
const ctx = { console, setTimeout, clearTimeout, Math, JSON, isNaN, parseInt, Infinity, document: mockDoc, echarts };
ctx.window = ctx;
vm.createContext(ctx);
function run(label, code) {
  try { vm.runInContext(code, ctx); }
  catch (e) { console.error('FAIL ' + label + ' 抛错: ' + e.message); process.exit(1); }
}
run('replayJs', replayJs);
run('replayInits', replayInits);
run('graphScript', graphScript);

// 断言 1：graph 节点 click→seek 联动已注册
if (clickHandlers.length < 1) { console.error('FAIL: chart.on(click) 未注册'); process.exit(1); }
// 断言 2：初始态高亮已触发 dispatchAction highlight
const initHi = dispatchCalls.filter(a => a.type === 'highlight' && a.dataIndex != null);
if (initHi.length < 1) { console.error('FAIL: 初始态未触发高亮'); process.exit(1); }

// 提取 graphNodes（括号平衡），用于校验高亮的目标类别正确
const gstart = graphScript.indexOf('var graphNodes = ') + 'var graphNodes = '.length;
let depth = 0, gend = -1;
for (let i = gstart; i < graphScript.length; i++) {
  const c = graphScript[i];
  if (c === '[') depth++;
  else if (c === ']') { depth--; if (depth === 0) { gend = i + 1; break; } }
}
const graphNodes = JSON.parse(graphScript.slice(gstart, gend));

// 断言 3：seek 到某 step 后，触发的高亮节点 ntype 与该 step 的 stage→category 一致
const rp = ctx.window.__Replay.get(sid);
const targetIdx = Math.min(2, rp.nodes.length - 1);
const expectedCat = rp.nodes[targetIdx].category;
dispatchCalls.length = 0;
rp.seek(targetIdx);
const seekHi = dispatchCalls.filter(a => a.type === 'highlight' && a.dataIndex != null);
if (seekHi.length < 1) { console.error('FAIL: seek 未触发高亮'); process.exit(1); }
const expectedCount = graphNodes.filter(n => n.ntype === expectedCat).length;
if (seekHi.length !== expectedCount) {
  console.error('FAIL: 高亮数 ' + seekHi.length + ' != 期望 ' + expectedCount + ' (cat=' + expectedCat + ')');
  process.exit(1);
}
const mism = seekHi.filter(a => !graphNodes[a.dataIndex] || graphNodes[a.dataIndex].ntype !== expectedCat);
if (mism.length > 0) { console.error('FAIL: 高亮节点 ntype 与桥接类别不符'); process.exit(1); }
console.log('OK sid=' + sid + ' clicks=' + clickHandlers.length + ' initHi=' + initHi.length + ' seekHi=' + seekHi.length + ' cat=' + expectedCat);
"""


def test_render_d22_graph_linkage_fires_dispatch(tmp_path):
    """D2.2 行为级回归：graph IIFE 必须真正触发联动高亮（非字符串断言）。

    历史 bug：highlightCategory 引用了未定义的 JS 变量 ``nodes``（Python 列表仅内联进
    ``data:`` 字段），导致每次 step 变更 / 初始高亮都抛 ``ReferenceError``——既不高亮，
    也因 IIFE 中途中断而未注册 ``chart.on('click')``。静态字符串断言（grep / ``in js``）
    无法捕获此类**运行时**错误。

    故用 Node 真实执行 replay.js + graph IIFE（mock echarts/document），断言：
      - ``chart.on('click')`` 已注册（click→seek 联动存在）；
      - 初始态与 ``seek(idx)`` 均触发 ``dispatchAction highlight``；
      - 被高亮 graph 节点的 ``ntype`` 与当前 step 的 stage→category 桥接键一致（映射正确）。
    CI 无 node 时跳过。
    """
    node = _node_exe()
    if not node:
        pytest.skip("node 不可用，跳过 D2.2 graph 联动行为级测试")

    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    html_file = tmp_path / "explorer.html"
    html_file.write_text(html, encoding="utf-8")
    harness = tmp_path / "linkage_harness.js"
    harness.write_text(_D22_LINKAGE_HARNESS, encoding="utf-8")
    res = subprocess.run(
        [node, str(harness), str(html_file)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert res.returncode == 0, (
        f"D2.2 graph 联动行为测试失败:\n{res.stdout}\n{res.stderr}"
    )


# ---------------------------------------------------------------------------
# D2.3 Trace Replay：Decision Explanation 卡片重放（第二条重放轨道）
# ---------------------------------------------------------------------------

def test_render_d23_trace_replay_wired(tmp_path):
    """D2.3 静态接线：Decision Explanation 升级为可重放 trace 轨道。

    断言：
    - 控制条 rp-trace-* 七控件齐全（与主时间轴 rp-* 互不干扰）；
    - trace 列表容器 trace-list-{sid} 存在且为 <ul class="trace-list dc-grid">；
    - replay-trace-data-{sid} 数据岛存在，每个卡片带 kind→graph 类别桥接键
      （Event/Decision/Action，与 _DECISION_KIND_TO_GRAPH_CATEGORY 一致）；
    - replay_inits 含 init(sid, 'trace') 第二轨道初始化调用；
    - 常量与桥接映射一致（防漂移）。
    """
    import home_perception.visualizer.renderer as R

    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    sid = "sw_t1"
    # 控制条七控件（rp-trace-* 前缀，与主时间轴 rp-* 同形但 id 唯一）
    for suffix in ("reset", "toggle", "prev", "next", "speed", "progress", "progress-label"):
        assert f'id="rp-trace-{suffix}-{sid}"' in html, f"缺 trace 控制条控件 rp-trace-{suffix}-{sid}"
    # trace 列表容器
    assert f'id="trace-list-{sid}"' in html
    assert 'class="trace-list dc-grid"' in html
    # trace 数据岛（含 category 桥接键）
    m = re.search(
        r'<script type="application/json" id="replay-trace-data-' + sid + r'">(.*?)</script>',
        html, re.DOTALL,
    )
    assert m, "缺 replay-trace-data 数据岛"
    trace = json.loads(m.group(1))
    assert isinstance(trace, list) and trace, "trace 数据应为非空列表"
    cats = {n.get("category") for n in trace}
    assert cats == {"Event", "Decision", "Action"}, f"trace 类别桥接错误: {cats}"
    # 第二轨道初始化调用
    assert f'window.__Replay.init("{sid}", \'trace\');' in html
    # 常量与桥接一致（防漂移）
    assert R._DECISION_KIND_TO_GRAPH_CATEGORY == {
        "evidence": "Event", "reasoning": "Decision", "outcome": "Action"
    }


# D2.3 行为级回归：trace 轨道真实驱动 graph 高亮（Node 执行 replay.js + graph IIFE）。
_D23_TRACE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf8');

// 1) 找带 trace 数据岛的场景 id
const tracePre = 'id="replay-trace-data-';
const tp = html.indexOf(tracePre);
if (tp === -1) { console.error('FAIL: 无 replay-trace-data 数据岛（D2.3 trace 未接线）'); process.exit(1); }
const sid = html.slice(tp + tracePre.length, html.indexOf('"', tp + tracePre.length));

// 2) 拆出所有 <script>...</script> 块（非贪婪，匹配诊断可用实现）
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const replayJs = scripts.find(s => s.includes('global.__Replay = {'));
if (!replayJs) { console.error('FAIL: 未找到 replay.js'); process.exit(1); }
const replayInits = scripts.find(s => s.includes('window.__Replay.init("' + sid + '")'));
if (!replayInits) { console.error('FAIL: 未找到 replay_inits'); process.exit(1); }
const graphScript = scripts.find(s => s.includes("get(\"" + sid + "\", 'trace')"));
if (!graphScript) { console.error('FAIL: 未找到含 trace 订阅的 graph IIFE'); process.exit(1); }

// 3) 提取 trace / timeline 数据岛 JSON（供 mock document 回填）
function islandRaw(pre) {
  const p = html.indexOf(pre);
  const open = html.indexOf('>', p) + 1;
  const close = html.indexOf('</script>', open);
  return html.slice(open, close);
}
const traceRaw = islandRaw(tracePre);
const tlRaw = islandRaw('id="replay-data-' + sid + '"');
let traceData;
try { traceData = JSON.parse(traceRaw); }
catch (e) { console.error('FAIL: trace 数据岛 JSON 非法: ' + e.message); process.exit(1); }
if (!Array.isArray(traceData) || traceData.length === 0) { console.error('FAIL: trace 数据为空'); process.exit(1); }
const want = ['Event', 'Decision', 'Action'];
const got = traceData.map(n => n.category);
if (!want.every(c => got.includes(c))) { console.error('FAIL: trace 类别不完整 ' + JSON.stringify(got)); process.exit(1); }

// 4) mock document / echarts
const dispatchCalls = [];
const clickHandlers = [];
function mockEl(id) {
  return {
    _id: id,
    textContent: id.indexOf('replay-trace-data-') !== -1 ? traceRaw
               : (id.indexOf('replay-data-') !== -1 ? tlRaw : ''),
    style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    getAttribute(a) { return a === 'data-idx' ? '0' : null; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    scrollIntoView() {},
    set onclick(f) {}, set onchange(f) {},
  };
}
const mockDoc = { getElementById: (id) => mockEl(id) };
const echarts = {
  init() {
    return {
      setOption() {},
      on(ev, cb) { if (ev === 'click') clickHandlers.push(cb); },
      dispatchAction(a) { dispatchCalls.push(a); },
      resize() {},
    };
  },
};
const ctx = { console, setTimeout, clearTimeout, Math, JSON, isNaN, parseInt, Infinity, document: mockDoc, echarts };
ctx.window = ctx;
vm.createContext(ctx);
function run(label, code) {
  try { vm.runInContext(code, ctx); }
  catch (e) { console.error('FAIL ' + label + ' 抛错: ' + e.message); process.exit(1); }
}
run('replayJs', replayJs);
run('replayInits', replayInits);
run('graphScript', graphScript);

// 5) 断言：两条独立重放实例（timeline 与 trace 互不干扰）
const rp = ctx.window.__Replay.get(sid);
const rpTrace = ctx.window.__Replay.get(sid, 'trace');
if (!rp) { console.error('FAIL: timeline 实例缺失'); process.exit(1); }
if (!rpTrace) { console.error('FAIL: trace 实例缺失'); process.exit(1); }
if (rp === rpTrace) { console.error('FAIL: timeline 与 trace 实例不可区分'); process.exit(1); }

// 6) 提取 graphNodes 用于校验高亮目标
const gstart = graphScript.indexOf('var graphNodes = ') + 'var graphNodes = '.length;
let depth = 0, gend = -1;
for (let i = gstart; i < graphScript.length; i++) {
  const c = graphScript[i];
  if (c === '[') depth++;
  else if (c === ']') { depth--; if (depth === 0) { gend = i + 1; break; } }
}
const graphNodes = JSON.parse(graphScript.slice(gstart, gend));

// 断言：trace seek 到 Action 类别卡片 → graph 高亮对应节点
let actIdx = -1;
for (let i = 0; i < rpTrace.nodes.length; i++) {
  if (rpTrace.nodes[i].category === 'Action') { actIdx = i; break; }
}
if (actIdx === -1) { console.error('FAIL: trace 无 Action 类别卡片'); process.exit(1); }
dispatchCalls.length = 0;
rpTrace.seek(actIdx);
const seekHi = dispatchCalls.filter(a => a.type === 'highlight' && a.dataIndex != null);
if (seekHi.length < 1) { console.error('FAIL: trace seek 未触发高亮'); process.exit(1); }
const expCount = graphNodes.filter(n => n.ntype === 'Action').length;
if (seekHi.length !== expCount) { console.error('FAIL: 高亮数 ' + seekHi.length + ' != 期望 ' + expCount); process.exit(1); }
const mism = seekHi.filter(a => !graphNodes[a.dataIndex] || graphNodes[a.dataIndex].ntype !== 'Action');
if (mism.length > 0) { console.error('FAIL: 高亮节点类别错误'); process.exit(1); }

// 7) 断言：点击 graph 节点（Action）→ trace 轨道被 seek（反向联动）
let traceSeekCalls = 0;
const origSeek = rpTrace.seek.bind(rpTrace);
rpTrace.seek = function (i) { traceSeekCalls++; return origSeek(i); };
if (!clickHandlers.length) { console.error('FAIL: chart.on(click) 未注册'); process.exit(1); }
clickHandlers[0]({ dataType: 'node', data: { ntype: 'Action' } });
if (traceSeekCalls < 1) { console.error('FAIL: 点击 graph Action 节点未联动 trace seek'); process.exit(1); }

console.log('OK sid=' + sid + ' timelineNodes=' + rp.nodes.length + ' traceNodes=' + rpTrace.nodes.length + ' seekHi=' + seekHi.length + ' traceSeekCalls=' + traceSeekCalls);
"""


def test_render_d23_trace_replay_fires_dispatch(tmp_path):
    """D2.3 行为级回归：trace 轨道必须真正驱动 graph 联动高亮（非字符串断言）。

    历史教训（D2.2）：highlightCategory 曾引用未定义 JS 变量导致运行时 ReferenceError，
    静态字符串断言无法捕获。故用 Node 真实执行 replay.js + graph IIFE（mock
    echarts/document），断言：
      - timeline 与 trace 是两条**独立**重放实例（注册键 `sid::timeline` / `sid::trace`）；
      - trace seek 到 Action 类别卡片 → 触发 dispatchAction highlight，且高亮节点 ntype
        与 trace kind→category 桥接键一致（Event/Decision/Action）；
      - 点击 graph 节点（Action）→ trace 轨道被 seek（反向联动同样打通）。
    CI 无 node 时跳过。
    """
    node = _node_exe()
    if not node:
        pytest.skip("node 不可用，跳过 D2.3 trace 联动行为级测试")

    d = make_artifacts(tmp_path / "a")
    html = _render(d)
    html_file = tmp_path / "explorer.html"
    html_file.write_text(html, encoding="utf-8")
    harness = tmp_path / "d23_trace_harness.js"
    harness.write_text(_D23_TRACE_HARNESS, encoding="utf-8")
    res = subprocess.run(
        [node, str(harness), str(html_file)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert res.returncode == 0, (
        f"D2.3 trace 联动行为测试失败:\n{res.stdout}\n{res.stderr}"
    )


# ---------------------------------------------------------------------------
# 转义工具安全锁定（ADR-0036 评审 R2-#6 / #367）
# ---------------------------------------------------------------------------


from home_perception.visualizer import renderer  # 文件末追加，避免扰动顶部 imports


def test_renderer_esc_js_escapes_line_separators():
    """_esc_js 必须转义 U+2028/U+2029（JS 字符串层语法换行符，会提前终结字符串/语句）。

    json.dumps 默认 ensure_ascii=True 会把它们转义为 \\u2028/\\u2029，而 html.escape
    不处理——故 JS 层必须走 _esc_js 而非 _esc（评审 R2-#6 安全锁定）。
    """
    s = "a\u2028b\u2029c"
    out = renderer._esc_js(s)
    # 原始行/段分隔符不得出现在输出（否则浏览器 JS 解析报错）。
    assert "\u2028" not in out
    assert "\u2029" not in out
    # 应被转义为 \\u2028 / \\u2029（JSON 字符串内）。
    assert "\\u2028" in out
    assert "\\u2029" in out
    # 对照：html.escape 不处理这两个字符，证明为何需要 _esc_js。
    assert "\u2028" in renderer._esc(s)
    assert "\u2029" in renderer._esc(s)


def test_renderer_guard_script_close_rewrites_closing_tag():
    """_guard_script_close 仅改写字面 </script（防注入 JS 提前终结脚本块）。"""
    assert renderer._guard_script_close("a</script>b") == "a<\\/script>b"
    assert renderer._guard_script_close("</SCRIPT>") == "<\\/script>"
    # 无 </script 的子串应原样返回（不误伤）。
    assert renderer._guard_script_close("var x = '</not-close';") == "var x = '</not-close';"

