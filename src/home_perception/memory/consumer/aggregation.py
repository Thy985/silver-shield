"""RuleBasedAggregation（C-2 默认读侧聚合）。

只计算、不召回、不决策（ADR-0025 C1 / C2）。

把 Retrieval 交付的边界化 ``EpisodicRecord`` 列表聚合成长期模式视图
（``VisitorProfile`` / ``RiskPattern``）。绝不内部再调 Retrieval（单向管道）；
绝不回答"是否异常"的最终结论（C1 无 score / decision / warning）。

数据来源 = 已召回记录（边界由 ``RetrievalConfig`` 施加，见 DESIGN §3.1 / C-1
review #7）：本组件信任输入已按 ``max_records`` / ``lookback_days`` 裁剪，不重复
裁剪窗口。

确定性（C3）：``tags`` 与 ``escalation_history`` 显式排序输出，与输入排序无关 →
同输入两次产出字段级一致（回放 / 审计可复现）。
"""

from __future__ import annotations

from datetime import datetime

from home_perception.memory.consumer.config import AggregationConfig
from home_perception.memory.consumer.contracts import RiskPattern, VisitorProfile
from home_perception.memory.consumer.exceptions import AggregationError
from home_perception.memory.consumer.interfaces import Aggregation
from home_perception.memory.records import EpisodicRecord


class RuleBasedAggregation(Aggregation):
    """确定性规则聚合（C3）。

    流程（build_profile + build_pattern）：
    1. build_profile：从记录统计访客长期画像（visit_count / night_visit_ratio /
       first_seen / last_seen / confidence 分级）。
    2. build_pattern：发现风险模式标签（repeated_visit / escalating_behavior），
       低于 ``min_records_for_pattern`` 时不产出 RiskPattern（返回 None）。

    空记录 → (None, None)（C2 无输入则无画像 / 模式）。
    """

    def __init__(self, config: AggregationConfig | None = None) -> None:
        self._config = config or AggregationConfig()

    def aggregate(
        self, records: list[EpisodicRecord]
    ) -> tuple[VisitorProfile | None, RiskPattern | None]:
        if not records:
            return (None, None)
        try:
            self._assert_same_visitor(records)  # 混合访客输入 -> 显式异常（守 C2 只读 / C3 确定性）
            profile = self._build_profile(records)
            pattern = self._build_pattern(records)
        except AggregationError:  # 契约违例（混合访客）原样上抛，不二次包装
            raise
        except Exception as exc:  # 其余意外异常转译为分层异常，不静默、不向上抛未分类异常
            raise AggregationError(
                f"Aggregation 计算失败 (n={len(records)}): {exc}"
            ) from exc
        return (profile, pattern)

    def _assert_same_visitor(self, records: list[EpisodicRecord]) -> None:
        """校验输入全部归属同一 ``visitor_instance_id``（C2/C3）。

        Aggregation 仅接受 Retrieval 已边界化的单一访客记录；若混入其他访客，
        静默聚合会得到错乱画像，且 ``records[0]`` 顺序可改变归属，破坏 C3 确定性。
        显式抛 ``AggregationError`` 让编排器决定如何处理。
        """
        anchor = records[0].visitor_instance_id
        for ep in records:
            if ep.visitor_instance_id != anchor:
                raise AggregationError(
                    f"聚合输入含多访客记录：锚定 {anchor!r} 但出现 {ep.visitor_instance_id!r}；"
                    f"Aggregation 仅接受单一 visitor_instance_id 的边界化输入"
                )

    # -- 访客画像（统计描述，非分数）---------------------------------------
    def _build_profile(self, records: list[EpisodicRecord]) -> VisitorProfile:
        n = len(records)
        night = sum(1 for ep in records if self._is_night(ep.enter_time))
        ratio = (night / n) if n else 0.0
        first = min((ep.enter_time for ep in records), default=None)
        last = max((ep.leave_time for ep in records), default=None)
        return VisitorProfile(
            visitor_instance_id=records[0].visitor_instance_id,
            visit_count=n,
            night_visit_ratio=ratio,
            confidence=self._confidence_tier(n),
            identity_confirmed=False,  # v1 临时画像恒 False（ADR-0023）
            first_seen=first,
            last_seen=last,
        )

    # -- 风险模式（非分数，模式描述）---------------------------------------
    def _build_pattern(self, records: list[EpisodicRecord]) -> RiskPattern | None:
        n = len(records)
        if n < self._config.min_records_for_pattern:
            return None
        tags: list[str] = []
        if n >= 2:  # 重复来访 → repeated_visit
            tags.append("repeated_visit")
        markers = self._behavior_markers(records)
        # 升级模式要求"多阶段（不同阶段）"：过滤空后缀 + 去重，仅唯一非空标记数
        # >= 2 才判升级（避免两条相同 behavior:loiter 或空 behavior: 误判为升级）。
        unique_markers = tuple(sorted({m for m in markers if m}))
        escalation_history: tuple[str, ...] | None = None
        if len(unique_markers) >= 2:
            tags.append("escalating_behavior")
            escalation_history = unique_markers
        if not tags:
            return None
        return RiskPattern(
            tags=tuple(sorted(set(tags))),
            escalation_history=escalation_history,
            confidence=self._confidence_tier(n),
        )

    # -- 辅助（纯函数，确定性）---------------------------------------------
    @staticmethod
    def _behavior_markers(records: list[EpisodicRecord]) -> list[str]:
        """从记录 ``reason_summary`` 抽取 ``behavior:`` 后缀（含可能为空者，按记录序）；空后缀过滤与去重在 ``_build_pattern`` 完成。"""
        markers: list[str] = []
        for ep in records:
            for r in ep.reason_summary or []:
                if r.startswith("behavior:"):
                    markers.append(r[len("behavior:"):])
        return markers

    def _is_night(self, dt: datetime) -> bool:
        """夜间判定（可配）：``night_start_hour`` 之后或 ``night_end_hour`` 之前。"""
        return dt.hour >= self._config.night_start_hour or dt.hour < self._config.night_end_hour

    def _confidence_tier(self, n: int) -> str:
        """置信度分级（DESIGN §3.2，继承 M0）：cold_start<5 / weak 5–29 / stable≥30。"""
        if n < self._config.cold_start_threshold:
            return "cold_start"
        if n < self._config.weak_pattern_threshold:
            return "weak_pattern"
        return "stable_pattern"


__all__ = ["RuleBasedAggregation"]
