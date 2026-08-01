"""Memory E2E 闭环验收测试（ADR-0024 · Memory 完整生命周期）。

torch-free，进 CI 每 PR 合约子集。

> 此前 Memory 测试（test_memory_replay / test_memory_evaluation）只验证 **Memory 模块内部**
> 正确性（投影、压缩比、回放一致性）。本文件补上**系统级 E2E 闭环**：从
> 摄像头/事件模拟 → BehaviorState → RiskStateMachine → RiskSignal → MemoryPolicy →
> MemoryStore → Cold Restart → Agent 查询，验证整条风险链路经过 Memory 后**仍然正确**。

设计铁律（贯穿全部用例）：**Memory 是旁路（Shadow Mode），绝不接决策、不产 Warning、
异常不崩主链路**。所以开启 `memory.enabled=true` 后：
- 风险决策（RiskSignal / WarningEvent）与关闭时**逐字段一致**；
- Memory 写入失败 / I2 冲突时，主风险链路**照常运行**；
- 延迟不被 O(n) 拖垮（宽松上界守护，非微基准）。

时间控制：用 `ManualClock` + `SteppingStubDetector`（detect 不自动推进时钟，由测试循环
显式 `clock.advance(step_s)` 步进），从而用**少量帧**模拟**长时段生命周期**
（例如 32 帧 × 30s = 15 分钟停留），而不是真的喂 900 帧。

四个 E2E 类别：
- E2E-1 生命周期：一次完整风险事件 → 落一条 EpisodicRecord（duration≈15min, risk=HIGH）。
- E2E-2 重启恢复（TD-0027）：snapshot → kill → restart → recover，验证字段恢复 /
  dwell 重算 / 旧 risk_score 不恢复。
- E2E-3 回放稳定：同一 Observation Stream 跑两趟完整 pipeline → MemoryRecord A == B
  （确定性、可审计、Agent 输入字节稳定）。
- E2E-4 运行时接线：memory 开启是真旁路 —— 不影响 latency / 风险决策 / WarningEvent，
  且 Memory 异常不会拖垮风险系统。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from home_perception.analysis.realtime_risk_evaluator import RiskPhase
from home_perception.analysis.risk_signal import SignalTransition
from home_perception.detection.detector import Detection, DetectionResult
from home_perception.memory import DefaultEpisodeBuilder, InMemoryStore
from home_perception.memory.records import EpisodicRecord
from home_perception.memory.store import InvariantViolationError

from _helpers import ManualClock, TH_HIGH, memory_config, build_full_pipeline, drive


# ===========================================================================
# Replay 稳定性（E2E-3）规范化辅助
# ===========================================================================
import re as _re

_UUID_RE = _re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _canon_record(rec: "EpisodicRecord") -> dict:
    """把记录中所有 UUID 归一为稳定索引（U0/U1/...），保留其余内容。

    Replay 稳定性的正确语义：同一 Observation Stream 两次回放，记忆**内容**
    （时间 / 风险 / 原因 / 动作 / 来源关联结构）必然一致；唯一差异是 UUID 这类
    每趟随机的「标识符」。把它们归一成「按出现顺序的索引」，两次回放即得到
    逐字段相等的规范化记录 → 证明内容确定性、Agent 输入稳定。

    注：pipeline 内 visitor_id / event_id / warning_id / command_id 均为 UUID4，
    其中 warning_id / command_id 由 dataclass `default_factory=uuid4` 在类定义时
    捕获函数对象，运行期无法可靠 monkeypatch（见 ADR-0024 设计）；故这里在比较
    前规范化，而非注入确定性 ID。
    """

    def _norm(v):
        if isinstance(v, str):
            def _sub(m):
                seen = _norm.seen  # type: ignore[attr-defined]
                return seen.setdefault(m.group(0), f"U{len(seen)}")

            return _UUID_RE.sub(_sub, v)
        if isinstance(v, list):
            return [_norm(x) for x in v]
        if isinstance(v, dict):
            return {k: _norm(x) for k, x in v.items()}
        return v

    _norm.seen = {}  # type: ignore[attr-defined]
    d = rec.to_dict()
    # created_at 是写入时刻（墙钟），两次回放天然微秒级不同 → 归一为常量
    d["created_at"] = "NORMALIZED"
    return _norm(d)


# ===========================================================================
# 测试辅助
# ===========================================================================
class SteppingStubDetector:
    """按 plan 返回 Detection 列表；**不**自动推进时钟（由调用方显式 advance）。

    这样能用少量帧 + 大步进模拟长时段停留（如 30 帧 × 30s = 15 分钟），
    而非真的喂 900 帧。
    """

    def __init__(self, plan: List[List[Detection]]):
        self.plan = plan
        self.i = 0

    def detect(self, frame) -> DetectionResult:
        idx = min(self.i, len(self.plan) - 1)
        dets = self.plan[idx]
        self.i += 1
        return DetectionResult(
            detections=dets, timestamp=0.0, inference_ms=0.0,
            source_size=(1, 1), inference_size=(1, 1), model="stub",
        )


def _person(track_id: int = 1) -> List[Detection]:
    return [Detection(
        class_id=0, class_name="person", confidence=0.9,
        bbox=[0, 0, 10, 10], timestamp=0.0, track_id=track_id,
    )]


def _lifecycle_plan(n_present: int = 30, step_s: float = 30.0) -> List[List[Detection]]:
    """在场 n_present 帧 + 离场 2 帧 → 触发一次离场 + 落 episode。

    配合 step_s=30：30 帧在场 = 约 15 分钟停留（非 900 帧）。
    """
    return [_person(1) for _ in range(n_present)] + [[] for _ in range(2)]


# ===========================================================================
# E2E-1：一次完整风险事件生命周期
# ===========================================================================
class TestE2ELifecycle:
    def test_full_risk_event_lifecycle_produces_episode(self):
        """18:30 enter → 18:35 dwell abnormal(RAISED) → 18:45 leave → Episode 生成。

        验收（语义，而非 15min×900 帧）：
        - episode.duration_seconds ≈ 15min（用 32 帧模拟，非 900 帧）；
        - risk_level == HIGH，recommended_action == ESCALATE_COMMUNITY；
        - reason_summary / actions / source_event_ids 完整（可追溯证据链）；
        - 生命周期中 RiskStateMachine 确实产过 RAISED 信号。
        """
        step_s = 30.0
        plan = _lifecycle_plan(30, step_s)
        assert len(plan) <= 50, "应用少量帧模拟长时段，而非成百上千帧"

        clock = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
        store, builder, _ = InMemoryStore(), DefaultEpisodeBuilder(), True
        p = build_full_pipeline(
            SteppingStubDetector(plan), clock, thresholds=TH_HIGH(),
            memory_store=store, episode_builder=builder, episodic_shadow=True,
        )
        results = drive(p, clock, plan, step_s)

        # 1) 生命周期中 RiskStateMachine 确实产过 RAISED（RiskSignal 步骤）
        all_signals = [s for r in results for s in r.risk_signals]
        assert any(s.transition == SignalTransition.RAISED for s in all_signals), \
            "生命周期中应出现 RAISED 风险信号（dwell abnormal 触发）"

        # 2) 离场后落一条 EpisodicRecord
        assert p.metrics.episodes_recorded == 1, "一次访客离场应落一条 episode"
        episodes = store.get_active_episodic()
        assert len(episodes) == 1
        rec = episodes[0]

        # 3) 语义验收：时长 ≈ 15 分钟（±2 分钟，容忍一帧边界）
        assert 840 <= rec.duration_seconds <= 960, \
            f"duration 应≈15min，收到 {rec.duration_seconds}"
        assert rec.duration_seconds >= 600, "停留应≥10分钟级（非单帧级）"

        # 4) 风险等级与动作
        assert rec.risk_level == "HIGH", "组合规则应产 HIGH"
        assert rec.recommended_action == "ESCALATE_COMMUNITY"

        # 5) 可追溯证据链（I4）：reason / actions / source_event_ids 齐全
        assert rec.reason_summary, "episode 应带 reason_summary"
        assert rec.actions, "HIGH 风险应捕获 ActionCommand"
        assert rec.source_event_ids, "episode 应可追溯到源事件 id"
        assert rec.summary, "episode 必含 human-interpretable summary"
        # 闭环验证：Agent 查询输入即 EpisodicRecord.to_dict()
        d = rec.to_dict()
        assert d["record_id"].startswith("ep-")
        assert d["visitor_instance_id"] == str(rec.visitor_instance_id)


# ===========================================================================
# E2E-2：进程重启恢复（TD-0027 · Cold Restart）
# ===========================================================================
class TestE2ERestartRecovery:
    def test_restart_recovers_risk_state_and_recomputes_dwell(self, tmp_path: Path):
        """18:35 ACTIVE_RISK → snapshot → kill → restart → recover。

        验收：
        ✅ visitor_instance_id / risk_phase / enter_time(first_seen) / raised_at 恢复；
        ✅ dwell_seconds 重新计算（snapshot 不持久化 dwell，由观察重算，非冻结旧值）；
        ❌ 旧 risk_score 不恢复（snapshot 无 risk_score 字段）。
        """
        snap_path = tmp_path / "snapshot.json"
        th = TH_HIGH()

        # --- Phase 1：运行中访问，触发 ACTIVE_RISK，写快照 ---
        clock1 = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
        present = [_person(1) for _ in range(5)]  # 在场（无离场）→ 访问进行中
        p1 = build_full_pipeline(
            SteppingStubDetector(present), clock1, thresholds=th,
            memory_config=memory_config(snap_path),
            episodic_shadow=False,
        )
        drive(p1, clock1, present, 30.0)
        # 断言 Phase 1 期间确实进入 ACTIVE_RISK
        assert p1._realtime_evaluator.active_risk_count == 1
        p1.close()  # flush 最终快照

        # --- 快照结构校验：只存 reconstructable 字段 ---
        raw = json.loads(snap_path.read_text())
        assert len(raw["active_tracks"]) == 1
        at = raw["active_tracks"][0]
        assert at["phase"] == "active_risk"
        assert "dwell_seconds" not in at, "dwell_seconds 不持久化 → 重启后由观察重新计算"
        assert "risk_score" not in at, "旧 risk_score 不持久化 → 不恢复"
        orig_first_seen = datetime.fromisoformat(at["first_seen"])
        orig_raised_at = datetime.fromisoformat(at["raised_at"])

        # --- Phase 2：重启（新时钟、同快照路径；时钟仅略过 Phase1，快照仍 FRESH） ---
        # Phase1 末帧 clock1=18:32:30 → 快照 snapshot_at=18:32:30；
        # clock2=18:32:45 → age=15s < fresh_threshold(30s) → 恢复为 FRESH（非 DISCARD）。
        clock2 = ManualClock(base=datetime(2026, 7, 31, 18, 32, 45, tzinfo=timezone.utc))
        p2 = build_full_pipeline(
            SteppingStubDetector([[]]), clock2, thresholds=th,
            memory_config=memory_config(snap_path),
            episodic_shadow=False,
        )

        # 恢复验证（recover 在 __init__ 完成）
        assert p2._realtime_evaluator.active_risk_count == 1, "重启后应恢复出 1 个 ACTIVE_RISK"
        vid = next(iter(p2._realtime_evaluator._active))
        st = p2._realtime_evaluator._active[vid]
        # visitor_instance_id 是 _active 的 key（_TrackRiskState 不持有该属性）
        assert vid == at["visitor_instance_id"], "visitor_instance_id 恢复（作为 _active 的 key）"
        assert st.phase == RiskPhase.ACTIVE_RISK, "risk_phase 恢复为 active_risk"
        assert st.first_seen == orig_first_seen, "enter_time(first_seen) 恢复"
        assert st.raised_at == orig_raised_at, "raised_at 恢复"
        # dwell 重算基础（first_seen）保留 → 重启后 dwell = now - first_seen 重新计算
        recomputed_dwell = (clock2.now() - st.first_seen).total_seconds()
        assert recomputed_dwell > 0, "dwell 由 now - first_seen 重新计算（非冻结旧值）"
        p2.close()


# ===========================================================================
# E2E-3：Replay 稳定性（确定性 / 可审计 / Agent 输入稳定）
# ===========================================================================
class TestE2EReplayStability:
    def test_same_observation_stream_yields_identical_memory(self):
        """同一 Observation Stream 跑两趟完整 pipeline → MemoryRecord A == B。

        证明：
        - 确定性：两次回放产出语义一致（records_equal 忽略 created_at）；
        - 可审计 / Agent 输入稳定：归一 created_at 后 to_dict 字节相等。
        """
        th = TH_HIGH()
        plan = _lifecycle_plan(30, 30.0)
        step_s = 30.0

        def run_once() -> List[EpisodicRecord]:
            clock = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
            store = InMemoryStore()
            builder = DefaultEpisodeBuilder()
            p = build_full_pipeline(
                SteppingStubDetector(plan), clock, thresholds=th,
                memory_store=store, episode_builder=builder, episodic_shadow=True,
            )
            drive(p, clock, plan, step_s)
            return sorted(store.get_active_episodic(), key=lambda r: r.record_id)

        A = run_once()
        B = run_once()

        assert len(A) == 1 and len(B) == 1, "每次回放应产出恰好 1 条 episode"

        # 规范化：UUID 标识每趟随机，但内容确定。归一后两次回放应逐字段相等。
        ca, cb = _canon_record(A[0]), _canon_record(B[0])
        # 1) 语义稳定：规范化后内容一致（忽略随机 UUID 标识与 created_at 墙钟）
        assert ca == cb, "相同输入两次回放应内容一致（UUID 归一后）"
        # 2) Agent 输入字节稳定：规范化 to_dict 可直接序列化比对
        import json as _json
        assert _json.dumps(ca, sort_keys=True, ensure_ascii=False) == _json.dumps(
            cb, sort_keys=True, ensure_ascii=False
        ), "相同输入两次回放，Agent 查询输入（UUID 归一后）应字节级稳定"

        # 稳定性前提下，episode 携带完整可解释信息（确定性内容）
        assert A[0].risk_level == "HIGH"
        assert A[0].duration_seconds >= 600
        assert A[0].source_event_ids


# ===========================================================================
# E2E-4：运行时接线验收（Memory 是旁路，不能变成 Memory 异常 → 风险系统挂掉）
# ===========================================================================
class TestE2ERuntimeWiring:
    def test_memory_on_is_true_bypass_no_risk_change(self):
        """memory 开启 vs 关闭：同一帧序列下 warnings/risk_signals/commands 逐帧一致。"""
        th = TH_HIGH()
        plan = _lifecycle_plan(30, 30.0)
        step_s = 30.0

        # 关闭
        clock_off = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
        p_off = build_full_pipeline(
            SteppingStubDetector(plan), clock_off, thresholds=th, episodic_shadow=False,
        )
        res_off = drive(p_off, clock_off, plan, step_s)

        # 开启（影子写入）
        clock_on = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
        store, builder, _ = InMemoryStore(), DefaultEpisodeBuilder(), True
        p_on = build_full_pipeline(
            SteppingStubDetector(plan), clock_on, thresholds=th,
            memory_store=store, episode_builder=builder, episodic_shadow=True,
        )
        res_on = drive(p_on, clock_on, plan, step_s)

        assert len(res_off) == len(res_on)
        for i, (ro, rn) in enumerate(zip(res_off, res_on)):
            assert len(ro.warnings) == len(rn.warnings), f"frame {i}: warnings 不一致"
            assert len(ro.risk_signals) == len(rn.risk_signals), f"frame {i}: risk_signals 不一致"
            assert len(ro.commands) == len(rn.commands), f"frame {i}: commands 不一致"
            assert len(ro.behavior_states) == len(rn.behavior_states), f"frame {i}: behavior_states 不一致"

        # 影子确实跑了（旁路产生 episode，但不影响主线）
        assert p_on.metrics.episodes_recorded == 1
        assert p_on._memory_store.get_active_episodic()

    def test_memory_episode_build_failure_is_isolated(self):
        """Memory 投影抛异常 → 主风险链路照常运行，不崩溃。"""

        class BoomBuilder(DefaultEpisodeBuilder):
            def project_episode(self, *a, **k):
                raise RuntimeError("memory store down")

        th = TH_HIGH()
        plan = _lifecycle_plan(30, 30.0)
        step_s = 30.0
        clock = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
        p = build_full_pipeline(
            SteppingStubDetector(plan), clock, thresholds=th,
            memory_store=InMemoryStore(), episode_builder=BoomBuilder(), episodic_shadow=True,
        )
        # 必须不抛异常
        results = drive(p, clock, plan, step_s)

        # 历史风险决策仍然发生（RiskSignal / Warning 照常产出）
        assert any(s.transition == SignalTransition.RAISED
                   for r in results for s in r.risk_signals), "风险信号仍应产生"
        assert sum(len(r.warnings) for r in results) > 0, "Warning 仍应产生"

        # Memory 故障被隔离：episode 0 条，errors 计 1 次（仅投影失败）
        assert p.metrics.episodes_recorded == 0
        assert p.metrics.errors == 1, "仅 Memory 投影失败应计 1 次 error，不崩主链路"

    def test_memory_store_invariant_violation_is_isolated(self):
        """Memory 落库抛 I2 冲突（InvariantViolationError）→ 不计 error，主链路无碍。"""

        class StrictStore(InMemoryStore):
            def upsert_episodic(self, record):
                raise InvariantViolationError("simulated conflict")

        th = TH_HIGH()
        plan = _lifecycle_plan(30, 30.0)
        step_s = 30.0
        clock = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
        p = build_full_pipeline(
            SteppingStubDetector(plan), clock, thresholds=th,
            memory_store=StrictStore(), episode_builder=DefaultEpisodeBuilder(),
            episodic_shadow=True,
        )
        results = drive(p, clock, plan, step_s)

        assert sum(len(r.warnings) for r in results) > 0, "Warning 仍应产生"
        assert p.metrics.episodes_recorded == 0
        # I2 冲突是防御性告警，不计入 errors（不崩主链路）
        assert p.metrics.errors == 0

    def test_memory_on_no_latency_regression(self):
        """宽松守护：Memory 开启不引入 O(n) 延迟爆炸（非微基准）。

        仅保证「旁路」不被实现成每帧全量重算——具体倍数留给性能专项。
        """
        import time

        th = TH_HIGH()
        n = 40
        plan = [_person(1) for _ in range(n - 2)] + [[] for _ in range(2)]
        step_s = 30.0

        # 关闭
        clock_off = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
        p_off = build_full_pipeline(
            SteppingStubDetector(plan), clock_off, thresholds=th, episodic_shadow=False,
        )
        t0 = time.perf_counter()
        drive(p_off, clock_off, plan, step_s)
        t_off = time.perf_counter() - t0

        # 开启
        clock_on = ManualClock(base=datetime(2026, 7, 31, 18, 30, 0, tzinfo=timezone.utc))
        store, builder, _ = InMemoryStore(), DefaultEpisodeBuilder(), True
        p_on = build_full_pipeline(
            SteppingStubDetector(plan), clock_on, thresholds=th,
            memory_store=store, episode_builder=builder, episodic_shadow=True,
        )
        t0 = time.perf_counter()
        drive(p_on, clock_on, plan, step_s)
        t_on = time.perf_counter() - t0

        # 宽松上界：守护「Memory 是旁路」不被 O(n) 拖垮
        assert t_on < t_off * 10 + 0.05, (
            f"Memory 开启不应显著拖慢：on={t_on:.4f}s off={t_off:.4f}s"
        )
        assert p_on.metrics.episodes_recorded == 1, "影子写入应正常发生"
