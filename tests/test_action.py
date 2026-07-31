"""ActionLayer 测试（P0-9 · 行动层）。

> **P0-9 = 行动层。** ActionCommand + ActionDispatcher + ActionExecutor。
> 三大必验证保证：
> 1. **消费正确**：WarningEvent.recommended_action 路由到正确 ActionCommand
> 2. **幂等**：同 warning_id 重复 execute → 只产生一个下游任务
> 3. **失败保护**：publisher 失败时 Warning 保持 PENDING 不丢，重试 → max_retries → REJECTED
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from home_perception.action import (
    COMMAND_STATUSES,
    COMMAND_TYPES,
    FORBIDDEN_ACTION_FIELDS,
    ActionCommand,
    ActionDispatcher,
    ActionExecutor,
    DispatcherConfig,
    FamilyContact,
    MockNotifier,
    MockPublisher,
    assert_transition_warning,
    can_transition_warning,
)
from home_perception.analysis.warning import WarningEvent


# ============================================================================
# 时区 helper
# ============================================================================

def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def make_warning(
    risk_level: str = "MEDIUM",
    recommended_action: str = "NOTIFY_FAMILY",
    status: str = "CREATED",
    elder_id: str = "elder_001",
    device_id: str = "home_entry_01",
    warning_id: uuid.UUID = None,
    trigger_events: list = None,
    reason_summary: list = None,
) -> WarningEvent:
    if trigger_events is None:
        trigger_events = [{
            "event_id": f"{uuid.uuid4()}:abnormal_dwell",
            "event_type": "abnormal_dwell",
            "score": 0.5,
            "timestamp": 1.0,
        }]
    if reason_summary is None:
        reason_summary = ["异常停留"]
    return WarningEvent(
        elder_id=elder_id,
        device_id=device_id,
        risk_level=risk_level,
        recommended_action=recommended_action,
        trigger_events=trigger_events,
        reason_summary=reason_summary,
        warning_id=warning_id or uuid.uuid4(),
        status=status,
    )


# ============================================================================
# ActionCommand 字段校验
# ============================================================================

class TestActionCommandFieldValidation:
    def test_basic_construction(self):
        cmd = ActionCommand(
            command_type="LOG_ONLY",
            warning_id=uuid.uuid4(),
            payload={"device_id": "d1"},
        )
        assert isinstance(cmd.command_id, uuid.UUID)
        assert cmd.status == "PENDING"
        assert cmd.attempts == 0

    def test_invalid_command_type_raises(self):
        with pytest.raises(ValueError, match="command_type"):
            ActionCommand(command_type="INVALID", warning_id=uuid.uuid4(), payload={})

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            ActionCommand(
                command_type="LOG_ONLY",
                warning_id=uuid.uuid4(),
                payload={},
                status="SENT",  # 不是合法状态
            )

    def test_attempts_negative_raises(self):
        with pytest.raises(ValueError, match="attempts"):
            ActionCommand(
                command_type="LOG_ONLY",
                warning_id=uuid.uuid4(),
                payload={},
                attempts=-1,
            )

    def test_naive_datetime_raises(self):
        with pytest.raises(ValueError, match="UTC timezone-aware"):
            ActionCommand(
                command_type="LOG_ONLY",
                warning_id=uuid.uuid4(),
                payload={},
                created_at=datetime(2026, 7, 19, 12, 0, 0),  # naive
            )


# ============================================================================
# ActionCommand 黑名单（行动层不做最终判定）
# ============================================================================

class TestActionCommandBlacklist:
    @pytest.mark.parametrize("forbidden_field", sorted(FORBIDDEN_ACTION_FIELDS))
    def test_payload_rejects_forbidden_field(self, forbidden_field):
        with pytest.raises(ValueError, match="禁止的业务判定字段"):
            ActionCommand(
                command_type="LOG_ONLY",
                warning_id=uuid.uuid4(),
                payload={forbidden_field: "any"},
            )

    @pytest.mark.parametrize("forbidden_field", sorted(FORBIDDEN_ACTION_FIELDS))
    def test_meta_rejects_forbidden_field(self, forbidden_field):
        with pytest.raises(ValueError, match="禁止的业务判定字段"):
            ActionCommand(
                command_type="LOG_ONLY",
                warning_id=uuid.uuid4(),
                payload={},
                meta={forbidden_field: "any"},
            )


# ============================================================================
# 状态翻转
# ============================================================================

class TestWarningStatusTransitions:
    def test_can_transition_created_to_pending(self):
        assert can_transition_warning("CREATED", "PENDING")

    def test_can_transition_pending_to_confirmed(self):
        assert can_transition_warning("PENDING", "CONFIRMED")

    def test_can_transition_pending_to_rejected(self):
        assert can_transition_warning("PENDING", "REJECTED")

    def test_cannot_transition_created_to_confirmed(self):
        """必须先经过 PENDING。"""
        assert not can_transition_warning("CREATED", "CONFIRMED")

    def test_cannot_transition_resolved_to_anything(self):
        """RESOLVED 是终态。"""
        assert not can_transition_warning("RESOLVED", "PENDING")
        assert not can_transition_warning("RESOLVED", "REJECTED")

    def test_cannot_transition_rejected_to_anything(self):
        """REJECTED 是终态。"""
        assert not can_transition_warning("REJECTED", "PENDING")
        assert not can_transition_warning("REJECTED", "CONFIRMED")

    def test_assert_transition_raises_on_invalid(self):
        with pytest.raises(ValueError, match="不能从"):
            assert_transition_warning("RESOLVED", "PENDING")


# ============================================================================
# MockPublisher
# ============================================================================

class TestMockPublisher:
    def test_publish_success(self):
        pub = MockPublisher()
        ok = pub.publish("test/topic", {"foo": "bar"})
        assert ok is True
        assert pub.publish_count == 1
        assert pub.published[0]["topic"] == "test/topic"
        assert pub.published[0]["payload"] == {"foo": "bar"}

    def test_publish_failure_via_fail_next(self):
        pub = MockPublisher()
        pub.fail_next = True
        ok = pub.publish("test/topic", {"foo": "bar"})
        assert ok is False
        assert pub.publish_count == 0  # 未追加
        assert pub.fail_next is False  # 一次性

    def test_publish_to_file(self, tmp_path):
        f = tmp_path / "mqtt.jsonl"
        pub = MockPublisher(output_path=str(f))
        pub.publish("a/b", {"x": 1})
        pub.publish("a/c", {"y": 2})
        lines = f.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["topic"] == "a/b"
        assert json.loads(lines[1])["topic"] == "a/c"

    def test_reset_clears_memory(self):
        pub = MockPublisher()
        pub.publish("a", {})
        pub.reset()
        assert pub.publish_count == 0


# ============================================================================
# MockNotifier
# ============================================================================

class TestMockNotifier:
    def test_notify_family(self):
        n = MockNotifier()
        c = FamilyContact(elder_id="e1", name="子女A", phone="+8612345", relation="son")
        ok = n.notify_family(c, "hello")
        assert ok is True
        assert n.family_count == 1
        assert n.family_messages[0]["contact"].phone == "+8612345"
        assert n.family_messages[0]["message"] == "hello"

    def test_notify_family_fail_next(self):
        n = MockNotifier()
        n.fail_next = True
        ok = n.notify_family(FamilyContact("e", "n", "+1"), "x")
        assert ok is False
        assert n.family_count == 0

    def test_notify_community(self):
        n = MockNotifier()
        ok = n.notify_community("https://api.community/v1/tasks", {"task": "review"})
        assert ok is True
        assert n.community_count == 1
        assert n.community_messages[0]["endpoint"] == "https://api.community/v1/tasks"


# ============================================================================
# ActionDispatcher 路由
# ============================================================================

class TestActionDispatcherRouting:
    def test_monitor_routes_to_log_only(self):
        d = ActionDispatcher()
        w = make_warning(recommended_action="MONITOR")
        cmds = d.dispatch(w)
        assert len(cmds) == 1
        assert cmds[0].command_type == "LOG_ONLY"
        assert cmds[0].warning_id == w.warning_id

    def test_notify_family_routes_to_send_family_message(self):
        cfg = DispatcherConfig(
            family_contact=FamilyContact(elder_id="elder_001", name="子女A", phone="+8612345"),
        )
        d = ActionDispatcher(config=cfg)
        w = make_warning(recommended_action="NOTIFY_FAMILY")
        cmds = d.dispatch(w)
        assert len(cmds) == 1
        assert cmds[0].command_type == "SEND_FAMILY_MESSAGE"
        assert cmds[0].payload["contact"]["phone"] == "+8612345"
        assert "银龄盾告警" in cmds[0].payload["message"]

    def test_notify_family_without_contact_falls_back_to_log(self):
        """未配 family_contact → 降级为 LOG_ONLY（不丢决策）。"""
        d = ActionDispatcher()  # 无 contact
        w = make_warning(recommended_action="NOTIFY_FAMILY")
        cmds = d.dispatch(w)
        assert len(cmds) == 1
        assert cmds[0].command_type == "LOG_ONLY"

    def test_escalate_community_routes_to_create_task(self):
        cfg = DispatcherConfig(community_endpoint="https://api.community/v1/tasks")
        d = ActionDispatcher(config=cfg)
        w = make_warning(recommended_action="ESCALATE_COMMUNITY", risk_level="HIGH")
        cmds = d.dispatch(w)
        assert len(cmds) == 1
        assert cmds[0].command_type == "CREATE_COMMUNITY_TASK"
        assert cmds[0].payload["endpoint"] == "https://api.community/v1/tasks"
        assert cmds[0].payload["risk_level"] == "HIGH"
        assert cmds[0].payload["elder_id"] == "elder_001"
        assert "topic" in cmds[0].payload
        assert "silvershield/home" in cmds[0].payload["topic"]

    def test_unknown_action_returns_empty(self):
        d = ActionDispatcher()
        # 构造一个非标准 recommended_action 的 warning（绕过 enum 校验，仅用于 dispatcher 测试）
        w = make_warning(recommended_action="MONITOR")  # 用合法值进入测试
        w.recommended_action = "UNKNOWN_ACTION"  # 临时改
        cmds = d.dispatch(w)
        assert cmds == []


# ============================================================================
# ActionExecutor 基础执行
# ============================================================================

class TestActionExecutorBasic:
    def test_monitor_executes_log_only_success(self):
        d = ActionDispatcher()
        pub = MockPublisher()
        notifier = MockNotifier()
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier, max_retries=3)
        w = make_warning(recommended_action="MONITOR")
        cmds = executor.execute(w)
        assert len(cmds) == 1
        assert cmds[0].status == "DONE"
        assert w.status == "CONFIRMED"
        assert pub.publish_count == 0
        assert notifier.family_count == 0

    def test_notify_family_executes_notifier_success(self):
        cfg = DispatcherConfig(family_contact=FamilyContact("e1", "子女A", "+8612345"))
        d = ActionDispatcher(config=cfg)
        pub = MockPublisher()
        notifier = MockNotifier()
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier, max_retries=3)
        w = make_warning(recommended_action="NOTIFY_FAMILY")
        cmds = executor.execute(w)
        assert len(cmds) == 1
        assert cmds[0].status == "DONE"
        assert w.status == "CONFIRMED"
        assert notifier.family_count == 1
        assert notifier.family_messages[0]["message"]

    def test_escalate_community_executes_publisher_success(self):
        cfg = DispatcherConfig(community_endpoint="https://api.community/v1/tasks")
        d = ActionDispatcher(config=cfg)
        pub = MockPublisher()
        notifier = MockNotifier()
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier, max_retries=3)
        w = make_warning(recommended_action="ESCALATE_COMMUNITY", risk_level="HIGH")
        cmds = executor.execute(w)
        assert len(cmds) == 1
        assert cmds[0].status == "DONE"
        assert w.status == "CONFIRMED"
        assert pub.publish_count == 1
        assert pub.published[0]["payload"]["risk_level"] == "HIGH"


# ============================================================================
# ActionExecutor 幂等（Owner 三大必验证 #2）
# ============================================================================

class TestActionExecutorIdempotency:
    def test_same_warning_id_dispatched_once(self):
        """同 warning_id 重复 execute → 只产生一个下游任务。"""
        cfg = DispatcherConfig(community_endpoint="https://api.community/v1/tasks")
        d = ActionDispatcher(config=cfg)
        pub = MockPublisher()
        notifier = MockNotifier()
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier)

        w = make_warning(recommended_action="ESCALATE_COMMUNITY", warning_id=uuid.uuid4())
        first = executor.execute(w)
        second = executor.execute(w)  # 重复

        assert len(first) == 1
        assert len(second) == 1  # 返回已记录的 command（不重复 dispatch）
        assert first[0].command_id == second[0].command_id
        assert pub.publish_count == 1  # 关键断言：只 publish 1 次
        assert executor.dispatched_count == 1

    def test_different_warning_ids_dispatched_separately(self):
        cfg = DispatcherConfig(community_endpoint="https://api.community/v1/tasks")
        d = ActionDispatcher(config=cfg)
        pub = MockPublisher()
        notifier = MockNotifier()
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier)

        w1 = make_warning(recommended_action="ESCALATE_COMMUNITY")
        w2 = make_warning(recommended_action="ESCALATE_COMMUNITY")
        executor.execute(w1)
        executor.execute(w2)

        assert pub.publish_count == 2  # 不同 warning 各 publish 1 次
        assert executor.dispatched_count == 2


# ============================================================================
# ActionExecutor 失败保护（Owner 三大必验证 #3）
# ============================================================================

class TestActionExecutorFailureHandling:
    def test_publisher_failure_keeps_warning_pending(self):
        """publisher 失败时 WarningEvent.status 保持 PENDING，不丢。"""
        cfg = DispatcherConfig(community_endpoint="https://api.community/v1/tasks")
        d = ActionDispatcher(config=cfg)
        pub = MockPublisher()
        pub.fail_next = True  # 下次 publish 失败
        notifier = MockNotifier()
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier)

        w = make_warning(recommended_action="ESCALATE_COMMUNITY")
        cmds = executor.execute(w)
        assert len(cmds) == 1
        assert cmds[0].status == "FAILED"
        assert w.status == "PENDING"  # 关键：保持 PENDING
        assert "dispatch_error" in w.meta
        assert executor.dispatched_count == 1  # 已记录幂等

    def test_retry_eventually_succeeds(self):
        cfg = DispatcherConfig(community_endpoint="https://api.community/v1/tasks")
        d = ActionDispatcher(config=cfg)
        pub = MockPublisher()
        notifier = MockNotifier()
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier, max_retries=3)

        w = make_warning(recommended_action="ESCALATE_COMMUNITY")
        # 第一次失败
        pub.fail_next = True
        executor.execute(w)
        # 重试：publisher 这次成功
        pub.fail_next = False
        retried = executor.retry_pending()
        assert retried[0].status == "DONE"
        assert w.status == "CONFIRMED"

    def test_retry_exhausted_marks_rejected(self):
        """重试耗尽 → Command GIVEN_UP + Warning REJECTED。"""
        cfg = DispatcherConfig(community_endpoint="https://api.community/v1/tasks")
        d = ActionDispatcher(config=cfg)
        pub = MockPublisher()
        notifier = MockNotifier()
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier, max_retries=2)

        w = make_warning(recommended_action="ESCALATE_COMMUNITY")
        # 第一次失败（fail_next 一次性）
        pub.fail_next = True
        executor.execute(w)
        # 第 1 次重试：失败
        pub.fail_next = True
        executor.retry_pending()
        # 第 2 次重试：失败（达到 max_retries）
        pub.fail_next = True
        executor.retry_pending()

        # Command 状态 GIVEN_UP
        cmd = list(executor._command_index.values())[0]
        assert cmd.status == "GIVEN_UP"
        assert cmd.attempts == 3  # 1 + 2 retries
        # Warning 状态 REJECTED
        assert w.status == "REJECTED"

    def test_family_notifier_failure_keeps_warning_pending(self):
        cfg = DispatcherConfig(family_contact=FamilyContact("e", "n", "+1"))
        d = ActionDispatcher(config=cfg)
        pub = MockPublisher()
        notifier = MockNotifier()
        notifier.fail_next = True
        executor = ActionExecutor(dispatcher=d, publisher=pub, notifier=notifier)

        w = make_warning(recommended_action="NOTIFY_FAMILY")
        cmds = executor.execute(w)
        assert cmds[0].status == "FAILED"
        assert w.status == "PENDING"

    def test_max_retries_negative_raises(self):
        d = ActionDispatcher()
        with pytest.raises(ValueError, match="max_retries"):
            ActionExecutor(dispatcher=d, publisher=MockPublisher(), notifier=MockNotifier(), max_retries=-1)


# ============================================================================
# 警告事件无业务判定字段（黑名单）
# ============================================================================

class TestActionLayerNoBusinessJudgment:
    """行动层黑名单测试：所有 ActionCommand 字段（含 payload + meta）不含业务判定字段。"""

    def test_dispatcher_payload_no_business_judgment(self):
        cfg = DispatcherConfig(
            family_contact=FamilyContact("e", "n", "+1"),
            community_endpoint="https://api.community/v1/tasks",
        )
        d = ActionDispatcher(config=cfg)
        for action in ["MONITOR", "NOTIFY_FAMILY", "ESCALATE_COMMUNITY"]:
            w = make_warning(recommended_action=action)
            cmds = d.dispatch(w)
            for cmd in cmds:
                # payload 检查
                for forbidden in FORBIDDEN_ACTION_FIELDS:
                    assert forbidden not in cmd.payload, (
                        f"cmd.payload 含禁止字段 {forbidden!r} (action={action})"
                    )
                # meta 检查
                for forbidden in FORBIDDEN_ACTION_FIELDS:
                    assert forbidden not in cmd.meta, (
                        f"cmd.meta 含禁止字段 {forbidden!r} (action={action})"
                    )

    def test_warning_status_transitions_respect_decision_lifecycle(self):
        """警告状态机按 Owner P0-8 review：描述决策生命周期，不描述执行结果。"""
        # CREATED → PENDING（开始 dispatch）
        # PENDING → CONFIRMED（dispatch 成功）
        # PENDING → REJECTED（重试耗尽）
        # CONFIRMED → RESOLVED / REJECTED
        # RESOLVED / REJECTED 是终态
        assert can_transition_warning("CREATED", "PENDING")
        assert can_transition_warning("PENDING", "CONFIRMED")
        assert can_transition_warning("PENDING", "REJECTED")
        assert can_transition_warning("CONFIRMED", "RESOLVED")
        # 禁止的"执行结果"状态（不应作为 Warning.status）
        for fake_status in ["SENT", "DELIVERED", "READ", "FAILED"]:
            assert not can_transition_warning("PENDING", fake_status)


# ============================================================================
# CAVIAR 真实链路端到端（fixture 缺失优雅 skip）
# ============================================================================

CAVIAR_ONE_STOP_ENTER = "tests/fixtures/doorway/one_stop_enter"


def test_caviar_end_to_end_pipeline_emits_action_command():
    """CAVIAR OneStopEnter1cor: detector → ... → decision → action 全链路。"""
    pytest.importorskip("ultralytics")
    import cv2
    from pathlib import Path

    p = Path(CAVIAR_ONE_STOP_ENTER)
    if not p.is_dir() or not list(p.glob("frame_*.jpg")):
        pytest.skip("CAVIAR fixture 缺失")

    frames = []
    for f in sorted(p.glob("frame_*.jpg")):
        img = cv2.imread(str(f))
        if img is not None:
            frames.append(img)
    if not frames:
        pytest.skip("CAVIAR frames 解析失败")

    from home_perception.analysis.decision_engine import DecisionEngine
    from home_perception.analysis.event_builder import VisitorEventBuilder
    from home_perception.analysis.feature_extractor import FeatureExtractor
    from home_perception.analysis.rule_engine import RuleEngine
    from home_perception.detection.detector import YOLODetector
    from home_perception.detection.tracker import VisitorTracker

    det = YOLODetector(model="yolo11n.pt", conf_threshold=0.25)
    tracker = VisitorTracker(absence_gap_s=5.0)
    event_builder = VisitorEventBuilder(tracker, source_video="CAVIAR/OneStopEnter1cor")
    feat_ext = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(device_id="CAVIAR-Test", location="入户门")
    decision_engine = DecisionEngine(elder_id="CAVIAR-Elder")

    cfg = DispatcherConfig(community_endpoint="https://api.community/v1/tasks")
    dispatcher = ActionDispatcher(config=cfg)
    publisher = MockPublisher()
    notifier = MockNotifier()
    executor = ActionExecutor(dispatcher=dispatcher, publisher=publisher, notifier=notifier)

    all_commands = []
    for f in frames:
        r = det.detect(f)
        for ev in event_builder.update(r.detections):
            risk = feat_ext.extract(ev)
            perception_events = rule_engine.evaluate(risk)
            warning = decision_engine.evaluate(perception_events)
            if warning is not None:
                cmds = executor.execute(warning)
                all_commands.extend(cmds)

    # CAVIAR OneStopEnter1cor 端到端：单访客 1 次访问
    # 决策层可能产生 0/1 个 Warning；行动层执行 0/1 个 Command
    # 关键断言：所有 ActionCommand 不含业务判定字段
    for cmd in all_commands:
        for forbidden in FORBIDDEN_ACTION_FIELDS:
            assert forbidden not in cmd.payload
            assert forbidden not in cmd.meta
        assert cmd.command_type in COMMAND_TYPES
        assert cmd.status in COMMAND_STATUSES
