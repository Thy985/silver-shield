"""实时音频风险评估器（RealTimeAudioRiskEvaluator）— ADR-0042 D5 实现载体。

> **独立组件，不扩展现有视觉评估器**（``RealTimeRiskEvaluator``，ADR-0021）：两者共享
> ``RiskSignal`` 类型，**不共享实例状态、不共享代码路径**。视觉侧状态机键是
> visitor（主体离场兜底 CLEARED）；本组件键是 ``AudioPerceptionKind``——
> **CLEARED 语义 = 同 kind 静默超时**（≠ 主体离场），这是两个限界上下文的本质差异。

判级流水线（单事件 ``observe``）：

```
AudioPerceptionEvent + case_time（ADR-0041 EpisodeClock 换算）
    ↓ ① INSUFFICIENT：score/confidence 低于显式门槛（配置非 None 才检查）
    ↓ ② 候选判级（Evidence Continuity > Event Count，单事件永不升级）：
    │     ESCALATE（须 escalate_enabled 且经 ADR-0041 LinkedSignalPair 验证，D6）
    │     > NOTIFY（窗口内独立 kind 数 ≥ M）
    │     > RAISE（同 kind 窗口计数 ≥ N）
    │     > MONITOR（单次可信信号，仅观察记录）
    ↓ ③ MONITOR ceiling 压制（D4 硬闸门）：全局开关开启或 fallback kind
    │     （AUDIO_ANOMALY_OTHER 兜底类）→ 强度封顶 MONITOR
    ↓ ④ 状态机：strength ≥ RAISE 且该 kind 无活跃 RAISED → emit RiskSignal(RAISED)
    │        （已有 → 去抖不重复 emit）；route_strength 得决策建议
    ↓ EvidenceOutcome（含压制前判级审计字段）
```

**参数悬空期语义（D2/D3，有意为之的安全默认）**：升级维度参数（N/T/M）为 None 时
对应档位结构性不可达——默认配置下一切事件最多 MONITOR（观察记录无害），升级动作
零可达；参数由真实 telephone_risk 验收数据回填后逐档打开（灰度路径）。

**本组件只产 EvidenceOutcome / RiskSignal，不接 DecisionPolicy、不产 Warning**
（守 ADR-0010 单一决策中心）；``routed`` 仅为 modality-aware 决策建议输入。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..audio.event import AudioPerceptionEvent, AudioPerceptionKind
from ..common.logging import get_logger
from .evidence_strength import (
    STRENGTH_ORDER,
    EvidenceStrength,
    route_strength,
)
from .risk_signal import (
    RiskSignal,
    SignalCategory,
    SignalTransition,
    SourceModality,
    SubjectType,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..core.config import AudioEvidenceConfig

log = get_logger(__name__)

# fallback 兜底 kind（YAMNet class_map_path="" 修复前的数据质量标志，D4）：
# 该 kind 事件**恒**封顶 MONITOR——即使全局 ceiling 开关被解除
# （解除开关针对"标签真实性验证通过"的全局状态，兜底类本身永远不是可信标签）。
FALLBACK_AUDIO_KIND: AudioPerceptionKind = AudioPerceptionKind.AUDIO_ANOMALY_OTHER


@dataclass(frozen=True)
class EvidenceOutcome:
    """单事件判级产物（审计面：pre_ceiling_strength 记录压制前判级，可解释可灰度）。

    - ``strength``：ceiling 压制后的**最终**证据强度；
    - ``signal``：仅当状态机 emit RAISED/CLEARED 时非 None（MONITOR/INSUFFICIENT 恒 None）；
    - ``routed``：modality-aware 决策建议 (risk_level, recommended_action)，INSUFFICIENT 为 None；
    - ``reasons``：判级人话（含持续性/多样性证据数字）。
    """

    event_id: str
    kind: AudioPerceptionKind
    strength: EvidenceStrength
    pre_ceiling_strength: EvidenceStrength
    signal: RiskSignal | None
    routed: tuple[str, str] | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if STRENGTH_ORDER[self.strength] > STRENGTH_ORDER[self.pre_ceiling_strength]:
            raise ValueError(
                f"strength({self.strength.value}) 不得高于 pre_ceiling_strength"
                f"({self.pre_ceiling_strength.value})——ceiling 只降不升"
            )


class RealTimeAudioRiskEvaluator:
    """同 kind 会话窗口状态机（D5）：AudioPerceptionEvent 流 → 判级 + RAISED/CLEARED 信号对。

    - 键 = ``AudioPerceptionKind``（同 kind 共享一个窗口与一台状态机）；
    - 多样性维度例外：NOTIFY 判级读取**跨 kind** 全局窗口（per-kind 窗口互相不可见，
      独立声学信号种类数必须跨 kind 统计）；
    - RAISED 去抖：该 kind 已有活跃 RAISED 时不重复 emit（跨事件节流）；
    - CLEARED = 同 kind 静默超时（``clear_timeout_s``，经 ``tick`` 扫描）；
      每个 RAISED 恰好配对一个 CLEARED（``paired_signal_id`` 回填）；
    - 时间轴：调用方经 ``case_time`` 注入 runtime 伪时钟（ADR-0041 EpisodeClock 换算），
      本组件不读墙钟（确定性/可回放）。
    """

    def __init__(
        self,
        *,
        device_id: str,
        config: AudioEvidenceConfig,
        subject_id: str | None = None,
    ) -> None:
        self._device_id = device_id
        self._config = config
        self._subject_id = subject_id or str(uuid4())
        # 同 kind 会话窗口：(case_time, event) 序列（按 raise_window_s 剪枝）
        self._windows: dict[AudioPerceptionKind, deque[tuple[float, AudioPerceptionEvent]]] = {}
        # 跨 kind 全局窗口：NOTIFY 多样性维度的证据源（per-kind 窗口互相不可见，
        # 独立声学信号种类数必须跨 kind 统计；同样按 raise_window_s 剪枝）
        self._recent: deque[tuple[float, AudioPerceptionEvent]] = deque()
        # 活跃 RAISED（未 CLEARED）
        self._active: dict[AudioPerceptionKind, RiskSignal] = {}
        # 各 kind 最近一次出现时间（case_time；CLEARED 扫描依据）
        self._last_seen: dict[AudioPerceptionKind, float] = {}

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def observe(
        self,
        event: AudioPerceptionEvent,
        *,
        case_time: float,
        linked_pair_verified: bool = False,
    ) -> EvidenceOutcome:
        """单事件进给：判级 + 状态机推进（纯同步、无 IO、无墙钟读取）。"""
        if not isinstance(event, AudioPerceptionEvent):
            raise TypeError(
                f"event 必须是 AudioPerceptionEvent，收到 {type(event).__name__}"
            )
        cfg = self._config
        kind = event.kind
        window = self._windows.setdefault(kind, deque())
        window.append((case_time, event))
        self._last_seen[kind] = case_time

        # ① INSUFFICIENT：显式门槛未达（门槛 None = 不设门槛，观察无害）
        reasons: list[str] = []
        if (
            cfg.monitor_score_threshold is not None
            and event.score < cfg.monitor_score_threshold
        ):
            return self._outcome(event, EvidenceStrength.INSUFFICIENT, None, reasons)
        if (
            cfg.monitor_confidence_threshold is not None
            and event.confidence < cfg.monitor_confidence_threshold
        ):
            return self._outcome(event, EvidenceStrength.INSUFFICIENT, None, reasons)

        # ② 候选判级（强 → 弱；单事件永不升级，升级维度参数悬空即不可达）
        self._prune_window(window, case_time)
        # 低分事件（INSUFFICIENT）不进多样性证据窗口（提前 return 已排除）
        self._recent.append((case_time, event))
        self._prune_window(self._recent, case_time)
        candidate, cand_reasons = self._grade(event, window, linked_pair_verified)
        reasons.extend(cand_reasons)

        # ③ D4 MONITOR ceiling（全局开关 or fallback 兜底 kind）
        pre_ceiling = candidate
        ceiling_active = cfg.ceiling_monitor_only or kind is FALLBACK_AUDIO_KIND
        above_monitor = (
            STRENGTH_ORDER[candidate] > STRENGTH_ORDER[EvidenceStrength.MONITOR]
        )
        if ceiling_active and above_monitor:
            reasons.append(
                f"MONITOR ceiling 压制（{candidate.value} → monitor；"
                + (
                    "fallback kind"
                    if kind is FALLBACK_AUDIO_KIND
                    else "class_map 修复 + 标签验证前"
                )
                + "）"
            )
            candidate = EvidenceStrength.MONITOR

        # ④ 状态机：≥ RAISE 且无活跃 RAISED → emit；否则去抖
        signal: RiskSignal | None = None
        if STRENGTH_ORDER[candidate] >= STRENGTH_ORDER[EvidenceStrength.RAISE]:
            if kind not in self._active:
                signal = self._emit_raised(event, case_time, reasons)
                self._active[kind] = signal
            else:
                reasons.append("该 kind 已有活跃 RAISED（去抖，不重复 emit）")

        return self._outcome(event, candidate, signal, reasons, pre_ceiling=pre_ceiling)

    def tick(self, *, now_case_time: float) -> list[RiskSignal]:
        """静默超时扫描：同 kind 静默超过 clear_timeout_s → emit CLEARED（成对契约）。"""
        cleared: list[RiskSignal] = []
        timeout = self._config.clear_timeout_s
        if timeout is None:
            return cleared
        for kind in sorted(self._active, key=lambda k: k.value):
            last = self._last_seen.get(kind)
            if last is None or now_case_time - last <= timeout:
                continue
            raised = self._active.pop(kind)
            cleared.append(
                self._make_signal(
                    kind=kind,
                    transition=SignalTransition.CLEARED,
                    case_time=now_case_time,
                    paired_signal_id=raised.signal_id,
                    features_extra={"clear_reason": "kind_silence_timeout"},
                )
            )
            log.info(
                "audio.evaluator.cleared",
                kind=kind.value,
                paired_signal_id=raised.signal_id,
            )
        return cleared

    @property
    def active_kinds(self) -> tuple[AudioPerceptionKind, ...]:
        """当前有活跃 RAISED 的 kind（只读视图，供审计/展示）。"""
        return tuple(sorted(self._active, key=lambda k: k.value))

    def emit_combined_signal(
        self,
        *,
        kind: AudioPerceptionKind,
        pair: Any,
        case_time: float,
    ) -> RiskSignal:
        """Evidence Synthesis 产物（Gate G · ADR-0019 母体 / ADR-0041 硬前提）：
        Vision+Audio 经时间对齐成立 ``LinkedSignalPair`` → ESCALATE 组合风险信号。

        与单模态 RAISED 的关系：**补充信号，不改写状态机**——原 RAISED 保持活跃
        （其 CLEARED 配对语义不受影响）；本信号是瞬时多模态验证宣告（features 携带
        pair 元数据供 policy/浏览器追溯贡献链），不进入 ``_active`` 去抖账本。

        调用方契约：仅当 ``escalate_enabled=True`` **且** runtime 真实产出
        ``LinkedSignalPair``（ADR-0041 D6 双重门控的第二道在调用方——link 先行，
        EvidenceStrength 判级后行，Q3→Q4 依赖方向）时调用。
        """
        from .signal_temporal_linker import LinkedSignalPair  # 局部导入防环

        if not isinstance(pair, LinkedSignalPair):
            raise TypeError(
                f"pair 必须是 LinkedSignalPair，收到 {type(pair).__name__}"
            )
        signal = self._make_signal(
            kind=kind,
            transition=SignalTransition.RAISED,
            case_time=case_time,
            paired_signal_id=None,
            features_extra={
                "combined_risk": True,
                "linked_pair_level": pair.level.value,
                "link_strength": pair.link_strength,
                "linked_delta_s": pair.delta,
                "vision_signal_id": pair.vision_signal.signal_id,
                "paired_audio_signal_id": pair.audio_signal.signal_id,
                "escalate_route": "HIGH/ESCALATE_COMMUNITY (ADR-0042 候选路由)",
            },
        )
        log.info(
            "audio.evaluator.combined_raised",
            kind=kind.value,
            signal_id=signal.signal_id,
            linked_pair_level=pair.level.value,
            link_strength=pair.link_strength,
        )
        return signal

    # ------------------------------------------------------------------
    # 内部：判级 / 剪枝 / 信号构造
    # ------------------------------------------------------------------

    def _grade(
        self,
        event: AudioPerceptionEvent,
        window: deque[tuple[float, AudioPerceptionEvent]],
        linked_pair_verified: bool,
    ) -> tuple[EvidenceStrength, list[str]]:
        """候选判级（强 → 弱；参数悬空 = 该档结构性不可达）。"""
        cfg = self._config
        reasons: list[str] = []
        # ESCALATE（D6 反幻觉：必须经 ADR-0041 LinkedSignalPair 验证 + 显式开关）
        if cfg.escalate_enabled:
            if linked_pair_verified:
                return EvidenceStrength.ESCALATE, ["多模态验证链成立（LinkedSignalPair）"]
            reasons.append("escalate 已启用但缺 LinkedSignalPair 验证（D6 反幻觉）")
        # NOTIFY：跨 kind 全局窗口内独立 kind 数 ≥ M（多样性维度）
        if cfg.notify_min_kinds is not None:
            distinct = self._distinct_kinds_in_window(self._recent)
            if distinct >= cfg.notify_min_kinds:
                return (
                    EvidenceStrength.NOTIFY,
                    [f"窗口内独立声学信号 {distinct} 类 ≥ {cfg.notify_min_kinds}"],
                )
        # RAISE：同 kind 窗口计数 ≥ N（持续性维度）
        if cfg.raise_min_count is not None:
            if len(window) >= cfg.raise_min_count:
                reasons.append(
                    f"同 kind 窗口计数 {len(window)} ≥ {cfg.raise_min_count}（持续性成立）"
                )
                return EvidenceStrength.RAISE, reasons
            reasons.append(
                f"同 kind 窗口计数 {len(window)} < {cfg.raise_min_count}（未达持续性）"
            )
        # 默认：单次可信信号 → 仅观察
        reasons.append("单次可信信号（仅观察记录）")
        return EvidenceStrength.MONITOR, reasons

    def _prune_window(
        self,
        window: deque[tuple[float, AudioPerceptionEvent]],
        now_case_time: float,
    ) -> None:
        span = self._config.raise_window_s
        if span is None:
            return
        while window and now_case_time - window[0][0] > span:
            window.popleft()

    def _distinct_kinds_in_window(
        self, window: deque[tuple[float, AudioPerceptionEvent]]
    ) -> int:
        return len({ev.kind for _, ev in window})

    def _emit_raised(
        self,
        event: AudioPerceptionEvent,
        case_time: float,
        reasons: list[str],
    ) -> RiskSignal:
        window = self._windows.get(event.kind, deque())
        span = (
            round(case_time - window[0][0], 3)
            if window
            else 0.0
        )
        signal = self._make_signal(
            kind=event.kind,
            transition=SignalTransition.RAISED,
            case_time=case_time,
            paired_signal_id=None,
            event=event,
            features_extra={
                "window_count": len(window),
                "window_span_s": span,
            },
        )
        log.info(
            "audio.evaluator.raised",
            kind=event.kind.value,
            signal_id=signal.signal_id,
            window_count=len(window),
        )
        return signal

    def _make_signal(
        self,
        *,
        kind: AudioPerceptionKind,
        transition: SignalTransition,
        case_time: float,
        paired_signal_id: str | None,
        event: AudioPerceptionEvent | None = None,
        features_extra: dict[str, Any] | None = None,
    ) -> RiskSignal:
        # features 与 adapt_audio_event（ADR-0038 已验证翻译器）同构，另加持续性证据键
        features: dict[str, Any] = {
            "audio_kind": kind.value,
            "audio_case_time": round(case_time, 3),
        }
        if event is not None:
            max_tier1 = max((t.score for t in event.scored_labels), default=0.0)
            features.update(
                {
                    "audio_score": round(event.score, 4),
                    "audio_confidence": round(event.confidence, 4),
                    "labels": list(event.labels),
                    "source_segment_ids": list(event.source_segment_ids),
                    "audio_tier1_max_score": round(float(max_tier1), 4),
                }
            )
        if features_extra:
            features.update(features_extra)
        # created_at：调用方时间轴上的确定性值不可得（case_time 是伪时钟秒），
        # 沿用 RiskSignal 契约的 UTC 要求 → 由事件墙钟或当前 UTC 提供（仅作元数据，
        # 不参与窗口计算——窗口全部基于 case_time）
        created = event.created_at if event is not None else _utc_now()
        return RiskSignal(
            signal_id=str(uuid4()),
            subject_type=SubjectType.VISITOR,
            subject_id=self._subject_id,
            category=SignalCategory.COMMUNICATION,
            source=SourceModality.AUDIO,
            transition=transition,
            features=features,
            paired_signal_id=paired_signal_id,
            severity_hint=event.score if event is not None else None,
            created_at=created,
        )

    def _outcome(
        self,
        event: AudioPerceptionEvent,
        strength: EvidenceStrength,
        signal: RiskSignal | None,
        reasons: list[str],
        *,
        pre_ceiling: EvidenceStrength | None = None,
    ) -> EvidenceOutcome:
        routed = route_strength(strength, ceiling_monitor_only=self._config.ceiling_monitor_only)
        return EvidenceOutcome(
            event_id=event.event_id,
            kind=event.kind,
            strength=strength,
            pre_ceiling_strength=pre_ceiling or strength,
            signal=signal,
            routed=routed,
            reasons=tuple(reasons),
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)