"""运行期指标与可观测性（P0-10 · 装配联调）。

> P0-10 = 工程层问题（"怎么启动系统"），本模块只做指标采集 / 健康检查，
> **不**做任何风险判定（那是 analysis 层职责）。

`PipelineMetrics` 在流水线每帧处理后累加计数，供 Demo 结束时的汇总报告与
健康检查使用。所有字段可被 structlog 安全序列化（`snapshot()` 返回纯 dict）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from ..common.timeutil import now_ts


@dataclass
class PipelineMetrics:
    """流水线运行期指标（进程内累加计数）。

    字段分两类：
    - 处理量：frames_processed / detection_calls / detections_total / visitor_events
    - 产出量：perception_events / warnings / commands / errors
    - 分布：*_by_type / *_by_level（便于快速看哪类事件最多）
    """

    frames_processed: int = 0
    detection_calls: int = 0
    detections_total: int = 0
    visitor_events: int = 0
    perception_events: int = 0
    warnings: int = 0
    commands: int = 0
    episodes_recorded: int = 0  # ADR-0024 Slice 5 · Stage F 影子写入落库计数
    errors: int = 0
    perception_by_type: Dict[str, int] = field(default_factory=dict)
    warnings_by_level: Dict[str, int] = field(default_factory=dict)
    commands_by_type: Dict[str, int] = field(default_factory=dict)
    started_at: float = 0.0
    ended_at: float = 0.0

    def start(self) -> None:
        self.started_at = now_ts()

    def stop(self) -> None:
        self.ended_at = now_ts()

    @property
    def elapsed_s(self) -> float:
        if self.started_at == 0.0:
            return 0.0
        end = self.ended_at if self.ended_at else now_ts()
        return round(end - self.started_at, 3)

    def record_perception(self, event_type: str) -> None:
        self.perception_events += 1
        self.perception_by_type[event_type] = self.perception_by_type.get(event_type, 0) + 1

    def record_warning(self, risk_level: str) -> None:
        self.warnings += 1
        self.warnings_by_level[risk_level] = self.warnings_by_level.get(risk_level, 0) + 1

    def record_command(self, command_type: str) -> None:
        self.commands += 1
        self.commands_by_type[command_type] = self.commands_by_type.get(command_type, 0) + 1

    def record_episode(self) -> None:
        """Stage F：一次 EpisodicRecord 成功落 InMemoryStore。"""
        self.episodes_recorded += 1

    def snapshot(self) -> Dict[str, Any]:
        """structlog-safe 纯 dict 快照（无 datetime 对象 / 无 numpy 类型）。"""
        return {
            "frames_processed": self.frames_processed,
            "detection_calls": self.detection_calls,
            "detections_total": self.detections_total,
            "visitor_events": self.visitor_events,
            "perception_events": self.perception_events,
            "perception_by_type": dict(self.perception_by_type),
            "warnings": self.warnings,
            "warnings_by_level": dict(self.warnings_by_level),
            "commands": self.commands,
            "commands_by_type": dict(self.commands_by_type),
            "episodes_recorded": self.episodes_recorded,
            "errors": self.errors,
            "elapsed_s": self.elapsed_s,
        }
