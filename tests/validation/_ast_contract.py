"""ADR-0032 契约测试助手：**AST 级**静态依赖校验。

为什么不用子串扫描（``"torch" not in inspect.getsource(m)``）：
- 假阳性：模块文档字符串里写"torch-free""不调用 RuleEngine"会被误判为真实依赖；
- 假阴性：``import   torch as t`` 之类的写法子串能过，但依赖真实存在；
- 语义弱：注释/字符串与可执行代码等价对待。

本助手改为解析 AST，**只**看可执行语义：
- ``imported_modules``：``import`` / ``from ... import`` 的模块名（含顶层包名）；
- ``referenced_names``：``Name`` / ``Attribute`` / 导入绑定名（不含文档字符串与注释）；
- ``string_literals``：字符串常量，但**排除**模块/类/函数文档字符串（用于查真实路径引用）。

所有函数同时接受"模块对象"与"源码字符串"，便于对助手自身做变异验证
（项目铁律：断言必须能在"违例注入"时真的失败）。
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterable
from types import ModuleType

_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _parse(module_or_source: ModuleType | str) -> ast.AST:
    src = (
        module_or_source
        if isinstance(module_or_source, str)
        else inspect.getsource(module_or_source)
    )
    return ast.parse(src)


def imported_modules(module_or_source: ModuleType | str) -> set[str]:
    """模块真实导入的模块名集合（含点分全名与其顶层包名）。"""
    out: set[str] = set()
    for node in ast.walk(_parse(module_or_source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
                out.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:  # 相对导入 ``from . import x`` 的 module 为 None
                out.add(node.module)
                out.add(node.module.split(".")[0])
            for alias in node.names:
                if node.module:
                    out.add(f"{node.module}.{alias.name}")
    return out


def referenced_names(module_or_source: ModuleType | str) -> set[str]:
    """代码中真实引用的标识符（变量名 / 属性名 / 导入绑定名）。

    文档字符串与注释不会进入结果——这正是它相对子串扫描的价值。
    """
    out: set[str] = set()
    for node in ast.walk(_parse(module_or_source)):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.add(alias.asname or alias.name)
    return out


def string_literals(module_or_source: ModuleType | str) -> set[str]:
    """字符串常量集合，**排除**模块/类/函数文档字符串。"""
    tree = _parse(module_or_source)
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _DOC_OWNERS):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            doc_nodes.add(id(body[0].value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in doc_nodes
    }


def assert_no_dependency(
    module_or_source: ModuleType | str,
    *,
    forbidden_modules: Iterable[str] = (),
    forbidden_names: Iterable[str] = (),
    forbidden_literal_substrings: Iterable[str] = (),
) -> None:
    """断言模块在**代码层面**不依赖给定模块 / 标识符 / 字面量片段（fail-closed）。"""
    label = "<source>" if isinstance(module_or_source, str) else module_or_source.__name__
    mods = imported_modules(module_or_source)
    names = referenced_names(module_or_source)
    literals = string_literals(module_or_source)

    for forbidden in forbidden_modules:
        hits = {m for m in mods if m == forbidden or m.startswith(f"{forbidden}.")}
        assert not hits, f"{label} 不应导入 {forbidden!r}，实际命中：{sorted(hits)}"

    for forbidden in forbidden_names:
        assert forbidden not in names, f"{label} 不应引用标识符 {forbidden!r}"

    for needle in forbidden_literal_substrings:
        hits = {s for s in literals if needle in s}
        assert not hits, f"{label} 不应包含字面量片段 {needle!r}，实际命中：{sorted(hits)}"
