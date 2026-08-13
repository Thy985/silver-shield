"""ADR-0035 D3 · AST 契约：visualizer import 边界（验收 3 / D3-1 单向锁定）。

import 图约束（ADR-0035 D3 §2.4.1 + Owner Decision Record D3-1）：
- 全包禁区：visualizer 不得反向 import runtime/evaluation/integration/memory。
- 单向例外（D3-1）：仅 ``visualizer/video/`` 子包可作为「呈现适配层」import
  ``home_perception.validation`` / ``home_perception.audio``（presentation adapter
  dependency，非 business dependency）；反向（validation/audio → visualizer.video）禁止。
- ``visualizer/video/`` 额外允许栅格化/合成依赖（Pillow-first，D3-8）+ pydantic + yaml。
- visualizer 非 video 部分（D1/D2）维持 stdlib + 同包零第三方依赖。

本测试用 AST 扫描源码（非运行时 import），防延迟 import / TYPE_CHECKING 绕过。
"""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "home_perception" / "visualizer"

# 全包禁区：visualizer 永远不得反向 import 业务包（含 D3-1 反向方向）。
# 注意：home_perception.validation / home_perception.audio 不在禁区——D3-1 允许
# visualizer.video → validation/audio 单向（presentation adapter dependency）。
_FORBIDDEN_PREFIXES = (
    "home_perception.runtime",
    "home_perception.evaluation",
    "home_perception.integration",
    "home_perception.memory",
)

# D3-1 单向例外：仅 visualizer/video/ 允许 import 的生产包（整前缀匹配）。
_VIDEO_PRODUCER_PREFIXES = (
    "home_perception.validation",
    "home_perception.audio",
)

# 非 video 部分（D1/D2）允许的 stdlib 顶层（+ 同包引用）。
_STDLIB_TOP = {
    "ast", "html", "json", "pathlib", "typing",
    "functools", "collections", "re", "dataclasses", "enum",
    "io", "os", "sys", "math", "warnings", "logging",
    "importlib",  # PEP 562 惰性转发（video/__init__.py 避免加载期拉 cv2/PIL）
    "unicodedata",  # CJK 显示宽度计算（text_safety 字幕截断）
    "traceback",  # CLI --verbose 保留栈（脚本侧，非包内）
    "__future__",  # `from __future__ import annotations` 是语言设施
}

# visualizer/video/ 额外允许的第三方/生产依赖顶层（D3-8 Pillow-first + pydantic schema）。
_VIDEO_EXTRA_TOP = {
    "cv2", "numpy", "PIL", "pydantic", "yaml",
}


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


def _is_video_subpackage(path: Path) -> bool:
    rel = path.relative_to(_PACKAGE_ROOT)
    return bool(rel.parts) and rel.parts[0] == "video"


def test_visualizer_imports_no_production_code():
    """visualizer/ 全部 .py 不得 import runtime/evaluation/integration/memory（验收 3）。

    变异验证：若未来有人加 ``from home_perception.integration.loop.report import ...``，
    本测试立即变红（AST 扫描，TYPE_CHECKING 内 import 同样命中）。
    """
    offenders: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        for module in _collect_imports(path):
            if module.startswith(_FORBIDDEN_PREFIXES):
                offenders.append(f"{path.relative_to(_PACKAGE_ROOT.parent)} -> {module}")
    assert offenders == [], f"visualizer 不得 import 业务包：{offenders}"


def test_visualizer_imports_are_stdlib_or_self():
    """visualizer 依赖边界（D1/D2 stdlib-only，video 子包放开 D3-1 + D3-8 依赖）。

    - 非 video 部分：仅 stdlib + 同包（零第三方运行时依赖）。
    - video 子包：额外允许 cv2/numpy/PIL/pydantic/yaml，且允许 D3-1 单向 import
      home_perception.validation / home_perception.audio；其余一律非法。
    """
    offenders: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        is_video = _is_video_subpackage(path)
        for module in _collect_imports(path):
            # 同包内部引用（loader/renderer/schema/video 互引）合法。
            if module.startswith("home_perception.visualizer"):
                continue
            # D3-1 单向例外：video 子包允许 import validation/audio。
            if is_video and module.startswith(_VIDEO_PRODUCER_PREFIXES):
                continue
            top = module.split(".")[0]
            allowed = _STDLIB_TOP | _VIDEO_EXTRA_TOP if is_video else _STDLIB_TOP
            if top not in allowed:
                offenders.append(
                    f"{path.relative_to(_PACKAGE_ROOT.parent)} -> {module} (top={top})"
                )
    assert offenders == [], f"visualizer 出现非允许依赖：{offenders}"
