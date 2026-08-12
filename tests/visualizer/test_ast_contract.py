"""ADR-0035 D3 · AST 契约：visualizer 零 import 生产/验证代码（验收 3）。

visualizer 在 import 图中必须是死胡同叶子——纯 JSON 消费 + stdlib 渲染。
本测试用 AST 扫描源码（非运行时 import），防延迟 import / TYPE_CHECKING 绕过。
"""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "home_perception" / "visualizer"
_FORBIDDEN_PREFIXES = (
    "home_perception.runtime",
    "home_perception.evaluation",
    "home_perception.integration",
    "home_perception.memory",
)
# 允许的自身引用（同包内部）。
_ALLOWED_SELF = ("home_perception.visualizer",)


def _collect_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_visualizer_imports_no_production_code():
    """visualizer/ 全部 .py 不得 import runtime/evaluation/integration/memory（验收 3）。

    变异验证：若未来有人加 ``from home_perception.integration.loop.report import ...``，
    本测试立即变红（AST 扫描，TYPE_CHECKING 内 import 同样命中）。
    """
    offenders: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        for module in _collect_imports(path):
            if module.startswith(_FORBIDDEN_PREFIXES) and not module.startswith(_ALLOWED_SELF):
                offenders.append(f"{path.relative_to(_PACKAGE_ROOT.parent)} -> {module}")
    assert offenders == [], f"visualizer 不得 import 生产/验证代码：{offenders}"


def test_visualizer_imports_are_stdlib_or_self():
    """visualizer 只允许 stdlib + 同包引用（无第三方运行时依赖，D4 零新增依赖）。"""
    allowed_top = {
        "ast", "html", "json", "pathlib", "typing",
        "functools", "collections", "re", "dataclasses", "enum",
        "__future__",  # `from __future__ import annotations` 是语言设施
    }
    seen: set[str] = set()
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        for module in _collect_imports(path):
            if module.startswith("home_perception.visualizer"):
                continue  # 同包内部引用（loader/renderer/schema 互引）合法
            top = module.split(".")[0]
            seen.add(top)
    assert seen <= allowed_top, f"visualizer 出现非 stdlib/同包依赖：{seen - allowed_top}"
