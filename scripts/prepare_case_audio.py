"""ADR-0036 音频 E2E · 可播放音频样本准备脚本：把合成 WAV 注册进 artifact 音频目录。

背景（音频 E2E P0）：``EvidenceProjection.audio_evidence`` 只描述"系统听到了什么"
（kind/score/confidence/labels/segments），**不含**任何 url / 媒体字节（VM-9 / AC-11）。
本脚本为"可播放样本"建立**独立**的 Audio Source 绑定层——把 ``src/home_perception/
audio/tts/fixtures/`` 下确定性合成 WAV 复制到 ``{artifacts}/{sid}/audio/{kind}.wav``，
并写 ``{sid}/audio/manifest.json``（``AudioFileSource`` + kind→相对 url 映射）。

**证据与媒体严格分离**（用户铁律）：
- 本脚本**只**写 ``{sid}/audio/``（样本），绝不触碰 ``audio_evidence``（证据，在 canonical 内）；
- manifest 只有"kind → 样本相对 url"，源字节留磁盘，绝不 base64 内联进 HTML；
- 渲染层（``_render_audio_perception``）仅当 Audio Source Adapter 命中该 kind 才渲染
  ``<audio controls>``——证据本身不可播放、也不声称可播放（诚实降级，不编造）。

纪律（对齐 ADR-0036 不变式 + prepare_case_media 风格）：
- **Provenance 诚实**：样本是确定性合成素材（非真实设备录音）；manifest 不暗示"真实传感器"；
- **幂等**：``{sid}/audio/`` 已存在合法 manifest 且文件齐备 → 跳过（不重复复制）；
- **fail-closed**：fixtures 缺失 / 复制失败 / manifest 写入失败 → 退出非 0，不产残缺 manifest；
- **诚实映射**：只为 canonical 中确实出现的 kind 复制样本；fixtures 未覆盖的 kind 不编造。

用法：
    python scripts/prepare_case_audio.py
    python scripts/prepare_case_audio.py --artifacts D:/temp/artifacts --fixtures src/home_perception/audio/tts/fixtures
    python scripts/prepare_case_audio.py --force
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
_DEFAULT_FIXTURES = (
    Path(__file__).resolve().parent.parent / "src" / "home_perception" / "audio" / "tts" / "fixtures"
)

# kind → 确定性合成 WAV fixture（语义匹配）。
# audio_anomaly_other 暂无对应 fixture → 不映射（诚实：不为未覆盖 kind 编造样本）。
_FIXTURE_MAP: dict[str, str] = {
    "audio_speech_rapid": "normal_speech_fast.wav",
    "audio_voice_raised": "raised_voice_far.wav",
    "audio_telephone_persistent": "telephone_noisy.wav",
    "audio_distress_cry": "crying_reverberant.wav",
}


def _discover_audio_specs(canonical_path: Path) -> dict[str, dict]:
    """从 canonical 读取真实出现的音频 kind → 时间锚点（仅声明有的才准备样本，诚实）。

    P0-3（media_tracks 时间绑定）：返回 ``{kind: {"timestamp": float}}``——timestamp 为
    canonical ``audio_timestamp``（Unix 秒，证据层时间）；manifest 的 ``tracks`` 据此
    推导相对最早音频的 start_time（与渲染层卡片 rel 时间同源，Case Time 对齐）。
    """
    try:
        data = json.loads(canonical_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("canonical 解析失败（跳过）", path=str(canonical_path), error=str(exc))
        return {}
    raw = (data.get("artifacts") or {}).get("audio_evidence")
    if not isinstance(raw, list):
        return {}
    specs: dict[str, dict] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        k = entry.get("audio_kind")
        if isinstance(k, str) and k and k not in specs:
            ts = entry.get("audio_timestamp")
            specs[k] = {"timestamp": float(ts) if isinstance(ts, (int, float)) else 0.0}
    return specs


def _audio_valid(audio_dir: Path, manifest: dict | None, want_kinds: set[str]) -> bool:
    """幂等判定：已就绪 = AudioFileSource manifest + 每个 want_kind 的样本文件存在。"""
    if not manifest:
        return False
    if manifest.get("source_kind") != "AudioFileSource":
        return False
    files = manifest.get("files")
    if not isinstance(files, dict):
        return False
    if not want_kinds:
        return True  # 无音频 kind：空 manifest 也算就绪（无样本可放）
    for k in want_kinds:
        rel = files.get(k)
        if not isinstance(rel, str) or not rel:
            return False
        if not (audio_dir.parent.parent / rel).is_file():
            return False
    return True


def _prepare_one(
    artifacts: Path,
    fixtures: Path,
    scenario_id: str,
    audio_specs: dict[str, dict],
    *,
    force: bool,
) -> bool:
    """为单个场景准备可播放样本：复制 WAV + 写 manifest。成功 True，失败 False。

    只为 ``audio_specs`` 中能在 ``_FIXTURE_MAP`` 命中 fixture 的 kind 复制样本；其余
    kind（如 audio_anomaly_other）如实留空（不编造样本）。无样本可放时也写合法空 manifest，
    使 resolve_audio_source 返回 ``files={}``（诚实：有证据但无样本，不渲染播放控件）。

    P0-3（media_tracks 时间绑定）：manifest 增写 ``tracks``——每条样本轨带
    ``start_time``（相对最早音频 T0 的秒，与渲染层卡片 rel 时间同源）与
    ``provenance_kind``（SIMULATED：确定性合成素材，诚实标注）。
    """
    audio_dir = artifacts / scenario_id / "audio"
    existing = None
    mani_p = audio_dir / "manifest.json"
    if mani_p.exists():
        try:
            existing = json.loads(mani_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
    want_kinds = set(audio_specs)
    if not force and _audio_valid(audio_dir, existing, want_kinds):
        logger.info("音频样本已就绪，跳过（幂等）", scenario=scenario_id)
        return True

    files: dict[str, str] = {}
    missing_fixture = False
    for kind in sorted(want_kinds):
        fixture_name = _FIXTURE_MAP.get(kind)
        if not fixture_name:
            # 无对应 fixture：诚实跳过（不编造样本）。
            logger.info("无对应合成样本，跳过 kind", scenario=scenario_id, kind=kind)
            continue
        src = fixtures / fixture_name
        if not src.is_file():
            missing_fixture = True
            logger.error("音频 fixture 缺失（fail-closed）", scenario=scenario_id, kind=kind, src=str(src))
            continue
        dst_name = f"{kind}.wav"
        dst = audio_dir / dst_name
        try:
            audio_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError as exc:
            logger.error("音频样本复制失败（fail-closed）", scenario=scenario_id, error=str(exc))
            return False
        # 相对 artifacts 根（渲染层叠加 audio_base_url 解析，与媒体同契约）。
        files[kind] = f"{scenario_id}/audio/{dst_name}"

    if missing_fixture:
        # 有 kind 无 fixture：仍写合法 manifest（只含能放的样本），但整体标记失败，
        # 避免"声称准备了音频却缺样本"的残缺态（fail-closed）。
        logger.error("部分 kind 缺少 fixture，音频样本准备不完整（fail-closed）", scenario=scenario_id)
        return False

    # P0-3：tracks = 样本轨时间绑定（start_time 相对最早音频 T0，与渲染卡片 rel 同源）。
    timestamps = sorted(
        (audio_specs[k].get("timestamp", 0.0), k) for k in files
    )
    t0 = timestamps[0][0] if timestamps else 0.0
    tracks: list[dict] = []
    for ts, kind in timestamps:
        tracks.append(
            {
                "id": kind,
                "kind": kind,
                "url": files[kind],
                "start_time": round(ts - t0, 3),
                "end_time": None,  # 合成样本整段可播，无独立结束锚点
                "provenance_kind": "SIMULATED",
            }
        )
    manifest = {"source_kind": "AudioFileSource", "files": files, "tracks": tracks}
    try:
        audio_dir.mkdir(parents=True, exist_ok=True)
        mani_p.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.error("音频 manifest 写入失败（fail-closed）", scenario=scenario_id, error=str(exc))
        return False
    logger.info(
        "可播放音频样本已登记为 AudioFileSource",
        scenario=scenario_id,
        kinds=list(files.keys()),
        tracks=[t["id"] for t in tracks],
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0036 音频 E2E · 可播放音频样本准备")
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=_DEFAULT_ARTIFACTS,
        help="ADR-0034 artifact 目录（默认 artifacts/adr0034_integration）",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=_DEFAULT_FIXTURES,
        help="确定性合成 WAV 根目录（默认 src/home_perception/audio/tts/fixtures）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制覆盖已有音频样本（默认幂等跳过已就绪场景）",
    )
    args = parser.parse_args(argv)

    if not args.fixtures.is_dir():
        logger.error("fixtures 目录不存在（fail-closed）", path=str(args.fixtures))
        return 2
    if not args.artifacts.is_dir():
        logger.error("artifacts 目录不存在（fail-closed）", path=str(args.artifacts))
        return 2

    ok = True
    for canonical in sorted(args.artifacts.glob("*.canonical.json")):
        sid = canonical.name[: -len(".canonical.json")]
        audio_specs = _discover_audio_specs(canonical)
        if not audio_specs:
            # 该场景无音频证据：确保无遗留音频目录（干净态），跳过准备。
            leftover = args.artifacts / sid / "audio"
            if leftover.exists():
                logger.info("场景无音频证据，清理残留音频目录", scenario=sid)
                shutil.rmtree(leftover, ignore_errors=True)
            continue
        if not _prepare_one(args.artifacts, args.fixtures, sid, audio_specs, force=args.force):
            ok = False

    if not ok:
        logger.error("可播放音频样本准备存在失败项（fail-closed）")
        return 1
    logger.info("可播放音频样本准备完成", artifacts=str(args.artifacts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
