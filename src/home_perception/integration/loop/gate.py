"""ADR-0034 Phase C · 生产门禁层（``evaluate_integration_gate``）。

与验证层的分工（避免"报告说通过、门禁说不通过"的双事实源）：

- ``IntegrationValidator`` 回答「闭环本身是否自洽」（``IntegrationValidationResult.ok``，
  全 AND，**不含** severity 语义）——报告里写死；
- ``evaluate_integration_gate`` 回答「按生产标准，这轮闭环是否允许放行」
  （``IntegrationGateResult.passed``，blocking 语义）——门禁层独立判定。

severity 语义（Phase C，实施计划 §2.5）：

| 取值 | 失败后果 |
|---|---|
| ``blocking``（默认） | 门禁不通过（``passed=False``） |
| ``warning`` | 门禁仍通过，但 ``degraded=True`` 且该 stage 进入降级清单 |

**四条铁律**：

1. **severity 只来自配置对象（suite / 显式 severity_table），绝不读
   ``report.stages[].severity``**（t17，frozen）：``StageResult`` 只是展示投影，运行期可被
   篡改；门禁判定必须免疫，否则"篡改报告即放行"。
2. **observability（F6）恒 blocking，永不可降级**（t18）：F6 是「观测一致性」——降级它
   等于允许"看不见的丢弃"。任何显式表里写 ``observability: warning`` 都会被忽略并记入
   ``notices``。
3. **未声明子期望的 stage**（suite 无对应块，但静默丢弃检测仍可能判失败）：severity
   默认 ``blocking``——没写明"可降级"就按最严处理（fail-closed）。
4. **warning 降级必须可见**：warning 失败不拦门禁，但 ``degraded=True`` + 逐 stage 清单，
   否则降级就变成"静默放行"，与 ADR-0034 命题背道而驰。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型标注用，加载期不拉起验证/运行时链
    from .report import IntegrationReport

__all__ = [
    "EXPECTED_SEVERITIES",
    "NON_DOWNGRADABLE_STAGES",
    "IntegrationGateResult",
    "StageVerdict",
    "evaluate_integration_gate",
    "stage_severity",
]

# severity 合法取值（与 ``validation.contracts.EXPECTED_SEVERITIES`` 语义一致；
# 此处重复声明是为让本模块在加载期零 import contracts，取值差异由 t18 等值断言锁死）。
EXPECTED_SEVERITIES: tuple[str, ...] = ("blocking", "warning")

# F6（observability）永不可降级：任何显式 severity 表中出现该键都被忽略并记 notice。
NON_DOWNGRADABLE_STAGES: tuple[str, ...] = ("observability",)

# stage 名 → suite 子期望字段名（observability 无对应子期望，恒 blocking）。
_STAGE_TO_SUITE_FIELD: dict[str, str] = {
    "perception": "perception",
    "decision": "decision",
    "notification": "action",
    "memory": "memory",
    "cross_modal": "cross_modal",
}


def stage_severity(
    suite: Any | None,
    stage_name: str,
    *,
    severity_table: dict[str, str] | None = None,
) -> str:
    """解析某 stage 的**生效** severity（gate 判定的唯一来源，t17）。

    优先级：observability 恒 ``blocking``（铁律 2）→ 显式 ``severity_table``
    （运维覆盖，但不覆盖 observability）→ suite 对应子期望 → 默认 ``blocking``。

    Args:
        suite: ``IntegrationExpectationSuite`` 或 ``None``（等价空套件：全 stage 默认
            blocking）。类型错误抛 ``TypeError``。
        stage_name: stage 名（``perception``/``decision``/``notification``/``memory``/
            ``cross_modal``/``observability``）。
        severity_table: 可选显式覆盖表（stage → severity）。含 ``observability`` 键时
            该键**被忽略**（返回值仍为 ``blocking``），由调用方负责记入 notices
            （t18：忽略 + warn）。含未知 stage / 非法值抛 ``ValueError``。

    Returns:
        ``"blocking"`` 或 ``"warning"``。
    """
    if stage_name in NON_DOWNGRADABLE_STAGES:
        return "blocking"

    if severity_table is not None:
        if stage_name in severity_table:
            value = severity_table[stage_name]
            if value not in EXPECTED_SEVERITIES:
                raise ValueError(
                    f"severity_table[{stage_name!r}]={value!r} 非法；"
                    f"必须为 {EXPECTED_SEVERITIES}（fail-closed）"
                )
            return value
        return "blocking"

    if suite is None:
        return "blocking"
    from home_perception.validation.contracts import IntegrationExpectationSuite

    if not isinstance(suite, IntegrationExpectationSuite):
        raise TypeError(
            f"suite 必须是 IntegrationExpectationSuite，收到 {type(suite).__name__}"
        )
    field = _STAGE_TO_SUITE_FIELD.get(stage_name)
    sub = getattr(suite, field, None) if field else None
    if sub is None:
        return "blocking"  # 未声明子期望 → fail-closed 默认最严
    return sub.severity


@dataclass(frozen=True, slots=True)
class StageVerdict:
    """单个 stage 的门禁视角判定（severity 为**生效**值，非报告投影）。"""

    name: str
    passed: bool
    severity: str
    failure_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class IntegrationGateResult:
    """门禁判定结论：``passed``（blocking 语义）+ ``degraded``（warning 语义）。

    ``passed`` 与 ``degraded`` **独立**：可以并存（blocking 失败 + warning 失败 →
    ``passed=False, degraded=True``），也可以各自单独出现。二者都从
    ``StageVerdict`` 汇总，不额外信任任何运行时状态。
    """

    scenario_id: str
    passed: bool
    degraded: bool
    verdicts: tuple[StageVerdict, ...] = ()
    notices: tuple[str, ...] = ()

    def blocking_failures(self) -> tuple[StageVerdict, ...]:
        return tuple(v for v in self.verdicts if not v.passed and v.severity == "blocking")

    def warning_failures(self) -> tuple[StageVerdict, ...]:
        return tuple(v for v in self.verdicts if not v.passed and v.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        """完整序列化（本结构无易变字段：name/passed/severity/failure_code/notices 均确定）。"""
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "degraded": self.degraded,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "notices": list(self.notices),
        }

    def canonical_dict(self) -> dict[str, Any]:
        """确定性序列化（与 ``to_dict`` 等价——门禁结论本身就是结构事实）。"""
        return self.to_dict()

    def render_markdown(self) -> str:
        """人类可读结论（排障用；不进任何门禁判定）。"""
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"# Integration Gate — `{self.scenario_id}` [{status}]",
            "",
            f"- **passed**: {self.passed}　**degraded**: {self.degraded}",
        ]
        for v in self.verdicts:
            mark = "✅" if v.passed else f"❌ {v.failure_code}"
            lines.append(f"- `{v.name}` [{v.severity}] {mark}")
        if self.notices:
            lines.append("")
            lines.append("## notices")
            lines.extend(f"- {n}" for n in self.notices)
        return "\n".join(lines)


def evaluate_integration_gate(
    report: IntegrationReport,
    suite: Any | None,
    *,
    severity_table: dict[str, str] | None = None,
) -> IntegrationGateResult:
    """按生产标准判定一轮闭环运行（Phase C 门禁入口）。

    Args:
        report: ``IntegrationReport``（含 stages）。仅消费 ``scenario_id`` 与
            ``stages`` 的 ``name``/``passed``/``failure_code``——**不读** stage 的
            ``severity`` 字段（t17：那是展示投影，判定只看配置来源）。
        suite: ``IntegrationExpectationSuite`` 或 ``None``（等价空套件）。
        severity_table: 可选显式覆盖表（见 ``stage_severity``）。含
            ``observability`` 键 → 忽略 + 记入 ``notices``（t18）。

    Returns:
        ``IntegrationGateResult``。

    判定顺序（与 ADR-0033 gate 同纪律）：任一 blocking 失败 → ``passed=False``；
    任一 warning 失败 → ``degraded=True``。空 stage 集合视为**不通过**（fail-closed：
    没有证据就没有门禁结论）。
    """
    from home_perception.validation.contracts import IntegrationExpectationSuite

    if suite is not None and not isinstance(suite, IntegrationExpectationSuite):
        raise TypeError(
            f"suite 必须是 IntegrationExpectationSuite，收到 {type(suite).__name__}"
        )

    stages = tuple(getattr(report, "stages", ()) or ())
    if not stages:
        return IntegrationGateResult(
            scenario_id=getattr(report, "scenario_id", ""),
            passed=False,
            degraded=False,
            notices=("空 stage 集合：无证据可判定，fail-closed 视为不通过",),
        )

    notices: list[str] = []
    if severity_table is not None:
        for stage in NON_DOWNGRADABLE_STAGES:
            if stage in severity_table:
                notices.append(
                    f"severity_table 含 {stage!r} 被忽略："
                    "F6（observability）永不可降级（t18）"
                )
        unknown = sorted(set(severity_table) - set(_STAGE_TO_SUITE_FIELD) - set(NON_DOWNGRADABLE_STAGES))
        if unknown:
            notices.append(f"severity_table 含未知 stage {unknown}，被忽略")

    verdicts: list[StageVerdict] = []
    for s in stages:
        name = getattr(s, "name", "")
        verdicts.append(
            StageVerdict(
                name=name,
                passed=bool(getattr(s, "passed", False)),
                severity=stage_severity(suite, name, severity_table=severity_table),
                failure_code=getattr(s, "failure_code", None),
            )
        )

    blocking_failed = any(not v.passed and v.severity == "blocking" for v in verdicts)
    warning_failed = any(not v.passed and v.severity == "warning" for v in verdicts)
    return IntegrationGateResult(
        scenario_id=getattr(report, "scenario_id", ""),
        passed=not blocking_failed,
        degraded=warning_failed,
        verdicts=tuple(verdicts),
        notices=tuple(notices),
    )
