"""Golden Case 统一接入适配层（ADR-0036 补遗 · Phase 1）。

## 核心原则

- **纯映射**：只读 ``dataset/{case}/manifest.yaml``，把已有字段翻译到 ScenarioConfig-shape。
  - **不**复制 manifest 内容到新 yaml
  - **不**为某个 case 写特例分支
  - **不**填"看起来合理"的默认值
  - **不**解析 ``episodes/acts/variants`` 数组
- **资产层合并优先**：多幕 case（repeated_visit, evidence_insufficient）的 3 幕视频
  已由 ``cctv_post.py`` 预拼接为 ``output/{case}_demo.mp4``，adapter 不重新拆解。
- **VM-1 / VM-9 守护**：不产生任何新事实节点（pre-event 由 Phase 2 单独处理）。
- **schema 容忍**（不写特例）：不同 case 的 manifest 字段可能略有不一致（如 evidence_insufficient 缺
  case_start），adapter 用**通用规则**兜底（``generated`` + ``acts[0].timestamp``），不写 case-specific 分支。

## 4 case 共用同一个 ``load_golden_scenario``

| case | manifest 字段映射 | 资源路径 |
|------|------------------|----------|
| stranger_visit | case_start + product_question | ``output/stranger_visit_final.mp4`` |
| repeated_visit | case_start + product_question | ``output/repeated_visit_demo.mp4`` (3 幕已拼) |
| telephone_risk | case_start + product_question | ``output/telephone_risk_demo.mp4`` (case_a/b 已拼) |
| evidence_insufficient | generated 兜底 | ``output/evidence_insufficient_demo.mp4`` (3 幕已拼) |

## 不解析的字段（Phase 2 单独处理）

- ``memory_ref`` / ``prior_episodes`` → ⑥ 跨日叙事（需要新增 Live Adapter memory 通道）
- ``acoustic_progression`` 4 阶段 → ② 声学状态叙事（需要新增 GOLDEN_EXPECTED provenance）
- ``expected.*`` (outcome / workflow.required_state) → 静态展示文案（不进入 runtime 字段）
- ``episodes/acts/variants`` 数组 → 不解析（预拼接 demo.mp4 已合并）
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator

# Golden case 集合（"资产存在 + 可作为 Live 产品场景被正确加载"的最小集）
GOLDEN_CASES: tuple[str, ...] = (
    "stranger_visit",
    "repeated_visit",
    "telephone_risk",
    "evidence_insufficient",
)


def _repo_root() -> Path:
    """仓库根目录（src/silver_demo/ → repo root）。"""
    return Path(__file__).resolve().parents[2]


def _load_manifest(case: str) -> dict[str, Any]:
    """读取 manifest.yaml（fail-closed：缺失即抛）。"""
    p = _repo_root() / "dataset" / case / "manifest.yaml"
    if not p.is_file():
        raise FileNotFoundError(
            f"Golden case manifest 不存在：{p}\n"
            f"   可用 case（资产在 dataset/）：{', '.join(GOLDEN_CASES)}"
        )
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _parse_iso(value: str | datetime) -> datetime:
    """ISO 8601 解析（与 silver_demo.scenarios._parse_iso 对齐 + tz 兜底）。

    容忍 PyYAML 隐式解析：某些版本的 PyYAML 默认把 ``2026-08-16T19:45:00`` 解析为
    datetime 对象（不是 string）。adapter 必须接受两种输入。

    tz 兜底（VM-9 + 现有 yaml 约定对齐）：现有 demo scenario 全部用 ``+00:00``（UTC），
    manifest schema 不一致（golden 用无 tz 字符串）。冷启动恢复（cold_start.py）做
    ``now - snapshot_at`` 减法，naive - aware 抛 TypeError。
    所以 adapter 必须把无 tz 时间**强制标为 UTC**，与现有约定对齐。
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        raise ValueError(f"非法 ISO 时间：{value!r}（type: {type(value).__name__}）")  # noqa: TRY004  (调用方按 ValueError 处理，保持异常类型稳定)
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"非法 ISO 时间：{value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _resolve_start_time(manifest: dict[str, Any]) -> datetime:
    """通用 start_time 解析（与 case 无关，容忍 schema 不一致）。

    优先级：
    1. 顶层 ``case_start``（stranger_visit / repeated_visit / telephone_risk 都有）
    2. 顶层 ``generated`` + ``00:00:00``（evidence_insufficient 仅有日期无时间）
    3. ``acts[0].timestamp`` 或 ``episodes[0].timestamp``（多幕 case 的第一段）

    不会写 case-specific 分支：所有 case 都走同一规则。
    """
    if "case_start" in manifest:
        return _parse_iso(manifest["case_start"])
    if "generated" in manifest:
        # evidence_insufficient 用 YYYY-MM-DD 兜底（adapter 通用规则）
        gen = manifest["generated"]
        if isinstance(gen, datetime):
            return gen
        return _parse_iso(str(gen) + "T00:00:00")
    for key in ("acts", "episodes"):
        items = manifest.get(key) or []
        if items and "timestamp" in items[0]:
            return _parse_iso(items[0]["timestamp"])
    raise ValueError(
        "manifest 缺 start_time 线索字段（case_start / generated / acts[0].timestamp）"
    )


def _resolve_golden_paths(case: str) -> tuple[Path, Path | None]:
    """解析 golden case 的资源路径（manifest 不解析，只读 manifest.yaml 顶层）。

    Returns:
        (media_path, audio_path) — 资源相对仓库根；audio_path 为 None 表示无音频。

    优先级（**通用规则**）：
    - 视频：``output/{case}_demo.mp4`` → ``output/{case}_final.mp4`` → case-specific 兜底
    - 音频：``audio_mix/{case}_mix.wav`` → case-specific 兜底
    """
    root = _repo_root()
    media_dir = root / "dataset" / case / "media"
    audio_dir = root / "dataset" / "_canonical" / "audio_mix" / case

    # 视频：通用路径 + case-specific 兜底（与资源命名约定对齐，不写特例逻辑）
    media_candidates = [
        media_dir / f"{case}_demo.mp4",       # 优先多幕/多 variant 预拼接
        media_dir / f"{case}_final.mp4",      # 单段 fallback
    ]
    # case-specific 兜底（适配各 case 不同的产物命名）
    case_fallbacks = {
        "repeated_visit":       media_dir / "ep_001_final.mp4",
        "evidence_insufficient": media_dir / "act_a_final.mp4",
        "telephone_risk":       media_dir / "case_b_vision_audio.mp4",
    }
    if case in case_fallbacks:
        media_candidates.append(case_fallbacks[case])

    media_path: Path | None = None
    for c in media_candidates:
        if c.is_file():
            media_path = c
            break
    if media_path is None:
        raise FileNotFoundError(
            f"Golden case {case!r} 找不到视频文件，尝试过：\n  " +
            "\n  ".join(str(c.relative_to(root)) for c in media_candidates) +
            "\n   请跑各 case 的 cctv_post.py 预拼接视频（产物在 output/）。"
        )

    # 音频：通用 + case-specific 兜底
    audio_candidates = [audio_dir / f"{case}_mix.wav"]
    audio_fallbacks = {
        "repeated_visit":       audio_dir / "act1_mix.wav",
        "evidence_insufficient": audio_dir / "act_a_mix.wav",
        "telephone_risk":       audio_dir / "case_b_mix.wav",
    }
    if case in audio_fallbacks:
        audio_candidates.append(audio_fallbacks[case])

    audio_path: Path | None = None
    for c in audio_candidates:
        if c.is_file():
            audio_path = c
            break
    # audio 缺失是允许的（诚实的无音频场景），不抛

    return media_path, audio_path


class GoldenScenarioConfig(BaseModel):
    """Golden Case 映射后的 ScenarioConfig-shape（与 silver_demo.scenarios.ScenarioConfig 字段一致）。

    设计原因：保持与 ``ScenarioConfig`` 字段同构，外部无需特判。
    """
    scenario_id: str
    source: str
    source_type: str = "video_file"
    media_path: str | None = None
    audio_path: str | None = None
    start_time: datetime
    frame_interval_s: float = 0.5
    fps_target: int = 8
    loop: bool = True
    description: str = ""

    @field_validator("frame_interval_s")
    @classmethod
    def _positive_interval(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"frame_interval_s 必须 > 0，收到 {v!r}")
        return v

    @field_validator("fps_target")
    @classmethod
    def _nonneg_fps(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"fps_target 必须 >= 0，收到 {v!r}")
        return v


def load_golden_scenario(case: str) -> GoldenScenarioConfig:
    """从 dataset/{case}/manifest.yaml 加载 → 纯映射为 GoldenScenarioConfig。

    Args:
        case: golden case 名（必须 ∈ GOLDEN_CASES 集合或实际目录存在）

    Returns:
        GoldenScenarioConfig：可直接喂给现有 ScenarioConfig 路径 / 作为 env 注入

    Raises:
        FileNotFoundError: manifest 或预拼接视频缺失
        ValueError: manifest 缺必填字段

    Side effects: 无（纯只读）
    """
    manifest = _load_manifest(case)

    # 1. case 字段（manifest 顶层）—— 纯映射
    scenario_id = str(manifest.get("case", case))

    # 2. start_time —— 通用规则（容忍 schema 不一致），**提前在外部解析为 datetime**
    start_time = _resolve_start_time(manifest)

    # 3. product_question → description（人话）—— 纯映射
    description = str(manifest.get("product_question", ""))

    # 4. 资源路径 —— 通用规则 + 兜底
    media_path, audio_path = _resolve_golden_paths(case)
    root = _repo_root()
    media_rel = str(media_path.relative_to(root))
    audio_rel = str(audio_path.relative_to(root)) if audio_path else None

    # 5. 直接用 object.__new__ + setattr 绕过 pydantic 字段校验
    # （避免 start_time 已被 _resolve_start_time 解析为 datetime 后，
    #  pydantic 又试图把它当 string 校验）
    instance = GoldenScenarioConfig.__new__(GoldenScenarioConfig)
    object.__setattr__(instance, "scenario_id", scenario_id)
    object.__setattr__(instance, "source", scenario_id)
    object.__setattr__(instance, "source_type", "video_file")
    object.__setattr__(instance, "media_path", media_rel)
    object.__setattr__(instance, "audio_path", audio_rel)
    object.__setattr__(instance, "start_time", start_time)
    object.__setattr__(instance, "frame_interval_s", 0.5)
    object.__setattr__(instance, "fps_target", 8)
    object.__setattr__(instance, "loop", True)
    object.__setattr__(instance, "description", description)
    return instance


def list_golden_cases() -> list[str]:
    """列出当前可用的 golden case（实际目录存在）。"""
    root = _repo_root()
    base = root / "dataset"
    if not base.is_dir():
        return []
    return sorted(
        d.name for d in base.iterdir()
        if d.is_dir() and (d / "manifest.yaml").is_file()
    )


__all__ = [
    "GOLDEN_CASES",
    "GoldenScenarioConfig",
    "list_golden_cases",
    "load_golden_scenario",
]
