"""场景级 audio_evidence 覆盖合约测试（ADR-0042 · Gate F 接入 · torch-free）。

Gate F F1/F2 需要 telephone_risk E2E 场景开启音频证据评估器装配，但
``AudioEvidenceConfig.enabled`` 全局默认 False（零开销灰度纪律）。本测试锁定
演示层的场景覆盖合约（与 ``test_p0_11_5a_stable_high.py`` 的 rule_overrides /
realtime_risk 范式同型）：

1. **字段合约**：``ScenarioConfig.audio_evidence`` 默认 None + 接受 dict；
2. **E2E 场景接线**：``e2e_telephone_risk.yaml`` 必须含
   ``audio_evidence: {enabled: true}``（Gate F 前置条件，防止后续改 yaml 漏检）；
3. **覆写逻辑**：``DemoGateway._apply_scenario_audio_evidence_overrides``
   把 ``enabled`` 写入 ``hp_settings.audio_evidence``；None 时复位基线 False；
   未知键告警跳过；跨场景不残留 True（防状态泄漏）。

**白名单纪律**：仅 ``enabled`` 可覆盖——升级参数 N/T/M、monitor 门槛、
``ceiling_monitor_only``、``escalate_enabled`` 属 ADR-0042 Owner 拍板项，
场景 YAML 旁路一律拒绝（有专门断言）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_scenario(**kwargs):
    from silver_demo.scenarios import ScenarioConfig

    base = {
        "scenario_id": "t",
        "source": "t",
        "start_time": datetime(2026, 7, 22, tzinfo=UTC),
    }
    base.update(kwargs)
    return ScenarioConfig(**base)


def _make_gateway(scenario):
    """object.__new__ 绕过 DemoGateway.__init__（避免 YOLO 加载），注入 stub。"""
    from types import SimpleNamespace

    from home_perception.core.config import AudioEvidenceConfig
    from silver_demo.gateway import DemoGateway

    gw = object.__new__(DemoGateway)
    gw.scenario = scenario
    gw.hp_settings = SimpleNamespace(audio_evidence=AudioEvidenceConfig())
    return gw


# ============================================================================
# 1. 字段合约
# ============================================================================


def test_scenario_config_audio_evidence_defaults_to_none():
    """ScenarioConfig.audio_evidence 默认 None（不影响全局默认 enabled=False）。"""
    sc = _make_scenario()
    assert sc.audio_evidence is None


def test_scenario_config_accepts_audio_evidence():
    sc = _make_scenario(audio_evidence={"enabled": True})
    assert sc.audio_evidence == {"enabled": True}


# ============================================================================
# 2. E2E 场景接线（Gate F 前置条件）
# ============================================================================


def test_e2e_telephone_risk_yaml_has_audio_evidence_enabled():
    """e2e_telephone_risk.yaml 必须含 ``audio_evidence: {enabled: true}``。

    Gate F F1/F2 的前置条件：该场景装配 RealTimeAudioRiskEvaluator。
    同时断言升级参数未被旁路改写（白名单纪律）。
    """
    from silver_demo.scenarios import load_scenario

    path = REPO_ROOT / "config" / "demo" / "scenarios" / "e2e_telephone_risk.yaml"
    sc = load_scenario(path)
    assert sc.audio_evidence == {"enabled": True}, (
        f"e2e_telephone_risk.yaml 必须且仅开启 audio_evidence.enabled；"
        f"当前 {sc.audio_evidence!r}"
    )


# ============================================================================
# 3. 覆写逻辑
# ============================================================================


def test_apply_scenario_audio_evidence_overrides_writes_enabled():
    """覆盖方法把 scenario.audio_evidence.enabled 写入 hp_settings.audio_evidence。"""
    sc = _make_scenario(audio_evidence={"enabled": True})
    gw = _make_gateway(sc)

    gw._apply_scenario_audio_evidence_overrides()

    assert gw.hp_settings.audio_evidence.enabled is True
    # 白名单外的字段必须保持基线默认值（Owner 拍板项不可经场景旁路）
    assert gw.hp_settings.audio_evidence.ceiling_monitor_only is True
    assert gw.hp_settings.audio_evidence.escalate_enabled is False
    assert gw.hp_settings.audio_evidence.raise_min_count is None


def test_apply_scenario_audio_evidence_overrides_no_op_when_none():
    """audio_evidence=None 时复位基线 False——即使上一场景残留 True 也被清掉（防泄漏）。"""
    sc = _make_scenario(audio_evidence=None)
    gw = _make_gateway(sc)
    gw.hp_settings.audio_evidence.enabled = True  # 模拟上一场景残留

    gw._apply_scenario_audio_evidence_overrides()

    assert gw.hp_settings.audio_evidence.enabled is False


def test_apply_scenario_audio_evidence_overrides_cross_scenario_reset():
    """跨场景切换：开音频场景 → 无覆盖场景 → enabled 复位 False（无残留）。"""
    on = _make_gateway(_make_scenario(scenario_id="a", audio_evidence={"enabled": True}))
    on._apply_scenario_audio_evidence_overrides()
    assert on.hp_settings.audio_evidence.enabled is True

    off = _make_gateway(_make_scenario(scenario_id="b"))
    off._apply_scenario_audio_evidence_overrides()
    assert off.hp_settings.audio_evidence.enabled is False


def test_apply_scenario_audio_evidence_overrides_unknown_key_warns_skips():
    """未知键：告警 + 跳过 + 不写入配置对象；已知键照常生效。"""
    sc = _make_scenario(
        audio_evidence={"enabled": True, "ceiling_monitor_only": False, "nonsense_key_xyz_999": 1}
    )
    gw = _make_gateway(sc)

    gw._apply_scenario_audio_evidence_overrides()

    assert gw.hp_settings.audio_evidence.enabled is True
    # 硬门控字段绝不被场景 YAML 翻转
    assert gw.hp_settings.audio_evidence.ceiling_monitor_only is True
    assert not hasattr(gw.hp_settings.audio_evidence, "nonsense_key_xyz_999")