"""ADR-0036 Slice B · Live Viewer 入口（Replay 重放模式，可选）。

把**实时帧流录制（JSONL）**经 Live Adapter 增量投影为 ``EvidenceProjection``（VM-1 唯一事实源，
provenance=REAL_SENSOR），叠加纯展示编排，渲染为**自包含单页 HTML**（与 Artifact 模式共用
Case Viewer，仅 provenance 着色差异）。

设计取舍（对齐 ADR-0036 Slice B）：
- **不绑定传输协议**：WS 由宿主层喂帧；本 CLI 仅做「JSONL 录制 → 重放投影 → HTML」，用于
  离线回放 / 验收 / 调试，保持 torch-free、CI 友好；
- **不 import 生产 runtime**：帧以 FrameResult 契约的 JSON 形态摄入（鸭子类型映射，VM-3）；
- **fail-closed**：帧契约违规 / 渲染契约违规 → 非 0 退出，不产残缺 HTML。

用法：
    python scripts/run_live_viewer.py --frames live_frames.jsonl --output live.html
    cat live_frames.jsonl | python scripts/run_live_viewer.py --output live.html
    python scripts/run_live_viewer.py --frames live_frames.jsonl --scenario-id cam-01 --window-size 128

JSONL 每行一个对象（FrameResult 契约子集 / AudioPerceptionEvent 契约子集），按 ``type`` 分流：
- 视觉帧（无 ``type`` 字段或 ``type != "audio"``）：
    {"frame_index":0,"n_detections":2,"n_visitor_events":1,
     "perception_events":[{"event_type":"stranger_loiter"}],"warnings":[],"commands":[]}
- 音频感知（``type="audio"``，ADR-0036 Slice C Phase B 增量合并，时间轴 AUDIO 节点；
  VM-9/AC-10 守卫：不得携带 text/transcript/FORBIDDEN_AUDIO_FIELDS/媒体字节）：
    {"type":"audio","timestamp":"1700000000.0","kind":"audio_voice_raised",
     "score":0.83,"confidence":0.91,"source_segment_ids":["seg-1"],"labels":["raised"]}

退出码：
- 0：成功生成 HTML；
- 1：帧/音频/渲染契约违规（fail-closed）；
- 2：参数/IO 错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from home_perception.common.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "live_viewer.html"


def _read_frames(path: Path | None, *, scenario_id: str, window_size: int) -> str:
    """读取 JSONL 帧流并投影为 HTML（返回 HTML 文档字符串）。

    延迟导入 visualizer：参数解析/IO 阶段不拉起渲染链。
    """
    from home_perception.visualizer.viewer.live_adapter import (
        LiveIngestError,
        ProjectionAccumulator,
        build_live_presentation,
    )
    from home_perception.visualizer.viewer.render import render_case_viewer

    lines: list[str]
    if path is None:
        lines = [ln for ln in sys.stdin.read().splitlines() if ln.strip()]
    else:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("读取帧流文件失败", path=str(path), error=str(exc))
            raise _ExitCode(2) from exc
        lines = [ln for ln in raw.splitlines() if ln.strip()]

    acc = ProjectionAccumulator(scenario_id, window_size=window_size)
    for idx, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.error("条目 JSON 解析失败", line=idx, error=str(exc))
            raise _ExitCode(1) from exc
        # 按 type 分流：audio 条目 → ingest_audio（时间轴 AUDIO 节点增量合并）；
        # 其余（无 type 或 type!="audio"）→ ingest（视觉帧）。
        is_audio = isinstance(entry, dict) and entry.get("type") == "audio"
        try:
            if is_audio:
                acc.ingest_audio(entry)
            else:
                acc.ingest(entry)
        except LiveIngestError as exc:
            label = "音频" if is_audio else "帧"
            logger.error(
                f"{label}契约违规，拒绝生成（fail-closed）", line=idx, error=str(exc)
            )
            raise _ExitCode(1) from exc

    try:
        projection = acc.to_evidence_projection()
        projection, descriptor = build_live_presentation(projection)
        html_doc = render_case_viewer(projection, descriptor)
    except (LiveIngestError, ValueError) as exc:
        logger.error("投影/渲染契约违规，拒绝生成（fail-closed）", error=str(exc))
        raise _ExitCode(1) from exc
    return html_doc


class _ExitCode(Exception):
    """内部：携带退出码跳出嵌套调用（避免多层 return 透传）。"""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(str(code))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0036 Slice B Live Viewer (replay)")
    parser.add_argument(
        "--frames",
        type=Path,
        default=None,
        help="实时帧流 JSONL 路径（默认从 stdin 读取）",
    )
    parser.add_argument(
        "--scenario-id",
        default="live-session",
        help="实时会话标识（作为 scenario_id / scenario_fingerprint）",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=64,
        help="时间轴滚动窗口保留的最近帧数（默认 64）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="输出 HTML 路径（默认 live_viewer.html）",
    )
    args = parser.parse_args(argv)

    if args.window_size < 1:
        logger.error("window-size 必须 ≥1", value=args.window_size)
        return 2

    try:
        html_doc = _read_frames(
            args.frames, scenario_id=args.scenario_id, window_size=args.window_size
        )
    except _ExitCode as exc:
        return exc.code

    out: Path = args.output
    try:
        out.write_text(html_doc, encoding="utf-8")
    except OSError as exc:
        logger.error("写输出失败", path=str(out), error=str(exc))
        return 2

    logger.info(
        "Live Viewer 已生成",
        path=str(out),
        scenario_id=args.scenario_id,
        kb=round(out.stat().st_size / 1024),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
