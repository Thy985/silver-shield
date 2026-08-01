"""MemoryPolicy ABC 契约测试（Slice 1 · Stage A）。

> 验证 ABC 接口约束：不能直接实例化，子类必须实现全部抽象方法。
> 不测具体实现逻辑（DefaultEpisodeBuilder 见 Slice 4）。
"""

from __future__ import annotations

import pytest

from home_perception.memory import MemoryPolicy


def test_memory_policy_is_abstract():
    """MemoryPolicy 是 ABC，不能直接实例化。"""
    with pytest.raises(TypeError):
        MemoryPolicy()  # type: ignore[abstract]


def test_partial_implementation_rejected():
    """子类必须实现全部 3 个抽象方法，否则不能实例化。"""

    class PartialPolicy(MemoryPolicy):
        """只实现 1 个方法，缺 2 个。"""

        def transform_short_term(self, state_snapshot, transition):
            return None

    with pytest.raises(TypeError):
        PartialPolicy()  # type: ignore[abstract]


def test_full_implementation_accepted():
    """子类实现全部 3 个抽象方法可实例化。"""

    class FullPolicy(MemoryPolicy):
        def transform_short_term(self, state_snapshot, transition, current_record=None):
            return None

        def project_episode(self, visitor_event, warnings, actions):
            return None

        def aggregate_semantic(self, episodes, dimension, period_key):
            return None

    # 不抛异常即通过
    policy = FullPolicy()
    assert policy is not None
    # 签名可调用
    assert callable(policy.transform_short_term)
    assert callable(policy.project_episode)
    assert callable(policy.aggregate_semantic)
