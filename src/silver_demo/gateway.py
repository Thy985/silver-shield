"""FastAPI 网关 — P0-11.1 核心（ADR-0015 §2.3 / §2.4 / §3）。

职责（仅消费冻结契约，零改 home_perception）：
1. 经 ``PerceptionPipeline.from_settings(settings, ...)`` 装配流水线 + ``load_detector()`` 懒加载 YOLO。
2. 经 ``build_frame_source(scenario, hp_settings)`` 构建帧源（CAVIAR jpg 或 真实 MP4，P0-11.3 可替换）。
3. 后台帧循环：``DemoClock.tick()`` → ``process_frame(frame, i)`` → bridge 翻译 → WS 广播。
4. WebSocket 端点：下行推 frame view-model + state 快照；上行接 action 写 DemoStateStore。
5. StaticFiles 托管 ``dashboard/``（P0-11.2 实现完整 5 区域 HTML Dashboard）。

冻结合规白名单（ADR-0015 §2.1，只 import 以下符号）：
- ``PerceptionPipeline`` / ``DemoClock`` / ``FrameResult`` ← ``home_perception.runtime.pipeline``
- ``read_caviar_frames`` ← ``home_perception.runtime.config``（经 ``.sources`` 间接消费）
- ``Settings`` ← ``home_perception.core.config``
- ``WarningEvent`` / ``ActionCommand`` 仅在类型标注中引用（运行期不调构造器）

严禁 import：``rule_engine`` / ``decision_engine`` / ``action.executor`` / ``action.dispatcher`` 等 7 层内部；
亦严禁 import 冻结包内的帧源实现模块（FrameSource 所在子模块）；``.sources`` 以结构一致的本地抽象自提供帧源。
``tests/demo/test_freeze_boundary.py`` 守此边界。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import structlog
from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# === 冻结契约白名单 import（仅以下 home_perception 符号） ===
from home_perception.core.config import Settings
from home_perception.runtime.pipeline import DemoClock, FrameResult, PerceptionPipeline

# === 本包内部 ===
from .bridge import (
    collect_active_warnings,
    encode_frame_to_base64_jpeg,
    frame_result_to_view,
    route_commands,
)
from .config import DemoSettings
from .scenarios import ScenarioConfig, load_scenario
from .sources import Source
from .state import DemoAggregateState, DemoStateStore
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
        self.source: Optional[Source] = None
        self.n_frames: int = -1

        # 展示层组件
        self.hub = ConnectionHub()
        self.store = DemoStateStore()
        self.aggregate_state = DemoAggregateState()  # 服务端权威聚合状态（P0-11.3.5）

        # 循环控制
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._frame_index = 0
        self.loop_count = 0  # 循环重放计数（P0-11.3.5 状态面板）

    @classmethod
    def create_for_test(cls) -> "DemoGateway":
        """测试用工厂：构造未装配的网关实例，跳过 YOLO 加载与场景解析。

        避免测试用 ``DemoGateway.__new__`` 绕过 ``__init__`` 后手动补齐一堆属性
        （脆弱、易漏属性导致 ``AttributeError``）。直接 ``cls(None, None, None)``
        即获得 ``__init__`` 赋予的全部默认属性（hub / store / aggregate_state 等），
        测试再按需 monkeypatch 隔离 I/O 副作用。
        """
        return cls(None, None, None)

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

        # 构建帧源（P0-11.3：CAVIAR jpg 或 真实 MP4，按 scenario.source_type 分发）
        self.source = Source()
        self.source.load(self.scenario, self.hp_settings)
        self.n_frames = self.source.frame_count

        # 聚合状态会话信息（P0-11.3.5：供状态面板 / snapshot）
        self.aggregate_state.started_at = time.time()
        self.aggregate_state.scenario = self.scenario.scenario_id
        self.aggregate_state.source = self.scenario.source
        self.aggregate_state.source_type = self.scenario.source_type
        self.aggregate_state.n_frames = self.n_frames

        # 场景级规则阈值覆盖（P0-11.5a：CCTV 夜间场景降 repeat_visit_count 以稳定产出 HIGH）
        self._apply_scenario_rule_overrides()

        # 帧源可用性校验（CAVIAR / video_file 共用，见 _validate_frame_source）
        self._validate_frame_source(self.scenario)

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
        # 帧循环间隔：DemoSettings.frame_loop_interval_s 显式设置优先；
        # 若为 0（不限速），回退到 scenario.fps_target（1.0 / fps）。
        interval = self.demo_settings.frame_loop_interval_s
        if interval <= 0 and self.scenario.fps_target > 0:
            interval = 1.0 / self.scenario.fps_target

        # 迭代帧源抽象（P0-11.3：CAVIAR jpg 或 真实 MP4，均产出 (timestamp, frame) 流）
        frame_iter = iter(self.source)
        while self._running:
            try:
                _, frame = next(frame_iter)
            except StopIteration:
                # 帧源耗尽：loop=True 时重放（重新迭代，MP4 重新打开文件 / CAVIAR 回到第 0 帧）。
                # 关键：重建流水线状态组件（复用已加载 YOLO detector，免重载权重）以清空跨循环累积的
                # 追踪/窗口/决策状态——否则多循环后状态饱和，warning 不再产生（演示区 ②③④ 变空白）。
                if self.scenario.loop:
                    self.loop_count += 1
                    self._rebuild_pipeline(self.scenario)
                    self._frame_index = 0
                    frame_iter = iter(self.source)
                    continue
                break

            # 推进模拟时间（在网关内，不在 pipeline.run 内；process_frame 不推进 clock）
            self.clock.tick(self.scenario.frame_interval_s)

            # 消费冻结契约：process_frame（唯一出口）
            # 注意：frame_index 单调递增、loop 重放时**不回绕**（与冻结 read_caviar_frames 的 i % n 不同）。
            # 长 loop 下 frame_index 会超过 n_frames——Dashboard 不要用它作取模/进度条边界，
            # 展示进度请用 clock.now()（demo_time）或 self.n_frames。
            result: FrameResult = self.pipeline.process_frame(frame, frame_index=self._frame_index)

            # bridge 翻译（只读 to_dict + base64）
            frame_b64 = encode_frame_to_base64_jpeg(frame, quality=self.demo_settings.jpeg_quality)
            demo_time = self.clock.now().isoformat()
            view = frame_result_to_view(
                result, frame_index=self._frame_index, frame_base64=frame_b64, demo_time=demo_time
            )

            # 广播（frame view + state 快照 + 衍生的三端聚合视图）
            # active_warnings / routed_commands 由 bridge 消费 view-model 产出（P0-11.2 区域 3/4 直接渲染），
            # 此处调用即"消费" collect_active_warnings / route_commands（消除孤儿代码），
            # 且避免在展示层 JS 里重复实现路由/过滤逻辑（守住 ADR-0015 §5 冻结边界）。
            active_warnings = collect_active_warnings(view["warnings"])
            routed_commands = route_commands(view["commands"])
            # 服务端权威聚合状态（P0-11.3.5）：每帧累积 warning/behavior/command + 运行时元数据
            self.aggregate_state.ingest(
                active_warnings,
                view["perception_events"],
                view["warnings"],
                routed_commands,
                self._frame_index,
                self.loop_count,
            )
            state_snap = await self.store.snapshot()
            await self.hub.broadcast(
                {
                    "type": "frame",
                    "view": view,
                    "state": state_snap,
                    "active_warnings": active_warnings,
                    "routed_commands": routed_commands,
                    "meta": self.aggregate_state.meta(),  # 状态面板 / 晚连恢复
                }
            )

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
            except Exception as exc:  # 资源释放失败也要记日志，避免静默泄漏（AGENTS.md §2.5）
                structlog.get_logger(__name__).warning("pipeline.close 失败", exc_info=exc)
            self.pipeline = None

    def _validate_frame_source(self, scenario: "ScenarioConfig") -> None:
        """校验帧源可用性（assemble 与 switch_source 共用，避免损坏场景静默空循环）。

        - ``caviar_jpg``：n_frames == 0 即 fixture 缺失 / cv2 未装 → 启动即失败
        - ``video_file``：文件不存在 → 启动即失败；n_frames == 0（编码不支持 / 时长为 0）→ 启动即失败
        """
        if scenario.source_type == "caviar_jpg" and self.n_frames == 0:
            raise RuntimeError(
                f"CAVIAR 场景 {scenario.source!r} 无可用帧（base_dir="
                f"{self.hp_settings.runtime.caviar_base_dir!r}，fixture 缺失或 cv2 未装）"
            )
        if scenario.source_type == "video_file":
            mp = scenario.media_path
            if not mp or not Path(mp).is_file():
                raise RuntimeError(f"video_file 源文件缺失: {mp!r}")
            if self.n_frames == 0:
                raise RuntimeError(f"视频无可用帧: {mp!r}（可能编码不支持或时长为 0）")

    def _apply_scenario_rule_overrides(self) -> None:
        """场景级规则阈值覆盖（P0-11.5a）。

        从 ``scenario.rule_overrides`` 把阈值键覆盖进 ``pipeline.rule_engine.thresholds``，
        使单个场景可微调规则（如 CCTV 夜间场景降 ``repeat_visit_count`` 以稳定产出 HIGH），
        不影响全局默认与其他场景。仅在键存在于 ThresholdConfig 时生效；未知键告警跳过。

        注意：CooldownGate 在 RuleEngine.__init__ 时已按当时阈值构造，
        故本覆盖应在重建流水线（assemble / _rebuild_pipeline）之后调用，
        且仅用于运行期阈值（如 repeat_visit_count），不用于 cooldown 类参数。
        """
        overrides = getattr(self.scenario, "rule_overrides", None)
        if not overrides:
            return
        th = self.pipeline.rule_engine.thresholds
        for k, v in overrides.items():
            if hasattr(th, k):
                setattr(th, k, v)
            else:
                structlog.get_logger(__name__).warning(
                    "scenario.rule_overrides.unknown_key", key=k, scenario=self.scenario.scenario_id,
                )

    # ------------------------------------------------------------------
    # 输入源热切换（P0-11.4 视频输入适配层）
    # ------------------------------------------------------------------

    async def switch_source(self, scenario: "ScenarioConfig") -> None:
        """热切换输入源（P0-11.4 视频输入适配）：停旧循环 → 重建帧源/时钟 → 清空跨帧状态 → 重开循环。

        不重建 pipeline（昂贵的 YOLO 加载只做一次），严格复用已装配的 ``pipeline`` / ``hp_settings``。
        切换后清空 ``DemoStateStore``（新视频 = 新会话），并广播 ``source_switched``，
        前端据此清空跨帧累积（warningMap / behaviorEvents / commandMap 等），避免旧视频数据串场。
        """
        # 1. 停旧循环
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # 非取消异常（如取消中 pipeline.close 抛错）需记录，避免静默丢失
                structlog.get_logger(__name__).warning("帧循环任务取消时发生异常", exc_info=exc)
            self._task = None

        # 2. 重建帧源 + 流水线状态（复用已加载 detector，清空跨场景/跨循环累积状态）
        self.scenario = scenario
        self._rebuild_pipeline(scenario)
        self.source = Source()
        self.source.load(scenario, self.hp_settings)
        self.n_frames = self.source.frame_count

        # 帧源可用性校验（CAVIAR / video_file 共用，含 caviar_jpg 无帧保护）
        self._validate_frame_source(scenario)

        self._frame_index = 0
        self.loop_count = 0
        self.store = DemoStateStore()  # 新会话：清空历史闭环状态
        # 服务端聚合状态清空（解决「切换视频源状态残留」服务端侧）；重置会话计时
        self.aggregate_state.clear(reset_session=True)
        self.aggregate_state.scenario = scenario.scenario_id
        self.aggregate_state.source = scenario.source
        self.aggregate_state.source_type = scenario.source_type
        self.aggregate_state.n_frames = self.n_frames

        # 3. 重开循环
        self._task = asyncio.create_task(self.run_loop())

        # 4. 广播切换事件（前端清空跨帧累积状态）
        await self.hub.broadcast({
            "type": "source_switched",
            "scenario": scenario.scenario_id,
            "source": scenario.source,
            "source_type": scenario.source_type,
            "frames": self.n_frames,
        })


    def _rebuild_pipeline(self, scenario: "ScenarioConfig") -> None:
        """重建流水线状态组件（复用已加载的 YOLO detector，避免重载权重）。

        清空跨循环/跨场景累积的追踪（VisitorTracker）/ 时间窗口（FeatureExtractor）/
        规则计数（RuleEngine）/ 决策状态（DecisionEngine）等，使每次分析都从干净状态开始，
        保证循环重放 / 切换场景后风险能重新触发（否则演示区 ②③④ 在多轮循环后变空白）。
        仅重建组件，detector 实例复用（model.track(persist=True) 要求同一实例保证 track_id 一致）。
        """
        self.clock = DemoClock(start=scenario.start_time, interval_s=scenario.frame_interval_s)
        self.pipeline = PerceptionPipeline.from_settings(
            self.hp_settings,
            detector=self.pipeline.detector,
            device_id=scenario.source,
            now_provider=self.clock,
            frame_interval_s=scenario.frame_interval_s,
        )
        # 场景级规则阈值覆盖（与 assemble 一致；每次重建流水线后重应用）
        self._apply_scenario_rule_overrides()

    # ======================================================================
    # FastAPI app 工厂
    # ======================================================================

def _resolve_inference_device(hp_settings: "Settings") -> str:
    """解析推理设备：环境变量 > CUDA 可用性 > 配置默认值。

    - ``SILVER_DEMO_DEVICE`` 非空时直接采用（``cpu`` / ``cuda:0`` / ``cuda:1``…），便于强制覆盖。
    - 未设置且 ``torch.cuda.is_available()`` 为真 → ``cuda:0``：本机有 GPU，
      把长视频首卡延迟从 CPU 的 ~0.5s/帧降到 ~30ms/帧，直接消减
      「CCTV / 上传视频前段空窗、风险卡迟迟不出现」的观感问题。
    - 否则保持 ``hp_settings.detection.device`` 原值（通常 ``cpu``）。

    注意：延迟 ``import torch``，保持网关模块在无需判定设备时仍 torch-free。
    """
    env = os.environ.get("SILVER_DEMO_DEVICE", "").strip()
    if env:
        return env
    try:
        import torch  # 延迟导入：仅在真正需要判定设备时才引入 torch

        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            return "cuda:0"
    except Exception:
        pass
    return getattr(getattr(hp_settings, "detection", None), "device", "cpu") or "cpu"


def create_app(
    demo_settings: Optional[DemoSettings] = None,
) -> FastAPI:
    """构造 FastAPI app（ADR-0015 §3）。

    Args:
        demo_settings: Demo 网关配置；None 则从环境变量构造。

    Returns:
        FastAPI app，已注册：
        - ``GET /`` → Dashboard index.html（P0-11.2 完整 5 区域）
        - ``GET /health`` → 健康检查
        - ``WS {ws_path}`` → WebSocket 端点（帧下行 + action 上行）
        - ``StaticFiles`` → dashboard/ 静态资源
    """
    demo_settings = demo_settings or DemoSettings.from_env()
    hp_settings = Settings.load(demo_settings.home_perception_config)

    # 检测器设备选择（P0-11 后续 · 消减长视频前段空窗观感）：CUDA 可用时上 GPU。
    # 复用同一 hp_settings 对象，使 assemble / _rebuild_pipeline 自动继承设备
    # （不复制构造、不改冻结包；运行时首位消费者始终是 create_app 这条路径）。
    desired = _resolve_inference_device(hp_settings)
    cur = getattr(getattr(hp_settings, "detection", None), "device", None)
    if cur != desired:
        try:
            hp_settings.detection.device = desired
        except Exception:
            # 冻结 / 不可变配置：复制后再改，保证 detector 继承设备选择
            hp_settings = hp_settings.model_copy(deep=True)
            hp_settings.detection.device = desired

    scenario = load_scenario(demo_settings.scenario_path)

    gateway = DemoGateway(demo_settings, hp_settings, scenario)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动：确保上传目录存在 + 装配 + 起帧循环后台 task
        Path(demo_settings.upload_dir).mkdir(parents=True, exist_ok=True)
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
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # 非取消异常需记录，避免静默丢失
                    structlog.get_logger(__name__).warning("帧循环任务取消时发生异常", exc_info=exc)
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
        """Dashboard 入口（P0-11.2 完整 5 区域 HTML）。"""
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
            "scenario": gateway.scenario.scenario_id,
            "source": gateway.scenario.source,
            "source_type": gateway.scenario.source_type,
            "n_frames": gateway.n_frames,
            "frame_index": gateway._frame_index,
            "active_connections": len(gateway.hub.active),
        }

    @app.websocket(demo_settings.ws_path)
    async def websocket_endpoint(ws: WebSocket) -> None:
        """WebSocket 端点：下行 frame view + state；上行 action 写 store。"""
        await gateway.hub.connect(ws)
        # 首连 snapshot：把服务端权威聚合状态推给新连接（晚连也能看到历史）
        await gateway.hub.send_to(ws, {
            "type": "snapshot",
            **gateway.aggregate_state.snapshot(),
            "meta": gateway.aggregate_state.meta(),
        })
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

    # ------------------------------------------------------------------
    # P0-11.4 视频输入适配层：场景输入 / 视频源接入
    # ------------------------------------------------------------------

    @app.post("/demo/upload")
    async def upload_video(file: UploadFile = File(...)) -> Dict[str, Any]:
        """P0-11.4 视频输入适配：接收本地视频 → 落盘 → 热切换 VideoFileFrameSource → 重开帧循环。

        视频只是"传感器"：经冻结 Pipeline 产出 身份→轨迹→行为→风险→解释→干预 全链，
        不调用任何视觉大模型 API（工程闭环验证，非模型性能评测）。
        """
        allowed = {".mp4", ".mpg", ".mpeg", ".avi", ".mov", ".mkv", ".webm"}
        fname = file.filename or ""
        ext = Path(fname).suffix.lower()
        if ext not in allowed:
            return JSONResponse(
                status_code=400,
                content={"error": f"不支持的视频格式 {ext or '(无扩展名)'}，仅允许 {sorted(allowed)}"},
            )

        upload_dir = Path(demo_settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{uuid.uuid4().hex}{ext}"
        dest = upload_dir / safe_name

        # 流式写入，避免大文件占满内存；同时累计字节数，超 max_upload_mb 则 413 拒绝并清理
        max_bytes = int(demo_settings.max_upload_mb * 1024 * 1024)
        written = 0
        try:
            with dest.open("wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        out.close()
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                        return JSONResponse(
                            status_code=413,
                            content={
                                "error": (
                                    f"视频过大（{written / 1024 / 1024:.1f} MB），"
                                    f"上限 {demo_settings.max_upload_mb:.0f} MB"
                                )
                            },
                        )
                    out.write(chunk)
        except Exception as exc:
            try:
                dest.unlink()
            except OSError:
                pass
            return JSONResponse(status_code=500, content={"error": f"文件写入失败：{exc}"})

        # 构造 video_file 场景（复用当前 scenario 的播放参数，保证观感一致）
        base = gateway.scenario
        new_scenario = ScenarioConfig(
            scenario_id="uploaded_video",
            source=dest.stem,
            source_type="video_file",
            media_path=str(dest),
            start_time=datetime.now(timezone.utc),
            frame_interval_s=base.frame_interval_s,
            fps_target=base.fps_target,
            loop=True,
            description=f"用户上传：{fname}",
        )
        try:
            await gateway.switch_source(new_scenario)
        except Exception as exc:
            try:
                dest.unlink()
            except OSError:
                pass
            return JSONResponse(status_code=422, content={"error": f"视频接入失败：{exc}"})

        return {
            "status": "ok",
            "source_id": safe_name,
            "filename": fname,
            "frames": gateway.n_frames,
            "message": "已开始分析，Dashboard 实时刷新（身份→轨迹→行为→风险→干预）",
        }

    @app.post("/demo/scenario")
    async def switch_scenario(req: Request) -> Dict[str, Any]:
        """P0-11.4 场景输入：按 scenario_id（或路径）热切换到预置场景（模拟场景 / CAVIAR 工程验证）。"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        scn_id = (body.get("scenario_id") or "").strip()
        if not scn_id:
            return JSONResponse(status_code=400, content={"error": "缺少 scenario_id"})
        # 仅允许 scenarios_dir 内的预置场景；解析后校验归属，杜绝 ../ 路径穿越（评审 #4）
        scenarios_root = Path(demo_settings.scenarios_dir).resolve()
        candidate = (scenarios_root / f"{scn_id}.yaml").resolve()
        if not candidate.is_relative_to(scenarios_root) or not candidate.is_file():
            return JSONResponse(
                status_code=404,
                content={"error": f"场景不存在: {scn_id}（应在 {demo_settings.scenarios_dir}/ 下）"},
            )
        found = candidate
        try:
            sc = load_scenario(found)
            await gateway.switch_source(sc)
        except Exception as exc:
            return JSONResponse(status_code=422, content={"error": f"场景切换失败：{exc}"})
        return {
            "status": "ok",
            "scenario": sc.scenario_id,
            "source": sc.source,
            "source_type": sc.source_type,
            "frames": gateway.n_frames,
        }

    @app.post("/demo/reset")
    async def reset_demo() -> Dict[str, Any]:
        """P0-11.3.5 Reset 生命周期：清空 pipeline / 状态 / 聚合，恢复干净会话。

        复用 ``switch_source(同场景)``：停旧循环 → 重建流水线（复用已加载 YOLO detector）
        → 清空闭环 store + 服务端聚合状态 → 重置帧索引 / 循环计数 / 会话计时 → 重开循环。
        广播 ``source_switched``（前端据此 ``resetSession()`` 清空跨帧累积）。
        比赛换组场景：点 Reset → ≤30s 内恢复干净状态可重跑。
        """
        try:
            await gateway.switch_source(gateway.scenario)
        except Exception as exc:  # 重置中 pipeline 重建失败
            return JSONResponse(status_code=422, content={"error": f"重置失败：{exc}"})
        return {
            "status": "ok",
            "frame_index": 0,
            "session_status": gateway.aggregate_state.session_status,
            "loop_count": 0,
        }

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
