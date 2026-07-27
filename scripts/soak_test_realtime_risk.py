"""ADR-0021 Stage E · 运行稳定性验证（Soak Test）。

对应工程方案 docs/DESIGN-realtime-riskstream-engineering-plan.md §9.2。

定位
----
Stage A-D 证明**逻辑正确性**（单测/契约/集成测试全绿）；本脚本证明**运行稳定性**
（长时间运行下状态不泄漏、生命周期闭合、延迟分布合理）。

**不是什么**：
- 不验证业务准确率（误报率/漏报率）—— 那是 E2E + 人工标注的职责
- 不验证内存泄漏（Python GC + tracemalloc 是另一套工具链）
- 不验证并发安全（pipeline 单线程）

用法
----
    # 单场景运行
    python scripts/soak_test_realtime_risk.py \\
        --scenario config/scenarios/soak_s2_abnormal_dwell.yaml \\
        --duration 300

    # 5 分钟烟雾测试（验证脚本本身）
    python scripts/soak_test_realtime_risk.py --duration 300

输出：reports/soak_YYYYMMDD_HHMMSS.json

环境要求
--------
必须装在带 torch / ultralytics / opencv 的 venv（system Python 3.14），
managed venv 缺 torch 会 SKIP。复用 e2e_validate_demo.py 的环境门禁。
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import quantiles
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


# ============================================================================
# 环境门禁
# ============================================================================

def _env_gate() -> bool:
    """检查是否具备真实运行时（torch + ultralytics + cv2）。"""
    try:
        import torch  # noqa: F401
        import ultralytics  # noqa: F401
        import cv2  # noqa: F401
    except Exception as exc:
        print(f"SKIP: AI 运行时不可用（请跑在 system Python 3.14）：{exc}")
        return False
    return True


# ============================================================================
# SoakMetrics：指标收集器（对应工程方案 §9.2.2 5 类指标）
# ============================================================================

@dataclass
class SoakSnapshot:
    """单帧采样快照（用于趋势分析）。"""
    frame_index: int
    wall_ts: float  # time.time() 秒
    sim_ts: str  # 注入时钟 ISO 字符串
    active_tracks: int  # evaluator.active_count
    active_risk: int  # evaluator.active_risk_count
    behavior_states_count: int
    store_entries: int  # RecentBehaviorStore._entries 大小
    raised_total: int  # 累计 RAISED 数
    cleared_total: int  # 累计 CLEARED 数
    warnings_total: int  # 累计 Warning 数
    latency_ms: float  # 本帧 process_frame 耗时


@dataclass
class SoakMetrics:
    """soak test 全局指标收集器。

    分层（对应工程方案 §9.2.2 修订后的 4 层）：
    - Layer 1 Correctness: raised/cleared/unpaired/paired_mismatched
    - Layer 2 Resource Stability: active_tracks_peak/end, store_entries 趋势
    - Layer 3 Performance: latency_ms p50/p95/p99/max
    - Layer 4 Architecture Integrity: historical vs realtime warning
    """
    # —— Layer 1: Correctness ——
    raised_signals: List[Dict[str, Any]] = field(default_factory=list)
    cleared_signals: List[Dict[str, Any]] = field(default_factory=list)

    # —— Layer 2: Resource Stability ——
    active_tracks_peak: int = 0
    active_risk_peak: int = 0
    snapshots: List[SoakSnapshot] = field(default_factory=list)  # 每分钟采样

    # —— Layer 3: Performance ——
    latency_samples_ms: List[float] = field(default_factory=list)

    # —— Layer 4: Architecture Integrity ——
    historical_warning_count: int = 0  # 离场 VisitorEvent 触发的 Warning
    realtime_warning_count: int = 0  # RAISED 信号触发的 Warning

    # —— 元数据 ——
    frames_processed: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    loop_boundaries: List[Dict[str, Any]] = field(default_factory=list)

    def record_signal(self, sig: Any) -> None:
        """记录一个 RiskSignal（RAISED 或 CLEARED）。"""
        d = sig.to_dict() if hasattr(sig, "to_dict") else dict(sig)
        if str(d.get("transition", "")).lower() == "raised":
            self.raised_signals.append(d)
        elif str(d.get("transition", "")).lower() == "cleared":
            self.cleared_signals.append(d)

    def record_warning(self, source: str) -> None:
        """记录一个 Warning（source: 'historical' or 'realtime'）。"""
        if source == "historical":
            self.historical_warning_count += 1
        elif source == "realtime":
            self.realtime_warning_count += 1

    def update_peak(self, active_count: int, active_risk: int) -> None:
        """每帧更新峰值（Layer 2）。"""
        self.active_tracks_peak = max(self.active_tracks_peak, active_count)
        self.active_risk_peak = max(self.active_risk_peak, active_risk)

    def take_snapshot(
        self,
        frame_index: int,
        sim_ts: str,
        active_count: int,
        active_risk: int,
        behavior_states_count: int,
        store_entries: int,
        latency_ms: float,
    ) -> None:
        """每分钟采样一次（Layer 2 趋势）。"""
        snap = SoakSnapshot(
            frame_index=frame_index,
            wall_ts=time.time(),
            sim_ts=sim_ts,
            active_tracks=active_count,
            active_risk=active_risk,
            behavior_states_count=behavior_states_count,
            store_entries=store_entries,
            raised_total=len(self.raised_signals),
            cleared_total=len(self.cleared_signals),
            warnings_total=self.historical_warning_count + self.realtime_warning_count,
            latency_ms=latency_ms,
        )
        self.snapshots.append(snap)
        self.update_peak(active_count, active_risk)

    def record_latency(self, latency_ms: float) -> None:
        """记录单帧延迟（Layer 3）。"""
        self.latency_samples_ms.append(latency_ms)

    # —— 配对正确性校验（Layer 1 核心）——

    def _compute_pairing(self) -> Dict[str, int]:
        """计算 RAISED/CLEARED 配对统计。

        配对规则：CLEARED.paired_signal_id 必须等于某个 RAISED.signal_id。
        unpaired_raised = 没有 CLEARED 配对的 RAISED 数。
        paired_mismatched = paired_signal_id 找不到对应 RAISED 的 CLEARED 数。
        """
        raised_ids = {s["signal_id"] for s in self.raised_signals}
        cleared_paired_ids = [c.get("paired_signal_id") for c in self.cleared_signals]

        paired_correct = sum(1 for pid in cleared_paired_ids if pid in raised_ids)
        paired_mismatched = sum(1 for pid in cleared_paired_ids if pid is not None and pid not in raised_ids)
        # unpaired_raised：被 CLEARED 配对过的 RAISED 数
        paired_raised_ids = {pid for pid in cleared_paired_ids if pid in raised_ids}
        unpaired_raised = len(raised_ids) - len(paired_raised_ids)
        return {
            "raised_count": len(self.raised_signals),
            "cleared_count": len(self.cleared_signals),
            "unpaired_raised": unpaired_raised,
            "paired_correct": paired_correct,
            "paired_mismatched": paired_mismatched,
        }

    def _latency_stats(self) -> Dict[str, float]:
        """计算延迟分布（Layer 3）。"""
        if not self.latency_samples_ms:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
        samples = sorted(self.latency_samples_ms)
        n = len(samples)
        # statistics.quantiles 用 (n-1) 分位，需 n>=2
        if n < 2:
            return {"p50": samples[0], "p95": samples[0], "p99": samples[0], "max": samples[-1]}
        # 简化：直接索引取分位
        def _pct(p: float) -> float:
            idx = min(n - 1, max(0, int(p * n)))
            return round(samples[idx], 3)
        return {
            "p50": _pct(0.50),
            "p95": _pct(0.95),
            "p99": _pct(0.99),
            "max": round(samples[-1], 3),
        }

    def _behavior_states_trend(self) -> List[Dict[str, Any]]:
        """每分钟采样点（Layer 2 趋势）。"""
        return [
            {
                "frame_index": s.frame_index,
                "wall_ts": s.wall_ts,
                "sim_ts": s.sim_ts,
                "active_tracks": s.active_tracks,
                "active_risk": s.active_risk,
                "behavior_states_count": s.behavior_states_count,
                "store_entries": s.store_entries,
                "raised_total": s.raised_total,
                "cleared_total": s.cleared_total,
                "latency_ms": s.latency_ms,
            }
            for s in self.snapshots
        ]

    def to_report(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """生成最终 JSON 报告。"""
        return {
            "metadata": metadata,
            "risk_signal": self._compute_pairing(),
            "evaluator_state": {
                "active_tracks_peak": self.active_tracks_peak,
                "active_tracks_end": 0,  # 运行结束时由 caller 填入
                "active_risk_peak": self.active_risk_peak,
                "active_risk_end": 0,  # 运行结束时由 caller 填入
            },
            "behavior_states_trend": self._behavior_states_trend(),
            "latency_ms": self._latency_stats(),
            "warnings": {
                "historical_count": self.historical_warning_count,
                "realtime_count": self.realtime_warning_count,
            },
            "loop_boundaries": self.loop_boundaries,
        }


# ============================================================================
# 视频帧源（循环 + DemoClock）
# ============================================================================

class LoopingVideoSource:
    """循环视频帧源（OpenCV 读取，支持 loop_count 次循环）。

    不继承 FrameSource（pipeline 通过 run(frames) 接收 list，无需迭代器协议）。
    """

    def __init__(self, video_path: str, loop_count: int = 1, fps_target: int = 8):
        import cv2
        self.video_path = video_path
        self.loop_count = max(1, int(loop_count))
        self.fps_target = fps_target
        self._cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        if not self._cap.isOpened():
            raise RuntimeError(f"无法打开视频：{video_path}")
        self._total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))

    def read_all_frames(self) -> List[Any]:
        """读取所有帧（含循环），返回 list[np.ndarray]。"""
        import cv2
        frames: List[Any] = []
        for loop_idx in range(self.loop_count):
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # 回到起点
            while True:
                ret, frame = self._cap.read()
                if not ret:
                    break
                frames.append(frame)
        return frames

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


# ============================================================================
# 主流程
# ============================================================================

def _load_scenario(path: Path) -> Dict[str, Any]:
    """加载场景 YAML 配置。"""
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_pipeline_with_realtime(
    scenario_cfg: Dict[str, Any],
    device_id: str = "soak_test",
):
    """从场景配置装配带实时路径的 pipeline。

    复用 PerceptionPipeline.from_settings，但强制开启 realtime + 注入 DemoClock。
    """
    from home_perception.core.config import load_settings
    from home_perception.runtime.pipeline import DemoClock, PerceptionPipeline

    settings = load_settings()
    realtime_cfg = scenario_cfg.get("realtime", {})
    # 强制覆盖 realtime 配置
    settings.realtime_risk.enabled = bool(realtime_cfg.get("enabled", True))
    settings.realtime_risk.decision_enabled = bool(realtime_cfg.get("decision_enabled", False))
    settings.realtime_risk.eval_interval_frames = int(realtime_cfg.get("eval_interval_frames", 1))

    # 注入 DemoClock（加速时序）
    interval_s = float(scenario_cfg.get("clock", {}).get("interval_s", 5.0))
    clock = DemoClock(
        start=datetime(2026, 7, 27, 8, 0, 0, tzinfo=timezone.utc),
        interval_s=interval_s,
    )

    pipeline = PerceptionPipeline.from_settings(
        settings,
        device_id=device_id,
        now_provider=clock,
        frame_interval_s=interval_s,
    )
    return pipeline, clock, settings


def _get_store_entries(pipeline) -> int:
    """读取 RecentBehaviorStore._entries 大小（监控用，不暴露接口）。"""
    store = getattr(pipeline, "_recent_behavior_store", None)
    if store is None:
        return 0
    return len(store._entries)  # type: ignore[attr-defined]


def run_soak(
    scenario_path: Path,
    duration_s: int,
    snapshot_interval_s: int = 60,
) -> Dict[str, Any]:
    """运行一次 soak test。

    参数：
    - scenario_path: 场景 YAML 路径
    - duration_s: 墙钟预算（秒）；达到后停止
    - snapshot_interval_s: 采样间隔（秒）

    返回：完整报告 dict（可 json.dump）。
    """
    scenario_cfg = _load_scenario(scenario_path)
    scenario_name = scenario_cfg.get("scenario_name", scenario_path.stem)

    print(f"[soak] 场景: {scenario_name}")
    print(f"[soak] 描述: {scenario_cfg.get('description', '')}")
    print(f"[soak] 预算: {duration_s}s")

    # —— 装配 pipeline ——
    pipeline, clock, settings = _build_pipeline_with_realtime(scenario_cfg)
    pipeline.load_detector()

    # —— 读取视频帧 ——
    video_cfg = scenario_cfg.get("video", {})
    video_path = ROOT / video_cfg.get("source", "")
    if not video_path.is_file():
        raise FileNotFoundError(f"视频文件不存在：{video_path}")
    loop_count = int(video_cfg.get("loop_count", 1))

    print(f"[soak] 视频: {video_path}")
    print(f"[soak] 循环: {loop_count} 次")

    source = LoopingVideoSource(str(video_path), loop_count=loop_count)
    frames = source.read_all_frames()
    total_frames = len(frames)
    print(f"[soak] 总帧数: {total_frames}")

    # —— 指标收集器 ——
    metrics = SoakMetrics()
    metrics.started_at = datetime.now(timezone.utc)

    # —— 主循环 ——
    wall_start = time.time()
    last_snapshot_wall = wall_start
    frame_index = 0
    loop_boundary_frames = total_frames // loop_count  # 每个循环的帧数

    try:
        while (time.time() - wall_start) < duration_s:
            frame = frames[frame_index % total_frames]
            is_loop_boundary = (frame_index > 0 and frame_index % loop_boundary_frames == 0)

            # 循环边界：记录 + 重置评估器（防 tracker ID 复用串号污染指标）
            if is_loop_boundary:
                metrics.loop_boundaries.append({
                    "frame_index": frame_index,
                    "action": "loop_boundary",
                    "sim_ts": clock.now().isoformat(),
                })
                # 不重置 evaluator/tracker——让 soak test 暴露真实的跨循环行为
                # 如果重置，反而掩盖了"track_id 复用串号"类问题

            # 推进 DemoClock（与 pipeline.run 行为一致）
            clock.tick(clock.interval_s)

            # 计时
            t0 = time.time()
            result = pipeline.process_frame(frame, frame_index=frame_index)
            latency_ms = (time.time() - t0) * 1000.0
            metrics.record_latency(latency_ms)
            metrics.frames_processed += 1

            # 记录 RiskSignal
            for sig in result.risk_signals:
                metrics.record_signal(sig)

            # 记录 Warning（区分历史/实时）
            # 历史路径 Warning：由 VisitorEvent 触发（result.n_visitor_events > 0 时）
            # 实时路径 Warning：由 RAISED 信号触发（pipeline._decision_enabled）
            if pipeline._decision_enabled and result.warnings:
                # 简化判定：本帧有 risk_signals → 实时；否则历史
                if result.risk_signals:
                    for _ in result.warnings:
                        metrics.record_warning("realtime")
                else:
                    for _ in result.warnings:
                        metrics.record_warning("historical")
            elif result.warnings:
                for _ in result.warnings:
                    metrics.record_warning("historical")

            # 更新峰值
            ev = pipeline._realtime_evaluator
            if ev is not None:
                metrics.update_peak(ev.active_count, ev.active_risk_count)

            # 采样（每 snapshot_interval_s 秒）
            now_wall = time.time()
            if (now_wall - last_snapshot_wall) >= snapshot_interval_s:
                metrics.take_snapshot(
                    frame_index=frame_index,
                    sim_ts=clock.now().isoformat(),
                    active_count=ev.active_count if ev else 0,
                    active_risk=ev.active_risk_count if ev else 0,
                    behavior_states_count=len(result.behavior_states),
                    store_entries=_get_store_entries(pipeline),
                    latency_ms=latency_ms,
                )
                last_snapshot_wall = now_wall
                # 触发 GC 防止采样本身被延迟影响
                gc.collect()

            frame_index += 1

            # 周期性进度输出
            if frame_index % 500 == 0:
                elapsed = time.time() - wall_start
                print(
                    f"[soak] frame={frame_index} elapsed={elapsed:.1f}s "
                    f"raised={len(metrics.raised_signals)} "
                    f"cleared={len(metrics.cleared_signals)} "
                    f"active={ev.active_count if ev else 0} "
                    f"latency_p50={metrics._latency_stats()['p50']}ms"
                )

    except KeyboardInterrupt:
        print(f"\n[soak] 收到中断信号，停止处理（已处理 {frame_index} 帧）")
    finally:
        metrics.finished_at = datetime.now(timezone.utc)
        # 填入运行结束时的 evaluator 状态
        ev = pipeline._realtime_evaluator
        if ev is not None:
            # 报告中填入 end 值
            pass  # 在 to_report 后修正

    # —— 生成报告 ——
    wall_duration = time.time() - wall_start
    sim_duration = clock.now() - datetime(2026, 7, 27, 8, 0, 0, tzinfo=timezone.utc)
    metadata = {
        "scenario": scenario_name,
        "description": scenario_cfg.get("description", ""),
        "video_source": str(video_cfg.get("source", "")),
        "loop_count": loop_count,
        "duration_s": round(wall_duration, 2),
        "simulated_duration_s": sim_duration.total_seconds(),
        "acceleration_ratio": round(sim_duration.total_seconds() / max(1.0, wall_duration), 2),
        "frames_processed": metrics.frames_processed,
        "started_at": metrics.started_at.isoformat() if metrics.started_at else None,
        "finished_at": metrics.finished_at.isoformat() if metrics.finished_at else None,
        "realtime_enabled": settings.realtime_risk.enabled,
        "decision_enabled": settings.realtime_risk.decision_enabled,
        "clock_interval_s": clock.interval_s,
        "snapshot_interval_s": snapshot_interval_s,
    }
    report = metrics.to_report(metadata)

    # 修正 evaluator end 状态
    ev = pipeline._realtime_evaluator
    if ev is not None:
        report["evaluator_state"]["active_tracks_end"] = ev.active_count
        report["evaluator_state"]["active_risk_end"] = ev.active_risk_count

    # —— 清理 ——
    pipeline.close()
    source.release()

    return report


# ============================================================================
# CLI 入口
# ============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="ADR-0021 Stage E Soak Test")
    ap.add_argument(
        "--scenario",
        default="config/scenarios/soak_s2_abnormal_dwell.yaml",
        help="场景 YAML 路径（默认 S2 异常停留）",
    )
    ap.add_argument(
        "--duration",
        type=int,
        default=300,
        help="运行时长（秒，默认 300 = 5min 烟雾测试）",
    )
    ap.add_argument(
        "--snapshot-interval",
        type=int,
        default=60,
        help="采样间隔（秒，默认 60）",
    )
    ap.add_argument(
        "--output-dir",
        default="reports",
        help="报告输出目录（默认 reports/，已 gitignore）",
    )
    args = ap.parse_args()

    if not _env_gate():
        return 2

    scenario_path = (ROOT / args.scenario) if not Path(args.scenario).is_absolute() else Path(args.scenario)
    if not scenario_path.is_file():
        print(f"❌ 场景文件不存在：{scenario_path}")
        return 2

    # 输出目录
    output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[soak] 开始运行（预算 {args.duration}s）")
    report = run_soak(
        scenario_path=scenario_path,
        duration_s=args.duration,
        snapshot_interval_s=args.snapshot_interval,
    )

    # 写报告
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    scenario_name = report["metadata"]["scenario"]
    output_path = output_dir / f"soak_{ts}_{scenario_name}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n[soak] 报告已写入: {output_path}")
    print(f"[soak] 摘要:")
    print(f"  - 场景: {report['metadata']['scenario']}")
    print(f"  - 帧数: {report['metadata']['frames_processed']}")
    print(f"  - 加速比: {report['metadata']['acceleration_ratio']}x")
    print(f"  - RAISED/CLEARED: {report['risk_signal']['raised_count']}/{report['risk_signal']['cleared_count']}")
    print(f"  - unpaired_raised: {report['risk_signal']['unpaired_raised']}")
    print(f"  - paired_mismatched: {report['risk_signal']['paired_mismatched']}")
    print(f"  - active_tracks peak/end: {report['evaluator_state']['active_tracks_peak']}/{report['evaluator_state']['active_tracks_end']}")
    print(f"  - latency p50/p95/p99: {report['latency_ms']['p50']}/{report['latency_ms']['p95']}/{report['latency_ms']['p99']} ms")
    print(f"  - warnings historical/realtime: {report['warnings']['historical_count']}/{report['warnings']['realtime_count']}")

    # 不变式校验（不做硬退出，让用户看报告判断）
    rs = report["risk_signal"]
    es = report["evaluator_state"]
    issues = []
    if rs["unpaired_raised"] > 0:
        issues.append(f"❌ unpaired_raised={rs['unpaired_raised']} > 0（状态泄漏）")
    if rs["paired_mismatched"] > 0:
        issues.append(f"❌ paired_mismatched={rs['paired_mismatched']} > 0（配对错配）")
    if es["active_tracks_end"] > 0:
        issues.append(f"❌ active_tracks_end={es['active_tracks_end']} > 0（dict 泄漏）")

    if issues:
        print("\n[soak] ⚠️ 发现不变式违反：")
        for issue in issues:
            print(f"  {issue}")
        return 1

    print("\n[soak] ✅ 所有关键不变式通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
