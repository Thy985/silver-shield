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
from typing import Any, Dict, List, Optional


def encode_frame_to_base64_jpeg(
    frame: Any,
    quality: int = 50,
) -> Optional[str]:
    """把 BGR np.ndarray 编码为 base64 JPEG 字符串。

    Args:
        frame: ``process_frame`` 接收的同款帧对象（np.ndarray BGR）。None 或编码失败返回 None。
        quality: JPEG 质量 1-100（Demo 50 足够，降带宽）。

    Returns:
        base64 编码的 JPEG 字符串（无 data: 前缀），或 None。

    边界：仅用 cv2 做编码，不触碰任何 home_perception 组件。
    """
    if frame is None:
        return None
    try:
        import cv2
    except ImportError:  # pragma: no cover - 依赖缺失
        return None
    try:
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        return base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception:  # 编码失败不崩溃网关（AGENTS.md §2.5 可恢复）
        return None


def frame_result_to_view(
    frame_result: Any,
    frame_index: int,
    frame_base64: Optional[str],
    demo_time: Optional[str] = None,
) -> Dict[str, Any]:
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
        }
        ```

    冻结合规：对 warnings/commands/perception_events 只调 ``to_dict()``，
    不调构造器、不改字段。若对象无 to_dict 则降级为空 dict（防御性，不崩溃）。
    """
    def _safe_to_dict(obj: Any) -> Dict[str, Any]:
        """调 obj.to_dict()，失败返回空 dict（不崩溃网关）。"""
        try:
            d = obj.to_dict()
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    perception_events = [_safe_to_dict(p) for p in getattr(frame_result, "perception_events", [])]
    warnings = [_safe_to_dict(w) for w in getattr(frame_result, "warnings", [])]
    commands = [_safe_to_dict(c) for c in getattr(frame_result, "commands", [])]

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

    return {
        "frame_index": frame_index,
        "demo_time": demo_time,
        "frame_base64": frame_base64,
        "n_detections": getattr(frame_result, "n_detections", 0),
        "n_visitor_events": getattr(frame_result, "n_visitor_events", 0),
        "perception_events": perception_events,
        "warnings": warnings,
        "commands": commands,
    }


def collect_active_warnings(warnings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 view-model 的 warnings 列表中筛出"待处理"的（status != RESOLVED/REJECTED）。

    供 AI 风险中心"风险解释卡片"展示当前活跃风险（P0-11.3 消费）。
    只做 dict 过滤，不触碰冻结对象。

    防御：非 dict 元素（None / 其他类型）直接跳过，不崩溃（公开函数，可能被直接调用）。
    """
    return [w for w in warnings if isinstance(w, dict) and w.get("status") not in ("RESOLVED", "REJECTED")]


def route_commands(commands: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
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
    family: List[Dict[str, Any]] = []
    community: List[Dict[str, Any]] = []
    log_only: List[Dict[str, Any]] = []
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
