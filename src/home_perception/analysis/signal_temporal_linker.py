"""Signal 级跨模态时间对齐（ADR-0041 · SignalTemporalLinker）。

> **职责边界（ADR-0041 D5）**：本模块回答「同一时刻的 Vision RiskSignal 与 Audio
> RiskSignal 是否构成时间关联」；episode 级异步关联归 ``CrossModalLinker``
> （ADR-0028）。两者**不共享代码、不共享配置**——瞬时跃迁消息 ≠ 持久化 episode，
> 类型与触发时机均不匹配，禁止复用/伪造。

组成（D1 机制冻结 / D2 窗口数值不冻结）：

- ``EpisodeClock``：runtime 会话时钟锚定（D3）——会话启动时锚定一次
  ``episode_start_unix``，audio Unix 墙钟秒统一换算为 runtime 伪时钟
  ``case_time = unix_ts - episode_start_unix``，与视觉侧
  ``case_time = frame_index × frame_interval_s``（ADR-0039 锚点）进入同一 timeline。
  **会话重启处理 = 重新构造实例**（锚点不可变，保证 VM-8 重放确定性）。
- ``SignalPosition``：RiskSignal + 其在 runtime timeline 上的位置（frame_index /
  case_time）。位置由调用方（产出信号的帧循环 / 评估器）提供，**不污染**
  ADR-0021 冻结的 ``RiskSignal`` 类型。
- ``classify`` / ``link``：纯函数、零状态、可单测。分级判定：

  ========  ==============================  ================
  级别      判定                            说明
  ========  ==============================  ================
  SAME_FRAME  双方 frame_index 非空且相等    强关联，零阈值
  NEAR_WINDOW |Δcase_time| <= window_s       弱关联，窗口可配置
  UNLINKED    以上皆否                       不合并（link 返回 None）
  ========

  fail-safe：任一方缺位置信息（frame_index / case_time 为 None）→ UNLINKED，
  宁可不关联也不猜（窗口计算的前提是 Δt 有意义，见 ADR-0041 动机第 1 条）。

**窗口悬空期语义（D2，有意为之的安全默认）**：``window_s=None`` 时 NEAR_WINDOW
恒不可用（配置项 ``signal_temporal_window_s`` 默认 None）；默认数值由真实
telephone_risk 验收数据的 Δt 分布决定后回填（候选档位 same frame / ≤0.5s /
≤1.0s / ≤2.0s），本组件不预设答案。SAME_FRAME 不受悬空影响。

依赖方向：Temporal Alignment（本模块）在 Evidence Strength（ADR-0042）**之前**
——Q3 是 Q4 的前置件；下游 Evidence Synthesis 以 ``LinkedSignalPair`` 为输入。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .risk_signal import RiskSignal, SourceModality

# ============================================================================
# D3：runtime 会话时钟锚定
# ============================================================================


class EpisodeClock:
    """会话时钟锚点（D3）：audio Unix 墙钟秒 → runtime 伪时钟 case_time。

    - 会话启动时构造一次（``episode_start_unix`` 通常为 ``time.time()``）；
    - **会话重启 = 重新构造实例**（锚点不可变；重启后旧实例丢弃，不回写）；
    - 早于锚点的 audio 时间戳（设备时钟漂移）钳位到 0.0——与
      ``RuntimeFrameContext.case_time >= 0``（ADR-0039）同一非负语义；
    - 换算是确定性的纯算术（减法 + clamp），无隐藏状态、无墙钟读取。
    """

    __slots__ = ("_start",)

    def __init__(self, episode_start_unix: float) -> None:
        if isinstance(episode_start_unix, bool) or not isinstance(
            episode_start_unix, (int, float)
        ):
            raise TypeError(
                "episode_start_unix 必须是数值 Unix 秒，"
                f"收到 {type(episode_start_unix).__name__}"
            )
        self._start = float(episode_start_unix)

    @property
    def episode_start_unix(self) -> float:
        return self._start

    def unix_to_case(self, unix_ts: float) -> float:
        """Unix 墙钟秒 → runtime case_time（负值钳位 0.0，见类 docstring）。"""
        if isinstance(unix_ts, bool) or not isinstance(unix_ts, (int, float)):
            raise TypeError(f"unix_ts 必须是数值，收到 {type(unix_ts).__name__}")
        return max(0.0, float(unix_ts) - self._start)


# ============================================================================
# D1：信号时间位置 + 分级 + 关联产物
# ============================================================================


class LinkLevel(str, Enum):
    """时间关联分级（ADR-0041 D1 表格逐字对应）。"""

    SAME_FRAME = "same_frame"  # 同一 frame_index 内共现（强关联，零阈值）
    NEAR_WINDOW = "near_window"  # |Δcase_time| <= window_s（弱关联，窗口可配置）
    UNLINKED = "unlinked"  # 不合并


@dataclass(frozen=True)
class SignalPosition:
    """RiskSignal 及其在 runtime timeline 上的位置（调用方填充，fail-safe 缺失即 UNLINKED）。

    - ``vision`` 信号：frame_index 与 case_time 均已知（产出帧的 ctx 位置）；
    - ``audio`` 信号：仅 case_time 已知（经 :class:`EpisodeClock` 换算）；
      frame_index 可选（若调用方能可靠反推帧序号则填，否则 None）。
    """

    signal: RiskSignal
    frame_index: int | None = None
    case_time: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.signal, RiskSignal):
            raise TypeError(
                f"signal 必须是 RiskSignal，收到 {type(self.signal).__name__}"
            )
        if self.frame_index is not None and (
            isinstance(self.frame_index, bool)
            or not isinstance(self.frame_index, int)
            or self.frame_index < 0
        ):
            raise ValueError(
                f"frame_index 必须是非负 int 或 None，收到 {self.frame_index!r}"
            )
        if self.case_time is not None:
            if (
                isinstance(self.case_time, bool)
                or not isinstance(self.case_time, (int, float))
                or self.case_time < 0
            ):
                raise ValueError(
                    f"case_time 必须是非负数值或 None，收到 {self.case_time!r}"
                )
            object.__setattr__(self, "case_time", float(self.case_time))


@dataclass(frozen=True)
class LinkedSignalPair:
    """时间关联产物（D1）：Evidence Synthesis（ADR-0019 Phase 1）的决策前输入。

    - ``level``：SAME_FRAME / NEAR_WINDOW（UNLINKED 不产 pair）；
    - ``delta``：|Δcase_time| 秒；SAME_FRAME 且缺 case_time 时记 0.0（同帧零间隔）；
    - ``link_strength``：关联强度 ∈ (0, 1]——SAME_FRAME 恒 1.0；NEAR_WINDOW 为
      线性衰减 ``1 - delta / window_s``（工程实现选择，**公式未冻结**，调参不动架构）。
    """

    vision_signal: RiskSignal
    audio_signal: RiskSignal
    level: LinkLevel
    link_strength: float
    delta: float

    def __post_init__(self) -> None:
        if self.level is LinkLevel.UNLINKED:
            raise ValueError("UNLINKED 不构成关联，不得构造 LinkedSignalPair")
        if not (0.0 < self.link_strength <= 1.0):
            raise ValueError(
                f"link_strength 必须在 (0, 1]，收到 {self.link_strength}"
            )
        if self.delta < 0:
            raise ValueError(f"delta 必须非负，收到 {self.delta}")


def _require_modalities(
    vision: SignalPosition, audio: SignalPosition
) -> None:
    """防御式模态校验（防传反/传错源）。"""
    if vision.signal.source is not SourceModality.VISION:
        raise ValueError(
            f"vision.source 必须是 vision，收到 {vision.signal.source.value}"
        )
    if audio.signal.source is not SourceModality.AUDIO:
        raise ValueError(
            f"audio.source 必须是 audio，收到 {audio.signal.source.value}"
        )


def classify(
    vision: SignalPosition, audio: SignalPosition, *, window_s: float | None
) -> LinkLevel:
    """三态分级判定（D1 表格；纯函数）。

    - SAME_FRAME 优先于 NEAR_WINDOW（同帧共现必然 |Δ| 小，取更强级别）；
    - ``window_s=None``（悬空期）→ NEAR_WINDOW 恒不可用，只剩 SAME_FRAME / UNLINKED；
    - 任一方缺位置信息 → UNLINKED（宁可不关联也不猜）。
    """
    _require_modalities(vision, audio)
    same_frame = (
        vision.frame_index is not None
        and audio.frame_index is not None
        and vision.frame_index == audio.frame_index
    )
    if same_frame:
        return LinkLevel.SAME_FRAME
    in_window = (
        window_s is not None
        and window_s > 0
        and vision.case_time is not None
        and audio.case_time is not None
        and abs(vision.case_time - audio.case_time) <= window_s
    )
    if in_window:
        return LinkLevel.NEAR_WINDOW
    return LinkLevel.UNLINKED


def link(
    vision: SignalPosition, audio: SignalPosition, *, window_s: float | None
) -> LinkedSignalPair | None:
    """判定并构造关联产物（UNLINKED → None；纯函数）。

    ``link_strength``：SAME_FRAME → 1.0；NEAR_WINDOW → ``1 - delta / window_s``
    （线性衰减；窗口边缘趋 0，故 NEAR_WINDOW 判定通过时 strength ∈ (0, 1]）。
    """
    level = classify(vision, audio, window_s=window_s)
    if level is LinkLevel.UNLINKED:
        return None
    if level is LinkLevel.SAME_FRAME:
        if vision.case_time is not None and audio.case_time is not None:
            delta = abs(vision.case_time - audio.case_time)
        else:
            delta = 0.0  # 同帧即视为零间隔（缺 case_time 的调用方仍可得 pair）
        strength = 1.0
    else:  # NEAR_WINDOW（classify 保证 window_s 非 None 且双方 case_time 非 None）
        delta = abs(vision.case_time - audio.case_time)  # type: ignore[operator]
        strength = round(1.0 - delta / window_s, 4)  # type: ignore[operator]
        strength = min(1.0, max(strength, 1e-4))  # 边缘防 0（(0,1] 契约）
    return LinkedSignalPair(
        vision_signal=vision.signal,
        audio_signal=audio.signal,
        level=level,
        link_strength=strength,
        delta=round(delta, 6),
    )

__all__ = [
    "EpisodeClock",
    "LinkLevel",
    "LinkedSignalPair",
    "SignalPosition",
    "classify",
    "link",
]