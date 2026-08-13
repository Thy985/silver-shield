"""ADR-0035 D3 · 案例视频生成 CLI（generate_case_video）。

用法示例：
    python scripts/generate_case_video.py \
        --artifact-dir artifacts/adr0034_integration \
        --scenario-id sw_adr0034_elderly_dwell \
        --output-dir generated/demo_videos

退出码：0 成功 / 1 fail-closed（断言/校验失败）/ 2 参数错误。
import 全部延迟到 ``main()`` 内，避免无参调用即拉入 cv2/PIL。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from home_perception.visualizer.video.spec import CaseVideoSpec


def _parse_resolution(text: str) -> tuple[int, int]:
    parts = text.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("resolution 须为 WxH，如 1280x720")
    return (int(parts[0]), int(parts[1]))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_case_video",
        description="ADR-0035 D3 Evidence Story Compiler · 生成叙事案例视频",
    )
    parser.add_argument("--artifact-dir", required=True, type=Path, help="EvidenceProjection 目录")
    parser.add_argument("--scenario-id", required=True, help="待编译场景 id")
    parser.add_argument("--output-dir", required=True, type=Path, help="产物落盘根目录")
    parser.add_argument("--audience", default="general", help="受众维度（general/judges/...）")
    parser.add_argument("--template", default=None, help="显式 ScenarioTemplate 名（默认按类别自动选）")
    parser.add_argument(
        "--background", default="synthetic", choices=["synthetic", "validation"],
        help="背景层来源（D3-A 默认 synthetic）",
    )
    parser.add_argument("--fps", type=float, default=2.0, help="输出帧率")
    parser.add_argument("--resolution", type=_parse_resolution, default=(1280, 720), help="WxH")
    parser.add_argument("--version", type=int, default=1, help="产物版本号")
    parser.add_argument("--with-audio", action="store_true", help="D3-B 旁白（默认关闭）")
    parser.add_argument("--seed", type=int, default=None, help="确定性种子（默认沿用 scenario）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    spec = CaseVideoSpec(
        scenario_id=args.scenario_id,
        artifact_dir=args.artifact_dir,
        output_dir=args.output_dir,
        audience=args.audience,
        template_name=args.template,
        background=args.background,  # type: ignore[arg-type]
        fps=args.fps,
        resolution=args.resolution,
        version=args.version,
        with_audio=args.with_audio,
        seed=args.seed,
    )

    # 延迟 import：仅在实际生成时才拉入 cv2/PIL/pydantic 栈。
    from home_perception.visualizer.video.compiler import generate_case_video

    result = generate_case_video(spec)
    print(
        f"[D3] 生成完成 scenario={result.scenario_id} "
        f"frames={result.n_frames} duration_s={result.duration_s:.1f}\n"
        f"    case.mp4       -> {result.case_mp4}\n"
        f"    storyboard.yaml-> {result.storyboard_yaml}\n"
        f"    provenance.json-> {result.provenance_json}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, AssertionError, FileNotFoundError, KeyError) as exc:
        print(f"[D3] fail-closed: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001——CLI 顶层兜底，避免栈暴露
        print(f"[D3] 意外错误: {exc}", file=sys.stderr)
        sys.exit(1)
