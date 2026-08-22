"""ADR-0043 · RiskSignal 双轨投影契约测试。

冻结契约钉死项：
- **D1 两轨分工**：状态轨 ``risk_signals`` 覆盖式（"当前风险是什么"，服务端权威
  risk_transition 状态机数据源零改动）；事件轨 ``risk_signal_events`` 累积式
  （"刚刚发生过什么"，RAISED→CLEARED 全生命周期可追溯）；
- **D2 幂等**：幂等键 = ``signal_id``（重放重喂同一信号不重复累积）；
  序列 = ``seq`` 单调递增（增量推送判定）；
- **D3 payload**：指纹未变不推；新 seq 才推（仅携带水位后新增）；首连全量；
- **schema 契约**：事件轨条目字段集合钉死（增删字段须改本测试并走契约评审）。
"""

from __future__ import annotations

import pytest

from home_perception.visualizer.viewer.live_adapter import ProjectionAccumulator

# 事件轨条目字段集合（D2/D3 实现设计落定后的 schema 钉死；变更 = BREAKING 须评审）
SIGNAL_EVENT_FIELDS = frozenset(
    {
        "seq",
        "frame_index",
        "signal_id",
        "transition",
        "source",
        "category",
        "subject_type",
        "subject_id",
        "severity_hint",
        "paired_signal_id",
        "created_at",
        "features",
    }
)


def _signal(signal_id, *, transition="RAISED", paired=None, severity=0.8):
    return {
        "signal_id": signal_id,
        "subject_type": "VISITOR",
        "subject_id": "visitor-1",
        "category": "COMMUNICATION",
        "source": "AUDIO",
        "transition": transition,
        "features": {"audio_kind": "audio_telephone_persistent"},
        "paired_signal_id": paired,
        "severity_hint": severity,
        "created_at": "2026-08-22T12:00:00+00:00",
    }


def _frame(frame_index, *, risk_signals=()):
    return {
        "frame_index": frame_index,
        "n_detections": 0,
        "n_visitor_events": 0,
        "perception_events": [],
        "warnings": [],
        "commands": [],
        "detections": [],
        "risk_signals": list(risk_signals),
    }


# ============================================================================
# D1：两轨分工语义
# ============================================================================


class TestDualTrackSemantics:
    def test_state_track_overwritten_event_track_accumulated(self):
        """ADR 背景缺陷回归：CLEARED 覆盖 RAISED 后，事件轨仍保留完整生命周期。"""
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[_signal("sig-raised")]))
        acc.ingest(_frame(1, risk_signals=[_signal("sig-cleared", transition="CLEARED", paired="sig-raised")]))
        # 状态轨：覆盖式 → 只见最近一帧（服务端权威语义不变）
        assert [s["signal_id"] for s in acc.risk_fingerprint()[7]] == ["sig-cleared"]
        # 事件轨：累积式 → RAISED→CLEARED 全部可追溯
        assert [e["signal_id"] for e in acc.risk_signal_history] == ["sig-raised", "sig-cleared"]

    def test_paired_signal_id_renderable_from_history(self):
        """paired_signal_id 配对依赖历史可见性：从事件轨可还原配对关系。"""
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[_signal("r1")]))
        acc.ingest(_frame(1, risk_signals=[_signal("c1", transition="CLEARED", paired="r1")]))
        by_id = {e["signal_id"]: e for e in acc.risk_signal_history}
        assert by_id["c1"]["paired_signal_id"] == by_id["r1"]["signal_id"]
        assert by_id["c1"]["seq"] > by_id["r1"]["seq"]

    def test_history_ordered_by_ingestion_and_readonly(self):
        """事件轨 seq 升序 = 摄入序；property 返回 tuple（只读视图）。"""
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[_signal("a")]))
        acc.ingest(_frame(1, risk_signals=[_signal("b")]))
        acc.ingest(_frame(2, risk_signals=[_signal("c")]))
        hist = acc.risk_signal_history
        assert isinstance(hist, tuple)
        assert [e["seq"] for e in hist] == [1, 2, 3]


# ============================================================================
# D2：幂等契约
# ============================================================================


class TestIdempotency:
    def test_replay_same_stream_no_duplicate(self):
        """VM-8：live 循环重启重喂同一有序帧流 → 事件轨逐字段一致。"""
        frames = [
            _frame(0, risk_signals=[_signal("s1")]),
            _frame(1, risk_signals=[_signal("s2", transition="CLEARED", paired="s1")]),
        ]
        acc1 = ProjectionAccumulator("s")
        acc2 = ProjectionAccumulator("s")
        for frames_in in (frames, frames):
            for f in frames_in:
                acc1.ingest(f)
        for f in frames:
            acc2.ingest(f)
        assert list(acc1.risk_signal_history) == list(acc2.risk_signal_history)

    def test_duplicate_signal_id_within_session_skipped(self):
        """同会话内重复 signal_id 不重复累积（跨帧重复投递防御）。"""
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[_signal("dup")]))
        acc.ingest(_frame(1, risk_signals=[_signal("dup")]))
        assert len(acc.risk_signal_history) == 1
        assert acc._risk_signal_seq == 1

    def test_missing_signal_id_fail_closed_skip(self):
        """缺 signal_id 的信号跳过（无主键无法保证 D2），不中断整帧。"""
        bad = _signal("")

        bad.pop("signal_id")
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[bad, _signal("ok")]))
        assert [e["signal_id"] for e in acc.risk_signal_history] == ["ok"]


# ============================================================================
# D3：payload 增量推送
# ============================================================================


class TestDeltaIncremental:
    def test_first_connect_carries_full_history(self):
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[_signal("a"), _signal("b")]))
        delta = acc.extract_risk_delta(None)
        assert [e["signal_id"] for e in delta["risk_signal_events"]] == ["a", "b"]

    def test_fingerprint_unchanged_pushes_empty(self):
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[_signal("a")]))
        fp = acc.risk_fingerprint()
        delta = acc.extract_risk_delta(fp)
        assert delta["risk_signal_events"] == []

    def test_new_seq_only_carries_increment(self):
        """新信号到达 → 仅携带水位之后的新增（不全量刷屏）；无新信号的指纹变化 → 空增量。"""
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[_signal("a")]))
        fp0 = acc.risk_fingerprint()
        acc.ingest(_frame(1))  # 无信号帧：指纹可能因 frame_index 外字段不变而未变
        fp1 = acc.risk_fingerprint()
        acc.ingest(_frame(2, risk_signals=[_signal("b", transition="CLEARED", paired="a")]))
        delta = acc.extract_risk_delta(fp1)
        assert [e["signal_id"] for e in delta["risk_signal_events"]] == ["b"]
        # 水位推进后再取 → 无重复推送
        fp2 = acc.risk_fingerprint()
        assert acc.extract_risk_delta(fp2)["risk_signal_events"] == []
        # 从更早水位（fp0，seq=1）取 → 仅补发 seq>1 的新增
        assert [e["signal_id"] for e in acc.extract_risk_delta(fp0)["risk_signal_events"]] == ["b"]

    def test_changed_without_new_signals_empty_increment(self):
        """风险状态变化但无新信号 → risk_signal_events 为空（增量语义，非全量）。"""
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0))
        fp = acc.risk_fingerprint()
        acc.ingest(_frame(1, risk_signals=[]))
        delta = acc.extract_risk_delta(fp)
        assert delta["risk_signal_events"] == []


# ============================================================================
# Schema 钉死
# ============================================================================


class TestEventSchema:
    def test_event_entry_fields_frozen(self):
        acc = ProjectionAccumulator("s")
        acc.ingest(_frame(0, risk_signals=[_signal("a")]))
        entry = acc.risk_signal_history[0]
        assert frozenset(entry.keys()) == SIGNAL_EVENT_FIELDS


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])