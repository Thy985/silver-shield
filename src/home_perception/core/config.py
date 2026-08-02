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
from typing import TYPE_CHECKING, Any

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
    def resolve(cls, profile: ImgszProfile | str | None, explicit_imgsz: int | None) -> int:
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
    imgsz_profile: ImgszProfile = (
        ImgszProfile.BALANCED
    )  # accuracy=640 / balanced=480 / realtime=416
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
    odd_hour_set: list[int] = Field(default_factory=lambda: [23, 0, 1, 2, 3, 4])
    cooldown_seconds: float = 600.0
    reset_gap_seconds: float = 1800.0
    frequency_window_s: float = 1800.0
    high_risk_required_rules: list[str] = Field(
        default_factory=lambda: ["LongDurationRule", "RepeatVisitRule", "OddHourRule"]
    )
    rule_weights: dict[str, float] = Field(
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
            raise ValueError(f"repeat_visit_count 必须是整数，收到 bool {v!r}")  # noqa: TRY004  # pydantic validator 走 ValueError 转 ValidationError
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
    def _weights_in_unit_range(cls, v: dict[str, float]) -> dict[str, float]:
        for name, w in v.items():
            if isinstance(w, float) and math.isnan(w):
                raise ValueError(f"rule_weights[{name!r}] 不能是 NaN，收到 {w!r}")
            if not (0.0 <= w <= 1.0):
                raise ValueError(f"rule_weights[{name!r}] 必须在 [0, 1]，收到 {w!r}")
        return v

    def to_threshold_config(self) -> ThresholdConfig:
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

    family_contact: FamilyContactConfig | None = None
    community_endpoint: str | None = None
    mqtt_topic_prefix: str = "silvershield/home"
    max_retries: int = 3
    # MockPublisher 落盘 JSONL 路径；None = 仅内存收集（不落盘）
    mock_publisher_output: str | None = None


class RuntimeConfig(BaseModel):
    """P0-10 运行模式配置。MVP 仅 demo（CAVIAR fixtures）；realtime 留待 v1。

    demo 模式不接真实萤石摄像头（Owner 决策：比赛 Demo 用公开数据集复现，隐私安全）。
    """

    mode: str = "demo"  # demo | realtime (realtime 留待 v1)
    caviar_base_dir: str = "tests/fixtures/doorway"
    frame_glob: str = "frame_*.jpg"
    demo_scenarios: list[str] = Field(
        default_factory=lambda: [
            "one_stop_enter",
            "one_leave_reenter",
            "meet_walk_together",
        ]
    )
    # 覆盖 detection 段（demo 可用更轻量模型/分辨率提速）
    detector_model: str | None = None
    detector_imgsz: int | None = None
    detector_conf: float | None = None
    # Demo 模拟时钟起点（ISO 8601，必须带时区）。默认 23:30 UTC：
    # 让 OddHourRule 在 CAVIAR 短片（~25s）自然触发，同时把 Demo 时间线交由 YAML 控制，
    # 更换场景 / 调整异常时段无需改源码（见 ADR-0013）。
    demo_clock_start: str = "2026-07-19T23:30:00+00:00"


class RealtimeRiskConfig(BaseModel):
    """实时风险状态流开关（ADR-0021 · Migration Stage B 起）。

    只放开关与实时特有项；**阈值不在此重复**（复用 ``rule`` 段，单一阈值来源）。
    默认 ``enabled=false``——Stage B 起 main 合并后默认关闭，Demo 场景经
    ``ScenarioConfig.rule_overrides`` 同型通道或 YAML 显式开启。

    - ``enabled``：Feature Flag。关闭时 ``from_settings`` **不构造**实时组件，
      ``process_frame`` 跳过旁路块（零运行时开销，边缘 CPU 友好）。
    - ``eval_interval_frames``：每 N 帧评估一次（性能旋钮）。语义见工程方案 §5.3：
      RAISED/CLEARED **对称延迟**，最坏延迟 ≈ N×帧间隔；评估帧消费
      ``tracker.active()`` 全量在场主体（非增量）。
    - ``decision_enabled``：Stage D 决策接入开关。默认 ``false``——Stage C
      Shadow Mode（只产信号不接决策）即使 ``enabled=true`` 也不产 Warning；
      ``decision_enabled=true`` 时 RAISED 信号经 ``signal_adapter`` 翻译为
      ``PerceptionEvent`` 汇入 ``DecisionEngine`` 产 ``WarningEvent``。
      **灰度策略**：先 ``enabled=true, decision_enabled=false`` 观察误报率
      （Shadow Mode），数据可信后再 ``decision_enabled=true`` 接决策。
      ``decision_enabled=true`` 隐含要求 ``enabled=true``（关闭态下此开关无意义）。
    """

    enabled: bool = False
    eval_interval_frames: int = 1
    decision_enabled: bool = False

    @field_validator("eval_interval_frames", mode="before")
    @classmethod
    def _positive_int(cls, v):
        # mode="before"：在 pydantic 把 bool 强转 int 之前拦截（bool 是 int 子类，
        # 默认 mode 会把 True 当 1 通过，掩盖配置类型错误）
        if isinstance(v, bool):
            raise ValueError(f"eval_interval_frames 必须是整数，收到 bool {v!r}")  # noqa: TRY004  # pydantic validator 走 ValueError
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"eval_interval_frames 必须是 int，收到 {type(v).__name__} {v!r}")  # noqa: TRY004  # pydantic validator 走 ValueError
        if v < 1:
            raise ValueError(f"eval_interval_frames 必须 >= 1，收到 {v!r}")
        return v


class MemoryConfig(BaseModel):
    """ADR-0024 Slice 3（Stage C + Stage E）Snapshot Recovery 配置。

    - ``enabled``：Memory 子系统总开关（含 Slice 3 Snapshot Recovery）。默认 ``false``
      ——Slice 3 合并后默认关闭，经 YAML ``memory.enabled: true`` 显式开启。
    - ``episodic_shadow``：ADR-0024 Slice 5 · Stage F Episodic Memory 影子写入开关。
      默认 ``false``——**Stage F Shadow Mode 默认关闭，v1 不产 Warning**。仅当
      ``memory.enabled=true`` **且** ``episodic_shadow=true`` 时，流水线才把每次访客离场
      经 ``DefaultEpisodeBuilder.project_episode`` 投影为 ``EpisodicRecord`` 并写入
      ``InMemoryStore``。影子写入只记录、不接决策、不产 Warning；是否开启与 Snapshot
      Recovery 相互独立（可仅开快照恢复，不落 Episode）。
    - ``consumer_enabled``：ADR-0025 C-4 Memory Consumer 接入开关。默认 ``false``
      ——**默认关闭，零行为变化**。仅当 ``memory.enabled`` **且** ``episodic_shadow``
      **且** ``consumer_enabled`` 三者同时为真时，流水线才在每次访客离场按模式 B
      门控（MEDIUM+ 或已知访客再现）召回历史并组装 ``ReasoningInput``。消费侧
      **只读、不决策、不产 Warning**（守 ADR-0010）；依赖 ``episodic_shadow`` 是因为
      ``MemoryStore`` 仅在影子写入激活时才被构造——没有历史可读时消费无意义。
    - ``snapshot_path``：JSON 持久化路径（原子写：先 .tmp 再 os.replace）。
    - ``snapshot_interval_seconds``：周期快照间隔（默认 30s）；写入时机见工程方案 §5.3.6。
    - ``snapshot_fresh_threshold_seconds``：FRESH/STALE 分界（默认 30s）。
    - ``snapshot_ttl_seconds``：STALE/DISCARD 分界（默认 300s=5min）；超过则冷启动。
    - ``recent_behavior_retention_seconds``：恢复时只保留 ``last_seen_at`` 在窗口内的
      visitor（默认 3600s=1h），避免 TD-0024 旧条目累积重现。
    - ``eviction_interval_frames``：每 N 帧内联 ``evict_expired()``（Stage D，默认 60）。
    - ``cold_start_stale_confidence``：STALE 档恢复 confidence 值（默认 0.5）。
    """

    enabled: bool = False
    # Stage F（Slice 5）Episodic Memory 影子写入开关；默认关闭（v1 不产 Warning）。
    episodic_shadow: bool = False
    # ADR-0025 C-4 Memory Consumer 接入开关；默认关闭（消费侧只读、不决策）。
    consumer_enabled: bool = False
    snapshot_path: str = "data/memory/snapshot.json"
    snapshot_interval_seconds: float = 30.0
    snapshot_fresh_threshold_seconds: float = 30.0
    snapshot_ttl_seconds: float = 300.0
    recent_behavior_retention_seconds: float = 3600.0
    eviction_interval_frames: int = 60
    cold_start_stale_confidence: float = 0.5

    @field_validator(
        "snapshot_interval_seconds",
        "snapshot_fresh_threshold_seconds",
        "snapshot_ttl_seconds",
        "recent_behavior_retention_seconds",
    )
    @classmethod
    def _non_negative_seconds(cls, v: float) -> float:
        # 配置攻击防护（ADR-0014 前置 #5）：负时长必须明确报错
        if isinstance(v, float) and math.isnan(v):
            raise ValueError(f"时长配置不能是 NaN，收到 {v!r}")
        if v < 0:
            raise ValueError(f"时长配置必须 >= 0，收到 {v!r}")
        return v

    @field_validator("snapshot_path")
    @classmethod
    def _non_empty_path(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("snapshot_path 不能为空")
        return v

    @field_validator("cold_start_stale_confidence")
    @classmethod
    def _confidence_unit_range(cls, v: float) -> float:
        if isinstance(v, float) and math.isnan(v):
            raise ValueError(f"cold_start_stale_confidence 不能是 NaN，收到 {v!r}")
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"cold_start_stale_confidence 必须在 [0, 1]，收到 {v!r}")
        return v


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
    realtime_risk: RealtimeRiskConfig = RealtimeRiskConfig()
    memory: MemoryConfig = MemoryConfig()

    @classmethod
    def load(cls, path: str | os.PathLike = "config/default.yaml") -> Settings:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw = _expand_env(raw)
        return cls(**raw)
