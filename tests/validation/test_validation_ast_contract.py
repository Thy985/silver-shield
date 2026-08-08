"""``_ast_contract`` 助手自身的变异验证。

项目铁律：静态契约断言必须能在"违例注入"时**真的失败**，否则 T5/T7 只是装饰。
本文件用最小源码片段做正反两向验证，避免助手成为永真断言。
"""

from __future__ import annotations

import pytest
from _ast_contract import (
    assert_no_dependency,
    imported_modules,
    referenced_names,
    string_literals,
)

CLEAN = '''
"""这个文档字符串里写了 torch-free，也提到不调用 RuleEngine，还提到 data/demo 目录。"""

import numpy as np


def f(x):
    # 注释里也写 torch 与 VideoCapture
    return np.zeros(x)
'''

DIRTY_IMPORT = """
import torch


def f(x):
    return torch.zeros(x)
"""

DIRTY_ALIAS = """
import torch as t


def f(x):
    return t.zeros(x)
"""

DIRTY_FROM = """
from ultralytics import YOLO


def f():
    return YOLO()
"""

DIRTY_NAME = """
from home_perception.analysis.rule_engine import RuleEngine


def f():
    return RuleEngine()
"""

DIRTY_ATTR = """
import cv2


def f(p):
    return cv2.VideoCapture(p)
"""

DIRTY_LITERAL = """
PATH = "data/demo/real_video.mp4"
"""


def test_ast_contract_ignores_docstrings_and_comments():
    """正向：文档字符串/注释中的关键词不构成依赖（这正是子串扫描的假阳性来源）。"""
    assert_no_dependency(
        CLEAN,
        forbidden_modules=["torch", "ultralytics"],
        forbidden_names=["RuleEngine", "VideoCapture"],
        forbidden_literal_substrings=["data/demo"],
    )


@pytest.mark.parametrize(
    ("source", "kwargs"),
    [
        (DIRTY_IMPORT, {"forbidden_modules": ["torch"]}),
        (DIRTY_ALIAS, {"forbidden_modules": ["torch"]}),
        (DIRTY_FROM, {"forbidden_modules": ["ultralytics"]}),
        (DIRTY_NAME, {"forbidden_names": ["RuleEngine"]}),
        (DIRTY_ATTR, {"forbidden_names": ["VideoCapture"]}),
        (DIRTY_LITERAL, {"forbidden_literal_substrings": ["data/demo"]}),
    ],
)
def test_ast_contract_detects_real_violations(source, kwargs):
    """变异验证：真实依赖注入后断言必须失败（否则 T5/T7 是永真的）。"""
    with pytest.raises(AssertionError):
        assert_no_dependency(source, **kwargs)


def test_ast_contract_submodule_import_is_caught():
    """``import torch.nn`` 也算依赖 torch（前缀匹配，fail-closed）。"""
    with pytest.raises(AssertionError):
        assert_no_dependency("import torch.nn", forbidden_modules=["torch"])
    # 但不误伤同前缀的无关模块名
    assert_no_dependency("import torchless_helper", forbidden_modules=["torch"])


def test_ast_contract_primitives():
    assert "numpy" in imported_modules(CLEAN)
    assert "np" in referenced_names(CLEAN)
    assert "zeros" in referenced_names(CLEAN)  # Attribute
    # 文档字符串不进入字面量集合
    assert not any("torch-free" in s for s in string_literals(CLEAN))
    assert "data/demo/real_video.mp4" in string_literals(DIRTY_LITERAL)
