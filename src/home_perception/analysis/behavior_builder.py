"""行为状态构建器（BehaviorBuilder）— ADR-0021 State Layer 装配（Migration Stage B）。

> **Stage B 边界**：把 ``tracker.active()`` 的 ``VisitorTrack`` 投影成 ``BehaviorState``
> 纯实时快照，挂入 ``FrameResult.behavior_states`` 供调试观察。**不产信号**（Stage C 才做）。

**职责（工程方案 §2.2）**：
- 纯函数 ``build(tracks, now) -> List[BehaviorState]``：无内部状态、无账本。
- 通过持有 ``event_builder`` 引用查询 ``visitor_instance_id``（UUID，会话级稳定主键），
  而非会复用的 ``track_id``（防串号，见 ADR-0006 / 工程方案 §4.1）。

**为何持有 event_builder 而非自维护映射**：
- ``visitor_instance_id`` 的唯一分配者是 ``VisitorEventBuilder``（``visitor_id_for`` public 方法）；
- BehaviorBuilder 若自维护映射会与 event_builder 漂移（同一 track_id 两个 UUID）；
- 持有引用只读查询，不写入——保持纯函数语义（无内部可变状态）。

**时间约定（ADR-0021 §3.2）**：
- 时刻一律 ``datetime``（UTC）；时长 ``float`` 秒。
- ``dwell_seconds = (now - enter_time).total_seconds()``，二者均 datetime。
"""
from __future__ import annotations

from typing import List

from ..common.logging import get_logger
from ..common.timeutil import require_utc
from ..detection.schemas import VisitorTrack
from .behavior_state import BehaviorPhase, BehaviorState, compute_is_odd_hour
from .event_builder import VisitorEventBuilder

log = get_logger(__name__)


class BehaviorBuilder:
    """VisitorTrack + now → BehaviorState（纯函数，无内部账本）。

    用法（由 ``PerceptionPipeline.process_frame`` 在实时旁路块调用）::

        states = behavior_builder.build(tracker.active(), now)
        # states: List[BehaviorState]，每帧重算的纯实时快照

    注意：必须在 ``event_builder.update(dets)`` 之后调用——``visitor_instance_id``
    由 event_builder 在 ``update`` 时分配，未分配时 ``visitor_id_for`` 返回 None。
    """

    def __init__(self, event_builder: VisitorEventBuilder) -> None:
        if event_builder is None:
            raise ValueError("event_builder 不能为空")
        self._event_builder = event_builder

    def build(self, tracks: List[VisitorTrack], now) -> List[BehaviorState]:
        """把当前在场 VisitorTrack 列表投影成 BehaviorState 快照。

        参数：
        - ``tracks``：``tracker.active()`` 返回的在场访客列表（status=active）
        - ``now``：当前时刻（datetime UTC，与 pipeline 同源 now_provider）

        返回：``List[BehaviorState]``，每个 active track 对应一条；phase=ONGOING。

        跳过条件（不产出、记 warning）：
        - track 的 ``enter_time`` 为 None（理论不会，防御）；
        - ``event_builder.visitor_id_for(track_id)`` 返回 None（track 未被 event_builder 见过）。
        """
        require_utc(now, "now")

        states: List[BehaviorState] = []
        for vt in tracks:
            if vt.enter_time is None:
                # 防御：active track 的 enter_time 应已被 tracker 回填
                log.warning(
                    "behavior_builder.skip_missing_enter_time",
                    track_id=vt.track_id,
                )
                continue

            visitor_uuid = self._event_builder.visitor_id_for(vt.track_id)
            if visitor_uuid is None:
                # event_builder 尚未为此 track 分配 UUID（时序异常：应先 event_builder.update）
                log.warning(
                    "behavior_builder.skip_missing_visitor_id",
                    track_id=vt.track_id,
                )
                continue

            dwell_seconds = (now - vt.enter_time).total_seconds()
            # dwell 理论 >= 0（now >= enter_time）；防御负值（时钟回拨等）。
            # 时钟回拨时同时把 last_seen 拉到 enter_time，满足 BehaviorState 契约不变式
            # （last_seen >= first_seen）；is_odd_hour 仍按注入 now 判定（now 反映"当前"时刻）。
            if dwell_seconds < 0:
                dwell_seconds = 0.0
                effective_last_seen = vt.enter_time
            else:
                effective_last_seen = now

            states.append(
                BehaviorState(
                    track_id=vt.track_id,
                    visitor_instance_id=str(visitor_uuid),
                    phase=BehaviorPhase.ONGOING,
                    first_seen=vt.enter_time,
                    last_seen=effective_last_seen,
                    dwell_seconds=dwell_seconds,
                    is_odd_hour=compute_is_odd_hour(now),
                    proximity_score=0.0,  # Stage B 占位，不参与判定（工程方案附录 O1）
                )
            )
        return states
