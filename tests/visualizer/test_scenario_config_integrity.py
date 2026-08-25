"""Product Scenario Registry Integrity Guard.

防 5 类漂移（Owner 拍板 2026-08-25 PR 边界 #10）：

  1. Registry 字段完整性：scenario_id / display_name / scenario_yaml /
     expected_product_result / contract_module / contract_class / description 均非空；
     expected_product_result ∈ {RAISED, WARN, MONITOR}（冻结枚举）。
  2. Registry.scenario_id ↔ Contract 实例 .scenario_id 对齐。
  3. Registry.expected_product_result ↔ Contract.expected_product_result ClassVar 对齐。
  4. Registry.scenario_yaml 指向真实 YAML，且 YAML 里 scenario_id 与 Registry 一致；
     source_type=video_file 的场景必须声明 media_path（防 YOLO 装配阶段崩）。
  5. YAML start_time 是 ISO 8601 + UTC（与 ScenarioConfig._parse_iso 校验一致）。

冻结原则：本测试**不**修改 Contract / Registry / YAML，只读不写；fail = 立刻修源。
Contract._skip_assertions 引用真实 TestClass 的守护（更深一层）保留为 future guard，
需扫描 tests/visualizer/ 全部模块并验证类名，存在拉 torch 风险，单独 PR 处理。

设计依据：docs/design/architecture/SCENARIO-ARCHITECTURE-GAP-ANALYSIS-2026-08-25.md
"""

from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from silver_demo.product_scenarios import (
    PRODUCT_SCENARIOS,
    ProductScenario,
    get_product_scenario,
    list_product_scenarios,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_PRODUCT_RESULTS = {"RAISED", "WARN", "MONITOR"}
EXPECTED_REGISTRY_COUNT = 3


# ============================================================================
# 1. Registry 字段完整性 + 枚举
# ============================================================================


def test_registry_count_is_frozen() -> None:
    """白名单冻结为 3 项；新增 / 删除需 Owner 授权（ADR / 设计文档）。"""
    assert len(PRODUCT_SCENARIOS) == EXPECTED_REGISTRY_COUNT, (
        f"Product Scenario Registry 项数={len(PRODUCT_SCENARIOS)}，"
        f"期望 {EXPECTED_REGISTRY_COUNT}。新增 / 删除须 Owner 授权。"
    )


def test_registry_no_duplicate_scenario_id() -> None:
    """scenario_id 必须全局唯一。"""
    ids = [ps.scenario_id for ps in PRODUCT_SCENARIOS]
    assert len(ids) == len(set(ids)), f"重复 scenario_id：{ids}"


@pytest.mark.parametrize(
    "field",
    [
        "scenario_id",
        "display_name",
        "scenario_yaml",
        "expected_product_result",
        "contract_module",
        "contract_class",
        "description",
    ],
)
def test_registry_field_non_empty(field: str) -> None:
    """每个 Registry 项的必填字段非空。"""
    for ps in PRODUCT_SCENARIOS:
        value = getattr(ps, field)
        assert value, f"{ps.scenario_id}.{field} 为空"


def test_registry_expected_product_result_enum() -> None:
    """expected_product_result 只能是 RAISED / WARN / MONITOR（冻结枚举）。"""
    for ps in PRODUCT_SCENARIOS:
        assert ps.expected_product_result in ALLOWED_PRODUCT_RESULTS, (
            f"{ps.scenario_id}.expected_product_result="
            f"{ps.expected_product_result!r} 不在 {ALLOWED_PRODUCT_RESULTS} 中"
        )


def test_registry_get_product_scenario_roundtrip() -> None:
    """get_product_scenario(scenario_id) 与 list 顺序一致；未知 ID 返回 None。"""
    for ps in list_product_scenarios():
        assert get_product_scenario(ps.scenario_id) is ps
    assert get_product_scenario("nonexistent_scenario") is None


# ============================================================================
# 2. Registry ↔ Contract 对齐（核心守护）
# ============================================================================


@pytest.mark.parametrize(
    "ps", list_product_scenarios(), ids=lambda p: p.scenario_id
)
def test_registry_contract_class_resolves(ps: ProductScenario) -> None:
    """Registry.contract_module / contract_class 必须指向真实可导入的类。"""
    mod = importlib.import_module(ps.contract_module)
    cls = getattr(mod, ps.contract_class)
    assert isinstance(cls, type), (
        f"{ps.contract_module}.{ps.contract_class} 不是类"
    )


@pytest.mark.parametrize(
    "ps", list_product_scenarios(), ids=lambda p: p.scenario_id
)
def test_registry_scenario_id_matches_contract(ps: ProductScenario) -> None:
    """Registry.scenario_id ↔ Contract 实例 .scenario_id 一致。"""
    mod = importlib.import_module(ps.contract_module)
    cls = getattr(mod, ps.contract_class)
    contract = cls()
    assert contract.scenario_id == ps.scenario_id, (
        f"Registry 与 Contract.scenario_id 不一致："
        f"{ps.scenario_id!r} vs {contract.scenario_id!r}"
    )


@pytest.mark.parametrize(
    "ps", list_product_scenarios(), ids=lambda p: p.scenario_id
)
def test_registry_expected_product_result_matches_contract(
    ps: ProductScenario,
) -> None:
    """Registry.expected_product_result ↔ Contract.expected_product_result ClassVar 一致。"""
    mod = importlib.import_module(ps.contract_module)
    cls = getattr(mod, ps.contract_class)
    contract_expected = getattr(cls, "expected_product_result", None)
    assert contract_expected == ps.expected_product_result, (
        f"Registry 与 Contract.expected_product_result 不一致："
        f"{ps.expected_product_result!r} vs {contract_expected!r}"
    )


# ============================================================================
# 3. Registry ↔ YAML 对齐 + 字段完整性
# ============================================================================


@pytest.mark.parametrize(
    "ps", list_product_scenarios(), ids=lambda p: p.scenario_id
)
def test_registry_scenario_yaml_exists(ps: ProductScenario) -> None:
    """Registry.scenario_yaml 指向真实文件（从仓库根解析）。"""
    yaml_path = REPO_ROOT / ps.scenario_yaml
    assert yaml_path.is_file(), f"{yaml_path} 不存在"


@pytest.mark.parametrize(
    "ps", list_product_scenarios(), ids=lambda p: p.scenario_id
)
def test_registry_yaml_scenario_id_matches(ps: ProductScenario) -> None:
    """Registry.scenario_id ↔ YAML 文件里 scenario_id 一致。"""
    yaml_path = REPO_ROOT / ps.scenario_yaml
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    yaml_sid = data.get("scenario_id", "")
    assert yaml_sid == ps.scenario_id, (
        f"YAML {ps.scenario_yaml} 里 scenario_id={yaml_sid!r} "
        f"≠ Registry {ps.scenario_id!r}"
    )


@pytest.mark.parametrize(
    "ps", list_product_scenarios(), ids=lambda p: p.scenario_id
)
def test_registry_video_scenario_has_media_path(ps: ProductScenario) -> None:
    """source_type=video_file 的场景必须声明 media_path（防 YOLO 装配阶段崩）。"""
    yaml_path = REPO_ROOT / ps.scenario_yaml
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if data.get("source_type") == "video_file":
        assert data.get("media_path"), (
            f"{ps.scenario_yaml} 是 video_file 但缺 media_path（YOLO 装配会崩）"
        )


@pytest.mark.parametrize(
    "ps", list_product_scenarios(), ids=lambda p: p.scenario_id
)
def test_registry_yaml_start_time_iso_utc(ps: ProductScenario) -> None:
    """start_time 必须是 ISO 8601 + UTC（与 ScenarioConfig._parse_iso 校验一致）。"""
    yaml_path = REPO_ROOT / ps.scenario_yaml
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    st = data.get("start_time", "")
    assert isinstance(st, str) and st, f"{ps.scenario_yaml} 缺 start_time"
    assert st.endswith(("+00:00", "Z")), (
        f"{ps.scenario_yaml}.start_time={st!r} 不是 UTC（必须 +00:00 或 Z 结尾）"
    )
    s = st.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    datetime.fromisoformat(s)  # 失败抛 ValueError → 测试 fail