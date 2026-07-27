"""实时风险信号（RiskSignal）— ADR-0021 Signal Layer 类型定义（Migration Stage A · 仅类型）。

> **ADR-0021 = 实时风险状态流与信号生成层。** 本模块只定义**信号层的数据类型与枚举**，
> 不接入 pipeline、不产出 Warning、不接决策层（Stage A 边界：只加类型 + 契约测试）。
> 真正的发射 / 配对 / 去重逻辑由 Stage C 的 `RealTimeRiskEvaluator` / `SignalAdapter` 负责。

**关键语义（ADR-0021 §3.3）**：
- `RiskSignal` 是「瞬时信号」，是状态机的一次「状态跃迁(transition)」，**不是**「长期状态」。
  `RAISED` 与 `CLEARED` 是两条独立发射的消息（各有自己的 `signal_id` / `created_at`），
  持续态(ACTIVE_RISK)由评估器内部状态机持有，本模块不持有。
- `category`（异常类别）与 `source`（物理来源）**正交**，分别回答"哪类异常 / 哪个模态"，不混。
- 主体泛化：以 `subject_type` + `subject_id` 表达风险主体，`track_id` / `visitor_instance_id`
  仅为 `subject_type==VISITOR` 时的便利冗余字段（Phase 1 恒 `VISITOR` 且 `subject_id==visitor_instance_id`）。
- `paired_signal_id` 是**顶级可选字段**（非 features 内），CLEARED 回填对应 RAISED 的 signal_id。

**模块边界铁律（ADR-0014 / ADR-0021）**：
- 不输出 fraud / suspect / verdict 等犯罪认定字段（黑名单由下游 adapter 拦截，本类型也不提供该字段）。
- **禁止** import ADR-0022 的 `EvidenceModality`（两者是不同限界上下文的独立枚举，见 ADR-0021 §3.3 命名消歧）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID


def _utc_now() -> datetime:
    """时区感知的 UTC 当前时间（对齐仓库其他领域对象，替代 deprecated `datetime.utcnow()`）。"""
    return datetime.now(timezone.utc)


def _coerce_enum(enum_cls: type, value: Any, field_name: str) -> Enum:
    """接受枚举实例或字符串值，归一为枚举实例；非法值抛 ValueError。"""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            valid = ", ".join(repr(e.value) for e in enum_cls)
            raise ValueError(
                f"{field_name} 必须是 {enum_cls.__name__} 之一，收到 {value!r}；合法值：{valid}"
            ) from exc
    raise TypeError(
        f"{field_name} 必须是 {enum_cls.__name__} 或 str，收到 {type(value).__name__}"
    )


def _coerce_uuid(value: Any) -> str:
    """signal_id 接受 UUID 或 str（内部统一为 str）。"""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"signal_id 必须是 UUID 或 str，收到 {type(value).__name__}")


def _require_utc(dt: datetime, field_name: str) -> None:
    """校验 datetime 是 timezone-aware 且为 UTC（防御 naive 漏标，对齐 ADR-0007）。"""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(
            f"{field_name} 必须是 timezone-aware datetime（建议 UTC），收到 naive datetime: {dt!r}"
        )


# ============================================================================
# 枚举（严格白名单，禁止自由文本）
# ============================================================================

class SignalCategory(str, Enum):
    """异常类别（按风险语义域划分，非检测手段；未来新检测器归入既有域，不新增枚举）。"""

    BEHAVIORAL = "behavioral"      # 行为异常（停留/重复/接近/徘徊轨迹）
    IDENTITY = "identity"          # 身份异常（陌生人脸/声纹，Phase 4）
    COMMUNICATION = "communication"  # 沟通/话术异常（语音威胁/诱导话术，Phase 3）
    SAFETY = "safety"              # 安全威胁（持械/跌倒/老人走失等人身安全）
    ENVIRONMENT = "environment"    # 环境异常（预留）


class SourceModality(str, Enum):
    """信号上下文的「传感来源」（勿与 ADR-0022 的 EvidenceModality 混淆）。"""

    VISION = "vision"
    AUDIO = "audio"
    SENSOR = "sensor"


class SignalTransition(str, Enum):
    """本次发射是升起还是解除（是跃迁类型，不是长期状态）。"""

    RAISED = "raised"    # 异常升起的一次跃迁
    CLEARED = "cleared"  # 异常解除的一次跃迁


class SubjectType(str, Enum):
    """风险主体类型（前瞻接口：信号未必来自视觉 track）。"""

    VISITOR = "visitor"        # 门口访客（Phase 1 唯一取值）
    PERSON = "person"          # 已识别的具体人（Phase 4 ReID 之后）
    DEVICE = "device"          # 设备 / 终端（如电话诈骗、异常转账，未来）
    ENVIRONMENT = "environment"  # 环境主体（无具体人，如烟感 / 门磁，未来）


# 枚举闭合性基线（契约测试会逐值校验，防止未来误删/扩值漂移）
SIGNAL_CATEGORY_VALUES: tuple = tuple(e.value for e in SignalCategory)
SOURCE_MODALITY_VALUES: tuple = tuple(e.value for e in SourceModality)
SIGNAL_TRANSITION_VALUES: tuple = tuple(e.value for e in SignalTransition)
SUBJECT_TYPE_VALUES: tuple = tuple(e.value for e in SubjectType)

# to_dict 字段闭合基准（契约测试据此断言「字段集合恒定 + 不含黑名单字段」）
RISKSIGNAL_DICT_KEYS: tuple = (
    "signal_id",
    "subject_type",
    "subject_id",
    "category",
    "source",
    "transition",
    "features",
    "paired_signal_id",
    "track_id",
    "visitor_instance_id",
    "severity_hint",
    "created_at",
)

# RiskSignal 顶层**禁止**出现的犯罪认定 / 判定字段（模块边界铁律，见 docstring）
FORBIDDEN_RISKSIGNAL_FIELDS: frozenset = frozenset({
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
})


@dataclass
class RiskSignal:
    """实时风险信号（ADR-0021 Signal Layer，瞬时跃迁消息）。

    字段：
    - `signal_id`：uuid4 字符串，本次发射的唯一标识（瞬时，非会话常驻）
    - `subject_type`：风险主体类型（VISITOR/PERSON/DEVICE/ENVIRONMENT）
    - `subject_id`：风险主体标识；Phase 1 恒 == visitor_instance_id
    - `category`：异常类别（与 source 正交，不展开具体行为）
    - `source`：产出该信号的物理来源（VISION/AUDIO/SENSOR）
    - `transition`：本次发射是升起还是解除（RAISED/CLEARED）
    - `features`：原始异常证据（如 {"dwell_seconds":350}），只放证据不放关系元数据
    - `paired_signal_id`：顶级可选；CLEARED 回填对应 RAISED 的 signal_id；RAISED 时为 None
    - `track_id` / `visitor_instance_id`：仅当 subject_type==VISITOR 时的便利冗余字段
    - `severity_hint`：可选 [0,1] 严重度提示（非决策判定）
    - `created_at`：发射时刻（UTC timezone-aware datetime，**非 float 戳**）

    契约不变式（__post_init__ 强制）：
    1. 四个枚举字段严格闭合（接受 str 自动归一）
    2. `transition==RAISED` ⇒ `paired_signal_id is None`（升起无配对）
    3. `created_at` 必须 UTC timezone-aware（防跨设备时间漂移）
    4. `severity_hint` 若有值必须 ∈ [0,1]
    5. 顶层字段不含黑名单判定字段（本类型不提供，结构性保证）
    """

    signal_id: str
    subject_type: SubjectType
    subject_id: str
    category: SignalCategory
    source: SourceModality
    transition: SignalTransition
    features: Dict[str, Any]
    paired_signal_id: Optional[str] = None
    track_id: Optional[int] = None
    visitor_instance_id: Optional[str] = None
    severity_hint: Optional[float] = None
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        # 1) 枚举归一 + 闭合校验
        self.subject_type = _coerce_enum(SubjectType, self.subject_type, "subject_type")
        self.category = _coerce_enum(SignalCategory, self.category, "category")
        self.source = _coerce_enum(SourceModality, self.source, "source")
        self.transition = _coerce_enum(SignalTransition, self.transition, "transition")

        # 2) signal_id 归一为 str
        self.signal_id = _coerce_uuid(self.signal_id)

        # 3) RAISED 必无配对
        if self.transition is SignalTransition.RAISED and self.paired_signal_id is not None:
            raise ValueError(
                f"RAISED 信号的 paired_signal_id 必须为 None，收到 {self.paired_signal_id!r}"
            )

        # 4) created_at 必须 UTC-aware
        _require_utc(self.created_at, "created_at")

        # 5) severity_hint 范围
        if self.severity_hint is not None:
            if not isinstance(self.severity_hint, (int, float)):
                raise TypeError(
                    f"severity_hint 必须是 float，收到 {type(self.severity_hint).__name__}"
                )
            if not (0.0 <= float(self.severity_hint) <= 1.0):
                raise ValueError(
                    f"severity_hint 必须在 [0, 1]，收到 {self.severity_hint}"
                )
            self.severity_hint = float(self.severity_hint)

        # 6) 黑名单字段结构性保证（features 内也不允许出现判定字段）
        forbidden_top = FORBIDDEN_RISKSIGNAL_FIELDS.intersection(self.__dict__.keys())
        if forbidden_top:
            raise ValueError(f"RiskSignal 含禁止字段 {forbidden_top}")
        if isinstance(self.features, dict):
            forbidden_in_features = FORBIDDEN_RISKSIGNAL_FIELDS.intersection(self.features.keys())
            if forbidden_in_features:
                raise ValueError(f"RiskSignal.features 含禁止字段 {forbidden_in_features}")

    def to_dict(self) -> Dict[str, Any]:
        """structlog-safe 字典（枚举转 value、datetime 转 ISO 字符串）。"""
        return {
            "signal_id": self.signal_id,
            "subject_type": self.subject_type.value,
            "subject_id": self.subject_id,
            "category": self.category.value,
            "source": self.source.value,
            "transition": self.transition.value,
            "features": self.features,
            "paired_signal_id": self.paired_signal_id,
            "track_id": self.track_id,
            "visitor_instance_id": self.visitor_instance_id,
            "severity_hint": self.severity_hint,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        """JSON 序列化（日志归档 / 跨进程传递用）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
