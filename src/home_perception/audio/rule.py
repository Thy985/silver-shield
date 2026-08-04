"""音频规则（ADR-0026 管道：``AudioRule → AudioPerceptionEvent``，Tier0 Prosody 规则）。

> **边界约束（冻结）**：``AudioRule`` 只从 ``AudioFeatures`` 生成 ``AudioPerceptionEvent``，
> **绝不直接生成 ``RiskSignal``**。所有"音频 → ``RiskSignal``"翻译必经 ``integration/audio_adapter``。
>
> 判定基于合成 fixture 的可分离物理线索（与 fixture 生成签名一一对应，见 PR 描述「阈值校准」）：
>   - 高声 raised：响度 rms（fixture 先归一化再强增益 → 明显超过正常）
>   - 持续通话 telephone：窄带（高频能量≈0，砖墙带限）+ 低音节率（AGC 拉平包络 → 无音节峰）
>   - 哭诉 crying：窄带 + 有音节（音节率>0）+ 高调制深度（tremolo 慢调幅）
>   - 急促 rapid：宽带 + 快 AM（~7Hz 调幅） + 足够调制深度
> 阈值集中在本类（便于变异测试 + 与 fixture 结对校准）。``crying`` 的 Tier0 检测为近似，
> 真实泛化须靠后续家庭录音 + Tier1 YAMNet（ADR-0026 §8 开放问题）。
>
> 判定顺序（多特征命中取先匹配，避免跨类误判）：raised → telephone → crying → rapid → None。
> 注：telephone 与 crying 同为窄带，靠「音节率」区分——电话 AGC 抹平包络致音节率≈0，
> 哭腔保留自然音节（音节率>0），二者在阈值上互斥。
"""

from __future__ import annotations

from dataclasses import dataclass

from .event import (
    AudioPerceptionEvent,
    AudioPerceptionKind,
    new_event_id,
)
from .features import AudioFeatures


@dataclass
class RuleThresholds:
    """Tier0 规则阈值（集中管理，便于校准 / 变异测试）。

    阈值经 5 类 TTS fixture 实证校准（见 ``scripts/gen_audio_fixtures.py`` 自校准 + PR 描述）：
    - raised_rms：raised fixture rms≈0.40，正常/其他 ≤0.21 → 0.30 留足裕度
    - narrowband_hi：电话/哭腔 hi≈0（砖墙带限），正常/急促/高声 hi≥0.07 → 0.05 分隔
    - telephone_rate / cry_min_rate：电话 AGC 后音节率≈0，哭腔≈3 → 0.8 / 1.5 分隔
    - cry_tremor：哭腔 tremor≈0.9，窄带但低调制的不误判 → 0.60
    - rapid_min_am_rate：急促 AM≈6.7Hz，其他 ≤1.8Hz → 5.5 分隔
    - rapid_tremor：急促 tremor≈0.97 → 0.50
    """

    raised_rms: float = 0.30  # 响度阈值（raised fixture≈0.40，其余 ≤0.21）
    narrowband_hi: float = 0.05  # 高频能量占比阈值（低于 → 窄带/电话/哭腔）
    telephone_rate: float = 0.8  # 电话 AGC 后音节率上限（低于 → 无音节峰）
    cry_min_rate: float = 1.5  # 哭腔音节率下限（达到 → 有自然音节）
    cry_tremor: float = 0.60  # 哭腔调制深度阈值
    rapid_min_am_rate: float = 5.5  # 急促 AM 速率下限（Hz，明显快于正常）
    rapid_tremor: float = 0.50  # 急促调制深度下限
    cry_confidence: float = 0.6  # 哭腔 Tier0 置信（近似，标记需 Tier1）
    activation: float = 0.05  # 最低激活 score


class AudioRule:
    """Tier0 Prosody 规则：特征 → AudioPerceptionEvent（或 None）。"""

    def __init__(self, thresholds: RuleThresholds | None = None) -> None:
        self.t = thresholds or RuleThresholds()

    def evaluate(
        self,
        features: AudioFeatures,
        vad_ratio: float,
        timestamp: float,
        segment_id: str,
    ) -> AudioPerceptionEvent | None:
        """返回命中感知事件；无任何命中返回 None（负向对照不误报）。"""
        narrow = features.highband_ratio < self.t.narrowband_hi

        # 1) 高声 / 争吵（响度最强区分度，优先）
        if features.rms >= self.t.raised_rms:
            score = self._norm(features.rms, self.t.raised_rms, 0.6)
            return self._mk(
                AudioPerceptionKind.AUDIO_VOICE_RAISED, score,
                conf=self._clamp(0.6 + score * 0.4), ts=timestamp, seg=segment_id,
            )

        # 2) 持续通话（窄带 + AGC 抹平包络致音节率≈0）
        if narrow and features.speech_rate < self.t.telephone_rate:
            score = self._norm(1.0 - features.highband_ratio, 1.0 - self.t.narrowband_hi, 1.0)
            return self._mk(
                AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT, score,
                conf=self._clamp(0.55 + score * 0.35), ts=timestamp, seg=segment_id,
            )

        # 3) 哭诉 / 求助（窄带 + 有自然音节 + 高调制深度；Tier0 近似）
        if (
            narrow
            and features.speech_rate >= self.t.cry_min_rate
            and features.tremor >= self.t.cry_tremor
        ):
            score = self._norm(features.tremor, self.t.cry_tremor, 0.95)
            return self._mk(
                AudioPerceptionKind.AUDIO_DISTRESS_CRY, score, conf=self.t.cry_confidence,
                ts=timestamp, seg=segment_id,
            )

        # 4) 急促言语（宽带 + 快 AM + 足够调制深度）
        if features.am_rate >= self.t.rapid_min_am_rate and features.tremor >= self.t.rapid_tremor:
            score = self._norm(features.am_rate, self.t.rapid_min_am_rate, 8.0)
            return self._mk(
                AudioPerceptionKind.AUDIO_SPEECH_RAPID, score,
                conf=self._clamp(0.8 + score * 0.2), ts=timestamp, seg=segment_id,
            )

        return None

    # ---- 内部 ----

    def _mk(
        self, kind: AudioPerceptionKind, score: float, conf: float, ts: float, seg: str
    ) -> AudioPerceptionEvent | None:
        score = self._clamp(score)
        if score < self.t.activation:
            return None
        return AudioPerceptionEvent(
            event_id=new_event_id(),
            timestamp=ts,
            kind=kind,
            score=score,
            confidence=self._clamp(conf),
            source_segment_ids=[seg],
            labels=self._labels_for(kind),
        )

    @staticmethod
    def _labels_for(kind: AudioPerceptionKind) -> list[str]:
        mapping = {
            AudioPerceptionKind.AUDIO_VOICE_RAISED: ["speech", "loud"],
            AudioPerceptionKind.AUDIO_SPEECH_RAPID: ["speech", "rapid"],
            AudioPerceptionKind.AUDIO_TELEPHONE_PERSISTENT: ["speech", "telephone"],
            AudioPerceptionKind.AUDIO_DISTRESS_CRY: ["speech", "distress"],
            AudioPerceptionKind.AUDIO_ANOMALY_OTHER: ["anomaly"],
        }
        return list(mapping.get(kind, []))

    @staticmethod
    def _norm(x: float, lo: float, hi: float) -> float:
        """把 [lo, hi] 线性映射到 [0.5, 1.0]（命中即至少 0.5 强度）。"""
        if hi <= lo:
            return 1.0
        ratio = (x - lo) / (hi - lo)
        return 0.5 + min(1.0, max(0.0, ratio)) * 0.5

    @staticmethod
    def _clamp(x: float) -> float:
        return min(1.0, max(0.0, x))
