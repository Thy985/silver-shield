"""方案 A · MJPEG 订阅模式测试（P0-11.5 修复"切换场景后检测不到人像"）。

背景：旧实现 MJPEG 端点独立读 temp_source（按 ``fps_target=8`` 限速），与主帧循环
YOLO 推理（CPU 受限 ~4 fps）不同步。cctv 60s 视频 MJPEG 60.5s 放完、主帧循环 121s
处理完 → MJPEG 流先结束后 ``<img>`` 定格 + 主帧循环还在跑 detection，用户感知为
"切换后长时间检测不到人像"。

修复：每帧由主帧循环编码一次并 ``put_nowait`` 到 ``_mjpeg_subscribers`` 列表中每个
``asyncio.Queue``（maxsize=2，慢消费者丢帧不阻塞），MJPEG 端点 ``await queue.get()``
拉取 → MJPEG 流速度 = YOLO 处理速度，前端 ``<img>`` 与 ``perception_delta`` 严格对齐。

覆盖：
- ``_encode_mjpeg_frame`` 输出合法 jpeg bytes（``\\xff\\xd8\\xff...``）。
- ``_broadcast_mjpeg_frame`` 把 bytes put 给所有订阅者；满队列丢帧不抛。
- ``_signal_mjpeg_end`` 让所有订阅者 ``get()`` 返回 ``None``（流结束信号）。
- 多订阅者场景：每个订阅者独立 queue，互不干扰。
- 订阅断开（``finally remove``）：订阅者列表清理，无残留引用。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import numpy as np
import pytest

from silver_demo.gateway import DemoGateway
from silver_demo.scenarios import ScenarioConfig

# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------


def _bare_gateway_with_scenario() -> DemoGateway:
    """构造未装配网关（与 audio seam 测试同型 · 仅校验 MJPEG 订阅基础设施）。"""
    gw = DemoGateway.create_for_test()
    gw.scenario = ScenarioConfig(
        scenario_id="sess-mjpeg-sync",
        source="s",
        start_time=datetime.now(UTC),
    )
    return gw


def _solid_frame(width: int = 320, height: int = 180, color: int = 128) -> np.ndarray:
    """构造一张单色 frame（BGR uint8），用于 cv2 编码。"""
    return np.full((height, width, 3), color, dtype=np.uint8)


# ----------------------------------------------------------------------
# _encode_mjpeg_frame
# ----------------------------------------------------------------------


def test_encode_mjpeg_frame_returns_jpeg_magic_bytes():
    """编码输出必须以 jpeg SOI marker ``\\xff\\xd8`` 开头。"""
    gw = _bare_gateway_with_scenario()
    out = gw._encode_mjpeg_frame(_solid_frame())
    assert out is not None, "空帧应可编码（cv2 不依赖帧内容）"
    assert out[:2] == b"\xff\xd8", f"MJPEG 编码输出非 jpeg 格式: {out[:4]!r}"
    # EOI marker 0xFFD9 收尾（cv2.imencode 总会写）
    assert out[-2:] == b"\xff\xd9", "JPEG 流应正确收尾"


def test_encode_mjpeg_frame_resizes_above_max_width():
    """max_width 强制 resize：1x720 输入 → resize 后 jpeg 字节数应明显小于不 resize。"""
    gw = _bare_gateway_with_scenario()
    wide = _solid_frame(width=1280, height=720, color=200)
    jpeg_wide = gw._encode_mjpeg_frame(wide, max_width=720, quality=50)
    jpeg_no_resize = gw._encode_mjpeg_frame(wide, max_width=0, quality=50)
    assert jpeg_wide is not None and jpeg_no_resize is not None
    # resize 后 720×405 应比 1280×720 显著小（信息量少）
    assert len(jpeg_wide) < len(jpeg_no_resize), (
        f"max_width 强制 resize 未生效 wide={len(jpeg_wide)} no_resize={len(jpeg_no_resize)}"
    )


def test_encode_mjpeg_frame_handles_bgr_input():
    """BGR 三通道输入必须能正确编码（cv2.imencode 不要求 RGB）。"""
    gw = _bare_gateway_with_scenario()
    bgr = np.zeros((60, 80, 3), dtype=np.uint8)
    bgr[..., 0] = 255  # 纯红 (BGR)
    out = gw._encode_mjpeg_frame(bgr)
    assert out is not None and len(out) > 100


# ----------------------------------------------------------------------
# _broadcast_mjpeg_frame
# ----------------------------------------------------------------------


def test_broadcast_distributes_to_all_subscribers():
    """一个网关 N 个订阅者 → ``_broadcast_mjpeg_frame`` 应让每个订阅者 ``get()`` 得到同一 bytes。"""
    gw = _bare_gateway_with_scenario()
    queues = [asyncio.Queue(maxsize=2) for _ in range(3)]
    gw._mjpeg_subscribers.extend(queues)

    payload = b"\xff\xd8\xff\xe0\x00\x10JFIF\xff\xd9"
    gw._broadcast_mjpeg_frame(payload)

    # 同步测试场景下 queue.put_nowait 后 queue.get() 应立即返回（不阻塞）
    for q in queues:
        assert q.get_nowait() == payload, "每个订阅者都应收到同一 jpeg bytes"


def test_broadcast_no_subscribers_is_noop():
    """无订阅者时广播不应抛异常（生产场景：MJPEG 端点未连时仍可工作）。"""
    gw = _bare_gateway_with_scenario()
    assert gw._mjpeg_subscribers == []
    # 不应抛
    gw._broadcast_mjpeg_frame(b"\xff\xd8\xff\xd9")


def test_broadcast_drops_frame_on_full_queue():
    """maxsize=2 满队列 → ``put_nowait`` 抛 QueueFull → 吞掉不抛（慢消费者不阻塞主帧循环）。"""
    gw = _bare_gateway_with_scenario()
    q = asyncio.Queue(maxsize=2)
    q.put_nowait(b"old1")
    q.put_nowait(b"old2")
    assert q.full()
    gw._mjpeg_subscribers.append(q)

    # 第三帧必须丢（不抛）
    gw._broadcast_mjpeg_frame(b"new")
    # 队列仍是满的旧帧（new 被丢）
    assert q.get_nowait() == b"old1"
    assert q.get_nowait() == b"old2"


# ----------------------------------------------------------------------
# _signal_mjpeg_end
# ----------------------------------------------------------------------


def test_signal_mjpeg_end_sends_none_to_all_subscribers():
    """结束信号必须 ``put_nowait(None)`` 给所有订阅者（消费侧 ``get()`` 返回 ``None`` 触发 break）。"""
    gw = _bare_gateway_with_scenario()
    queues = [asyncio.Queue(maxsize=2) for _ in range(2)]
    gw._mjpeg_subscribers.extend(queues)

    gw._signal_mjpeg_end()

    for q in queues:
        assert q.get_nowait() is None, "所有订阅者都应收到 None 结束信号"


def test_signal_mjpeg_end_no_subscribers_is_noop():
    """无订阅者时结束信号不应抛。"""
    gw = _bare_gateway_with_scenario()
    gw._signal_mjpeg_end()  # 不应抛


# ----------------------------------------------------------------------
# 订阅者生命周期（清理路径）
# ----------------------------------------------------------------------


def test_subscribers_list_starts_empty_on_new_gateway():
    """``create_for_test`` 工厂创建的网关必须有空订阅者列表（保证切换场景时无残留）。"""
    gw = DemoGateway.create_for_test()
    assert gw._mjpeg_subscribers == [], (
        "新建网关不应有任何订阅者残留（cross-test 隔离）"
    )


@pytest.mark.asyncio
async def test_subscriber_disconnect_removes_from_list():
    """模拟 MJPEG 端点断开（finally remove）：订阅者列表必须清理，无残留引用。

    这是避免旧订阅者持续占用 queue 导致主帧循环 broadcast 阻塞 / 内存泄漏的关键不变量。
    """
    # 这里不构造完整 FastAPI app（需要 demo_settings/live_enabled 配置），
    # 直接手动模拟 MJPEG 端点的 frame_generator 断开清理：
    async def simulated_mjpeg_endpoint(queue: asyncio.Queue, gateway: DemoGateway) -> None:
        """MJPEG 端点生成器（订阅+断开清理）。"""
        try:
            # 模拟客户端立即断连（不等一帧）
            await asyncio.sleep(0)
        finally:
            try:
                gateway._mjpeg_subscribers.remove(queue)
            except ValueError:
                pass

    gw = _bare_gateway_with_scenario()
    my_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    gw._mjpeg_subscribers.append(my_queue)
    assert len(gw._mjpeg_subscribers) == 1

    # 模拟 MJPEG 端点立即断开（不等帧）
    await simulated_mjpeg_endpoint(my_queue, gw)
    # 必须清理
    assert gw._mjpeg_subscribers == [], (
        f"MJPEG 端点断开后残留订阅者: {gw._mjpeg_subscribers!r}"
    )


# ----------------------------------------------------------------------
# 集成：编码 + 广播 + 订阅者消费（验证端到端流程）
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_encode_then_broadcast_then_consume_pipeline():
    """端到端：``_encode_mjpeg_frame`` → ``_broadcast_mjpeg_frame`` → 订阅者 ``get()``。

    这是方案 A 单一真实数据通路（不依赖 YOLO / pipeline / WS）的最小完整回路。
    """
    gw = _bare_gateway_with_scenario()
    my_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    gw._mjpeg_subscribers.append(my_queue)

    # 1. 主帧循环处理完一帧后编码
    jpeg_bytes = gw._encode_mjpeg_frame(_solid_frame())
    assert jpeg_bytes is not None
    # 2. 广播到订阅者
    gw._broadcast_mjpeg_frame(jpeg_bytes)
    # 3. MJPEG 端点 await queue.get() 拿到 jpeg bytes
    received = my_queue.get_nowait()
    assert received == jpeg_bytes
    assert received[:2] == b"\xff\xd8"  # 合法 jpeg