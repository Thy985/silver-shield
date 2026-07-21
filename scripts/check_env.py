"""环境预检（P0-11 演示可复现性 · 比赛现场可用）。

检查三类依赖是否就绪：

  - runtime : torch / opencv-python / ultralytics   （Demo 运行必需，AI 栈，当前装在 system Python 3.14）
  - web     : fastapi / uvicorn / websockets / python-multipart （网关）
  - test    : pytest / pytest-asyncio               （运行测试）

设计原则：本脚本**只使用标准库 + 可选 import**，绝不 import torch 等重依赖，
避免"还没装就因为 import 报错崩溃"。既可作为 CLI 直接运行：

    python scripts/check_env.py

也可被 ``run_demo.py`` 导入复用：

    from check_env import run_checks
    ok, lines, missing = run_checks()
    if not ok:
        ...

退出码：全部就绪返回 0；有缺失返回 1（方便 CI / 脚本串联）。
"""

from __future__ import annotations

import importlib
import sys
from collections import defaultdict

# (展示名, import 名, 类别, 安装提示)
CHECKS = [
    ("torch", "torch", "runtime", 'pip install -e ".[demo]"'),
    ("opencv-python", "cv2", "runtime", 'pip install -e ".[demo]"'),
    ("ultralytics", "ultralytics", "runtime", 'pip install -e ".[demo]"'),
    ("fastapi", "fastapi", "web", 'pip install -e ".[demo]"'),
    ("uvicorn", "uvicorn", "web", 'pip install -e ".[demo]"'),
    ("websockets", "websockets", "web", 'pip install -e ".[demo]"'),
    ("python-multipart", "multipart", "web", 'pip install -e ".[demo]"'),
    ("pytest", "pytest", "test", "pip install pytest pytest-asyncio"),
    ("pytest-asyncio", "pytest_asyncio", "test", "pip install pytest pytest-asyncio"),
]

CATEGORY_LABEL = {
    "runtime": "AI 运行时 (Demo 必需)",
    "web": "网关 (Web)",
    "test": "测试",
}

OK = "✓"
MISS = "✗"


def _version(mod: object) -> str:
    for attr in ("__version__", "version"):
        v = getattr(mod, attr, None)
        if isinstance(v, str):
            return v
    return "installed"


def run_checks() -> tuple[bool, list[str], list[tuple[str, str, str]]]:
    """返回 (全部就绪?, 输出行列表, 缺失项列表[(展示名, 类别, 安装提示)])。"""
    by_cat: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    missing: list[tuple[str, str, str]] = []

    for disp, imp, cat, hint in CHECKS:
        try:
            mod = importlib.import_module(imp)
            by_cat[cat].append((disp, _version(mod), True))
        except Exception:
            by_cat[cat].append((disp, "", False))
            missing.append((disp, cat, hint))

    lines: list[str] = []
    for cat in ("runtime", "web", "test"):
        rows = by_cat.get(cat, [])
        if not rows:
            continue
        lines.append(f"\n{CATEGORY_LABEL.get(cat, cat)}:")
        for disp, ver, present in rows:
            mark = OK if present else MISS
            ver_str = f"  {ver}" if (present and ver != "installed") else ""
            lines.append(f"  [{mark}] {disp}{ver_str}")

    return (len(missing) == 0), lines, missing


def main() -> int:
    ok, lines, missing = run_checks()
    print("银龄盾 Demo · 环境预检")
    print("=" * 48)
    for ln in lines:
        print(ln)

    if ok:
        print("\n✅ 环境就绪：可运行 `python scripts/run_demo.py`。")
        return 0

    print("\n❌ 缺少依赖，请按需安装：")
    seen_hints: set[str] = set()
    for disp, cat, hint in missing:
        print(f"  - {disp}  [{CATEGORY_LABEL.get(cat, cat)}]")
        if hint not in seen_hints:
            print(f"      安装：{hint}")
            seen_hints.add(hint)
    print(
        "\n说明：AI 运行时 (torch/ultralytics/opencv) 当前使用 system Python 3.14，"
        "未迁移到 managed venv（原型阶段可接受，详见 docs/DEVELOPMENT_ENV.md）。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
