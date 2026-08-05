"""音频感知事件模型（ADR-0026 §4 · 对外契约，MINOR 扩展，不破 ADR-0014 冻结）。

> **ADR-0026 = 音频感知链路具体设计。** 本模块定义音频域的两条事件：
> - ``AudioSegmentEvent``：事实层，纯音频域（分段 + 声学特征），不持有任何跨模态字段。
> - ``AudioPerceptionEvent``：语义层，5 类声学感知（``AudioPerceptionKind``），
>   对应视觉侧 ``PerceptionEvent`` 的音频同位体。
>
> **边界铁律（ADR-0001 / ADR-0002）**：事件不携带 ``fraud`` / ``suspect`` / ``verdict``
> 等犯罪认定字段；音频只产 **perception**，不产风险结论（结论由中心 / ``DecisionPolicy`` 综合）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .tagging import AudioTag

# ============================================================================
# 枚举（严格白名单，禁止自由文本 —— 与视觉侧 EventType 同构）
# ============================================================================


class AudioPerceptionKind(str, Enum):
    """音频语义层 5 类声学感知（ADR-0026 §4.2）。

    命名守 ADR-0001：只描述"声学感知"，不暗示"诈骗 / 犯罪"。
    """

    AUDIO_SPEECH_RAPID = "audio_speech_rapid"  # 急促言语
    AUDIO_VOICE_RAISED = "audio_voice_raised"  # 高声 / 争吵
    AUDIO_TELEPHONE_PERSISTENT = "audio_telephone_persistent"  # 异常/持续通话
    AUDIO_DISTRESS_CRY = "audio_distress_cry"  # 哭诉 / 求助声
    AUDIO_ANOMALY_OTHER = "audio_anomaly_other"  # 其他异常声学信号


# 枚举闭合性基线（契约测试逐值校验，防未来误删/扩值漂移）
AUDIO_PERCEPTION_KIND_VALUES: tuple = tuple(e.value for e in AudioPerceptionKind)

# AudioPerceptionEvent 顶层禁止出现的犯罪认定 / 判定字段（模块边界铁律）
FORBIDDEN_AUDIO_FIELDS: frozenset = frozenset(
    {
        "fraud_result",
        "fraud_probability",
        "is_fraud",
        "is_scammer",
        "is_criminal",
        "verdict",
        "final_decision",
        "crime_probability",
        "guilt_score",
        "deception_score",
    }
)


# ============================================================================
# AudioSegmentEvent —— 事实层（纯音频域）
# ============================================================================


@dataclass
class AudioSegmentEvent:
    """音频事实层事件（ADR-0026 §4.1）。

    只描述音频自身的事实（分段 + 声学特征），不持有视觉域 / 其他传感器域状态
    （架构纯度约束，见 ADR-0026 §4.1 / §6）。

    字段：
    - ``segment_id``：分段唯一 ID（uuid4 字符串）
    - ``timestamp``：分段起点 Unix 秒
    - ``duration``：分段时长（秒）
    - ``vad_ratio``：语音占比 0~1（来自 VAD 后端）
    - ``rms``：均方根振幅（响度代理）
    - ``speech_rate``：语速代理指标（音节/能量峰率，syllables-per-second 近似）
    - ``labels``：来自 Tier0/1 的声学标签，如 ["speech","telephone"]
    """

    segment_id: str
    timestamp: float
    duration: float
    vad_ratio: float
    rms: float
    speech_rate: float
    labels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not (0.0 <= self.vad_ratio <= 1.0):
            raise ValueError(f"vad_ratio 必须在 [0, 1]，收到 {self.vad_ratio}")
        if self.rms < 0:
            raise ValueError(f"rms 必须 >= 0，收到 {self.rms}")
        if self.speech_rate < 0:
            raise ValueError(f"speech_rate 必须 >= 0，收到 {self.speech_rate}")
        if self.duration < 0:
            raise ValueError(f"duration 必须 >= 0，收到 {self.duration}")


# ============================================================================
# AudioPerceptionEvent —— 语义层（5 类声学感知）
# ============================================================================


@dataclass
class AudioPerceptionEvent:
    """音频语义层事件（ADR-0026 §4.2）。

    字段：
    - ``event_id``：事件唯一 ID（uuid4 字符串）
    - ``timestamp``：事件 Unix 秒
    - ``kind``：5 类声学感知之一（``AudioPerceptionKind``）
    - ``score``：规则强度（0~1）—— **不是诈骗概率**（类比视觉 ``PerceptionEvent.score``）
    - ``confidence``：检测可信度（0~1）：模型/特征对该 segment 判定的把握
    - ``source_segment_ids``：派生自哪些 ``AudioSegmentEvent``
    - ``labels``：声学标签透传（去重 + 字母序排序的**集合**，顺序不具语义，评审 2.4）
    - ``scored_labels``：Tier1 声学标签 + 置信分 ``list[AudioTag]``（保留 score 供下游阈值/审计，评审 1.5；
      无 Tier1 时为空；属 MINOR 扩展，不破 ADR-0014 冻结）

    ``score`` vs ``confidence``（评审新增的语义区分）：
    - ``score`` = "这条声学风险有多强"（语速越快 score 越高）
    - ``confidence`` = "这个判定有多可信"（噪声下 score 高但 confidence 低）
    下游 ``RiskSignal`` / ``DecisionPolicy`` 据此区分"强但不可信"与"弱但确凿"。
    """

    event_id: str
    timestamp: float
    kind: AudioPerceptionKind
    score: float
    confidence: float
    source_segment_ids: list[str]
    labels: list[str] = field(default_factory=list)
    scored_labels: list[AudioTag] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        # kind 枚举归一 + 闭合校验
        if not isinstance(self.kind, AudioPerceptionKind):
            try:
                self.kind = AudioPerceptionKind(self.kind)
            except ValueError as exc:
                valid = ", ".join(repr(v) for v in AUDIO_PERCEPTION_KIND_VALUES)
                raise ValueError(
                    f"kind 必须是 AudioPerceptionKind 之一，收到 {self.kind!r}；合法值：{valid}"
                ) from exc
        # score / confidence 范围
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score 必须在 [0, 1]，收到 {self.score}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 必须在 [0, 1]，收到 {self.confidence}")
        # 黑名单字段结构性保证
        forbidden = FORBIDDEN_AUDIO_FIELDS.intersection(self.__dict__.keys())
        if forbidden:
            raise ValueError(f"AudioPerceptionEvent 含禁止字段 {forbidden}")

    def to_dict(self) -> dict[str, Any]:
        """structlog-safe 字典（枚举转 value、datetime 转 ISO 字符串）。"""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "kind": self.kind.value,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "source_segment_ids": list(self.source_segment_ids),
            "labels": list(self.labels),
            "scored_labels": [
                {"label": t.label, "score": round(t.score, 4)} for t in self.scored_labels
            ],
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        """JSON 序列化（日志归档 / 跨进程传递用）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioPerceptionEvent:
        """从 to_dict() 产出的字典反序列化（与 to_dict() 严格对称）。"""
        return cls(
            event_id=data["event_id"],
            timestamp=data["timestamp"],
            kind=AudioPerceptionKind(data["kind"]),
            score=data["score"],
            confidence=data["confidence"],
            source_segment_ids=list(data["source_segment_ids"]),
            labels=list(data.get("labels", [])),
            scored_labels=[AudioTag(**d) for d in data.get("scored_labels", [])],
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    @classmethod
    def from_json(cls, json_str: str) -> AudioPerceptionEvent:
        """从 to_json() 产出的 JSON 字符串反序列化。"""
        return cls.from_dict(json.loads(json_str))


def new_event_id() -> str:
    """生成事件/分段唯一 ID（uuid4 字符串）。"""
    return str(uuid.uuid4())
