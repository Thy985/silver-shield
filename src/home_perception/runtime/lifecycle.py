"""运行期生命周期与 Demo 主流程（P0-10 · 装配联调）。

`run_demo(settings)` 是 P0-10 的"一键启动"入口：
1. 构造复用型 `YOLODetector`（跨场景同一实例，保证 track_id 跨帧一致）
2. 逐个 CAVIAR 场景跑完整 7 层流水线（每场景独立状态，互不污染）
3. 捕获 Ctrl-C / SIGTERM 优雅停止（不丢已处理成果）
4. 输出结构化 Demo 汇总日志（供评委一键复现）

边界（P0-10 = 工程层）：
- 只编排"启动 → 跑 → 收尾"，不引入任何风险判定逻辑
- 复用已验证组件；不接真实萤石（demo 模式用 CAVIAR；realtime 留待 v1）
"""
from __future__ import annotations

import signal as _signal
from datetime import datetime
from typing import List

from ..common.logging import get_logger
from ..core.config import Settings
from ..detection.detector import YOLODetector
from .config import read_caviar_frames
from .pipeline import DemoClock, PerceptionPipeline, RunSummary

log = get_logger(__name__)


def _parse_demo_clock_start(value: str) -> datetime:
    """解析 runtime.demo_clock_start（ISO 8601，支持 'Z' 或 '+00:00' 时区后缀）。

    失败抛出 ValueError，让配置错误在启动时即暴露（不静默回退到错误时间线）。
    """
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"runtime.demo_clock_start 非法 ISO 时间: {value!r}") from exc


def _install_shutdown_handler() -> None:
    """注册 Ctrl-C / SIGTERM → KeyboardInterrupt，让 run() 的优雅停止生效。

    非主线程 / 不支持的平台静默跳过（不阻塞 Demo 启动）。
    """
    try:
        def _handler(signum, frame):  # noqa: ANN001
            raise KeyboardInterrupt()

        _signal.signal(_signal.SIGINT, _handler)
        if hasattr(_signal, "SIGTERM"):
            _signal.signal(_signal.SIGTERM, _handler)
    except (ValueError, OSError, AttributeError):
        # 非主线程或平台不支持：忽略
        pass


def run_demo(settings: Settings) -> List[RunSummary]:
    """P0-10 Demo 主流程：CAVIAR 三个场景端到端跑通并产出汇总。

    Returns:
        List[RunSummary]：每个成功加载的场景一条汇总（fixtures 缺失的场景跳过）。

    Raises:
        NotImplementedError：runtime.mode != "demo"（realtime 留待 v1）。
    """
    if settings.runtime.mode != "demo":
        raise NotImplementedError(
            f"runtime.mode={settings.runtime.mode!r} 尚未实现；P0-10 仅支持 demo 模式（CAVIAR 复现）"
        )
    _install_shutdown_handler()

    # detector 跨场景复用：同一实例保证 track_id 跨帧一致（model.track persist=True 要求）
    # 直接构造 YOLODetector，避免仅为"提取 detector"白白装配一次完整 7 层流水线（审查 #2）
    shared_detector = YOLODetector(
        model=settings.runtime.detector_model or settings.detection.model,
        conf_threshold=settings.runtime.detector_conf or settings.detection.conf_threshold,
        classes=settings.detection.classes,
        device=settings.detection.device,
        imgsz=settings.runtime.detector_imgsz or settings.detection.imgsz,
        profile=settings.detection.imgsz_profile,
        enable_track=settings.detection.enable_track,
        tracker=settings.detection.tracker,
    )
    log.info(
        "demo.start",
        mode=settings.runtime.mode,
        scenarios=list(settings.runtime.demo_scenarios),
        caviar_base_dir=settings.runtime.caviar_base_dir,
    )
    detector_loaded = False
    summaries: List[RunSummary] = []

    try:
        for scenario in settings.runtime.demo_scenarios:
            # 每场景独立 tracker/builder/feature/rule/decision/executor（状态隔离，互不污染）
            # 注入 DemoClock：模拟 2fps 视频时序（0.5s/帧），让离场判定确定可复现
            clock = DemoClock(
                start=_parse_demo_clock_start(settings.runtime.demo_clock_start),
                interval_s=0.5,
            )
            pipeline = PerceptionPipeline.from_settings(
                settings, detector=shared_detector, device_id=scenario,
                now_provider=clock, frame_interval_s=0.5,
            )
            if not detector_loaded:
                pipeline.load_detector()
                detector_loaded = True

            frames = read_caviar_frames(
                settings.runtime.caviar_base_dir, scenario, settings.runtime.frame_glob
            )
            if not frames:
                log.warning(
                    "demo.scenario_skipped",
                    scenario=scenario,
                    reason="no frames (fixtures 缺失或未装 cv2)",
                )
                continue

            summary = pipeline.run(frames, scenario=scenario)
            summaries.append(summary)
            log.info("demo.scenario_done", **summary.to_log())
    except KeyboardInterrupt:
        log.info("demo.interrupted_by_user")
    finally:
        # 释放 detector 模型引用（跨场景复用实例，退出前统一清理）
        try:
            shared_detector.unload()
        except Exception:  # pragma: no cover - 防御性
            pass

    _emit_demo_summary(summaries)
    return summaries


def _emit_demo_summary(summaries: List[RunSummary]) -> None:
    """汇总所有场景的运行指标，输出一条结构化日志（供评委/CI 复核）。"""
    totals = {
        "scenarios_run": len(summaries),
        "total_frames": sum(s.frames_processed for s in summaries),
        "total_visitor_events": sum(s.n_visitor_events for s in summaries),
        "total_perception": sum(s.n_perception for s in summaries),
        "total_warnings": sum(s.n_warnings for s in summaries),
        "total_commands": sum(s.n_commands for s in summaries),
        "total_publish": sum(s.publish_count for s in summaries),
        "total_errors": sum(s.errors for s in summaries),
    }
    if totals["scenarios_run"] == 0:
        # 审查 #7：所有场景 fixtures 缺失 / cv2 未装 → 0 帧，需更强提示避免"看似启动正常"
        log.warning(
            "demo.all_scenarios_skipped",
            reason="所有场景 fixtures 缺失或 cv2 未装，未处理任何帧",
        )
    log.info("demo.summary", **totals)
