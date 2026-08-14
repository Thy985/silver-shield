"""ADR-0036 Slice A.1 · Media Source Adapter（只读解析层，非生成层）。

铁律（用户决策）：
- **只读解析**：本模块只"读" artifact/media 目录里的 ``manifest.json``，绝不"生成"帧/
  视频（不调 ``ScenarioCompiler.render_frames``、不接 Scenario/Simulation Runtime、不
  base64 内联 660 帧导致 HTML 膨胀到几十 MB）。
- 媒体字节绝不进 View Model（VM-10 / AC-11）：``MediaManifest`` 只持 ref/template/count，
  不持任何媒体字节；``EvidenceProjection`` 只持 media ref/timestamp。
- VM-3：只 import stdlib + ``case_presentation.MediaSourceKind``，**绝不** import
  ``silver_demo`` / 生产 runtime（viewer/ 仍是 import 图死胡同叶子）。

契约：
- ``resolve_media_source(base_dir, scenario_id, source_kind)``：
  - ``SyntheticFrameSource``：读 ``{base_dir}/{sid}/media/manifest.json``（frames 模板）；
    目录/文件缺失 → 返回 ``None``（降级为无媒体，Case Viewer 画布留空，不崩）。
  - ``ArtifactVideoSource``：读同一 manifest（``video_url``）；缺失 → ``None``。
  - ``LiveFrameSource``：Slice A 不实现运行时帧源 → 返回 ``None``（未来 slice 注入）。
- 结构非法（字段类型/缺关键字段）→ 抛 ``MediaSourceError``（fail-closed，不产残缺 manifest）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from home_perception.visualizer.viewer.case_presentation import MediaSourceKind

# 媒体资产目录结构（对齐用户决策）：
#   {base_dir}/{scenario_id}/media/manifest.json
#   {base_dir}/{scenario_id}/media/frames/{idx:06d}.png   （SyntheticFrameSource）
#   {base_dir}/{scenario_id}/media/case.mp4              （ArtifactVideoSource，可选）
_MEDIA_SUBDIR = "media"
_MANIFEST_FILENAME = "manifest.json"


class MediaManifest(TypedDict):
    """媒体源解析结果（只读；不持任何媒体字节，VM-10/AC-11）。

    - ``source_kind``：解析出的源类型（可能与 binding 不一致时以 manifest 为准）；
    - ``frame_count`` / ``fps`` / ``duration_sec``：媒体时钟参数（驱动 MediaPlayer）；
    - ``frame_template``：相对 **media base 目录** 的帧 URL 模板（``{idx:06d}`` 占位）；
      渲染层会再叠加 ``media_base_url``（HTML→artifact 的相对路径）形成最终可解析 URL；
    - ``video_url``：``ArtifactVideoSource`` 用；``SyntheticFrameSource`` 留空串。
    """

    source_kind: MediaSourceKind
    frame_count: int
    fps: float
    duration_sec: float
    frame_template: str
    video_url: str


class MediaSourceError(ValueError):
    """媒体源 manifest 结构非法（fail-closed：拒绝产残缺 manifest）。"""


def _read_manifest_json(media_dir: Path, owner: str) -> dict:
    p = media_dir / _MANIFEST_FILENAME
    if not p.exists():
        raise FileNotFoundError(f"{owner} 无媒体 manifest：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaSourceError(f"{owner} 媒体 manifest 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise MediaSourceError(f"{owner} 媒体 manifest 顶层非对象（fail-closed）")
    return data


def resolve_media_source(
    base_dir: str | Path,
    scenario_id: str,
    source_kind: MediaSourceKind,
) -> MediaManifest | None:
    """只读解析媒体源 manifest（绝不生成帧/视频）。

    Args:
        base_dir: artifact 根目录（内含 ``{scenario_id}/media/``）。
        scenario_id: 场景标识（用于定位媒体子目录）。
        source_kind: 绑定声明的源类型（仅用于 ``LiveFrameSource`` 早返与诊断）。

    Returns:
        ``MediaManifest``：解析成功；``None``：无媒体资产（降级，不崩）；
        ``LiveFrameSource``：Slice A 不实现 → ``None``。

    Raises:
        MediaSourceError: manifest 结构非法（字段类型/缺关键字段，fail-closed）。
    """
    # LiveFrameSource 是运行时注入的帧源，Slice A 不实现（未来 slice 接 runtime）。
    if source_kind == "LiveFrameSource":
        return None

    media_dir = Path(base_dir) / scenario_id / _MEDIA_SUBDIR
    # 媒体资产是 artifact 的可选组成部分：缺失即降级（画布留空），fail-open 不崩。
    if not media_dir.exists():
        return None
    try:
        data = _read_manifest_json(media_dir, f"media[{scenario_id}]")
    except FileNotFoundError:
        return None

    # 源类型以 manifest 声明为准（可能比 binding 更精确）。
    kind = data.get("source_kind", source_kind)
    if kind not in ("SyntheticFrameSource", "ArtifactVideoSource"):
        raise MediaSourceError(
            f"media[{scenario_id}].source_kind 非法：{kind!r}（fail-closed）"
        )

    frame_count = data.get("frame_count")
    fps = data.get("fps")
    duration_sec = data.get("duration_sec")
    frame_template = data.get("frame_template", "")
    video_url = data.get("video_url", "")

    # 字段强校验（fail-closed：类型/取值非法 → 拒绝，不产残缺 manifest）。
    if not isinstance(frame_count, int) or frame_count < 0:
        raise MediaSourceError(f"media[{scenario_id}].frame_count 非法（fail-closed）")
    if not isinstance(fps, (int, float)) or fps <= 0:
        raise MediaSourceError(f"media[{scenario_id}].fps 非法（fail-closed）")
    if not isinstance(duration_sec, (int, float)) or duration_sec <= 0:
        raise MediaSourceError(f"media[{scenario_id}].duration_sec 非法（fail-closed）")
    if not isinstance(frame_template, str):
        raise MediaSourceError(f"media[{scenario_id}].frame_template 类型非法（fail-closed）")
    if not isinstance(video_url, str):
        raise MediaSourceError(f"media[{scenario_id}].video_url 类型非法（fail-closed）")

    if kind == "ArtifactVideoSource":
        if not video_url:
            raise MediaSourceError(
                f"media[{scenario_id}].video_url 缺失（ArtifactVideoSource，fail-closed）"
            )
    else:  # SyntheticFrameSource
        if not frame_template:
            raise MediaSourceError(
                f"media[{scenario_id}].frame_template 缺失（SyntheticFrameSource，fail-closed）"
            )

    return MediaManifest(
        source_kind=kind,
        frame_count=frame_count,
        fps=float(fps),
        duration_sec=float(duration_sec),
        frame_template=frame_template,
        video_url=video_url,
    )


__all__ = [
    "MediaManifest",
    "MediaSourceError",
    "resolve_media_source",
]
