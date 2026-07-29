"""AI Pipeline Separation · 可复用骨架（示例）。

来源：Silver Shield 7 层流水线（home_perception）的抽象提炼。
本文件**不是银龄盾代码**，而是抽出的模式骨架——新项目据此类推各层。

关键思想：
- 每层只做一件事，层间只通过结构化数据 / ABC 接口通信。
- 事实层（Fact）不含业务判断；动作层（Action）只翻译不重新判断。
- 装配经 ``Pipeline.from_settings()`` 单一入口，实现可替换。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List


# ----------------------------------------------------------------------
# 层间数据结构（冻结契约的雏形）
# ----------------------------------------------------------------------
@dataclass
class Detection:
    track_id: str
    bbox: tuple


@dataclass
class FactEvent:
    """事实层产物：结构化、无业务判断。"""
    entity_id: str
    kind: str
    duration_seconds: float


@dataclass
class RiskEvent:
    """规则层产物：风险语义，不是概率。"""
    fact_id: str
    risk_level: str          # LOW / MEDIUM / HIGH
    reason: List[str]


@dataclass
class ActionIntent:
    """动作层产物：只翻译，不重新判断。"""
    risk_id: str
    command_type: str


# ----------------------------------------------------------------------
# 各层接口（ABC）—— 实现可替换，接口冻结
# ----------------------------------------------------------------------
class Detector(ABC):
    @abstractmethod
    def detect(self, frame) -> List[Detection]: ...


class Rule(ABC):
    @abstractmethod
    def evaluate(self, facts: Iterable[FactEvent]) -> List[RiskEvent]: ...


class Dispatcher(ABC):
    @abstractmethod
    def route(self, intent: ActionIntent) -> None: ...


# ----------------------------------------------------------------------
# 流水线装配（单一入口）
# ----------------------------------------------------------------------
class Pipeline:
    def __init__(self, detector: Detector, rule: Rule, dispatcher: Dispatcher) -> None:
        self.detector = detector
        self.rule = rule
        self.dispatcher = dispatcher
        self._state: dict = {}   # 跨帧状态（追踪/窗口/决策）—— 须可重置

    @classmethod
    def from_settings(cls, settings) -> "Pipeline":
        """单一装配入口：从配置构建各层，实现可替换。"""
        return cls(
            detector=settings.build_detector(),
            rule=settings.build_rule(),
            dispatcher=settings.build_dispatcher(),
        )

    def process_frame(self, frame, frame_index: int) -> List[RiskEvent]:
        """逐帧：检测 → 事实 → 规则 → 动作意图（不在此做展示）。"""
        dets = self.detector.detect(frame)
        facts = self._to_facts(dets, frame_index)
        risks = self.rule.evaluate(facts)
        for r in risks:
            self.dispatcher.route(ActionIntent(r.fact_id, _command_for(r)))
        return risks

    def _to_facts(self, dets: List[Detection], frame_index: int) -> List[FactEvent]:
        # 事实层：只描述"看到了什么"，不含 risk_level / 业务结论
        return [FactEvent(d.track_id, "present", 0.0) for d in dets]

    def reset(self) -> None:
        """清空跨帧累积状态（循环重放 / 切换源 / Reset 时调用）。

        关键：重建状态组件但复用已加载模型，避免重载权重。
        """
        self._state = {}


def _command_for(risk: RiskEvent) -> str:
    """动作层只翻译风险语义为指令类型，不重新决策。"""
    return {"HIGH": "ESCALATE", "MEDIUM": "NOTIFY", "LOW": "MONITOR"}[risk.risk_level]


# ----------------------------------------------------------------------
# 帧源抽象（Pipeline 不感知具体来源）
# ----------------------------------------------------------------------
class FrameSource(ABC):
    @abstractmethod
    def load(self, scenario) -> None: ...
    @abstractmethod
    def __iter__(self) -> Iterator: ...
