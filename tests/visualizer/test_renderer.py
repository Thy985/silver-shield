"""ADR-0035 D4/D6/D7/D8 · 渲染测试：四视图 / 确定性 / 脱敏 / 自包含。

对应验收：1（输入契约）、2（四视图）、4（脱敏）、5（确定性）。
"""

from __future__ import annotations

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
    assert len(html) < 3_000_000  # 有界（正常 ~1MB 含 echarts；10k 节点脚本 <2MB）


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
