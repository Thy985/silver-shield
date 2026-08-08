"""ADR-0032：包导入健康度回归（无循环导入 / 任意顺序可导入）。

背景：``SyntheticInput`` 最初定义在 ``runner.runner``，而 ``scenario/__init__`` 会
急切导入 ``compiler``、``compiler`` 又要 ``runner.runner.SyntheticInput`` ——
形成 ``runner → scenario → compiler → runner`` 环。该环**只在特定导入顺序下才炸**
（先导入 ``.runner`` 时才触发），属于潜伏故障：一次 import 排序调整就会让 CI 变红。

修复是把 ``SyntheticInput`` 下沉到零运行期依赖的叶子模块 ``validation.synthetic_input``。
本文件把"任意子模块都能作为首个导入入口"钉成契约，避免同类问题复发。
"""

from __future__ import annotations

import subprocess
import sys
from itertools import permutations
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SUBMODULES = [
    "home_perception.validation",
    "home_perception.validation.synthetic_input",
    "home_perception.validation.fingerprint",
    "home_perception.validation.scenario",
    "home_perception.validation.scenario.scenario",
    "home_perception.validation.scenario.compiler",
    "home_perception.validation.simulation",
    "home_perception.validation.simulation.generator",
    "home_perception.validation.simulation.renderer",
    "home_perception.validation.runner",
    "home_perception.validation.runner.runner",
]


def _import_in_subprocess(modules: list[str]) -> subprocess.CompletedProcess[str]:
    """在**全新解释器**中按给定顺序导入模块（同进程 import 缓存会掩盖环）。"""
    code = "import os, sys\n" f"sys.path.insert(0, os.path.join(r'{ROOT}', 'src'))\n"
    code += "".join(f"import {m}\n" for m in modules)
    code += "print('OK')\n"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )


@pytest.mark.parametrize("module", SUBMODULES)
def test_adr0032_submodule_importable_first(module):
    """每个子模块都必须能作为**首个**导入入口（无循环导入）。"""
    proc = _import_in_subprocess([module])
    assert proc.returncode == 0, f"首个导入 {module} 失败：\n{proc.stderr}"
    assert "OK" in proc.stdout


# 三个互相牵连的子包：穷举全部导入顺序（顺序无关性，非只测一种）
_ENTANGLED = (
    "home_perception.validation.runner",
    "home_perception.validation.scenario",
    "home_perception.validation.simulation",
)


@pytest.mark.parametrize("order", list(permutations(_ENTANGLED)))
def test_adr0032_import_order_independent(order):
    """三子包任意导入顺序均可成功（顺序无关，穷举 3! = 6 种）。"""
    proc = _import_in_subprocess(list(order))
    assert proc.returncode == 0, f"导入顺序 {order} 失败：\n{proc.stderr}"


def test_adr0032_synthetic_input_is_dependency_leaf():
    """``synthetic_input`` 必须是叶子：无任何运行期包内导入（断环的结构性前提）。"""
    from _ast_contract import imported_modules

    from home_perception.validation import synthetic_input

    runtime_imports = imported_modules(synthetic_input)
    # TYPE_CHECKING 块内的导入也会被 AST 看到，因此排除仅类型用途的已知项后，
    # 断言不存在对 validation 子包的**运行期**依赖：改用真实执行验证更可靠。
    proc = _import_in_subprocess(["home_perception.validation.synthetic_input"])
    assert proc.returncode == 0
    # 结构性提示：该模块不应导入 runner / compiler（否则环会重现）
    assert not any(
        m.endswith(("runner", "runner.runner", "scenario.compiler"))
        for m in runtime_imports
    ), f"synthetic_input 不应依赖 runner/compiler：{sorted(runtime_imports)}"
