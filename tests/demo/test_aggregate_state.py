"""P0-11.3.5 DemoAggregateState 单元测试（无需 YOLO / 网络 / torch）。

验证服务端权威聚合状态的核心不变量：
- 每帧 ingest 正确累积 warning / behavior / command（去重规则与 dashboard 一致）
- 终态 warning 移除 + 超上限修剪
- last_warning 取最高风险
- clear() 清空累积；reset_session=True 重置会话计时
- snapshot() 含客户端恢复所需的 visitor_seq / behavior_seen 等键
- meta() 提供状态面板 / 晚连恢复所需的运行时元数据
"""
from __future__ import annotations

from silver_demo.state import DemoAggregateState


def _sample() -> dict:
    return {
        "active_warnings": [
            {"warning_id": "w1", "risk_level": "LOW", "status": "PENDING",
             "created_at": "2026-01-01T00:00:01", "reason_summary": ["夜间异常"]},
            {"warning_id": "w2", "risk_level": "HIGH", "status": "PENDING",
             "created_at": "2026-01-01T00:00:02"},
        ],
        "perception_events": [
            {"visitor_id": "v1", "event_type": "abnormal_dwell", "created_at": "t",
             "location": "门口", "score": 0.7, "repeat_count": 1},
        ],
        "all_warnings": [],
        "routed": {
            "family": [{"command_id": "c1", "warning_id": "w1",
                        "command_type": "SEND_FAMILY_MESSAGE"}],
            "community": [],
            "log_only": [],
        },
        "frame_index": 7,
        "loop_count": 2,
    }


def test_ingest_accumulates_warnings_behaviors_commands():
    a = DemoAggregateState()
    s = _sample()
    a.ingest(s["active_warnings"], s["perception_events"], s["all_warnings"],
             s["routed"], s["frame_index"], s["loop_count"])

    assert set(a.warnings.keys()) == {"w1", "w2"}
    keys = {b["key"] for b in a.behaviors}
    assert "enter|v1" in keys              # 访客首次出现
    assert any(k.startswith("pe|v1|") for k in keys)  # 行为里程碑
    assert "warn|w1" in keys and "warn|w2" in keys     # 风险预警里程碑
    assert "w1" in a.commands and "c1" in a.commands["w1"]["family"]
    assert a.frame_index == 7 and a.loop_count == 2


def test_ingest_is_idempotent_on_repeat_frame():
    a = DemoAggregateState()
    s = _sample()
    a.ingest(s["active_warnings"], s["perception_events"], s["all_warnings"],
             s["routed"], 7, 2)
    n_before = len(a.behaviors)
    # 重复同一帧：行为里程碑不应翻倍（去重键生效）
    a.ingest(s["active_warnings"], s["perception_events"], s["all_warnings"],
             s["routed"], 8, 2)
    assert len(a.behaviors) == n_before
    assert set(a.warnings.keys()) == {"w1", "w2"}


def test_last_warning_picks_highest_risk():
    a = DemoAggregateState()
    a.ingest(
        [{"warning_id": "w1", "risk_level": "LOW"},
         {"warning_id": "w2", "risk_level": "HIGH"}],
        [], [], {"family": [], "community": [], "log_only": []}, 0, 0,
    )
    assert a.last_warning["warning_id"] == "w2"


def test_terminal_status_removed():
    a = DemoAggregateState()
    a.ingest(
        [{"warning_id": "w1", "risk_level": "LOW", "status": "RESOLVED",
          "created_at": "t"}],
        [], [], {"family": [], "community": [], "log_only": []}, 0, 0,
    )
    assert "w1" not in a.warnings


def test_clear_resets_accumulation():
    a = DemoAggregateState()
    a.ingest(_sample()["active_warnings"], _sample()["perception_events"],
             [], _sample()["routed"], 7, 2)
    a.clear()
    assert a.warnings == {}
    assert a.behaviors == []
    assert a.commands == {}
    assert a.frame_index == 0 and a.loop_count == 0


def test_clear_reset_session_refreshes_started_at():
    a = DemoAggregateState()
    a.started_at = 1.0
    a.clear(reset_session=True)
    assert a.started_at > 1.0  # 会话计时刷新


def test_snapshot_roundtrip_carries_client_restore_keys():
    a = DemoAggregateState()
    s = _sample()
    a.ingest(s["active_warnings"], s["perception_events"], s["all_warnings"],
             s["routed"], 7, 2)
    snap = a.snapshot()
    assert len(snap["warnings"]) == 2
    assert len(snap["behaviors"]) >= 4
    assert snap["visitor_seq"]            # 访客友好名映射（客户端恢复）
    assert snap["behavior_seen"]          # 去重键集合（客户端恢复）
    assert snap["visitor_first"]          # 首次出现记录
    assert "w1" in snap["commands"]


def test_meta_shape():
    a = DemoAggregateState()
    a.scenario = "s"
    a.source = "x"
    a.source_type = "video_file"
    a.n_frames = 100
    a.started_at = 123.0
    a.last_warning = {"warning_id": "w2", "risk_level": "HIGH"}
    m = a.meta()
    assert m["session_status"] == "RUNNING"
    assert m["n_frames"] == 100
    assert m["source"] == "x"
    assert m["started_at"] == 123.0
    assert m["last_warning"]["warning_id"] == "w2"


def test_warning_prune_respects_max():
    a = DemoAggregateState()
    # 注入 35 条全 PENDING warning（无终态移除），应修剪到 _WARNING_MAX(30)
    warns = [
        {"warning_id": f"w{i}", "risk_level": "LOW", "status": "PENDING",
         "created_at": f"2026-01-01T00:00:{i:02d}"}
        for i in range(35)
    ]
    a.ingest(warns, [], [], {"family": [], "community": [], "log_only": []}, 0, 0)
    assert len(a.warnings) == 30  # _WARNING_MAX
    # 最旧的 5 条（created_at 最早）被移除
    assert "w0" not in a.warnings and "w1" not in a.warnings
    assert "w34" in a.warnings


def test_behavior_prune_respects_max():
    a = DemoAggregateState()
    # 注入 125 条去重的行为里程碑（不同 visitor_id），应修剪到 _BEHAVIOR_MAX(120)
    pes = [
        {"visitor_id": f"v{i}", "event_type": "abnormal_dwell",
         "created_at": f"2026-01-01T00:00:{i:02d}", "repeat_count": 1}
        for i in range(125)
    ]
    a.ingest([], pes, [], {"family": [], "community": [], "log_only": []}, 0, 0)
    assert len(a.behaviors) == 120  # _BEHAVIOR_MAX


def test_merge_commands_single_bucket_capped_at_24():
    a = DemoAggregateState()
    # 同一 warning_id 同一 type 注入 30 条命令（不同 command_id），单桶应截断到 ≤24
    cmds = [
        {"command_id": f"c{i}", "warning_id": "w1", "command_type": "SEND_FAMILY_MESSAGE"}
        for i in range(30)
    ]
    a.ingest([], [], [], {"family": cmds, "community": [], "log_only": []}, 0, 0)
    assert len(a.commands["w1"]["family"]) == 24  # 单桶上限 24（与客户端一致）


def test_snapshot_restores_warnings_into_fresh_state():
    a = DemoAggregateState()
    a.ingest(_sample()["active_warnings"], _sample()["perception_events"],
             _sample()["all_warnings"], _sample()["routed"], 7, 2)
    snap = a.snapshot()
    # 晚连客户端（或新聚合实例）用 snapshot 重建：warnings 集合应与源一致
    # （镜像前端 applySnapshot 用 snapshot.warnings 恢复 warningMap 的端到端可达性）
    b = DemoAggregateState()
    b.ingest(snap["warnings"], [], [], {"family": [], "community": [], "log_only": []}, 0, 0)
    assert set(b.warnings.keys()) == set(a.warnings.keys()) == {"w1", "w2"}
