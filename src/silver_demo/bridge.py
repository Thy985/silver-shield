"""帧编码桥接（ADR-0015 §2.2 / §2.4）。

本模块是「冻结契约」与「展示层」之间的**唯一编码点**：

    FrameResult（冻结对象）
        │
        └── frame(np.ndarray) ──► JPEG encode ──► base64 ──► Case Viewer 媒体区（帧缩图）

ADR-0036 Phase 3 收敛后，第二套事实模型（``frame_result_to_view`` /
``collect_active_warnings`` / ``route_commands`` / ``build_memory_profiles``）
已移除：统一事实源为 ``FrameResult → Live Adapter(ProjectionAccumulator) →
EvidenceProjection → Case Viewer``，不再有 ``view`` / ``state`` / ``meta`` 第二套
事实模型。本模块仅保留帧编码辅助函数。

严格规则（ADR-0015 §5 冻结合规）：
- ``frame`` 是 ``np.ndarray``（BGR），经 OpenCV JPEG 编码 → base64 字符串。
- 不引入业务判定逻辑（本模块不做风险解释，只做格式转换）。
"""

from __future__ import annotations

import base64
from typing import Any


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


def encode_frame_to_jpeg_bytes(
    frame: Any,
    quality: int = 50,
    max_width: int | None = None,
) -> bytes | None:
    """把 BGR np.ndarray 编码为 JPEG 字节流（用于 MJPEG streaming）。

    Args:
        frame: ``np.ndarray`` BGR。None 或编码失败返回 None。
        quality: JPEG 质量 1-100。
        max_width: 编码前将帧宽度缩放到此值（保持比例）。

    Returns:
        JPEG 字节流，或 None。

    用途：MJPEG over HTTP (multipart/x-mixed-replace)，浏览器原生解码，
    避免 Base64 开销与前端重复解码，显著降低 CPU/延迟。
    """
    if frame is None:
        return None
    try:
        import cv2
    except ImportError:
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
        return buf.tobytes()
    except Exception:  # noqa: BLE001
        return None
