"""冻结合规契约测试（ADR-0015 §5）。

攻击性契约测试：证明 ``silver_demo`` 只消费冻结契约白名单符号，
不穿透 ``home_perception`` 的 7 层内部。

三条断言：
1. **import 边界**：``silver_demo`` 的模块依赖图中，``home_perception.*`` 的 import
   必须仅来自白名单子模块；出现 ``rule_engine`` / ``decision_engine`` /
   ``action.executor`` / ``action.dispatcher`` / ``action.notifier`` / ``action.publisher``
   / ``detection`` / ``analysis.feature_extractor`` / ``analysis.event_builder`` 等 → 失败。
2. **消费形态**：``DemoGateway`` 只调 ``PerceptionPipeline.from_settings`` / ``process_frame`` /
   ``load_detector`` / ``close``，不持有 ``RuleEngine``/``DecisionEngine``/``ActionExecutor`` 实例。
3. **类型只读**：``bridge`` 对 ``WarningEvent``/``ActionCommand`` 只调 ``to_dict()``，
   不调构造器（source 中不出现 ``WarningEvent(`` / ``ActionCommand(`` 构造调用）。

> 这条测试把 ADR-0014 Level 3（Runtime Assembly 契约）从"内部纪律"变成"外部可验证"。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

# silver_demo 包根目录（用于 source 扫描）
SILVER_DEMO_SRC = Path(__file__).resolve().parents[2] / "src" / "silver_demo"


# ============================================================================
# 白名单：silver_demo 允许 import 的 home_perception 子模块
# ============================================================================

ALLOWED_HP_IMPORTS = {
    "home_perception.core.config",            # Settings
    "home_perception.runtime.pipeline",       # PerceptionPipeline / DemoClock / FrameResult
    "home_perception.runtime.config",         # read_caviar_frames
    "home_perception.analysis.warning",       # WarningEvent（类型标注 / 只读 to_dict）
    "home_perception.action.command",         # ActionCommand（类型标注 / 只读 to_dict）
}

# 明确禁止的 7 层内部子模块（silver_demo 不得 import）
FORBIDDEN_HP_SUBMODULES = {
    "home_perception.analysis.rule_engine",
    "home_perception.analysis.decision_engine",
    "home_perception.analysis.decision_policy",
    "home_perception.analysis.feature_extractor",
    "home_perception.analysis.event_builder",
    "home_perception.analysis.event",
    "home_perception.analysis.perception",
    "home_perception.analysis.rule",
    "home_perception.analysis.cooldown",
    "home_perception.action.executor",
    "home_perception.action.dispatcher",
    "home_perception.action.notifier",
    "home_perception.action.publisher",
    "home_perception.detection.detector",
    "home_perception.detection.tracker",
    "home_perception.ingestion.frame_source",
    "home_perception.evidence",
    "home_perception.output",
    "home_perception.common.logging",
}


# ============================================================================
# 测试 1：import 边界
# ============================================================================

def _collect_silver_demo_modules() -> list[str]:
    """收集 silver_demo 包下所有 .py 模块的完整限定名。"""
    modules: list[str] = []
    for py in sorted(SILVER_DEMO_SRC.rglob("*.py")):
        rel = py.relative_to(SILVER_DEMO_SRC.parent)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            modules.append(".".join(parts))
    return modules


def test_import_boundary_only_whitelist() -> None:
    """断言 silver_demo 的 home_perception 依赖仅在白名单内。"""
    # 注意：gateway import 会触发 home_perception.runtime.pipeline 加载，
    # 而 pipeline 内部 import 了 detection/analysis/action 等 7 层。
    # 因此不能直接断言 sys.modules（会被 pipeline 的内部依赖污染）。
    # 改用 AST 扫描 silver_demo 源码，检查其 *直接 import 语句*。
    import ast

    violations: list[str] = []
    for py in sorted(SILVER_DEMO_SRC.rglob("*.py")):
        rel = py.relative_to(SILVER_DEMO_SRC.parent)
        modname = ".".join(rel.with_suffix("").parts)
        if modname.endswith(".__init__"):
            modname = modname[: -len(".__init__")]
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            violations.append(f"{modname}: 语法错误")
            continue

        for node in ast.walk(tree):
            # import home_perception.xxx
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("home_perception."):
                        if alias.name not in ALLOWED_HP_IMPORTS:
                            violations.append(f"{modname}: 禁止 import {alias.name!r}")
            # from home_perception.xxx import ...
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("home_perception."):
                    if node.module not in ALLOWED_HP_IMPORTS:
                        violations.append(f"{modname}: 禁止 from {node.module!r} import")

    assert not violations, (
        "silver_demo 违反冻结合规：以下 import 超出白名单\n  - "
        + "\n  - ".join(violations)
    )


def test_no_forbidden_submodule_imports() -> None:
    """二次确认：silver_demo 源码中不出现任何禁止子模块的引用字符串。"""
    forbidden_patterns = [
        "rule_engine",
        "decision_engine",
        "decision_policy",
        "feature_extractor",
        "event_builder",
        "action.executor",
        "action.dispatcher",
        "action.notifier",
        "action.publisher",
        "detection.detector",
        "detection.tracker",
        "ingestion.frame_source",
    ]
    violations: list[str] = []
    for py in sorted(SILVER_DEMO_SRC.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        # 跳过本测试文件自身（它必须提及这些名字以守边界）
        if py.name == "test_freeze_boundary.py":
            continue
        for pat in forbidden_patterns:
            # 匹配 import 语句中的引用（from X import / import X）
            if f"import {pat}" in text or f"from {pat}" in text or f"home_perception.{pat}" in text:
                violations.append(f"{py.name}: 出现禁止引用 {pat!r}")
    assert not violations, (
        "silver_demo 违反冻结合规：出现禁止子模块引用\n  - "
        + "\n  - ".join(violations)
    )


# ============================================================================
# 测试 2：消费形态 — DemoGateway 只调白名单 API
# ============================================================================

def test_gateway_consumes_only_whitelist_api() -> None:
    """断言 DemoGateway 只调 PerceptionPipeline.from_settings / process_frame / load_detector / close。"""
    # 延迟 import：fastapi 未装时跳过
    pytest.importorskip("fastapi")
    from silver_demo.gateway import DemoGateway

    # 收集 DemoGateway 所有方法的 source
    src = inspect.getsource(DemoGateway)
    # 禁止的调用（构造 7 层内部组件）
    forbidden_constructions = [
        "RuleEngine(",
        "DecisionEngine(",
        "ActionExecutor(",
        "ActionDispatcher(",
        "FeatureExtractor(",
        "VisitorEventBuilder(",
        "VisitorTracker(",
        "YOLODetector(",
    ]
    for fc in forbidden_constructions:
        assert fc not in src, (
            f"DemoGateway 禁止构造 7 层内部组件：出现 {fc!r}"
        )


# ============================================================================
# 测试 3：类型只读 — bridge 不调 WarningEvent/ActionCommand 构造器
# ============================================================================

def test_bridge_does_not_construct_frozen_objects() -> None:
    """断言 bridge.py 源码中不出现 WarningEvent(...) / ActionCommand(...) 构造调用。"""
    bridge_src = (SILVER_DEMO_SRC / "bridge.py").read_text(encoding="utf-8")
    assert "WarningEvent(" not in bridge_src, (
        "bridge.py 禁止构造 WarningEvent（只读 to_dict）"
    )
    assert "ActionCommand(" not in bridge_src, (
        "bridge.py 禁止构造 ActionCommand（只读 to_dict）"
    )
    # 允许 to_dict 调用
    assert "to_dict()" in bridge_src, "bridge.py 应通过 to_dict() 消费冻结对象"


def test_gateway_does_not_construct_frozen_objects() -> None:
    """断言 gateway.py 源码中不出现 WarningEvent(...) / ActionCommand(...) 构造调用。"""
    gw_src = (SILVER_DEMO_SRC / "gateway.py").read_text(encoding="utf-8")
    # gateway 有类型标注 import，但不应有构造调用
    # 简单启发：构造调用形如 `WarningEvent(` 但前面不是 `#` 或 `import`
    for forbidden in ["WarningEvent(", "ActionCommand("]:
        # 排除注释行和 import 行
        for i, line in enumerate(gw_src.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "import" in line and forbidden.rstrip("(") in line:
                continue
            if forbidden in line and "import" not in line:
                pytest.fail(
                    f"gateway.py:{i} 禁止构造冻结对象 {forbidden!r}（只读消费）"
                )


# ============================================================================
# 测试 4：bridge view-model 结构正确
# ============================================================================

def test_bridge_view_model_structure() -> None:
    """断言 frame_result_to_view 产出的 dict 含 ADR-0015 §2.2 要求的字段。"""
    from silver_demo.bridge import frame_result_to_view

    # 用一个最小 stub FrameResult（不依赖真实 pipeline）
    class _StubFrameResult:
        n_detections = 2
        n_visitor_events = 1
        perception_events = []
        warnings = []
        commands = []

    view = frame_result_to_view(
        _StubFrameResult(),
        frame_index=5,
        frame_base64="abc123",
        demo_time="2026-07-19T23:30:00+00:00",
    )
    assert view["frame_index"] == 5
    assert view["frame_base64"] == "abc123"
    assert view["demo_time"] == "2026-07-19T23:30:00+00:00"
    assert view["n_detections"] == 2
    assert view["n_visitor_events"] == 1
    assert view["perception_events"] == []
    assert view["warnings"] == []
    assert view["commands"] == []


def test_bridge_view_model_restamps_created_at_to_demo_time() -> None:
    """断言 frame_result_to_view 把透传的 perception_events/warnings created_at 重打为 demo_time。

    根因：模型 created_at 是真实墙钟 UTC（_utc_now），而 Region 1 的 demo_time 是 DemoClock
    模拟时间，两者时基不同 → ①区模拟时间 vs ②区 AI 行为时间线对不上。重打后两区统一。
    demo_time=None 时保留原始 created_at（降级不破坏）。
    """
    from silver_demo.bridge import frame_result_to_view

    class _StubEvent:
        def to_dict(self):
            return {"event_type": "long_stay", "created_at": "2026-07-22T03:11:45.123456+00:00"}

    class _StubWarning:
        def to_dict(self):
            return {"warning_id": "w1", "risk_level": "HIGH",
                    "created_at": "2026-07-22T03:11:50.654321+00:00"}

    class _StubFrameResult:
        n_detections = 1
        n_visitor_events = 1
        perception_events = [_StubEvent()]
        warnings = [_StubWarning()]
        commands = []

    demo_time = "2026-07-19T23:30:00+00:00"
    view = frame_result_to_view(_StubFrameResult(), frame_index=10, frame_base64=None, demo_time=demo_time)
    assert view["perception_events"][0]["created_at"] == demo_time
    assert view["warnings"][0]["created_at"] == demo_time

    # demo_time=None → 保留真实墙钟 created_at（降级路径）
    view2 = frame_result_to_view(_StubFrameResult(), frame_index=10, frame_base64=None, demo_time=None)
    assert view2["perception_events"][0]["created_at"].startswith("2026-07-22")
    assert view2["warnings"][0]["created_at"].startswith("2026-07-22")


def test_bridge_route_commands() -> None:
    """断言 route_commands 按 command_type 正确路由到三端。"""
    from silver_demo.bridge import route_commands

    commands = [
        {"command_type": "SEND_FAMILY_MESSAGE", "command_id": "1"},
        {"command_type": "CREATE_COMMUNITY_TASK", "command_id": "2"},
        {"command_type": "LOG_ONLY", "command_id": "3"},
        {"command_type": "SEND_FAMILY_MESSAGE", "command_id": "4"},
    ]
    routed = route_commands(commands)
    assert len(routed["family"]) == 2
    assert len(routed["community"]) == 1
    assert len(routed["log_only"]) == 1


def test_bridge_collect_active_warnings() -> None:
    """断言 collect_active_warnings 过滤掉 RESOLVED/REJECTED 的告警（P0-11.2 区域 3 渲染）。"""
    from silver_demo.bridge import collect_active_warnings

    warnings = [
        {"warning_id": "1", "status": "CREATED"},
        {"warning_id": "2", "status": "PENDING"},
        {"warning_id": "3", "status": "RESOLVED"},
        {"warning_id": "4", "status": "REJECTED"},
        {"warning_id": "5", "status": "CONFIRMED"},
    ]
    active = collect_active_warnings(warnings)
    assert {w["warning_id"] for w in active} == {"1", "2", "5"}
    # 缺 status 字段也视为活跃（防御性，不静默丢弃）
    assert collect_active_warnings([{"warning_id": "x"}]) == [{"warning_id": "x"}]
    # 空列表安全
    assert collect_active_warnings([]) == []


def test_bridge_collect_active_warnings_skips_non_dict() -> None:
    """断言 collect_active_warnings 对 None / 非 dict 元素防御性跳过（不崩溃）。"""
    from silver_demo.bridge import collect_active_warnings

    # 非 dict 元素（None / 字符串 / 数字）被跳过，仅保留 dict
    mixed = [None, "not-a-dict", 42, {"warning_id": "1"}, {"warning_id": "2", "status": "RESOLVED"}]
    assert collect_active_warnings(mixed) == [{"warning_id": "1"}]


# ============================================================================
# 测试 5：DemoStateStore 状态机
# ============================================================================

@pytest.mark.asyncio
async def test_state_store_transition() -> None:
    """断言 DemoStateStore 状态翻转合法/非法符合 ADR-0015 §2.5。"""
    from silver_demo.state import DemoStateStore

    store = DemoStateStore()
    # 首次 upsert 强制 pending
    s = await store.upsert("w1")
    assert s["status"] == "pending"

    # pending → family_handled 合法
    s = await store.upsert("w1", status="family_handled", operator="family")
    assert s["status"] == "family_handled"
    assert s["operator"] == "family"

    # family_handled → community_done 合法
    s = await store.upsert("w1", status="community_done", operator="community")
    assert s["status"] == "community_done"

    # community_done 是终态，不能再翻
    with pytest.raises(ValueError):
        await store.upsert("w1", status="pending")

    # 非法翻转：pending → community_done（跳过 family_handled）
    with pytest.raises(ValueError):
        await store.upsert("w2", status="pending")
        await store.upsert("w2", status="community_done", operator="community")


@pytest.mark.asyncio
async def test_state_store_first_seen_direct_non_pending() -> None:
    """回归：单次点击即确认——首次 upsert 带明确非 pending 状态应直接作为初值。

    PR #51 修复前，首见 warning 被强制覆盖为 pending，导致 WS 上行「单次点击
    确认」状态被静默丢弃（status 停留 pending）。修复后 family_handled /
    community_done 可作首态写入，同时仍受 TRANSITIONS 单向约束。
    """
    from silver_demo.state import DemoStateStore

    store = DemoStateStore()
    s = await store.upsert("w_family", status="family_handled", operator="family")
    assert s["status"] == "family_handled"
    assert s["operator"] == "family"

    # 终态仍不可被非法翻转回退
    with pytest.raises(ValueError):
        await store.upsert("w_family", status="pending")

    store2 = DemoStateStore()
    s2 = await store2.upsert("w_community", status="community_done", operator="community")
    assert s2["status"] == "community_done"


# ============================================================================
# 测试 6：ScenarioConfig 加载
# ============================================================================

def test_load_night_visit_scenario() -> None:
    """断言 night_visit.yaml 能被正确加载为 ScenarioConfig。"""
    from silver_demo.scenarios import load_scenario

    scenario = load_scenario("config/demo/scenarios/night_visit.yaml")
    assert scenario.scenario_id == "night_visit"
    # 注意：source 是本地 fixture 目录名（对应 CAVIAR 公开序列 OneLeaveShopReenter1cor），
    # 必须与 settings.runtime.caviar_base_dir 下的真实目录一致（tests/fixtures/doorway/one_leave_reenter）。
    assert scenario.source == "one_leave_reenter"
    assert scenario.start_time.year == 2026
    assert scenario.start_time.month == 7
    assert scenario.frame_interval_s == 0.5
    assert scenario.loop is True
