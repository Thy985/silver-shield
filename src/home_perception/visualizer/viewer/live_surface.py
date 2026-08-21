"""ADR-0038/0040 · Live Surface Phase 1 纯函数模块。

设计来源（合同冻结）：
- ``WIREFRAME-DESIGN.md`` v3.2 §1.2 L0/L2/L5
- ``LIVE-PERCEPTION-STREAM-SPEC.md`` v1.2 §2.4 L0 Audio Health 语义
- ``LIVE-PERCEPTION-STREAM-SEMANTICS.md`` v2.0 整体语义表

模块职责（VM-1 · 唯一 View Model）：
- **L0 Audio Health 三值状态机**：``RECENT_EVENT`` / ``NO_RECENT_EVENT`` / ``UNAVAILABLE``
  （非健康度二元判定，明确区分"最近事件"与"硬件链路"）；
- **Risk Reason 追源**：从 ``risk_delta.reason_summary[]`` 提取，并对照白名单
  （``routing_table`` 硬编码人话）拒绝任何产品预写文案；
- **L5 Provenance 快捷入口**：生成"为什么相信？"链接 HTML（极低成本可达）。

纪律（VM-3 · 依赖方向）：
- 本模块**仅** import stdlib（``enum`` / ``dataclasses`` / ``typing``）；
- **不** import ``silver_demo`` / 生产 runtime；
- **不** import 同包 ``render`` / ``live_adapter``（避免循环依赖）。

AST 契约（D3）：纯 Python，零外部依赖；可在 torch-free 环境跑测试。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class AudioHealth(str, enum.Enum):
    """L0 Audio Health 三值状态（见 LIVE-PERCEPTION-STREAM-SPEC §2.4）。

    **铁律**：
    - 三值**非**二元健康度（"正常/中断"是禁止映射）；
    - ``RECENT_EVENT`` 仅证明"最近有 Audio Event"，**不**证明"音频设备当前仍在采集"；
    - ``NO_RECENT_EVENT`` 仅证明"5s 内无事件"，**不**证明"音频中断"，可能是静默期；
    - 真正的硬件健康度需要 ``audio_input_tick`` / ``audio_last_seen``，当前 🟡 Partial。
    """

    RECENT_EVENT = "RECENT_EVENT"
    NO_RECENT_EVENT = "NO_RECENT_EVENT"
    UNAVAILABLE = "UNAVAILABLE"


class RiskReason(str, enum.Enum):
    """Risk Reason 白名单（来自 ``decision_policy.routing_table`` 第三元素）。

    **铁律**（见 WIREFRAME-DESIGN.md §Risk Reason 追源）：
    - 风险卡片原因文案**必须**来自 ``risk_delta.reason_summary[]``；
    - 禁止产品预写文案（如"声学状态变化 + 电话交互"）；
    - ``live_stream.js`` 的 ``_REASON_ZH`` 预定义键 ``acoustic_state_change`` /
      ``telephone_interaction`` 等**目前不会被 runtime 触发**，禁止进入 UI；
    - 白名单为**单向**：发现新 reason 必须先更新 ``routing_table`` 再扩展白名单。
    """

    ABNORMAL_DWELL = "异常停留"
    REPEAT_VISIT = "重复访问"
    VISIT_PENDING_VERIFY = "未在白名单"
    VISIT_NORMAL = "异常时段访问"
    HIGH_RISK_APPROACH = "多风险规则同时命中"


# 路由表第三元素全集（白名单硬编码；与 decision_policy.DEFAULT_ROUTING_TABLE 对齐）
_ALLOWED_REASON_TEXTS: frozenset[str] = frozenset(r.value for r in RiskReason)


@dataclass(frozen=True)
class AudioHealthState:
    """Audio Health 状态 + 推荐 UI 文案 + 颜色（确定性函数输出）。"""

    state: AudioHealth
    label: str
    detail: str
    css_class: str


# 文案映射（三值 → UI），与 WIREFRAME-DESIGN.md §1.2 L0 三值降级分层对齐
_AUDIO_HEALTH_UI: dict[AudioHealth, AudioHealthState] = {
    AudioHealth.RECENT_EVENT: AudioHealthState(
        state=AudioHealth.RECENT_EVENT,
        label="🔊 最近声音事件",
        detail="最近检测到音频事件",
        css_class="audio-recent",
    ),
    AudioHealth.NO_RECENT_EVENT: AudioHealthState(
        state=AudioHealth.NO_RECENT_EVENT,
        label="⏸ 最近无声音事件",
        detail="5s 内无事件（可能是静默期，不表设备离线）",
        css_class="audio-stale",
    ),
    AudioHealth.UNAVAILABLE: AudioHealthState(
        state=AudioHealth.UNAVAILABLE,
        label="🔇 本场景无音频轨",
        detail="场景硬件配置无音频",
        css_class="audio-na",
    ),
}


# L0 NO_RECENT_EVENT 判定阈值（ms）；超过此间隔视为无最近事件
_AUDIO_STALE_THRESHOLD_MS = 5000


def compute_audio_health(
    *,
    last_audio_event_ts_ms: int | None,
    now_ms: int,
    scenario_has_audio_track: bool,
) -> AudioHealthState:
    """L0 Audio Health 三值状态机（纯函数，确定性）。

    Args:
        last_audio_event_ts_ms: 最近一次 audio event 的时间戳（Unix ms）；
            ``None`` 表示从未收到过。
        now_ms: 当前时间戳（Unix ms）。
        scenario_has_audio_track: 场景本身是否含音频轨
            （如 ``cctv_surveillance`` 等无音频场景 → ``False``）。

    Returns:
        :class:`AudioHealthState`：含状态枚举 / 文案 / CSS class。

    **判定顺序**（禁止翻转）：
    1. ``scenario_has_audio_track=False`` → ``UNAVAILABLE``（场景硬件无音频）
    2. ``last_audio_event_ts_ms is None`` → ``NO_RECENT_EVENT``
    3. ``now_ms - last_audio_event_ts_ms > _AUDIO_STALE_THRESHOLD_MS`` → ``NO_RECENT_EVENT``
    4. 否则 → ``RECENT_EVENT``

    Examples:
        >>> compute_audio_health(last_audio_event_ts_ms=1000, now_ms=2000,
        ...                       scenario_has_audio_track=True).state.value
        'RECENT_EVENT'
        >>> compute_audio_health(last_audio_event_ts_ms=1000, now_ms=8000,
        ...                       scenario_has_audio_track=True).state.value
        'NO_RECENT_EVENT'
        >>> compute_audio_health(last_audio_event_ts_ms=None, now_ms=2000,
        ...                       scenario_has_audio_track=False).state.value
        'UNAVAILABLE'
    """
    # 判定 1：场景硬件无音频 → UNAVAILABLE
    if not scenario_has_audio_track:
        return _AUDIO_HEALTH_UI[AudioHealth.UNAVAILABLE]
    # 判定 2：从未收到过 audio event → NO_RECENT_EVENT
    if last_audio_event_ts_ms is None:
        return _AUDIO_HEALTH_UI[AudioHealth.NO_RECENT_EVENT]
    # 判定 3：超时 → NO_RECENT_EVENT
    if now_ms - last_audio_event_ts_ms > _AUDIO_STALE_THRESHOLD_MS:
        return _AUDIO_HEALTH_UI[AudioHealth.NO_RECENT_EVENT]
    # 判定 4：默认 → RECENT_EVENT
    return _AUDIO_HEALTH_UI[AudioHealth.RECENT_EVENT]


@dataclass(frozen=True)
class RiskReasonResult:
    """Risk Reason 追源结果：runtime 字段 + 白名单校验 + 拒绝项。"""

    valid_reasons: tuple[str, ...]
    rejected_reasons: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        """所有 reason 均来自 routing_table 白名单 → True。"""
        return len(self.rejected_reasons) == 0


def extract_risk_reasons(
    reason_summary: list[str] | tuple[str, ...] | None,
) -> RiskReasonResult:
    """Risk Reason 追源（白名单校验）。

    **铁律**（见 WIREFRAME-DESIGN.md §Risk Reason 追源）：
    - 仅接受来自 ``decision_policy.routing_table`` 第三元素的人话字符串；
    - 其他任何字符串（包括产品预写文案 / live_stream.js 预定义键 /
      旧 event_type 枚举等）一律归入 ``rejected_reasons``；
    - **不抛异常**（fail-soft：违规文案会被记录但不影响 valid 部分渲染）。

    Args:
        reason_summary: ``risk_delta.reason_summary[]`` 内容（来自 runtime）；
            ``None`` / 空列表均视为"无原因"。

    Returns:
        :class:`RiskReasonResult`：含 ``valid_reasons`` / ``rejected_reasons``。

    Examples:
        >>> r = extract_risk_reasons(["未在白名单"])
        >>> r.is_clean
        True
        >>> r.valid_reasons
        ('未在白名单',)
        >>> r = extract_risk_reasons(["声学状态变化 + 电话交互"])
        >>> r.is_clean
        False
        >>> r.rejected_reasons
        ('声学状态变化 + 电话交互',)
    """
    if not reason_summary:
        return RiskReasonResult(valid_reasons=(), rejected_reasons=())
    valid: list[str] = []
    rejected: list[str] = []
    for r in reason_summary:
        if not isinstance(r, str):
            rejected.append(str(r))
            continue
        if r in _ALLOWED_REASON_TEXTS:
            valid.append(r)
        else:
            rejected.append(r)
    return RiskReasonResult(valid_reasons=tuple(valid), rejected_reasons=tuple(rejected))


def render_why_believe_link(scenario_id: str) -> str:
    """L5 Provenance 快捷入口：首屏底部"为什么相信？"链接（极低成本可达）。

    设计（见 WIREFRAME-DESIGN.md §1.2 L5）：
    - 链接指向 ``<details id="fs-details-{sid}">`` 折叠区；
    - 默认在 L4 行动卡片下方显示（一级可达，非折叠区内）；
    - 点击后浏览器原生展开 details。

    Args:
        scenario_id: 场景 ID（用于 DOM 锚点）。

    Returns:
        HTML 字符串（可直接嵌入 Shell）。
    """
    sid = scenario_id
    return (
        f"<a class='why-believe-link' "
        f"href='#fs-details-{sid}' "
        f"data-target='fs-details-{sid}'>"
        f"🔍 为什么相信？"
        f"</a>"
    )