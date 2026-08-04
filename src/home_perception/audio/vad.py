"""语音活动检测（VAD）后端（ADR-0026 Tier0 · 零模型分段）。

> 提供两种后端，按可用性自动选择：
> - ``WebRtcVadBackend``：精度高，但 ``webrtcvad`` 在 Windows 无 wheel（编译需 MSVC），
>   仅在能成功 import 的环境（如 Linux CI）启用；Spike 实测 CPU 开销 ~0.0ms/segment。
> - ``EnergyVadBackend``：纯 numpy 能量阈值，**全平台可用**，作为默认兜底。
>
> 设计要点：``webrtcvad`` 为**可选依赖**，绝不在模块顶层 import（否则 torch-free CI 子集无法
> import 本包）。探测在 ``WebRtcVadBackend.__init__`` 内惰性进行，失败时该后端不可用，
> ``AudioDetector`` 自动回退到能量后端。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ..common.logging import get_logger
from .source import LoadedAudio

log = get_logger(__name__)


class VadBackend(ABC):
    """VAD 后端抽象：把整段音频切成「语音段」[(start_sec, end_sec), ...]。"""

    @abstractmethod
    def detect(self, audio: LoadedAudio) -> list[tuple[float, float]]:
        """返回语音段列表，每段为 (start_sec, end_sec)。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名称（用于日志 / 可观测）。"""
        ...


class EnergyVadBackend(VadBackend):
    """能量阈值 VAD（纯 numpy，全平台可用兜底）。

    逐帧计算 RMS，用「绝对地板 + 相对中位数」双阈值判定语音帧；合并连续语音帧为段。
    """

    def __init__(
        self,
        frame_ms: int = 20,
        floor: float = 0.01,
        relative_ratio: float = 0.4,
        min_segment_ms: int = 150,
    ) -> None:
        self.frame_ms = frame_ms
        self.floor = floor
        self.relative_ratio = relative_ratio
        self.min_segment_ms = min_segment_ms

    @property
    def name(self) -> str:
        return "energy"

    def detect(self, audio: LoadedAudio) -> list[tuple[float, float]]:
        sr = audio.sample_rate
        frame_len = max(1, int(sr * self.frame_ms / 1000.0))
        samples = audio.samples
        if len(samples) < frame_len:
            # 太短：整段视为一段（避免空输出）
            return [(0.0, len(samples) / sr)] if len(samples) > 0 else []

        n_frames = len(samples) // frame_len
        if n_frames == 0:
            return [(0.0, len(samples) / sr)]
        frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
        rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)

        median = float(np.median(rms)) if n_frames > 0 else 0.0
        thr = max(self.floor, median * self.relative_ratio)
        speech = rms > thr

        return self._merge(speech, frame_len, sr)

    def _merge(self, speech: np.ndarray, frame_len: int, sr: int) -> list[tuple[float, float]]:
        segments: list[tuple[float, float]] = []
        min_frames = max(1, int(self.min_segment_ms / self.frame_ms))
        in_seg = False
        start = 0
        for i, is_sp in enumerate(speech):
            if is_sp and not in_seg:
                in_seg = True
                start = i
            elif not is_sp and in_seg:
                if (i - start) >= min_frames:
                    segments.append((start * frame_len / sr, i * frame_len / sr))
                in_seg = False
        if in_seg and (len(speech) - start) >= min_frames:
            segments.append((start * frame_len / sr, len(speech) * frame_len / sr))
        return segments


class WebRtcVadBackend(VadBackend):
    """WebRTC VAD（精度高，Linux CI 可用；Windows 无 wheel 时自动不可用）。

    惰性 import ``webrtcvad``；不可用时 ``available`` 为 False，由 ``AudioDetector`` 回退。
    WebRTC 要求 16-bit PCM mono @ 8k/16k/32k，帧长 10/20/30ms；本实现统一重采样到 16k + 30ms。
    """

    def __init__(self, aggressiveness: int = 3) -> None:
        self.aggressiveness = aggressiveness
        self._vad = None
        try:
            import webrtcvad  # type: ignore

            self._vad = webrtcvad.Vad(aggressiveness)
            self._available = True
        except Exception as exc:  # noqa: BLE001  # 后端不可用：记录并降级，不抛
            log.warning("audio.vad.webrtc_unavailable", error=str(exc))
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def name(self) -> str:
        return "webrtc" if self._available else "webrtc(unavailable)"

    def detect(self, audio: LoadedAudio) -> list[tuple[float, float]]:
        if not self._available:
            return []
        target_sr = 16000
        frame_ms = 30
        frame_len = target_sr * frame_ms // 1000  # 480 samples

        # 重采样到 16k（线性插值）
        if audio.sample_rate != target_sr:
            n_target = round(len(audio.samples) * target_sr / audio.sample_rate)
            if n_target < 1:
                return []
            x = np.linspace(0, len(audio.samples) - 1, n_target)
            pcm = np.interp(x, np.arange(len(audio.samples)), audio.samples)
        else:
            pcm = audio.samples

        int16 = np.clip(pcm * 32768.0, -32768, 32767).astype("<i2")
        n_frames = len(int16) // frame_len
        if n_frames == 0:
            return [(0.0, len(int16) / target_sr)] if len(int16) > 0 else []

        speech = np.zeros(n_frames, dtype=bool)
        for i in range(n_frames):
            chunk = int16[i * frame_len : (i + 1) * frame_len].tobytes()
            try:
                speech[i] = bool(self._vad.is_speech(chunk, target_sr))  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001  # 单帧解码异常：保守判为非语音
                speech[i] = False

        segments: list[tuple[float, float]] = []
        in_seg = False
        start = 0
        for i, is_sp in enumerate(speech):
            if is_sp and not in_seg:
                in_seg = True
                start = i
            elif not is_sp and in_seg:
                segments.append((start * frame_ms / 1000.0, i * frame_ms / 1000.0))
                in_seg = False
        if in_seg:
            segments.append((start * frame_ms / 1000.0, n_frames * frame_ms / 1000.0))
        return segments


def select_vad(aggressiveness: int = 3) -> VadBackend:
    """按可用性选择 VAD 后端：优先 WebRTC，缺失回退能量后端。"""
    webrtc = WebRtcVadBackend(aggressiveness=aggressiveness)
    if webrtc.available:
        log.info("audio.vad.selected", backend="webrtc")
        return webrtc
    log.info("audio.vad.selected", backend="energy")
    return EnergyVadBackend()
