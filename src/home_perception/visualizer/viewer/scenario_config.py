"""Phase 2: 场景布局配置（WIREFRAME-DESIGN.md §807 / LIVE-PERCEPTION-STREAM-SPEC.md §7）。

设计来源（合同冻结）：
- ``WIREFRAME-DESIGN.md`` v3.2 §807 Phase 2 场景适配
- ``LIVE-PERCEPTION-STREAM-SPEC.md`` v1.2 §7 场景适配规范
- ``LIVE-SCENARIO-CONTROLLER-SPEC.md`` v0.1 场景叙事路由规范

模块职责：
- **场景布局配置**：根据 scenario_id 派生当前场景应显示哪些 Surface（六层感知流）
- **Surface 可见性**：音频类 surface 仅在 telephone_risk 场景可见（cctv_surveillance 必须隐藏）
- **Memory 依赖**：repeated_visit 场景的 Memory 关联感知流标记为 Phase 3（🟡 阻塞）
- **场景叙事路由**：根据 scenario_id 返回显式 Narrative Mode（AUDIO_FIRST / VISION_FIRST /
  MEMORY_FIRST / NEUTRAL），决定前端 grid 权重与 Surface composition

Surface 层级（从低到高）：
- L0: Audio Health（所有场景）
- L1: Audio Perception（telephone_risk 专属）
- L2: Acoustic State（telephone_risk 专属）
- L3: Perception Stream（所有场景）
- L4: Risk Signals（所有场景）
- L5: Provenance（所有场景）
- L6: Memory Context（repeated_visit 🟡 Phase 3）

Narrative Mode 映射：
- telephone_risk → AUDIO_FIRST（音频是主叙事）
- cctv_surveillance → VISION_FIRST（视觉是主叙事）
- repeated_visit → MEMORY_FIRST（历史记忆是主叙事）
- 未知场景 → NEUTRAL（fail-closed）

铁律（VM-1 / VM-9 / AC-12）：
- 本模块仅导出纯函数，不依赖 runtime
- 场景 ID 必须来自 runtime，禁止硬编码产品文案
- telephone_risk 场景禁止展示 "NORMAL → ATTENTION → AROUSAL → STRESS"（Golden Case 叙事，非 Runtime）
- **Runtime Event 不改变 Narrative Mode**：mode 由 scenario_id 唯一决定
- **browser 只消费**：前端读 data-narrative-mode，不自行计算
"""

from __future__ import annotations

import enum
from typing import Final


class ScenarioSurface(str, enum.Enum):
    """Phase 2: 场景适配 Surface 枚举（LIVE-PERCEPTION-STREAM-SPEC.md §7）。

    铁律：
    - 音频类 Surface 仅 telephone_risk 可见（cctv_surveillance 必须隐藏）
    - Memory Context 依赖 Memory API（Phase 3）
    - 禁止 Golden Case 叙事（NORMAL → ... → STRESS）伪装成 Runtime
    """

    # 基础（所有场景）
    L0_AUDIO_HEALTH = "L0_AUDIO_HEALTH"
    L3_PERCEPTION_STREAM = "L3_PERCEPTION_STREAM"
    L4_RISK_SIGNALS = "L4_RISK_SIGNALS"
    L5_PROVENANCE = "L5_PROVENANCE"

    # telephone_risk 专属（P0.5 叙事层）
    L1_AUDIO_PERCEPTION = "L1_AUDIO_PERCEPTION"
    L2_ACOUSTIC_STATE = "L2_ACOUSTIC_STATE"

    # repeated_visit 专属（🟡 Phase 3 阻塞）
    L6_MEMORY_CONTEXT = "L6_MEMORY_CONTEXT"


# 场景 ID → 可用 Surface 集合（只读，运行时不可变）
_SCENARIO_SURFACES: dict[str, frozenset[ScenarioSurface]] = {
    "telephone_risk": frozenset({
        ScenarioSurface.L0_AUDIO_HEALTH,
        ScenarioSurface.L1_AUDIO_PERCEPTION,
        ScenarioSurface.L2_ACOUSTIC_STATE,
        ScenarioSurface.L3_PERCEPTION_STREAM,
        ScenarioSurface.L4_RISK_SIGNALS,
        ScenarioSurface.L5_PROVENANCE,
    }),
    # SSOT v4.1：telephone_risk_reality_check 沿用 telephone_risk Surface 集
    # （同场景族，仅 source 标识不同——audio real pipeline + visual real runtime），
    # 否则 has_audio_surface()=False 门控会把 audio 面板（含 Audio Evidence Lane）屏蔽，
    # 触发"audio 区域巨大空白"回归。
    "telephone_risk_reality_check": frozenset({
        ScenarioSurface.L0_AUDIO_HEALTH,
        ScenarioSurface.L1_AUDIO_PERCEPTION,
        ScenarioSurface.L2_ACOUSTIC_STATE,
        ScenarioSurface.L3_PERCEPTION_STREAM,
        ScenarioSurface.L4_RISK_SIGNALS,
        ScenarioSurface.L5_PROVENANCE,
    }),
    "cctv_surveillance": frozenset({
        ScenarioSurface.L0_AUDIO_HEALTH,
        ScenarioSurface.L3_PERCEPTION_STREAM,
        ScenarioSurface.L4_RISK_SIGNALS,
        ScenarioSurface.L5_PROVENANCE,
    }),
    # repeated_visit 需要 Memory API（Phase 3）
    "repeated_visit": frozenset({
        ScenarioSurface.L0_AUDIO_HEALTH,
        ScenarioSurface.L3_PERCEPTION_STREAM,
        ScenarioSurface.L4_RISK_SIGNALS,
        ScenarioSurface.L5_PROVENANCE,
        # ScenarioSurface.L6_MEMORY_CONTEXT,  # 🟡 Phase 3 阻塞
    }),
    # D0 AU-06（B4）根因修复历史：原 product_story_risk 场景含 synthetic_replay 音频注入
    # （audio_replay_path），此前未注册音频 Surface → has_audio_surface()=False 门控
    # 了 waveform canvas 渲染。该场景音频是叙事主轴之一，注册 L1/L2 音频 Surface。
    # 2026-08-25 决策：product_story_risk 已重命名为 telephone_risk（场景身份迁移，详见
    # docs/design/architecture/SCENARIO-RENAME-CONFLICTS-2026-08-25.md §3 D1），
    # 上面 telephone_risk key 直接接管全部 Surface 注册；下方原 product_story_risk key 删除。
}

# 未知场景默认 Surface（最小集）
_DEFAULT_SURFACES: Final[frozenset[ScenarioSurface]] = frozenset({
    ScenarioSurface.L0_AUDIO_HEALTH,
    ScenarioSurface.L3_PERCEPTION_STREAM,
    ScenarioSurface.L4_RISK_SIGNALS,
    ScenarioSurface.L5_PROVENANCE,
})


def get_scenario_surfaces(scenario_id: str) -> frozenset[ScenarioSurface]:
    """根据场景 ID 返回可用 Surface 集合。

    Args:
        scenario_id: 场景标识（如 ``"telephone_risk"`` / ``"cctv_surveillance"``）。

    Returns:
        该场景可用 Surface 的不可变集合；未知场景返回默认最小集。

    Examples:
        >>> sorted(get_scenario_surfaces("telephone_risk"), key=lambda x: x.value)
        [ScenarioSurface.L0_AUDIO_HEALTH, ScenarioSurface.L1_AUDIO_PERCEPTION, ...]
        >>> sorted(get_scenario_surfaces("cctv_surveillance"), key=lambda x: x.value)
        [ScenarioSurface.L0_AUDIO_HEALTH, ScenarioSurface.L3_PERCEPTION_STREAM, ...]
    """
    return _SCENARIO_SURFACES.get(scenario_id, _DEFAULT_SURFACES)


def has_audio_surface(scenario_id: str) -> bool:
    """判断场景是否可见音频类 Surface（L1 / L2）。

    Args:
        scenario_id: 场景标识。

    Returns:
        True 如果场景包含 L1_AUDIO_PERCEPTION 或 L2_ACOUSTIC_STATE。
    """
    surfaces = get_scenario_surfaces(scenario_id)
    return (
        ScenarioSurface.L1_AUDIO_PERCEPTION in surfaces
        or ScenarioSurface.L2_ACOUSTIC_STATE in surfaces
    )


def has_memory_surface(scenario_id: str) -> bool:
    """判断场景是否可见 Memory Context Surface（L6）。

    注意：当前 L6 标记为 Phase 3 阻塞（repeated_visit 暂未启用）。

    Args:
        scenario_id: 场景标识。

    Returns:
        True 如果场景包含 L6_MEMORY_CONTEXT。
    """
    surfaces = get_scenario_surfaces(scenario_id)
    return ScenarioSurface.L6_MEMORY_CONTEXT in surfaces


def render_scenario_surface_banner(scenario_id: str) -> str:
    """渲染场景 Surface 配置说明（用于调试 / 审计入口）。

    Args:
        scenario_id: 场景标识。

    Returns:
        HTML 字符串，描述当前场景启用的 Surface 列表。
    """
    surfaces = get_scenario_surfaces(scenario_id)
    labels = [s.value for s in sorted(surfaces, key=lambda x: x.value)]
    labels_html = ", ".join(f"<code>{_esc_label(l)}</code>" for l in labels)
    return (
        f'<div class="scenario-surface-banner" '
        f'data-scenario="{_esc_attr(scenario_id)}" data-surfaces="{len(labels)}">'
        f"场景 <code>{_esc_attr(scenario_id)}</code> 启用 Surface: {labels_html}"
        f"</div>"
    )


def _esc_attr(s: str) -> str:
    """HTML 属性转义（简化版，等价于 render._R._esc）。"""
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _esc_label(s: str) -> str:
    """HTML 文本转义（简化版）。"""
    return _esc_attr(s)


# ---------------------------------------------------------------------------
# Scenario Narrative Mode（LIVE-SCENARIO-CONTROLLER-SPEC.md）
# ---------------------------------------------------------------------------


class ScenarioNarrativeMode(str, enum.Enum):
    """场景叙事模式枚举。

    铁律：
    - 由 scenario_id 唯一决定，Runtime Event 不得改变
    - 前端只消费 data-narrative-mode 属性，不自行计算
    - 未知场景 fail-closed → NEUTRAL
    """

    AUDIO_FIRST = "audio_first"
    VISION_FIRST = "vision_first"
    MEMORY_FIRST = "memory_first"
    NEUTRAL = "neutral"


# scenario_id -> narrative_mode 映射表（只读，运行时不可变）
_NARRATIVE_MODES: Final[dict[str, ScenarioNarrativeMode]] = {
    "telephone_risk": ScenarioNarrativeMode.AUDIO_FIRST,
    "cctv_surveillance": ScenarioNarrativeMode.VISION_FIRST,
    "repeated_visit": ScenarioNarrativeMode.MEMORY_FIRST,
}


def get_scenario_narrative_mode(scenario_id: str) -> ScenarioNarrativeMode:
    """根据场景 ID 返回 Narrative Mode。

    Args:
        scenario_id: 场景标识（如 ``"telephone_risk"`` / ``"cctv_surveillance"``）。

    Returns:
        该场景的 Narrative Mode；未知场景返回 NEUTRAL。

    铁律：
    - 返回值由 scenario_id 唯一决定，Runtime Event 不改变此值
    - 前端通过 data-narrative-mode 属性消费，不自行计算
    """
    return _NARRATIVE_MODES.get(scenario_id, ScenarioNarrativeMode.NEUTRAL)