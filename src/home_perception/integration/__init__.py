"""集成层（Integration Layer）。

> 把新模态（音频）的输出翻译为既有 ``RiskSignal`` / ``Evidence`` 契约。本目录**不属于**
> ``analysis/`` 也不属于 ``core/``——与视觉侧 ``VisitorEvent → RiskSignal`` 的适配方式一致，
> 使 ``analysis/`` 对各模态具体实现保持无知（ADR-0019 不侵入原则）。
"""

from .audio_adapter import AudioAdapter, AudioEvidenceCollector, adapt_audio_event

__all__ = ["AudioAdapter", "AudioEvidenceCollector", "adapt_audio_event"]
