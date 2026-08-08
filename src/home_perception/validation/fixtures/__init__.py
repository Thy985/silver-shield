"""ADR-0032 D8 Scenario Registry 资产目录（声明式 scenario YAML）。

目录分层（为 ADR-0033 Benchmark Harness 前置）：
- ``scenarios/perception`` ：单点链路验证
- ``scenarios/regression``：回归守护
- ``scenarios/benchmark`` ：横向打分集

场景 YAML 不入公共 PR 约束（T2/S1）：普通场景集按 ``.gitignore`` 排除或置于私有目录；
本目录仅承载**非 gold** 的声明式几何场景（不含真实人脸 / 户型 / PII）。
"""
