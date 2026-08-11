"""ADR-0034 Phase A · D7：``IntegrationReport`` —— 闭环判定的可复现报告与落盘守卫。

报告承担两个互不重叠的职责，因此提供**两套序列化**：

| 方法 | 用途 | 是否含易变字段 |
|---|---|---|
| ``to_dict`` | 人/CI 排障，要看清"到底哪里断了" | 含 ``generated_at`` / stage ``detail`` |
| ``canonical_dict`` | 确定性比对（t1：同 seed 两次运行逐字节一致） | **全部剔除** |

**为什么 canonical 必须剔除到"只剩结构事实"**（实测约束，非保守估计）：

- ``ActionCommand.command_id`` / ``WarningEvent.warning_id`` 均为 ``uuid4()``；
- ``ActionCommand.created_at`` / ``updated_at`` 走 ``_utc_now()`` 墙钟，**不**受
  ``IntegrationContext`` 的可控时钟约束；
- ``StageResult.detail`` 在失败分支会内嵌上述 UUID（如"warning 无对应命令 [...]"）。

即便把时钟固定成 ``DEFAULT_CLOCK_START``，这三类字段仍逐次不同。若把它们放进 canonical，
t1 会变成一个**永远失败**的断言，进而被"放宽成只比字段名"之类的妥协架空。因此 canonical
只保留计数与"去重排序后的类型集合"——这恰好也是闭环真正要守住的不变量：**发生了哪几类事、
各发生多少次**，而不是每件事的流水号。

**脱敏与键名的硬约束**（``analysis.decision_sink.assert_desensitized`` 的禁止键是**精确
匹配**）：``{"decision", "score", "verdict", "risk_score", ...}`` 一旦作为 JSON **键**出现
即拒绝落盘。这直接约束了本模块的序列化形态：

1. stage **必须**序列化为**列表** ``[{"name": "decision", ...}]``，而不是
   ``{"decision": {...}}``——守卫只扫键名，``"decision"`` 作为**值**是安全的；
2. ``ScenarioScore`` 挂在 ``perception_score`` 键下，不能用 ``score``（精确命中禁止集）。

这两条不是风格偏好，写错会在落盘那一刻 fail-closed。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from home_perception.analysis.decision_sink import assert_desensitized

if TYPE_CHECKING:
    from .runner import IntegrationRunResult
    from .validator import IntegrationValidationResult

__all__ = ["IntegrationReport", "LoopArtifactSummary"]


def _sorted_unique(values: Any) -> tuple[str, ...]:
    """去重 + 排序为字符串元组（canonical 的基本构件）。

    ``None`` 一律剔除而非转成 ``"None"``：报告里出现字面量 ``"None"`` 会让"字段缺失"和
    "字段值恰好叫 None"无法区分。
    """
    return tuple(sorted({str(v) for v in values if v is not None}))


def _enum_value(obj: Any) -> Any:
    """取枚举 ``.value``，非枚举原样返回（``TraceOutcomeKind`` / ``SuppressReason``）。"""
    return getattr(obj, "value", obj)


@dataclass(frozen=True, slots=True)
class LoopArtifactSummary:
    """闭环 artifacts 的**结构化**摘要：只含计数与类型集合。

    刻意不含 UUID / 时间戳 / 文件路径——既为 canonical 的确定性，也让报告天然满足
    ADR-0002 的脱敏姿态（不是靠事后扫描，而是结构上就装不下）。
    """

    n_perception_events: int = 0
    n_warnings: int = 0
    n_commands: int = 0
    n_sink_commands: int = 0
    n_decision_traces: int = 0
    n_episodes: int = 0
    n_cross_modal_links: int = 0
    event_types: tuple[str, ...] = ()
    risk_levels: tuple[str, ...] = ()
    recommended_actions: tuple[str, ...] = ()
    command_types: tuple[str, ...] = ()
    trace_outcome_kinds: tuple[str, ...] = ()
    suppress_reasons: tuple[str, ...] = ()
    episode_action_command_types: tuple[str, ...] = ()

    @classmethod
    def from_run(cls, result: IntegrationRunResult) -> LoopArtifactSummary:
        """从 ``IntegrationRunResult`` 投影（纯函数，不判定、不兜底填充）。"""
        traces = result.decision_traces
        episode_action_types: list[str] = []
        for ep in result.episodes:
            for act in getattr(ep, "actions", ()) or ():
                episode_action_types.append(act.command_type)
        return cls(
            n_perception_events=len(result.perception_events),
            n_warnings=len(result.warnings),
            n_commands=len(result.commands),
            n_sink_commands=len(result.sink_commands),
            n_decision_traces=len(traces),
            n_episodes=len(result.episodes),
            n_cross_modal_links=len(result.cross_modal_links),
            event_types=_sorted_unique(e.event_type for e in result.perception_events),
            risk_levels=_sorted_unique(w.risk_level for w in result.warnings),
            recommended_actions=_sorted_unique(
                w.recommended_action for w in result.warnings
            ),
            command_types=_sorted_unique(c.command_type for c in result.commands),
            trace_outcome_kinds=_sorted_unique(
                _enum_value(t.outcome.kind) for t in traces
            ),
            suppress_reasons=_sorted_unique(
                _enum_value(t.outcome.suppress_reason) for t in traces
            ),
            episode_action_command_types=_sorted_unique(episode_action_types),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": {
                "perception_events": self.n_perception_events,
                "warnings": self.n_warnings,
                "commands": self.n_commands,
                "sink_commands": self.n_sink_commands,
                "decision_traces": self.n_decision_traces,
                "episodes": self.n_episodes,
                "cross_modal_links": self.n_cross_modal_links,
            },
            "event_types": list(self.event_types),
            "risk_levels": list(self.risk_levels),
            "recommended_actions": list(self.recommended_actions),
            "command_types": list(self.command_types),
            "trace_outcome_kinds": list(self.trace_outcome_kinds),
            "suppress_reasons": list(self.suppress_reasons),
            "episode_action_command_types": list(self.episode_action_command_types),
        }


@dataclass(frozen=True)
class IntegrationReport:
    """一次闭环运行的完整报告（判定结论 + 结构化 artifacts + 溯源）。

    ``provenance`` 是唯一的自由字典（供调用方塞 ``code_version`` / ``scenario_set_id``
    等溯源信息）。它也因此是报告里唯一可能混入 PII 的入口——落盘守卫正是为它存在
    （t5：塞入原始媒体路径时 ``write_report`` 必须 fail-closed，而不是写出去再说）。

    **两枚闭环指纹的填充契约**（评审 B3/E3）：``expectation_fingerprint`` /
    ``loop_fingerprint`` 由 ``build()`` 从 ``run_result`` 投影（runner 保证非空）；
    **直接构造** ``IntegrationReport(...)`` 会得到空字符串——属调用方责任。canonical
    序列化**固定**包含这两个键（空字符串也输出，键集稳定），同 seed 两次运行的一致性
    不受影响（空则两次皆空）。指纹成分均为确定性输入（场景/期望/装配，**不含**环境
    版本），跨环境（本地/CI）算值一致——场景变更导致的指纹变化正是 DoD C4 基线漂移
    治理的预期触发源，而非伪造漂移。
    """

    scenario_id: str
    ok: bool
    mode: str = ""
    n_frames: int = 0
    scenario_fingerprint: str = ""
    expectation_fingerprint: str = ""  # Phase C：评价标准指纹（run_result 投影）
    loop_fingerprint: str = ""  # Phase C：运行血缘指纹（run_result 投影）
    failure_codes: tuple[str, ...] = ()
    stages: tuple[Any, ...] = ()  # tuple[StageResult, ...]（避免运行期循环 import）
    artifacts: LoopArtifactSummary = field(default_factory=LoopArtifactSummary)
    perception_score: dict[str, Any] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""

    # ------------------------------------------------------------------ 构造
    @classmethod
    def build(
        cls,
        run_result: IntegrationRunResult,
        validation: IntegrationValidationResult,
        *,
        generated_at: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> IntegrationReport:
        """由 Runner 产物 + Validator 结论组装（纯函数，不重新判定）。

        报告**不**自行推导 ``ok``——那是 ``IntegrationValidator`` 的唯一职责。这里若再算
        一遍，就会出现"报告说通过、验证器说不通过"的双事实源，而两者不一致时没人知道
        该信谁。

        两枚闭环指纹（``expectation_fingerprint`` / ``loop_fingerprint``）**直接投影**
        ``run_result`` 的已算值（B.3），不在此重算——报告是 CI 决策依据，必须能看到
        "用什么标准 + 怎么跑的"（DoD C2/C5）。
        """
        return cls(
            scenario_id=validation.scenario_id or run_result.scenario_id,
            ok=validation.ok,
            mode=run_result.mode,
            n_frames=run_result.n_frames,
            scenario_fingerprint=run_result.fingerprint,
            expectation_fingerprint=run_result.expectation_fingerprint,
            loop_fingerprint=run_result.loop_fingerprint,
            failure_codes=validation.failure_codes(),
            stages=tuple(validation.stages),
            artifacts=LoopArtifactSummary.from_run(run_result),
            perception_score=(
                validation.score.to_dict() if validation.score is not None else None
            ),
            provenance=dict(provenance or {}),
            generated_at=generated_at,
        )

    # ------------------------------------------------------------------ 序列化
    def _stage_dicts(self, *, include_detail: bool) -> list[dict[str, Any]]:
        """stage 序列化（**列表**形态，stage 名作为值而非键——见模块 docstring）。

        顺序即 ``IntegrationValidator`` 的产出顺序（perception → decision → notification
        → memory → observability），本身已确定，故不再排序：再排一次会让"F6 恒最后"这一
        语义在报告里消失，而确定性并不因此增强。
        """
        out: list[dict[str, Any]] = []
        for s in self.stages:
            d: dict[str, Any] = {
                "name": s.name,
                "passed": s.passed,
                "failure_code": s.failure_code,
                "severity": s.severity,
            }
            if include_detail:
                d["detail"] = s.detail
            out.append(d)
        return out

    def to_dict(self) -> dict[str, Any]:
        """完整报告（含 ``generated_at`` 与 stage ``detail``，供人排障）。"""
        return {
            "scenario_id": self.scenario_id,
            "ok": self.ok,
            "mode": self.mode,
            "n_frames": self.n_frames,
            "scenario_fingerprint": self.scenario_fingerprint,
            "expectation_fingerprint": self.expectation_fingerprint,
            "loop_fingerprint": self.loop_fingerprint,
            "generated_at": self.generated_at,
            "failure_codes": list(self.failure_codes),
            "stages": self._stage_dicts(include_detail=True),
            "artifacts": self.artifacts.to_dict(),
            "perception_score": self.perception_score,
            "provenance": self.provenance,
        }

    def canonical_dict(self) -> dict[str, Any]:
        """确定性序列化（t1：同 seed 两次运行逐字节一致）。

        相对 ``to_dict`` 剔除三类易变内容：``generated_at``（墙钟）、stage ``detail``
        （失败分支内嵌 uuid4）、``provenance``（调用方自由字典，可能含时间/路径）。
        """
        d = self.to_dict()
        d.pop("generated_at", None)
        d.pop("provenance", None)
        d["stages"] = self._stage_dicts(include_detail=False)
        return d

    def render_markdown(self) -> str:
        """人类可读结论（排障用；不进任何门禁判定）。"""
        status = "PASS" if self.ok else "FAIL"
        a = self.artifacts
        lines = [
            f"# Integration Report — `{self.scenario_id}` [{status}]",
            "",
            f"- **mode**: {self.mode}　**frames**: {self.n_frames}",
            f"- **scenario_fingerprint**: `{self.scenario_fingerprint[:16]}…`",
            f"- **failure_codes**: {list(self.failure_codes) or '—'}",
            "",
            "## 闭环 artifacts",
            (
                f"- events={a.n_perception_events} warnings={a.n_warnings} "
                f"commands={a.n_commands} (sink={a.n_sink_commands}) "
                f"traces={a.n_decision_traces} episodes={a.n_episodes}"
            ),
            f"- event_types={list(a.event_types)} risk_levels={list(a.risk_levels)}",
            (
                f"- command_types={list(a.command_types)} "
                f"trace_kinds={list(a.trace_outcome_kinds)}"
            ),
            "",
            "## Stages（全 AND）",
        ]
        for s in self.stages:
            mark = "✅" if s.passed else f"❌ {s.failure_code}"
            lines.append(f"- `{s.name}` {mark} — {s.detail}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 落盘
    @staticmethod
    def _guarded_write(path: str | Path, payload: dict[str, Any], *, who: str) -> None:
        """落盘双重守卫（fail-closed），与 ADR-0033 ``BenchmarkReport`` 同口径。

        1. **父目录存在性**：拒绝自动创建父目录，纵深防御 ``../../etc/...`` 类路径穿越；
        2. **脱敏守卫**：复用 ADR-0031 ``assert_desensitized``，把"写入磁盘 = 已过守卫"
           收口到 API 内部，而不是指望每个调用方自觉。
        """
        p = Path(path).resolve()
        if not p.parent.exists():
            raise ValueError(
                f"{who} 父目录不存在，拒绝自动创建以防路径穿越：{p.parent}"
            )
        assert_desensitized(payload)
        p.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def write_report(self, path: str | Path) -> None:
        """落盘完整报告 JSON（含 ``generated_at``），落盘前过双重守卫。"""
        self._guarded_write(path, self.to_dict(), who="write_report")

    def write_canonical_report(self, path: str | Path) -> None:
        """落盘**确定性**报告 JSON（``canonical_dict``），落盘前过双重守卫。"""
        self._guarded_write(path, self.canonical_dict(), who="write_canonical_report")
