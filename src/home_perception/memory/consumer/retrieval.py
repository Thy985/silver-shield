"""RuleBasedRetrieval（C-1 默认规则召回）。

只召回、不计算、不聚合（ADR-0025 C1 / C2）。

数据源 = ``MemoryStore.get_episodic_by_visitor``（真实代码基元）。**不是**
``MemoryQuery.compose_context``：后者返回人类可读的 ``dict`` 解释视图，不是记录
列表（详见 ``docs/DESIGN-memory-consumer.md`` Errata）。若强行用 ``compose_context``
再反解回 ``EpisodicRecord``，会破坏 C3 确定性与 C5 溯源（ADR-0024 I4）。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from home_perception.core.event import EvidenceModality
from home_perception.memory.consumer.config import RetrievalConfig
from home_perception.memory.consumer.contracts import CurrentEvent
from home_perception.memory.consumer.exceptions import RetrievalError
from home_perception.memory.consumer.interfaces import Retrieval
from home_perception.memory.records import EpisodicRecord, MemoryStatus
from home_perception.memory.store import MemoryStore


class RuleBasedRetrieval(Retrieval):
    """确定性规则召回（C3）。

    流程（Recall → Filter → Rank → Cap）：
    1. Recall：``store.get_episodic_by_visitor(visitor_instance_id)``。
    2. Filter：仅 ``MemoryStatus.ACTIVE``（ADR-0024：只有可消费记忆进消费）+ 近
       ``lookback_days`` 天（以 ``current_event.occurred_at`` 为截止锚点）。
    3. Rank：确定性排序键（见 ``_rank_key``）。
    4. Cap：取前 ``max_records`` 条。

    ``device_id``：保留参数（ADR-0025 隐私边界 / DESIGN §3.1）。v1 的
    ``EpisodicRecord`` 无 ``device_id`` 字段，故该排序键在 v1 物理上不可实现；保留为
    未来检索策略（如 O1 VectorRetrieval / 同设备优先）扩展点，**当前 no-op**，且永不
    进入 ``ReasoningInput``（C1 隐私边界）。
    """

    def __init__(
        self,
        store: MemoryStore,
        config: RetrievalConfig | None = None,
        device_id: str | None = None,
    ) -> None:
        self._store = store
        self._config = config or RetrievalConfig()
        # 保留参数：v1 记录无 device_id 字段，本实现不使用，仅占位供未来策略。
        self._device_id = device_id

    def retrieve(self, current_event: CurrentEvent) -> list[EpisodicRecord]:
        """召回全部相关历史（Recall → Filter → Rank → Cap，见 ``_retrieve_filtered``）。"""
        return self._retrieve_filtered(current_event)

    def retrieve_by_modality(
        self,
        current_event: CurrentEvent,
        modalities: Iterable[EvidenceModality] | EvidenceModality,
    ) -> list[EpisodicRecord]:
        """按证据模态召回（ADR-0027 D6，如"取所有含 AUDIO 的 episode"）。

        **不是** ``Retrieval`` ABC 的抽象方法（零新组件，D6）：在 ``retrieve`` 的
        Recall → Filter → Rank → Cap 基础上，Filter 阶段额外要求记录 ``modalities``
        与请求模态**相交**（任一命中即保留）。

        语义与 ``retrieve`` 一致：模态过滤先于排序裁剪（先取"窗口内所有含 AUDIO 的
        episode"再按相关性排序 + 上限裁剪），与 ADR「取所有含 AUDIO 的 episode」一致。

        Args:
            current_event: 当前触发事件投影（召回窗口 / 排序锚点，与 ``retrieve`` 一致）。
            modalities: 请求的证据模态（单个或可迭代；空可迭代等价于 ``retrieve`` 全量）。

        Returns:
            按同一确定性排序键（``_rank_key``）排序、上限裁剪后的记录列表（C3）。

        Raises:
            RetrievalError: 召回失败（与 ``retrieve`` 同口径）。
        """
        wanted = (
            {modalities}
            if isinstance(modalities, EvidenceModality)
            else set(modalities)
        )
        return self._retrieve_filtered(current_event, modalities=wanted or None)

    # -- 召回管道（Recall → Filter → Rank → Cap，C3 确定性）-------------------
    def _retrieve_filtered(
        self,
        current_event: CurrentEvent,
        *,
        modalities: set[EvidenceModality] | None = None,
    ) -> list[EpisodicRecord]:
        """统一召回管道；``modalities`` 非空时在 Filter 阶段额外按模态交集过滤。"""
        try:
            raw = self._store.get_episodic_by_visitor(current_event.visitor_instance_id)
        except Exception as exc:  # 转译为分层异常，不静默、不向上抛未分类异常
            raise RetrievalError(
                f"Retrieval 召回失败 visitor={current_event.visitor_instance_id!r}: {exc}"
            ) from exc

        cutoff = current_event.occurred_at - timedelta(days=self._config.lookback_days)
        filtered = [
            r
            for r in raw
            if r.memory_status == MemoryStatus.ACTIVE and r.enter_time >= cutoff
        ]
        if modalities:
            filtered = [
                r for r in filtered if set(r.modalities or []) & modalities
            ]
        ranked = sorted(filtered, key=lambda r: self._rank_key(current_event, r))
        return ranked[: self._config.max_records]

    # -- 确定性排序键（C3）-----------------------------------------------------
    def _rank_key(self, current_event: CurrentEvent, record: EpisodicRecord) -> tuple:
        # ① risk_category_match（heuristic proxy，非语义相似度，DESIGN Errata）：
        #    risk_signal 当前事件 → 命中含 risk_level 的记录；
        #    visitor_event 当前事件 → 命中 risk_level 为 None 的记录。
        #    True 在前（用 0/1 让升序把 True 置前）。
        match = 0 if self._risk_category_match(current_event, record) else 1
        # ② same_time_band：记录 enter hour 与当前事件 hour 环形距离 <= 阈值。
        band = 0 if self._same_time_band(current_event.occurred_at, record.enter_time) else 1
        # ③ recency：enter_time 越近越前（升序用负时间戳）。
        recency = -record.enter_time.timestamp()
        # ④ record_id 升序：最终 tiebreak，保证 C3 完全确定（回放/审计一致）。
        return (match, band, recency, record.record_id)

    @staticmethod
    def _risk_category_match(current_event: CurrentEvent, record: EpisodicRecord) -> bool:
        if current_event.event_type == "risk_signal":
            return record.risk_level is not None
        # visitor_event：无 warning 投影（risk_level 为 None）的记录。
        return record.risk_level is None

    def _same_time_band(self, cur: datetime, rec: datetime) -> bool:
        half = self._config.same_time_band_hours
        d = abs(cur.hour - rec.hour)
        d = min(d, 24 - d)
        return d <= half


__all__ = ["RuleBasedRetrieval"]
