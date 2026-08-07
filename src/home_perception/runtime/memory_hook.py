"""Memory 在 runtime 中的接入点（Memory Hook）。

把 ``PerceptionPipeline`` 内联的 Episodic Memory 影子写入逻辑（ADR-0024 Slice 5 /
Stage F）抽取为独立、可单测的 ``MemoryHook``（Integration Closure **Slice A**）。

设计契约（见 ``docs/DESIGN-memory-integration-closure.md`` §4 Slice A）：

- **门控**：仅 ``enabled``（即 ``episodic_shadow``）为真时调用 ``record``；
- **输入**：``VisitorEvent`` + 其关联 ``warnings`` + ``actions``；
- **输出（副作用，经共享 ``metrics``）**：``metrics.episodes_recorded`` /
  ``metrics.errors``；
- **失败隔离**：投影 / 落库异常只计 ``errors`` + 日志，绝不中断主风险链路
  （AGENTS.md §2.5）；
- **0 行为变化**：与抽出前的 ``_record_episode`` 逐字段一致，仅结构从内联改为
  Hook 对象；``MemoryPolicy.project_episode`` / ``EpisodicRecord`` /
  ``VisitorEvent`` 签名一律不动。
"""

from __future__ import annotations

from typing import Any

from ..analysis.event import VisitorEvent
from ..analysis.warning import WarningEvent
from ..common.logging import get_logger
from ..core.event import EvidenceItem
from ..memory import DefaultEpisodeBuilder, InvariantViolationError, MemoryStore
from .observability import PipelineMetrics

log = get_logger(__name__)


class MemoryHook:
    """Episodic Memory 影子写入的接线点（Memory 子系统 ↔ 主链路）。

    仅做"投影 + 落库"，不接决策、不产 Warning；开启影子开关不改变任何历史行为。
    """

    def __init__(
        self,
        episode_builder: DefaultEpisodeBuilder | None,
        memory_store: MemoryStore | None,
        enabled: bool,
        metrics: PipelineMetrics,
        cross_modal_runtime: Any | None = None,
    ) -> None:
        self._episode_builder = episode_builder
        self._memory_store = memory_store
        self._enabled = bool(enabled)
        self._metrics = metrics
        # ADR-0028 D4：跨模态关联运行时（可选注入；None = 不触发，零行为变化）。
        # 落库成功后触发建边；runtime 自身失败仅日志，不计入 metrics.errors。
        self._cross_modal_runtime = cross_modal_runtime

    @property
    def enabled(self) -> bool:
        """运行期门控：``episodic_shadow`` 是否激活。"""
        return self._enabled

    def record(
        self,
        ev: VisitorEvent | None,
        warnings: list[WarningEvent],
        actions: list[Any],
        *,
        evidence: list[EvidenceItem] | None = None,
        audio_session_id: str | None = None,
        device_id: str | None = None,
    ) -> None:
        """把一次访客离场（或纯音频会话结束）投影为 EpisodicRecord 并写入 MemoryStore。

        触发时机：
        - 视觉路径：``process_frame`` 中每个 ``VisitorEvent`` 产出后立即调用
          （含其关联的 ``warnings`` / ``actions``）；
        - 纯音频路径（ADR-0027 D4，经 ``AudioSessionRecorder``）：``ev=None`` +
          非空 ``audio_session_id`` + 音频 ``evidence``，投影为匿名音频 episode
          （``visitor_instance_id=None``，绝不反填访客）。

        ``device_id``（ADR-0028 D1）：部署源标识（如 ``home_entry_01``），透传至
        ``EpisodicRecord.device_id`` 供跨模态同设备关联；None 表示未知。

        影子写入**只记录、不接决策、不产 Warning**，因此开启 ``episodic_shadow``
        不会改变流水线任何历史行为（工程方案 §8.3 合入门）。

        容错（AGENTS.md §2.5：记忆写入失败不崩溃主链路）：
        - 投影异常 / 落库未知异常 → 计 ``errors`` + 记日志，跳过本 episode；
        - ``InvariantViolationError``（I2 单调性：字段冲突）→ 防御性告警，不计入 errors；
        - 落库成功后触发 ``cross_modal_runtime.on_episode_recorded``（ADR-0028 D4，
          可选注入；其自身失败仅日志，不计入 errors——errors 属落库通道契约）。
        """
        if self._episode_builder is None or self._memory_store is None:
            return
        try:
            record = self._episode_builder.project_episode(
                ev,
                warnings,
                actions,
                evidence=evidence or [],
                audio_session_id=audio_session_id,
                device_id=device_id,
            )
        except Exception:  # 投影失败（理论上 DefaultEpisodeBuilder 为纯函数不应抛）
            self._metrics.errors += 1
            log.exception(
                "pipeline.episode_build_failed",
                event_id=getattr(ev, "event_id", None),
            )
            return
        if record is None:
            return
        try:
            self._memory_store.upsert_episodic(record)
        except InvariantViolationError as exc:
            # I2 单调保护：字段冲突属防御性告警，不计入 errors（不崩溃流水线）
            log.warning("pipeline.episode_invariant_violation", error=str(exc))
            return
        except Exception:
            self._metrics.errors += 1
            log.exception("pipeline.episode_store_failed", record_id=record.record_id)
            return
        self._metrics.episodes_recorded += 1
        # —— ADR-0028 D4：落库成功后触发跨模态建边（可选注入，失败仅日志）——
        if self._cross_modal_runtime is not None:
            try:
                self._cross_modal_runtime.on_episode_recorded(record)
            except Exception:  # 建边失败仅日志，不计 errors（D4 review）
                log.exception(
                    "pipeline.cross_modal_link_failed", record_id=record.record_id
                )
