"""ADR-0033 Phase 1：``BenchmarkReport``（离散指标聚合，禁止单一加权分）。

仅聚合离散、可解释指标（precision / recall / F1 / FN / FP / suppression_rate /
false_alarm_rate + 每场景混淆 + unlabeled_scenario_ids），携带 ``harness_fingerprint``
（D4 三元组）。**不产出** ``BenchmarkScore`` 单一加权分（D5，Phase 3 才引入且须
``calibrated=False``）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from home_perception.analysis.decision_sink import assert_desensitized

from .metrics import (
    OUTCOME_FN,
    OUTCOME_FP,
    OUTCOME_TN,
    OUTCOME_TP,
    OUTCOME_UNLABELED,
    ScenarioScore,
    _require_mapping,
)


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


@dataclass(frozen=True)
class BenchmarkReport:
    """跨场景聚合报告（D1 / D3 / D4）。

    ``harness_fingerprint`` 由 ``harness.compute_harness_fingerprint`` 计算（D4 三元组）。
    ``generated_at`` 为信息性时间戳，**不**进入 ``canonical_dict``（保证 T1 确定性）。
    """

    scenario_set_id: str
    harness_fingerprint: str
    scores: tuple[ScenarioScore, ...] = field(default_factory=tuple)
    generated_at: str = ""

    # —— 聚合指标（Phase 1 离散，无单一加权分）——
    tp: int = 0
    tn: int = 0
    fn: int = 0
    fp: int = 0
    unlabeled_scenario_count: int = 0
    suppression_rate: float = 0.0
    false_alarm_rate: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    mean_event_recall: float = 0.0
    mean_risk_shortfall: float | None = None
    unlabeled_scenario_ids: tuple[str, ...] = field(default_factory=tuple)
    provenance: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def aggregate(
        cls,
        *,
        scenario_set_id: str,
        harness_fingerprint: str,
        scores: list[ScenarioScore],
        generated_at: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> BenchmarkReport:
        """从 ``ScenarioScore`` 列表聚合（纯函数）。"""
        tp = tn = fn = fp = unlabeled = 0
        recalls: list[float] = []
        shortfalls: list[float] = []
        unlabeled_ids: list[str] = []
        for s in scores:
            if s.outcome == OUTCOME_TP:
                tp += 1
            elif s.outcome == OUTCOME_TN:
                tn += 1
            elif s.outcome == OUTCOME_FN:
                fn += 1
            elif s.outcome == OUTCOME_FP:
                fp += 1
            elif s.outcome == OUTCOME_UNLABELED:
                unlabeled += 1
                unlabeled_ids.append(s.scenario_id)
            recalls.append(s.event_recall)
            if s.risk_shortfall is not None:
                shortfalls.append(s.risk_shortfall)
        suppression_rate = _safe_div(fn, fn + tp)
        false_alarm_rate = _safe_div(fp, fp + tn)
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        mean_event_recall = _safe_div(sum(recalls), len(recalls)) if recalls else 0.0
        mean_risk_shortfall = (sum(shortfalls) / len(shortfalls)) if shortfalls else None
        return cls(
            scenario_set_id=scenario_set_id,
            harness_fingerprint=harness_fingerprint,
            scores=tuple(scores),
            generated_at=generated_at,
            tp=tp,
            tn=tn,
            fn=fn,
            fp=fp,
            unlabeled_scenario_count=unlabeled,
            suppression_rate=suppression_rate,
            false_alarm_rate=false_alarm_rate,
            precision=precision,
            recall=recall,
            f1=f1,
            mean_event_recall=mean_event_recall,
            mean_risk_shortfall=mean_risk_shortfall,
            unlabeled_scenario_ids=tuple(sorted(unlabeled_ids)),
            provenance=provenance or {},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_set_id": self.scenario_set_id,
            "harness_fingerprint": self.harness_fingerprint,
            "generated_at": self.generated_at,
            "metrics": {
                "tp": self.tp,
                "tn": self.tn,
                "fn": self.fn,
                "fp": self.fp,
                "unlabeled_scenario_count": self.unlabeled_scenario_count,
                "suppression_rate": self.suppression_rate,
                "false_alarm_rate": self.false_alarm_rate,
                "precision": self.precision,
                "recall": self.recall,
                "f1": self.f1,
                "mean_event_recall": self.mean_event_recall,
                "mean_risk_shortfall": self.mean_risk_shortfall,
            },
            "unlabeled_scenario_ids": list(self.unlabeled_scenario_ids),
            "provenance": self.provenance,
            "scores": [s.to_dict() for s in self.scores],
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> BenchmarkReport:
        """从 ``to_dict`` / ``canonical_dict`` 结构重建（Phase 2 基线可回放 T10）。

        ``canonical_dict`` 不含 ``generated_at``；``mean_risk_shortfall`` 允许 ``None``；
        ``provenance`` 一并恢复（基线对照的守恒校验依赖它）。

        反序列化校验（M10 / L11）：缺字段 / 类型不符统一转 ``TypeError`` 带上下文
        （TRY004：类型错误用 ``TypeError``，与 ``load_baseline_report_path`` 一致）；
        数值字段强制 ``isinstance`` 校验，拒绝字符串 / ``None`` 静默塌缩（手工编辑基线若塞入
        ``"tp": "abc"`` 或 ``None`` 必须显式报错，而非被 ``int()``/``float()`` 吞掉）。
        """
        if not isinstance(d, dict):
            _require_mapping(d, "基线报告")  # Medium 14：与 ScenarioScore / load 共享校验口径

        scenario_set_id = d.get("scenario_set_id")
        if not isinstance(scenario_set_id, str) or not scenario_set_id:
            raise TypeError("基线报告缺字段 scenario_set_id（或为空/非字符串）")
        harness_fingerprint = d.get("harness_fingerprint")
        if not isinstance(harness_fingerprint, str) or not harness_fingerprint:
            raise TypeError("基线报告缺字段 harness_fingerprint（或为空/非字符串）")

        metrics = d.get("metrics")
        if not isinstance(metrics, dict):
            raise TypeError("基线报告缺字段 metrics（或为非对象）")

        int_keys = ("tp", "tn", "fn", "fp", "unlabeled_scenario_count")
        float_keys = (
            "suppression_rate",
            "false_alarm_rate",
            "precision",
            "recall",
            "f1",
            "mean_event_recall",
        )
        int_vals: dict[str, int] = {}
        for k in int_keys:
            v = metrics.get(k)
            # 计数键：纯 int，或整数值 float（如手工编辑写成 1.0）；bool 子类显式拒绝，
            # 非整数 float（如 1.5）拒收（Medium 7：与 aggregate 永远产 int 对齐，且容错手工 1.0）。
            if isinstance(v, bool) or not isinstance(v, (int, float)) or (
                isinstance(v, float) and not v.is_integer()
            ):
                raise TypeError(f"基线报告指标 {k!r} 须为整数（或整数值浮点），收到 {v!r}")
            int_vals[k] = int(v)
        float_vals: dict[str, float] = {}
        for k in float_keys:
            v = metrics.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise TypeError(f"基线报告指标 {k!r} 须为数值，收到 {v!r}")
            float_vals[k] = float(v)

        shortfall = metrics.get("mean_risk_shortfall")
        if shortfall is not None and (
            not isinstance(shortfall, (int, float)) or isinstance(shortfall, bool)
        ):
            raise TypeError(f"基线报告指标 mean_risk_shortfall 须为数值或 None，收到 {shortfall!r}")

        scores_raw = d.get("scores", [])
        if not isinstance(scores_raw, list):
            raise TypeError("基线报告 scores 须为列表")

        return cls(
            scenario_set_id=scenario_set_id,
            harness_fingerprint=harness_fingerprint,
            scores=tuple(ScenarioScore.from_dict(s) for s in scores_raw),
            generated_at=str(d.get("generated_at", "")),
            tp=int_vals["tp"],
            tn=int_vals["tn"],
            fn=int_vals["fn"],
            fp=int_vals["fp"],
            unlabeled_scenario_count=int_vals["unlabeled_scenario_count"],
            suppression_rate=float_vals["suppression_rate"],
            false_alarm_rate=float_vals["false_alarm_rate"],
            precision=float_vals["precision"],
            recall=float_vals["recall"],
            f1=float_vals["f1"],
            mean_event_recall=float_vals["mean_event_recall"],
            mean_risk_shortfall=(float(shortfall) if shortfall is not None else None),
            unlabeled_scenario_ids=tuple(d.get("unlabeled_scenario_ids", [])),  # type: ignore[arg-type]
            provenance=dict(d.get("provenance", {})),  # type: ignore[arg-type]
        )

    def _sorted_scores(self) -> list[ScenarioScore]:
        """场景排序**唯一来源**（按 ``scenario_id`` 升序）。

        ``render_markdown`` 与 ``canonical_dict`` 共用，避免两处独立 ``sorted`` 排序键不一致
        导致"文本报告"与"canonical JSON"场景序错位（review 1.3）。
        """
        return sorted(self.scores, key=lambda s: s.scenario_id)

    def canonical_dict(self) -> dict[str, object]:
        """确定性序列化（剔除 ``generated_at`` 时间戳，保证 T1 跨进程逐字节一致）。

        同时对 ``scores`` 按 ``scenario_id`` 升序排序，使聚合结果顺序无关
        （排序经 ``_sorted_scores`` 单一来源）。
        """
        d = self.to_dict()
        d.pop("generated_at", None)
        d["scores"] = [s.to_dict() for s in self._sorted_scores()]  # type: ignore[index]
        return d

    def render_markdown(self) -> str:
        """人类可读 Markdown 报告（Phase 1 给人读、人工判断，不进门禁）。"""
        lines = [
            f"# Benchmark Report — `{self.scenario_set_id}`",
            "",
            f"- **harness_fingerprint**: `{self.harness_fingerprint[:16]}…`",
            (
                f"- **scenarios**: {len(self.scores)} "
                f"(labeled={self.tp + self.tn + self.fn + self.fp}, "
                f"unlabeled={self.unlabeled_scenario_count})"
            ),
            "",
            "## 离散指标（非门控，人工判断）",
            f"- TP={self.tp} TN={self.tn} FN(漏报)={self.fn} FP(误报)={self.fp}",
            f"- suppression_rate(漏报率)={self.suppression_rate:.3f}",
            f"- false_alarm_rate(误报率)={self.false_alarm_rate:.3f}",
            f"- precision={self.precision:.3f} recall={self.recall:.3f} F1={self.f1:.3f}",
            f"- mean_event_recall={self.mean_event_recall:.3f}",
            f"- mean_risk_shortfall={self.mean_risk_shortfall}",
            "",
            "## 每场景",
        ]
        for s in self._sorted_scores():
            lines.append(
                f"- `{s.scenario_id}`: {s.outcome} "
                f"(expected={s.expected_label}, actual={s.actual_label}, "
                f"event_recall={s.event_recall:.2f}, validation_ok={s.validation_ok})"
            )
        if self.unlabeled_scenario_ids:
            lines.append("")
            lines.append(f"## 未标注（不参与混淆矩阵）: {list(self.unlabeled_scenario_ids)}")
        return "\n".join(lines)

    def write_report(self, path: str) -> None:
        """落盘 JSON 报告（确定性部分用 canonical_dict 之外仍含 generated_at）。

        落盘前双重守卫（fail-closed）：
        - **脱敏守卫**（review 2.3，复用 ADR-0031 ``assert_desensitized``）：任何未脱敏内容
          （原始媒体路径 / 凭证类键 / bytes）一律拒绝写入，把"写入磁盘 = 已通过守卫"的内聚
          责任收口到 API 本身，而非依赖测试侧护栏；
        - **父目录存在性守卫**（review 3.2）：拒绝自动创建父目录，纵深防御
          ``../../etc/...`` 类路径穿越——调用方须显式提供已存在的输出目录。
        """
        p = Path(path).resolve()
        if not p.parent.exists():
            raise ValueError(
                f"write_report 父目录不存在，拒绝自动创建以防路径穿越：{p.parent}"
            )
        assert_desensitized(self.to_dict())  # 落盘即脱敏守卫
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def write_canonical_report(self, path: str) -> None:
        """落盘**确定性**基线 JSON（``canonical_dict``，剔除 ``generated_at``）。

        与 ``write_report`` 共用双重守卫（fail-closed）：脱敏守卫 + 父目录存在性守卫
        （拒绝自动创建父目录，纵深防御路径穿越）。供基线 bump 工作流（``--write-baseline``）
        复用，保证"基线落盘 = 已通过脱敏守卫"（C2 / M8，与 ``write_report`` 同向）。
        """
        p = Path(path).resolve()
        if not p.parent.exists():
            raise ValueError(
                f"write_canonical_report 父目录不存在，拒绝自动创建以防路径穿越：{p.parent}"
            )
        assert_desensitized(self.canonical_dict())  # 落盘即脱敏守卫
        p.write_text(
            json.dumps(self.canonical_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
