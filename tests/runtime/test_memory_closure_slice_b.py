"""Memory Integration Closure — Slice B：闭环场景测试（真实 detector 路径）。

torch-free，进 CI 合约子集。与 ``test_memory_e2e_closed_loop.py`` 的区别：本文件用
``CachedDetectionDetector`` **重放真实 YOLO+ByteTrack 预跑的检测缓存**
(``tests/fixtures/detections/stranger_visit_short.detections.json``) 驱动
``tracker→event_builder→rule→decision→memory``，证明「真实系统事件进入 Memory」——
而非 ``StubDetector`` 绕过 detection/tracking。

两级测试（见 DESIGN-memory-integration-closure.md §3.1）：
- **Contract E2E（本文件，CI）**：cached detection 驱动整链，跳过 YOLO 推理；
- **Production Demo（真机 / 人工）**：完整 ``camera→YOLO→tracker``，不进 CI。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from _closed_loop_helpers import (
    CachedDetectionDetector,
    ManualClock,
    TH_HIGH,
    _memory_config,
    build_full_pipeline,
    drive_cached,
    load_cached_detections,
)
from home_perception.analysis.realtime_risk_evaluator import RiskPhase
from home_perception.analysis.risk_signal import SignalTransition
from home_perception.memory import DefaultEpisodeBuilder, InMemoryStore
from home_perception.memory.query import MemoryQuery
from home_perception.memory.records import VisitorPresenceStatus
from home_perception.memory.store import InvariantViolationError

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "detections"
    / "stranger_visit_short.detections.json"
)


# ===========================================================================
# Slice B 场景 1：Contract E2E（真实 detector 缓存 → tracker → event → memory）
# ===========================================================================
class TestContractE2E:
    def test_cached_detection_enters_memory_and_is_traceable(self):
        """真实检测缓存驱动整链 → 事件进入 Memory，且可追溯到源事件。

        验收：
        - 生命周期中出现 RAISED 风险信号（确为真实风险链路，非 stub 直出）；
        - 离场后落 1 条 EpisodicRecord（episodes_recorded==1）；
        - EpisodicRecord.source_event_ids[0] == VisitorEvent.event_id（可溯源）；
        - 风险 HIGH / 动作 ESCALATE_COMMUNITY / 时长≈15min；
        - 跨切片收口：MemoryQuery.compose_context 能据此答「为什么报警」（Slice C 价值）。
        """
        data = load_cached_detections(FIXTURE)
        frames = data["frames"]
        step = float(data["frame_interval_s"])
        clock = ManualClock(base=datetime.fromisoformat(data["base_time"]))

        store, builder = InMemoryStore(), DefaultEpisodeBuilder()
        p = build_full_pipeline(
            CachedDetectionDetector(frames), clock, thresholds=TH_HIGH(),
            memory_store=store, episode_builder=builder, episodic_shadow=True,
        )
        results = drive_cached(p, clock, frames, step)

        # 真实风险链路：生命周期中确实产过 RAISED
        assert any(s.transition == SignalTransition.RAISED
                   for r in results for s in r.risk_signals), \
            "应经真实 risk 链路产 RAISED 信号"

        # 离场后落一条 episode
        assert p.metrics.episodes_recorded == 1, "一次访客离场应落一条 episode"
        vevents = p.event_builder._events
        assert vevents, "event_builder 应产出 VisitorEvent"
        ve = vevents[-1]
        recs = store.get_episodic_by_visitor(str(ve.visitor_id))
        assert len(recs) == 1
        rec = recs[0]

        # 可溯源：episode 的 source_event_ids 引用真实 VisitorEvent（及 warning/action）
        assert rec.source_event_ids[0] == ve.event_id, \
            "episode 应可追溯到触发它的 VisitorEvent"
        assert len(rec.source_event_ids) >= 2, \
            "source_event_ids 应覆盖 visitor + warning/action"

        # 语义验收：时长≈15min（±2min，容忍一帧边界），风险 HIGH
        assert 840 <= rec.duration_seconds <= 960, \
            f"duration 应≈15min，收到 {rec.duration_seconds}"
        assert rec.risk_level == "HIGH"
        assert rec.recommended_action == "ESCALATE_COMMUNITY"
        assert rec.reason_summary and rec.actions and rec.summary, \
            "episode 应带完整可消费字段"

        # 跨切片收口：Memory 能答「为什么报警」（Product Closure，Slice C 价值）
        ctx = MemoryQuery(store).compose_context(
            str(ve.visitor_id), rec.enter_time, rec.leave_time, as_of=rec.leave_time,
        )
        assert ctx["current_status"] in (
            VisitorPresenceStatus.IN_PROGRESS, VisitorPresenceStatus.CLEARED,
        )
        assert ctx["reason"] and ctx["evidence"] and ctx["handling"], \
            "compose_context 应产出可消费的 why/evidence/handling"


# ===========================================================================
# Slice B 场景 4：Lifecycle Closure（episode 不截断在风险解除点，聚合全窗口）
# ===========================================================================
class TestLifecycleClosure:
    def test_episode_spans_full_visit_and_aggregates_max_risk(self):
        """陌生访客→风险降→继续聊天→离开：episode 仍记完整访问（不截断在风险解除点）。

        当前 pipeline 在离场时按 enter→leave 全窗口投影：聚合 max risk + 全部 action。
        验收：1 条 episode；enter/leave 均存在且覆盖完整在场窗口；risk 保留 HIGH（max）；
        actions 非空（全部 action 聚合，不丢）。
        """
        data = load_cached_detections(FIXTURE)
        frames = data["frames"]
        step = float(data["frame_interval_s"])
        clock = ManualClock(base=datetime.fromisoformat(data["base_time"]))

        store, builder = InMemoryStore(), DefaultEpisodeBuilder()
        p = build_full_pipeline(
            CachedDetectionDetector(frames), clock, thresholds=TH_HIGH(),
            memory_store=store, episode_builder=builder, episodic_shadow=True,
        )
        drive_cached(p, clock, frames, step)

        assert p.metrics.episodes_recorded == 1
        ve = p.event_builder._events[-1]
        rec = store.get_episodic_by_visitor(str(ve.visitor_id))[0]

        # 完整访问窗口：enter/leave 均存在，时长覆盖长时段（非单帧级）
        assert rec.enter_time is not None and rec.leave_time is not None
        assert rec.duration_seconds >= 600, "应覆盖完整在场窗口（>=10min）"

        # 不截断在风险解除点：保留全窗口 max risk（HIGH）
        assert rec.risk_level == "HIGH", "episode 应保留全窗口 max risk"
        # 全部 action 聚合（不止最早一条 warning 对应的 action）
        assert rec.actions, "episode 应聚合全部 action"


# ===========================================================================
# Slice B 场景 3：异常失败隔离（Memory 故障不拖垮主风险链路）
# ===========================================================================
class TestFailureIsolation:
    def test_episode_build_failure_is_isolated(self):
        """Memory 投影抛异常 → 主风险链路照常运行，不崩溃。"""
        data = load_cached_detections(FIXTURE)
        frames = data["frames"]
        step = float(data["frame_interval_s"])
        clock = ManualClock(base=datetime.fromisoformat(data["base_time"]))

        class BoomBuilder(DefaultEpisodeBuilder):
            def project_episode(self, *a, **k):
                raise RuntimeError("memory store down")

        p = build_full_pipeline(
            CachedDetectionDetector(frames), clock, thresholds=TH_HIGH(),
            memory_store=InMemoryStore(), episode_builder=BoomBuilder(),
            episodic_shadow=True,
        )
        results = drive_cached(p, clock, frames, step)

        assert any(s.transition == SignalTransition.RAISED
                   for r in results for s in r.risk_signals), "风险信号仍应产生"
        assert sum(len(r.warnings) for r in results) > 0, "Warning 仍应产生"
        assert p.metrics.episodes_recorded == 0
        assert p.metrics.errors == 1, "仅 Memory 投影失败应计 1 次 error，不崩主链路"

    def test_memory_store_invariant_violation_is_isolated(self):
        """Memory 落库抛 I2 冲突（InvariantViolationError）→ 不计 error，主链路无碍。"""
        data = load_cached_detections(FIXTURE)
        frames = data["frames"]
        step = float(data["frame_interval_s"])
        clock = ManualClock(base=datetime.fromisoformat(data["base_time"]))

        class StrictStore(InMemoryStore):
            def upsert_episodic(self, record):
                raise InvariantViolationError("simulated conflict")

        p = build_full_pipeline(
            CachedDetectionDetector(frames), clock, thresholds=TH_HIGH(),
            memory_store=StrictStore(), episode_builder=DefaultEpisodeBuilder(),
            episodic_shadow=True,
        )
        results = drive_cached(p, clock, frames, step)

        assert sum(len(r.warnings) for r in results) > 0, "Warning 仍应产生"
        assert p.metrics.episodes_recorded == 0
        # I2 冲突是防御性告警，不计入 errors（不崩主链路）
        assert p.metrics.errors == 0


# ===========================================================================
# Slice B 场景 2：重启恢复（TD-0027 · Cold Restart，真实 detector 路径）
# ===========================================================================
class TestRestartRecovery:
    def test_restart_recovers_risk_state(self, tmp_path: Path):
        """在场（cached detection 前 5 帧）→ ACTIVE_RISK → snapshot → 重启 → recover。

        验收：
        ✅ visitor_instance_id / risk_phase / first_seen / raised_at 恢复；
        ✅ dwell 重算（snapshot 不持久化 dwell_seconds / risk_score）。
        """
        snap_path = tmp_path / "snapshot.json"
        th = TH_HIGH()
        data = load_cached_detections(FIXTURE)
        step = float(data["frame_interval_s"])
        base = datetime.fromisoformat(data["base_time"])

        # --- Phase 1：在场 5 帧（取 fixture 前 5 帧，均有人）→ ACTIVE_RISK，写快照 ---
        present_frames = data["frames"][:5]
        clock1 = ManualClock(base=base)
        p1 = build_full_pipeline(
            CachedDetectionDetector(present_frames), clock1, thresholds=th,
            memory_config=_memory_config(snap_path), episodic_shadow=False,
        )
        drive_cached(p1, clock1, present_frames, step)
        assert p1._realtime_evaluator.active_risk_count == 1
        p1.close()

        # 快照结构校验：只存 reconstructable 字段
        raw = __import__("json").loads(snap_path.read_text())
        assert len(raw["active_tracks"]) == 1
        at = raw["active_tracks"][0]
        assert at["phase"] == "active_risk"
        assert "dwell_seconds" not in at, "dwell_seconds 不持久化 → 重启后由观察重算"
        assert "risk_score" not in at, "旧 risk_score 不持久化 → 不恢复"
        orig_first_seen = datetime.fromisoformat(at["first_seen"])
        orig_raised_at = datetime.fromisoformat(at["raised_at"])

        # --- Phase 2：重启（新时钟、同快照路径；时钟仅略过 Phase1，快照仍 FRESH）---
        clock2 = ManualClock(base=base + timedelta(minutes=2, seconds=45))
        p2 = build_full_pipeline(
            CachedDetectionDetector([{"detections": []}]), clock2, thresholds=th,
            memory_config=_memory_config(snap_path), episodic_shadow=False,
        )

        # 恢复验证（recover 在 __init__ 完成）
        assert p2._realtime_evaluator.active_risk_count == 1, "重启后应恢复出 1 个 ACTIVE_RISK"
        vid = next(iter(p2._realtime_evaluator._active))
        st = p2._realtime_evaluator._active[vid]
        assert vid == at["visitor_instance_id"], "visitor_instance_id 恢复"
        assert st.phase == RiskPhase.ACTIVE_RISK, "risk_phase 恢复为 active_risk"
        assert st.first_seen == orig_first_seen, "first_seen 恢复"
        assert st.raised_at == orig_raised_at, "raised_at 恢复"
        recomputed_dwell = (clock2.now() - st.first_seen).total_seconds()
        assert recomputed_dwell > 0, "dwell 由 now - first_seen 重新计算（非冻结旧值）"
        p2.close()
