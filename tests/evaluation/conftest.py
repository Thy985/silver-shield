"""ADR-0033 评估层测试的路径接缝。

把 ``tests/validation`` 挂进 ``sys.path``，复用 ADR-0032 已做变异验证的 AST 契约助手
（``_ast_contract``），避免在本包内重复实现一套弱于它的子串扫描。
"""

from __future__ import annotations

import sys
from pathlib import Path

_VALIDATION_TESTS = Path(__file__).resolve().parents[1] / "validation"
if str(_VALIDATION_TESTS) not in sys.path:
    sys.path.insert(0, str(_VALIDATION_TESTS))
