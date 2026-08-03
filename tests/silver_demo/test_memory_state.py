"""区域⑥ Memory Context 粘性持有测试（方案1 修复面板随空帧闪空）。

验证 ``DemoAggregateState.ingest_memory`` 的「仅非空帧覆盖、空帧保留」语义，以及
``clear`` / ``snapshot`` / ``meta`` 对 ``memory_profiles`` 的处理。不依赖任何 Demo
运行实例 / 网络 / 重依赖（纯单元测试，CI torch-free 可跑）。
"""

from __future__ import annotations

from silver_demo.state import DemoAggregateState


def _profile(vid: str, n: int) -> dict[str, object]:
    return {"visitor_instance_id": vid, "memory_status": "active", "n_episodes": n}


def test_empty_frame_does_not_clear_sticky() -> None:
    agg = DemoAggregateState()
    assert agg.memory_profiles == []
    # 首个非空帧建立粘性态
    agg.ingest_memory([_profile("v1", 2)])
    assert agg.memory_profiles == [_profile("v1", 2)]
    # 后续空帧（绝大多数帧）必须保留上一画像，不清空
    agg.ingest_memory([])
    assert agg.memory_profiles == [_profile("v1", 2)]
    agg.ingest_memory([])
    assert agg.memory_profiles == [_profile("v1", 2)]


def test_nonempty_frame_overwrites() -> None:
    agg = DemoAggregateState()
    agg.ingest_memory([_profile("v1", 1)])
    agg.ingest_memory([_profile("v2", 3), _profile("v3", 5)])
    assert agg.memory_profiles == [_profile("v2", 3), _profile("v3", 5)]


def test_clear_resets_memory_profiles() -> None:
    agg = DemoAggregateState()
    agg.ingest_memory([_profile("v1", 2)])
    assert agg.memory_profiles  # 非空
    agg.clear(reset_session=True)
    assert agg.memory_profiles == []


def test_snapshot_and_meta_carry_memory_profiles() -> None:
    agg = DemoAggregateState()
    agg.ingest_memory([_profile("v1", 2)])
    snap = agg.snapshot()
    assert snap["memory_profiles"] == [_profile("v1", 2)]
    meta = agg.meta()
    assert meta["memory_profiles"] == [_profile("v1", 2)]
    # snapshot 返回独立拷贝，外部改不动权威态
    snap["memory_profiles"].append(_profile("vx", 9))
    assert agg.memory_profiles == [_profile("v1", 2)]
