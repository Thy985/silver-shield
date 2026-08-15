"""P0 Integration Validation —— 系统级端到端验证（冻结前验收）。

> **不是**单元测试（已有 P0-3 ~ P0-9 各模块测试 256 条全绿）。
> **而是**把"感知 → 理解 → 决策 → 行动"完整链路跑通，验证模块组合后的系统级行为。

按 Owner 6 个 Golden Scenarios + 状态机完整验证 + 故障注入 + CAVIAR 端到端
的"冻结前验收"清单落地。

链路（端到端）：
    VisitorEvent (P0-6)
        ↓
    RiskFeature (P0-7a)
        ↓
    PerceptionEvent (P0-7b)
        ↓
    WarningEvent (P0-8)
        ↓
    ActionCommand (P0-9)
        ↓
    MockPublisher / MockNotifier

测试范围：
- 6 个 Golden Scenarios（系统级场景验证）
- WarningEvent + ActionCommand 状态机完整翻转（独立 + 互不污染）
- 故障注入（Publisher 失败 / 重复执行 / 数据缺失）
- CAVIAR 三个真实场景（OneStopEnter1cor / OneLeaveShopReenter1cor / Meet_WalkTogether1）
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from home_perception.action import (
    ActionDispatcher,
    ActionExecutor,
    DispatcherConfig,
    FamilyContact,
    MockNotifier,
    MockPublisher,
)
from home_perception.analysis.decision_engine import DecisionEngine
from home_perception.analysis.decision_policy import RuleBasedDecisionPolicy
from home_perception.analysis.event import VisitorEvent
from home_perception.analysis.feature_extractor import FeatureExtractor
from home_perception.analysis.rule_engine import RuleEngine
from home_perception.analysis.warning import WarningEvent
from home_perception.common.timeutil import now_dt

# ============================================================================
# 时区 helper
# ============================================================================


def utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# ============================================================================
# Stub WhitelistProvider（Scenario 5 用）
# ============================================================================


@dataclass
class StubWhitelist:
    """白名单 stub：测试时可配置。"""

    whitelisted_ids: set[UUID] = field(default_factory=set)

    def is_whitelisted(self, visitor_id: UUID) -> bool:
        return visitor_id in self.whitelisted_ids


# ============================================================================
# Pipeline 工厂
# ============================================================================


@dataclass
class IntegrationPipeline:
    """完整 Pipeline 句柄：FeatureExtractor + RuleEngine + DecisionEngine + ActionExecutor。"""

    feature_extractor: FeatureExtractor
    rule_engine: RuleEngine
    decision_engine: DecisionEngine
    dispatcher: ActionDispatcher
    executor: ActionExecutor
    publisher: MockPublisher
    notifier: MockNotifier
    whitelist: StubWhitelist
    # 内部追踪
    perception_events: list = field(default_factory=list)
    warnings: list[WarningEvent] = field(default_factory=list)
    commands: list = field(default_factory=list)


def make_pipeline(
    elder_id: str = "elder_001",
    device_id: str = "home_entry_01",
    family_contact: FamilyContact | None = None,
    community_endpoint: str = "https://community.example/api/v1/tasks",
    enable_whitelist: bool = False,
    whitelist_ids: set[UUID] | None = None,
    max_retries: int = 3,
) -> IntegrationPipeline:
    """构造端到端 Pipeline。"""
    feat_ext = FeatureExtractor(frequency_window_s=1800.0)
    rule_engine = RuleEngine(device_id=device_id, location="入户门")
    if enable_whitelist:
        wl = StubWhitelist(whitelisted_ids=whitelist_ids or set())
        rule_engine.enable_pending_verify(wl)
    else:
        wl = StubWhitelist()

    decision_engine = DecisionEngine(
        elder_id=elder_id,
        policy=RuleBasedDecisionPolicy(),
    )
    dispatcher = ActionDispatcher(
        DispatcherConfig(
            family_contact=family_contact,
            community_endpoint=community_endpoint,
        )
    )
    pub = MockPublisher()
    notifier = MockNotifier()
    executor = ActionExecutor(
        dispatcher=dispatcher,
        publisher=pub,
        notifier=notifier,
        max_retries=max_retries,
    )
    return IntegrationPipeline(
        feature_extractor=feat_ext,
        rule_engine=rule_engine,
        decision_engine=decision_engine,
        dispatcher=dispatcher,
        executor=executor,
        publisher=pub,
        notifier=notifier,
        whitelist=wl,
    )


def make_visitor_event(
    visitor_id: UUID | None = None,
    enter_time: datetime | None = None,
    duration_seconds: float = 30.0,
    source_video: str = "test/integration",
) -> VisitorEvent:
    """构造一个用于系统级测试的最小 VisitorEvent。"""
    if enter_time is None:
        enter_time = utc(2026, 7, 19, 10, 0, 0)
    return VisitorEvent(
        visitor_id=visitor_id or uuid.uuid4(),
        enter_time=enter_time,
        leave_time=enter_time + timedelta(seconds=duration_seconds),
        duration_seconds=duration_seconds,
        source_video=source_video,
    )


def run_event_through_pipeline(pipeline: IntegrationPipeline, event: VisitorEvent) -> None:
    """把单个 VisitorEvent 跑完整个 pipeline，更新 pipeline 追踪字段。"""
    risk = pipeline.feature_extractor.extract(event)
    perception = pipeline.rule_engine.evaluate(risk)
    pipeline.perception_events.extend(perception)
    if not perception:
        return
    warning = pipeline.decision_engine.evaluate(perception)
    if warning is None:
        return
    pipeline.warnings.append(warning)
    cmds = pipeline.executor.execute(warning)
    pipeline.commands.extend(cmds)


# ============================================================================
# 1. Golden Scenarios
# ============================================================================


class TestScenario1NormalVisitor:
    """Scenario 1：正常访客（短停留、白天）。期望：no warning, no action。"""

    def test_short_daytime_visit_produces_no_warning(self):
        pipeline = make_pipeline()
        event = make_visitor_event(
            enter_time=utc(2026, 7, 19, 10, 0, 0),  # 上午 10 点
            duration_seconds=30.0,  # 30 秒
        )
        run_event_through_pipeline(pipeline, event)

        # Feature 层：duration=30s，frequency=1，hour=10（非 odd_hour）
        # Rule Engine：4 条规则都不命中（duration < 300s, frequency < 3, hour 不 odd）
        # → PerceptionEvent 为空 → WarningEvent = None → ActionCommand = 空
        assert pipeline.perception_events == [], "短停留白天不应触发任何 PerceptionEvent"
        assert pipeline.warnings == [], "不应产生 WarningEvent"
        assert pipeline.commands == [], "不应产生 ActionCommand"
        assert pipeline.publisher.publish_count == 0
        assert pipeline.notifier.family_count == 0
        assert pipeline.notifier.community_count == 0


class TestScenario2AbnormalDwell:
    """Scenario 2：异常停留（> 300 秒）。期望：abnormal_dwell → LOW → NOTIFY_FAMILY。"""

    def test_long_visit_triggers_notify_family(self):
        contact = FamilyContact(
            elder_id="elder_001", name="张子女", phone="+8613800001111", relation="子女"
        )
        pipeline = make_pipeline(family_contact=contact)
        event = make_visitor_event(
            enter_time=utc(2026, 7, 19, 14, 0, 0),  # 下午 2 点
            duration_seconds=600.0,  # 10 分钟（>= 300s 阈值）
        )
        run_event_through_pipeline(pipeline, event)

        # 期望：1 个 PerceptionEvent(abnormal_dwell) → 1 个 WarningEvent(LOW, NOTIFY_FAMILY) → 1 个 ActionCommand(SEND_FAMILY_MESSAGE)
        assert len(pipeline.perception_events) == 1
        assert pipeline.perception_events[0].event_type == "abnormal_dwell"
        assert pipeline.perception_events[0].score > 0.0

        assert len(pipeline.warnings) == 1
        w = pipeline.warnings[0]
        assert w.risk_level == "LOW"
        assert w.recommended_action == "NOTIFY_FAMILY"
        assert w.status == "CONFIRMED", f"期望 status=CONFIRMED（已 ACK），实际 {w.status}"

        assert len(pipeline.commands) == 1
        cmd = pipeline.commands[0]
        assert cmd.command_type == "SEND_FAMILY_MESSAGE"
        assert cmd.status == "DONE"
        assert cmd.attempts == 1

        # 验证 MockNotifier 收到 1 条家属消息
        assert pipeline.notifier.family_count == 1
        assert pipeline.notifier.community_count == 0


class TestScenario3RepeatVisit:
    """Scenario 3：重复访问（同 visitor_id 3 次进出场）。期望：repeat_visit → LOW → NOTIFY_FAMILY。"""

    def test_three_repeat_visits_triggers_repeat_visit(self):
        contact = FamilyContact(
            elder_id="elder_001", name="张子女", phone="+8613800001111", relation="子女"
        )
        pipeline = make_pipeline(family_contact=contact)
        vid = uuid.uuid4()

        # 3 次访问间隔 5 分钟（frequency_window=1800s=30min 内全部命中）
        # 第 1 次 10:00 → frequency=1（< 3，不触发）
        # 第 2 次 10:05 → frequency=2（< 3，不触发）
        # 第 3 次 10:10 → frequency=3（>= 3，触发 repeat_visit）
        for i in range(3):
            run_event_through_pipeline(
                pipeline,
                make_visitor_event(
                    visitor_id=vid,
                    enter_time=utc(2026, 7, 19, 10, 0 + 5 * i, 0),
                    duration_seconds=20.0,
                ),
            )

        # 第 3 次：frequency=3 ≥ 3 → repeat_visit
        repeat_events = [e for e in pipeline.perception_events if e.event_type == "repeat_visit"]
        assert len(repeat_events) == 1, f"期望 1 个 repeat_visit，实际 {len(repeat_events)}"

        # 1 个 Warning + 1 个 Action
        assert len(pipeline.warnings) == 1
        w = pipeline.warnings[0]
        assert w.risk_level == "LOW"
        assert w.recommended_action == "NOTIFY_FAMILY"
        assert w.status == "CONFIRMED"

        assert len(pipeline.commands) == 1
        assert pipeline.commands[0].command_type == "SEND_FAMILY_MESSAGE"
        assert pipeline.commands[0].status == "DONE"
        assert pipeline.notifier.family_count == 1


class TestScenario4HighRiskApproach:
    """Scenario 4：高风险组合（长停留 + 重复 + 异常时段）。期望：high_risk_approach → HIGH → ESCALATE_COMMUNITY。"""

    def test_long_repeat_oddhour_triggers_high_risk_approach(self):
        pipeline = make_pipeline()
        vid = uuid.uuid4()

        # 凌晨 1 点开始，3 次访问间隔 5 分钟（确保都在 30min 窗口内）
        # 第 1 次 01:00: frequency=1, duration=30s, hour=1 (odd) → OddHourRule 触发
        # 第 2 次 01:05: frequency=2, duration=30s, hour=1 (odd) → 同上（cooldown 抑制）
        # 第 3 次 01:10: frequency=3, duration=600s, hour=1 (odd) → 3 条基础 Rule 全触发
        #   → HighRiskApproachRule Composite 触发 → high_risk_approach (HIGH)
        enter = utc(2026, 7, 19, 1, 0, 0)
        for i in range(2):
            run_event_through_pipeline(
                pipeline,
                make_visitor_event(
                    visitor_id=vid,
                    enter_time=enter + timedelta(minutes=5 * i),
                    duration_seconds=30.0,
                ),
            )
        # 第 3 次：长停留 + odd_hour + frequency=3 → CompositeRule 命中
        run_event_through_pipeline(
            pipeline,
            make_visitor_event(
                visitor_id=vid,
                enter_time=enter + timedelta(minutes=10),
                duration_seconds=600.0,  # 10 分钟
            ),
        )

        # CompositeRule 触发 high_risk_approach（HIGH）
        high_risk = [e for e in pipeline.perception_events if e.event_type == "high_risk_approach"]
        assert len(high_risk) == 1, f"期望 1 个 high_risk_approach，实际 {len(high_risk)}"
        assert high_risk[0].score >= 0.5, (
            f"high_risk_approach score 应 >= 0.5，实际 {high_risk[0].score}"
        )

        # 决策层：HIGH → ESCALATE_COMMUNITY（场景内至少 1 个 HIGH）
        high_warnings = [w for w in pipeline.warnings if w.risk_level == "HIGH"]
        assert len(high_warnings) == 1, f"期望 1 个 HIGH warning，实际 {len(high_warnings)}"
        w = high_warnings[0]
        assert w.recommended_action == "ESCALATE_COMMUNITY"
        assert w.status == "CONFIRMED"

        # 行动层：CREATE_COMMUNITY_TASK（走 publisher）
        community_cmds = [c for c in pipeline.commands if c.command_type == "CREATE_COMMUNITY_TASK"]
        assert len(community_cmds) == 1, (
            f"期望 1 个 CREATE_COMMUNITY_TASK，实际 {len(community_cmds)}"
        )
        assert community_cmds[0].status == "DONE"
        assert pipeline.publisher.publish_count == 1
        assert "silvershield/home" in pipeline.publisher.published[0]["topic"]
        assert pipeline.notifier.community_count == 0, (
            "ESCALATE_COMMUNITY 走 publisher，不走 notifier"
        )


class TestScenario5WhitelistSuppression:
    """Scenario 5：白名单抑制（快递员停留 400s 但在白名单）。期望：no escalation。"""

    def test_whitelisted_long_visit_no_escalation(self):
        pipeline = make_pipeline(enable_whitelist=True)
        vid = uuid.uuid4()
        pipeline.whitelist.whitelisted_ids.add(vid)

        # 400s 停留（>= 300s 阈值 → abnormal_dwell 应该命中）
        # 但因为启用 PendingVerifyRule 且白名单命中 → 该 Rule 不触发
        # LongDurationRule 仍会触发（它不读白名单）→ abnormal_dwell → LOW/NOTIFY_FAMILY
        # 这是"白名单只抑制 PendingVerifyRule" 的预期行为
        # 本场景验证：即使 abnormal_dwell 触发，也不会升级到 HIGH/ESCALATE_COMMUNITY
        event = make_visitor_event(
            visitor_id=vid,
            enter_time=utc(2026, 7, 19, 14, 0, 0),
            duration_seconds=400.0,
        )
        run_event_through_pipeline(pipeline, event)

        # 期望：可以产生 abnormal_dwell（因为 LongDurationRule 不查白名单）
        # 但不应产生 HIGH 或 ESCALATE_COMMUNITY
        for w in pipeline.warnings:
            assert w.risk_level != "HIGH", "白名单场景不应升级到 HIGH"
            assert w.recommended_action != "ESCALATE_COMMUNITY", (
                "白名单场景不应升级到 ESCALATE_COMMUNITY"
            )
            assert w.recommended_action in ("NOTIFY_FAMILY", "MONITOR")

        # 关键断言：publisher（社区通道）未触发
        assert pipeline.publisher.publish_count == 0, "白名单场景不应触发社区通道"
        assert pipeline.notifier.community_count == 0


class TestScenario6Idempotency:
    """Scenario 6：重复消息（同 warning_id dispatch 两次）。期望：第 2 次 ignored，publish_count=1。"""

    def test_same_warning_dispatched_twice_only_one_action(self):
        pipeline = make_pipeline()
        # 直接构造一个 HIGH WarningEvent（避免依赖 3 次 VisitorEvent 触发 CompositeRule）
        warning = _make_warning(
            risk_level="HIGH",
            recommended_action="ESCALATE_COMMUNITY",
            status="CREATED",
        )
        assert warning.risk_level == "HIGH"
        assert warning.recommended_action == "ESCALATE_COMMUNITY"

        # 第 1 次 execute
        cmds1 = pipeline.executor.execute(warning)
        assert len(cmds1) == 1
        assert cmds1[0].command_type == "CREATE_COMMUNITY_TASK"
        assert pipeline.publisher.publish_count == 1
        assert pipeline.executor.dispatched_count == 1

        # 第 2 次 execute（模拟 MQTT ACK 丢失 / 重复执行）
        cmds2 = pipeline.executor.execute(warning)
        # 关键断言：返回已有 commands 但不重复发送
        assert len(cmds2) == 1, "幂等命中应返回已有 commands"
        assert pipeline.publisher.publish_count == 1, (
            f"第 2 次执行 publish_count 应仍为 1，实际 {pipeline.publisher.publish_count}"
        )
        assert pipeline.executor.dispatched_count == 1, "executor 内部 _dispatched set 应去重"


# ============================================================================
# 2. 状态机完整验证
# ============================================================================


class TestStateMachineWarningEvent:
    """WarningEvent 状态翻转完整路径：CREATED → PENDING → CONFIRMED → RESOLVED / REJECTED。"""

    def test_happy_path_created_to_resolved(self):
        contact = FamilyContact(elder_id="e1", name="子女", phone="+86", relation="子女")
        pipeline = make_pipeline(family_contact=contact)
        warning = pipeline.decision_engine.evaluate(
            [
                _make_perception(event_type="abnormal_dwell", score=0.5),
            ]
        )
        assert warning is not None
        assert warning.status == "CREATED", (
            f"DecisionEngine 刚产出，期望 CREATED，实际 {warning.status}"
        )

        # execute 触发 CREATED → PENDING → CONFIRMED（成功）
        pipeline.executor.execute(warning)
        assert warning.status == "CONFIRMED", f"成功执行后应 CONFIRMED，实际 {warning.status}"

    def test_failure_path_pending_to_rejected(self):
        """失败重试耗尽：CREATED → PENDING → （重试）→ REJECTED。"""
        pipeline = make_pipeline(max_retries=1)  # 最小重试次数便于测试
        # 直接构造 HIGH + ESCALATE_COMMUNITY（走 publisher 路径）
        warning = _make_warning(
            risk_level="HIGH",
            recommended_action="ESCALATE_COMMUNITY",
            status="CREATED",
        )
        assert warning.status == "CREATED"

        # 第 1 次 execute：publisher 失败 → warning 保持 PENDING
        pipeline.publisher.fail_next = True
        pipeline.executor.execute(warning)
        assert warning.status == "PENDING", f"失败后应保持 PENDING，实际 {warning.status}"
        # meta 应记录 dispatch_error
        assert "dispatch_error" in warning.meta, "失败后 meta 应记录 dispatch_error"

        # 重试 1 次：仍失败（fail_next=True）→ 达到 max_retries → GIVEN_UP → warning REJECTED
        pipeline.publisher.fail_next = True
        pipeline.executor.retry_pending()
        assert warning.status == "REJECTED", f"重试耗尽应 REJECTED，实际 {warning.status}"

    def test_transition_invalid_raises(self):
        """非法翻转：CREATED → CONFIRMED（跳过 PENDING）必须拒绝。"""
        from home_perception.action.command import assert_transition_warning

        with pytest.raises(ValueError, match="不能从"):
            assert_transition_warning("CREATED", "CONFIRMED")


class TestStateMachineActionCommand:
    """ActionCommand 状态翻转：与 WarningEvent 状态机独立。"""

    def test_action_command_independent_of_warning(self):
        """ActionCommand 状态翻转不影响 WarningEvent.status（反之亦然）。"""
        pipeline = make_pipeline()
        warning = pipeline.decision_engine.evaluate(
            [
                _make_perception(event_type="abnormal_dwell", score=0.5),
            ]
        )
        assert warning is not None

        # execute：成功路径
        cmds = pipeline.executor.execute(warning)
        assert len(cmds) == 1
        cmd = cmds[0]

        # ActionCommand 状态独立变化
        assert cmd.status == "DONE"
        assert cmd.attempts == 1

        # WarningEvent 状态独立变化
        assert warning.status == "CONFIRMED"

        # 状态机互不污染：修改 cmd.status 不应影响 warning.status
        cmd.status = "FAILED"  # 强制改（仅测试）
        assert warning.status == "CONFIRMED", "改 cmd.status 不应影响 warning.status"

    def test_action_command_failure_progression(self):
        """ActionCommand: PENDING → FAILED → RETRYING → DONE（重试成功）。"""
        pipeline = make_pipeline(max_retries=2)
        # 直接构造 HIGH + ESCALATE_COMMUNITY 走 publisher
        warning = _make_warning(
            risk_level="HIGH",
            recommended_action="ESCALATE_COMMUNITY",
            status="CREATED",
        )

        # 第 1 次：失败
        pipeline.publisher.fail_next = True
        cmds = pipeline.executor.execute(warning)
        cmd = cmds[0]
        assert cmd.status == "FAILED"
        assert cmd.attempts == 1

        # 重试：成功
        retried = pipeline.executor.retry_pending()
        assert len(retried) == 1
        assert retried[0].status == "DONE"
        assert retried[0].attempts == 2  # 第 1 次 + 第 1 次重试

        # warning 翻到 CONFIRMED（因所有 command 成功）
        assert warning.status == "CONFIRMED"


# ============================================================================
# 3. 故障注入测试
# ============================================================================


class TestFailureInjection:
    """故障注入：Consumer 失败 / 重复 / 数据缺失。"""

    def test_publisher_failure_keeps_warning_pending(self):
        """Publisher 抛异常（Mock 返 False）→ Warning 保持 PENDING 不丢。"""
        pipeline = make_pipeline(max_retries=3)
        warning = pipeline.decision_engine.evaluate(
            [
                _make_perception(event_type="abnormal_dwell", score=0.5),
            ]
        )
        assert warning is not None
        # 强制走 publisher 路径
        warning.recommended_action = "ESCALATE_COMMUNITY"
        warning.risk_level = "HIGH"

        # 注入失败
        pipeline.publisher.fail_next = True
        cmds = pipeline.executor.execute(warning)

        # ActionCommand 标 FAILED（重试机会保留）
        assert cmds[0].status == "FAILED"
        assert cmds[0].attempts == 1

        # Warning 保持 PENDING（**不丢**，等重试）
        assert warning.status == "PENDING", f"期望 PENDING 等待重试，实际 {warning.status}"
        assert "dispatch_error" in warning.meta

    def test_retry_eventually_succeeds(self):
        """重试成功路径。"""
        pipeline = make_pipeline(max_retries=3)
        warning = pipeline.decision_engine.evaluate(
            [
                _make_perception(event_type="abnormal_dwell", score=0.5),
            ]
        )
        warning.recommended_action = "ESCALATE_COMMUNITY"
        warning.risk_level = "HIGH"

        # 第 1 次：失败
        pipeline.publisher.fail_next = True
        pipeline.executor.execute(warning)
        assert warning.status == "PENDING"

        # 第 2 次重试：成功
        retried = pipeline.executor.retry_pending()
        assert len(retried) == 1
        assert retried[0].status == "DONE"
        assert retried[0].attempts == 2
        assert warning.status == "CONFIRMED"
        assert pipeline.publisher.publish_count == 1

    def test_idempotency_under_repeated_execute(self):
        """MQTT ACK 丢失模拟：重复 execute → 幂等（publish_count 不变）。"""
        pipeline = make_pipeline()
        warning = pipeline.decision_engine.evaluate(
            [
                _make_perception(event_type="abnormal_dwell", score=0.5),
            ]
        )
        warning.recommended_action = "ESCALATE_COMMUNITY"
        warning.risk_level = "HIGH"

        # 第 1 次
        pipeline.executor.execute(warning)
        assert pipeline.publisher.publish_count == 1
        assert warning.status == "CONFIRMED"

        # 重复 5 次模拟 ACK 丢失
        for i in range(5):
            pipeline.executor.execute(warning)
        assert pipeline.publisher.publish_count == 1, (
            f"重复执行后 publish_count 应仍为 1，实际 {pipeline.publisher.publish_count}"
        )
        assert warning.status == "CONFIRMED", "重复执行不应改变 warning.status"

    def test_missing_leave_time_does_not_produce_invalid_warning(self):
        """VisitorEvent 数据缺失（duration=0 / 时间异常）→ 不产生错误 Warning。"""
        pipeline = make_pipeline()
        # 边界：0 秒停留（enter == leave，duration_seconds=0）
        event = make_visitor_event(
            enter_time=utc(2026, 7, 19, 14, 0, 0),
            duration_seconds=0.0,
        )
        # 0s 停留应被 VisitorEvent 接受（duration >= 0）但不应触发 LongDurationRule
        run_event_through_pipeline(pipeline, event)
        # 期望：可能产生 repeat_visit（若 frequency 命中），但不应有 abnormal_dwell
        for ev in pipeline.perception_events:
            assert ev.event_type != "abnormal_dwell", "0s 停留不应触发 abnormal_dwell"


# ============================================================================
# 4. CAVIAR 端到端回归（从 frame 到 ActionCommand）
# ============================================================================


CAVIAR_SCENARIOS = [
    ("tests/fixtures/doorway/one_stop_enter", "CAVIAR/OneStopEnter1cor"),
    ("tests/fixtures/doorway/one_leave_reenter", "CAVIAR/OneLeaveShopReenter1cor"),
    ("tests/fixtures/doorway/meet_walk_together", "CAVIAR/Meet_WalkTogether1"),
]


@pytest.mark.parametrize("fixture_dir,scenario_name", CAVIAR_SCENARIOS)
def test_caviar_end_to_end_full_pipeline(fixture_dir, scenario_name):
    """CAVIAR 真实场景：frame → ActionCommand 全链路跑通。"""
    pytest.importorskip("ultralytics")
    from pathlib import Path

    import cv2

    from home_perception.analysis.event_builder import VisitorEventBuilder
    from home_perception.detection.detector import YOLODetector
    from home_perception.detection.tracker import VisitorTracker

    p = Path(fixture_dir)
    if not p.is_dir() or not list(p.glob("frame_*.jpg")):
        pytest.skip(f"CAVIAR fixture 缺失: {fixture_dir}")

    frames = []
    for f in sorted(p.glob("frame_*.jpg")):
        img = cv2.imread(str(f))
        if img is not None:
            frames.append(img)
    if not frames:
        pytest.skip(f"CAVIAR frames 解析失败: {fixture_dir}")

    # 端到端 Pipeline
    pipeline = make_pipeline(device_id=scenario_name)
    det = YOLODetector(
        model="yolo11n.pt",
        conf_threshold=0.25,
        classes=[0],
        imgsz=416,
        device="cpu",
        enable_track=True,
        tracker="bytetrack",
    ).load()
    tracker = VisitorTracker(absence_gap_s=5.0)
    event_builder = VisitorEventBuilder(tracker, source_video=scenario_name)

    # 跑完整链路
    for f in frames:
        r = det.detect(f)
        for event in event_builder.update(r.detections):
            run_event_through_pipeline(pipeline, event)

    # 关键断言：执行不抛异常 + 无字段污染
    # 1) WarningEvent 字段无业务判定（黑名单）
    for w in pipeline.warnings:
        d = w.to_dict()
        for forbidden in (
            "fraud_result",
            "fraud_probability",
            "is_fraud",
            "is_scammer",
            "verdict",
            "crime_probability",
            "final_decision",
            "guilt_score",
        ):
            assert forbidden not in d, f"WarningEvent 含禁止字段 {forbidden}：{scenario_name}"
            assert forbidden not in (w.meta or {}), f"WarningEvent.meta 含禁止字段 {forbidden}"

    # 2) publisher/notifier 调用次数与 **真实发送的** commands 数量一致。
    # 历史断言缺陷修复：LOG_ONLY 命令即使 DONE 也不实际发送（executor.py 对
    # LOG_ONLY 只记日志、status=DONE，不调 publisher/notifier），故只统计
    # 非 LOG_ONLY 的 DONE 命令（SEND_FAMILY_MESSAGE → notifier、
    # CREATE_COMMUNITY_TASK → publisher）——对齐真实行为，避免本机 CAVIAR
    # 产出 LOW→MONITOR→LOG_ONLY（4 个 DONE、0 次 pub/notif）时误判失败。
    pub_calls = pipeline.publisher.publish_count
    notif_calls = pipeline.notifier.family_count + pipeline.notifier.community_count
    expected_calls = sum(
        1
        for c in pipeline.commands
        if c.status == "DONE" and c.command_type != "LOG_ONLY"
    )
    assert pub_calls + notif_calls == expected_calls, (
        f"实际发送 {pub_calls + notif_calls} 次，但非 LOG_ONLY 的 DONE commands 有 "
        f"{expected_calls} 个（LOG_ONLY 不实际发送）"
    )

    # 3) 所有 DONE command 的 warning_id 都来自实际产生的 warning（无孤儿）
    produced_warning_ids = {w.warning_id for w in pipeline.warnings}
    for cmd in pipeline.commands:
        if cmd.status == "DONE":
            assert cmd.warning_id in produced_warning_ids, "DONE command 引用了未产出的 warning_id"


# ============================================================================
# Internal helpers
# ============================================================================


def _make_perception(
    event_type: str = "visit_normal", score: float = 0.5, is_odd_hour: bool = False
):
    from home_perception.analysis.perception import PerceptionEvent

    return PerceptionEvent(
        device_id="home_entry_01",
        event_type=event_type,
        score=score,
        visitor_id=uuid.uuid4(),
        source_video="test/integration",
        timestamp=now_dt().timestamp(),
        is_odd_hour=is_odd_hour,
        meta={"rule": f"TestRule_{event_type}"},
    )


def _make_warning(
    status: str = "CREATED", risk_level: str = "MEDIUM", recommended_action: str = "NOTIFY_FAMILY"
):
    from home_perception.analysis.warning import WarningEvent

    return WarningEvent(
        elder_id="elder_001",
        device_id="home_entry_01",
        risk_level=risk_level,
        recommended_action=recommended_action,
        trigger_events=[
            {
                "event_id": f"{uuid.uuid4()}:abnormal_dwell",
                "event_type": "abnormal_dwell",
                "score": 0.5,
                "timestamp": 1.0,
            }
        ],
        reason_summary=["异常停留"],
        warning_id=uuid.uuid4(),
        status=status,
        meta={"policy": "TestPolicy", "decided_at": now_dt().isoformat()},
    )
