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
    latency_spikes: int = 0  # 延迟抖动次数（> p99 * 3，用于相关性分析）
    spike_frame_indices: List[int] = field(default_factory=list)  # 抖动发生的帧索引

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
            "latency_analysis": {
                "spikes": self.latency_spikes,
                "spike_frame_indices": self.spike_frame_indices,
                "video_restart_count": 0,  # 由 caller 填入
                "correlation": None,  # 由 caller 填入（spikes 与 video_restart_count 是否对齐）
            },
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

    流式读取：每次循环重开 VideoCapture，**不缓存所有帧**（避免内存爆）。
    soak test 跑 10 次循环 * 720×1280×3 ≈ 26GB，必须流式。

    使用方式：
        source = LoopingVideoSource(path, loop_count=10)
        for frame in source:  # 迭代所有循环的所有帧
            process(frame)
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
        self._current_loop = 0
        self._current_frame_in_loop = 0
        self.video_restart_count = 0  # 视频重开次数（用于延迟抖动相关性分析）

    def __iter__(self):
        """迭代所有循环的所有帧（流式，不缓存）。"""
        import cv2
        for loop_idx in range(self.loop_count):
            # 每次循环重开 VideoCapture（避免 seek 开销 + 防资源泄漏）
            if loop_idx > 0:
                self._cap.release()
                self._cap = cv2.VideoCapture(self.video_path, cv2.CAP_FFMPEG)
                if not self._cap.isOpened():
                    raise RuntimeError(f"循环 {loop_idx} 重新打开视频失败：{self.video_path}")
                self.video_restart_count += 1  # 统计重开次数
            self._current_loop = loop_idx
            self._current_frame_in_loop = 0
            while True:
                ret, frame = self._cap.read()
                if not ret:
                    break
                yield frame
                self._current_frame_in_loop += 1

    @property
    def current_loop(self) -> int:
        return self._current_loop

    @property
    def current_frame_in_loop(self) -> int:
        return self._current_frame_in_loop

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


# 三种实验模式（对应工程方案 §9.2.7 历史路径零污染验证）
# - historical: realtime_enabled=false, decision_enabled=true → 只历史路径产 Warning
# - shadow:     realtime_enabled=true,  decision_enabled=false → 实时信号只观察不产 Warning
# - realtime:   realtime_enabled=true,  decision_enabled=true → 历史路径 + 实时路径都产 Warning
MODE_CONFIGS = {
    "historical": {"realtime_enabled": False, "decision_enabled": True},
    "shadow":     {"realtime_enabled": True,  "decision_enabled": False},
    "realtime":   {"realtime_enabled": True,  "decision_enabled": True},
}


def _build_pipeline_with_realtime(
    scenario_cfg: Dict[str, Any],
    mode: str = "realtime",
    device_id: str = "soak_test",
):
    """从场景配置装配带实时路径的 pipeline。

    mode 参数覆盖场景 YAML 的 realtime 配置（对应工程方案 §9.2.7 三模式对照实验）：
    - historical: realtime_enabled=false → 只历史路径产 Warning，得 historical_warning_count = X
    - shadow:     decision_enabled=false → 实时信号只观察不产 Warning，得 risk_signal_count = Y
    - realtime:   完整模式 → 历史路径 + 实时路径都产 Warning，得 warning_count = Z
    验证：Z >= X（历史路径零污染，允许 Cooldown 去重导致 Z < X + Y）。
    """
    from home_perception.core.config import Settings
    from home_perception.runtime.pipeline import DemoClock, PerceptionPipeline

    settings = Settings.load("config/default.yaml")
    # mode 覆盖场景 YAML 的 realtime 配置
    if mode not in MODE_CONFIGS:
        raise ValueError(f"未知 mode: {mode}，应为 historical/shadow/realtime")
    mode_cfg = MODE_CONFIGS[mode]
    settings.realtime_risk.enabled = mode_cfg["realtime_enabled"]
    settings.realtime_risk.decision_enabled = mode_cfg["decision_enabled"]
    realtime_cfg = scenario_cfg.get("realtime", {})
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
    mode: str = "realtime",
    snapshot_interval_s: int = 60,
    loop_count_override: Optional[int] = None,
) -> Dict[str, Any]:
    """运行一次 soak test。

    参数：
    - scenario_path: 场景 YAML 路径
    - duration_s: 墙钟预算（秒）；达到后停止
    - mode: 实验模式（historical / shadow / realtime，对应工程方案 §9.2.7）
    - snapshot_interval_s: 采样间隔（秒）
    - loop_count_override: 覆盖场景 YAML 的 loop_count（用于 R3 长跑；None 则用 YAML 值）

    返回：完整报告 dict（可 json.dump）。
    """
    scenario_cfg = _load_scenario(scenario_path)
    scenario_name = scenario_cfg.get("scenario_name", scenario_path.stem)

    print(f"[soak] 场景: {scenario_name}")
    print(f"[soak] 描述: {scenario_cfg.get('description', '')}")
    print(f"[soak] 模式: {mode}")
    print(f"[soak] 预算: {duration_s}s")

    # —— 装配 pipeline ——
    pipeline, clock, settings = _build_pipeline_with_realtime(scenario_cfg, mode=mode)
    pipeline.load_detector()

    # —— 读取视频帧（流式）——
    video_cfg = scenario_cfg.get("video", {})
    video_path = ROOT / video_cfg.get("source", "")
    if not video_path.is_file():
        raise FileNotFoundError(f"视频文件不存在：{video_path}")
    loop_count = loop_count_override if loop_count_override is not None else int(video_cfg.get("loop_count", 1))

    print(f"[soak] 视频: {video_path}")
    print(f"[soak] 循环: {loop_count} 次")

    source = LoopingVideoSource(str(video_path), loop_count=loop_count)
    # 流式：不预读 total_frames，按需迭代

    # —— 指标收集器 ——
    metrics = SoakMetrics()
    metrics.started_at = datetime.now(timezone.utc)

    # —— 主循环（流式）——
    wall_start = time.time()
    last_snapshot_wall = wall_start
    frame_index = 0
    last_loop_idx = 0

    try:
        for frame in source:
            # 检查时间预算
            if (time.time() - wall_start) >= duration_s:
                print(f"[soak] 达到时间预算 {duration_s}s，停止")
                break

            # 循环边界检测：source.current_loop 变化
            if source.current_loop > last_loop_idx:
                metrics.loop_boundaries.append({
                    "frame_index": frame_index,
                    "action": "loop_boundary",
                    "loop_idx": source.current_loop,
                    "sim_ts": clock.now().isoformat(),
                })
                last_loop_idx = source.current_loop
                # 不重置 evaluator/tracker——让 soak test 暴露真实的跨循环行为

            # 推进 DemoClock（与 pipeline.run 行为一致）
            clock.tick(clock.interval_s)

            # 计时
            t0 = time.time()
            result = pipeline.process_frame(frame, frame_index=frame_index)
            latency_ms = (time.time() - t0) * 1000.0
            metrics.record_latency(latency_ms)
            metrics.frames_processed += 1

            # 延迟抖动检测（> 当前 p99 * 3 视为 spike，用于相关性分析）
            if len(metrics.latency_samples_ms) >= 100:  # 至少 100 帧才计算 p99
                p99 = metrics._latency_stats()["p99"]
                if p99 > 0 and latency_ms > p99 * 3:
                    metrics.latency_spikes += 1
                    metrics.spike_frame_indices.append(frame_index)

            # 记录 RiskSignal
            for sig in result.risk_signals:
                metrics.record_signal(sig)

            # 记录 Warning（按模式语义，对应工程方案 §9.2.7 三模式对照实验）
            # - historical 模式（realtime_enabled=false）：所有 Warning 来自历史路径 → X
            # - shadow 模式（decision_enabled=false）：不产生 Warning，只观察 risk_signals → Y
            # - realtime 模式（realtime_enabled=true, decision_enabled=true）：所有 Warning → Z
            #   （historical 基线 X 由 historical 模式单独运行获取，对照验证 Z >= X；
            #    不要求 Z = X + Y，因 Cooldown 去重 / 决策合并）
            if result.warnings:
                if not settings.realtime_risk.enabled:
                    # historical 模式：realtime 关闭，所有 Warning 来自历史路径
                    for _ in result.warnings:
                        metrics.record_warning("historical")
                elif settings.realtime_risk.decision_enabled:
                    # realtime 模式：完整模式，所有 Warning 记为 realtime（Z）
                    for _ in result.warnings:
                        metrics.record_warning("realtime")
                # shadow 模式：decision_enabled=false，pipeline 不产 Warning，无需记录

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
        "mode": mode,  # 实验模式（historical / shadow / realtime）
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

    # 填入延迟抖动相关性分析（latency_spikes vs video_restart_count）
    report["latency_analysis"]["video_restart_count"] = source.video_restart_count
    # 相关性判定：spike 帧索引是否对齐 loop_boundaries（允许 ±5 帧容差）
    boundary_frames = {lb["frame_index"] for lb in metrics.loop_boundaries}
    correlated_spikes = sum(
        1 for idx in metrics.spike_frame_indices
        if any(abs(idx - bf) <= 5 for bf in boundary_frames)
    )
    report["latency_analysis"]["correlation"] = {
        "spikes_at_loop_boundaries": correlated_spikes,
        "total_spikes": metrics.latency_spikes,
        "interpretation": (
            "all_at_boundaries" if correlated_spikes == metrics.latency_spikes and metrics.latency_spikes > 0
            else "partial_at_boundaries" if correlated_spikes > 0
            else "no_spikes" if metrics.latency_spikes == 0
            else "non_boundary_spikes"
        ),
    }

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
        "--mode",
        choices=["historical", "shadow", "realtime"],
        default="realtime",
        help="实验模式（默认 realtime）：historical=只历史路径；shadow=实时信号不接决策；realtime=完整",
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
        "--loop-count",
        type=int,
        default=None,
        help="覆盖场景 YAML 的 loop_count（用于 R3 长跑；默认 None = 用 YAML 值）",
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

    print(f"[soak] 开始运行（模式 {args.mode}，预算 {args.duration}s）")
    report = run_soak(
        scenario_path=scenario_path,
        duration_s=args.duration,
        mode=args.mode,
        snapshot_interval_s=args.snapshot_interval,
        loop_count_override=args.loop_count,
    )

    # 写报告（文件名包含 mode，便于对照实验归档）
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    scenario_name = report["metadata"]["scenario"]
    mode = report["metadata"]["mode"]
    output_path = output_dir / f"soak_{ts}_{scenario_name}_{mode}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n[soak] 报告已写入: {output_path}")
    print(f"[soak] 摘要 (mode={mode}):")
    print(f"  - 场景: {report['metadata']['scenario']}")
    print(f"  - 帧数: {report['metadata']['frames_processed']}")
    print(f"  - 加速比: {report['metadata']['acceleration_ratio']}x")
    print(f"  - RAISED/CLEARED: {report['risk_signal']['raised_count']}/{report['risk_signal']['cleared_count']}")
    print(f"  - unpaired_raised: {report['risk_signal']['unpaired_raised']}")
    print(f"  - paired_mismatched: {report['risk_signal']['paired_mismatched']}")
    print(f"  - active_tracks peak/end: {report['evaluator_state']['active_tracks_peak']}/{report['evaluator_state']['active_tracks_end']}")
    print(f"  - latency p50/p95/p99/max: {report['latency_ms']['p50']}/{report['latency_ms']['p95']}/{report['latency_ms']['p99']}/{report['latency_ms']['max']} ms")
    la = report["latency_analysis"]
    print(f"  - latency spikes: {la['spikes']} (video_restarts={la['video_restart_count']}, correlation={la['correlation']['interpretation']})")
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
