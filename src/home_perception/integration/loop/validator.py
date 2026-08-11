"""ADR-0034 Phase A · D5：``IntegrationValidator`` —— 闭环判定与失败归类（F1–F6）。

与 ADR-0032 ``ScenarioValidator`` 的关系：**包含**而非替代。感知层判据整段复用
ADR-0033 ``build_scenario_score``（其 ``validation_ok`` 即 ``ScenarioValidator`` 的结论），
本模块只在其上叠加下游 stage —— 复用不重写（t3）。

判定纪律（全部来自 ADR-0034 §0.4）：

1. **全 AND**：Phase A/B 任一 blocking stage 失败即整体不通过。Stage Severity 属 Phase C，
   此处所有 stage 的 ``severity`` 恒为 ``"blocking"``。
2. **静默丢弃判定式**：``上游存在 ∧ 下游缺失 ∧ 无显式理由`` → 丢弃。三个条件缺一不可——
   尤其"上游存在"这一前提：规则未命中导致的"无告警"是**合法未触发**，把它算作
   Decision Drop 会让每个良性场景都变红，门禁随即失去意义。
3. **归类穷尽**：``classify_failure`` 的分支必须覆盖全部已知形态，兜底 ``return "F6"``。
   无法归类 = 我们不理解系统当下的行为，这本身就是可观测性缺陷，必须不通过。
4. **F6 永不可降级**：可观测性 stage 的 severity 不接受任何配置覆盖（Phase C 亦然）。

F6 三通道交叉校验（互为独立观测源，任一对不齐即整体不可信）：

| 通道 | 探针侧 | 生产侧 | 比对键 |
|---|---|---|---|
| A | ``ActionSink`` | ``FrameResult.commands`` | ``command_id`` 集合 |
| B | ``DecisionTraceRecorder``（WARN） | ``FrameResult.warnings`` | ``warning_id`` 集合 |
| C | ``EpisodicRecord.actions`` | ``FrameResult.commands`` | ``command_id`` 子集 |

集合而非列表比对：幂等命中时 ``execute()`` 会重复返回历史命令但 sink 不重复记录
（见 ``action/executor.py`` 步骤 7 注释），列表逐项比对会因此产生**假阳性 F6**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from home_perception.analysis.decision_trace import TraceOutcomeKind
from home_perception.validation.contracts import (
    OUTCOME_NONE,
    IntegrationExpectationSuite,
)

if TYPE_CHECKING:
    from home_perception.evaluation.metrics import ScenarioScore
    from home_perception.validation.scenario.scenario import Scenario

    from .runner import IntegrationRunResult

__all__ = [
    "FAILURE_CODES",
    "STAGE_NAMES",
    "IntegrationValidationResult",
    "IntegrationValidator",
    "StageResult",
    "classify_failure",
]

StageName = Literal["perception", "memory", "cross_modal", "decision", "notification", "observability"]
FailureCode = Literal["F1", "F2", "F3", "F4", "F5", "F6"]

STAGE_NAMES: tuple[str, ...] = (
    "perception",
    "memory",
    "cross_modal",
    "decision",
    "notification",
    "observability",
)
FAILURE_CODES: tuple[str, ...] = ("F1", "F2", "F3", "F4", "F5", "F6")

# stage → 其"本职"失败码（§0.4 Failure Taxonomy）
_STAGE_FAILURE: dict[str, str] = {
    "perception": "F1",
    "decision": "F2",
    "notification": "F3",
    "memory": "F4",
    "cross_modal": "F5",
    "observability": "F6",
}


def classify_failure(stage: str) -> str:
    """stage → failure_code（**穷尽**分支；未知 stage 落 F6，fail-closed）。

    兜底到 F6 而不是抛异常：一个我们没预料到的 stage 出现在结果里，说明观测面已经
    偏离设计，应当如实记为可观测性缺陷并让整体不通过——而不是让判定过程本身崩掉
    （崩掉会被 CI 当成基础设施故障重试，真正的问题反而被掩盖）。
    """
    return _STAGE_FAILURE.get(stage, "F6")


@dataclass(frozen=True, slots=True)
class StageResult:
    """单个 stage 的判定结论。"""

    name: str
    passed: bool
    failure_code: str | None = None
    severity: str = "blocking"  # Phase C 前恒 blocking
    detail: str = ""

    def __str__(self) -> str:
        mark = "PASS" if self.passed else f"FAIL[{self.failure_code}]"
        return f"{self.name}={mark}({self.detail})" if self.detail else f"{self.name}={mark}"


@dataclass(frozen=True, slots=True)
class IntegrationValidationResult:
    """闭环判定结论（全 AND）。"""

    scenario_id: str
    ok: bool
    stages: tuple[StageResult, ...] = ()
    score: ScenarioScore | None = None

    def failed_stages(self) -> tuple[StageResult, ...]:
        return tuple(s for s in self.stages if not s.passed)

    def failure_codes(self) -> tuple[str, ...]:
        """已触发的失败码（去重后按 F1..F6 固定序，保证报告可复现）。"""
        seen = {s.failure_code for s in self.stages if not s.passed and s.failure_code}
        return tuple(code for code in FAILURE_CODES if code in seen)

    def __str__(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        return f"[{status}] {self.scenario_id} " + " ".join(str(s) for s in self.stages)


def _command_ids(commands: tuple[Any, ...]) -> set[str]:
    return {str(c.command_id) for c in commands}


def _command_types(commands: tuple[Any, ...]) -> set[str]:
    return {c.command_type for c in commands}


def _warning_ids(warnings: tuple[Any, ...]) -> set[str]:
    return {str(w.warning_id) for w in warnings}


def _warn_trace_warning_ids(traces: tuple[Any, ...]) -> set[str]:
    return {
        str(t.outcome.warning_id)
        for t in traces
        if t.outcome.kind is TraceOutcomeKind.WARN and t.outcome.warning_id is not None
    }


def _suppress_traces(traces: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(t for t in traces if t.outcome.kind is TraceOutcomeKind.SUPPRESS)


def _warn_traces(traces: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(t for t in traces if t.outcome.kind is TraceOutcomeKind.WARN)


class IntegrationValidator:
    """把 ``IntegrationRunResult`` 判定为 ``IntegrationValidationResult``（D5）。"""

    def validate(
        self, result: IntegrationRunResult, scenario: Scenario
    ) -> IntegrationValidationResult:
        """逐 stage 判定，全 AND 汇总。

        期望来源为 ``scenario.integration``（opt-in）。**未声明期望不等于不判定**：
        静默丢弃检测（§0.4）始终生效——它检查的是"系统内部是否自洽"，与作者是否
        写了期望无关。否则只要不写期望就能让任何丢弃逃逸。
        """
        from home_perception.evaluation.metrics import build_scenario_score
        from home_perception.validation.runner.runner import ScenarioValidator

        suite = scenario.integration or IntegrationExpectationSuite()

        scenario_validation = ScenarioValidator().validate(result.run_result, scenario)
        score = build_scenario_score(scenario, result.run_result, scenario_validation)

        stages: list[StageResult] = [
            self._check_perception(result, suite, score),
            self._check_decision(result, suite),
            self._check_notification(result, suite),
            self._check_memory(result, suite),
            # cross_modal（F5）属 Phase B，Phase A 不产出该 stage（不是"通过"，是"不适用"）
            self._check_observability(result),  # F6 恒最后跑：先有业务事实，再校验观测一致性
        ]
        ok = all(s.passed for s in stages)
        return IntegrationValidationResult(
            scenario_id=result.scenario_id, ok=ok, stages=tuple(stages), score=score
        )

    # ------------------------------------------------------------------ F1
    @staticmethod
    def _check_perception(
        result: IntegrationRunResult, suite: IntegrationExpectationSuite, score: ScenarioScore
    ) -> StageResult:
        """感知 stage（F1）：复用 ``build_scenario_score.validation_ok`` + 事件数下界。"""
        details: list[str] = [f"validation_ok={score.validation_ok}"]
        passed = bool(score.validation_ok)
        if not passed:
            details.append(f"missing_event_types={sorted(score.missing_event_types)}")

        exp = suite.perception
        if exp is not None and exp.min_perception_events is not None:
            observed = len(result.perception_events)
            details.append(f"perception_events={observed}>={exp.min_perception_events}?")
            if observed < exp.min_perception_events:
                passed = False
        return StageResult(
            name="perception",
            passed=passed,
            failure_code=None if passed else classify_failure("perception"),
            detail="; ".join(details),
        )

    # ------------------------------------------------------------------ F2
    @staticmethod
    def _check_decision(
        result: IntegrationRunResult, suite: IntegrationExpectationSuite
    ) -> StageResult:
        """决策 stage（F2）：静默丢弃检测 + 声明式期望。

        静默丢弃判定式在此的具体形态：
        ``有 perception_event（上游存在） ∧ 无任何 trace（下游缺失） ∧ 无 SUPPRESS 理由``。
        生产链路里"有感知事件"必然调用 ``DecisionEngine``，而 engine 无论 WARN 还是抑制
        都会留痕；因此"有事件却零 trace"只可能是决策层被跳过或探针未接上——两者都必须暴露。
        """
        traces = result.decision_traces
        warn_traces = _warn_traces(traces)
        suppress = _suppress_traces(traces)
        details: list[str] = [
            f"traces={len(traces)}(WARN={len(warn_traces)},SUPPRESS={len(suppress)})",
            f"warnings={len(result.warnings)}",
        ]
        passed = True

        # (a) 静默丢弃：上游存在 ∧ 下游缺失 ∧ 无理由
        upstream_exists = len(result.perception_events) > 0
        if upstream_exists and not traces and not result.warnings:
            passed = False
            details.append("silent_drop: 有 perception_event 但决策层零 trace 零 warning")

        # (b) 声明式期望
        exp = suite.decision
        if exp is not None and exp.outcome is not None:
            details.append(f"expect_outcome={exp.outcome}")
            if exp.outcome == TraceOutcomeKind.WARN.value:
                if not warn_traces:
                    passed = False
                    details.append("缺 WARN trace")
                else:
                    ok, why = IntegrationValidator._match_warn_fields(result, exp)
                    if not ok:
                        passed = False
                        details.append(why)
            elif exp.outcome == TraceOutcomeKind.SUPPRESS.value:
                if not suppress:
                    passed = False
                    details.append("缺 SUPPRESS trace")
                elif exp.reason_code is not None:
                    observed = {
                        t.outcome.suppress_reason.value
                        for t in suppress
                        if t.outcome.suppress_reason is not None
                    }
                    if exp.reason_code not in observed:
                        passed = False
                        details.append(f"suppress_reason {sorted(observed)} 不含 {exp.reason_code}")
            elif exp.outcome == OUTCOME_NONE:
                # "合法未触发"：既不能有告警，缺失又必须能被解释
                if warn_traces or result.warnings:
                    passed = False
                    details.append("期望 NONE 但产生了告警")
                elif upstream_exists and not suppress:
                    passed = False
                    details.append("期望 NONE 但上游有事件、下游无告警且无 SUPPRESS 理由")
        return StageResult(
            name="decision",
            passed=passed,
            failure_code=None if passed else classify_failure("decision"),
            detail="; ".join(details),
        )

    @staticmethod
    def _match_warn_fields(result: IntegrationRunResult, exp: Any) -> tuple[bool, str]:
        """WARN 的结构化字段比对（区分 WARN_LOW / WARN_HIGH，D4）。

        ``risk_level`` / ``recommended_action`` 按"**存在任一 warning 满足**"判定，而非
        "全部满足"：一个场景可能合法地产出多条不同等级的告警，要求全部相同会把正常的
        多告警场景判死。
        """
        warnings = result.warnings
        if exp.risk_level is not None:
            observed = {w.risk_level for w in warnings}
            if exp.risk_level not in observed:
                return False, f"risk_level {sorted(observed)} 不含 {exp.risk_level}"
        if exp.recommended_action is not None:
            observed_a = {w.recommended_action for w in warnings}
            if exp.recommended_action not in observed_a:
                return False, f"recommended_action {sorted(observed_a)} 不含 {exp.recommended_action}"
        if exp.confidence is not None:
            scores = [w.perception_score for w in warnings]
            if not scores or max(scores) < exp.confidence:
                return False, f"perception_score max={max(scores) if scores else None} < {exp.confidence}"
        return True, ""

    # ------------------------------------------------------------------ F3
    @staticmethod
    def _check_notification(
        result: IntegrationRunResult, suite: IntegrationExpectationSuite
    ) -> StageResult:
        """通知/行动 stage（F3）：逐 ``warning_id`` 覆盖检查 + 声明式期望。

        静默丢弃的精确形态：某条 warning 存在，却没有任何 ``ActionCommand`` 引用它的
        ``warning_id``。按 id 逐条比对而不是比总数——总数相等完全可能是"A 的命令发了两条、
        B 的一条没发"，这正是最危险的漏发形态。
        """
        commands = result.commands
        details: list[str] = [f"commands={len(commands)}", f"warnings={len(result.warnings)}"]
        passed = True

        covered = {str(getattr(c, "warning_id", "")) for c in commands}
        uncovered = sorted(_warning_ids(result.warnings) - covered)
        if uncovered:
            passed = False
            details.append(f"silent_drop: warning 无对应命令 {uncovered}")

        exp = suite.action
        if exp is not None:
            observed_types = _command_types(commands)
            if exp.expected_command_types is not None:
                required = set(exp.expected_command_types)
                details.append(f"types={sorted(observed_types)} ⊇ {sorted(required)}?")
                if not required.issubset(observed_types):
                    passed = False
                    details.append(f"缺命令类型 {sorted(required - observed_types)}")
            if exp.expected_notification is not None:
                details.append(f"expect_notification={exp.expected_notification}")
                if exp.expected_notification and not commands:
                    passed = False
                    details.append("期望发出通知但零命令")
                if not exp.expected_notification and commands:
                    passed = False
                    details.append(f"期望不发通知但产生 {len(commands)} 条命令（误发）")
        return StageResult(
            name="notification",
            passed=passed,
            failure_code=None if passed else classify_failure("notification"),
            detail="; ".join(details),
        )

    # ------------------------------------------------------------------ F4
    @staticmethod
    def _check_memory(
        result: IntegrationRunResult, suite: IntegrationExpectationSuite
    ) -> StageResult:
        """Memory stage（F4）：落库下界 + 静默丢弃检测。

        静默丢弃形态：有 ``perception_event``（意味着上游必然产生过 ``VisitorEvent``，
        ``MemoryHook.record`` 必被调用）却零 episodic 记录。

        反向不成立：无 ``perception_event`` 时仍可能有 episode（访客来过但规则未命中），
        因此不能用"episode 数 == 事件数"这类等式判定——那会把正常的良性访问判失败。
        """
        episodes = result.episodes
        details: list[str] = [f"episodes={len(episodes)}"]
        passed = True

        if result.perception_events and not episodes:
            passed = False
            details.append("silent_drop: 有 perception_event 但零 episodic 记录")

        exp = suite.memory
        if exp is not None:
            details.append(f"min_records={exp.min_records}")
            if len(episodes) < exp.min_records:
                passed = False
                details.append(f"落库 {len(episodes)} < 下界 {exp.min_records}")
        return StageResult(
            name="memory",
            passed=passed,
            failure_code=None if passed else classify_failure("memory"),
            detail="; ".join(details),
        )

    # ------------------------------------------------------------------ F6
    @staticmethod
    def _check_observability(result: IntegrationRunResult) -> StageResult:
        """可观测性 stage（F6）：三通道交叉校验，**severity 恒 blocking，永不可降级**。

        这一 stage 检查的不是"系统做得对不对"，而是"我们看到的是不是系统真正做的"。
        它一旦失败，其余所有 stage 的结论都失去证据基础——所以哪怕业务 stage 全绿，
        整体也必须判不通过。
        """
        details: list[str] = []
        passed = True

        # 通道 A：ActionSink ↔ FrameResult.commands（command_id 集合）
        prod_cmd_ids = _command_ids(result.commands)
        sink_cmd_ids = _command_ids(result.sink_commands)
        if prod_cmd_ids != sink_cmd_ids:
            passed = False
            details.append(
                "chA mismatch: FrameResult.commands 独有="
                f"{sorted(prod_cmd_ids - sink_cmd_ids)}，sink 独有={sorted(sink_cmd_ids - prod_cmd_ids)}"
            )
        else:
            details.append(f"chA ok({len(prod_cmd_ids)})")

        # 通道 B：WARN traces ↔ FrameResult.warnings（warning_id 集合）
        prod_warn_ids = _warning_ids(result.warnings)
        trace_warn_ids = _warn_trace_warning_ids(result.decision_traces)
        if prod_warn_ids != trace_warn_ids:
            passed = False
            details.append(
                "chB mismatch: warnings 独有="
                f"{sorted(prod_warn_ids - trace_warn_ids)}，WARN trace 独有="
                f"{sorted(trace_warn_ids - prod_warn_ids)}"
            )
        else:
            details.append(f"chB ok({len(prod_warn_ids)})")

        # 通道 C：EpisodicRecord.actions ⊆ 已执行命令（Memory 投影不得凭空出现命令）
        episode_cmd_ids: set[str] = set()
        for ep in result.episodes:
            for act in getattr(ep, "actions", ()) or ():
                episode_cmd_ids.add(str(act.command_id))
        phantom = sorted(episode_cmd_ids - prod_cmd_ids)
        if phantom:
            passed = False
            details.append(f"chC mismatch: episode 引用了未执行的命令 {phantom}")
        else:
            details.append(f"chC ok({len(episode_cmd_ids)})")

        return StageResult(
            name="observability",
            passed=passed,
            failure_code=None if passed else classify_failure("observability"),
            severity="blocking",  # F6 永不可降级（Phase C 亦然）
            detail="; ".join(details),
        )
