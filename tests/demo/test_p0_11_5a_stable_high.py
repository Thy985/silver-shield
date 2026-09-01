"""P0-11.5a 稳定 HIGH 闭环回归测试（torch-free 合约层 · ADR-0017）。

P0-11.5a 目标：CCTV 夜间场景确定性产出 HIGH + family + community 命令。
本测试断言使其稳定的两个关键合约（不依赖 torch / YOLO / 真实视频）：

1. **场景规则覆盖**：`ScenarioConfig.rule_overrides` 字段 + 解析正确 +
   ``DemoGateway._apply_scenario_rule_overrides`` 把键写入 ``pipeline.rule_engine.thresholds``。
   —— CCTV 场景 ``repeat_visit_count: 3``（loop=false 后需真实 3 次重复）由此生效，HighRiskApproachRule
   (LongDuration + RepeatVisit + OddHour 同帧全中) 才能确定性触发 HIGH。

2. **家属联系配置**：`config/default.yaml` 的 ``action.family_contact`` 已配置，
   使 ``NOTIFY_FAMILY`` LOW 警告派发 ``SEND_FAMILY_MESSAGE``（而非因 family_contact=null
   降级为 LOG_ONLY），家属命令桶非空、3 角色视图「家属确认」有内容可展示。

> 本测试与 ``tests/test_action.py`` 的 dispatcher 单元测试互补：
> 后者覆盖 dispatcher 路由逻辑（null vs 非 null），本测试覆盖**演示配置**的
> 实际接线（防止后续改 config 时意外把 family_contact 改回 null 而漏检）。
> 端到端 3-loop 验证见 ``scripts/measure_cctv_high.py``（runtime-gated）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ============================================================================
# 1. 场景规则覆盖（rule_overrides）合约
# ============================================================================


def test_scenario_config_rule_overrides_defaults_to_none():
    """ScenarioConfig.rule_overrides 默认 None（不影响全局默认 repeat_visit_count=3）。"""
    from silver_demo.scenarios import ScenarioConfig

    sc = ScenarioConfig(
        scenario_id="t",
        source="t",
        start_time=datetime(2026, 7, 22, tzinfo=UTC),
    )
    assert sc.rule_overrides is None


def test_scenario_config_accepts_rule_overrides():
    from silver_demo.scenarios import ScenarioConfig

    sc = ScenarioConfig(
        scenario_id="t",
        source="t",
        start_time=datetime(2026, 7, 22, tzinfo=UTC),
        rule_overrides={"repeat_visit_count": 2},
    )
    assert sc.rule_overrides == {"repeat_visit_count": 2}


def test_cctv_surveillance_yaml_has_rule_overrides_repeat_visit_count_2():
    """CCTV 夜间场景 yaml 必须含 ``rule_overrides: {repeat_visit_count: 2}``。

    该文件本地 untracked（不入库），缺失时 skip——但默认应存在。

    P0-11.5a 5分钟确定性 HIGH 闭环核心：repeat_visit_count=2 让视频内 2 次进出
    触发 RepeatVisitRule，叠加 OddHour + LongDuration 同帧命中 → HighRiskApproachRule → HIGH。
    测试于 2026-09-01 从 _3 恢复为 _2（PR #321 误将其改成 3 导致演示效果降级为 LOW-only）。
    """
    from silver_demo.scenarios import load_scenario

    path = REPO_ROOT / "config" / "demo" / "scenarios" / "cctv_surveillance_suspicious.yaml"
    if not path.is_file():
        pytest.skip(f"CCTV 场景 yaml 不存在（演示者本地提供）: {path}")
    sc = load_scenario(path)
    assert sc.scenario_id == "cctv_surveillance_suspicious"
    assert sc.source_type == "video_file"
    assert sc.rule_overrides == {"repeat_visit_count": 2}, (
        f"CCTV 场景 repeat_visit_count 必须为 2（P0-11.5a 5分钟确定性 HIGH 闭环）；当前 {sc.rule_overrides!r}"
    )


def test_apply_scenario_rule_overrides_writes_thresholds():
    """_apply_scenario_rule_overrides 将 ``scenario.rule_overrides`` 写入 pipeline 阈值。

    用 ``object.__new__`` 绕过 ``DemoGateway.__init__``（避免触发 YOLO 加载），
    直接注入 stub ``scenario`` + stub ``pipeline``，验证覆写逻辑。
    """
    from silver_demo.gateway import DemoGateway
    from silver_demo.scenarios import ScenarioConfig

    sc = ScenarioConfig(
        scenario_id="t",
        source="t",
        start_time=datetime(2026, 7, 22, tzinfo=UTC),
        rule_overrides={"repeat_visit_count": 2},
    )
    thresholds = SimpleNamespace(repeat_visit_count=3, odd_hour_set=(0,))
    pipeline = SimpleNamespace(rule_engine=SimpleNamespace(thresholds=thresholds))
    gw = object.__new__(DemoGateway)
    gw.scenario = sc
    gw.pipeline = pipeline

    gw._apply_scenario_rule_overrides()

    assert thresholds.repeat_visit_count == 2
    # 未覆盖的键保持原值
    assert thresholds.odd_hour_set == (0,)


def test_apply_scenario_rule_overrides_no_op_when_none():
    """rule_overrides=None 时直接返回，不动阈值。"""
    from silver_demo.gateway import DemoGateway
    from silver_demo.scenarios import ScenarioConfig

    sc = ScenarioConfig(
        scenario_id="t",
        source="t",
        start_time=datetime(2026, 7, 22, tzinfo=UTC),
        rule_overrides=None,
    )
    thresholds = SimpleNamespace(repeat_visit_count=3)
    pipeline = SimpleNamespace(rule_engine=SimpleNamespace(thresholds=thresholds))
    gw = object.__new__(DemoGateway)
    gw.scenario = sc
    gw.pipeline = pipeline

    gw._apply_scenario_rule_overrides()

    assert thresholds.repeat_visit_count == 3


def test_apply_scenario_rule_overrides_unknown_key_warns_skips():
    """未知键：告警 + 跳过，不抛错（容错：后续新增阈值字段不破坏老配置）。"""
    from silver_demo.gateway import DemoGateway
    from silver_demo.scenarios import ScenarioConfig

    sc = ScenarioConfig(
        scenario_id="t",
        source="t",
        start_time=datetime(2026, 7, 22, tzinfo=UTC),
        rule_overrides={"repeat_visit_count": 2, "nonsense_key_xyz_999": 99},
    )
    thresholds = SimpleNamespace(repeat_visit_count=3)
    pipeline = SimpleNamespace(rule_engine=SimpleNamespace(thresholds=thresholds))
    gw = object.__new__(DemoGateway)
    gw.scenario = sc
    gw.pipeline = pipeline

    # 不应抛错
    gw._apply_scenario_rule_overrides()

    assert thresholds.repeat_visit_count == 2
    # 未知键未被错误地塞进 thresholds
    assert not hasattr(thresholds, "nonsense_key_xyz_999")


# ============================================================================
# 2. 家属联系配置（family_contact）合约
# ============================================================================


def test_default_yaml_family_contact_is_configured():
    """``config/default.yaml`` 必须配置 ``action.family_contact``，使 NOTIFY_FAMILY 不降级为 LOG_ONLY。

    此前 ``family_contact: null`` → ``NOTIFY_FAMILY`` LOW 警告全部降级 LOG_ONLY →
    family 命令桶为空 → 三角色视图「家属确认」无可展示内容。
    P0-11.5a 修复：配 demo 联系信息（elder_001 / 家属演示 / +8613800000000）。
    """
    from home_perception.core.config import Settings

    settings = Settings.load(str(REPO_ROOT / "config" / "default.yaml"))
    fc = settings.action.family_contact
    assert fc is not None, (
        "config/default.yaml action.family_contact 必须配置；"
        "否则 NOTIFY_FAMILY 降级 LOG_ONLY，family 命令桶为空，P0-11.4 家属视图无内容"
    )
    for key in ("elder_id", "name", "phone"):
        assert getattr(fc, key), f"family_contact 字段 {key!r} 不能为空"


def test_dispatcher_notify_family_with_configured_contact_emits_send_family_message():
    """端到端：配 family_contact + NOTIFY_FAMILY 警告 → SEND_FAMILY_MESSAGE（不是 LOG_ONLY）。

    证明 demo 的 family_contact 配置切实接进 dispatcher 派发链路。
    """
    from home_perception.action import ActionDispatcher, DispatcherConfig, FamilyContact
    from home_perception.analysis.warning import WarningEvent
    from home_perception.core.config import Settings

    settings = Settings.load(str(REPO_ROOT / "config" / "default.yaml"))
    fc = settings.action.family_contact
    assert fc is not None, "前置：family_contact 必须配置"

    dispatcher = ActionDispatcher(
        DispatcherConfig(
            family_contact=FamilyContact(
                elder_id=fc.elder_id,
                name=fc.name,
                phone=fc.phone,
                relation=fc.relation,
            ),
        )
    )
    warning = WarningEvent(
        elder_id="elder_001",
        device_id="home_entry_01",
        risk_level="LOW",
        recommended_action="NOTIFY_FAMILY",
        trigger_events=[
            {
                "event_id": f"{uuid4()}:abnormal_dwell",
                "event_type": "abnormal_dwell",
                "score": 0.5,
                "timestamp": 1.0,
            }
        ],
        reason_summary=["异常停留"],
        warning_id=uuid4(),
    )
    cmds = dispatcher.dispatch(warning)
    assert len(cmds) == 1
    assert cmds[0].command_type == "SEND_FAMILY_MESSAGE", (
        f"family_contact 已配时 NOTIFY_FAMILY 必须派发 SEND_FAMILY_MESSAGE，"
        f"实际 {cmds[0].command_type!r}"
    )
    # 兜底：确保不会回退到 LOG_ONLY
    assert cmds[0].command_type != "LOG_ONLY"
