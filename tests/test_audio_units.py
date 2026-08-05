"""音频感知链路**单元测试**（ADR-0026 Tier0 · features / vad / detector）。

> 与 ``test_audio_perception.py`` 的分工：
> - 那边是**端到端**——跑真实 WAV fixture 过完整管道，守 manifest 契约。
> - 这边是**单元**——用解析可控的合成信号（正弦 / AM 调幅 / 白噪 / 静音）直接打特征提取、
>   VAD 分段、片段合并三个模块，断言可从信号参数**独立推导**的数值，不依赖 fixture。
>
> 为什么必须补：Tier0 校准期真实踩过两个 bug，端到端测试**当时全绿却没抓住**——
> ① ``_am_rate`` 自相关峰落在搜索下界，平坦包络被误报为 16.67Hz 调制；
> ② ``EnergyVadBackend`` 的 ``relative_ratio`` 过高，连续语音（无静音间隙）被判 0 段。
> 二者都在本文件里有专门的回归锁（见 ``*_regression`` 测试）。
>
> 全 torch-free：只依赖 numpy。
"""

from __future__ import annotations

import time
from itertools import pairwise

import numpy as np
import pytest

from home_perception.audio import (
    AudioDetector,
    AudioFeatureExtractor,
    EnergyVadBackend,
    LoadedAudio,
    VadBackend,
    WebRtcVadBackend,
    select_vad,
)

SR = 16000
EX = AudioFeatureExtractor()


# ============================================================================
# 合成信号（解析可控：每个特征的期望值都能从参数推出来）
# ============================================================================


def tone(freq: float, dur: float, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    """纯正弦。理论 rms = amp / sqrt(2)，f0 = freq，包络恒定 → tremor≈0。"""
    t = np.arange(int(sr * dur)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def am_tone(
    carrier: float, mod_hz: float, dur: float, depth: float = 1.0, amp: float = 0.3
) -> np.ndarray:
    """调幅正弦：载波 ``carrier``，包络以 ``mod_hz`` 起伏。

    ``depth=1.0`` → 包络在 [0,1] 全幅摆动；``depth=0`` → 包络恒为 1（平坦，无调制）。
    """
    t = np.arange(int(SR * dur)) / SR
    env = 1.0 - depth * 0.5 * (1.0 + np.cos(2 * np.pi * mod_hz * t))
    return (amp * env * np.sin(2 * np.pi * carrier * t)).astype(np.float32)


def silence(dur: float) -> np.ndarray:
    return np.zeros(int(SR * dur), dtype=np.float32)


def _audio(samples: np.ndarray, sr: int = SR) -> LoadedAudio:
    return LoadedAudio(samples=samples, sample_rate=sr)


# ============================================================================
# 1) AudioFeatureExtractor —— 每个特征对着可解析推导的信号
# ============================================================================


@pytest.mark.parametrize("amp", [0.2, 0.5, 0.8])
def test_rms_matches_analytic_value(amp: float) -> None:
    """正弦的 RMS 有闭式解 amp/sqrt(2)，用它校验 rms 不是随手写的近似。"""
    f = EX.extract(tone(440, 2.0, amp), SR)
    assert f.rms == pytest.approx(amp / np.sqrt(2), rel=0.02)


def test_rms_is_amplitude_monotonic() -> None:
    """振幅翻倍 → rms 翻倍（线性，不是任意单调函数）。"""
    quiet = EX.extract(tone(440, 2.0, 0.2), SR).rms
    loud = EX.extract(tone(440, 2.0, 0.4), SR).rms
    assert loud == pytest.approx(quiet * 2.0, rel=0.03)


@pytest.mark.parametrize("freq", [80.0, 120.0, 200.0, 300.0, 500.0])
def test_f0_tracks_carrier_pitch(freq: float) -> None:
    """f0 估计须跟随真实基频（自相关法，在 f0_range 80~500Hz 内）。

    含下界 80Hz：其基频峰恰好落在 lag=100（= hi_lag），旧 range(..hi_lag) 会漏检 → f0 失真；
    此参数即该回归锁。
    """
    f = EX.extract(tone(freq, 2.0, 0.5), SR)
    assert f.f0_mean == pytest.approx(freq, rel=0.05)


def test_highband_ratio_separates_narrow_and_wide() -> None:
    """highband_ratio 以 3400Hz 为界：低频音≈0，高频音≈1。

    这是电话/哭腔（砖墙带限）与正常/急促（宽带）的分隔特征，必须真的分得开。
    """
    low = EX.extract(tone(200, 2.0, 0.5), SR).highband_ratio
    high = EX.extract(tone(6000, 2.0, 0.5), SR).highband_ratio
    assert low < 0.01, f"200Hz 纯音的高频占比应≈0，实际 {low}"
    assert high > 0.95, f"6000Hz 纯音的高频占比应≈1，实际 {high}"


def test_highband_ratio_broadband_noise_is_intermediate() -> None:
    """白噪是宽带的：高频占比落在中间区间，不会被误判为窄带。"""
    rng = np.random.default_rng(42)
    noise = (rng.standard_normal(SR * 2) * 0.1).astype(np.float32)
    hi = EX.extract(noise, SR).highband_ratio
    assert 0.3 < hi < 0.8, f"白噪高频占比应居中，实际 {hi}"


@pytest.mark.parametrize(("mod_hz", "dur"), [(1.5, 4.0), (3.0, 3.0), (7.0, 3.0)])
def test_am_rate_matches_modulation_frequency(mod_hz: float, dur: float) -> None:
    """am_rate 必须还原真实调制频率（容差含帧率量化误差）。

    这是区分「急促（快 AM ~7Hz）」与「哭腔（慢抽泣 ~1.5Hz）」的唯一特征。
    """
    f = EX.extract(am_tone(300, mod_hz, dur, depth=1.0), SR)
    assert f.am_rate == pytest.approx(mod_hz, abs=0.5), (
        f"调制 {mod_hz}Hz 应测得同频，实际 {f.am_rate}"
    )


def test_am_rate_is_zero_on_flat_envelope_regression() -> None:
    """回归锁：平坦包络必须判「无调制」= 0，不得产出边界伪峰。

    历史 bug —— ``_am_rate`` 取自相关峰时未排除搜索下界，AGC 压平的电话段（包络恒定）
    会稳定产出 16.67Hz 的假调制，进而把电话段误判成急促/哭腔。修复方式是「峰落在
    搜索下界即视为无调制」。端到端 fixture 测试当时全绿，没抓住这个 bug。
    """
    flat = am_tone(300, 0.0001, 3.0, depth=0.0)
    f = EX.extract(flat, SR)
    assert f.am_rate == 0.0, f"平坦包络应判无调制，实际 {f.am_rate}Hz（疑似边界伪峰）"
    assert f.tremor < 0.05, f"平坦包络的调制深度应≈0，实际 {f.tremor}"


def test_tremor_tracks_modulation_depth() -> None:
    """tremor（调制深度）随包络起伏幅度单调上升，平坦时≈0、全幅时≈1。"""
    flat = EX.extract(am_tone(300, 3.0, 3.0, depth=0.0), SR).tremor
    shallow = EX.extract(am_tone(300, 3.0, 3.0, depth=0.4), SR).tremor
    deep = EX.extract(am_tone(300, 3.0, 3.0, depth=1.0), SR).tremor
    assert flat < shallow < deep
    assert flat < 0.05 and deep > 0.9


def test_speech_rate_tracks_syllable_pace() -> None:
    """音节率随包络起伏速率上升——电话（AGC 抹平）与哭腔（有音节）靠它分开。"""
    slow = EX.extract(am_tone(300, 1.5, 4.0, depth=1.0), SR).speech_rate
    fast = EX.extract(am_tone(300, 7.0, 3.0, depth=1.0), SR).speech_rate
    flat = EX.extract(am_tone(300, 3.0, 3.0, depth=0.0), SR).speech_rate
    assert flat == pytest.approx(0.0, abs=0.1), "包络平坦时不应数出音节"
    assert slow < fast


@pytest.mark.parametrize("dur", [0.5, 1.0, 2.0, 5.0])
def test_f0_is_stable_across_signal_lengths(dur: float) -> None:
    """同一音高、不同时长，f0 估计必须一致。

    这条同时守着自相关实现的正确性：基频用 FFT 自相关算前 N 个 lag，若零填充不足，
    循环卷积会把尾部绕回污染低 lag，且污染量随信号长度变化 —— 症状正是"f0 随时长漂移"。
    """
    f = EX.extract(tone(300, dur, 0.5), SR)
    assert f.f0_mean == pytest.approx(300.0, rel=0.05), (
        f"时长 {dur}s 时 f0 漂移到 {f.f0_mean}Hz"
    )


@pytest.mark.parametrize("n_samples", [8093, 16285, 4095, 9999])
def test_autocorr_zero_padding_no_circular_leak(n_samples: int) -> None:
    """_autocorr_upto 的零填充必须 >= n+max_lag，否则循环卷积会把尾部绕回污染低 lag。

    取若干"临界长度"（n+max_lag 恰好跨过 2 的幂，是零填充余量最紧的点），f0 仍须等于
    真实基频。若未来有人把 ``n_fft`` 改成 ``1 << n.bit_length()``（漏加 max_lag），这些长度会
    因循环卷积污染而给出错误 f0 —— 此测试即是该回归锁。
    """
    sig = tone(300, n_samples / 8000.0, 0.5, sr=8000)
    f = EX.extract(sig, 8000)
    assert f.f0_mean == pytest.approx(300.0, rel=0.05), (
        f"长度 {n_samples}（零填充边界）下 f0 漂移到 {f.f0_mean}Hz，疑似循环卷积污染"
    )


def test_extract_is_much_faster_than_realtime() -> None:
    """性能回归锁：特征提取必须显著快于实时。

    历史缺陷（由本文件的单元测试暴露）—— ``_pitch_and_tremor`` 用
    ``np.correlate(mode="full")`` 做自相关，是 O(n²) 且算出全部 2n-1 个 lag，而基频只需
    前 ~100 个。实测 2 秒片段耗时 3.0s、4 秒 10.8s、8 秒 27.9s：**比实时还慢**，且超线性
    恶化，实时管道根本跑不动。端到端 fixture 测试全绿（fixture 都短），没抓住。
    改 FFT 自相关后 8 秒片段降到 ~18ms。

    这里的 1.0s 上限对修复后的实现有 ~50 倍裕度（不受 CI 机器性能波动影响），
    但对退化回 O(n²) 的实现（27.9s）会立刻报红。
    """
    sig = tone(300, 8.0, 0.5)
    EX.extract(tone(300, 0.5, 0.5), SR)  # 预热，排除首次导入/JIT 开销

    start = time.perf_counter()
    EX.extract(sig, SR)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, (
        f"8 秒音频的特征提取耗时 {elapsed:.2f}s，远超预算——疑似自相关退化回 O(n²)"
    )


def test_duration_matches_sample_count() -> None:
    f = EX.extract(tone(300, 1.75, 0.5), SR)
    assert f.duration == pytest.approx(1.75, rel=1e-6)


@pytest.mark.parametrize(
    ("name", "samples"),
    [
        ("empty", np.array([], dtype=np.float32)),
        ("sub_frame", tone(300, 10 / SR, 0.5)),
        ("all_zero", silence(1.0)),
    ],
)
def test_extract_degenerate_input_no_crash(name: str, samples: np.ndarray) -> None:
    """退化输入（空 / 短于一帧 / 全静音）必须返回有限数值，不抛异常、不产 NaN。"""
    f = EX.extract(samples, SR)
    for field in ("rms", "speech_rate", "highband_ratio", "f0_mean", "tremor", "am_rate"):
        v = getattr(f, field)
        assert np.isfinite(v), f"{name}.{field} 非有限值：{v}"
        assert v >= 0.0, f"{name}.{field} 为负：{v}"


# ============================================================================
# 2) EnergyVadBackend —— 分段行为
# ============================================================================


def test_energy_vad_finds_speech_between_silence() -> None:
    """静音 / 语音交替 → 恰好切出两段，且落在真实语音区间上。"""
    sig = np.concatenate(
        [silence(0.5), tone(300, 0.5), silence(0.5), tone(300, 0.5), silence(0.5)]
    )
    segs = EnergyVadBackend().detect(_audio(sig))
    assert len(segs) == 2, f"应切出 2 段，实际 {segs}"
    assert segs[0][0] == pytest.approx(0.5, abs=0.05)
    assert segs[0][1] == pytest.approx(1.0, abs=0.05)
    assert segs[1][0] == pytest.approx(1.5, abs=0.05)


def test_energy_vad_all_silence_no_segments() -> None:
    assert EnergyVadBackend().detect(_audio(silence(2.0))) == []


def test_energy_vad_empty_returns_empty() -> None:
    assert EnergyVadBackend().detect(_audio(np.array([], dtype=np.float32))) == []


def test_energy_vad_continuous_speech_is_one_segment_regression() -> None:
    """回归锁：全程有声（无静音间隙）必须切出**一整段**，不能是 0 段。

    历史 bug —— 阈值是「中位数 × relative_ratio」的相对阈值，当 fixture 经 RMS 归一化后
    整段能量被拉平，``relative_ratio=1.5`` 使得没有任何帧能超过 1.5×中位数 → 0 段 →
    整条管道静默产不出事件。降到 0.4 后修复。
    """
    segs = EnergyVadBackend().detect(_audio(tone(300, 2.0, 0.5)))
    assert len(segs) == 1, f"连续语音应为单段，实际 {segs}"
    assert segs[0][0] == pytest.approx(0.0, abs=0.05)
    assert segs[0][1] == pytest.approx(2.0, abs=0.05)


def test_energy_vad_relative_ratio_mutation_flips_detection() -> None:
    """变异验证：``relative_ratio`` 真的驱动分段——调回致病值即复现 0 段。"""
    sig = tone(300, 2.0, 0.5)
    assert len(EnergyVadBackend(relative_ratio=0.4).detect(_audio(sig))) == 1
    assert EnergyVadBackend(relative_ratio=1.5).detect(_audio(sig)) == []


def test_energy_vad_floor_mutation_flips_detection() -> None:
    """变异验证：``floor`` 绝对地板真的驱动分段——抬高地板即淹没弱语音段。

    构造「长静音 + 低幅纯音」：相对阈值（中位数×ratio）被静音拉到≈0，分段完全由 floor
    决定。floor 低于音幅 → 检出 1 段；floor 高于音幅 → 0 段。
    """
    sig = np.concatenate([silence(0.5), tone(300, 0.5, amp=0.005), silence(0.5)])
    assert len(EnergyVadBackend(floor=0.001).detect(_audio(sig))) == 1
    assert EnergyVadBackend(floor=0.01).detect(_audio(sig)) == []


def test_energy_vad_rejects_segment_shorter_than_min() -> None:
    """短于 ``min_segment_ms`` 的瞬时噪声不成段（抑制误触发）。"""
    sig = np.concatenate([silence(0.5), tone(300, 0.05), silence(0.5)])
    assert EnergyVadBackend(min_segment_ms=150).detect(_audio(sig)) == []


def test_energy_vad_min_segment_mutation_flips_detection() -> None:
    """变异验证：放宽 ``min_segment_ms`` 后，同一段 50ms 突发就应被接受。"""
    sig = np.concatenate([silence(0.5), tone(300, 0.05), silence(0.5)])
    assert EnergyVadBackend(min_segment_ms=150).detect(_audio(sig)) == []
    assert len(EnergyVadBackend(min_segment_ms=20).detect(_audio(sig))) == 1


def test_energy_vad_sub_frame_signal_falls_back_to_whole() -> None:
    """短于一帧的信号走兜底：整段视为一段（避免空输出，见 vad.py 注释）。"""
    segs = EnergyVadBackend().detect(_audio(tone(300, 100 / SR, 0.5)))
    assert len(segs) == 1
    assert segs[0] == (0.0, pytest.approx(100 / SR, rel=1e-6))


def test_energy_vad_segments_are_sorted_and_non_overlapping() -> None:
    """结构不变量：段有序、不重叠、start < end。"""
    sig = np.concatenate(
        [silence(0.3), tone(300, 0.4), silence(0.4), tone(300, 0.4), silence(0.3)]
    )
    segs = EnergyVadBackend().detect(_audio(sig))
    assert segs, "应至少切出一段"
    for s, e in segs:
        assert s < e
    for prev, nxt in pairwise(segs):
        assert prev[1] <= nxt[0], f"段重叠或倒序：{prev} -> {nxt}"


def test_energy_vad_backend_name() -> None:
    assert EnergyVadBackend().name == "energy"


# ============================================================================
# 3) WebRtcVadBackend / select_vad —— 可选后端的降级契约
# ============================================================================


def test_webrtc_backend_degrades_without_raising() -> None:
    """``webrtcvad`` 未安装（Windows 本地 / 当前 CI 轻依赖）时必须静默降级，不得抛异常。

    安装了则应能正常分段——两种环境下都不允许崩。
    """
    backend = WebRtcVadBackend()
    segs = backend.detect(_audio(tone(300, 1.0, 0.5)))
    assert isinstance(segs, list)
    if backend.available:
        assert all(s < e for s, e in segs)
    else:
        assert segs == []
        assert "unavailable" in backend.name


def test_select_vad_returns_usable_backend() -> None:
    """select_vad 必须返回可用后端；其分段结果须结构合法（start < end、非负、≤1 段）。"""
    backend = select_vad()
    assert isinstance(backend, VadBackend)
    segs = backend.detect(_audio(tone(300, 1.0, 0.5)))
    assert isinstance(segs, list)
    # 1s 纯音至多一段；若有段，必须结构合法（start < end、非负、不超音频边界）
    assert len(segs) <= 1, f"1s 纯音不应切出多段，实际 {segs}"
    for s, e in segs:
        assert 0.0 <= s < e <= 1.0, f"分段结构非法：({s}, {e})"


def test_detector_default_backend_is_energy_for_determinism() -> None:
    """架构锁：默认管道必须用能量后端，**不能**用 ``select_vad()``。

    否则本地(Windows→energy) 与 CI(Linux 若装了 webrtcvad→webrtc) 会切出不同片段，
    自校准的 manifest 随即漂移、fixture 测试不可复现。WebRTC 只能显式 opt-in。
    """
    assert isinstance(AudioDetector().vad, EnergyVadBackend)
    assert AudioDetector().vad.name == "energy"


def test_detector_uses_explicit_webrtc_when_provided() -> None:
    """架构锁补充：显式传入 WebRTC 后端时必须真的用上它（opt-in 入口）。

    与 ``test_detector_default_backend_is_energy_for_determinism`` 形成完整契约：
    默认走能量后端保证确定性；只有显式 ``AudioDetector(vad=WebRtcVadBackend())`` 才切 WebRTC。
    """
    det = AudioDetector(vad=WebRtcVadBackend(aggressiveness=2))
    assert det.vad.name.startswith("webrtc")


# ============================================================================
# 4) AudioDetector —— 片段合并 + vad_ratio
# ============================================================================


class _FakeVad(VadBackend):
    """注入固定分段，把合并逻辑与 VAD 本身解耦测试。"""

    def __init__(self, segments: list[tuple[float, float]]) -> None:
        self._segments = segments

    def detect(self, audio: LoadedAudio) -> list[tuple[float, float]]:
        return list(self._segments)

    @property
    def name(self) -> str:
        return "fake"


_AUDIO_2S = _audio(silence(2.0))


def test_detector_merges_segments_within_gap() -> None:
    """间隔 200ms < merge_gap 300ms → 合并为整句级单段。"""
    det = AudioDetector(vad=_FakeVad([(0.0, 1.0), (1.2, 2.0)]), merge_gap_ms=300)
    assert det.detect(_AUDIO_2S).segments == [(0.0, 2.0)]


def test_detector_keeps_segments_beyond_gap() -> None:
    """间隔 500ms > merge_gap 300ms → 保持两段。"""
    det = AudioDetector(vad=_FakeVad([(0.0, 1.0), (1.5, 2.0)]), merge_gap_ms=300)
    assert det.detect(_AUDIO_2S).segments == [(0.0, 1.0), (1.5, 2.0)]


def test_detector_merge_gap_mutation_flips_result() -> None:
    """变异验证：``merge_gap_ms`` 真的驱动合并——同一输入，收紧间隔即不再合并。"""
    segs = [(0.0, 1.0), (1.2, 2.0)]
    merged = AudioDetector(vad=_FakeVad(segs), merge_gap_ms=300).detect(_AUDIO_2S)
    split = AudioDetector(vad=_FakeVad(segs), merge_gap_ms=100).detect(_AUDIO_2S)
    assert merged.segments == [(0.0, 2.0)]
    assert split.segments == [(0.0, 1.0), (1.2, 2.0)]


# 顺序无关测试用的规范输入（避免参数矩阵里重复写两遍同一对段）
_CASE_SEGMENTS = [(0.0, 1.0), (1.2, 2.0)]


@pytest.mark.parametrize(
    "order",
    ["sorted", "reversed"],
    ids=["sorted", "reversed"],
)
def test_detector_merge_is_order_independent(order: str) -> None:
    """顺序无关：VAD 交付顺序不应影响合并结果（内部先排序）。"""
    segs = list(_CASE_SEGMENTS) if order == "sorted" else list(reversed(_CASE_SEGMENTS))
    det = AudioDetector(vad=_FakeVad(segs), merge_gap_ms=300)
    assert det.detect(_AUDIO_2S).segments == [(0.0, 2.0)]


def test_detector_merges_overlapping_segments() -> None:
    """重叠段应合并成一个覆盖区间，不产生倒挂。"""
    det = AudioDetector(vad=_FakeVad([(0.0, 1.2), (0.8, 2.0)]), merge_gap_ms=300)
    assert det.detect(_AUDIO_2S).segments == [(0.0, 2.0)]


def test_detector_nested_segment_does_not_shrink_range() -> None:
    """被包含的短段不得把已合并区间的右端点缩回去。"""
    det = AudioDetector(vad=_FakeVad([(0.0, 2.0), (0.5, 1.0)]), merge_gap_ms=300)
    assert det.detect(_AUDIO_2S).segments == [(0.0, 2.0)]


def test_detector_empty_input() -> None:
    result = AudioDetector(vad=_FakeVad([]), merge_gap_ms=300).detect(_AUDIO_2S)
    assert result.segments == []
    assert result.vad_ratio == 0.0


@pytest.mark.parametrize(
    ("segments", "expected_ratio"),
    [
        ([(0.0, 2.0)], 1.0),
        ([(0.0, 1.0), (1.5, 2.0)], 0.75),
        ([(0.0, 0.5)], 0.25),
        ([], 0.0),
    ],
    ids=["full", "two_thirds", "quarter", "none"],
)
def test_detector_vad_ratio(
    segments: list[tuple[float, float]], expected_ratio: float
) -> None:
    """vad_ratio = 合并后语音总时长 / 音频总时长。"""
    det = AudioDetector(vad=_FakeVad(segments), merge_gap_ms=300)
    assert det.detect(_AUDIO_2S).vad_ratio == pytest.approx(expected_ratio, abs=1e-6)


def test_detector_vad_ratio_clamped_to_one() -> None:
    """即使 VAD 交付越界段，占比也不得 > 1（下游按 0~1 消费）。"""
    det = AudioDetector(vad=_FakeVad([(0.0, 5.0)]), merge_gap_ms=300)
    assert det.detect(_AUDIO_2S).vad_ratio == 1.0


def test_detector_vad_ratio_uses_actual_audio_duration() -> None:
    """vad_ratio 分母必须是真实音频时长，而非硬编码 2s（防多通道/截断时长取错）。"""
    audio = _audio(silence(4.0))
    det = AudioDetector(vad=_FakeVad([(0.0, 2.0)]), merge_gap_ms=300)
    assert det.detect(audio).vad_ratio == pytest.approx(0.5, abs=1e-6)


def test_detector_reports_backend_name() -> None:
    """可观测性：结果须带回后端名，便于定位跨平台分段差异。"""
    assert AudioDetector(vad=_FakeVad([]), merge_gap_ms=300).detect(_AUDIO_2S).backend == "fake"


def test_detector_end_to_end_on_synthetic_signal() -> None:
    """真实 VAD + 合并联跑：两段近距语音（间隔 200ms）应被并成一整句。"""
    sig = np.concatenate([tone(300, 0.5, 0.5), silence(0.2), tone(300, 0.5, 0.5)])
    assert len(EnergyVadBackend().detect(_audio(sig))) == 2, "原始 VAD 应先切出两段"
    merged = AudioDetector(merge_gap_ms=300).detect(_audio(sig)).segments
    assert len(merged) == 1, f"间隔 200ms 应合并为一段，实际 {merged}"
