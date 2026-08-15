"""ADR-0036 Slice A · CasePresentationDescriptor（VM-11 纯展示编排）。

本模块**只定义展示编排类型** + 默认编排派生，不承载任何业务事实（VM-11）。

- ``CasePresentationDescriptor``：纯展示元数据（case_id/title/scenario_ref/media_binding/
  first_screen_layout/time_mapping + 可选 generated_by/renderer_version/provenance_ref），
  **不含** ``case_risk_level``/``case_decision``/``case_timeline`` 等可由
  ``EvidenceProjection`` 派生的业务事实（AC-13 静态扫描 + 加载校验双保险）。可选字段
  仅承载"生成方标识 / 渲染器版本 / 溯源引用"等可信元数据（P0-4.2），不引入任何新事实。
- ``build_default_case_presentation``：从 ``EvidenceProjection`` 派生**展示元数据**（标题
  由 scenario_id 派生、媒体绑定默认空、首屏布局默认、时间映射默认）——只取展示层字段，
  不读任何风险/决策/时间轴事实值做"事实判断"。
- ``load_case_descriptor``：从可选 JSON 文件读取人类提供的展示编排；fail-closed 拒绝任何
  事实型字段（AC-13）。

不 import ``silver_demo`` / 生产 runtime（VM-3）。本模块是 ``visualizer`` 子包，仍为
import 图死胡同叶子。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Required, TypedDict

from home_perception.visualizer.schema.evidence import EvidenceProjection

# 渲染器版本（P0-4.2）：自描述 artifact 锁定"由哪个版本渲染器生成"，
# 与 demo/ 包的 manifest.json 中 renderer_version 对齐，用于可信溯源与漂移检测。
# 仅 bump 当 Case Viewer 渲染契约（HTML 结构 / 数据岛键名 / 行为）发生不兼容变化时。
RENDERER_VERSION = "1.0.0"

# 生成方标识（P0-4）：CI 受控生成 vs 人工手动生成。
# - "manual"：开发者本地手动跑 run_case_viewer.py 生成（默认）；
# - "ci"：Trusted Case Factory 在 CI 受控环境生成，页面呈现"本案例由 CI 受控生成"可信徽章。
GeneratedBy = Literal["manual", "ci"]

# 媒体源类型（D-MediaSource / D-CaseVideo）：媒体字节由 Media Source Adapter 经 ref
# 解析，不进 EvidenceProjection（VM-10 / AC-11）。本枚举仅描述"绑定哪个源"，不持字节。
MediaSourceKind = Literal["ArtifactVideoSource", "SyntheticFrameSource", "LiveFrameSource"]

# 音频样本源类型（音频 E2E）：可播放音频样本由 Audio Source Adapter 经 ref 解析，
# 与 EvidenceProjection.audio_evidence（证据，无 url）**严格分离**（VM-9 / VM-10 / AC-11）。
# 本枚举仅描述"绑定哪个音频样本源"，不持字节。
AudioSourceKind = Literal["AudioFileSource"]

# 首屏面板默认顺序（§展示契约 AC-16 扩展，音频 E2E）：Case Video → 音频感知 → 当前风险 →
# 为什么 → 系统行动 → 统一 Evidence Timeline；详细证据（Graph/Fingerprint/Gate）折叠在二级视图。
# 「音频感知」首屏紧接视频：让用户一眼看懂"系统看到了什么 + 听到了什么"（音频 E2E P0 验收）。
_DEFAULT_FIRST_SCREEN_PANELS: tuple[str, ...] = (
    "case_video",
    "audio_perception",
    "current_risk",
    "why",
    "action",
    "evidence_timeline",
)


class MediaBinding(TypedDict):
    """媒体源绑定（纯展示 ref，不持媒体字节）。

    字节由 Media Source Adapter（ArtifactVideoSource / SyntheticFrameSource /
    LiveFrameSource）经 ``ref`` 解析；ref 是 artifact / D3 导出产物定位，不进 View Model。
    """

    source_kind: MediaSourceKind
    ref: str  # 媒体字节由 Media Source Adapter 经此 ref 解析（不进 View Model）


class TimeMapping(TypedDict):
    """Case Time 映射参数（VM-10 / D-SyncClock）：Media Time ↔ Evidence Time。

    仅描述线性映射参数，前端据此同步 Media Timeline 与 Evidence Timeline；不进
    EvidenceProjection（纯展示层时钟，VM-10）。
    """

    media_duration_s: float
    mode: Literal["linear"]


class FirstScreenLayout(TypedDict):
    """首屏面板编排（仅顺序，不携带事实值，VM-11）。"""

    panels: tuple[str, ...]


class CasePresentationDescriptor(TypedDict, total=False):
    """VM-11 · 纯展示编排对象（非业务事实模型）。

    铁律（AC-13）：**不得**含 ``case_risk_level`` / ``case_decision`` / ``case_timeline``
    等可由 ``EvidenceProjection`` 派生的业务事实；只编排"显示什么标题 / 播放哪个媒体 /
    首屏放哪些面板 / 时间映射参数"。一切事实值仍来自 ``EvidenceProjection``。

    6 个原始字段为 ``Required``（构造方必须提供），其余为可选（向下兼容）：
    - ``generated_by``：生成方（"manual" / "ci"，P0-4.2 可信徽章驱动）；
    - ``renderer_version``：渲染器版本（与 demo/ 包 manifest 锁版本对齐）；
    - ``provenance_ref``：Trusted Case Factory 产出的 provenance.json 路径（CI 受控生成时填）。

    可选字段均为**纯展示元数据**，不承载任何业务事实（VM-11 不变量不变）。
    """

    case_id: Required[str]
    title: Required[str]
    scenario_ref: Required[str]
    media_binding: Required[MediaBinding]
    first_screen_layout: Required[FirstScreenLayout]
    time_mapping: Required[TimeMapping]
    # —— 以下为 P0-4 新增纯展示元数据（可选，向下兼容）——
    generated_by: GeneratedBy
    renderer_version: str
    provenance_ref: str
    # —— P0-1 新增：Live WS 连接路径（纯展示元数据，非事实字段，AC-13 不拦）——
    # 仅 Live 模式（build_live_presentation）注入：行动闭环面板的前端 WS 客户端据此连接
    # gateway（收 snapshot/state_update、发 action 上行）。Artifact 模式缺省无该键。
    live_ws_path: str


# AC-13 守护：Descriptor 不得承载的"事实型字段"黑名单（静态扫描 + 加载校验双保险）。
# 这些字段一旦进入展示编排，就会悄悄形成"第二份业务事实状态"，违背 VM-1。
_FORBIDDEN_DESCRIPTOR_FACT_FIELDS = (
    "case_risk_level",
    "case_decision",
    "case_timeline",
    "risk_data",
    "decision_data",
    "timeline_data",
    "audio_data",
    "audio_state",
)


def build_default_case_presentation(
    projection: EvidenceProjection, *, scenario_index: int = 0
) -> CasePresentationDescriptor:
    """从 EvidenceProjection 派生**展示元数据**（VM-11 合规：不读事实值做事实判断）。

    - case_id / scenario_ref：取 scenario 标识（展示标识，非新事实）；
    - title：由 scenario_id 派生的展示标题文案（非风险事实）；
    - media_binding：默认空绑定（SyntheticFrameSource + 占位 ref，字节由 Adapter 解析）；
    - first_screen_layout：默认首屏面板顺序（AC-16）；
    - time_mapping：默认线性 Case Time 映射。
    """
    scenarios = projection["scenarios"]
    if not isinstance(scenarios, tuple) or not scenarios:
        raise ValueError("EvidenceProjection 无场景，无法派生 CasePresentationDescriptor")
    if not 0 <= scenario_index < len(scenarios):
        raise ValueError(
            f"scenario_index 越界：{scenario_index} 不在 [0, {len(scenarios)})（fail-closed）"
        )
    sid = scenarios[scenario_index]["scenario_id"]
    return CasePresentationDescriptor(
        case_id=sid,
        title=f"Case · {sid}",
        scenario_ref=sid,
        media_binding=MediaBinding(
            source_kind="SyntheticFrameSource",
            # 占位 ref：字节由 Media Source Adapter 经此解析，不进 View Model（VM-10/AC-11）
            ref=f"{sid}.canonical.json#media",
        ),
        first_screen_layout=FirstScreenLayout(panels=_DEFAULT_FIRST_SCREEN_PANELS),
        time_mapping=TimeMapping(media_duration_s=60.0, mode="linear"),
        # P0-4.2：默认派生为 manual（人工本地生成）；CI 受控生成须显式置 "ci"。
        generated_by="manual",
        renderer_version=RENDERER_VERSION,
    )


def load_case_descriptor(path: str | Path) -> CasePresentationDescriptor:
    """从可选 JSON 文件读取人类提供的展示编排；fail-closed 拒绝事实型字段（AC-13）。

    Raises:
        ValueError: 文件含 ``case_risk_level``/``case_decision``/``case_timeline`` 等
            事实型字段（VM-11 违规）。
        FileNotFoundError: 文件不存在。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    data = json.loads(p.read_text(encoding="utf-8"))
    _assert_no_forbidden_fact_fields(data)
    # 缺省字段补齐（纯展示元数据，缺则给安全默认，绝不读事实值）
    data.setdefault("case_id", data.get("scenario_ref", "unknown-case"))
    data.setdefault("title", f"Case · {data.get('scenario_ref', 'unknown')}")
    data.setdefault(
        "media_binding", {"source_kind": "SyntheticFrameSource", "ref": ""}
    )
    # 媒体绑定 shape 校验（评审 R2-#5）：即便用户提供畸形 media_binding，也须 fail-closed
    # 拒绝，否则下游 render 的 mb["source_kind"] 会抛 KeyError（孤儿 ref / 非法源类型）。
    _assert_media_binding_shape(data["media_binding"])
    data.setdefault(
        "first_screen_layout", {"panels": list(_DEFAULT_FIRST_SCREEN_PANELS)}
    )
    data.setdefault("time_mapping", {"media_duration_s": 60.0, "mode": "linear"})
    # P0-4.2：生成方 / 渲染器版本纯展示元数据（向下兼容默认）。
    # - generated_by 缺省 "manual"（人工本地生成，不显示 CI 徽章）；
    # - renderer_version 缺省 RENDERER_VERSION（与当前渲染器对齐）；
    # - provenance_ref 缺省空串（非 CI 受控生成时不指向任何 provenance.json）。
    data.setdefault("generated_by", "manual")
    data.setdefault("renderer_version", RENDERER_VERSION)
    data.setdefault("provenance_ref", "")
    return data  # type: ignore[return-value]


def _assert_media_binding_shape(mb: object) -> None:
    """媒体绑定 shape 校验（评审 R2-#5）：source_kind 须为合法枚举、ref 须为字符串。

    用户可能提供畸形绑定（如 ``{"foo": "bar"}`` 缺 source_kind/ref），若放任下游
    ``mb["source_kind"]`` 会抛 KeyError。这里 fail-closed 显式拒绝。
    """
    if not isinstance(mb, dict):
        raise ValueError("media_binding 必须是对象（含 source_kind 与 ref）")  # noqa: TRY004
    sk = mb.get("source_kind")  # type: ignore[union-attr]
    if sk not in ("ArtifactVideoSource", "SyntheticFrameSource", "LiveFrameSource"):
        raise ValueError(
            f"media_binding.source_kind 非法：{sk!r}（须为 ArtifactVideoSource/"
            f"SyntheticFrameSource/LiveFrameSource）"
        )
    if not isinstance(mb.get("ref", ""), str):  # type: ignore[union-attr]
        raise ValueError("media_binding.ref 必须是字符串")  # noqa: TRY004


def _assert_no_forbidden_fact_fields(data: object) -> None:
    """递归扫描，拒绝任何事实型字段伪装进展示编排（AC-13 双保险）。

    设计取舍（评审 R2-#4）：本守卫**仅比对键名**，不深校验"值"的内容——
    即它拒绝 ``case_risk_level`` 这类键出现，但**不保证**非禁止键的值里嵌套了事实语义
    （例如某个自定义键的值是一段风险判断）。这符合 AC-13 的「字段层面」防线；更深层的事实
    语义污染由静态扫描（AC-13 静态）+ 投影层派生（VM-1：一切事实值仍来自 EvidenceProjection）
    兜底，不在加载校验范围内。键名黑名单 + 值语义扫描二者分工明确，不在此合并。
    """

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k).lower() in _FORBIDDEN_DESCRIPTOR_FACT_FIELDS:
                    raise ValueError(
                        f"CasePresentationDescriptor 含事实型字段 {k!r}，违反 VM-11（AC-13）"
                    )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)


__all__ = [
    "RENDERER_VERSION",
    "CasePresentationDescriptor",
    "FirstScreenLayout",
    "GeneratedBy",
    "MediaBinding",
    "MediaSourceKind",
    "TimeMapping",
    "build_default_case_presentation",
    "load_case_descriptor",
]
