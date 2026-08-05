"""音频合成增强效果库（Audio Synthetic Infrastructure / ``tts`` 包子模块）。

> 属测试 / 评估基础设施，不被音频感知运行时 import（与 ``tts`` 包整体一致）。
> 仅依赖 numpy（与音频包「零额外依赖」一致）；确定性由显式 ``seed`` 控制。
>
> 设计：每个效果是 ``(samples, sr, **params) -> samples`` 的纯函数，由
> :func:`apply_effects` 按场景声明的 chain 顺序依次施加，构成可复现的合成管线。
>
> 支持的效果（``EFFECTS`` 注册表）：
>   - ``speech_rate`` 语速扰动（保音高时长伸缩，相位声码器）
>   - ``volume``      音量扰动（线性增益 / dB）
>   - ``noise``       噪声（按 SNR 叠加背景噪声，white / pink）
>   - ``reverb``      混响（与合成房间脉冲响应卷积）
>   - ``distance``    距离衰减（球面声传播 + 可选空气吸收高频滚降）
"""

from __future__ import annotations

import warnings
from collections.abc import Callable

import numpy as np

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _clip(x: np.ndarray) -> np.ndarray:
    return np.clip(x, -1.0, 1.0).astype(np.float32)


def _lowpass(x: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """一阶 IIR 低通（空气吸收 / 距离高频衰减）。numpy 实现，无 scipy 依赖。"""
    rc = 1.0 / (2.0 * np.pi * max(cutoff_hz, 1.0))
    dt = 1.0 / sr
    alpha = dt / (rc + dt)
    y = np.empty_like(x, dtype=np.float64)
    y[0] = float(x[0])
    for i in range(1, len(x)):
        y[i] = y[i - 1] + alpha * (float(x[i]) - y[i - 1])
    return y.astype(np.float32)


def _pink(x: np.ndarray, sr: int) -> np.ndarray:
    """近似 pink 噪声：白噪声经低通后按能量归一，使幅度分布与输入噪声一致。"""
    y = _lowpass(x, sr, 2500.0)
    std = float(np.std(y)) + 1e-9
    return (y / std * float(np.std(x))).astype(np.float32)


# ---------------------------------------------------------------------------
# 1. 语速扰动（保音高时长伸缩，相位声码器）
# ---------------------------------------------------------------------------


def apply_speech_rate(
    samples: np.ndarray,
    sr: int,
    factor: float = 1.0,
    n_fft: int = 1024,
    hop: int | None = None,
) -> np.ndarray:
    """语速扰动：保持音高的时长伸缩。``factor > 1`` 更快（时长缩短为 ``1/factor``）。

    采用相位声码器（phase vocoder）：在 STFT 域累加相位并沿时间轴重采样，
    从而在改变语速的同时避免「芯片音」式音高偏移。
    """
    if factor <= 0:
        raise ValueError("factor 必须 > 0")
    if len(samples) < n_fft:
        warnings.warn(
            f"apply_speech_rate: 信号长度 {len(samples)} < n_fft({n_fft})，相位声码器不可用，"
            "静默返回原信号（语速不变）。请传入更长音频或减小 n_fft。",
            stacklevel=2,
        )
        return samples.astype(np.float32)
    if abs(factor - 1.0) < 1e-3:
        return samples.astype(np.float32)

    x = samples.astype(np.float32)
    hop = hop or n_fft // 4
    window = np.hanning(n_fft)
    n = len(x)
    n_frames = 1 + (n - n_fft) // hop
    if n_frames < 2:
        return x

    frames = np.stack([x[i * hop : i * hop + n_fft] for i in range(n_frames)]) * window
    spec = np.fft.rfft(frames, n=n_fft)  # (F, B)
    mag = np.abs(spec)
    phase = np.angle(spec)
    expected = 2.0 * np.pi * hop * np.fft.rfftfreq(n_fft, d=1.0 / sr)  # (B,)

    # 相位累加（连续相位轨迹，消除帧间 2π 跳变）
    phase_acc = np.zeros_like(phase)
    phase_acc[0] = phase[0]
    prev = phase[0]
    for i in range(1, n_frames):
        dphi = phase[i] - prev - expected
        dphi = dphi - 2.0 * np.pi * np.round(dphi / (2.0 * np.pi))
        phase_acc[i] = phase_acc[i - 1] + expected + dphi
        prev = phase[i]

    # 沿时间轴按 factor 重采样（线性插值；phase 轨迹连续，插值安全）
    out_frames = max(1, round(n_frames / factor))
    old_idx = np.arange(n_frames)
    new_idx = np.linspace(0, n_frames - 1, out_frames)
    lo = np.searchsorted(old_idx, new_idx) - 1
    lo = np.clip(lo, 0, n_frames - 2)
    hi = lo + 1
    t = (new_idx - old_idx[lo]) / (old_idx[hi] - old_idx[lo] + 1e-12)
    mag_i = (1.0 - t)[:, None] * mag[lo] + t[:, None] * mag[hi]
    phase_i = (1.0 - t)[:, None] * phase_acc[lo] + t[:, None] * phase_acc[hi]
    stft_out = mag_i * np.exp(1j * phase_i)  # (outF, B)

    # 逆 STFT（重叠相加）
    y_frames = np.fft.irfft(stft_out, n=n_fft) * window
    y = np.zeros(out_frames * hop + n_fft, dtype=np.float64)
    w = np.zeros_like(y)
    for i in range(out_frames):
        s = i * hop
        y[s : s + n_fft] += y_frames[i]
        w[s : s + n_fft] += window
    y = y / (w + 1e-8)
    return y.astype(np.float32)


# ---------------------------------------------------------------------------
# 2. 音量扰动（线性增益 / dB）
# ---------------------------------------------------------------------------


def apply_volume(
    samples: np.ndarray,
    sr: int,
    gain_db: float = 0.0,
    jitter_db: float = 0.0,
    seed: int = 42,
) -> np.ndarray:
    """音量扰动：线性增益（dB）。``gain_db > 0`` 更响；``jitter_db > 0`` 在 ±jitter 内随机化（确定性 seed）。"""
    db = gain_db
    if jitter_db:
        db = db + _rng(seed).uniform(-jitter_db, jitter_db)
    return _clip(samples * (10.0 ** (db / 20.0)))


# ---------------------------------------------------------------------------
# 3. 噪声（按 SNR 叠加背景噪声）
# ---------------------------------------------------------------------------


def apply_noise(
    samples: np.ndarray,
    sr: int,
    snr_db: float = 20.0,
    color: str = "white",
    seed: int = 42,
) -> np.ndarray:
    """噪声：叠加背景噪声，按 SNR(dB) 控制强度。``color``: ``white`` / ``pink``。确定性 seed。"""
    rng = _rng(seed)
    sig_power = float(np.mean(samples**2)) + 1e-12
    noise_power = sig_power / (10.0 ** (snr_db / 10.0))
    noise = rng.normal(0.0, np.sqrt(noise_power), size=samples.shape).astype(np.float32)
    if color == "pink":
        noise = _pink(noise, sr)
    return _clip(samples + noise)


# ---------------------------------------------------------------------------
# 4. 混响（合成房间脉冲响应卷积）
# ---------------------------------------------------------------------------


def _make_rir(sr: int, rt60: float, seed: int) -> np.ndarray:
    if rt60 <= 0.0:
        # 无混响 → 单位脉冲（卷积恒等式），避免 length=1 退化数值路径
        return np.array([1.0], dtype=np.float32)
    rng = _rng(seed)
    length = max(int(rt60 * sr), 1)
    decay = np.linspace(1.0, 0.0, length) ** 1.5  # 衰减包络
    rir = (rng.normal(0.0, 1.0, length) * decay).astype(np.float32)
    rir = rir / (np.max(np.abs(rir)) + 1e-9)
    rir[0] = 1.0  # 直达声
    return rir


def apply_reverb(
    samples: np.ndarray,
    sr: int,
    rt60: float = 0.3,
    wet: float = 0.4,
    seed: int = 42,
) -> np.ndarray:
    """混响：与合成房间脉冲响应(RIR)卷积。``rt60`` 混响时间(秒)，``wet`` 湿声比例。确定性 seed。

    保留完整混响尾部：输出长度 = ``len(samples) + len(rir) - 1``。
    直达声（干声）占前 ``len(samples)`` 样本，RIR 衰减尾部自然延伸其后，与物理一致。
    """
    if not 0.0 <= wet <= 1.0:
        raise ValueError("wet 必须在 [0, 1]")
    rir = _make_rir(sr, rt60, seed)
    wet_full = np.convolve(samples.astype(np.float32), rir, mode="full")
    # 湿声按干声 RMS 归一（含完整尾部能量），避免能量失控
    dry_rms = float(np.sqrt(np.mean(samples**2) + 1e-12))
    wet_rms = float(np.sqrt(np.mean(wet_full**2) + 1e-12))
    if wet_rms > 1e-9:
        wet_full = wet_full * (dry_rms / wet_rms)
    out = np.zeros(len(wet_full), dtype=np.float64)
    out[: len(samples)] += (1.0 - wet) * samples.astype(np.float64)
    out += wet * wet_full
    return _clip(out)


# ---------------------------------------------------------------------------
# 5. 距离衰减（球面声传播 + 空气吸收）
# ---------------------------------------------------------------------------


def apply_distance(
    samples: np.ndarray,
    sr: int,
    meters: float = 1.0,
    ref_meters: float = 1.0,
    air_absorption: bool = True,
    seed: int = 42,
) -> np.ndarray:
    """距离衰减：球面声传播衰减 + 可选空气吸收高频滚降。

    ``meters`` 距离；``ref_meters`` 参考距离（默认 1m，增益=1）；
    ``air_absorption`` 开启时高频随距离低通（模拟高频空气吸收）。
    """
    gain = ref_meters / (ref_meters + max(meters, 0.0))
    out = samples * gain
    if air_absorption:
        cutoff = max(400.0, 8000.0 - meters * 800.0)
        out = _lowpass(out, sr, cutoff)
    return _clip(out)


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------


EFFECTS: dict[str, Callable[..., np.ndarray]] = {
    "speech_rate": apply_speech_rate,
    "volume": apply_volume,
    "noise": apply_noise,
    "reverb": apply_reverb,
    "distance": apply_distance,
}


def apply_effects(samples: np.ndarray, sr: int, chain: list[dict]) -> np.ndarray:
    """按顺序施加效果链。``chain`` 中每个元素是单键 dict：``{ 效果名: { 参数 } }``。"""
    x = np.asarray(samples, dtype=np.float32)
    for step in chain:
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError(f"effect step 必须是单键 dict，如 {{name: {{...}}}}；收到 {step!r}")
        (name, params), = step.items()
        if name not in EFFECTS:
            raise KeyError(f"未知效果: {name!r}（可选：{sorted(EFFECTS)}）")
        x = EFFECTS[name](x, sr, **(params or {}))
    return x.astype(np.float32)
