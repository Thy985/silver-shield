"""信号适配器（SignalAdapter）— ADR-0021 Signal → Perception 翻译（Migration Stage C）。

> **ADR-0021 = 实时风险状态流与信号生成层。** 本模块把 ``RiskSignal`` 翻译为既有冻结的
> ``PerceptionEvent``（5 类 ``EventType``，零新增枚举），让实时信号能复用历史决策 / 行动链路。
>
> **Stage C 边界（Shadow Mode）**：本模块**已实现但未接入决策路径**。Stage C 的 pipeline
> 只产 ``RiskSignal`` 进 ``FrameResult.risk_signals`` 供观察；Stage D 才调用本适配器
> 把 RAISED 信号汇入 ``DecisionEngine`` 产 ``WarningEvent``。

**翻译规则（工程方案 §3.1 步骤 4）**：
- ``RAISED`` 信号 → ``PerceptionEvent``（按 ``features`` 中的主导证据映射 ``EventType``）：
  - ``dwell_seconds >= threshold`` → ``abnormal_dwell``
  - ``visits_in_window >= threshold`` → ``repeat_visit``
  - ``is_odd_hour == True`` → ``visit_pending_verify``（异常时段来访待核验）
  - 多条件同时满足时按优先级：dwell > visits > odd_hour
- ``CLEARED`` 信号 → ``None``（不产出 PerceptionEvent；CLEARED 仅随 FrameResult 供展示层熄灭风险卡）

**模块边界铁律**：
- 翻译产物过 ``PerceptionEvent`` 的 schema 校验（5 类 ``EventType`` 之一，score ∈ [0,1]）；
- 黑名单字段（fraud/suspect/verdict 等）结构性拒绝（``PerceptionEvent.__post_init__`` 已守，
  本适配器不再额外校验，但 docstring 明示此约束）；
- **ADR-0040 硬门控 3**：``signal.source is SourceModality.AUDIO`` 时**直接返回 None**，
  禁止 audio RiskSignal 翻译为视觉 event_type（详见函数实现注释）。
"""

from __future__ import annotations

from uuid import UUID

from .perception import PerceptionEvent
from .risk_signal import RiskSignal, SignalTransition, SourceModality


def risk_signal_to_perception(
    signal: RiskSignal,
    device_id: str,
    location: str | None = None,
) -> PerceptionEvent | None:
    """把 RiskSignal 翻译为 PerceptionEvent（RAISED → 事件；CLEARED → None）。

    参数：
    - ``signal``：实时评估器产出的 RiskSignal
    - ``device_id``：设备 ID（透传到 PerceptionEvent.device_id）
    - ``location``：可选安装区域描述（透传到 PerceptionEvent.location）

    返回：
    - ``RAISED`` 信号 → 对应 ``PerceptionEvent``（按 features 主导证据映射 EventType）
    - ``CLEARED`` 信号 → ``None``（不产出；CLEARED 仅供展示层熄灭风险卡）

    翻译优先级（多条件同时满足时）：
    1. dwell 超阈 → ``abnormal_dwell``
    2. visits_in_window 超阈 → ``repeat_visit``
    3. is_odd_hour → ``visit_pending_verify``

    score 取信号触发强度（基于 features 中的阈值比例，clamp [0,1]）；
    若无法计算则回退 0.5（中性强度，交决策层综合判断）。
    """
    if not isinstance(signal, RiskSignal):
        raise TypeError(f"signal 必须是 RiskSignal，收到 {type(signal).__name__}")

    # CLEARED 不产出 PerceptionEvent
    if signal.transition is SignalTransition.CLEARED:
        return None

    # ADR-0040 硬门控 3：禁止 audio RiskSignal 翻译为视觉 event_type。
    # audio features 不含 dwell/visits/odd_hour 等视觉语义，_map_features_to_event
    # 必落幻觉兜底 ``visit_pending_verify`` —— 把音频证据伪装成视觉事件。
    # audio 信号应在 audio 层处置（evidence 采集 + 纯音频 episode 落库 + CrossModalLinker
    # 建边），绝**不**进入视觉决策链冒充电影觉事件。
    if signal.source is SourceModality.AUDIO:
        return None

    # RAISED：按 features 主导证据映射 EventType
    features = signal.features if isinstance(signal.features, dict) else {}
    event_type, score = _map_features_to_event(features)

    # visitor_id 从 subject_id（Phase 1 恒 == visitor_instance_id UUID）派生
    try:
        visitor_uuid = UUID(signal.subject_id)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"signal.subject_id 必须是合法 UUID 字符串（Phase 1 恒 == visitor_instance_id），"
            f"收到 {signal.subject_id!r}"
        ) from exc

    return PerceptionEvent(
        device_id=device_id,
        event_type=event_type,
        score=score,
        visitor_id=visitor_uuid,
        source_video=device_id,  # Demo 模式下 source_video == device_id（与历史路径一致）
        timestamp=signal.created_at.timestamp(),
        track_id=signal.track_id,
        bbox=None,  # 实时信号不带 bbox（Stage B 占位，未来 Stage D 可补）
        location=location,
        repeat_count=_extract_int(features, "visits_in_window"),
        is_odd_hour=bool(features.get("is_odd_hour", False)),
        evidence=[],  # Shadow Mode 不带证据引用
        meta={
            "rule": "RealTimeRiskEvaluator",  # §7.2 必填：触发的 Rule 名称
            "signal_id": signal.signal_id,
            "signal_transition": signal.transition.value,
            "signal_category": signal.category.value,
            "source_modality": signal.source.value,
            "realtime": True,  # 标识来自实时路径（审计用）
        },
        created_at=signal.created_at,
    )


# ============================================================================
# 内部：features → EventType 映射
# ============================================================================


def _map_features_to_event(features: dict) -> tuple[str, float]:
    """按 features 主导证据映射 EventType + 计算 score。

    优先级：dwell > visits > odd_hour（与 HighRiskApproachRule 组合权重一致）。
    score 基于阈值比例（如 dwell_seconds / threshold），clamp [0,1]；
    无阈值信息时回退 0.5（中性强度）。
    """
    thresholds = (
        features.get("thresholds", {}) if isinstance(features.get("thresholds"), dict) else {}
    )
    dwell_threshold = thresholds.get("long_duration_seconds")
    visits_threshold = thresholds.get("repeat_visit_count")

    dwell_seconds = features.get("dwell_seconds", 0.0)
    visits_in_window = features.get("visits_in_window", 0)
    is_odd_hour = features.get("is_odd_hour", False)

    # 1) dwell 超阈 → abnormal_dwell
    if (
        isinstance(dwell_threshold, (int, float))
        and isinstance(dwell_seconds, (int, float))
        and dwell_threshold > 0
        and dwell_seconds >= dwell_threshold
    ):
        ratio = dwell_seconds / dwell_threshold
        score = min(1.0, max(0.0, ratio * 0.5))  # ratio=1 → 0.5, ratio=2 → 1.0
        return "abnormal_dwell", round(score, 4)

    # 2) visits_in_window 超阈 → repeat_visit
    if (
        isinstance(visits_threshold, (int, float))
        and isinstance(visits_in_window, (int, float))
        and visits_threshold > 0
        and visits_in_window >= visits_threshold
    ):
        ratio = visits_in_window / visits_threshold
        score = min(1.0, max(0.0, ratio * 0.5))
        return "repeat_visit", round(score, 4)

    # 3) is_odd_hour → visit_pending_verify
    if is_odd_hour:
        return "visit_pending_verify", 0.3  # 异常时段待核验，固定低强度

    # 兜底（理论不会到这：RAISED 必有触发证据）
    return "visit_pending_verify", 0.5


def _extract_int(features: dict, key: str) -> int | None:
    """从 features 提取 int 值（防 bool/str 误传）。"""
    v = features.get(key)
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None
