"""Slice E（落盘与留存）契约测试：JSONL sink / 保留期 / 落盘期脱敏守卫 / ABRun 序列化。

对齐 ADR-0031 Slice E + ADR-0002（本地、仅引用 ID）/ ADR-0027 D9（本地 UTC 计时、幂等删除、
失败不阻塞主链）+ T3（失败隔离）。所有测试用 tmp_path（pytest 提供，自动清理）。
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from home_perception.analysis.decision_sink import (
    DesensitizationError,
    JsonlABRunRecorder,
    JsonlTraceRecorder,
    assert_desensitized,
    prune_jsonl,
)
from home_perception.analysis.decision_trace import (
    CandidateRecord,
    DecisionABRun,
    DecisionTrace,
    MemoryRefs,
    SuppressReason,
    TraceIdentity,
    TraceOutcome,
    TraceOutcomeKind,
    TracePolicy,
    TraceProvenance,
    TraceRationale,
    TriggerRef,
)

# ---------------------------------------------------------------------------
# 构造助手（最小化但合法的 DecisionTrace / DecisionABRun）
# ---------------------------------------------------------------------------


def _make_trace(
    kind: TraceOutcomeKind = TraceOutcomeKind.WARN,
    *,
    correlation_id: str = "cid-1",
    decision_id: str = "d-1",
    arm: str = "production",
    created_at: datetime | None = None,
    reasoning_present: bool = False,
    visitor_id: str = "visitor_01",
) -> DecisionTrace:
    created = created_at or datetime(2026, 8, 8, 3, 0, 0, tzinfo=UTC)
    identity = TraceIdentity(
        decision_id=decision_id,
        correlation_id=correlation_id,
        arm=arm,
        created_at=created,
    )
    provenance = TraceProvenance(
        input_digest="id-abc",
        trigger_digest="td-abc",
        trigger_refs=(
            TriggerRef(index=0, visitor_id=visitor_id, event_type="visit_normal", timestamp=1.0),
        ),
        memory_refs=MemoryRefs(reasoning_input_present=reasoning_present),
    )
    policy = TracePolicy(name="rule", fingerprint="fp-abc")
    rationale = TraceRationale(
        considered_candidates=(
            CandidateRecord(
                trigger_index=0,
                event_type="visit_normal",
                routed_level="LOW",
                routed_action="MONITOR",
                priority=1,
            ),
        ),
        chosen_index=0 if kind == TraceOutcomeKind.WARN else None,
    )
    if kind == TraceOutcomeKind.WARN:
        outcome = TraceOutcome(
            kind=kind, risk_level="LOW", recommended_action="MONITOR", warning_id="w-1"
        )
    else:
        outcome = TraceOutcome(kind=kind, suppress_reason=SuppressReason.NO_TRIGGER_EVENTS)
    return DecisionTrace(
        identity=identity, provenance=provenance, policy=policy, rationale=rationale, outcome=outcome
    )


def _make_ab_run(
    baseline_kind: TraceOutcomeKind = TraceOutcomeKind.WARN,
    candidate_kind: TraceOutcomeKind = TraceOutcomeKind.SUPPRESS,
    correlation_id: str = "cid-1",
    baseline_created: datetime | None = None,
    candidate_created: datetime | None = None,
) -> DecisionABRun:
    baseline = _make_trace(
        baseline_kind,
        correlation_id=correlation_id,
        decision_id="d-base",
        arm="baseline",
        created_at=baseline_created,
        reasoning_present=False,
    )
    candidate = _make_trace(
        candidate_kind,
        correlation_id=correlation_id,
        decision_id="d-cand",
        arm="candidate",
        created_at=candidate_created,
        reasoning_present=True,
    )
    return DecisionABRun(
        correlation_id=correlation_id, trace_baseline=baseline, trace_candidate=candidate
    )


def _read_lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# 脱敏守卫（fail-closed 兜底）
# ---------------------------------------------------------------------------


class TestDesensitizationGuard:
    def test_normal_trace_payload_passes(self):
        # 正常 trace 不能触发误报（守卫不得拒绝合法审计血缘）
        payload = _make_trace().to_dict()
        assert_desensitized(payload)  # 不抛

    def test_forbidden_verdict_field_rejected(self):
        payload = {"identity": {"decision_id": "x"}, "fraud": True}
        with pytest.raises(DesensitizationError, match="forbidden_field:fraud"):
            assert_desensitized(payload)

    def test_sensitive_key_rejected(self):
        payload = {"identity": {"decision_id": "x"}, "api_key": "sk-123"}
        with pytest.raises(DesensitizationError, match="sensitive_key:api_key"):
            assert_desensitized(payload)

    def test_absolute_path_string_rejected(self):
        payload = {"identity": {"decision_id": "x"}, "note": "/home/elder/video.mp4"}
        with pytest.raises(DesensitizationError, match="path_or_url"):
            assert_desensitized(payload)

    def test_url_string_rejected(self):
        payload = {"identity": {"decision_id": "x"}, "note": "http://center/上传"}
        with pytest.raises(DesensitizationError, match="path_or_url"):
            assert_desensitized(payload)

    def test_nested_forbidden_field_rejected(self):
        # 嵌套到 provenance.memory_refs 内部仍应被递归捕获
        payload = {"provenance": {"memory_refs": {"is_scammer": 1}}}
        with pytest.raises(DesensitizationError, match="forbidden_field:is_scammer"):
            assert_desensitized(payload)

    def test_sensitive_key_substring_match(self):
        # 子串匹配：命名变体也必须命中（修复 exact-set 语义偏差）
        for variant in [
            "user_password_hash",
            "secret_question",
            "auth_token",
            "aws_access_key_id",
            "api_keys",
            "client_secret",
        ]:
            payload = {"identity": {"decision_id": "x"}, variant: "v"}  # type: ignore[dict-item]
            with pytest.raises(DesensitizationError, match="sensitive_key:"):
                assert_desensitized(payload)

    def test_id_card_and_ssn_keys_rejected(self):
        # 身份证 / ssn / social_security 等个人敏感键（PR 描述承诺，此前漏判）
        for key in ["id_card", "ssn", "social_security", "身份证"]:
            payload = {"identity": {"decision_id": "x"}, key: "v"}  # type: ignore[dict-item]
            with pytest.raises(DesensitizationError, match="sensitive_key:"):
                assert_desensitized(payload)

    def test_path_detection_only_at_string_start(self):
        # 当前意图：路径 / URL 仅从字符串**开头**匹配（避免正文里的 "http://" 误报）。
        # 钉住此行为：嵌套正文中的 URL 不触发，但开头路径触发。
        inline = {"identity": {"decision_id": "x"}, "note": "见 http://center 上传"}
        assert_desensitized(inline)  # 不抛
        leading = {"identity": {"decision_id": "x"}, "note": "http://center/上传"}
        with pytest.raises(DesensitizationError, match="path_or_url"):
            assert_desensitized(leading)

    def test_bytes_payload_rejected_fail_closed(self):
        # bytes 一律 fail-closed 拒绝（不该出现在落盘 payload，且可藏匿路径 / 凭证）
        payload = {"identity": {"decision_id": "x"}, "blob": b"/etc/passwd"}
        with pytest.raises(DesensitizationError, match="bytes_payload"):
            assert_desensitized(payload)

    def test_tuple_recursion_no_false_negative(self):
        # tuple 复用 list 分支递归；路径藏在 tuple 内仍应被捕获
        payload = {"identity": {"decision_id": "x"}, "refs": ("/home/elder/a.mp4",)}
        with pytest.raises(DesensitizationError, match="path_or_url"):
            assert_desensitized(payload)


# ---------------------------------------------------------------------------
# JSONL 落盘 + 往返
# ---------------------------------------------------------------------------


class TestJsonlTraceRecorder:
    def test_record_writes_one_line_and_roundtrips(self, tmp_path: Path):
        path = tmp_path / "traces.jsonl"
        rec = JsonlTraceRecorder(path=path)
        trace = _make_trace()
        rec.record(trace)
        rec.flush()

        lines = _read_lines(path)
        assert len(lines) == 1
        restored = DecisionTrace.from_dict(json.loads(lines[0]))
        assert restored == trace  # 往返一致（frozen dataclass 深比较）

    def test_multiple_records_appended(self, tmp_path: Path):
        path = tmp_path / "traces.jsonl"
        rec = JsonlTraceRecorder(path=path)
        rec.record(_make_trace(decision_id="d-1"))
        rec.record(_make_trace(decision_id="d-2", kind=TraceOutcomeKind.SUPPRESS))
        assert len(_read_lines(path)) == 2

    def test_recorder_is_structurally_a_decision_trace_recorder(self):
        # 满足 DecisionTraceRecorder Protocol（record + flush），可被 engine 注入
        from home_perception.analysis.decision_trace import DecisionTraceRecorder

        rec = JsonlTraceRecorder(path=Path("/tmp/_unused.jsonl"))
        assert isinstance(rec, DecisionTraceRecorder)

    def test_failure_isolated_t3(self, tmp_path: Path, monkeypatch):
        # T3：写盘异常不得外抛影响决策——record 静默吞掉，仅 log
        path = tmp_path / "traces.jsonl"
        rec = JsonlTraceRecorder(path=path)
        monkeypatch.setattr(
            Path, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        )
        # record 必须不抛（异常被隔离，决策不受影响）
        rec.record(_make_trace())


# ---------------------------------------------------------------------------
# 保留期轮转（本地 UTC、inclusive 边界、幂等）
# ---------------------------------------------------------------------------


class TestRetentionPrune:
    def test_prune_removes_expired_keeps_recent(self, tmp_path: Path):
        path = tmp_path / "traces.jsonl"
        rec = JsonlTraceRecorder(path=path, retention_days=1)
        now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        old = _make_trace(decision_id="d-old", created_at=now - timedelta(days=2))
        recent = _make_trace(decision_id="d-recent", created_at=now - timedelta(hours=1))
        rec.record(old)
        rec.record(recent)

        removed = prune_jsonl(path, retention_days=1, now_utc=now)
        assert removed == 1
        lines = _read_lines(path)
        assert len(lines) == 1
        restored = DecisionTrace.from_dict(json.loads(lines[0]))
        assert restored.identity.decision_id == "d-recent"

    def test_retention_boundary_inclusive(self, tmp_path: Path):
        # created_at 恰好 = now - retention_days → 边界 inclusive，保留
        path = tmp_path / "traces.jsonl"
        rec = JsonlTraceRecorder(path=path, retention_days=1)
        now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        exactly = _make_trace(created_at=now - timedelta(days=1))
        over = _make_trace(decision_id="d-over", created_at=now - timedelta(days=1, seconds=1))
        rec.record(exactly)
        rec.record(over)

        removed = prune_jsonl(path, retention_days=1, now_utc=now)
        assert removed == 1  # 仅 over 被删，exactly 边界保留
        lines = _read_lines(path)
        assert len(lines) == 1
        assert DecisionTrace.from_dict(json.loads(lines[0])).identity.decision_id != "d-over"

    def test_prune_idempotent(self, tmp_path: Path):
        path = tmp_path / "traces.jsonl"
        rec = JsonlTraceRecorder(path=path, retention_days=1)
        now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        rec.record(_make_trace(decision_id="d-old", created_at=now - timedelta(days=3)))
        rec.record(_make_trace(decision_id="d-new", created_at=now - timedelta(hours=1)))

        first = prune_jsonl(path, retention_days=1, now_utc=now)
        second = prune_jsonl(path, retention_days=1, now_utc=now)
        assert first == 1
        assert second == 0  # 幂等：第二次无删除
        assert len(_read_lines(path)) == 1

    def test_prune_missing_file_noop(self, tmp_path: Path):
        path = tmp_path / "absent.jsonl"
        assert prune_jsonl(path, retention_days=1) == 0

    def test_recorder_prune_method_proxies(self, tmp_path: Path):
        path = tmp_path / "traces.jsonl"
        rec = JsonlTraceRecorder(path=path, retention_days=1)
        now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        rec.record(_make_trace(decision_id="d-old", created_at=now - timedelta(days=3)))
        assert rec.prune(now_utc=now) == 1

    def test_prune_preserves_corrupt_line(self, tmp_path: Path, capsys):
        # PR 承诺「损坏行不阻断其余行」：损坏行保留、正常行按保留期轮转。
        path = tmp_path / "traces.jsonl"
        now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        # 手工写入：一行损坏 + 一行过期正常 trace + 一行近期正常 trace
        corrupt = "{malformed json no closing"
        expired = json.dumps(
            _make_trace(decision_id="d-old", created_at=now - timedelta(days=3)).to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
        recent = json.dumps(
            _make_trace(decision_id="d-recent", created_at=now - timedelta(hours=1)).to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
        path.write_text(corrupt + "\n" + expired + "\n" + recent + "\n", encoding="utf-8")

        removed = prune_jsonl(path, retention_days=1, now_utc=now)
        # 仅 expired 被删；corrupt 保留（宁留痕不丢）；recent 保留
        assert removed == 1
        lines = _read_lines(path)
        assert corrupt in lines  # 损坏行被保留
        assert len(lines) == 2  # corrupt + recent
        # 损坏行确实触发了告警（structlog 经 PrintLogger 写 stdout，用 capsys 捕获）
        assert "decision.trace_prune_parse_failed" in capsys.readouterr().out


class TestConcurrentRecord:
    def test_shared_fh_no_race(self, tmp_path: Path):
        # 长生命周期 fh + 锁：多线程并发写不丢行、不抛异常。
        path = tmp_path / "traces.jsonl"
        rec = JsonlTraceRecorder(path=path)
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            try:
                for _ in range(20):
                    rec.record(_make_trace(decision_id=f"d-{i}-{_}"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rec.flush()

        assert not errors
        # 4 线程 × 20 行 = 80 行，无丢失
        assert len(_read_lines(path)) == 80


# ---------------------------------------------------------------------------
# DecisionABRun 序列化（review #4 归 Slice E）+ 双轨落盘
# ---------------------------------------------------------------------------


class TestDecisionABRunSerialization:
    def test_abrun_roundtrip(self):
        run = _make_ab_run()
        restored = DecisionABRun.from_dict(run.to_dict())
        assert restored == run
        # 守恒在往返后仍然成立
        restored.assert_conserved()

    def test_abrun_jsonl_persist_and_restore(self, tmp_path: Path):
        path = tmp_path / "abruns.jsonl"
        rec = JsonlABRunRecorder(path=path)
        run = _make_ab_run()
        rec.record(run)
        rec.flush()

        lines = _read_lines(path)
        assert len(lines) == 1
        restored = DecisionABRun.from_dict(json.loads(lines[0]))
        assert restored == run
        assert restored.outcome_pair == (TraceOutcomeKind.WARN, TraceOutcomeKind.SUPPRESS)

    def test_abrun_recorder_failure_isolated_t3(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "abruns.jsonl"
        rec = JsonlABRunRecorder(path=path)
        monkeypatch.setattr(
            Path, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        )
        rec.record(_make_ab_run())  # 不抛

    def test_abrun_prune_by_baseline_created_at(self, tmp_path: Path):
        path = tmp_path / "abruns.jsonl"
        rec = JsonlABRunRecorder(path=path, retention_days=1)
        now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        old = _make_ab_run(correlation_id="c-old", baseline_created=now - timedelta(days=3))
        recent = _make_ab_run(correlation_id="c-new", baseline_created=now - timedelta(hours=1))
        rec.record(old)
        rec.record(recent)

        removed = prune_jsonl(path, retention_days=1, now_utc=now)
        assert removed == 1
        lines = _read_lines(path)
        assert len(lines) == 1
        assert (
            DecisionABRun.from_dict(json.loads(lines[0])).correlation_id == "c-new"
        )
