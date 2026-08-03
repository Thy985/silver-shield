"""FrameResult → 三端 view-model 桥接（ADR-0015 §2.2 / §2.4）。

本模块是「冻结契约」与「展示层」之间的**唯一翻译点**：

    FrameResult（冻结对象）
        │
        ├── frame(np.ndarray) ──► JPEG encode ──► base64 ──► Dashboard 实时视频区
        │
        └── warnings / commands / perception_events ──► to_dict() ──► 三端 view-model

严格规则（ADR-0015 §5 冻结合规）：
- 对 ``WarningEvent`` / ``ActionCommand`` / ``PerceptionEvent`` **只调** ``to_dict()``
  或读取公开字段，**不**调构造器、**不**修改字段。
- ``frame`` 是 ``np.ndarray``（BGR），经 OpenCV JPEG 编码 → base64 字符串。
- 不引入业务判定逻辑（本模块不做风险解释，只做格式转换）。
"""

from __future__ import annotations

import base64
from typing import Any

import structlog


def encode_frame_to_base64_jpeg(
    frame: Any,
    quality: int = 50,
    max_width: int | None = None,
) -> str | None:
    """把 BGR np.ndarray 编码为 base64 JPEG 字符串。

    Args:
        frame: ``process_frame`` 接收的同款帧对象（np.ndarray BGR）。None 或编码失败返回 None。
        quality: JPEG 质量 1-100（Demo 50 足够，降带宽）。
        max_width: 编码前将帧宽度缩放到此值（保持比例）；None 或不满足 >0 则原尺寸编码。
            用于降低推送给前端的预览帧体积（降 base64 与前端解码耗时）。

    Returns:
        base64 编码的 JPEG 字符串（无 data: 前缀），或 None。

    边界：仅用 cv2 做编码/缩放，不触碰任何 home_perception 组件。
    """
    if frame is None:
        return None
    try:
        import cv2
    except ImportError:  # pragma: no cover - 依赖缺失
        return None
    try:
        if max_width and isinstance(max_width, int) and max_width > 0:
            _, w = frame.shape[:2]
            if w > max_width:
                scale = max_width / float(w)
                frame = cv2.resize(
                    frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
                )
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        return base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception:  # noqa: BLE001  # 编码失败不崩溃网关（AGENTS.md §2.5 可恢复）
        return None


def frame_result_to_view(
    frame_result: Any,
    frame_index: int,
    frame_base64: str | None,
    demo_time: str | None = None,
) -> dict[str, Any]:
    """把 FrameResult 翻译成 Dashboard view-model（JSON-serializable）。

    Args:
        frame_result: ``PerceptionPipeline.process_frame`` 返回的 FrameResult（冻结类型，只读）。
        frame_index: 帧序号（网关维护，非 FrameResult 字段）。
        frame_base64: 本帧的 base64 JPEG（由 encode_frame_to_base64_jpeg 产出）。
        demo_time: DemoClock 当前时间 ISO 字符串（供时间线展示）。

    Returns:
        dict，结构：
        ```
        {
          "frame_index": int,
          "demo_time": str | None,
          "frame_base64": str | None,
          "n_detections": int,
          "n_visitor_events": int,
          "perception_events": [ {event_type, score, ...}, ... ],   # 经 to_dict()
          "warnings": [ {warning_id, risk_level, reason_summary, ...}, ... ],
          "commands": [ {command_id, command_type, payload, ...}, ... ],
          # —— 实时风险状态流（ADR-0021 Phase 1 · 演示层接入）——
          "behavior_states": [ {track_id, phase, dwell_seconds, ...}, ... ],  # 在场访客纯实时快照
          "risk_signals":   [ {signal_id, transition, category, ...}, ... ],  # RAISED/CLEARED 跃迁
          # —— ADR-0025 C-4/C-6 Memory Context（区域⑥ · 只读 Shadow）——
          "memory_profiles": [ {visitor_instance_id, memory_status, n_episodes, known_patterns,
                                 baseline, current, deviation, evidence, with_memory,
                                 suggested_action_hint}, ... ],
        }
        ```

    冻结合规：对 warnings/commands/perception_events/behavior_states/risk_signals
    只调 ``to_dict()``，不调构造器、不改字段。若对象无 to_dict 则降级为空 dict
    （防御性，不崩溃）。behavior_states / risk_signals 为实时旁路产物，关闭
    realtime_risk 时 FrameResult 默认给空列表，本函数安全返回空列表。

    ⚠️ 下游不可信约定：``_safe_to_dict`` 对「无 ``to_dict()`` 或调用抛错」的对象会**静默**
    丢弃为 ``{}``（不记录、不告警）。调用方（DemoAggregateState.ingest /
    Dashboard 渲染）必须能容忍列表中出现空 dict，不得假设每个元素都是完整结构。
    这是有意的防御性取舍——保证单条坏数据不拖垮整帧展示。
    """

    def _safe_to_dict(obj: Any) -> dict[str, Any]:
        """调 obj.to_dict()，失败返回空 dict（不崩溃网关）。"""
        try:
            d = obj.to_dict()
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001  # to_dict 失败不崩溃网关
            return {}

    perception_events = [_safe_to_dict(p) for p in getattr(frame_result, "perception_events", [])]
    warnings = [_safe_to_dict(w) for w in getattr(frame_result, "warnings", [])]
    commands = [_safe_to_dict(c) for c in getattr(frame_result, "commands", [])]
    # 实时风险状态流（ADR-0021 Phase 1 · 演示层接入）：纯只读 to_dict 翻译，
    # 不调构造器、不改字段；关闭 realtime_risk 时 FrameResult 给空列表，安全返回 []。
    behavior_states = [_safe_to_dict(s) for s in getattr(frame_result, "behavior_states", [])]
    risk_signals = [_safe_to_dict(s) for s in getattr(frame_result, "risk_signals", [])]

    # 时间线一致性修复（Region 1 模拟时间 vs Region 2 AI 行为时间线对不上）：
    # 模型里 perception_event/warning 的 created_at 是真实墙钟 UTC（default_factory=_utc_now），
    # 而 Region 1 的 demo_time 是 DemoClock 模拟时间（每帧推进 frame_interval_s）。两者时基不同，
    # 直接透传会导致 ①区/②区时间错位。此处把「展示用副本」的 created_at 重打为 demo_time，
    # 使两区（及服务端聚合状态的行为时间线）共用同一模拟时基。
    # 仅改 to_dict() 产出的副本，不触碰冻结模型对象 —— 守住 ADR-0015 冻结边界。
    if demo_time is not None:
        for _d in perception_events:
            if isinstance(_d, dict):
                _d["created_at"] = demo_time
        for _d in warnings:
            if isinstance(_d, dict):
                _d["created_at"] = demo_time
        # 实时信号 created_at 同为真实墙钟 UTC（now_provider），重打为 demo_time
        # 使风险卡与 ①② 区共用同一模拟时基（同 perception/warnings 的处理）。
        for _d in risk_signals:
            if isinstance(_d, dict):
                _d["created_at"] = demo_time

    # —— ADR-0025 C-4/C-6 Memory Context（区域⑥ · 只读 Shadow）——
    # 从 FrameResult 的 (ReasoningInput, ReasoningResult) 对派生 Visitor Memory Profile。
    # 纯格式转换（与 warnings/commands 同款 to_dict 风格），不引入业务判定逻辑。
    memory_profiles = build_memory_profiles(
        getattr(frame_result, "memory_inputs", []),
        getattr(frame_result, "reasoning_results", []),
    )

    return {
        "frame_index": frame_index,
        "demo_time": demo_time,
        "frame_base64": frame_base64,
        "n_detections": getattr(frame_result, "n_detections", 0),
        "n_visitor_events": getattr(frame_result, "n_visitor_events", 0),
        "perception_events": perception_events,
        "warnings": warnings,
        "commands": commands,
        "behavior_states": behavior_states,
        "risk_signals": risk_signals,
        "memory_profiles": memory_profiles,
    }


def collect_active_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 view-model 的 warnings 列表中筛出"待处理"的（status != RESOLVED/REJECTED）。

    供 AI 风险中心"风险解释卡片"展示当前活跃风险（P0-11.3 消费）。
    只做 dict 过滤，不触碰冻结对象。

    防御：非 dict 元素（None / 其他类型）直接跳过，不崩溃（公开函数，可能被直接调用）。
    """
    return [
        w
        for w in warnings
        if isinstance(w, dict) and w.get("status") not in ("RESOLVED", "REJECTED")
    ]


def route_commands(commands: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按 command_type 路由 ActionCommand 到三端（ADR-0015 §2.2）。

    Returns:
        ``{"family": [...], "community": [...], "log_only": [...]}``
        - family ← SEND_FAMILY_MESSAGE
        - community ← CREATE_COMMUNITY_TASK
        - log_only ← LOG_ONLY

    **未知 command_type 会被静默丢弃**（不进入任何桶）——本函数只做路由，不做兜底。
    由 P0-11.4 家属/社区交互层消费（当前 P0-11.1 网关仅广播原始 commands，未调用本函数）。

    防御：非 dict 元素（None / 其他类型）直接跳过，不崩溃（公开函数，可能被直接调用）。
    """
    family: list[dict[str, Any]] = []
    community: list[dict[str, Any]] = []
    log_only: list[dict[str, Any]] = []
    for c in commands:
        if not isinstance(c, dict):
            continue  # 防御：非 dict 元素跳过，不崩溃
        ct = c.get("command_type", "")
        if ct == "SEND_FAMILY_MESSAGE":
            family.append(c)
        elif ct == "CREATE_COMMUNITY_TASK":
            community.append(c)
        elif ct == "LOG_ONLY":
            log_only.append(c)
    return {"family": family, "community": community, "log_only": log_only}


# ---------------------------------------------------------------------------
# Memory Context（区域⑥ · ADR-0025 C-4/C-6 · 只读 Shadow 视图模型）
# ---------------------------------------------------------------------------
def build_memory_profiles(
    memory_inputs: Any,
    reasoning_results: Any,
) -> list[dict[str, Any]]:
    """从 FrameResult 的 (ReasoningInput, ReasoningResult) 对派生区域⑥ 只读画像（纯函数）。

    与 ``reasoning_results`` 同 Shadow 语义：只读、不决策、不接决策（守 ADR-0010）。
    入参与 ``FrameResult.memory_inputs`` / ``reasoning_results`` 一一对应（同一次
    ``maybe_consume`` 产出的 ``ReasoningInput`` 与其 ``maybe_reason`` 产出的
    ``ReasoningResult`` 同序）。任一对象缺失/异常时跳过该条（防御性，不崩溃网关）。

    返回 list[dict]，每个 dict 结构（字段缺失时给合理默认，便于前端容错渲染）：
        {
          "visitor_instance_id": str,
          "memory_status": str,          # active/deprecated/archived/invalid/none
          "n_episodes": int,             # 历史上下文条数
          "known_patterns": [str, ...],  # risk_pattern.tags + 重复访客标识
          "baseline": {"enter_hours":[lo,hi], "avg_duration_s":float} | None,
          "current": {"hour": int | None} | None,
          "deviation": [str, ...],       # 当前到访 vs 基线的偏差描述
          "evidence": [str, ...],        # source_refs(record_id) + source_event_ids 去重
          "with_memory": {"findings":[...], "explanation":str} | None,  # ReasoningResult
          "suggested_action_hint": str | None,
        }
    """
    profiles: list[dict[str, Any]] = []
    mis = memory_inputs or []
    rrs = reasoning_results or []
    for i, ri in enumerate(mis):
        rr = rrs[i] if i < len(rrs) else None
        try:
            profiles.append(_build_one_memory_profile(ri, rr))
        except Exception as exc:  # noqa: BLE001  # 单条坏数据不拖垮整帧展示
            structlog.get_logger(__name__).warning(
                "build_memory_profiles: 跳过坏记忆输入", error=str(exc)
            )
            continue
    return profiles


def _build_one_memory_profile(ri: Any, rr: Any | None) -> dict[str, Any]:
    """派生单条 Visitor Memory Profile（纯函数；假定 ri 为 ReasoningInput 形状）。"""
    # —— 历史上下文聚合 ——
    hc = getattr(ri, "historical_context", ()) or ()
    n_episodes = len(hc)
    status_counts: dict[str, int] = {}
    enter_hours: list[int] = []
    durations: list[float] = []
    source_event_ids: list[str] = []
    for ep in hc:
        st = getattr(ep, "memory_status", None)
        key = getattr(st, "value", st) if st is not None else "unknown"
        if isinstance(key, str):
            status_counts[key] = status_counts.get(key, 0) + 1
        et = getattr(ep, "enter_time", None)
        if et is not None:
            try:
                enter_hours.append(int(et.hour))
            except Exception as exc:  # noqa: BLE001
                structlog.get_logger(__name__).warning(
                    "build_memory_profile: enter_time 解析失败, 跳过", error=str(exc)
                )
        dur = getattr(ep, "duration_seconds", None)
        if isinstance(dur, (int, float)):
            durations.append(float(dur))
        se = getattr(ep, "source_event_ids", None)
        if se:
            source_event_ids.extend(se)

    if not status_counts:
        memory_status = "none"
    elif status_counts.get("active"):
        memory_status = "active"
    else:
        # 无 active 时取出现最多的状态（如 deprecated×2）
        memory_status = max(status_counts.items(), key=lambda kv: kv[1])[0]

    # —— 已知模式 ——
    rp = getattr(ri, "risk_pattern", None)
    known_patterns = list(getattr(rp, "tags", ()) or ()) if rp is not None else []
    if n_episodes > 0 and "repeat_visitor" not in known_patterns:
        known_patterns.append("repeat_visitor")

    # —— 行为基线（来自历史 enter/leave/duration）——
    baseline: dict[str, Any] | None = None
    if enter_hours and durations:
        baseline = {
            "enter_hours": [min(enter_hours), max(enter_hours)],
            "avg_duration_s": round(sum(durations) / len(durations), 1),
        }

    # —— 当前到访 + 偏差 ——
    ce = getattr(ri, "current_event", None)
    current: dict[str, Any] | None = None
    deviation: list[str] = []
    if ce is not None:
        occ = getattr(ce, "occurred_at", None)
        cur_hour = None
        if occ is not None:
            try:
                cur_hour = int(occ.hour)
            except Exception:  # noqa: BLE001
                cur_hour = None
        current = {"hour": cur_hour}
        if baseline and cur_hour is not None:
            lo, hi = baseline["enter_hours"]
            if cur_hour < lo or cur_hour > hi:
                deviation.append(
                    f"当前到访 {cur_hour:02d}:00 偏离典型时段 {lo:02d}:00–{hi:02d}:00"
                )

    # —— 记忆证据（source_refs 锚点 + 历史 source_event_ids 去重）——
    evidence: list[str] = []
    if rr is not None:
        for sr in getattr(rr, "source_refs", ()) or ():
            if getattr(sr, "source", None) == "historical_context":
                ref = getattr(sr, "ref", None)
                if ref and ref not in evidence:
                    evidence.append(ref)
    seen = set(evidence)
    for sid in source_event_ids:
        if sid not in seen:
            seen.add(sid)
            evidence.append(sid)
    evidence = evidence[:8]

    # —— 记忆增量叙事（ReasoningResult）——
    with_memory: dict[str, Any] | None = None
    hint = None
    if rr is not None:
        with_memory = {
            "findings": list(getattr(rr, "findings", ()) or ()),
            "explanation": getattr(rr, "explanation", "") or "",
        }
        hint = getattr(rr, "suggested_action_hint", None)

    vid = getattr(ce, "visitor_instance_id", "") if ce is not None else ""
    return {
        "visitor_instance_id": vid,
        "memory_status": memory_status,
        "n_episodes": n_episodes,
        "known_patterns": known_patterns,
        "baseline": baseline,
        "current": current,
        "deviation": deviation,
        "evidence": evidence,
        "with_memory": with_memory,
        "suggested_action_hint": hint,
    }
