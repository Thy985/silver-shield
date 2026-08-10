"""P2：真实 YOLO 全链路闭环 smoke（tests/runtime）。

torch / ultralytics 缺失，或 ``person.jpg`` / ``yolo11n.pt`` fixture 缺失时自动跳过，
仅 ``ci-runtime`` 真跑（fixture 由 ``fixture_manager --acquire --strict`` 获取）。

目标：证明 7 层感知链路在**真实模型**下真正闭环，而非只跑 torch-free 合成 detector：

    YOLODetector(yolo11n.pt) → VisitorTracker → VisitorEventBuilder
        → FeatureExtractor → RuleEngine → DecisionEngine → ActionExecutor

关键诚实声明（评审 #2 同口径）：
- 本测试喂的是**单张 person.jpg 的平移副本**（轻微水平位移模拟同人在摄像头下连续出现），
  不是真实视频流；分布特性不能替代真机 ``camera→YOLO`` 抖动，真机分布由 Production Demo 人工验证。
- 这里验证的是「真实模型权重 + 真实 ByteTrack + 真实下游决策/行动代码路径」接通并产出 ActionCommand，
  即「闭环在真实模型下不崩、且确实触发了行动层」，而非端到端风险判定语义（那归 P0 集成测试与 Demo）。

导入约定（与 tests/test_tracker.py 一致）：torch/ultralytics/cv2 等重依赖在测试函数内
``importorskip`` + 局部导入，避免模块级导入在 torch-free 的 ci-test 环境崩溃。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np  # numpy 是仓库测试基础依赖（test_tracker 同样顶层导入）
import pytest

from home_perception.action import (
    ActionDispatcher,
    ActionExecutor,
    DispatcherConfig,
    MockNotifier,
    MockPublisher,
)
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.rule_engine import RuleEngine, ThresholdConfig
from home_perception.detection.tracker import VisitorTracker
from home_perception.runtime import PerceptionPipeline
from home_perception.runtime.pipeline import DemoClock

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "person.jpg"
# 权重 yolo11n.pt 是 gitignored（*.pt）且不入库；沿用 tests/test_tracker.py 的惯例传
# 裸字符串 "yolo11n.pt"，由 ultralytics 在缺失时自动下载（ci-runtime 有网络）。不要用
# 仓库根的绝对路径断言其存在——CI 里该文件本就不存在（靠 ultralytics 按需拉取）。


def _load_person_frames(n_person: int = 5) -> list[np.ndarray]:
    """用 person.jpg 构造同一人的连续帧（水平平移模拟摄像头下连续出现）。"""
    import cv2

    img = cv2.imread(str(FIXTURE))
    if img is None:
        return []
    frames: list[np.ndarray] = []
    for i in range(n_person):
        dx = i * 8
        f = img.copy()
        if dx:  # 向右平移，制造"同一目标在连续帧里"的视觉连续性
            f[:, :-dx] = img[:, dx:]
            f[:, -dx:] = 0
        frames.append(f)
    return frames


def _build_real_pipeline(detector, clock: DemoClock) -> PerceptionPipeline:
    """装配真实 YOLO 检测器 + 全下游 7 层（决策/行动层接 Mock 通道）。

    realtime_enabled=False：本测试只验证「逐事件」闭环（detect→…→action），
    即 ``process_frame`` 内 ``_act_on_event`` 路径——该路径无条件经过
    DecisionEngine + ActionExecutor，是生产默认行为（``decision_enabled`` 仅门控
    额外的实时信号 Stage D 路径，不影响本路径）。以此保持闭环语义单一、可断言。
    """
    tracker = VisitorTracker(absence_gap_s=2.0, now_provider=clock)
    event_builder = VisitorEventBuilder(tracker, source_video="demo/p2", now_provider=clock)
    feat = FeatureExtractor(frequency_window_s=1800.0)
    # long_duration_seconds=1.5：person 连续出现数帧即超阈值 → abnormal_dwell 命中，
    # 从而驱动 DecisionEngine → ActionExecutor（NOTIFY_FAMILY）。
    rule_engine = RuleEngine(
        device_id="demo/p2",
        location="入户门",
        thresholds=ThresholdConfig(long_duration_seconds=1.5),
        now_provider=clock,
    )
    decision = DecisionEngine(
        elder_id="elder_001", policy=RuleBasedDecisionPolicy(), now_provider=clock
    )
    dispatcher = ActionDispatcher(DispatcherConfig())
    publisher = MockPublisher()  # 内存收集，落盘关闭（纯单测）
    notifier = MockNotifier()
    executor = ActionExecutor(
        dispatcher=dispatcher, publisher=publisher, notifier=notifier, max_retries=3
    )
    return PerceptionPipeline(
        detector=detector,
        tracker=tracker,
        event_builder=event_builder,
        feature_extractor=feat,
        rule_engine=rule_engine,
        decision_engine=decision,
        executor=executor,
        now_provider=clock,
        frame_interval_s=1.0,  # run() 每帧推进模拟时间，驱动 tracker 离场判定
        realtime_enabled=False,
        decision_enabled=False,
    )


def test_real_yolo_closed_loop_smoke():
    """真实 YOLO 跑通 7 层闭环：检出 person → abnormal_dwell → 行动层下发 ActionCommand。"""
    pytest.importorskip("ultralytics")
    pytest.importorskip("cv2")
    from home_perception.detection.detector import YOLODetector

    if not FIXTURE.exists():
        pytest.skip("person.jpg 缺失（ci-runtime 经 fixture_manager 获取）")

    clock = DemoClock(start=datetime(2026, 8, 10, 20, 0, 0, tzinfo=UTC), interval_s=1.0)
    det = YOLODetector(
        model="yolo11n.pt",  # 裸字符串：ultralytics 缺失时自动下载
        conf_threshold=0.25,
        classes=[0],
        imgsz=416,
        device="cpu",
        enable_track=True,
        tracker="bytetrack",
    )
    try:
        det.load()
    except Exception as exc:  # noqa: BLE001  # 权重下载失败（网络）属环境故障，优雅跳过而非硬报错
        pytest.skip(f"yolo11n.pt 加载失败（环境/网络）：{type(exc).__name__}: {exc}")

    person_frames = _load_person_frames(n_person=5)
    if not person_frames:
        pytest.skip("person.jpg 读取为空")
    # 4 帧空检测：触发 tracker 离场 → VisitorEventBuilder 产出离场事件（含 dwell duration）
    empty = np.zeros_like(person_frames[0])
    frames = person_frames + [empty, empty, empty, empty]

    p = _build_real_pipeline(det, clock)

    # ① 真实模型确实检出目标且跨帧 track_id 一致（证明跑的是真 YOLO+ByteTrack，非 stub）
    #    此处直接调 detector 仅作前置断言；真实闭环仍由下方 p.run() 完整走通。
    first = det.detect(person_frames[0])
    second = det.detect(person_frames[1])
    assert len(first.detections) > 0, "真实 YOLO 应在 person.jpg 上检出至少一个目标"
    assert None not in [d.track_id for d in first.detections], "开启跟踪后 track_id 不应为 None"
    ids0 = {d.track_id for d in first.detections}
    ids1 = {d.track_id for d in second.detections}
    assert ids0 & ids1, "真实链路上跨帧 track_id 未保持一致（persist=True 是否生效？）"

    # ② 经 p.run() 跑完整帧序列：run() 每帧推进 DemoClock（frame_interval_s=1.0），
    #    驱动 tracker 的离场判定 → VisitorEvent（含 dwell duration）→ 下游决策/行动。
    #    这是生产 Demo 的真实驱动方式；手动 process_frame 不会推进时钟，会卡在"永不离场"。
    summary = p.run(frames, scenario="p2-real-yolo")
    p.close()

    # ③ 真实模型确实在序列上产出检测（闭环上游有真实输入）
    assert summary.n_detections > 0, "真实 YOLO 应在 person.jpg 序列上检出至少一个目标"

    # ④ 闭环触发了决策+行动层：真实感知 → WarningEvent → ActionCommand
    #    （abnormal_dwell → LOW / NOTIFY_FAMILY → SEND_FAMILY_MESSAGE → MockNotifier）
    assert summary.n_warnings > 0, "真实感知未驱动任何决策（WarningEvent 缺失）"
    assert summary.n_commands > 0, "闭环未下发任何行动指令（decision→action 未接通）"
    # 行动层确实执行（无论走 notify 还是 publish，都证明 ActionExecutor 真的跑了）
    assert (
        summary.publish_count > 0
        or summary.notify_family > 0
        or summary.notify_community > 0
        or p.executor.dispatched_count > 0
    ), "ActionExecutor 未实际执行任何 command"
