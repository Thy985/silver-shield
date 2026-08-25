# CCTV 场景通用验收框架 — 实施报告

> **日期**：2026-08-25
> **任务**：将 `test_product_story_browser_acceptance.py` / `test_visual_product_acceptance.py` 从 `product_story_risk` 专属测试抽象为通用 Scenario Acceptance Runner，使 CCTV 可作为第二个实例接入。

---

## 一、改动范围

| 文件 | 类型 | 说明 |
|---|---|---|
| `tests/__init__.py` | 新增 | 使 `tests` 成为可导入 Python 包（原有测试目录无此文件） |
| `tests/visualizer/_scenario_contract.py` | 新增 | 共享契约模块：`ScenarioAcceptanceContract` 基类 + 3 个具体实现 + JS 模板工厂 |
| `tests/visualizer/test_product_story_browser_acceptance.py` | 重构 | 替换硬编码 `SID` 为 `_CONTRACT.scenario_id`；JS 模板改为工厂函数调用；skip reason 来自契约 |
| `tests/visualizer/test_visual_product_acceptance.py` | 重构 | 同上；布局/DOM JS 也改为工厂函数 |
| `tests/visualizer/test_cctv_surveillance_acceptance.py` | 新增 | CCTV 单 phase 验收测试：P1（无音频版）/ P2（视觉事件链）/ P4（DOM Oracle）/ P5（Runtime Provenance）/ P8（Browser Cleanliness） |

---

## 二、契约设计

### 2.1 `ScenarioAcceptanceContract` 基类字段

```python
@dataclass
class ScenarioAcceptanceContract:
    scenario_id: str              # 服务端 scenario_id
    narrative: str                # 冻结叙事描述
    phases: list[PhaseSpec]       # [(name, observe_ms, need_source_switch)]
    observe_times: dict[str, int] # 视觉验收专用观察时间覆盖
    has_audio_surface: bool       # 是否有音频表面（控制 audio 断言跳过）
    _skip_assertions: list[str]   # 不适用的 Test* 类前缀
```

### 2.2 三个具体契约

| 契约类 | scenario_id | phases | has_audio |
|---|---|---|---|
| `ProductStoryRiskContract` | `product_story_risk` | risk → benign → switch_back → reset | True |
| `ProductStoryBenignContract` | `product_story_benign` | (standalone) | True |
| `CctvSurveillanceSuspiciousContract` | `cctv_surveillance_suspicious` | cctv_observe (120s) | False |

---

## 三、CCTV 验收测试断言集

CCTV 场景（单模态、无音频）的 P 组断言：

| 组 | 断言 | 与产品故事差异 |
|---|---|---|
| P1a-c | WS 建立 / frame_tick 单调 / video frame 变化 | 同 |
| P1d | **vision evidence 到达** | 替代 P1d（audio evidence） |
| P1e | **确认无 audio surface**（非 bug，是预期） | 新增 |
| P2a | Frame→Vision→Risk→Decision 时序 | 无 Audio 环节 |
| P2b | Warning 或 MONITOR 至少其一产出 | 放宽为 WARN or LOG_ONLY |
| P2c | 风险上限 ≤ WARN（非 HIGH） | 新增（冻结叙事核心命题） |
| P4a-e | 视频区 / 时间线 / 风险解释 / 信号区 / 行动闭环 | 去掉 audio_rows |
| P5a-b | DOM level 来自 runtime / 无"诈骗"判定词 | 同 |
| P8a-c | console / page error / WS 稳定 | 同 |

---

## 四、质量门禁

```
ruff check tests/visualizer/  →  All checks passed
pytest --collect-only          →  82 tests collected (all skipped without server)
pytest --ignore acceptance     →  3 pre-existing failures（与本次改动无关）
```

预存失败清单（本次未引入，均属于 `render.py` / 决策策略层已有缺陷）：
- `test_p011x_audio_verifiability.py::test_r9_no_audio_kinds_dash` — audio sensor 渲染缺失 `―` 字符
- `test_phase3_backend.py::test_waveform_surface_rendered_for_telephone_risk` — canvas HTML 空串
- `test_phase3_backend.py::test_waveform_surface_scenario_id_in_canvas` — 同上

---

## 五、一键验证命令

```bash
# 启动服务端（CCTV 场景）
python scripts/run_demo.py --live --scenario config/demo/scenarios/cctv_surveillance_suspicious.yaml

# 运行验收
python -m pytest tests/visualizer/test_cctv_surveillance_acceptance.py -v
```

---

## 六、后续扩展

新增场景只需：
1. 在 `config/demo/scenarios/` 下添加 YAML 配置文件
2. 在 `_scenario_contract.py` 中新增一个 `ScenarioAcceptanceContract` 子类
3. （可选）新建 `test_<scenario_id>_acceptance.py`，复用 `_scenario_contract.py` 的基础设施

现有 `product_story_*` 测试零行为变化，所有 37+27=64 个断言保持原样。