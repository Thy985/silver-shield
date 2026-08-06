"""C1 不变量共享测试常量（tests 内部复用，口径唯一）。

多个测试文件（``test_invariants`` / ``test_orchestrator`` / ``test_audio_patterns`` /
``test_memory_replay_dataset``）此前各自维护禁止字段 / 白名单集合，新增音频测试的
集合曾遗漏 ``recommended_action``——统一收敛到本模块，避免未来字段回潮时出现
「某个测试未覆盖」的口径漂移（AGENTS.md 测试有效性铁律）。

- ``CONSUMER_FORBIDDEN_FIELDS``：Consumer 契约（ReasoningInput / RiskPattern /
  ReasoningResult 等）**结构性禁止**出现的判定字段。**绝不允许**把其中任一加入契约。
- ``REASONING_INPUT_FIELD_WHITELIST``：``ReasoningInput`` 字段白名单（8 个，
  含 ADR-0027 D6 新增的 ``modalities`` 提示字段）；与契约声明逐字段一致，防漂移。
"""

from __future__ import annotations

CONSUMER_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "risk_score",
        "score",
        "decision",
        "warning",
        "recommended_action",
    }
)

REASONING_INPUT_FIELD_WHITELIST: frozenset[str] = frozenset(
    {
        "current_event",
        "historical_context",
        "visitor_profile",
        "risk_pattern",
        "evidence_refs",
        "previous_actions",
        "conflicts",
        "modalities",
    }
)
