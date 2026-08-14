"""ADR-0036 Slice A · Case Viewer 入口：一次运行 → 统一 Case Viewer HTML。

把 ADR-0034 落盘 artifact（``artifacts/adr0034_integration/``）经 Artifact Adapter 投影为
``EvidenceProjection``（VM-1 唯一事实源），叠加纯展示编排 ``CasePresentationDescriptor``
（VM-11），渲染为**自包含单页 HTML**（D4：ECharts 内联，零服务器、浏览器直开）。

用法：
    python scripts/run_case_viewer.py
    python scripts/run_case_viewer.py --artifacts D:/temp/d1-artifacts --output out.html
    python scripts/run_case_viewer.py --descriptor case_descriptor.json

退出码（D9 零行为：不接 CI 门禁，默认 0 = 生成成功；fail-closed 时才非 0）：
- 0：成功生成 HTML；
- 1：投影/展示编排契约违规（artifact 缺失/字段演化/descriptor 含事实字段）——fail-closed；
- 2：参数/目录错误。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath

from home_perception.common.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_ARTIFACTS = (
    Path(__file__).resolve().parent.parent
    / "artifacts"
    / "adr0034_integration"
)
_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "case_viewer.html"


def _parse_resolution(text: str) -> tuple[int, int]:
    """解析 ``WxH`` 分辨率（镜像 generate_case_video.py 的契约）。"""
    parts = text.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("resolution 须为 WxH，如 1280x720")
    return (int(parts[0]), int(parts[1]))


def _d3_generate_case_video(spec):
    """D3 导出入口（Slice D · AC-6）。

    懒导入隔离 cv2：cv2 缺失环境（如 CI 托管 venv）导入即在调用处抛 ImportError，
    由调用方 fail-closed（绝不静默跳过导出）。**测试可 monkeypatch 本函数**以绕过重依赖。
    """
    from home_perception.visualizer.video.compiler import generate_case_video

    return generate_case_video(spec)


def _export_case_videos(args, projection, descriptor) -> bool:
    """Slice D (AC-6)：D3 导出 Case Video 并登记为 ArtifactVideoSource。

    铁律：
    - 用 ``CaseVideoSpec``（Case Video 叙事路径，非 Analysis Video 重新产品化）；
    - ``with_audio`` 维持 ``False``（D3-B 未实现：compiler 会 ``NotImplementedError``
      fail-closed，绝不静默产出"无声片冒充有声片"）；
    - 不修改 ``EvidenceProjection``（VM-1 唯一事实源），仅向 ``{artifacts}/{sid}/media/``
      新增 manifest（媒体字节绝不进 View Model，VM-10/AC-11）；
    - 导出失败 → 返回 ``False``（调用方 fail-closed 退出 1，绝不静默产残缺 HTML）。

    Returns:
        True：全部场景导出并登记成功；False：任一场景失败（fail-closed）。
    """
    scenarios = projection.get("scenarios") or ()
    if not scenarios:
        logger.error("导出失败：projection 无场景（fail-closed）")
        return False

    from home_perception.visualizer.video.spec import CaseVideoSpec

    last_rel = ""
    for s in scenarios:
        sid = s["scenario_id"]
        media_dir = Path(args.artifacts) / sid / "media"
        spec = CaseVideoSpec(
            scenario_id=sid,
            artifact_dir=args.artifacts,
            output_dir=media_dir,
            fps=args.export_fps,
            resolution=args.export_resolution,
            version=args.export_version,
            with_audio=False,
        )
        try:
            result = _d3_generate_case_video(spec)
        except Exception as exc:  # noqa: BLE001 导出失败 fail-closed，不静默跳过
            logger.error("D3 导出失败（fail-closed）", scenario=sid, error=str(exc))
            return False

        # 登记 manifest：source_kind=ArtifactVideoSource，video_url 相对 artifacts 根
        # （渲染层会再叠加 media_base_url，与 frame_template 同契约）。
        try:
            rel_mp4 = Path(result.case_mp4).relative_to(Path(args.artifacts)).as_posix()
        except ValueError:
            # case.mp4 不在 artifacts 树下（异常布局）→ fail-closed 拒绝登记。
            logger.error(
                "D3 导出路径越界（fail-closed）",
                scenario=sid,
                case_mp4=str(result.case_mp4),
            )
            return False
        manifest = {
            "source_kind": "ArtifactVideoSource",
            "frame_count": int(result.n_frames),
            "fps": float(args.export_fps),
            "duration_sec": float(result.duration_s),
            "frame_template": "",
            "video_url": rel_mp4,
        }
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("D3 导出已登记为 ArtifactVideoSource", scenario=sid, video_url=rel_mp4)
        last_rel = rel_mp4

    # 诚实脚注：媒体绑定指向真实导出的 ArtifactVideoSource（VM-1 唯一事实源不变）。
    descriptor["media_binding"]["source_kind"] = "ArtifactVideoSource"
    descriptor["media_binding"]["ref"] = last_rel
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0036 Slice A Case Viewer")
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=_DEFAULT_ARTIFACTS,
        help="ADR-0034 artifact 目录（默认 artifacts/adr0034_integration）",
    )
    parser.add_argument(
        "--descriptor",
        type=Path,
        default=None,
        help="可选 CasePresentationDescriptor JSON（纯展示编排；含事实字段将拒绝，AC-13）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="输出 HTML 路径（默认 case_viewer.html）",
    )
    # Slice D（AC-6）：D3 导出接入 Case Viewer —— 将导出的 case.mp4 登记为 ArtifactVideoSource。
    parser.add_argument(
        "--export-case-video",
        action="store_true",
        help="Slice D (AC-6)：对每个场景调用 D3 generate_case_video，将导出 case.mp4 登记为 ArtifactVideoSource",
    )
    parser.add_argument(
        "--export-fps",
        type=float,
        default=2.0,
        help="导出 Case Video 帧率（默认 2.0，与 ADR-0032 camera.fps 默认一致）",
    )
    parser.add_argument(
        "--export-resolution",
        type=_parse_resolution,
        default="1280x720",
        help="导出 Case Video 分辨率 WxH（默认 1280x720）",
    )
    parser.add_argument(
        "--export-version",
        type=int,
        default=1,
        help="导出 Case Video 版本号（命名空间一部分，影响产物子目录 <sid>__v<ver>）",
    )
    args = parser.parse_args(argv)

    # 延迟导入：--help / 参数错误时不拉起 visualizer 链。
    from home_perception.visualizer.viewer import (
        EvidenceProjectionError,
        load_case_presentation,
        render_case_viewer,
    )

    try:
        projection, descriptor = load_case_presentation(
            args.artifacts, descriptor_path=args.descriptor
        )
    except FileNotFoundError as exc:
        logger.error("artifact 目录或 descriptor 不存在", path=str(args.artifacts), error=str(exc))
        return 2
    except (EvidenceProjectionError, ValueError) as exc:
        # EvidenceProjectionError：artifact 契约违规；ValueError：descriptor 含事实字段（AC-13）
        logger.error("投影/展示编排契约违规，拒绝生成（fail-closed）", error=str(exc))
        return 1

    # Slice D（AC-6）：导出 Case Video 并登记为 ArtifactVideoSource（须早于渲染，使
    # resolve_media_source 在渲染期能解析到真实 manifest）。导出失败 → fail-closed 退出 1。
    if args.export_case_video and not _export_case_videos(args, projection, descriptor):
        return 1

    # render_case_viewer 的 ValueError 是第二道防御层（loader 已保证投影结构合法）。
    # Slice A.1：从 artifact 目录只读解析媒体 manifest；并据输出位置计算媒体相对 URL。
    out_parent = args.output.parent if args.output.parent else Path(".")
    try:
        rel_to_artifacts = os.path.relpath(args.artifacts, out_parent)
    except ValueError:
        # 跨盘符（Windows）relpath 不可算 → 媒体相对 URL 无法构造，fail-closed 退出。
        # 退化为绝对路径会让浏览器经 file:// 跨域/越权读取，故拒绝而非放行（评审 R2-#10）。
        logger.error(
            "媒体相对 URL 无法构造：artifact 与输出跨盘符（fail-closed）",
            artifacts=str(args.artifacts),
            output=str(args.output),
        )
        return 2
    # 关键：relpath 在 Windows 上产出原生分隔符 ``\``，而 ``PurePosixPath`` 会把它当字面量
    # 字符（posix 不视 ``\`` 为分隔符），必须先用 ``os.sep`` 归一为正斜杠再构造，否则
    # 浏览器拿到的 frame_template 含 ``..\`` 无法解析（媒体帧加载失败）。
    media_base_url = PurePosixPath(rel_to_artifacts.replace(os.sep, "/")).as_posix().rstrip("/") + "/"
    try:
        html_doc = render_case_viewer(
            projection,
            descriptor,
            media_base_dir=args.artifacts,
            media_base_url=media_base_url,
        )
    except ValueError as exc:
        logger.error("渲染拒绝（fail-closed）", error=str(exc))
        return 1

    out: Path = args.output
    try:
        out.write_text(html_doc, encoding="utf-8")
    except OSError as exc:
        logger.error("写输出失败", path=str(out), error=str(exc))
        return 2

    n_scenarios = projection["meta"]["scenario_count"]
    logger.info(
        "Case Viewer 已生成",
        path=str(out),
        case_id=descriptor["case_id"],
        scenarios=n_scenarios,
        kb=round(out.stat().st_size / 1024),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
