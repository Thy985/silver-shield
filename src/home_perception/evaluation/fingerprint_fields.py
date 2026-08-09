"""D4 指纹成分字段**单一来源**（防 harness 与守恒校验漂移，Medium 9 round 4）。

``compute_harness_fingerprint``（harness.py）的入参集合与 ``BenchmarkABRun`` 守恒校验
（ab_runner.py）依赖的 provenance 字段必须一一对应。此处为唯一权威列表，两处都从这里取：
未来新增指纹成分（如 ``detector_config``）只改这一处，两处同步生效——真正 fail-closed，
避免只改 harness 忘记改守恒校验导致漏检（或反之）。
"""

from __future__ import annotations

# 指纹成分字段（键名 = ``provenance`` 键 / ``compute_harness_fingerprint`` 入参名）。
# 本模块为纯常量叶子，零依赖、零急切 import 副作用，可被 harness 与 ab_runner 安全引用。
FINGERPRINT_COMPONENT_FIELDS: tuple[str, ...] = (
    "scenario_set_id",
    "code_version",
    "generator_fingerprint",
    "policy_fingerprint",
    "model_fingerprint",
    "runtime_dependencies",
)

# 守恒校验所需 provenance 字段 = 全部指纹成分减去 scenario_set_id——
# 后者由 ``report.scenario_set_id`` 单独取（见 ab_runner._components），不入 provenance 守恒。
CONSERVATION_PROVENANCE_FIELDS: tuple[str, ...] = tuple(
    f for f in FINGERPRINT_COMPONENT_FIELDS if f != "scenario_set_id"
)

__all__ = ["CONSERVATION_PROVENANCE_FIELDS", "FINGERPRINT_COMPONENT_FIELDS"]
