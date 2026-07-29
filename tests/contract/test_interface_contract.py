"""Interface Contract（ADR-0014 Level 2）— 锁定组件接口（方法名 + 参数），实现可替换。

只测"系统承诺的接口"，不测实现。实现（YOLO / OpenCV / TensorRT / 云 API / Mock）
全部可替换，但方法名 / 入参名 / 返回意图不得随便变化。

锁定对象（Owner 指定）：Detector / Tracker / EventBuilder / FeatureExtractor /
RuleEngine / DecisionPolicy / ActionExecutor，以及各 Protocol（Publisher / Notifier /
EvidenceCollector / EvidenceStorage / NowProvider）。
"""
from __future__ import annotations

import inspect

import pytest

from home_perception.action.dispatcher import ActionDispatcher, DispatcherConfig
from home_perception.action.executor import ActionExecutor
from home_perception.action.notifier import MockNotifier, NotificationAdapter
from home_perception.action.publisher import MockPublisher, MQTTPublisher
from home_perception.analysis.decision_policy import DecisionPolicy, RuleBasedDecisionPolicy
from home_perception.analysis.event_builder import VisitorEventBuilder
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.rule_engine import RuleEngine
from home_perception.detection.detector import Detector, YOLODetector
from home_perception.detection.tracker import VisitorTracker
from home_perception.evidence.clip_collector import EvidenceCollector
from home_perception.evidence.storage import EvidenceStorage
from home_perception.ingestion.frame_source import CaviarFrameSource, FrameSource
from home_perception.runtime.pipeline import (
    NowProvider,
    PerceptionPipeline,
    TickableNowProvider,
)


def _params(cls, method: str) -> list[str]:
    sig = inspect.signature(getattr(cls, method))
    return [p for p in sig.parameters if p not in ("self", "cls")]


def _assert_method(cls, method: str, expected_params: list[str]) -> None:
    assert hasattr(cls, method), f"{cls.__name__} 缺少方法 {method}"
    got = _params(cls, method)
    assert got == expected_params, (
        f"{cls.__name__}.{method} 参数应为 {expected_params}，实为 {got}"
    )


def test_detector_interface():
    _assert_method(Detector, "detect", ["frame"])


def test_yolo_detector_implements_detector():
    """实现可替换：YOLODetector 是 Detector 的一个实现。"""
    assert issubclass(YOLODetector, Detector)


def test_tracker_interface():
    _assert_method(VisitorTracker, "update", ["detections"])


def test_event_builder_interface():
    _assert_method(VisitorEventBuilder, "update", ["detections"])


def test_feature_extractor_interface():
    _assert_method(FeatureExtractor, "extract", ["event"])


def test_rule_engine_interface():
    _assert_method(RuleEngine, "evaluate", ["risk"])


def test_decision_policy_interface():
    _assert_method(DecisionPolicy, "decide", ["perception_events", "ctx"])
    assert issubclass(RuleBasedDecisionPolicy, DecisionPolicy)


def test_action_executor_interface():
    _assert_method(ActionExecutor, "execute", ["warning"])
    _assert_method(ActionExecutor, "retry_pending", [])


def test_mqtt_publisher_protocol():
    _assert_method(MQTTPublisher, "publish", ["topic", "payload"])
    # MockPublisher 是 MQTTPublisher 的一个实现（Protocol 未标记 runtime_checkable，
    # 按鸭子类型校验：实现可替换，接口不变）
    assert hasattr(MockPublisher, "publish")


def test_notification_adapter_protocol():
    _assert_method(NotificationAdapter, "notify_family", ["contact", "message"])
    _assert_method(NotificationAdapter, "notify_community", ["endpoint", "task"])
    assert hasattr(MockNotifier, "notify_family")


def test_evidence_collector_interface():
    _assert_method(EvidenceCollector, "collect", ["event", "frame", "recent_frames"])


def test_evidence_storage_interface():
    _assert_method(EvidenceStorage, "save", ["data", "name"])


def test_now_provider_protocol_callable():
    """Runtime Assembly 时序源协议（ADR-0014 Level 3 相关）：NowProvider 可调用，
    TickableNowProvider 增加 tick(dt)。"""
    _assert_method(NowProvider, "__call__", [])
    _assert_method(TickableNowProvider, "tick", ["dt"])


def test_dispatcher_interface():
    _assert_method(ActionDispatcher, "dispatch", ["warning"])
    assert issubclass(DispatcherConfig, object)


def test_perception_pipeline_entry_from_settings():
    """Runtime Assembly 入口契约（ADR-0014 Level 3）：from_settings 必须存在且签名稳定。

    Pipeline 不感知 Source 类型（CAVIAR / RTSP / EZVIZ 都从外注入），只编排 7 层。
    """
    _assert_method(
        PerceptionPipeline,
        "from_settings",
        [
            "settings",
            "detector",
            "device_id",
            "location",
            "elder_id",
            "now_provider",
            "frame_interval_s",
        ],
    )
    _assert_method(PerceptionPipeline, "run", ["frames", "scenario"])


def test_frame_source_is_abstract_contract():
    """Freeze Gate（ADR-0014 Level 3）：FrameSource 为抽象接口，具体源实现之。

    Pipeline 仅依赖本抽象；CAVIAR / RTSP / EZVIZ 各实现同一契约（P0-12）。
    """
    assert inspect.isabstract(FrameSource), "FrameSource 必须是抽象接口（ABC）"
    assert hasattr(FrameSource, "__iter__"), "FrameSource 必须声明 __iter__ 抽象方法"
    # 具体实现存在且实现抽象接口
    assert issubclass(CaviarFrameSource, FrameSource)
    # 抽象接口不可直接实例化
    with pytest.raises(TypeError):
        FrameSource()
