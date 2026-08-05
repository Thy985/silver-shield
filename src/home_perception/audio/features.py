"""Tier0 声学特征提取（ADR-0026 §3 Tier 0 · 零模型 Prosody 特征）。

> 提取与「感知种类」直接相关的声学代理指标，供 ``AudioRule`` 映射为 ``AudioPerceptionEvent``。
> 全部基于 numpy，无模型依赖；阈值由 ``AudioRule`` 持有，本模块只算特征。
>
> 代理指标（设计文档 §5 参数矩阵对应）：
> - ``rms``：响度代理 → ``AUDIO_VOICE_RAISED``
> - ``speech_rate``：音节/能量峰率（syllables/sec） → ``AUDIO_SPEECH_RAPID``
> - ``band_energy_ratio``：300~3400Hz 能量占比（电话带宽指纹） → ``AUDIO_TELEPHONE_PERSISTENT``
> - ``f0_mean`` / ``tremor``：基频均值 / 振幅调制深度（哭腔代理） → ``AUDIO_DISTRESS_CRY``
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _autocorr_upto(sig: np.ndarray, max_lag: int) -> np.ndarray:
    """返回自相关的前 ``max_lag + 1`` 个 lag（FFT 法，O(n log n)）。

    为什么不用 ``np.correlate(sig, sig, mode="full")``：它是 O(n²) 且算出全部 2n-1 个
    lag，而基频估计只需要前 ~100 个（8kHz 采样 / 80Hz 下限）。实测在本机上，一段 2 秒
    语音的特征提取要 3.0s、4 秒要 10.8s、8 秒要 27.9s——**比实时还慢**，且超线性恶化，
    无法支撑实时感知管道。改 FFT 法后 2 秒片段的自相关快约 3000 倍（3191ms → 1.04ms），
    且两者数值等价（实测最大相对误差 ~1e-16，纯浮点噪声）。

    零填充到 ``>= n + max_lag`` 以避免循环卷积把尾部绕回污染低 lag。
    """
    n = len(sig)
    n_fft = 1 << (n + max_lag).bit_length()
    spec = np.fft.rfft(sig, n_fft)
    ac = np.fft.irfft(spec * np.conj(spec), n_fft)
    # np.array(..., dtype=np.float64) 同时**强制 copy**：
    # ① 调用方会就地改写（ac[:lo_lag] = -np.inf 屏蔽低 lag），不能写穿原 irfft 输出缓冲；
    # ② 只留前 max_lag+1 个，不必留住整个 n_fft 缓冲。
    return np.array(ac[: max_lag + 1], dtype=np.float64)


@dataclass
class AudioFeatures:
    """一段音频的 Tier0 声学特征。"""

    duration: float
    rms: float
    speech_rate: float  # 音节/能量峰率（per second 近似）
    highband_ratio: float  # >3400Hz 能量占比（宽带标志；窄带/电话≈0）
    f0_mean: float  # 基频均值（Hz），0 表示未检出
    tremor: float  # 振幅调制深度 [0,1]，哭腔代理
    am_rate: float  # 振幅调制速率（Hz，包络自相关峰）→ 急促/抽泣的节奏签名


class AudioFeatureExtractor:
    """Tier0 特征提取器（零模型）。"""

    def __init__(
        self,
        highband_cutoff: float = 3400.0,
        f0_range: tuple[float, float] = (80.0, 500.0),
        envelope_ms: int = 30,
    ) -> None:
        self.highband_cutoff = highband_cutoff
        self.f0_range = f0_range
        self.envelope_ms = envelope_ms

    def extract(self, samples: np.ndarray, sample_rate: int) -> AudioFeatures:
        if len(samples) == 0:
            return AudioFeatures(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        duration = len(samples) / sample_rate
        rms = float(np.sqrt(np.mean(samples**2) + 1e-12))

        speech_rate = self._syllable_rate(samples, sample_rate)
        highband = self._highband_ratio(samples, sample_rate)
        f0_mean, tremor = self._pitch_and_tremor(samples, sample_rate)
        am_rate = self._am_rate(samples, sample_rate)

        return AudioFeatures(
            duration=duration,
            rms=rms,
            speech_rate=speech_rate,
            highband_ratio=highband,
            f0_mean=f0_mean,
            tremor=tremor,
            am_rate=am_rate,
        )

    # ---- 内部 ----

    def _envelope(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        frame_len = max(1, int(sample_rate * self.envelope_ms / 1000.0))
        n = len(samples)
        if n < frame_len:
            return np.array([float(np.sqrt(np.mean(samples**2) + 1e-12))])
        n_frames = n // frame_len
        frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
        env = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
        return env

    def _syllable_rate(self, samples: np.ndarray, sample_rate: int) -> float:
        """能量峰率（音节近似）。统计局部能量极大值数 / 时长。"""
        env = self._envelope(samples, sample_rate)
        if len(env) < 3:
            return 0.0
        # 平滑
        if len(env) >= 5:
            k = np.ones(3) / 3
            env = np.convolve(env, k, mode="same")
        # 局部极大值（严格大于两侧）
        peaks = (env[1:-1] > env[:-2]) & (env[1:-1] > env[2:])
        # 且仅当超过全局中位数的 1.3 倍（滤除底噪波动）
        med = float(np.median(env)) if len(env) else 0.0
        thr = max(med * 1.3, 1e-4)
        count = int(np.sum(peaks & (env[1:-1] > thr)))
        duration = len(samples) / sample_rate
        if duration <= 0:
            return 0.0
        return count / duration

    def _highband_ratio(self, samples: np.ndarray, sample_rate: int) -> float:
        """高频能量占比（> highband_cutoff 的能量 / 总能量）。

        > cutoff 的能量是「宽带」标志：正常/急促语音（16k 宽频）有明显高频能量；
        电话（砖墙带限 3.4k）高频能量≈0。用于区分窄带（电话）与宽带语音。
        """
        n = len(samples)
        if n < 2:
            return 0.0
        win = np.hanning(n)
        spec = np.fft.rfft(samples * win)
        power = np.abs(spec) ** 2
        freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
        total = float(np.sum(power)) + 1e-12
        high = float(np.sum(power[freqs >= self.highband_cutoff])) + 1e-12
        return high / total

    def _pitch_and_tremor(self, samples: np.ndarray, sample_rate: int) -> tuple[float, float]:
        """基频均值（自相关）与振幅调制深度（哭腔代理）。"""
        # 降采样到 8k 以减少自相关计算量（基频精度足够）
        target = 8000
        if sample_rate != target:
            n_target = round(len(samples) * target / sample_rate)
            if n_target < 1:
                return 0.0, 0.0
            x = np.linspace(0, len(samples) - 1, n_target)
            sig = np.interp(x, np.arange(len(samples)), samples)
        else:
            sig = samples
        sig = sig - np.mean(sig)

        f0 = 0.0
        lo_lag = int(target / self.f0_range[1])  # 高频上限 → 最小 lag
        hi_lag = int(target / self.f0_range[0])  # 低频下限 → 最大 lag
        hi_lag = min(hi_lag, len(sig) - 1)
        if hi_lag > lo_lag and len(sig) > hi_lag:
            norm = sig / (np.sqrt(np.sum(sig**2)) + 1e-12)
            ac = _autocorr_upto(norm, hi_lag)
            ac[:lo_lag] = -np.inf
            # 基频 = [lo_lag, hi_lag] 内**首个**显著局部峰（最小 lag 的强峰优先）。
            #
            # 不能用全局 ``argmax``：纯音自相关在基频与各个谐波处都是局部峰，谐波（更大 lag）
            # 的数值往往略高于基频（例：300Hz 纯音 lag=27 处 ac≈0.996、lag=80 处 ac≈0.998），
            # 全局 argmax 会误选 3 次谐波 → 经典「octave / harmonic error」（f0 跳到 1/3 处）。
            # 基频峰永远是最小 lag 的那个（周期最短），故取首个超过阈值（0.9×全局 max）的
            # 局部峰即可规避。修改前 ``test_f0_is_stable_across_signal_lengths[5.0]`` 正是卡住这个 bug。
            gmax = float(np.max(ac))
            threshold = 0.9 * gmax
            best = 0
            # 取 [lo_lag, hi_lag] 内**首个**显著局部峰：基频峰永远是最小 lag 的强峰，
            # 故最小 lag 优先可规避全局 argmax 误选谐波的 octave/harmonic error。
            # 范围**含 hi_lag**（默认 80Hz 的基频峰恰好落在 lag=100，旧代码 `range(..hi_lag)`
            # 漏检）；i=hi_lag 是数组末位（ac 长 hi_lag+1），无右侧邻居，按「左高即峰」判定，
            # 不越界访问 ac[hi_lag+1]。
            for i in range(lo_lag, hi_lag + 1):
                if ac[i] < ac[i - 1] or ac[i] < threshold:
                    continue
                if i < hi_lag and ac[i] <= ac[i + 1]:
                    continue
                best = i
                break
            if best > 0:
                f0 = target / best

        # 振幅调制深度（tremor）：包络的 (max-min)/(max+min)
        env = self._envelope(samples, sample_rate)
        tremor = 0.0
        if len(env) > 1 and float(np.max(env)) > 1e-6:
            tremor = float((np.max(env) - np.min(env)) / (np.max(env) + np.min(env) + 1e-9))

        return float(f0), float(tremor)

    def _am_rate(self, samples: np.ndarray, sample_rate: int) -> float:
        """振幅调制速率（Hz）：包络自相关的主峰 lag → 调制频率。

        急促言语（快 AM ~7Hz）与抽泣（慢 AM ~1.5Hz）节奏不同；用此区分二者，
        避免与正常语音（音节率 ~3-5Hz）混淆。包络为短帧 RMS 序列。
        """
        env = self._envelope(samples, sample_rate)
        n = len(env)
        if n < 8:
            return 0.0
        env = env - np.mean(env)
        ac = np.correlate(env, env, mode="full")[n - 1 :]
        frame_hz = 1000.0 / self.envelope_ms
        # 搜索 lag 对应 [0.5Hz, 12Hz]
        min_lag = max(1, int(frame_hz / 12.0))
        max_lag = min(len(ac) - 1, int(frame_hz / 0.5))
        if max_lag <= min_lag:
            return 0.0
        segment = ac[min_lag : max_lag + 1]
        best = int(np.argmax(segment)) + min_lag
        if best <= 0:
            return 0.0
        # 峰值落在搜索下界 → 调制过快/不可靠（近平坦包络的伪峰），判为无明确调制
        if best == min_lag:
            return 0.0
        return float(frame_hz / best)
