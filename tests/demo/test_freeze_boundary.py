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

> 这条测试把 ADR-0014 Level 3（Runtime Assembly 契约）+ ADR-0015 §2.1.1（分层依赖契约）从"内部纪律"
> 变成"外部可验证"；T0-1~T0-5 守「Runtime Core 不依赖 Presentation Layer / Host 可依赖 / 不反向依赖」，
> T0-6 见 ``tests/demo/test_gateway_serves_case_viewer.py``。
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
    "home_perception.core.config",  # Settings
    "home_perception.runtime.pipeline",  # PerceptionPipeline / DemoClock / FrameResult
    "home_perception.runtime.runtime_context",  # ADR-0039：RuntimeFrameContext（process_frame 唯一入参容器）
    "home_perception.runtime.config",  # read_caviar_frames
    "home_perception.analysis.warning",  # WarningEvent（类型标注 / 只读 to_dict）
    "home_perception.action.command",  # ActionCommand（类型标注 / 只读 to_dict）
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


# ADR-0015 §2.1.1：仅 Host(gateway) 允许 import Presentation Layer（visualizer.viewer）；
# 其余 silver_demo 子模块（Runtime Core）一律禁止 import visualizer（T0-1 / T0-2）。
_GATEWAY_VISUALIZER_PREFIX = "home_perception.visualizer.viewer"


def _is_visualizer_import_allowed_for(modname: str, module: str) -> bool:
    """仅 ``silver_demo.gateway`` 可 import ``home_perception.visualizer.viewer``（含子模块）。"""
    if modname != "silver_demo.gateway":
        return False
    return (
        module == _GATEWAY_VISUALIZER_PREFIX
        or module.startswith(_GATEWAY_VISUALIZER_PREFIX + ".")
    )


def test_import_boundary_only_whitelist() -> None:
    """断言 silver_demo 的 home_perception 依赖仅在白名单内（gateway 的 visualizer.viewer 例外）。"""
    # 注意：gateway import 会触发 home_perception.runtime.pipeline 加载，
    # 而 pipeline 内部 import 了 detection/analysis/action 等 7 层。
    # 因此不能直接断言 sys.modules（会被 pipeline 的内部依赖污染）。
    # 改用 AST 扫描 silver_demo 源码，检查其 *直接 import 语句*。
    import ast

    violations: list[str] = []
    for py in sorted(SILVER_DEMO_SRC.rglob("*.py")):
        rel = py.relative_to(SILVER_DEMO_SRC.parent)
        modname = ".".join(rel.with_suffix("").parts)
        modname = modname.removesuffix(".__init__")
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            violations.append(f"{modname}: 语法错误")
            continue

        for node in ast.walk(tree):
            # import home_perception.xxx
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name.startswith("home_perception."):
                        continue
                    if _is_visualizer_import_allowed_for(modname, alias.name):
                        continue
                    if alias.name not in ALLOWED_HP_IMPORTS:
                        violations.append(f"{modname}: 禁止 import {alias.name!r}")
            # from home_perception.xxx import ...
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("home_perception.")
            ):
                if _is_visualizer_import_allowed_for(modname, node.module):
                    continue
                if node.module not in ALLOWED_HP_IMPORTS:
                    violations.append(f"{modname}: 禁止 from {node.module!r} import")

    assert not violations, "silver_demo 违反冻结合规：以下 import 超出白名单\n  - " + "\n  - ".join(
        violations
    )


def test_runtime_core_does_not_import_visualizer() -> None:
    """T0-1（ADR-0015 §2.1.1）：Runtime Core（silver_demo 除 gateway 外）不得 import visualizer。"""
    import ast as _ast

    violations: list[str] = []
    for py in sorted(SILVER_DEMO_SRC.rglob("*.py")):
        rel = py.relative_to(SILVER_DEMO_SRC.parent)
        modname = ".".join(rel.with_suffix("").parts).removesuffix(".__init__")
        if modname == "silver_demo.gateway":
            continue  # Host 层允许（T0-2）
        try:
            tree = _ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, _ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, _ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if m.startswith("home_perception.visualizer"):
                    violations.append(f"{modname}: 禁止 import {m!r}（仅 gateway 允许）")
    assert not violations, "silver_demo Runtime Core 违反分层依赖契约：\n  - " + "\n  - ".join(
        violations
    )


def test_gateway_may_import_visualizer_viewer() -> None:
    """T0-2（ADR-0015 §2.1.1）：Host(gateway) 可 import visualizer.viewer，但仅此子包。"""
    import ast as _ast

    gw = SILVER_DEMO_SRC / "gateway.py"
    tree = _ast.parse(gw.read_text(encoding="utf-8"), filename=str(gw))
    imported: list[str] = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            imported.extend([node.module])
    vis = [m for m in imported if m.startswith("home_perception.visualizer")]
    assert vis, "gateway 应 import home_perception.visualizer.viewer（T0-2 未满足）"
    for m in vis:
        assert m == "home_perception.visualizer.viewer" or m.startswith(
            "home_perception.visualizer.viewer."
        ), f"gateway 只允许 import visualizer.viewer（子包），不允许 {m!r}"


def test_gateway_projects_via_live_adapter_only() -> None:
    """T0-4（ADR-0015 §5）：gateway 只经 viewer/live_adapter 投影，不自行构造 EvidenceProjection。"""
    gw_src = (SILVER_DEMO_SRC / "gateway.py").read_text(encoding="utf-8")
    # 正向：使用 viewer 的投影入口
    assert "build_live_presentation" in gw_src, "gateway 应经 viewer 的 build_live_presentation 投影"
    assert "ProjectionAccumulator" in gw_src, "gateway 应经 viewer 的 ProjectionAccumulator 累积"
    assert "render_case_viewer" in gw_src, "gateway 应经 viewer 的 render_case_viewer 渲染"
    # 负向：不得自行从 schema 构造 View Model（只读投影，不造事实）
    assert "EvidenceProjection(" not in gw_src, "gateway 不得自行构造 EvidenceProjection"
    assert (
        "from home_perception.visualizer.schema" not in gw_src
    ), "gateway 不得直接 import visualizer.schema"


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
    assert not violations, "silver_demo 违反冻结合规：出现禁止子模块引用\n  - " + "\n  - ".join(
        violations
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
        assert fc not in src, f"DemoGateway 禁止构造 7 层内部组件：出现 {fc!r}"


# ============================================================================
# 测试 3：类型只读 — bridge 不调 WarningEvent/ActionCommand 构造器
# ============================================================================


def test_bridge_does_not_construct_frozen_objects() -> None:
    """断言 bridge.py 不构造冻结对象，且退化为纯帧编码器（ADR-0036 Phase 3 收敛）。

    第二套事实模型符号（frame_result_to_view / collect_active_warnings /
    route_commands / build_memory_profiles）是否彻底移除，由
    ``test_bridge_has_no_second_fact_model`` 用 ``hasattr`` 守（避免误判 docstring 中的名词提及）。
    """
    import silver_demo.bridge as bridge_mod

    bridge_src = (SILVER_DEMO_SRC / "bridge.py").read_text(encoding="utf-8")
    # 不构造冻结对象（只读消费 / 纯格式转换）
    assert "WarningEvent(" not in bridge_src, "bridge.py 禁止构造 WarningEvent"
    assert "ActionCommand(" not in bridge_src, "bridge.py 禁止构造 ActionCommand"
    # 收敛后 bridge 仅暴露帧编码入口
    assert hasattr(bridge_mod, "encode_frame_to_base64_jpeg")


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
                pytest.fail(f"gateway.py:{i} 禁止构造冻结对象 {forbidden!r}（只读消费）")


# ============================================================================
# 测试 4：bridge 收敛为单一帧编码点（ADR-0036 Phase 3 收敛）
# ============================================================================


def test_bridge_has_no_second_fact_model() -> None:
    """断言 silver_demo.bridge 已移除第二套事实模型函数。

    ADR-0036 Phase 3 收敛后，唯一事实源为
    ``FrameResult → Live Adapter(ProjectionAccumulator) → EvidenceProjection → Case Viewer``。
    原 bridge 的 ``frame_result_to_view`` / ``collect_active_warnings`` /
    ``route_commands`` / ``build_memory_profiles`` 必须彻底移除——它们曾与
    ``DemoAggregateState`` 在 run_loop 里每帧生产 ``view`` 第二套事实模型。

    本测试守「不再回退」：若有人误把第二套事实模型重新加回 bridge，本测试 fail。
    """
    import silver_demo.bridge as bridge_mod

    leaked = [
        name
        for name in (
            "frame_result_to_view",
            "collect_active_warnings",
            "route_commands",
            "build_memory_profiles",
        )
        if hasattr(bridge_mod, name)
    ]
    assert not leaked, f"bridge 仍泄漏第二套事实模型符号：{leaked}"
    # 帧编码辅助函数必须保留（供 live 媒体渲染路径使用）
    assert hasattr(bridge_mod, "encode_frame_to_base64_jpeg")


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
