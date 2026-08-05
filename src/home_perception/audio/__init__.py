"""音频感知包（ADR-0026 · Phase 3.0）。

> 本包是 **音频感知链** 的实现，与视觉链 ``detection``/``analysis`` 同构、互不依赖。
> 仅依赖标准库 + numpy（torch-free），可在 CI 契约子集下被 import 与测试。
> ``tts`` 模块属测试基础设施，不在此导出（避免引入 TTS 运行时依赖）。
"""

from .detector import AudioDetector, DetectionResult
from .event import (
    AUDIO_PERCEPTION_KIND_VALUES,
    FORBIDDEN_AUDIO_FIELDS,
    AudioPerceptionEvent,
    AudioPerceptionKind,
    AudioSegmentEvent,
    new_event_id,
)
from .features import AudioFeatureExtractor, AudioFeatures
from .pipeline import AudioPipeline
from .rule import AudioRule, RuleThresholds
from .source import AudioSource, FileAudioSource, LoadedAudio
from .tagging import (
    YAMNET_SEMANTIC_MAP,
    AcousticTagger,
    AudioTag,
    StubAcousticTagger,
    YamNetTagger,
    build_tagger,
)
from .vad import EnergyVadBackend, VadBackend, WebRtcVadBackend, select_vad

__all__ = [
    "AUDIO_PERCEPTION_KIND_VALUES",
    "FORBIDDEN_AUDIO_FIELDS",
    "YAMNET_SEMANTIC_MAP",
    "AcousticTagger",
    "AudioDetector",
    "AudioFeatureExtractor",
    "AudioFeatures",
    "AudioPerceptionEvent",
    "AudioPerceptionKind",
    "AudioPipeline",
    "AudioRule",
    "AudioSegmentEvent",
    "AudioSource",
    "AudioTag",
    "DetectionResult",
    "EnergyVadBackend",
    "FileAudioSource",
    "LoadedAudio",
    "RuleThresholds",
    "StubAcousticTagger",
    "VadBackend",
    "WebRtcVadBackend",
    "YamNetTagger",
    "build_tagger",
    "new_event_id",
    "select_vad",
]
