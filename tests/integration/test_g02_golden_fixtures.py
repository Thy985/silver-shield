"""G0-2 黄金案例 fixtures · 端到端契约测试（prior_episodes → 决策升级 → trace 引用 → gate）。

覆盖验收（docs/DESIGN-golden-scenario-set.md §9 G0-2/G0-4）：
- golden_repeated_visit：prior 预置（确定性身份桥 uuid5）→ memory_aware 决策升级
  （MEDIUM/NOTIFY_FAMILY）→ Decision Trace.historical_record_ids 引用 2 条 prior
  → Integration Gate PASS（Expected Outcome：WARN + LOG_ONLY + memory >= 2）；
- golden_benign：TN（无事件 → 不报警不命令）→ Gate PASS；
- 身份桥确定性：运行时 visitor_id == uuid5(NS, actor.id)（"同一访客"跨日可匹配）。

需要 cv2（integration loop 运行时重链）。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = (
    REPO_ROOT
    / "src"
    / "home_perception"
    / "validation"
    / "fixtures"
    / "scenarios"
    / "golden"
)


def _run(scenario_id: str):
    """加载 golden fixture → IntegrationRunner.run → IntegrationValidator.validate。"""
    from home_perception.integration.loop.context import (
        IntegrationContext,
        IntegrationRunnerConfig,
    )
    from home_perception.integration.loop.runner import IntegrationRunner
    from home_perception.integration.loop.validator import IntegrationValidator
    from home_perception.validation.scenario.scenario import load_scenario

    path = GOLDEN_DIR / f"{scenario_id}.yaml"
    scn = load_scenario(path)
    config = IntegrationRunnerConfig()
    ctx = IntegrationContext.build(config)
    result = IntegrationRunner(config=config).run(scn, context=ctx)
    verdict = IntegrationValidator().validate(result, scn)
    return scn, ctx, result, verdict


def test_golden_repeated_visit_gate_passes():
    """golden_repeated_visit：Integration Gate PASS（Expected Outcome 全断言）。"""
    _, ctx, result, verdict = _run("golden_repeated_visit")
    assert verdict.ok is True, verdict
    assert len(result.warnings) == 1
    w = result.warnings[0]
    # 历史感知升级：感知层 abnormal_dwell(LOW) → memory_aware 历史 >= 2 → MEDIUM
    assert w.risk_level == "MEDIUM"
    assert w.recommended_action == "NOTIFY_FAMILY"
    assert any("历史 2 次类似访问" in r for r in w.reason_summary)
    # memory 落库：2 prior + 1 本次 = 3
    assert len(ctx.memory_store.all_episodic()) >= 2


def test_golden_repeated_visit_trace_references_history():
    """G0-3 验收：Decision Trace.historical_record_ids 引用 2 条 prior（决策用了历史可证）。"""
    _, _, result, _ = _run("golden_repeated_visit")
    assert result.decision_traces
    trace = result.decision_traces[-1]
    refs = trace.provenance.memory_refs
    assert set(refs.historical_record_ids) == {
        "ep-prior-historical_001",
        "ep-prior-historical_002",
    }


def test_golden_visitor_identity_bridge_deterministic():
    """确定性身份桥：运行时 visitor_id == uuid5(NS, actor.id)（跨日"同一访客"可匹配）。"""
    scn, ctx, _, _ = _run("golden_repeated_visit")
    from home_perception.integration.loop.runner import IntegrationRunner

    expected = IntegrationRunner._golden_visitor_id("visitor_b")
    # 本次会话落库的 episodic 中，非 prior 那条的 visitor 应 == expected
    prior_ids = {f"ep-prior-{pe.episode_id}" for pe in scn.prior_episodes}
    for rec in ctx.memory_store.all_episodic():
        if rec.record_id not in prior_ids:
            assert rec.visitor_instance_id == expected
            assert rec.visitor_instance_id == expected


def test_golden_benign_gate_passes():
    """golden_benign：TN（无事件 → 不报警不命令）→ Gate PASS。"""
    _, _, result, verdict = _run("golden_benign")
    assert verdict.ok is True, verdict
    assert len(result.warnings) == 0
    assert len(result.commands) == 0


def test_golden_benign_no_memory_records():
    """golden_benign：无事件 → 无 episode 落库（不误触 memory）。"""
    _, ctx, _, _ = _run("golden_benign")
    assert len(ctx.memory_store.all_episodic()) == 0
