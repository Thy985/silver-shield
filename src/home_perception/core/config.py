"""配置加载与校验。

- 从 YAML 读取默认配置（config/default.yaml）
- 支持 ${ENV_VAR:-default} 形式引用环境变量（凭证走 .env，不入库）
- 使用 pydantic 做结构化校验，缺失项回退到默认值
"""
from __future__ import annotations

import math
import os
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

if TYPE_CHECKING:
    # 仅类型标注用，避免 core 在加载期 eager 引入 analysis（运行期仍走方法内懒导入）
    from ..analysis.rule_engine import ThresholdConfig


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def _repl(m: re.Match) -> str:
            var, default = m.group(1), m.group(2)
            return os.environ.get(var, default if default is not None else "")

        return _ENV_PATTERN.sub(_repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_logs: bool = True


class ReconnectConfig(BaseModel):
    max_retries: int = 5
    backoff_s: int = 3


class IngestionConfig(BaseModel):
    protocol: str = "rtsp"  # rtsp=低延迟(默认) | hls=已验证回退
    quality: int = 1
    channel_no: int = 1
    reconnect: ReconnectConfig = ReconnectConfig()
    fps_target: int = 8

    # 枚举约束（用户建议 · ADR-0014 前置 #5 扩展）：protocol 即"帧源传输类型"，
    # 未知 source 不应静默接受（P0-12 接入 RTSPSource/EZVIZSource 时在此扩展白名单）。
    @field_validator("protocol")
    @classmethod
    def _known_protocol(cls, v: str) -> str:
        if v not in ("rtsp", "hls"):
            raise ValueError(f"ingestion.protocol 必须是 rtsp 或 hls，收到 {v!r}")
        return v


class ImgszProfile(str, Enum):
    """推理分辨率预设（见 docs/09 / P0-4 实测结论）。

    P0-4 在 CPU 边缘机实测（yolo11n / 1080p 合成帧）：
    - accuracy(640)：推理 ~124ms / ~8FPS —— 精度收益有限但延迟高，不适合实时
    - balanced(480)：推理 ~86ms / ~11.6FPS —— 满足 <100ms 且 >10FPS，门前场景折中
    - realtime(416)：推理 ~47ms / ~21.5FPS —— 裕量大，但 cell phone 等小目标精度下降
    """

    ACCURACY = "accuracy"
    BALANCED = "balanced"
    REALTIME = "realtime"

    @property
    def imgsz(self) -> int:
        return {"accuracy": 640, "balanced": 480, "realtime": 416}[self.value]

    @classmethod
    def resolve(cls, profile: "ImgszProfile | str | None", explicit_imgsz: Optional[int]) -> int:
        """解析最终 imgsz：显式 imgsz 优先；否则用 profile；再否则回退 balanced(480)。"""
        if explicit_imgsz:
            return int(explicit_imgsz)
        if profile is None:
            return cls.BALANCED.imgsz
        if isinstance(profile, ImgszProfile):
            return profile.imgsz
        try:
            return cls(str(profile).lower()).imgsz
        except ValueError:
            return cls.BALANCED.imgsz


class TrackingConfig(BaseModel):
    """P0-5 跨帧跟踪配置（见 Owner 决策：固定摄像头/单区域/CPU/停留分析 → bytetrack 足够）。

    - enabled：是否开启跨帧跟踪（回填 track_id）。关闭时 detector 走 predict、VisitorTrack 无 ID。
    - algorithm：跟踪算法。MVP 用 bytetrack（轻量、无 ReID，契合固定单摄 CPU 部署）；
      botsort 的 ReID 价值（人离开又回来/长遮挡/多摄）不在 MVP 范围。
    - absence_gap_s：离场判定宽限（见 detection.tracker.DEFAULT_ABSENCE_GAP_S 注释）；
      同 track_id 连续该秒数未出现 → 视为本次在场 visit 结束。
    """
    enabled: bool = True
    algorithm: str = "bytetrack"  # bytetrack | botsort
    absence_gap_s: float = 5.0  # 离场判定宽限（容忍漏检闪烁）

    @field_validator("absence_gap_s")
    @classmethod
    def _absence_gap_positive(cls, v: float) -> float:
        # 配置攻击防护（ADR-0014 前置 #5）：负值 / NaN 必须明确报错，不得静默运行
        if isinstance(v, float) and math.isnan(v):
            raise ValueError("absence_gap_s 不能是 NaN")
        if v <= 0:
            raise ValueError(f"absence_gap_s 必须 > 0，收到 {v!r}")
        return v


class DetectionConfig(BaseModel):
    model: str = "yolo11n.pt"  # 第一阶段默认小模型：CPU 可跑、延迟低
    conf_threshold: float = 0.45
    # 仅第一阶段 4 类：person / backpack / handbag / cell phone（COCO id）
    classes: list[int] = Field(default_factory=lambda: [0, 24, 26, 67])
    device: str = "cpu"  # cpu | cuda:0
    # P0-4 实测结论：纯 CPU 边缘机 yolo11n@640 推理 ~124ms 未达实时目标；
    # MVP 默认 480（balanced）满足 <100ms 且 >10FPS。详见 docs/09。
    imgsz: int = ImgszProfile.BALANCED.imgsz  # 480
    imgsz_profile: ImgszProfile = ImgszProfile.BALANCED  # accuracy=640 / balanced=480 / realtime=416
    tracking: TrackingConfig = TrackingConfig()  # P0-5 跨帧跟踪（默认开启 bytetrack）
    enable_track: bool = True  # 向后兼容：值优先取自 tracking.enabled
    tracker: str = "bytetrack"  # 向后兼容：值优先取自 tracking.algorithm


class AnalysisConfig(BaseModel):
    dwell_threshold_s: int = 30
    odd_hour_start: int = 23
    odd_hour_end: int = 6
    cooldown_s: int = 60


class EvidenceConfig(BaseModel):
    store: str = "local"
    local_dir: str = "data/evidence"
    clip_seconds: int = 10
    snapshot: bool = True


class MqttConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 1883
    topic: str = "silvershield/home/{device_id}/events"


class BufferConfig(BaseModel):
    enabled: bool = True
    max_items: int = 200


class OutputConfig(BaseModel):
    transport: str = "mqtt"
    mqtt: MqttConfig = MqttConfig()
    buffer: BufferConfig = BufferConfig()


class FamilyContactConfig(BaseModel):
    """家属联系方式（P0-10 行动层；MVP 从 config 读，v2 从中心 RiskTwin 拉）。"""
    elder_id: str
    name: str = ""
    phone: str = ""
    relation: str = "family"


class RuleConfig(BaseModel):
    """P0-10 运行时规则阈值配置；直接映射到 analysis.rule_engine.ThresholdConfig。

    把阈值/权重集中到 YAML，规则逻辑不硬编码（ADR-0009 Decision 6：阈值配置化）。
    """

    long_duration_seconds: float = 300.0
    repeat_visit_count: int = 3
    odd_hour_set: List[int] = Field(default_factory=lambda: [23, 0, 1, 2, 3, 4])
    cooldown_seconds: float = 600.0
    reset_gap_seconds: float = 1800.0
    frequency_window_s: float = 1800.0
    high_risk_required_rules: List[str] = Field(
        default_factory=lambda: ["LongDurationRule", "RepeatVisitRule", "OddHourRule"]
    )
    rule_weights: Dict[str, float] = Field(
        default_factory=lambda: {
            "LongDurationRule": 0.50,
            "RepeatVisitRule": 0.30,
            "OddHourRule": 0.10,
            "HighRiskApproachRule": 0.90,
        }
    )

    # 配置攻击防护（ADR-0014 前置 #5）：阈值 / 计数必须 > 0 且非 NaN，
    # 否则规则层会静默流入负时长 / 负窗口，污染下游 Feature / Rule / ML。
    @field_validator(
        "long_duration_seconds",
        "cooldown_seconds",
        "reset_gap_seconds",
        "frequency_window_s",
    )
    @classmethod
    def _positive_float_threshold(cls, v: float) -> float:
        if isinstance(v, float) and math.isnan(v):
            raise ValueError(f"阈值配置不能是 NaN，收到 {v!r}")
        if v <= 0:
            raise ValueError(f"阈值配置必须 > 0，收到 {v!r}")
        return v

    # 类型防护（用户建议）：bool 是 int 的子类，pydantic 会把 True/False 静默当作 1/0，
    # 语义上是类型错误。用 before 校验器在强转前显式拒绝 bool（after 模式此时已丢失原类型）。
    @field_validator("repeat_visit_count", mode="before")
    @classmethod
    def _reject_bool_count(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError(f"repeat_visit_count 必须是整数，收到 bool {v!r}")
        return v

    @field_validator("repeat_visit_count")
    @classmethod
    def _positive_int_count(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"repeat_visit_count 必须 > 0，收到 {v!r}")
        return v

    # 范围约束（用户建议 · ADR-0014 前置 #5 扩展）：权重即"规则命中强度占比"，
    # 语义上必须在 [0, 1]；越界（如 2.5）或 NaN 必须明确报错，不得静默流入规则层。
    @field_validator("rule_weights")
    @classmethod
    def _weights_in_unit_range(cls, v: "Dict[str, float]") -> "Dict[str, float]":
        for name, w in v.items():
            if isinstance(w, float) and math.isnan(w):
                raise ValueError(f"rule_weights[{name!r}] 不能是 NaN，收到 {w!r}")
            if not (0.0 <= w <= 1.0):
                raise ValueError(f"rule_weights[{name!r}] 必须在 [0, 1]，收到 {w!r}")
        return v

    def to_threshold_config(self) -> "ThresholdConfig":
        """转换为 RuleEngine 内部阈值配置（懒导入，避免 core→analysis 加载期耦合）。"""
        from ..analysis.rule_engine import ThresholdConfig

        return ThresholdConfig(
            long_duration_seconds=self.long_duration_seconds,
            repeat_visit_count=self.repeat_visit_count,
            odd_hour_set=set(self.odd_hour_set),
            cooldown_seconds=self.cooldown_seconds,
            reset_gap_seconds=self.reset_gap_seconds,
            high_risk_required_rules=set(self.high_risk_required_rules),
            rule_weights=dict(self.rule_weights),
        )


class DecisionConfig(BaseModel):
    """P0-10 决策层配置。MVP 仅 rule_based（RuleBasedDecisionPolicy）。

    v2 可扩展 ml / llm 策略（替换 DecisionPolicy 实现，不改变 WarningEvent 契约）。
    """

    policy: str = "rule_based"  # rule_based | (v2: ml, llm)


class ActionConfig(BaseModel):
    """P0-10 行动层配置（MVP 用 Mock；v1 接真实 paho-mqtt / 短信网关 / 社区工单）。"""

    family_contact: Optional[FamilyContactConfig] = None
    community_endpoint: Optional[str] = None
    mqtt_topic_prefix: str = "silvershield/home"
    max_retries: int = 3
    # MockPublisher 落盘 JSONL 路径；None = 仅内存收集（不落盘）
    mock_publisher_output: Optional[str] = None


class RuntimeConfig(BaseModel):
    """P0-10 运行模式配置。MVP 仅 demo（CAVIAR fixtures）；realtime 留待 v1。

    demo 模式不接真实萤石摄像头（Owner 决策：比赛 Demo 用公开数据集复现，隐私安全）。
    """

    mode: str = "demo"  # demo | realtime (realtime 留待 v1)
    caviar_base_dir: str = "tests/fixtures/doorway"
    frame_glob: str = "frame_*.jpg"
    demo_scenarios: List[str] = Field(
        default_factory=lambda: [
            "one_stop_enter",
            "one_leave_reenter",
            "meet_walk_together",
        ]
    )
    # 覆盖 detection 段（demo 可用更轻量模型/分辨率提速）
    detector_model: Optional[str] = None
    detector_imgsz: Optional[int] = None
    detector_conf: Optional[float] = None
    # Demo 模拟时钟起点（ISO 8601，必须带时区）。默认 23:30 UTC：
    # 让 OddHourRule 在 CAVIAR 短片（~25s）自然触发，同时把 Demo 时间线交由 YAML 控制，
    # 更换场景 / 调整异常时段无需改源码（见 ADR-0013）。
    demo_clock_start: str = "2026-07-19T23:30:00+00:00"


class Settings(BaseModel):
    logging: LoggingConfig = LoggingConfig()
    ingestion: IngestionConfig = IngestionConfig()
    detection: DetectionConfig = DetectionConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    evidence: EvidenceConfig = EvidenceConfig()
    output: OutputConfig = OutputConfig()
    rule: RuleConfig = RuleConfig()
    decision: DecisionConfig = DecisionConfig()
    action: ActionConfig = ActionConfig()
    runtime: RuntimeConfig = RuntimeConfig()

    @classmethod
    def load(cls, path: str | os.PathLike = "config/default.yaml") -> "Settings":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw = _expand_env(raw)
        return cls(**raw)
