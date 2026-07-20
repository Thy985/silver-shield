"""FastAPI 网关 — P0-11.1 核心（ADR-0015 §2.3 / §2.4 / §3）。

职责（仅消费冻结契约，零改 home_perception）：
1. 经 ``PerceptionPipeline.from_settings(settings, ...)`` 装配流水线 + ``load_detector()`` 懒加载 YOLO。
2. 经 ``read_caviar_frames(base_dir, scenario, glob)`` 读取 fixture 帧（demo 真实路径）。
3. 后台帧循环：``DemoClock.tick()`` → ``process_frame(frame, i)`` → bridge 翻译 → WS 广播。
4. WebSocket 端点：下行推 frame view-model + state 快照；上行接 action 写 DemoStateStore。
5. StaticFiles 托管 ``dashboard/``（P0-11.2 实现完整 HTML；本阶段占位）。

冻结合规白名单（ADR-0015 §2.1，只 import 以下符号）：
- ``PerceptionPipeline`` / ``DemoClock`` / ``FrameResult`` ← ``home_perception.runtime.pipeline``
- ``read_caviar_frames`` ← ``home_perception.runtime.config``
- ``Settings`` ← ``home_perception.core.config``
- ``WarningEvent`` / ``ActionCommand`` 仅在类型标注中引用（运行期不调构造器）

严禁 import：``rule_engine`` / ``decision_engine`` / ``action.executor`` / ``action.dispatcher`` 等 7 层内部。
``tests/demo/test_freeze_boundary.py`` 守此边界。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# === 冻结契约白名单 import（仅以下 home_perception 符号） ===
from home_perception.core.config import Settings
from home_perception.runtime.config import read_caviar_frames
from home_perception.runtime.pipeline import DemoClock, FrameResult, PerceptionPipeline

# === 本包内部 ===
from .bridge import encode_frame_to_base64_jpeg, frame_result_to_view
from .config import DemoSettings
from .scenarios import ScenarioConfig, load_scenario
from .state import DemoStateStore
from .ws import ConnectionHub, handle_upstream

# 类型标注用（运行期不调构造器；仅用于 type hint 让代码可读）
from home_perception.action.command import ActionCommand  # noqa: F401  # 类型标注
from home_perception.analysis.warning import WarningEvent  # noqa: F401  # 类型标注


class DemoGateway:
    """Demo 网关核心：持有 pipeline + clock + frames + hub + store，驱动帧循环。

    生命周期：
        ``__init__`` → ``assemble()``（装配 pipeline + 读帧）→ ``run_loop()``（帧循环）→ ``close()``
    """

    def __init__(
        self,
        demo_settings: DemoSettings,
        hp_settings: Settings,
        scenario: ScenarioConfig,
    ) -> None:
        self.demo_settings = demo_settings
        self.hp_settings = hp_settings
        self.scenario = scenario

        # 冻结对象（装配后填充）
        self.pipeline: Optional[PerceptionPipeline] = None
        self.clock: Optional[DemoClock] = None
        self.frames: List[Any] = []

        # 展示层组件
        self.hub = ConnectionHub()
        self.store = DemoStateStore()

        # 循环控制
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._frame_index = 0

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------

    def assemble(self) -> None:
        """装配 pipeline + DemoClock + 读取 fixture 帧。

        严格经 ``PerceptionPipeline.from_settings`` 装配（不自行构造 RuleEngine 等），
        然后调 ``pipeline.load_detector()`` 懒加载 YOLO（ADR-0015 §2.3）。
        """
        self.clock = DemoClock(
            start=self.scenario.start_time,
            interval_s=self.scenario.frame_interval_s,
        )
        # device_id 用场景 source 名（与 runtime/lifecycle.run_demo 一致）
        self.pipeline = PerceptionPipeline.from_settings(
            self.hp_settings,
            device_id=self.scenario.source,
            now_provider=self.clock,
            frame_interval_s=self.scenario.frame_interval_s,
        )
        # 懒加载 YOLO 权重（构造期不触发 torch 导入）
        self.pipeline.load_detector()

        # 读取 CAVIAR fixture 帧（demo 真实路径：jpg 目录，非 CaviarFrameSource 视频流）
        self.frames = read_caviar_frames(
            self.hp_settings.runtime.caviar_base_dir,
            self.scenario.source,
            self.hp_settings.runtime.frame_glob,
        )
        if not self.frames:
            raise RuntimeError(
                f"场景 {self.scenario.source!r} 无可用帧（base_dir="
                f"{self.hp_settings.runtime.caviar_base_dir!r}，fixture 缺失或 cv2 未装）"
            )

    # ------------------------------------------------------------------
    # 帧循环（后台 task）
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        """后台帧循环：逐帧 process_frame → bridge → WS 广播。

        - 循环到 ``self._running`` 为 False。
        - ``scenario.loop=True`` 时帧列表耗尽后回到第 0 帧。
        - 每帧后 ``DemoClock.tick()`` 推进模拟时间（在网关内，不在 pipeline 内）。
        - ``demo_settings.frame_loop_interval_s > 0`` 时 sleep 限速（模拟实时观感）。
        """
        if self.pipeline is None or self.clock is None:
            raise RuntimeError("gateway 未装配，请先调 assemble()")

        self._running = True
        n = len(self.frames)
        interval = self.demo_settings.frame_loop_interval_s

        while self._running:
            i = self._frame_index % n  # loop 或单次
            frame = self.frames[i]

            # 推进模拟时间（在网关内，不在 pipeline.run 内；process_frame 不推进 clock）
            self.clock.tick(self.scenario.frame_interval_s)

            # 消费冻结契约：process_frame（唯一出口）
            result: FrameResult = self.pipeline.process_frame(frame, frame_index=i)

            # bridge 翻译（只读 to_dict + base64）
            frame_b64 = encode_frame_to_base64_jpeg(frame, quality=self.demo_settings.jpeg_quality)
            demo_time = self.clock.now().isoformat()
            view = frame_result_to_view(result, frame_index=i, frame_base64=frame_b64, demo_time=demo_time)

            # 广播（frame view + state 快照）
            state_snap = await self.store.snapshot()
            await self.hub.broadcast({"type": "frame", "view": view, "state": state_snap})

            self._frame_index += 1
            if interval > 0:
                await asyncio.sleep(interval)
            else:
                # 让出事件循环，避免阻塞 WS 上行
                await asyncio.sleep(0)

    def stop(self) -> None:
        """停止帧循环。"""
        self._running = False

    def close(self) -> None:
        """释放 pipeline 资源。"""
        if self.pipeline is not None:
            try:
                self.pipeline.close()
            except Exception:
                pass
            self.pipeline = None


# ======================================================================
# FastAPI app 工厂
# ======================================================================

def create_app(
    demo_settings: Optional[DemoSettings] = None,
) -> FastAPI:
    """构造 FastAPI app（ADR-0015 §3）。

    Args:
        demo_settings: Demo 网关配置；None 则从环境变量构造。

    Returns:
        FastAPI app，已注册：
        - ``GET /`` → Dashboard index.html（P0-11.2 完整 5 区域；本阶段占位）
        - ``GET /health`` → 健康检查
        - ``WS {ws_path}`` → WebSocket 端点（帧下行 + action 上行）
        - ``StaticFiles`` → dashboard/ 静态资源
    """
    demo_settings = demo_settings or DemoSettings.from_env()
    hp_settings = Settings.load(demo_settings.home_perception_config)
    scenario = load_scenario(demo_settings.scenario_path)

    gateway = DemoGateway(demo_settings, hp_settings, scenario)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动：装配 + 起帧循环后台 task
        gateway.assemble()
        gateway._task = asyncio.create_task(gateway.run_loop())
        try:
            yield
        finally:
            gateway.stop()
            if gateway._task is not None:
                gateway._task.cancel()
                try:
                    await gateway._task
                except (asyncio.CancelledError, Exception):
                    pass
            gateway.close()

    app = FastAPI(
        title="SilverShield Demo Gateway",
        version="0.1.0",
        description="P0-11 三端风险闭环展示层网关（ADR-0015）",
        lifespan=lifespan,
    )

    # 静态资源（dashboard/）
    dash_dir = Path(demo_settings.dashboard_dir)
    if dash_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(dash_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """Dashboard 入口（P0-11.2 实现完整 5 区域；本阶段占位）。"""
        index_file = dash_dir / "index.html"
        if index_file.is_file():
            return HTMLResponse(index_file.read_text(encoding="utf-8"))
        return HTMLResponse(
            "<h1>SilverShield Demo Gateway</h1>"
            "<p>P0-11.1 网关已启动。Dashboard HTML 将在 P0-11.2 落地。</p>"
            "<p>WebSocket 端点：{}</p>".format(demo_settings.ws_path),
            status_code=200,
        )

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "scenario": scenario.scenario_id,
            "source": scenario.source,
            "n_frames": len(gateway.frames),
            "frame_index": gateway._frame_index,
            "active_connections": len(gateway.hub.active),
        }

    @app.websocket(demo_settings.ws_path)
    async def websocket_endpoint(ws: WebSocket) -> None:
        """WebSocket 端点：下行 frame view + state；上行 action 写 store。"""
        await gateway.hub.connect(ws)
        try:
            while True:
                raw = await ws.receive_text()
                ack = await handle_upstream(ws, raw, gateway.store)
                if ack is not None:
                    await ws.send_text(json.dumps(ack, ensure_ascii=False))
                    # action 处理后广播最新 state 快照给所有连接
                    state_snap = await gateway.store.snapshot()
                    await gateway.hub.broadcast({"type": "state_update", "state": state_snap})
        except WebSocketDisconnect:
            pass
        finally:
            await gateway.hub.disconnect(ws)

    # 把 gateway 挂到 app.state，便于测试 / 调试访问
    app.state.gateway = gateway
    return app


# 模块级 app（uvicorn silver_demo.gateway:app 直接启动）
app: Optional[FastAPI] = None


def _ensure_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app


def main() -> None:
    """命令行入口：python -m silver_demo.gateway 或 uvicorn silver_demo.gateway:app。"""
    import uvicorn

    demo_settings = DemoSettings.from_env()
    a = create_app(demo_settings)
    uvicorn.run(a, host=demo_settings.host, port=demo_settings.port, log_level="info")


if __name__ == "__main__":
    main()
