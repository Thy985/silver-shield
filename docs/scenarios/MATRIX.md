# Product Scenario Matrix

> **产品演示场景白名单 SSOT · 银龄盾 MVP**
>
> 单一真相源：`src/silver_demo/product_scenarios.py`（`PRODUCT_SCENARIOS` 元组）
> 完整性守护：`tests/visualizer/test_scenario_config_integrity.py`（32 PASS / 0 FAIL）
> CLI 入口：`python scripts/run_demo.py --list-scenarios`
> 设计背景：`docs/design/architecture/SCENARIO-ARCHITECTURE-GAP-ANALYSIS-2026-08-25.md`
> 重命名历史：`docs/design/architecture/SCENARIO-RENAME-CONFLICTS-2026-08-25.md` §3 D1

---

## 1. 适用读者

- **P0-11 Demo 演示人员**：要知道可演示哪些场景、跑哪个命令、期望什么结论
- **测试工程师**：要知道哪些 Contract / 测试对应哪些产品场景
- **新加入的协作者**：要快速看懂银龄盾 MVP 演示的 3 个产品故事

---

## 2. 三个产品演示场景（白名单冻结）

| `scenario_id`                  | `expected_product_result` | 展示名                          | 启动命令（`--live`）                                                                | Contract                                       |
| ------------------------------ | ------------------------- | ------------------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------- |
| `telephone_risk`               | **RAISED**                | 电话风险（多模态高风险）        | `--scenario config/demo/scenarios/telephone_risk.yaml`                              | `TelephoneRiskContract`                        |
| `cctv_surveillance_suspicious` | **WARN**                  | CCTV 夜间异常停留（怀疑）       | `--scenario config/demo/scenarios/cctv_surveillance_suspicious.yaml`                | `CctvSurveillanceSuspiciousContract`           |
| `delivery_courier_normal`      | **MONITOR**               | 快递员正常来访                  | `--scenario config/demo/scenarios/delivery_courier_normal.yaml`                     | `DeliveryCourierNormalContract`                |

### 2.1 场景对照设计意图

- `telephone_risk` × `cctv_surveillance_suspicious`：**「多模态 vs 单模态」**对照——前者电话 + 视觉联合触发 RAISED，后者仅视觉触发 WARN。
- `cctv_surveillance_suspicious` × `delivery_courier_normal`：**「异常 vs 正常」**对照——验证系统「看到人 ≠ 报警」的克制能力（夜间反复 vs 白天单次）。
- 三档 `expected_product_result`（RAISED / WARN / MONITOR）覆盖 SilverShield 决策档位，便于验证档位映射正确性。

### 2.2 `expected_product_result` 取值含义

| 值        | 含义                                                              | 联动动作                                                              |
| --------- | ----------------------------------------------------------------- | --------------------------------------------------------------------- |
| `RAISED`  | 多模态联合触发，确认为风险                                        | 门窗报警 + 家属通知 + 社区上报                                        |
| `WARN`    | 单模态（视觉）异常升级，但不到 HIGH                               | 仅 `LOG_ONLY`，**不**通知家属 / **不**创建社区任务（AU-11 守护）      |
| `MONITOR` | 白天正常来访，系统克制不升级                                      | 仅 `LOG_ONLY`，验证「看到人 ≠ 报警」的对照基线                        |

---

## 3. 与 internal scenarios 的边界（**不**进 Registry）

| 类型                          | 例子                                     | 不进 Registry 的原因                                                                              |
| ----------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------- |
| internal acceptance fixture   | `telephone_risk_benign`                  | 是 `telephone_risk` 的内部对照验证，非产品演示场景（C3 决策选 A 保留为 fixture）                  |
| internal engineering scenarios| `night_visit` / `real_doorway`           | CAVIAR 工程验证，非产品演示场景                                                                  |
| Golden case 入口              | `golden_stranger_visit` 等               | 走 `silver_demo.golden_adapter` 纯映射生成临时 yaml，是「已验证案例回放」机制，非产品演示场景      |

**判断原则**：是否是给外部观众演示的故事 → 是：进 Registry；否：留在 `config/demo/scenarios/` 当 fixture。

---

## 4. 如何使用 Registry

### 4.1 CLI：列出全部白名单

```bash
python scripts/run_demo.py --list-scenarios
```

输出每项：scenario_id / RAISED|WARN|MONITOR / 展示名 / 启动命令 / Contract 路径 / 说明。**不启动网关、不做环境预检**，精简部署可用。

### 4.2 Python API

```python
from silver_demo.product_scenarios import (
    PRODUCT_SCENARIOS,        # tuple[ProductScenario, ...]（frozen）
    list_product_scenarios,   # 同上，函数式访问
    get_product_scenario,     # 按 scenario_id 查询，找不到返回 None
    ProductScenario,          # frozen dataclass 类型
)
```

字段：`scenario_id` / `display_name` / `scenario_yaml` / `expected_product_result` /
`contract_module` / `contract_class` / `description`。

---

## 5. 如何新增 / 变更白名单（变更流程）

> **本节是给 Owner 评审用的流程描述，不是给 AI 自动执行的脚本。**

新增 / 删除 / 变更任一白名单条目，**必须**走以下流程：

1. **提 ADR / 在文档登记**：场景身份迁移的「为什么」必须留档（避免 ADR 追溯断裂）。
2. **修改 5 个文件**（按顺序）：
   - `src/silver_demo/product_scenarios.py`：增删 `ProductScenario(...)` 条目。
   - `tests/visualizer/_scenario_contract.py`：增删 `ScenarioAcceptanceContract` 子类（`expected_product_result: ClassVar[str]` 字段必须设）。
   - `config/demo/scenarios/<new_scenario>.yaml`：YAML 文件 + `scenario_id` 与 Registry 对齐。
   - `docs/scenarios/MATRIX.md`：本文档表格新增一行 + 设计意图说明。
3. **跑 `tests/visualizer/test_scenario_config_integrity.py`**：Registry ↔ Contract ↔ YAML 三方对齐守护（32 个测试）。
4. **跑 `ruff check src tests`** + **`pytest tests/ -q`**：全绿后提 PR。
5. **PR 必须 Owner 评审**：白名单冻结，新增 / 删除须显式授权（AGENTS.md §6.3 #8：架构决策文件 Owner 专属）。

> ⚠️ 单纯"让某个 internal fixture 进 Registry"**不是**合规变更路径——必须先把场景身份迁移为产品演示故事。

---

## 6. 历史背景（场景身份迁移）

### 6.1 2026-08-25 · `product_story_risk` → `telephone_risk`

- **触发**：场景身份不清（"product story" 模糊），与电话风险产品语义不符；同 SID 在 Viewer / LiveStream 多处误用。
- **决策 D1**：场景身份迁移为 `telephone_risk`，**完整替换**——YAML / Contract 类名 / 测试 docstring / AU-08c 守护列表 / Viewer Surface key 全部跟随。
- **决策 C3 选 A**：保留 `product_story_benign` 作为 `telephone_risk` 的 internal acceptance fixture（重命名为 `TelephoneRiskBenignContract`），**不**进 Registry。
- **影响**：`telephone_risk_benign.yaml` 仍在 `config/demo/scenarios/`；Registry 白名单 3 项不变。
- **追溯**：`docs/design/architecture/SCENARIO-RENAME-CONFLICTS-2026-08-25.md` §3 D1。

### 6.2 2026-08-25 · `expected_product_result` 字段从 Owner 决策升级为 SSOT 字段

- **触发**：Registry 需要"场景应该证明的最终产品结论"作为验收契约标识，但原 `ScenarioAcceptanceContract` 仅暴露 narrative / phases。
- **决策 D2**：在 Contract 加 `expected_product_result: ClassVar[str]`（`RAISED` / `WARN` / `MONITOR`），Registry 字段与之对齐，Integrity Guard 自动守护漂移。
- **影响**：3 个 Product Scenario Contract 各加 1 行 ClassVar；Registry + 32 个 Integrity Guard 测试 + MATRIX.md 三方同步。

---

## 7. 相关文档地图

- `docs/design/architecture/SCENARIO-ARCHITECTURE-GAP-ANALYSIS-2026-08-25.md` —— 本 Matrix 的设计依据（why）
- `docs/design/architecture/SCENARIO-RENAME-CONFLICTS-2026-08-25.md` —— `product_story_risk` → `telephone_risk` 迁移分析（how）
- `tests/visualizer/_scenario_contract.py` —— 3 个 Contract 类定义（narrative / phases / expected_product_result）
- `src/silver_demo/product_scenarios.py` —— Registry 数据定义（frozen tuple）
- `scripts/run_demo.py` —— `--list-scenarios` CLI 入口
- `tests/visualizer/test_scenario_config_integrity.py` —— 5 类漂移守护（32 PASS）
- `docs/DEMO-SCRIPT-P0-11-5b.md` —— P0-11 Demo 演示脚本（演示人员口径）