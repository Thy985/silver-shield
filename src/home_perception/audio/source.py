"""音频源抽象与实现（ADR-0026 §2 · 与 Visual ``FrameSource`` 同构的解耦边界）。

> **ADR-0026 §2**：``AudioSource`` 与 ``VideoSource`` 是两条独立的传感器链路，互不依赖。
> Phase 3.0 使用 ``FileAudioSource``（测试 / 回放）与 ``LocalMicSource``（本机麦克风）验证闭环，
> 不依赖摄像头是否带音频码流。``RTSPAudioSource``（带音频摄像头）留待设备适配阶段。
>
> 设计约束：``FileAudioSource`` 仅依赖标准库 ``wave`` + ``numpy``（读 WAV），**不引入重解码依赖**，
> 保证音频包可在 torch-free CI 子集下被 import 与测试。MP3 / 其他编码的支持留待设备适配阶段
> （届时由 ``RTSPAudioSource`` 经 PyAV 解码，不污染本基础读取路径）。
"""

from __future__ import annotations

import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..common.logging import get_logger

log = get_logger(__name__)


@dataclass
class LoadedAudio:
    """解码后的音频张量（ mono float32，区间 [-1, 1] ）。"""

    samples: np.ndarray  # shape (n_samples,)，float32
    sample_rate: int


class AudioSource(ABC):
    """音频源抽象接口（与 ``FrameSource`` 同构的 Level 3 Runtime Assembly Contract）。

    Pipeline 仅依赖本抽象，不感知 ``File`` / ``LocalMic`` / ``RTSP`` 差异。
    """

    @abstractmethod
    def load(self) -> LoadedAudio:
        """加载并返回一段音频（mono float32）。"""
        ...


class FileAudioSource(AudioSource):
    """文件音频源（测试 / 回放，Phase 3.0 默认验证入口）。

    仅支持 WAV（标准库 ``wave`` 解码）。Fixture 由 TTS 生成基础设施产出为 WAV 后提交入库
    （详见 ``docs/audio_fixture_generation.md``），因此测试闭环无需网络 / 重解码依赖。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # 不在构造期校验存在性：缺失/损坏统一在 ``load()`` 抛异常，
        # 由管道失败隔离捕获并降级为「无音频事件」，避免构造期异常拖垮主管道。

    def load(self) -> LoadedAudio:
        with wave.open(str(self.path), "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        if sample_width == 1:
            dtype = np.uint8
            data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
            # 8-bit PCM 无符号，居中到 [-1, 1]
            data = (data - 128.0) / 128.0
        elif sample_width == 2:
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif sample_width == 4:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"不支持的采样宽度 {sample_width} 字节（仅支持 8/16/32-bit PCM）")

        # 多声道 → mono（取平均）
        if n_channels > 1:
            data = data.reshape(-1, n_channels).mean(axis=1)

        data = np.clip(data, -1.0, 1.0)
        log.debug("audio.file_loaded", path=str(self.path), frames=n_frames, sr=sample_rate)
        return LoadedAudio(samples=data.astype(np.float32), sample_rate=sample_rate)
