"""ADR-0036 P4 整改 · 真实案例媒体准备脚本：把真实演示视频注册进 artifact 媒体目录。

背景（验收批判 P4 致命项）：默认 Artifact 模式 Case Video 是空画布 + "无媒体绑定"
占位文案——主视觉不"讲案例"。本脚本把 ``data/demo/`` 下的**真实 CCTV / 门口视频**
复制进 ``{artifacts}/{sid}/media/case.mp4`` 并写 ``manifest.json``
（``ArtifactVideoSource``），使 ``run_case_viewer.py`` 默认渲染即产出可播放的
``<video>``（复用 Slice A.1/D 已实现的 Media Source Adapter 只读解析路径，零改动
``media_source.py`` / ``render.py`` 核心逻辑）。

纪律（对齐 ADR-0036 不变式）：
- **媒体字节不进 View Model**（VM-10/AC-11）：manifest 只持 ``video_url``（相对
  artifacts 根的路径字符串），字节留在磁盘，绝不 base64 内联进 HTML；
- **Provenance 诚实**（P8/P11）：复制的是**真实演示视频**，但场景本身仍是
  SIMULATED（程序化仿真闭环）——provenance 由 loader 按 artifact 投影，本脚本不
  改写任何 provenance；manifest 不含任何"真实传感器"暗示；
- **幂等**：media 目录已存在有效 ``ArtifactVideoSource`` manifest 且 video_url
  指向存在的文件 → 跳过（不重复复制大文件）；``--force`` 强制覆盖；
- **fail-closed**：视频缺失 / cv2 探测失败 → 退出非 0，不产残缺 manifest。

用法：
    python scripts/prepare_case_media.py
    python scripts/prepare_case_media.py --artifacts D:/temp/artifacts --media-root data/demo
    python scripts/prepare_case_media.py --map alarm=CCTV_Surveillance_Final.mp4,benign=Delivery_Courier_Final.mp4

默认映射（语义匹配真实视频）：
- sw_adr0034_alarm        → CCTV_Surveillance_Final.mp4（异常监控 60.5s）
- sw_adr0034_benign       → Delivery_Courier_Final.mp4（正常快递到访 40.3s）
- sw_adr0034_cross_modal  → CCTV_Surveillance_Final.mp4（复用监控素材）
- sw_adr0034_elderly_dwell→ real_doorway.mp4（真实门口 3.8s，老人停留语义匹配）
- sw_adr0034_audio_e2e    → CCTV_Surveillance_Final.mp4（音频 E2E 验收场景，主视觉复用监控）
- sw_adr0034_high_risk    → CCTV_Surveillance_Final.mp4（高风险场景，复用监控素材）

"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from home_perception.common.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_ARTIFACTS = (
    Path(__file__).resolve().parent.parent / "artifacts" / "adr0034_integration"
)
_DEFAULT_MEDIA_ROOT = Path(__file__).resolve().parent.parent / "data" / "demo"

# 默认场景 → 演示视频映射（语义匹配；key 为 scenario_id，value 为 media_root 下文件名）。
# 音频 E2E 整改：补 audio_e2e / high_risk（P0 验收目标场景也须有真实主轴画面，否则黑屏）。
_DEFAULT_MEDIA_MAP: dict[str, str] = {
    "sw_adr0034_alarm": "CCTV_Surveillance_Final.mp4",
    "sw_adr0034_benign": "Delivery_Courier_Final.mp4",
    "sw_adr0034_cross_modal": "CCTV_Surveillance_Final.mp4",
    "sw_adr0034_elderly_dwell": "real_doorway.mp4",
    "sw_adr0034_audio_e2e": "CCTV_Surveillance_Final.mp4",
    "sw_adr0034_high_risk": "CCTV_Surveillance_Final.mp4",
}

# 黄金案例集（Golden Scenario Set）媒体映射：值相对 ``data/golden/`` 根（G0-4 CI 生产）。
# ``build_trusted_case --golden`` 时经 ``--media-root data/golden`` + 本映射挂载真实 golden
# 视频资产（data/golden 整体 gitignore；CI 无资产时 --missing-skip 跳过，媒体为可选展示增强）。
# 仅映射有专属视频资产的 case；benign 无专属 golden 视频 → 不映射（缺失跳过，保持诚实）。
_DEFAULT_GOLDEN_MEDIA_MAP: dict[str, str] = {
    "sw_golden_repeated_visit": "repeated_visit/output/repeated_visit_demo.mp4",
}


def _probe_video(path: Path) -> tuple[int, float, float]:
    """读取视频元信息（帧数 / fps / 时长）。cv2 缺失 → ImportError（fail-closed）。

    独立函数便于测试 monkeypatch（避免测试环境强依赖 cv2）。
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise OSError(f"无法打开视频：{path}")
    try:
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    finally:
        cap.release()
    if n <= 0 or fps <= 0:
        raise OSError(f"视频元信息非法（frame_count={n}, fps={fps}）：{path}")
    return (n, fps, n / fps)


def _read_existing_manifest(media_dir: Path) -> dict | None:
    """读取已有 manifest（幂等判定用）；不存在 / 解析失败 → None。"""
    p = media_dir / "manifest.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _media_valid(media_dir: Path, manifest: dict | None) -> bool:
    """幂等判定：已有 ArtifactVideoSource manifest 且 video_url 指向 prepare 自身产物。

    仅当 ``video_url == "{sid}/media/case.mp4"``（本脚本的产物路径）且文件真实存在
    才算"已就绪"。D3 导出（``--export-case-video``）写入的 manifest 指向
    ``{sid}__v<ver>/case.mp4`` 子目录（灰底合成帧），**不视为**本脚本已准备的真实
    媒体——避免把合成帧误当真实案例视频（P4 验收红线）。
    """
    if not manifest:
        return False
    if manifest.get("source_kind") != "ArtifactVideoSource":
        return False
    vurl = manifest.get("video_url")
    if not isinstance(vurl, str) or not vurl:
        return False
    # 本脚本的产物路径：{sid}/media/case.mp4（video_url 相对 artifacts 根）。
    sid = media_dir.parent.name
    if vurl != f"{sid}/media/case.mp4":
        return False
    # 路径穿越防护：拒绝 ".."（video_url 已按固定格式比对，防御兜底）。
    if ".." in vurl:
        return False
    return (media_dir.parent.parent / vurl).is_file()


def _prepare_one(
    artifacts: Path,
    media_root: Path,
    scenario_id: str,
    video_name: str,
    *,
    force: bool,
    missing_skip: bool = False,
) -> bool | None:
    """为单个场景准备真实视频：复制 + 写 manifest。成功 True，失败 False，跳过 None。

    ``missing_skip=True`` 时，演示视频缺失 → 返回 ``None``（跳过该场景，不计失败、
    不产残缺 manifest）——供 ``build_trusted_case`` / CI 集成用：真实演示视频 gitignore
    （不入库），CI 全新 checkout 无视频时媒体是**可选的展示增强**，缺失应跳过而非 fail
    （媒体缺失不影响可信 artifact 完整性；本地有视频则照常挂上）。
    """
    src = media_root / video_name
    if not src.is_file():
        if missing_skip:
            logger.warning(
                "演示视频缺失（missing-skip 跳过，媒体为可选增强）",
                scenario=scenario_id,
                src=str(src),
            )
            return None
        logger.error("演示视频缺失（fail-closed）", scenario=scenario_id, src=str(src))
        return False

    media_dir = artifacts / scenario_id / "media"
    existing = _read_existing_manifest(media_dir)
    if not force and _media_valid(media_dir, existing):
        logger.info("媒体已就绪，跳过（幂等）", scenario=scenario_id, video_url=existing["video_url"])
        return True

    # 复制视频（覆盖写：--force 语义 / 首次准备）。shutil.copy2 保留元数据。
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
        dst = media_dir / "case.mp4"
        shutil.copy2(src, dst)
    except OSError as exc:
        logger.error("视频复制失败（fail-closed）", scenario=scenario_id, error=str(exc))
        return False

    # cv2 探测真实元信息（fail-closed：探测失败不产残缺 manifest）。
    try:
        n_frames, fps, duration_s = _probe_video(dst)
    except (OSError, ImportError) as exc:
        logger.error("视频元信息探测失败（fail-closed）", scenario=scenario_id, error=str(exc))
        return False

    # video_url 相对 artifacts 根（渲染层叠加 media_base_url 解析，与 D3 导出同契约）。
    rel = f"{scenario_id}/media/case.mp4"
    manifest = {
        "source_kind": "ArtifactVideoSource",
        "frame_count": n_frames,
        "fps": fps,
        "duration_sec": duration_s,
        "frame_template": "",
        "video_url": rel,
    }
    try:
        (media_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.error("manifest 写入失败（fail-closed）", scenario=scenario_id, error=str(exc))
        return False
    logger.info(
        "真实案例媒体已登记为 ArtifactVideoSource",
        scenario=scenario_id,
        video_url=rel,
        frames=n_frames,
        fps=fps,
        duration_s=round(duration_s, 2),
        source=video_name,
    )
    return True


def _parse_map(text: str) -> dict[str, str]:
    """解析 ``sid=video,sid2=video2`` 映射（覆盖默认）。"""
    mapping: dict[str, str] = {}
    for pair in text.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise argparse.ArgumentTypeError(
                f"媒体映射须为 sid=video 形式（收到：{pair!r}）"
            )
        sid, video = (part.strip() for part in pair.split("=", 1))
        if not sid or not video:
            raise argparse.ArgumentTypeError(f"媒体映射 sid/video 均非空（收到：{pair!r}）")
        mapping[sid] = video
    if not mapping:
        raise argparse.ArgumentTypeError("媒体映射为空")
    return mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0036 P4 · 真实案例媒体准备")
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=_DEFAULT_ARTIFACTS,
        help="ADR-0034 artifact 目录（默认 artifacts/adr0034_integration）",
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        default=_DEFAULT_MEDIA_ROOT,
        help="真实演示视频根目录（默认 data/demo）",
    )
    parser.add_argument(
        "--map",
        type=_parse_map,
        default=None,
        help='覆盖默认映射：sid=video,sid2=video2（如 alarm=CCTV_Surveillance_Final.mp4）',
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制覆盖已有媒体（默认幂等跳过已就绪场景）",
    )
    parser.add_argument(
        "--missing-skip",
        action="store_true",
        help=(
            "演示视频缺失时跳过该场景（媒体为可选展示增强，缺失不影响 artifact 可信性）；"
            "供 build_trusted_case / CI 集成用（真实演示视频 gitignore，CI 无视频不红）"
        ),
    )
    args = parser.parse_args(argv)

    media_map = args.map if args.map is not None else _DEFAULT_MEDIA_MAP

    # 前置校验：media_root 存在（fail-closed，避免逐场景才报难排障的缺失）。
    if not args.media_root.is_dir():
        logger.error("media_root 不存在（fail-closed）", path=str(args.media_root))
        return 2

    ok = True
    for sid, video in media_map.items():
        result = _prepare_one(
            args.artifacts,
            args.media_root,
            sid,
            video,
            force=args.force,
            missing_skip=args.missing_skip,
        )
        if result is None:
            continue  # missing-skip：该场景媒体跳过（不计失败）
        if not result:
            ok = False
    if not ok:
        logger.error("真实案例媒体准备存在失败项（fail-closed）")
        return 1

    logger.info(
        "真实案例媒体准备完成",
        artifacts=str(args.artifacts),
        scenarios=len(media_map),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
