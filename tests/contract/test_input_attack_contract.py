"""Input Attack Contract（ADR-0014 Contract Test 矩阵）— 验证系统面对现实世界异常输入
是否保持边界。

普通测试验证"输入 A → 输出 B"；攻击性测试验证"异常输入 → 系统不崩溃、不污染、不静默"。

覆盖矩阵（Owner 指定，扩展）：
- 空视频           → 返回空 summary，0 错误
- 时间倒流         → VisitorEvent 拒绝 leave < enter（不产生负 duration）
- 非法 visitor_id  → 拒绝（必须是 UUID）
- 负 duration      → 拒绝
- 脏/越界字段      → PerceptionEvent / WarningEvent / ActionCommand 在 __post_init__ 拒绝
- 状态机攻击       → 非法跳变拒绝（见 test_state_machine_contract）
- 通道失败         → Warning 保持 PENDING（不丢、不误翻 CONFIRMED）
- 重复事件幂等     → 同一 warning_id 不重复产生下游命令

这些测试现在全绿（CI 不破），并构成冻结前置清理后的回归网。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from home_perception.action.dispatcher import ActionDispatcher, DispatcherConfig
from home_perception.action.executor import ActionExecutor
from home_perception.action.notifier import MockNotifier
from home_perception.action.publisher import MockPublisher
from home_perception.action.command import ActionCommand
from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.perception import PerceptionEvent
from home_perception.analysis.warning import WarningEvent
from home_perception.runtime.pipeline import PerceptionPipeline, RunSummary


def _utc(offset_s: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_s)


# ---------------------------------------------------------------------------
# 时间异常 / 脏输入：数据层契约（下游永远不会收到坏数据）
# ---------------------------------------------------------------------------


def test_time_reversal_rejected_at_visitor_event():
    """时间倒流（leave < enter）→ VisitorEvent 构造时拒绝，不产生负 duration。"""
    with pytest.raises(ValueError):
        VisitorEvent(
            visitor_id=uuid4(),
            enter_time=_utc(10),
            leave_time=_utc(5),  # 早于 enter
            duration_seconds=-5,
        )


def test_negative_duration_rejected():
    with pytest.raises(ValueError):
        VisitorEvent(
            visitor_id=uuid4(),
            enter_time=_utc(0),
            leave_time=_utc(10),
            duration_seconds=-1.0,
        )


def test_naive_datetime_rejected():
    """naive datetime（无时区）拒绝，防御跨设备时间漂移。"""
    with pytest.raises(ValueError):
        VisitorEvent(
            visitor_id=uuid4(),
            enter_time=datetime(2026, 1, 1, 0, 0, 0),  # 无 tz
            leave_time=datetime(2026, 1, 1, 0, 0, 10),
            duration_seconds=10.0,
        )


def test_empty_visitor_id_rejected():
    with pytest.raises(ValueError):
        VisitorEvent(
            visitor_id="",  # 非 UUID
            enter_time=_utc(0),
            leave_time=_utc(10),
            duration_seconds=10.0,
        )


def test_perception_event_invalid_type_rejected():
    with pytest.raises(ValueError):
        PerceptionEvent(
            device_id="d1",
            event_type="fraud_event",  # 不在 5 类
            score=0.5,
            visitor_id=uuid4(),
            source_video="cam",
            timestamp=_utc().timestamp(),
            meta={"rule": "X"},
        )


def test_perception_event_score_out_of_range_rejected():
    with pytest.raises(ValueError):
        PerceptionEvent(
            device_id="d1",
            event_type="abnormal_dwell",
            score=1.5,  # > 1
            visitor_id=uuid4(),
            source_video="cam",
            timestamp=_utc().timestamp(),
            meta={"rule": "X"},
        )


def test_perception_event_negative_timestamp_rejected():
    with pytest.raises(ValueError):
        PerceptionEvent(
            device_id="d1",
            event_type="abnormal_dwell",
            score=0.5,
            visitor_id=uuid4(),
            source_video="cam",
            timestamp=-1.0,
            meta={"rule": "X"},
        )


def test_warning_event_illegal_status_rejected():
    with pytest.raises(ValueError):
        WarningEvent(
            elder_id="e1",
            device_id="d1",
            risk_level="LOW",
            recommended_action="MONITOR",
            trigger_events=[{"event_type": "visit_normal", "score": 0.1, "timestamp": 1.0}],
            reason_summary=["x"],
            status="NOT_A_STATUS",
        )


def test_action_command_illegal_status_rejected():
    with pytest.raises(ValueError):
        ActionCommand(
            command_type="LOG_ONLY",
            warning_id=uuid4(),
            payload={},
            status="NOT_A_STATUS",
        )


# ---------------------------------------------------------------------------
# 空源 / 通道失败 / 幂等：运行时边界
# ---------------------------------------------------------------------------


def _build_minimal_pipeline() -> PerceptionPipeline:
    """用 MagicMock 装配 PerceptionPipeline，**仅**用于验证 run([]) 的空源边界。

    ⚠️ 约束：此桩只在空帧序列（run 循环体不执行）下有效。run([]) 不会触碰
    detector/tracker/executor 的任何属性，故用 MagicMock。若未来 run() 在循环前
    引用这些依赖的属性，MagicMock 会返回一个新的 Mock（而非 AttributeError），
    需要为对应组件换成真实/契约级 fake 并显式设定返回值，否则断言可能被静默满足。
    """
    return PerceptionPipeline(
        detector=MagicMock(),
        tracker=MagicMock(),
        event_builder=MagicMock(),
        feature_extractor=MagicMock(),
        rule_engine=MagicMock(),
        decision_engine=MagicMock(),
        executor=MagicMock(),
    )


def test_empty_video_returns_empty_summary():
    """空视频（0 帧）→ 返回空 RunSummary，0 错误（不崩溃）。"""
    pipeline = _build_minimal_pipeline()
    summary: RunSummary = pipeline.run([], scenario="empty")
    assert summary.frames_processed == 0
    assert summary.n_visitor_events == 0
    assert summary.errors == 0


def test_publisher_failure_keeps_warning_pending():
    """通道失败 → Warning 保持 PENDING（不丢、不误翻 CONFIRMED）。"""
    dispatcher = ActionDispatcher(DispatcherConfig(community_endpoint="http://community.example.com"))
    pub = MockPublisher()
    pub.fail_next = True  # 下一次 publish 失败
    notifier = MockNotifier()
    executor = ActionExecutor(
        dispatcher=dispatcher, publisher=pub, notifier=notifier, max_retries=0
    )
    w = WarningEvent(
        elder_id="e1",
        device_id="d1",
        risk_level="HIGH",
        recommended_action="ESCALATE_COMMUNITY",
        trigger_events=[{"event_type": "high_risk_approach", "score": 0.9, "timestamp": 1.0}],
        reason_summary=["多风险规则同时命中"],
    )
    assert w.status == "CREATED"
    cmds = executor.execute(w)
    assert cmds, "应构造出 CREATE_COMMUNITY_TASK 命令"
    assert w.status == "PENDING", "通道失败后 Warning 必须保持 PENDING"
    assert any(c.status == "FAILED" for c in cmds)


def test_duplicate_warning_idempotent():
    """重复 warning_id → 不重复产生下游命令（高频事件防护）。"""
    dispatcher = ActionDispatcher(DispatcherConfig())
    pub = MockPublisher()
    notifier = MockNotifier()
    executor = ActionExecutor(dispatcher=dispatcher, publisher=pub, notifier=notifier)
    w = WarningEvent(
        elder_id="e1",
        device_id="d1",
        risk_level="LOW",
        recommended_action="MONITOR",
        trigger_events=[{"event_type": "visit_normal", "score": 0.1, "timestamp": 1.0}],
        reason_summary=["异常时段访问"],
    )
    first = executor.execute(w)
    second = executor.execute(w)
    assert second == first, "同一 warning_id 重复 execute 应返回已记录的命令（幂等）"
