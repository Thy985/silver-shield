"""Product Scenario Registry — 银龄盾 MVP 演示场景白名单（SSOT，frozen）。

本模块是 P0-11 多角色协同闭环 Demo 的「产品演示场景」真相源，**不同于**
``silver_demo.scenarios``（运行时场景配置加载器）：

- ``silver_demo.scenarios``         → 怎么运行场景（``ScenarioConfig`` 加载 / 解析）
- ``silver_demo.product_scenarios`` → 产品白名单 + 验收契约（SSOT，仅展示层消费）

冻结白名单（Owner 决策 2026-08-25；不得新增/删除，除非 P0-11 Owner 授权）：

========== ==================================== ==========================================
scenario_id      expected_product_result             用途
========== ==================================== ==========================================
telephone_risk               WARN          多模态风险：通知家属（不升级到社区上报）
cctv_surveillance_suspicious WARN          夜间异常停留：仅 LOG_ONLY，不通知
delivery_courier_normal      MONITOR       白天正常来访：系统克制，验证「看到人 ≠ 报警」
========== ==================================== ==========================================

**不**进入 Registry 的内部场景（fixture / 工程边界用）：

- ``telephone_risk_benign`` → telephone_risk 的 internal acceptance fixture（C3 决策选 A）
- 12 个 internal engineering scenarios → 测试工程边界，非产品演示场景

字段 ``contract_module`` / ``contract_class`` 仅作 Integrity Guard 字符串引用，
Registry 运行时不 import Contract（保持 silver_demo 与 tests/ 物理隔离，ADR-0015）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProductScenario:
    """单个产品演示场景的注册项（frozen SSOT）。

    字段：
      - scenario_id: 服务端场景标识（与 YAML ``scenario_id`` 字段对齐）
      - display_name: 人类可读展示名（CLI / 文档 / Demo README 复用）
      - scenario_yaml: 相对仓库根的 YAML 路径（用于 ``--scenario`` CLI 参数）
      - expected_product_result: 期望的产品结论
          取值 ``RAISED`` / ``WARN`` / ``MONITOR``；与对应
          ``ScenarioAcceptanceContract.expected_product_result`` ClassVar 字段对齐
          （Integrity Guard 测试守护此对齐）。
      - contract_module: Contract 所在模块路径（Integrity Guard 用 importlib 验证）
      - contract_class: Contract 类名（Integrity Guard 用 getattr 验证 + 对齐断言）
      - description: 简短说明（场景身份迁移历史 / 关键设计意图 / 与其他场景的对照关系）
    """

    scenario_id: str
    display_name: str
    scenario_yaml: str
    expected_product_result: str
    contract_module: str
    contract_class: str
    description: str


PRODUCT_SCENARIOS: tuple[ProductScenario, ...] = (
    ProductScenario(
        scenario_id="telephone_risk",
        display_name="电话风险（多模态风险）",
        scenario_yaml="config/demo/scenarios/telephone_risk.yaml",
        expected_product_result="WARN",
        contract_module="tests.visualizer._scenario_contract",
        contract_class="TelephoneRiskContract",
        description=(
            "电话持续交互 + 异常视觉 → 多模态风险 → 通知家属（WARN，不升级到社区上报）。"
            "重命名自 product_story_risk（2026-08-25 场景身份迁移），"
            "更贴近电话风险产品语义；详见 "
            "docs/design/architecture/SCENARIO-RENAME-CONFLICTS-2026-08-25.md §3 D1。"
        ),
    ),
    ProductScenario(
        scenario_id="cctv_surveillance_suspicious",
        display_name="CCTV 夜间异常停留（怀疑）",
        scenario_yaml="config/demo/scenarios/cctv_surveillance_suspicious.yaml",
        expected_product_result="WARN",
        contract_module="tests.visualizer._scenario_contract",
        contract_class="CctvSurveillanceSuspiciousContract",
        description=(
            "夜间反复出现 + 异常停留 → 视觉风险信号 → WARN/LOG_ONLY（无音频轨）。"
            "与 delivery_courier_normal 形成「异常 vs 正常」对照基线。"
        ),
    ),
    ProductScenario(
        scenario_id="delivery_courier_normal",
        display_name="快递员正常来访",
        scenario_yaml="config/demo/scenarios/delivery_courier_normal.yaml",
        expected_product_result="MONITOR",
        contract_module="tests.visualizer._scenario_contract",
        contract_class="DeliveryCourierNormalContract",
        description=(
            "白天单次正常来访 → visit_normal / MONITOR（系统克制不升级）。"
            "与 cctv_surveillance_suspicious 形成「正常 vs 异常」对照基线，"
            "验证「看到人 ≠ 报警」的克制能力。"
        ),
    ),
)


def list_product_scenarios() -> tuple[ProductScenario, ...]:
    """返回全部产品场景 Registry 条目（顺序稳定，便于 CLI 列表输出）。"""
    return PRODUCT_SCENARIOS


def iter_product_scenarios() -> Iterable[ProductScenario]:
    """迭代产品场景 Registry（同 list_product_scenarios，可读性别名）。"""
    return iter(PRODUCT_SCENARIOS)


def get_product_scenario(scenario_id: str) -> ProductScenario | None:
    """按 scenario_id 查 Registry 项；找不到返回 None。

    用于：
      - ``scripts/run_demo.py --list-scenarios`` 过滤
      - Integrity Guard 测试验证 Registry ↔ Contract 对齐
      - 错误消息（"未知产品场景 %s，可选：..."）输出可选项
    """
    for ps in PRODUCT_SCENARIOS:
        if ps.scenario_id == scenario_id:
            return ps
    return None


__all__ = [
    "PRODUCT_SCENARIOS",
    "ProductScenario",
    "get_product_scenario",
    "iter_product_scenarios",
    "list_product_scenarios",
]