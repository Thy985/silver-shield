"""[DEPRECATED] 感知流水线编排（旧路径）。

> **P0-10.5.2 Step4 将删除本模块。** 当前活跃流水线为 `runtime/pipeline.py`
> 的 `PerceptionPipeline`（from_settings 入口 + Source→Pipeline→Consumer 三段解耦）。
> 本文件仅作为迁移参考保留，不被任何运行时/测试代码依赖。

职责（旧）：拉流 -> 抽帧 -> 检测 -> 规则分析 -> 取证 -> 上报。
各阶段以接口（Detector / Rule / EvidenceCollector / Publisher）解耦，便于替换与测试。
入口由 main 注入已初始化的 EZVIZClient 与 FrameSource（见 run）。
"""
from __future__ import annotations

from ..analysis.rule import Rule, RuleContext  # [DEPRECATED] 收敛后引用权威 Rule/RuleContext
from ..common.logging import get_logger
from ..detection.detector import Detector
from ..evidence.clip_collector import EvidenceCollector
from ..ingestion.frame_source import FrameSource
from ..output.publisher import Publisher
from .config import Settings
from ..analysis.perception import PerceptionEvent  # [DEPRECATED] 收敛后引用权威定义

log = get_logger(__name__)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        device: dict,
        detector: Detector,
        rules: list[Rule],
        collector: EvidenceCollector,
        publisher: Publisher,
    ):
        self.settings = settings
        self.device = device
        self.detector = detector
        self.rules = rules
        self.collector = collector
        self.publisher = publisher

    def run(self, source: FrameSource) -> None:
        for ts, frame in source:
            result = self.detector.detect(frame)
            detections = result.detections
            if not detections:
                continue
            ctx = RuleContext(
                device_id=self.device["id"],
                location=self.device.get("location"),
                timestamp=result.timestamp,
                detections=detections,
            )
            for rule in self.rules:
                ev: PerceptionEvent | None = rule.evaluate(ctx)
                if ev is None:
                    continue
                ev.evidence = self.collector.collect(ev, frame, source.recent_frames)
                self.publisher.publish(ev)
                log.info(
                    "event.published",
                    device_id=ev.device_id,
                    event_type=ev.event_type.value,
                    score=round(ev.score, 3),
                )
