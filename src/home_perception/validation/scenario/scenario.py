"""ADR-0032 Slice A：声明式 ``Scenario`` schema + YAML 加载器（零行为变化）。

单一声明式场景描述派生两条生成通道（D1）：
- ``mode: detections`` → 复用现有 ``Detection`` 的 ``list[list[Detection]]``（通道一）
- ``mode: frames``      → OpenCV 程序化 BGR 帧 ``list[np.ndarray]``（通道二）

两层校验（fail-closed）：
1. **加载期必填** ``meta.schema_version`` / ``scenario_id`` / ``version``（资产身份，T10）
   + 结构合法（``camera.resolution`` / ``fps`` 必填、``actors[].tracks`` 帧序严格递增不越界、
   ``actor_type`` 枚举合法、``schema_version`` 已知）。
2. **生成期必填** ``meta.seed`` / ``meta.duration_frames``（仅当实际 ``synthesize`` 才需要）。

本模块只做"加载 + 校验 + 提供数据模型"，**不** import 任何生成器 / 渲染器 / 业务规则层，
保证零生产行为变化（D3）。
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from home_perception.validation.contracts import (
    BenchmarkExpectation,
    IntegrationExpectationSuite,
)

# 已知 Schema 格式版本（未知版本拒绝加载，fail-closed，防旧场景被新 renderer 静默误读）。
KNOWN_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0"})

# actor 实体类型白名单（解耦 "实体=人"；仅作生成 class_name 的语义提示，不参与业务判定）。
ACTOR_TYPES: tuple[str, ...] = ("human", "vehicle", "pet", "object")


# ============================================================================
# 子模型
# ============================================================================


class RegionSpec(BaseModel):
    """命名区域（抽象分类标签，非真实户型；与运行时 ROI 不强制一一对应，见 ADR-0032 B3/S1）。"""

    name: str
    # 归一化 bbox [x1, y1, x2, y2]（0~1），仅用于 generator 几何放置与语义表达。
    bbox: list[float] = Field(min_length=4, max_length=4)


class StaticObjectSpec(BaseModel):
    """场景静态物体（门 / 桌 / 柜等几何占位，不参与事件）。"""

    type: str
    bbox: list[float] = Field(min_length=4, max_length=4)


class EnvironmentSpec(BaseModel):
    """环境：拍的是哪类空间（与 camera 分离的抽象）。"""

    scene_type: str = "home_entry"
    regions: dict[str, list[float]] = Field(default_factory=dict)
    static_objects: list[StaticObjectSpec] = Field(default_factory=list)


class CameraSpec(BaseModel):
    """镜头：怎么拍（与 environment 分离）。"""

    resolution: list[int] = Field(min_length=2, max_length=2)  # [w, h]
    fps: float = 2.0
    viewpoint: str = ""


class PriorEpisodeSpec(BaseModel):
    """G0-3 历史记忆预置声明（跨日 prior episode，`EpisodicRecord` 的确定性输入）。

    字段对齐 `EpisodicRecord` 核心身份/时间/语义字段：
    - ``episode_id``：历史 episode 标识（如 ``historical_001``）；落库 record_id =
      ``ep-prior-<episode_id>``（前缀防与运行时 episode 冲突，I1 幂等）；
    - ``event_time``：历史事件时间（Unix 秒，**跨日**的时间真相源，如 3 days ago）；
    - ``visitor_id``：同一访客身份（决策检索 `get_episodic_by_visitor` 按此命中）；
    - ``risk_level`` / ``recommended_action`` / ``reason_summary`` / ``summary``：
      历史 episode 的决策侧语义（供决策层"历史模式重复 → 风险升级"引用）。

    只作**输入**（预置进 MemoryStore）；不参与场景自身感知合成（与 `expects` /
    `integration` 语义分离）。
    """

    episode_id: str
    event_time: float  # Unix 秒（UTC，跨日历史时间真相源）
    visitor_id: str
    duration_seconds: float = 30.0
    risk_level: str = "LOW"
    recommended_action: str = "MONITOR"
    reason_summary: list[str] = Field(default_factory=list)
    summary: str = ""
    device_id: str = "home_entry"
    modalities: list[str] = Field(default_factory=lambda: ["VISION"])


class TrackKeyframe(BaseModel):
    """轨迹关键帧：``frame`` 处的中心位置与尺寸。"""

    frame: int
    pos: list[float] = Field(min_length=2, max_length=2)  # [cx, cy] 像素
    size: list[float] = Field(min_length=2, max_length=2)  # [w, h] 像素


class ActorSpec(BaseModel):
    """场景实体（几何：轨迹 + 外观基元；不含语义角色/行为，见 ADR-0032 §3 EntitySpec 演化）。"""

    id: str
    actor_type: str  # human | vehicle | pet | object
    tracks: list[TrackKeyframe] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    appearance: dict[str, Any] = Field(default_factory=dict)


class EventGroundTruth(BaseModel):
    """期望 emit 的事件（ground truth，供 ``ScenarioValidator`` 对照）。"""

    frame: int
    type: str


class AudioEventSpec(BaseModel):
    """跨模态集成场景的音频感知事件声明（ADR-0034 Phase B.2：让闭环驱动音频会话）。

    仅描述「一段音频会话里有哪些声学感知事件」，由 ``ScenarioCompiler`` 编译为
    正式 ``AudioPerceptionEvent``（``created_at`` 由 ``timestamp`` 推导，确定性）。
    不新增音频模型 / 契约——完全复用 ``home_perception.audio.event`` 既有事件。

    时间约束（关联边成立的物理前提，ADR-0028 D1/D3）：``timestamp`` 必须落在会话窗口
    内（``>= clock_start`` 且 ``<= clock_start + duration_frames * frame_interval``）。
    建议声明「首帧 + 末帧」两枚事件，使音频 episode 时间窗跨度覆盖全会话，
    与视觉 episode 时间窗严格重叠，保证 ``CrossModalLinker`` 建边。
    """

    kind: str  # AudioPerceptionKind 值，如 ``audio_telephone_persistent``
    timestamp: float  # Unix 秒，落在会话窗口内（决定 episode 时间窗与重叠）
    score: float = 0.9  # 规则强度 0~1（非诈骗概率）
    confidence: float = 0.9  # 检测可信度 0~1
    source_segment_ids: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    event_id: str | None = None  # 缺省由编译器确定性生成（``aev-{i}``）


class ExpectsSpec(BaseModel):
    """``ScenarioValidator`` 的机器可校验期望（D1 / T6）。"""

    emitted_event_types: list[str] = Field(default_factory=list)
    # 可选风险等级下界（复用 analysis/warning.py 的 RISK_LEVELS 序值，见 ADR-0032 B4）。
    min_risk_level: str | None = None
    max_suppress_rate: float | None = None


class MetaSpec(BaseModel):
    """场景资产身份 + 内容版本化（三层版本化，T10）。"""

    schema_version: str  # Schema 格式版本（加载期必填）
    scenario_id: str  # 稳定资产标识（加载期必填）
    version: int  # 场景内容修订号（加载期必填）
    description: str = ""
    seed: int | None = None  # 生成期必填（仅 synthesize 时需要）
    duration_frames: int | None = None  # 生成期必填
    # —— 运行时确定性覆盖（opt-in；缺省 None = 用闭环装配默认值，向后兼容）——
    # ``clock_start``：会话时钟起点（Unix 秒，UTC）。缺省 None 时由 runner 用
    # ``IntegrationRunnerConfig.clock_start`` 默认值；声明后覆盖为该值——用于让
    # 异常时段（odd_hour_set）等依赖"当天几点"的规则在确定性下可触发（如
    # adr0034_high_risk 场景声明 22:59:30，使第三次访问跨过 23:00 落入异常时段）。
    # 语义与 ``audio[].timestamp``（同为 Unix 秒）一致，保证时钟/音频时间线可比。
    # ``rule_overrides``：覆盖 ``ThresholdConfig`` 字段（如 ``long_duration_seconds`` /
    # ``repeat_visit_count`` / ``cooldown_seconds``），键必须是 ThresholdConfig 既有字段，
    # 未知键由 run_integration_validation 拒绝（fail-closed）。用于在保持确定性前提下
    # 声明"本场景用更敏感/更宽松的阈值"（如 high_risk 场景把停留阈值降到 15s）。
    clock_start: float | None = None
    rule_overrides: dict[str, float | int] | None = None
    # —— D8 Scenario Registry 预留字段（schema 现在就预留，使 ADR-0033 可直接消费）——
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    difficulty: str = ""
    category: str = ""


class Scenario(BaseModel):
    """单一声明式场景（D1 单一真相源）。"""

    meta: MetaSpec
    mode: str = "detections"  # detections | frames
    camera: CameraSpec  # 必填（结构合法校验）
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    actors: list[ActorSpec] = Field(default_factory=list)
    timeline: list[EventGroundTruth] = Field(default_factory=list)
    expects: ExpectsSpec = Field(default_factory=ExpectsSpec)
    # ADR-0034 Phase B.2：可选音频通道（跨模态关联来源）。缺省空 = 无音频会话驱动；
    # 仅当声明且闭环 cross_modal_enabled=True 时，loop 才会驱动 AudioSessionRecorder
    # 产出纯音频 episode 并与视觉 episode 建跨模态边。向后兼容：旧场景无此字段。
    audio: list[AudioEventSpec] = Field(default_factory=list)
    # G0-3：可选历史记忆预置（跨日 prior episodes）。缺省空 = 无历史。
    # runner 在运行前经 ``memory_store.upsert_episodic`` 预置，决策检索引用
    # （如 repeated_visit：3 days ago / yesterday / today）。
    prior_episodes: list[PriorEpisodeSpec] = Field(default_factory=list)
    # G0-3：历史感知决策开关（golden opt-in，缺省 False = 纯感知决策，现有场景逐字不变）。
    # True 时 runner 注入 memory_store 并启用 RuleBasedDecisionPolicy(memory_aware=True)——
    # 历史 episodes >= 2（跨日模式重复）→ 风险升级（LOW→MEDIUM 等）。
    memory_aware: bool = False
    # ADR-0033：可选安全评价标签（向后兼容缺省 None；ScenarioValidator 不消费此字段，
    # 验证 ≠ 评价，职责分离）。benchmark.expected_alarm 由场景作者显式声明。
    benchmark: BenchmarkExpectation | None = None
    # ADR-0034 D4：可选闭环集成期望（opt-in，缺省 None；ScenarioValidator 同样**不消费**，
    # 只有 IntegrationValidator 消费）。与 benchmark 语义分离：benchmark 测"感知该不该
    # 报警"，integration 测"报警后 Memory/Decision/Notification 该不该真发生"。
    integration: IntegrationExpectationSuite | None = None


# ============================================================================
# 校验
# ============================================================================


def validate_scenario_structure(scn: Scenario) -> None:
    """加载期结构校验（fail-closed：任何非法即抛 ValueError）。

    校验项：schema_version 已知、actor_type 枚举合法、tracks 帧序严格递增且不越界
    （仅当 ``duration_frames`` 已知时做越界检查）。
    """
    if scn.meta.schema_version not in KNOWN_SCHEMA_VERSIONS:
        raise ValueError(
            f"未知 schema_version={scn.meta.schema_version!r}；"
            f"已知版本 {sorted(KNOWN_SCHEMA_VERSIONS)}（fail-closed）"
        )
    if scn.camera.fps <= 0:
        raise ValueError(f"camera.fps 必须 > 0，收到 {scn.camera.fps!r}")
    if any(r <= 0 for r in scn.camera.resolution):
        raise ValueError(f"camera.resolution 必须为正，收到 {scn.camera.resolution!r}")

    for actor in scn.actors:
        if actor.actor_type not in ACTOR_TYPES:
            raise ValueError(
                f"actor {actor.id!r} 的 actor_type={actor.actor_type!r} 非法；必须为 {ACTOR_TYPES}"
            )
        frames = [kf.frame for kf in actor.tracks]
        # 帧序严格递增（单调、无重复）
        for prev, cur in itertools.pairwise(frames):
            if cur <= prev:
                raise ValueError(
                    f"actor {actor.id!r} 的 tracks.frame 必须严格递增，但出现 {prev} >= {cur}"
                )
        # 越界检查（仅当 duration_frames 已知）
        if scn.meta.duration_frames is not None:
            dur = scn.meta.duration_frames
            for kf in actor.tracks:
                if kf.frame < 0 or kf.frame >= dur:
                    raise ValueError(
                        f"actor {actor.id!r} 的 tracks.frame={kf.frame} 越界 [0, {dur})"
                    )

    # 运行时确定性覆盖校验（fail-closed）：clock_start 必须是正 Unix 秒（UTC）；
    # rule_overrides 的键合法性由闭环装配侧（run_integration_validation）对照
    # ThresholdConfig 字段拒绝未知键——场景层不反向 import 分析包，保持依赖方向。
    if scn.meta.clock_start is not None and scn.meta.clock_start <= 0:
        raise ValueError(
            f"场景 {scn.meta.scenario_id!r} 的 meta.clock_start={scn.meta.clock_start!r} "
            "必须为正 Unix 秒（UTC）（fail-closed）"
        )


def ensure_synthesizable(scn: Scenario) -> None:
    """生成期补充校验：``seed`` / ``duration_frames`` 必填（仅当实际 synthesize 才需要）。"""
    validate_scenario_structure(scn)
    if scn.meta.seed is None:
        raise ValueError(
            f"场景 {scn.meta.scenario_id!r} 缺少生成必填字段 meta.seed（生成期 fail-closed）"
        )
    if scn.meta.duration_frames is None:
        raise ValueError(
            f"场景 {scn.meta.scenario_id!r} 缺少生成必填字段 meta.duration_frames"
            f"（生成期 fail-closed）"
        )


# ============================================================================
# 加载
# ============================================================================


def load_scenario(path: str | Path) -> Scenario:
    """从 YAML 加载 ``Scenario``（加载期 fail-closed 校验身份字段 + 结构）。

    ``meta.schema_version`` / ``scenario_id`` / ``version`` 为 pydantic 必填，
    缺失即由 pydantic 抛错（fail-closed，对应 T10）。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"场景文件不存在: {path!r}")
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    scn = Scenario(**raw)
    validate_scenario_structure(scn)  # 结构合法（身份已由 pydantic 保证）
    return scn


def load_scenarios_dir(directory: str | Path) -> list[Scenario]:
    """加载目录下全部 ``*.yaml`` 场景（按文件名排序，确定性）。"""
    directory = Path(directory)
    out: list[Scenario] = []
    for p in sorted(directory.glob("*.yaml")):
        out.append(load_scenario(p))
    return out
