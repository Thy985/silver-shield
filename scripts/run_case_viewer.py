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

    # render_case_viewer 的 ValueError 是第二道防御层（loader 已保证投影结构合法）。
    # Slice A.1：从 artifact 目录只读解析媒体 manifest；并据输出位置计算媒体相对 URL。
    out_parent = args.output.parent if args.output.parent else Path(".")
    try:
        rel_to_artifacts = os.path.relpath(args.artifacts, out_parent)
    except ValueError:
        rel_to_artifacts = str(args.artifacts)
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
