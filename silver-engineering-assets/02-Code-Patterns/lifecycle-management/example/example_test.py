"""Lifecycle Management · 测试示例。

验证两个最关键的不变式：
1. 循环重放后风险仍能稳定重现（状态被重建，不污染）；
2. reset 后会话状态全空（确定性恢复）。

真实项目用 pytest + TestClient 跑；此处用同步骨架演示断言逻辑。
"""

from session import RuntimeSession, Pipeline


def _make_session():
    s = RuntimeSession()
    s.assemble(scenario="demo")
    return s


def test_rebuild_clears_cross_frame_state():
    """循环重放必须清空跨帧累积状态，否则 warning 不再产生。"""
    s = _make_session()
    s.aggregate = {"warnings": [{"warning_id": "w1"}], "loop_count": 3}
    s._rebuild_pipeline()                       # 复用模型 + 清状态
    assert s.aggregate == {}, "跨帧状态未被清空"
    assert s.pipeline.detector is not None, "模型不应被重载"


def test_reset_restores_clean_session():
    """reset 后 frame_index/loop_count 归零、聚合状态为空。"""
    s = _make_session()
    s.frame_index = 99
    s.loop_count = 5
    s.aggregate = {"warnings": [{"warning_id": "w1"}]}
    s.store = {"w1": {"status": "family_handled"}}
    # 同步版 reset（真实为 async，此处直接调内部）
    s.stop()
    s._rebuild_pipeline()
    assert s.frame_index == 0
    assert s.loop_count == 0
    assert s.aggregate == {}
    assert s.store == {}


def test_pipeline_reuse_detector_on_rebuild():
    """重建流水线必须复用已加载 detector（避免重载权重）。"""
    s = _make_session()
    d0 = s.pipeline.detector
    s._rebuild_pipeline()
    assert s.pipeline.detector is d0, "rebuild 不应重载模型"
