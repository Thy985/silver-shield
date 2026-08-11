"""ADR-0034 Phase B.3 · D7：闭环两枚指纹（expectation_fingerprint / loop_fingerprint）。

语义（回答用户验收口径的两个问题）：

- ``expectation_fingerprint`` —— **用什么标准评价**：评价标准
  （``IntegrationExpectationSuite``）的规范化哈希。仅含"验收标准本身"，
  **不含**场景输入 / 装配 / 运行时；改任一子期望（``min_records`` /
  ``risk_level`` / ``min_links`` ...）必变（t15）。
- ``loop_fingerprint`` —— **这次闭环是用哪套输入 + 标准 + 装配跑的**：
  6 成分（场景输入指纹 / 决策策略指纹 / sink 类型 / memory 后端 /
  跨模态开关 / expectation_fingerprint）的规范化哈希，**全非空 fail-closed**（t16）。

与 ADR-0033 的关系（实施计划 §2.5 纪律）：

- **不得** import 或修改 ``evaluation.fingerprint_fields.FINGERPRINT_COMPONENT_FIELDS``
  （ADR-0033 指纹成分单一来源，属于感知级 harness 的字段守恒域）；
- ``loop_fingerprint`` 的 ``harness_fp`` 成分只取 ADR-0032 场景输入指纹（generator
  fingerprint）的**结果字符串**，不参与 0033 的字段守恒（t16 由测试单独守护）。

隐私边界（同 ADR-0032）：指纹仅由"可复现性要素"构成，**不含**任何设备 ID /
家庭 ID / 用户标识。

fail-closed：缺成分即抛 ``ValueError``（不静默降级——指纹缺成分 = 我们无法
复述"这次怎么跑的"，比跑不出结果更危险）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Phase A=0.1.0 / **B=0.2.0** / C=1.0.0（实施计划 §2.5）。改版本 = 强制旧指纹失效，
# 用于"评价标准语义有破坏性变化"的场景（如新增子期望字段改变 canonical 形态）。
SCENARIO_INTEGRATION_VERSION = "0.2.0"

# 本模块 loop_fingerprint 的成分清单（单一来源，供漂移守卫 + 审计）。
LOOP_FINGERPRINT_COMPONENT_FIELDS: tuple[str, ...] = (
    "harness_fp",
    "policy_fp",
    "sink_type",
    "memory_backend",
    "cross_modal_enabled",
    "expectation_fp",
)

# 未声明期望时的空标准（与 ``IntegrationValidator.validate`` 的
# ``scenario.integration or IntegrationExpectationSuite()`` 同款语义——「没写标准」
# 也是一种标准，且必须可指纹化，否则 t15 的"改标准必变"无从谈起）。
_DEFAULT_SUITE: Any = None  # 惰性构造（延迟 import contracts，避免加载期拉起）


def _default_suite() -> Any:
    global _DEFAULT_SUITE
    if _DEFAULT_SUITE is None:
        from home_perception.validation.contracts import IntegrationExpectationSuite

        _DEFAULT_SUITE = IntegrationExpectationSuite()
    return _DEFAULT_SUITE


def _canonical_json(obj: Any) -> str:
    """规范化 JSON（键排序 + 紧凑分隔），保证同内容必同字符串。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_expectation_fingerprint(suite: Any | None) -> str:
    """计算评价标准指纹（``expectation_fingerprint``，t15）。

    Args:
        suite: ``IntegrationExpectationSuite``；``None`` 等价于空套件
            （validator 的 ``or IntegrationExpectationSuite()`` 同款语义）。

    Returns:
        sha256 hex（``SCENARIO_INTEGRATION_VERSION`` + 规范化 suite）。

    确定性：canonical JSON 只含**声明的约束**（``exclude_none=True`` 剔除
    显式 None = 未声明约束；默认值保留 = 显式契约）。同标准必同指纹；
    改任一子期望必变（t15 变异验证）。
    """
    from home_perception.validation.contracts import IntegrationExpectationSuite

    if suite is None:
        suite = _default_suite()
    if not isinstance(suite, IntegrationExpectationSuite):
        raise TypeError(
            f"suite 必须是 IntegrationExpectationSuite，收到 {type(suite).__name__}"
        )
    canonical_suite = suite.model_dump(mode="json", exclude_none=True)
    payload = {
        "version": SCENARIO_INTEGRATION_VERSION,
        "suite": canonical_suite,
    }
    return _sha256(_canonical_json(payload))


def compute_loop_fingerprint(
    harness_fp: str,
    *,
    policy_fp: str,
    sink_type: str,
    memory_backend: str,
    cross_modal_enabled: bool,
    expectation_fp: str,
) -> str:
    """计算闭环运行指纹（``loop_fingerprint``，t15/t16）。

    6 成分**全非空** fail-closed：5 个字符串成分须为非空 ``str``（类型错误抛
    ``TypeError``、空值抛 ``ValueError``）；``cross_modal_enabled`` 为 bool 成分，
    须是显式 ``bool``（``False`` 是合法配置「未启用跨模态」，不得与"缺失"混淆——
    ``None``/``0``/``""`` 均拒绝）。缺任一成分 raise，绝不静默降级。漂移守卫：
    入参集合与 ``LOOP_FINGERPRINT_COMPONENT_FIELDS`` 必须一致（防新增成分只改一处）。
    """
    str_components: dict[str, str] = {
        "harness_fp": harness_fp,
        "policy_fp": policy_fp,
        "sink_type": sink_type,
        "memory_backend": memory_backend,
        "expectation_fp": expectation_fp,
    }
    for name, value in str_components.items():
        if not isinstance(value, str):
            raise TypeError(
                f"loop_fingerprint 成分 {name} 必须是 str，收到 {value!r}（fail-closed）"
            )
        if not value:
            raise ValueError(f"loop_fingerprint 成分 {name} 不能为空（fail-closed）")
    if not isinstance(cross_modal_enabled, bool):
        raise TypeError(
            "loop_fingerprint 成分 cross_modal_enabled 必须是 bool，"
            f"收到 {cross_modal_enabled!r}（fail-closed）"
        )
    values: dict[str, Any] = {**str_components, "cross_modal_enabled": cross_modal_enabled}
    # 漂移守卫：常量与入参集合必须完全一致（防只改 signature 或只改常量）
    if set(values) != set(LOOP_FINGERPRINT_COMPONENT_FIELDS):
        raise ValueError(
            "compute_loop_fingerprint 入参与 LOOP_FINGERPRINT_COMPONENT_FIELDS 不一致"
            f"（实现={sorted(values)}，常量={sorted(LOOP_FINGERPRINT_COMPONENT_FIELDS)}）"
        )
    payload = {
        "version": SCENARIO_INTEGRATION_VERSION,
        "components": values,
    }
    return _sha256(_canonical_json(payload))


def loop_fingerprint_components(
    harness_fp: str,
    *,
    policy_fp: str,
    sink_type: str,
    memory_backend: str,
    cross_modal_enabled: bool,
    expectation_fp: str,
) -> dict[str, str]:
    """返回 loop_fingerprint 的成分（供审计 / t15"其余成分不变"逐项断言）。

    与 ``compute_loop_fingerprint`` 同入参、同 fail-closed 校验（缺成分即 raise），
    仅不计算哈希——两处共用语义，避免"审计显示 A、指纹算的是 B"。
    """
    # 复用 compute 的校验逻辑：先求值（缺失即 raise），再投影为字符串审计视图。
    computed = compute_loop_fingerprint(
        harness_fp,
        policy_fp=policy_fp,
        sink_type=sink_type,
        memory_backend=memory_backend,
        cross_modal_enabled=cross_modal_enabled,
        expectation_fp=expectation_fp,
    )
    del computed  # 仅作校验副作用；审计视图逐成分重建
    return {
        "harness_fp": harness_fp,
        "policy_fp": policy_fp,
        "sink_type": sink_type,
        "memory_backend": memory_backend,
        "cross_modal_enabled": "1" if cross_modal_enabled else "0",
        "expectation_fp": expectation_fp,
    }


__all__ = [
    "LOOP_FINGERPRINT_COMPONENT_FIELDS",
    "SCENARIO_INTEGRATION_VERSION",
    "compute_expectation_fingerprint",
    "compute_loop_fingerprint",
    "loop_fingerprint_components",
]
