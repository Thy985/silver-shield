"""音频会话收割器（AudioSessionRecorder）—— 第二模态 → Memory 闭环的运行时接线。

把 ADR-0026 Phase 3.0 的音频感知产物真正接进既有 Memory 链路：一段已完成音频
（会话）的 ``AudioPerceptionEvent`` 列表 → 既有决策链 → 纯音频 ``EpisodicRecord``
落库。**只编排既有组件，零新增模型 / 契约**：

::

    AudioPerceptionEvent ──AudioAdapter.adapt_audio_event──▶ RiskSignal(source=AUDIO)
        ──signal_adapter.risk_signal_to_perception──▶ PerceptionEvent
        ──DecisionEngine.evaluate──▶ WarningEvent（可为 None）
        ──ActionExecutor.execute──▶ ActionCommand[]
        ──MemoryHook.record(ev=None, warnings, actions, evidence, audio_session_id)──▶
            EpisodicRecord(visitor_instance_id=None, modalities=[AUDIO], D4 匿名)

**硬约束（ADR-0027 D3 基石，本组件必须守）**：

- **不新增 Audio Memory 写入链**：音频仍只经
  ``RiskSignal → DecisionPolicy → WarningEvent → EpisodeBuilder`` 入 Memory——
  本组件不做「音频 → 直接写 Memory」的旁路；
- **决策门槛**：只有经 ``DecisionEngine`` 确认产出 ``WarningEvent`` 的会话才落库
  （``warning is None`` → 不记录）。Memory 记录的是「系统已确认的风险事件」，
  不是感知模型的原始打分；
- **D4 匿名**：纯音频 episode ``visitor_instance_id=None``（调用方经
  ``MemoryHook.record(ev=None, ...)`` 传 None），绝不把决策链路的 subject UUID
  反填进 episode；
- **C2 只读 / 失败隔离**：收割失败只返回空摘要 + 日志，绝不抛未分类异常拖垮调用方；
- **确定性**：同输入 events（+ 固定 ``audio_session_id`` / 固定 id 工厂）两次收割，
  摘要字段级一致（C3）。

运行时装配：``PerceptionPipeline.from_settings`` 在 ``audio.enabled`` +
Memory 影子激活时构造本组件，暴露 ``PerceptionPipeline.process_audio_session``
入口（独立 Audio Loop 设计，ADR-0026 §8：音频不随视频帧同步调用）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from uuid import uuid4

from ..action.command import ActionCommand
from ..action.executor import ActionExecutor
from ..analysis.decision_engine import DecisionEngine
from ..analysis.perception import PerceptionEvent
from ..analysis.risk_signal import SignalTransition
from ..analysis.signal_adapter import risk_signal_to_perception
from ..analysis.warning import WarningEvent
from ..audio.event import AudioPerceptionEvent
from ..common.logging import get_logger
from ..core.event import EvidenceItem
from ..integration.audio_adapter import (
    AudioEvidenceCollector,
    adapt_audio_event,
)
from .memory_hook import MemoryHook

log = get_logger(__name__)


def _default_session_id() -> str:
    """默认会话 ID 工厂：uuid 短前缀保证唯一（D4：音频原生身份）。"""
    return f"audio_session_{uuid4().hex[:12]}"


@dataclass(frozen=True)
class AudioSessionSummary:
    """一次音频会话收割摘要（**非分数、非决策**，可审计）。

    - ``session_id``：会话身份（``audio_session_id``，D4 音频原生身份）
    - ``n_events``：会话内音频感知事件数
    - ``warning_ids``：经决策确认的 WarningEvent id（空 = 未过决策门槛）
    - ``evidence_ids``：本次会话产出的音频证据 id（供 I4 溯源）
    - ``episode_recorded``：是否已落库纯音频 EpisodicRecord（D3 门槛后恒为真，
      无 Memory 后端时为 False）
    """

    session_id: str
    n_events: int
    warning_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    episode_recorded: bool


class AudioSessionRecorder:
    """音频会话收割器：会话结束 → 决策门槛 → 纯音频 EpisodicRecord 落库。

    组件依赖全部注入（与 ``PerceptionPipeline`` 装配模式一致）：``DecisionEngine`` /
    ``ActionExecutor`` / ``MemoryHook`` 复用既有实例；``AudioEvidenceCollector`` /
    会话 ID 工厂可替换（默认实现确定性见 ``_default_session_id``）。
    """

    def __init__(
        self,
        decision_engine: DecisionEngine,
        executor: ActionExecutor,
        memory_hook: MemoryHook | None,
        *,
        device_id: str = "home_entry_01",
        evidence_collector: AudioEvidenceCollector | None = None,
        session_id_factory: Callable[[], str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._decision_engine = decision_engine
        self._executor = executor
        self._memory_hook = memory_hook
        self._device_id = device_id
        self._evidence_collector = evidence_collector or AudioEvidenceCollector()
        self._session_id_factory = session_id_factory or _default_session_id
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        """运行期门控（``audio.enabled``，默认关闭）。"""
        return self._enabled

    def record_session(
        self,
        events: Iterable[AudioPerceptionEvent],
        *,
        audio_session_id: str | None = None,
        source_path: str | None = None,
    ) -> AudioSessionSummary:
        """收割一次音频会话（会话结束时机由调用方决定，如一段音频文件跑完）。

        Args:
            events: 会话内的音频感知事件（可空）。
            audio_session_id: 会话身份；缺省由 ``session_id_factory`` 生成
                （测试可注入固定值保证确定性）。**显式注入时须为安全标识符**
                （仅 ``[A-Za-z0-9_-]``）——它直接进入 ``record_id = f"ep-{...}"``，
                含路径/分隔符等危险字符会污染存储键（fail loud，不静默放行）。
            source_path: 可选音频源路径，透传给证据 ``uri``（原片不上传，ADR-0002 §3.3）。

        Returns:
            ``AudioSessionSummary``（非分数、非决策）。字段语义：
            - ``n_events`` = **输入**音频感知事件数（非证据数）；
            - ``evidence_ids`` = **成功采集**的证据数（单事件采集失败会被跳过，
              可能少于 ``n_events``）；
            - ``warning_ids`` = 经决策确认的 WarningEvent id（空 = 未过 D3 门槛）。

        Raises:
            ValueError: ``audio_session_id`` 显式注入且含非安全字符。
            不抛其他未分类异常：任何阶段失败降级为「空警告、不落库」并记日志（C2）。
        """
        if not self._enabled:
            # 开关关闭：不生成 session_id（零资源消耗）——与「业务空会话」刻意区分
            return self._empty_summary("")
        events = list(events)
        session_id = audio_session_id or self._session_id_factory()
        if audio_session_id is not None:
            self._validate_session_id(session_id)
        if not events:
            # 业务空会话：session_id 已生成（可审计「有一次空收割尝试」）
            return self._empty_summary(session_id)

        # 1) 证据收集（每事件一个 AUDIO EvidenceItem；audio_kind 语义写入 metadata，
        #    供 ADR-0027 D6 Consumer 解析 audio_patterns）
        evidence: list[EvidenceItem] = []
        for ev in events:
            try:
                item = self._evidence_collector.collect_segment(
                    ev, source_path or "", audio_kind=ev.kind.value
                )
                evidence.append(item)
            except Exception as exc:  # noqa: BLE001  # 单事件证据失败：跳过该证据
                log.warning(
                    "audio.recorder.evidence_failed",
                    event_id=getattr(ev, "event_id", None),
                    error=str(exc),
                )
        if not evidence:
            # 输入非空但全部采集失败：与「业务空会话」同形状（n_events=0 表示无有效
            # 证据），但日志区分可观测性（输入数可见），调用方无需区分两类降级。
            log.warning(
                "audio.recorder.evidence_all_failed",
                session_id=session_id,
                n_inputs=len(events),
            )
            return self._empty_summary(session_id)

        # 2) 信号翻译（AudioPerceptionEvent → RiskSignal；D3 门槛的入站契约）
        #    会话级 subject UUID：仅作决策上下文（risk_signal_to_perception 从
        #    subject_id 派生 PerceptionEvent.visitor_id），**绝不写进 episode**
        #    （D4 匿名）。adapt_audio_event 的 ``_looks_uuid`` 兜底会把 subject_id
        #    复制进 ``visitor_instance_id``（便利冗余字段）——这里**显式清空**，
        #    从信号源头杜绝未来 WarningEvent/ActionCommand 传导访客身份。
        subject_id = uuid4()
        signals = []
        for ev in events:
            try:
                sig = adapt_audio_event(
                    ev,
                    self._device_id,
                    subject_id=subject_id,
                )
                sig.visitor_instance_id = None  # D4 匿名：信号层不携带访客身份
                if sig.transition is SignalTransition.RAISED:
                    signals.append(sig)
            except Exception as exc:  # noqa: BLE001  # 单事件翻译失败：跳过
                log.warning(
                    "audio.recorder.adapt_failed",
                    event_id=getattr(ev, "event_id", None),
                    error=str(exc),
                )

        # 3) 感知映射 + 决策（复用既有 DecisionEngine，单一决策中心）
        #    感知映射与决策同层降级：risk_signal_to_perception 可能抛 ValueError
        #    （subject_id 非合法 UUID 等脏信号），单信号失败只跳过该信号，绝不
        #    上抛破坏「不抛未分类异常」契约。
        percs: list[PerceptionEvent] = []
        for sig in signals:
            try:
                perc = risk_signal_to_perception(sig, self._device_id)
            except Exception as exc:  # noqa: BLE001  # 单信号映射失败：跳过
                log.warning(
                    "audio.recorder.perception_failed",
                    session_id=session_id,
                    signal_id=getattr(sig, "signal_id", None),
                    error=str(exc),
                )
                continue
            if perc is not None:
                percs.append(perc)
        warning: WarningEvent | None = None
        actions: list[ActionCommand] = []
        try:
            warning = self._decision_engine.evaluate(percs) if percs else None
            if warning is not None:
                actions = list(self._executor.execute(warning))
        except Exception as exc:  # noqa: BLE001  # 决策失败：降级为不落库（不崩调用方）
            log.warning("audio.recorder.decision_failed", session_id=session_id, error=str(exc))
            warning = None
            actions = []

        # 4) D3 决策门槛：无 WarningEvent → 不落库（Memory 只记「系统已确认的事件」）
        if warning is None:
            log.info(
                "audio.recorder.no_warning_skip",
                session_id=session_id,
                n_events=len(events),
            )
            return AudioSessionSummary(
                session_id=session_id,
                n_events=len(events),
                warning_ids=(),
                evidence_ids=tuple(e.evidence_id for e in evidence),
                episode_recorded=False,
            )

        # 5) 落库：纯音频 episode（ev=None + audio_session_id，D4 匿名）
        #    落库计数由 MemoryHook 内部自增（episodes_recorded），本组件不重复计。
        recorded = False
        if self._memory_hook is not None:
            try:
                self._memory_hook.record(
                    None,
                    [warning],
                    actions,
                    evidence=evidence,
                    audio_session_id=session_id,
                )
                recorded = True
            except Exception as exc:  # noqa: BLE001  # 落库失败：摘要如实报告，不抛
                log.warning(
                    "audio.recorder.store_failed", session_id=session_id, error=str(exc)
                )
        return AudioSessionSummary(
            session_id=session_id,
            n_events=len(events),
            warning_ids=(str(warning.warning_id),),
            evidence_ids=tuple(e.evidence_id for e in evidence),
            episode_recorded=recorded,
        )

    # -- 内部 ---------------------------------------------------------------
    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        """校验显式注入的会话 ID 为安全标识符（fail loud，防 record_id 污染）。

        ``record_id = f"ep-{audio_session_id}"`` 直接进入存储键（episode_builder），
        含路径分隔符 / 冒号 / 引号等危险字符虽过 I1 非空校验，却可能污染存储层。
        仅允许 ``[A-Za-z0-9_-]``（与默认工厂 ``uuid4().hex`` 同安全级别）。
        """
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError(
                f"audio_session_id 必须是安全标识符（仅 [A-Za-z0-9_-]），收到 {session_id!r}"
            )

    def _empty_summary(self, session_id: str) -> AudioSessionSummary:
        return AudioSessionSummary(
            session_id=session_id,
            n_events=0,
            warning_ids=(),
            evidence_ids=(),
            episode_recorded=False,
        )


__all__ = ["AudioSessionRecorder", "AudioSessionSummary", "_default_session_id"]
