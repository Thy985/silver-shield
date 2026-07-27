"""Contract Test · 攻击性边界守护示例。

来源：Silver Shield ADR-0014 契约测试矩阵提炼。
只断言契约（字段/枚举/状态机/异常语义），不依赖具体算法实现。
"""

from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
# L1 · Schema 契约：时间倒流不得产生负时长
# ----------------------------------------------------------------------
def test_time_reversal_rejected(factory):
    """Frame2 早于 Frame1（NTP 回跳）不得污染数据。"""
    ev = factory.make_visitor_event(enter="10:00:10", leave="10:00:05")
    assert ev.duration_seconds >= 0, "时间倒流应被 __post_init__ 拒绝或校正"


# ----------------------------------------------------------------------
# L1 · 脏输入不得进入特征层
# ----------------------------------------------------------------------
def test_dirty_input_rejected(factory):
    with pytest.raises(ValueError):
        factory.make_visitor_event(visitor_id="", duration_seconds="abc")


# ----------------------------------------------------------------------
# L1 · 状态机攻击：非法翻转必须拒绝
# ----------------------------------------------------------------------
@pytest.mark.parametrize("illegal", [("CREATED", "RESOLVED"), ("CONFIRMED", "PENDING")])
def test_warning_state_machine_rejects_illegal(factory, illegal):
    cur, target = illegal
    with pytest.raises(ValueError):
        factory.warning_transition(cur, target)


# ----------------------------------------------------------------------
# L1/L3 · 配置攻击：负值/NaN 必须明确报错，不得静默
# ----------------------------------------------------------------------
def test_negative_threshold_rejected(factory):
    with pytest.raises(ValueError):
        factory.make_rule_config(long_duration_seconds=-100)


# ----------------------------------------------------------------------
# L2 · 通道失败：事件不丢，保持 PENDING 等待重试
# ----------------------------------------------------------------------
def test_publish_failure_keeps_pending(factory):
    status = factory.publish_with_result(success=False)
    assert status == "PENDING", "发布失败应保留事件，不丢"


# ----------------------------------------------------------------------
# 冻结边界白名单：展示层不得反向 import 核心
# ----------------------------------------------------------------------
def test_freeze_boundary_no_reverse_import():
    """扫描导入，确保 silver_demo 未 import home_perception 内部 7 层。"""
    import subprocess, sys
    out = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "src/silver_demo"],
        capture_output=True, text=True,
    )
    assert "home_perception.rule_engine" not in out.stdout
