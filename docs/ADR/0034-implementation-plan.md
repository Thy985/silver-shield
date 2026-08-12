# ADR-0034 Implementation Plan（闭环集成验证 · 实现计划）

- **Status**: Working Document（**非冻结件**）
- **Date**: 2026-08-09
- **Parent ADR**: [`0034-scenario-integration-validation.md`](0034-scenario-integration-validation.md)

> **文档定位**：ADR 正文只承载 **Why / Decision / Boundary / Contract / Failure Model / Acceptance Criteria**（冻结后 Owner 专属）。本文件承载**实现级细节**——类型草案、注入伪码、文件落点、分阶段 MUST 清单、测试编号明细。本文件**可随实现演进随时更新，无需 Owner 重审**；但**不得与 ADR 决策冲突**，冲突时以 ADR 为准。

---

## 1. 文件落点

```
src/home_perception/integration/
├── __init__.py          # Lazy __getattr__ 转发（mirror evaluation/__init__.py），加载期零急切 import
├── context.py           # IntegrationContext + IntegrationRunnerConfig + build()
├── runner.py            # IntegrationRunner（L1–L5 生命周期）+ IntegrationRunResult
├── validator.py         # IntegrationValidator + StageResult + failure_code 归类
├── report.py            # IntegrationReport + canonical_dict + write_report（脱敏守卫）
├── fingerprint.py       # SCENARIO_INTEGRATION_VERSION + expectation_fingerprint + loop_fingerprint
├── bridge.py            # 纯 Adapter：AudioScenario → list[AudioPerceptionEvent]（Phase B）
└── gate.py              # Expectation Severity + evaluate_integration_gate（Phase C）

src/home_perception/action/sink.py        # ActionSink(Protocol) + InMemoryActionRecorder + JsonlActionRecorder
src/home_perception/validation/contracts.py  # += IntegrationExpectationSuite 及各子结构
scripts/run_integration_validation.py        # CLI（mirror run_benchmark.py，不接线 demo/gateway）
tests/integration/test_adr0034_*.py          # 契约测试（测试名统一 adr0034 前缀）
```

**为什么 `ActionSink` 放 `action/` 而不是 `integration/`**：它是**生产模块的可选接缝**（`ActionExecutor` 要 import 它做类型标注），放 `integration/` 会让生产代码反向依赖评估包、直接违反 T2。这与 ADR-0031 把 `DecisionTraceRecorder` 放 `analysis/` 而非 `evaluation/` 同构。

---

## 2. 类型草案与注入伪码

> 字段语义以 ADR §D4 契约表为准；此处仅给实现形态。

### 2.1 `ActionSink` 三件套（D3）

```python
# action/sink.py
class ActionSink(Protocol):
    def record(self, command: ActionCommand) -> None: ...
    def flush(self) -> None: ...

class InMemoryActionRecorder:
    def commands(self) -> tuple[ActionCommand, ...]: ...
    def by_type(self, command_type: str) -> tuple[ActionCommand, ...]: ...
    def by_warning_id(self, warning_id: str) -> tuple[ActionCommand, ...]: ...
```

注入与派发（`action/executor.py`）：

```python
def __init__(self, ..., sink: ActionSink | None = None) -> None:
    self.sink = sink            # 默认 None ⇒ 零行为变化

def execute(self, warning: WarningEvent) -> ...:
    cmd = ...                   # 既有逻辑，完全不动
    ...                         # 既有派发
    if self.sink is not None:   # 仅在成功派发后记录
        try:
            self.sink.record(cmd)
        except Exception:       # 失败隔离：探针异常绝不影响生产派发
            log.warning("action_sink_record_failed", exc_info=True)
    return ...
```

**失败隔离铁律**（同 ADR-0031 `DecisionTraceRecorder`）：sink 抛异常必须被吞掉并记日志，**绝不能**让观测探针改变生产行为。对应测试：注入一个 `record()` 必抛的 sink，断言 `execute()` 仍正常返回、既有断言全过。

### 2.2 `IntegrationContext` + `IntegrationRunner`（D2）

```python
@dataclass(frozen=True, slots=True)
class IntegrationRunnerConfig:
    memory_backend: Literal["in_memory"] = "in_memory"
    sink_kind: Literal["in_memory", "jsonl"] = "in_memory"
    cross_modal_enabled: bool = False        # Phase A 恒 False
    trace_recorder_kind: Literal["in_memory", "jsonl"] = "in_memory"

@dataclass(frozen=True, slots=True)
class IntegrationContext:
    memory_store: ...            # InMemoryStore
    trace_recorder: ...          # DecisionTraceRecorder
    action_sink: ...             # ActionSink
    cross_modal_runtime: ... | None = None
    cross_modal_retrieval: ... | None = None
    clock: ... | None = None

    @classmethod
    def build(cls, config: IntegrationRunnerConfig) -> "IntegrationContext": ...   # 唯一创建点
```

`IntegrationRunner.run` 五步骨架：

```python
def run(self, scenario, context: IntegrationContext | None = None) -> IntegrationRunResult:
    ctx = context or IntegrationContext.build(self.config)      # L1
    pipeline = self._assemble(ctx)                              # L2 唯一注入点
    frames = ScenarioCompiler(...).compile(scenario)            # L3 复用 ADR-0032
    for fr in self._drive(pipeline, frames):
        ...                                                     # 逐帧聚合，含 fr.commands
    return self._collect(ctx, ...)                              # L4 + L5 只读回，不判定
```

**禁止形态**（契约测试守护）：`run()` 不得出现 `pipeline` / `recorder` / `store` 形参。用 `inspect.signature` 断言参数名集合 `== {"scenario", "context"}`。

### 2.3 `IntegrationExpectationSuite`（D4）

```python
# validation/contracts.py（中立子包，避免 validation 反向 import evaluation）
class PerceptionExpectation(BaseModel):
    min_perception_events: int | None = None

class MemoryExpectation(BaseModel):
    min_records: int = 1
    min_risk_episodes: int | None = None
    min_actionable_episodes: int | None = None
    required_modalities: list[str] | None = None
    severity: Literal["blocking", "warning"] = "blocking"   # Phase C 生效

class CrossModalExpectation(BaseModel):
    min_links: int | None = None
    required_link_kinds: list[str] | None = None
    severity: Literal["blocking", "warning"] = "blocking"

class DecisionExpectation(BaseModel):
    outcome: Literal["WARN", "SUPPRESS"] | None = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    recommended_action: Literal["MONITOR", "NOTIFY_FAMILY", "ESCALATE_COMMUNITY"] | None = None
    reason_code: str | None = None
    confidence: float | None = None
    severity: Literal["blocking", "warning"] = "blocking"

class ActionExpectation(BaseModel):
    expected_command_types: list[str] | None = None   # elements from COMMAND_TYPES
    expected_notification: bool | None = None
    severity: Literal["blocking", "warning"] = "blocking"

class IntegrationExpectationSuite(BaseModel):         # 顶层容器，避免 God Object
    perception: PerceptionExpectation | None = None
    memory: MemoryExpectation | None = None
    decision: DecisionExpectation | None = None
    action: ActionExpectation | None = None
    cross_modal: CrossModalExpectation | None = None
```

`Scenario` 增可选字段 `integration: IntegrationExpectationSuite | None = None`（opt-in，`ScenarioValidator` 不消费；同 ADR-0033 `benchmark` 字段的加法方式）。

### 2.4 `StageResult` 与失败归类（§0.4）

```python
@dataclass(frozen=True, slots=True)
class StageResult:
    name: Literal["perception", "memory", "cross_modal", "decision", "notification", "observability"]
    passed: bool
    failure_code: Literal["F1","F2","F3","F4","F5","F6"] | None
    severity: Literal["blocking", "warning"] = "blocking"   # Phase C 前按 blocking 处理；Phase C 由对应子期望 severity 字段填充
    detail: str = ""
```

归类实现要点：

- 归类函数 `classify_failure(stage, run_result) -> str` 必须**穷尽**分支，`else: return "F6"`（fail-closed）。
- F6 交叉校验独立成 stage（`name="observability"`），在所有业务 stage 之后运行，**severity 恒 `blocking`，配置/期望无法覆盖**（在 `gate.py` 里对 severity 表做键过滤，`observability` 键被忽略并 warn）。

### 2.5 两枚指纹（D7）

```python
SCENARIO_INTEGRATION_VERSION = "0.1.0"    # Phase A=0.1.0 / B=0.2.0 / C=1.0.0

def compute_expectation_fingerprint(suite) -> str:
    # canonical JSON(suite)（含每个子期望 severity 字段）+ SCENARIO_INTEGRATION_VERSION
    ...

def compute_loop_fingerprint(harness_fp, *, policy_fp, sink_type,
                             memory_backend, cross_modal_enabled,
                             expectation_fp) -> str:
    # 6 成分全非空校验 → 缺任一 raise（fail-closed）
    ...
```

**不得** import 或修改 `evaluation.fingerprint.FINGERPRINT_COMPONENT_FIELDS`（可读取 `harness_fingerprint` 的**结果字符串**，但不参与其字段守恒）。

---

## 3. 复用清单（禁止重写）

| 来源 | 符号 | 用途 |
|---|---|---|
| ADR-0032 | `ScenarioCompiler` / `ScenarioRunner` / `generator.fingerprint` | 输入合成、帧驱动 |
| ADR-0033 | `build_scenario_score` / `ScenarioScore` / `compute_harness_fingerprint` | 感知 Stage 判据 + 指纹基座 |
| ADR-0031 | `DecisionTraceRecorder` / `InMemoryRecorder` / `JsonlTraceRecorder` / `compute_policy_fingerprint` | Decision 探针 |
| ADR-0031 | `analysis.decision_sink.assert_desensitized` | 报告落盘脱敏守卫（**非 evaluation**，不触 T2） |
| Memory | `InMemoryStore.all_episodic()` | Memory Stage 读回 |
| ADR-0029 | `CrossModalRetrieval.get_links_for_episode` | CrossModal Stage 读回 |
| Runtime | `PerceptionPipeline._act_on_event` 行为事实 | 无需再接线，只需观测 |

**DRY 债（非阻塞项）**：`DemoClock` / `_SimpleClock`(`scripts/run_benchmark.py`) / `ManualClock`(`tests/runtime/_helpers.py`) / `_Clock` 四处重复时钟实现。可在 Phase A 顺带收敛为单一实现，但**不作为验收阻塞**。

---

## 4. 分阶段 MUST / MUST NOT 与测试编号

### Phase A · 最小闭环（单模态，零行为变化）

**MUST**
- `integration/` 包骨架 + Lazy `__init__`（D1）
- `action/sink.py` 三件套 + `ActionExecutor(sink=None)`（D3）
- `IntegrationContext` + `IntegrationRunner`（L1–L5）+ `IntegrationRunResult`（D2）
- `IntegrationExpectationSuite` 单模态子集（`memory` 基础 + `decision` + `action`）（D4）
- `IntegrationValidator`（全 AND）+ `failure_code` 归类 + F6 交叉校验（D5 / §0.4）
- `IntegrationReport` + 脱敏守卫（D7 脱敏部分）
- `scripts/run_integration_validation.py`
- 扩 T2 allowlist

**MUST NOT**：❌ 跨模态 ❌ 两枚指纹 ❌ Stage Severity ❌ Gate ❌ CI 门禁 ❌ 闭环级 A/B

| 测试 | 断言 |
|---|---|
| `adr0034_t1_determinism` | 同 seed 两次 `run()` → `IntegrationReport.canonical_dict()` 逐字节一致 |
| `adr0034_t2_integration_not_wired_into_production` | `integration/` 仅被 `scripts/run_integration_validation.py` + `tests/` 引用；allowlist 已扩展 |
| `adr0034_t3_reuse_not_rewrite` | `IntegrationRunner` AST 不出现 generator/renderer 调用；感知判据来自 `build_scenario_score` |
| `adr0034_t4_action_sink_zero_behavior_change` | `sink=None` 默认路径既有 action 测试全过；sink 抛异常时 `execute()` 仍正常返回 |
| `adr0034_t5_desensitized` | `write_report()` 落盘前过 `assert_desensitized`；含 PII 时 fail-closed |
| `adr0034_t6_minimal_loop` | 报警场景：全链到达 Notification + `outcome=WARN` + Memory 落库；良性场景：`SUPPRESS` + 无 `ActionCommand`（验证"不误发"） |
| `adr0034_t11_runner_signature_frozen` | `inspect.signature(IntegrationRunner.run)` 参数名集合 `== {"scenario","context"}`（防参数搬运器退化） |
| `adr0034_t12_failure_taxonomy` | 人为断链（禁用 executor / 清空 sink / 屏蔽 memory hook）→ 对应 F1–F5 归类正确；不可归类的失败落 F6 |
| `adr0034_t13_observability_cross_check` | `ActionSink` 与 `FrameResult.commands` 不一致 → F6 且整体不通过 |

### Phase B · Memory 深度 + 多模态 + 指纹

**MUST**
- `MemoryExpectation` 全字段 + `CrossModalExpectation`（D4 全）
- `integration/bridge.py` 纯 Adapter（D6）+ `IntegrationRunner` 侧音频驱动
- 显式注入 `memory_hook=MemoryHook(..., cross_modal_runtime=...)`（G3）
- `fingerprint.py` 两枚指纹 + `SCENARIO_INTEGRATION_VERSION`（D7）

**MUST NOT**：❌ Stage Severity ❌ Gate ❌ CI 门禁

| 测试 | 断言 |
|---|---|
| `adr0034_t7_memory_depth` | episodes 满足 `min_records`/`min_risk_episodes`/`min_actionable_episodes`/`required_modalities`；**无任何精确 `==` 计数断言**（AST 或 review 双保险） |
| `adr0034_t8_cross_modal_link` | 用 `CrossModalLink`/`CrossModalRetrieval`（非 `CrossModalEvidence`）；未注入 `cross_modal_runtime` 时报 F5 而非静默通过 |
| `adr0034_t9_decision_trace` | `trace_recorder` 采到 WARN/SUPPRESS；`risk_level` 可区分 `WARN_LOW`/`WARN_HIGH`；`reason_code` 匹配 `SuppressReason.value` |
| `adr0034_t14_bridge_is_pure_adapter` | `bridge.py` AST：不 import `runtime`；不出现 `PerceptionPipeline`/`MemoryHook`/`CrossModalLink*` 符号；`build_audio_events` 为纯函数 |
| `adr0034_t15_expectation_fingerprint` | 仅改期望（如 `min_records` 1→2）→ `expectation_fingerprint` 变、`loop_fingerprint` 随之变；场景/策略未变时其余成分不变 |
| `adr0034_t16_fingerprint_fail_closed` | 任一成分缺失 → raise；`FINGERPRINT_COMPONENT_FIELDS` 未被修改（读取 ADR-0033 常量做等值断言） |

### Phase C · 生产门控

> **✅ 已实现（v1.0，2026-08-12）**：`gate.py`（`evaluate_integration_gate` → `IntegrationGateResult`）、severity 随 suite 进 `expectation_fingerprint`、CI `integration-gate` job（PR 级）、loop 基线 + `integration-baseline-bump` 治理（原"可选"项已落地）全部完成；DoD C1–C8 验收通过。执行入口：`scripts/run_integration_validation.py --gate --strict`（产出 gate.json / fingerprints / canonical 报告）+ `scripts/check_integration_baseline.py`（漂移 + 基线文件变更双裁决，`--skip-file-policy` 供 main push 场景）。

**MUST**
- `gate.py`：`ExpectationSeverity` + per-category suite 的 `severity` 字段 + `evaluate_integration_gate(report, suite) -> IntegrationGateResult`
- severity 随 suite 纳入 `expectation_fingerprint`
- CI 新增 `integration-gate` job（PR 级）
- 可选：loop-level 基线 + bump 治理（类比 `check_baseline_bump.py`）

| 测试 | 断言 |
|---|---|
| `adr0034_t10_stage_severity` | memory=warning 失败 → `passed=True` + `degraded=True` + 报告标注；decision=blocking 失败 → `passed=False` |
| `adr0034_t17_severity_not_runtime_mutable` | severity 只能来自配置对象；运行时改 `StageResult.severity` 不影响 gate 判定（frozen） |
| `adr0034_t18_f6_never_downgradable` | severity 表里写 `observability: warning` → 被忽略并 warn，F6 仍使整体不通过 |
| `adr0034_t19_severity_change_moves_fingerprint` | 把 memory 由 blocking 降为 warning → `expectation_fingerprint` 必变（降级可追溯） |

---

## 5. 实施纪律（沿用 ADR-0033 血的教训）

1. **每阶段独立 PR**，base=main，Conventional commit + `Task scope:` footer，Owner review，禁自 merge。
2. **提交前全量** `ruff check src tests`（与 CI `lint` job 完全一致），**不能只查改动文件**——ADR-0033 Phase 3 曾因此漏检 `PLW1510`/`RUF100` 导致整轮 CI 红。
3. 所有 `subprocess.run` 一律显式 `check=`。
4. 循环导入：`integration/__init__.py` 加载期**零急切 import**，用 PEP 562 `__getattr__` 延迟转发。
5. 新增评价契约模型放 `validation/contracts.py`，**不得**让 `validation` 反向依赖 `evaluation`/`integration`。
6. 阈值/判定类断言做**变异验证**（改一个字段必须让测试变红），否则等于没测。
