"""FastAPI 网关 — P0-11.1 核心（ADR-0015 §2.3 / §2.4 / §3）。

职责（仅消费冻结契约，零改 home_perception）：
1. 经 ``PerceptionPipeline.from_settings(settings, ...)`` 装配流水线 + ``load_detector()`` 懒加载 YOLO。
2. 经 ``build_frame_source(scenario, hp_settings)`` 构建帧源（CAVIAR jpg 或 真实 MP4，P0-11.3 可替换）。
3. 后台帧循环：``DemoClock.tick()`` → ``process_frame(frame, i)`` → Live Adapter 投影（FrameResult → EvidenceProjection）→ WS 心跳。
4. WebSocket 端点：下行推 frame view-model + state 快照；上行接 action 写 DemoStateStore。
5. 双入口（Task 0）：``GET /`` 旗舰 Case Viewer（静态托管 Factory 预渲染 HTML，零渲染 import）；
   ``GET /live`` 收敛到统一 Case Viewer（``render_case_viewer`` 渲染同一 ``EvidenceProjection`` View Model；Host 层反向依赖 Presentation Layer，ADR-0015 §2.1.1）。

冻结合规白名单（ADR-0015 §2.1，只 import 以下符号）：
- ``PerceptionPipeline`` / ``DemoClock`` / ``FrameResult`` ← ``home_perception.runtime.pipeline``
- ``read_caviar_frames`` ← ``home_perception.runtime.config``（经 ``.sources`` 间接消费）
- ``Settings`` ← ``home_perception.core.config``
- ``WarningEvent`` / ``ActionCommand`` 仅在类型标注中引用（运行期不调构造器）

严禁 import：``rule_engine`` / ``decision_engine`` / ``action.executor`` / ``action.dispatcher`` 等 7 层内部；
亦严禁 import 冻结包内的帧源实现模块（FrameSource 所在子模块）；``.sources`` 以结构一致的本地抽象自提供帧源。

**唯一例外（ADR-0015 §2.1.1 分层依赖契约）**：``silver_demo.gateway`` 作为 Host / Composition Root，
允许 import ``home_perception.visualizer.viewer``（含 ``render_case_viewer`` / ``live_adapter`` 的
``ProjectionAccumulator`` / ``build_live_presentation``），用于把 ``FrameResult`` 投影为
``EvidenceProjection`` 并渲染统一 Case Viewer（ADR-0036 Phase 3 收敛 ``GET /live``）；这是
「Host → Presentation Layer」的单向依赖，**不构成** ``viewer → silver_demo``。Runtime Core
（除 gateway 外的子模块）仍禁止 import ``visualizer``。
``tests/demo/test_freeze_boundary.py``（T0-1~T0-5）+ ``tests/visualizer/test_ast_contract.py``（T0-3 / T0-5）守此边界。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

# 类型标注用（运行期不调构造器；仅用于 type hint 让代码可读）
from home_perception.action.command import ActionCommand  # noqa: F401  # 类型标注
from home_perception.analysis.warning import WarningEvent  # noqa: F401  # 类型标注

# 运行期惰性 import（失败隔离 + 避免循环）；仅用于类型标注解析
if TYPE_CHECKING:
    from home_perception.visualizer.viewer.live_adapter import ProjectionAccumulator

# === 冻结契约白名单 import（仅以下 home_perception 符号） ===
from home_perception.core.config import RealtimeRiskConfig, Settings
from home_perception.runtime.pipeline import DemoClock, FrameResult, PerceptionPipeline

# === 本包内部 ===
from .config import DemoSettings
from .scenarios import ScenarioConfig, load_scenario
from .sources import Source
from .state import DemoStateStore
from .ws import ConnectionHub, handle_upstream

# ===========================================================================
# Live 音频接入（ADR-0036 VM-13 Phase B · Owner 2026-08-16 · 依赖倒置接缝）
# ===========================================================================
# ``silver_demo`` 冻结边界（test_freeze_boundary.py T0-1）禁止 gateway 直接 import
# ``home_perception.audio``；音频管道（torch/YamNet）由**组装层**（scripts/run_demo.py，
# 非 silver_demo 包内）构建后以「事件列表」注入本模块。本模块只消费注入的音频事件，
# 绝不 import 音频生产类型（避免撞冻结白名单）。
#
# 组装层在调 ``main()`` 前把构建函数挂到本钩子：``gw.live_audio_builder = builder``。
# 签名：``(hp_settings, scenario) -> list[dict]``（每条为 AudioPerceptionEvent.to_dict()）。
# 返回空列表 = 本场景无音频（诚实空，audio_evidence 恒 ()）。
# 失败隔离：构建/注入异常 → 记日志并跳过，绝不阻断实时循环（VM-5 / 探针铁律）。
LiveAudioEventsBuilder = Callable[[Any, Any], "list[dict]"]
live_audio_builder: LiveAudioEventsBuilder | None = None


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
        self.pipeline: PerceptionPipeline | None = None
        self.clock: DemoClock | None = None
        self.source: Source | None = None
        self.n_frames: int = -1

        # 展示层组件（保留 Live Runtime 闭环：WS 上行 action → store 确认 ACK）
        self.hub = ConnectionHub()
        self.store = DemoStateStore()

        # ADR-0036 Phase 3：Live Adapter 增量投影累积器（Host 层反向依赖 visualizer.viewer）。
        # 单一事实源：每帧 FrameResult 经 Live Adapter 投影为 EvidenceProjection，
        # 不再有 aggregate_state 第二套事实模型；惰性创建，失败隔离——投影异常绝不改变生产行为。
        self._live_accumulator = None

        # Live 音频接入（VM-13 Phase B · 依赖倒置接缝）：由组装层注入的真实音频事件列表
        # （AudioPerceptionEvent.to_dict() 形态）；缺省空 = 本场景无音频，audio_evidence 恒 ()。
        self._live_audio_events: list = []

        # P0 evidence_delta 增量广播（Owner 2026-08-17 拍板）：浏览器 runtime clock 随
        # frame_tick 推进，增量投影让 DOM/timeline/卡片"事件涌现"。只读派生 + 失败隔离，
        # 绝不改变生产行为（VM-1/VM-9 边界：浏览器只渲染、不推理）。
        self._prev_evidence_fp: dict | None = None
        self._delta_seq = 0

        # 循环控制
        self._running = False
        self._task: asyncio.Task | None = None
        self._frame_index = 0
        self.loop_count = 0  # 循环重放计数（P0-11.3.5 状态面板）

    @classmethod
    def create_for_test(cls) -> DemoGateway:
        """测试用工厂：构造未装配的网关实例，跳过 YOLO 加载与场景解析。

        避免测试用 ``DemoGateway.__new__`` 绕过 ``__init__`` 后手动补齐一堆属性
        （脆弱、易漏属性导致 ``AttributeError``）。直接 ``cls(None, None, None)``
        即获得 ``__init__`` 赋予的全部默认属性（hub / store 等），
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
        # 场景级实时风险开关覆盖（ADR-0021 Phase 1：CCTV 夜间场景开启实时旁路 + 决策）
        # 必须在 PerceptionPipeline.from_settings 之前生效：它直接改 hp_settings.realtime_risk，
        # from_settings 读 hp_settings 装配实时组件（BehaviorBuilder / RecentBehaviorStore /
        # RealTimeRiskEvaluator）。hp_settings 在 assemble / _rebuild_pipeline 复用同一对象，
        # 故只需 assemble 应用一次，循环重放 / 切换场景都会重新读取该对象。
        self._apply_scenario_realtime_overrides()
        # Demo 默认开启 Memory 认知层（区域⑥ 运行时数据来源）；必须在 from_settings 之前
        self._apply_demo_memory_overrides()
        # Demo 推理尺寸覆盖（降 CPU 推理耗时、提帧率）；必须在 from_settings 之前，
        # 经 runtime.detector_imgsz 通道，不改动 config/default.yaml 的 detection.imgsz（生产不变）
        self._apply_demo_detector_overrides()

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
        # 帧循环限速间隔：frame_loop_interval_s > 0 → 按设定间隔 sleep（模拟实时观感）；
        # <= 0 → 不限速，尽快处理（见 DemoSettings / ScenarioConfig docstring "0 = 不限速"）。
        # 注意：scenario.fps_target 仅由 sources.py 用于「抽帧」，与此处限速无关。
        interval = self._resolve_frame_interval(
            self.demo_settings.frame_loop_interval_s, self.scenario.fps_target
        )

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

            # 消费冻结契约：process_frame（唯一出口，冻结对象只读消费）
            # 注意：frame_index 单调递增、loop 重放时**不回绕**（与冻结 read_caviar_frames 的 i % n 不同）。
            # 长 loop 下 frame_index 会超过 n_frames——展示进度请用 clock.now()（demo_time）或 self.n_frames。
            result: FrameResult = self.pipeline.process_frame(frame, frame_index=self._frame_index)

            # Live Adapter 增量投影（ADR-0036 Phase 3：收敛 /live 到统一 Case Viewer）。
            # 单一事实源：FrameResult → ProjectionAccumulator → EvidenceProjection。
            # 失败隔离：投影探针异常必须吞掉+记日志，绝不改变生产行为（VM-5 / 探针铁律）。
            try:
                acc = self._ensure_live_accumulator()
                acc.ingest(result)
                # Live 音频接入（VM-13 Phase B · Owner 2026-08-16）：把注入的真实
                # AudioPerceptionEvent 按帧位置流入 Live Adapter（provenance=REAL_SENSOR）。
                # 确定性投递（frame_index==k → 第 k 条音频事件）→ 重放幂等（VM-8）。
                self._feed_live_audio(acc)
            except Exception as exc:  # noqa: BLE001  # 投影失败不影响实时循环
                structlog.get_logger(__name__).warning("live_projection_ingest_failed", exc_info=exc)

            # P0-1 人类处置闭环：把产生的 WarningEvent 登记进处置工作流 store（前端有可操作目标）。
            # 这是 Workflow State（UI/会话态），不是 EvidenceProjection（VM-6 不回写）；
            # 失败隔离：登记异常吞掉+记日志，绝不改变实时循环（探针铁律）。
            try:
                for w in (getattr(result, "warnings", None) or ()):
                    await self.store.upsert(str(w.warning_id), status="pending")
            except Exception as exc:  # noqa: BLE001
                structlog.get_logger(__name__).warning("workflow_store_upsert_failed", exc_info=exc)

            # 心跳广播：每帧只推最小进度信号（frame_index / loop_count），
            # 不做第二套事实模型（view / state / meta）——真实展示走 GET /live 渲染同一 EvidenceProjection。
            await self.hub.broadcast(
                {
                    "type": "frame_tick",
                    "frame_index": self._frame_index,
                    "loop_count": self.loop_count,
                }
            )

            # P0 evidence_delta 增量广播：对同一 EvidenceProjection 做只读 diff，把新增
            # evidence（timeline 节点 / audio 证据 / Case Time 标记）推给浏览器增量渲染——
            # 浏览器 runtime clock 随 frame_tick 推进，增量投影驱动"事件涌现"（Live Intelligence
            # Viewer，Owner 2026-08-17 拍板）。无新增 → 不发（保持 frame_tick 最小心跳语义）。
            # 失败隔离：增量投影异常吞掉+记日志，绝不改变实时循环（探针铁律）。
            try:
                acc = self._ensure_live_accumulator()
                delta = acc.extract_evidence_delta(self._prev_evidence_fp)
                self._prev_evidence_fp = acc.projection_fingerprint()
                if delta.get("timeline") or delta.get("audio") or delta.get("case_time"):
                    self._delta_seq += 1
                    delta["seq"] = self._delta_seq
                    await self.hub.broadcast(delta)
            except Exception as exc:  # noqa: BLE001
                structlog.get_logger(__name__).warning("evidence_delta_failed", exc_info=exc)

            self._frame_index += 1
            if interval > 0:
                await asyncio.sleep(interval)
            else:
                # 让出事件循环，避免阻塞 WS 上行
                await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Live 音频接入（VM-13 Phase B · 依赖倒置接缝）
    # ------------------------------------------------------------------

    def set_live_audio_events(self, events: list) -> None:
        """注入真实音频感知事件列表（组装层经 AudioPipeline 产出，AudioPerceptionEvent.to_dict() 形态）。

        冻结边界合规：本方法不 import / 构造任何 ``home_perception.audio`` 符号，只存储调用方传入的
        字典列表；每条须含 ``timestamp`` / ``kind`` / ``score`` / ``confidence`` /
        ``source_segment_ids`` / ``labels``（``event_id`` 可选），由 Live Adapter 的
        ``ingest_audio`` 做 fail-closed 校验（命中 forbidden 字段 / 类型非法即拒绝）。
        """
        if not isinstance(events, (list, tuple)):
            raise TypeError(f"live_audio_events 须为 list/tuple，收到 {type(events).__name__}")
        self._live_audio_events = list(events)

    def _feed_live_audio(self, acc: ProjectionAccumulator) -> None:
        """按帧位置把注入的音频事件流入 Live Adapter（确定性、幂等）。

        投递规则：``frame_index == k`` 时喂入第 k 条音频事件（k 从 0 起），超出列表长度后不再喂。
        因 ``frame_index`` 单调递增（loop 重放不回绕），每条事件仅喂一次；同一有序流重放
        N 次 → 同一 audio_evidence（VM-8 幂等）。失败隔离：单条音频摄入异常 → 记日志跳过，
        绝不阻断实时帧循环（VM-5 / 探针铁律）。
        """
        events = self._live_audio_events
        if not events:
            return
        idx = self._frame_index
        if idx < 0 or idx >= len(events):
            return
        ev = events[idx]
        # 支持 AudioPerceptionEvent 对象（to_dict）或直接 dict（组装层已转换）。
        data = ev.to_dict() if hasattr(ev, "to_dict") else ev
        try:
            acc.ingest_audio(data)
        except Exception as exc:  # noqa: BLE001
            structlog.get_logger(__name__).warning(
                "live_audio_frame_ingest_failed", frame_index=idx, exc_info=exc
            )

    def stop(self) -> None:
        """停止帧循环。"""
        self._running = False

    def close(self) -> None:
        """释放 pipeline 资源。"""
        if self.pipeline is not None:
            try:
                self.pipeline.close()
            except Exception as exc:  # noqa: BLE001  # 资源释放失败也要记日志，避免静默泄漏（AGENTS.md §2.5）
                structlog.get_logger(__name__).warning("pipeline.close 失败", exc_info=exc)
            self.pipeline = None

    # ------------------------------------------------------------------
    # ADR-0036 Phase 3：Live Adapter 增量投影（Host 层反向依赖 Presentation Layer）
    # ------------------------------------------------------------------

    def _ensure_live_accumulator(self) -> ProjectionAccumulator:
        """惰性创建 Live Adapter 的 ``ProjectionAccumulator``（仅 Host 层反向依赖 visualizer.viewer）。

        失败隔离：投影是只读探针，异常必须吞掉+记日志，绝不改变生产行为（VM-5 / 探针铁律）。
        """
        if self._live_accumulator is None:
            from home_perception.visualizer.viewer.live_adapter import ProjectionAccumulator

            self._live_accumulator = ProjectionAccumulator(
                self.scenario.scenario_id, mode="live"
            )
        return self._live_accumulator

    def _validate_frame_source(self, scenario: ScenarioConfig) -> None:
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
                    "scenario.rule_overrides.unknown_key",
                    key=k,
                    scenario=self.scenario.scenario_id,
                )

    # 允许经场景 YAML 覆盖的实时开关字段白名单：仅这两个语义明确的开关可被 setattr 写入
    # RealtimeRiskConfig；禁止把任意字段名经 setattr 改写配置对象（避免误用私有/未文档字段）。
    _REALTIME_OVERRIDE_ALLOWED = ("enabled", "decision_enabled")

    def _apply_scenario_realtime_overrides(self) -> None:
        """场景级实时风险开关覆盖（ADR-0021 Phase 1）。

        从 ``scenario.realtime_risk`` 把开关覆盖进 ``hp_settings.realtime_risk``
        （如 CCTV 夜间场景 ``{enabled: true, decision_enabled: true}``），使单个场景
        可开启实时风险旁路，不影响全局默认与其他场景。

        **跨场景复位（防状态泄漏，关键）**：进入本方法先无条件把 ``enabled`` /
        ``decision_enabled`` 复位为基线（``RealtimeRiskConfig`` 默认值 ``False``），
        再应用本场景覆盖。否则从「已开启 realtime 的场景」热切到「无 realtime_risk
        override」的场景时，旧值会残留为 ``True``，导致实时旁路意外开启（跨场景状态泄漏）。

        **必须在 ``from_settings`` 之前调用**：``from_settings`` 读 ``hp_settings.realtime_risk``
        决定构造哪些实时组件（不同于 ``rule_overrides`` 阈值可在构造后覆盖）；本方法在
        ``assemble`` 与 ``_rebuild_pipeline`` 的 ``from_settings`` 之前各调用一次，故切换场景 /
        循环重放都会重新读取正确值。``hp_settings`` 在二者间复用同一对象。

        **白名单**：仅 ``enabled`` / ``decision_enabled`` 可被覆盖；其他键视为未知键告警跳过，
        不写入配置对象（避免任意字段名经 ``setattr`` 改写私有属性）。
        """
        rt = self.hp_settings.realtime_risk
        # 1) 先复位到基线，杜绝跨场景泄漏
        baseline = RealtimeRiskConfig()
        rt.enabled = baseline.enabled
        rt.decision_enabled = baseline.decision_enabled
        # 2) 再应用本场景覆盖（仅白名单内字段）
        overrides = getattr(self.scenario, "realtime_risk", None)
        if not overrides:
            return
        for k, v in overrides.items():
            if k in self._REALTIME_OVERRIDE_ALLOWED:
                setattr(rt, k, v)
            else:
                structlog.get_logger(__name__).warning(
                    "scenario.realtime_risk.unknown_key",
                    key=k,
                    scenario=self.scenario.scenario_id,
                )

    # ------------------------------------------------------------------
    # Demo 默认开启 Memory 认知层（ADR-0025 C-4/C-6 · Shadow 观测）
    # ------------------------------------------------------------------

    def _apply_demo_memory_overrides(self) -> None:
        """Demo 默认开启 Memory 认知层，使区域⑥ Memory Context 有运行时数据。

        把 ``hp_settings.memory`` 的四个开关置 True：
        - ``enabled`` / ``episodic_shadow``：构造 ``InMemoryStore`` + 开启 Stage F 影子写入
          （每次访客离场投影 ``EpisodicRecord``，为召回提供历史）；
        - ``consumer_enabled`` / ``reasoning_enabled``：按模式 B 门控召回历史 → 组装
          ``ReasoningInput`` → ``RuleBasedReasoningEngine`` 产出 ``ReasoningResult``，
          经 ``FrameResult`` 做 Shadow 观测。

        仅经 settings 覆盖，不触碰 pipeline 内部（守 ADR-0015）。消费侧只读、不决策、
        不产 Warning（守 ADR-0010）；聚合出的记忆只增强理解，不回流决策。幂等（重复调用
        结果一致），故在 ``assemble`` 与 ``_rebuild_pipeline`` 的 ``from_settings`` 前各调一次。
        """
        mem = self.hp_settings.memory
        mem.enabled = True
        mem.episodic_shadow = True
        mem.consumer_enabled = True
        mem.reasoning_enabled = True

    def _apply_demo_detector_overrides(self) -> None:
        """Demo 推理尺寸覆盖：经 ``runtime.detector_imgsz`` 通道降 CPU 推理耗时、提帧率。

        - 仅 demo 网关生效，不触碰 ``config/default.yaml`` 的 ``detection.imgsz``（生产 480 不变）。
        - ``DemoSettings.detector_imgsz`` 默认 416（= ``ImgszProfile.REALTIME``），专为无 GPU 机器；
          ``None`` 表示不覆盖（退回 ``detection.imgsz``）。
        - 幂等，故在 ``assemble`` 与 ``_rebuild_pipeline`` 的 ``from_settings`` 前各调一次。
        """
        if self.demo_settings.detector_imgsz is not None:
            self.hp_settings.runtime.detector_imgsz = self.demo_settings.detector_imgsz

    @staticmethod
    def _resolve_frame_interval(demo_interval: float, fps_target: int) -> float:
        """解析帧循环限速间隔（纯函数，便于单测）。

        - ``demo_interval > 0`` → 返回该间隔（模拟实时观感）；
        - ``<= 0`` → 返回 ``0.0``（不限速，尽快处理，见 docstring "0 = 不限速"）。

        不再回退到 ``1.0 / fps_target``：``fps_target`` 仅由 ``sources.py`` 用于「抽帧」，
        与此处限速是两件事，混用会让 ``frame_loop_interval_s=0`` 违背其文档语义。
        """
        return demo_interval if demo_interval > 0 else 0.0

    # ------------------------------------------------------------------
    # 输入源热切换（P0-11.4 视频输入适配层）
    # ------------------------------------------------------------------

    async def switch_source(self, scenario: ScenarioConfig) -> None:
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
            except Exception as exc:  # noqa: BLE001  # 非取消异常（如取消中 pipeline.close 抛错）需记录，避免静默丢失
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
        # Live Adapter 投影累积器重置（新会话 = 新证据投影；GET /live 重渲染即干净状态）
        self._live_accumulator = None
        # P0 delta 基线同步重置（新会话 = 新投影，避免陈旧指纹造成首帧全量重发）
        self._prev_evidence_fp = None
        self._delta_seq = 0

        # 3. 重开循环
        self._task = asyncio.create_task(self.run_loop())

        # 4. 广播切换事件（前端清空跨帧累积状态）
        await self.hub.broadcast(
            {
                "type": "source_switched",
                "scenario": scenario.scenario_id,
                "source": scenario.source,
                "source_type": scenario.source_type,
                "frames": self.n_frames,
            }
        )

    def _rebuild_pipeline(self, scenario: ScenarioConfig) -> None:
        """重建流水线状态组件（复用已加载的 YOLO detector，避免重载权重）。

        清空跨循环/跨场景累积的追踪（VisitorTracker）/ 时间窗口（FeatureExtractor）/
        规则计数（RuleEngine）/ 决策状态（DecisionEngine）等，使每次分析都从干净状态开始，
        保证循环重放 / 切换场景后风险能重新触发（否则演示区 ②③④ 在多轮循环后变空白）。
        仅重建组件，detector 实例复用（model.track(persist=True) 要求同一实例保证 track_id 一致）。
        """
        self.clock = DemoClock(start=scenario.start_time, interval_s=scenario.frame_interval_s)
        # 场景级实时风险开关覆盖（与 assemble 对齐）：必须在 from_settings 之前，
        # 因实时组件是否构造取决于 hp_settings.realtime_risk。内部先复位基线再覆盖，
        # 切到「无 realtime_risk override」的场景时不会残留上一个场景的 True（防泄漏）。
        self._apply_scenario_realtime_overrides()
        # Demo 默认开启 Memory 认知层（区域⑥ 运行时数据来源）；必须在 from_settings 之前
        self._apply_demo_memory_overrides()
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


def _resolve_inference_device(hp_settings: Settings) -> str:
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
    except Exception:  # noqa: BLE001, S110  # torch 不可用 / 无 CUDA：回退到配置或 CPU
        pass
    return getattr(getattr(hp_settings, "detection", None), "device", "cpu") or "cpu"


def _verified_case_missing_html() -> str:
    """旗舰入口未构建产物时的引导提示页（Verified Cases）。"""
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>SilverShield · Verified Cases</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:3rem;color:#222}"
        "code{background:#f4f4f4;padding:2px 6px;border-radius:4px}</style></head><body>"
        "<h1>SilverShield · Verified Cases</h1>"
        "<p>旗舰入口：可信案例回放 · CI 验证资产。</p>"
        "<p>尚未发现预渲染的 <code>case_viewer.html</code>。请先运行生产阶段的 Trusted Case Factory：</p>"
        "<pre>python scripts/build_trusted_case.py --scenarios config/demo/scenarios/night_visit.yaml --out-dir demo</pre>"
        "<p>或在 <code>DEMO_CASE_ARTIFACTS</code> 中指向已构建产物目录。</p>"
        "<p style=\"color:#888\">Live 实时运行入口（/live）仅在 <code>DEMO_LIVE=1</code> 时可用。</p>"
        "</body></html>"
    )


def _live_disabled_html() -> str:
    """Live 次级入口未启用时的提示页（404）。"""
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>SilverShield · Live Runtime Preview (disabled)</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:3rem;color:#222}"
        "code{background:#f4f4f4;padding:2px 6px;border-radius:4px}</style></head><body>"
        "<h1>SilverShield · Live Runtime Preview</h1>"
        "<p>实时运行模式 · 实验 / 演示入口。</p>"
        "<p>当前未启用（<code>DEMO_LIVE=1</code> 才暴露）。旗舰入口请访问 "
        "<a href=\"/\">/（Verified Cases）</a>。</p>"
        "</body></html>"
    )


def create_app(
    demo_settings: DemoSettings | None = None,
) -> FastAPI:
    """构造 FastAPI app（ADR-0015 §3 · Task 0 双入口）。

    Args:
        demo_settings: Demo 网关配置；None 则从环境变量构造。

    Returns:
        FastAPI app，已注册：
        - ``GET /`` → 旗舰 Case Viewer（静态托管 Factory 预渲染 case_viewer.html；不 import visualizer、不依赖 runtime）
        - ``GET /live`` → Live Runtime Preview（统一 Case Viewer：``render_case_viewer`` 渲染实时累积的 ``EvidenceProjection``；仅 ``live_enabled`` 时可用）
        - ``GET /health`` → 健康检查（含 mode / live_enabled / assembled）
        - ``WS {ws_path}`` → Live 帧下行 + action 上行（仅 live_enabled）
        - ``POST /demo/*`` → Live 视频输入 / 场景切换 / reset（仅 live_enabled）
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
        except Exception:  # noqa: BLE001  # 冻结 / 不可变配置：复制后再改，保证 detector 继承设备选择
            hp_settings = hp_settings.model_copy(deep=True)
            hp_settings.detection.device = desired

    scenario = load_scenario(demo_settings.scenario_path)

    gateway = DemoGateway(demo_settings, hp_settings, scenario)

    # Live 音频接入（VM-13 Phase B · Owner 2026-08-16 · 依赖倒置）：组装层（scripts/run_demo.py）
    # 经 ``live_audio_builder`` 钩子注入真实 AudioPerceptionEvent 列表（已跑过 AudioPipeline）。
    # 失败隔离：构建/注入异常 → 记日志 + 跳过，绝不阻断实时循环（VM-5 / 探针铁律）。
    if live_audio_builder is not None:
        try:
            gateway.set_live_audio_events(live_audio_builder(hp_settings, scenario))
        except Exception as exc:  # noqa: BLE001
            structlog.get_logger(__name__).warning("live_audio_injection_failed", exc_info=exc)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not demo_settings.live_enabled:
            # 旗舰模式：仅服务 Factory 预渲染的 Case Viewer，不装配 runtime、不启帧循环、
            # 不加载 YOLO / torch。网关此时零渲染逻辑、零 home_perception 运行期依赖。
            yield
            return
        # Live 模式：确保上传目录存在 + 装配 + 起帧循环后台 task
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
                except Exception as exc:  # noqa: BLE001  # 非取消异常需记录，避免静默丢失
                    structlog.get_logger(__name__).warning("帧循环任务取消时发生异常", exc_info=exc)
            gateway.close()

    app = FastAPI(
        title="SilverShield Demo Gateway",
        version="0.1.0",
        description="P0-11 三端风险闭环展示层网关（ADR-0015）",
        lifespan=lifespan,
    )

    # 静态资源：Live 次级入口不再独立挂载 /live-static（legacy dashboard 已移除，收敛到统一 Case Viewer）。
    # 旗舰 Case Viewer 的 HTML 由下方 GET / 直接读取文件返回（Phase 1 自包含，无需静态挂载；
    # 媒体资产经 EvidenceProjection → Case Viewer 渲染管线统一处理）。

    case_dir = Path(demo_settings.case_artifacts_dir) if demo_settings.case_artifacts_dir else None

    # 音频 E2E（P0 验收补全）：旗舰 Case Viewer 的媒体/音频资源是相对 artifacts 根的路径
    # （如 canonical/<sid>/audio/<kind>.wav；case_viewer.html 在包根、canonical/ 是其兄弟目录）。
    # 浏览器在 "/" 打开 HTML 后按相对路径请求 "/canonical/..."——若网关不伺服，<audio controls>
    # 的样本、媒体帧、case.mp4 全部 404，产品形态下"可播放音频"断裂（验收红线）。
    # 此处把 case_dir/canonical 挂载到 /canonical（与 HTML 相对前缀对齐，零改动渲染产物）。
    # 仅 canonical 目录存在时挂载；缺失 → 该路径自然 404，不额外兜底（诚实降级）。
    # starlette StaticFiles 自带路径穿越防护（拒绝 ".." 越界读取 artifacts 之外的文件，fail-closed）。
    _canonical_dir = (case_dir / "canonical") if case_dir else None
    if _canonical_dir is not None and _canonical_dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount(
            "/canonical",
            StaticFiles(directory=_canonical_dir, check_dir=True),
            name="case_artifacts",
        )

    @app.get("/", response_class=HTMLResponse)
    async def verified_case() -> HTMLResponse:
        """旗舰入口：Verified Case / 主展示。

        静态托管 Factory 预渲染的 case_viewer.html（由 build_trusted_case → run_case_viewer.py
        在外部生产阶段生成）。不启动渲染逻辑、不 import visualizer、不依赖 runtime。
        未构建时返回引导提示页。
        """
        if case_dir is None:
            return HTMLResponse(_verified_case_missing_html(), status_code=200)
        case_file = case_dir / demo_settings.case_viewer_filename
        if case_file.is_file():
            return HTMLResponse(case_file.read_text(encoding="utf-8"))
        return HTMLResponse(_verified_case_missing_html(), status_code=200)

    @app.get("/live", response_class=HTMLResponse)
    async def live_preview() -> HTMLResponse:
        """次级入口：Live Runtime Preview / 实验·演示入口（仅 DEMO_LIVE=1 暴露）。

        收敛到统一 Case Viewer（ADR-0036 Phase 3）：经 Host 层（gateway）反向依赖
        ``home_perception.visualizer.viewer``，把实时累积的 ``EvidenceProjection`` 投影为
        与旗舰同源的 Case Viewer HTML（同一渲染器 ``render_case_viewer``、同一 View Model、
        同一套语义体系，T0-6）。Legacy dashboard 不再作为 /live 表面。
        """
        if not demo_settings.live_enabled:
            return HTMLResponse(_live_disabled_html(), status_code=404)
        try:
            from home_perception.visualizer.viewer import render_case_viewer
            from home_perception.visualizer.viewer.live_adapter import build_live_presentation

            projection = gateway._ensure_live_accumulator().to_evidence_projection()
            proj, descriptor = build_live_presentation(
                projection, live_ws_path=demo_settings.ws_path
            )
            html = render_case_viewer(proj, descriptor)
            return HTMLResponse(html)
        except Exception as exc:  # noqa: BLE001  # 渲染失败 fail-closed：返回 500 + 诊断，绝不静默产残缺页
            structlog.get_logger(__name__).warning("live_case_viewer_render_failed", exc_info=exc)
            return HTMLResponse(
                f"<!doctype html><html><body><h1>Live Case Viewer 渲染失败</h1>"
                f"<pre>{exc!r}</pre></body></html>",
                status_code=500,
            )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "verified" if not demo_settings.live_enabled else "live",
            "live_enabled": demo_settings.live_enabled,
            "live_route": demo_settings.live_route,
            "assembled": gateway.pipeline is not None,
            "scenario": gateway.scenario.scenario_id,
            "source": gateway.scenario.source,
            "source_type": gateway.scenario.source_type,
            "n_frames": gateway.n_frames,
            "frame_index": gateway._frame_index,
            "active_connections": len(gateway.hub.active),
        }

    @app.websocket(demo_settings.ws_path)
    async def websocket_endpoint(ws: WebSocket) -> None:
        """WebSocket 端点：下行 state 快照 + 心跳；上行 action 写 store。"""
        if not demo_settings.live_enabled:
            # 旗舰模式：Live WS 不暴露；直接关闭连接（明确提示 disabled）
            await ws.close(code=1000)
            return
        await gateway.hub.connect(ws)
        # 首连 snapshot：把动作闭环状态推给新连接（晚连也能看到历史 action 状态）
        await gateway.hub.send_to(
            ws,
            {
                "type": "snapshot",
                "state": await gateway.store.snapshot(),
                "meta": {
                    "frame_index": gateway._frame_index,
                    "loop_count": gateway.loop_count,
                    "n_frames": gateway.n_frames,
                    "scenario": gateway.scenario.scenario_id,
                },
            },
        )
        try:
            while True:
                raw = await ws.receive_text()
                ack = await handle_upstream(ws, raw, gateway.store)
                if ack is not None:
                    await ws.send_text(json.dumps(ack, ensure_ascii=False))
                    # action 处理后广播最新 state 快照给所有连接
                    state_snap = await gateway.store.snapshot()
                    await gateway.hub.broadcast({"type": "state_update", "state": state_snap})
                    # P0-1（Projection 不回写 / VM-6）：状态机到达终态 community_done →
                    # 产生 Resolution 事实，作为**新事件** feed 进 accumulator（非 mutate
                    # projection），/live 渲染时 to_evidence_projection() 重新构造 →
                    # Evidence Timeline 出现 ACTION 节点。失败隔离：吞掉+记日志。
                    updated = ack.get("updated") or {}
                    if updated.get("status") == "community_done":
                        try:
                            gateway._ensure_live_accumulator().ingest_resolution(
                                {
                                    "warning_id": updated["warning_id"],
                                    "operator": updated.get("operator") or "community",
                                    "action": ack.get("action") or "complete",
                                    "status": "community_done",
                                }
                            )
                        except Exception as exc:  # noqa: BLE001
                            structlog.get_logger(__name__).warning(
                                "resolution_ingest_failed", exc_info=exc
                            )
        except WebSocketDisconnect:
            pass
        finally:
            await gateway.hub.disconnect(ws)

    # ------------------------------------------------------------------
    # P0-11.4 视频输入适配层：场景输入 / 视频源接入
    # ------------------------------------------------------------------

    @app.post("/demo/upload")
    async def upload_video(file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008  # FastAPI 依赖注入约定
        """P0-11.4 视频输入适配：接收本地视频 → 落盘 → 热切换 VideoFileFrameSource → 重开帧循环。

        视频只是"传感器"：经冻结 Pipeline 产出 身份→轨迹→行为→风险→解释→干预 全链，
        不调用任何视觉大模型 API（工程闭环验证，非模型性能评测）。
        """
        if not demo_settings.live_enabled:
            return JSONResponse(
                status_code=404,
                content={"error": "Live 入口未启用（DEMO_LIVE=1 才暴露 /live + WS + /demo/*）"},
            )
        allowed = {".mp4", ".mpg", ".mpeg", ".avi", ".mov", ".mkv", ".webm"}
        fname = file.filename or ""
        ext = Path(fname).suffix.lower()
        if ext not in allowed:
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"不支持的视频格式 {ext or '(无扩展名)'}，仅允许 {sorted(allowed)}"
                },
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
        except Exception as exc:  # noqa: BLE001  # 写入失败：清理临时文件并返回 500
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
            start_time=datetime.now(UTC),
            frame_interval_s=base.frame_interval_s,
            fps_target=base.fps_target,
            loop=True,
            description=f"用户上传：{fname}",
        )
        try:
            await gateway.switch_source(new_scenario)
        except Exception as exc:  # noqa: BLE001  # 接入失败：清理临时文件并返回 422
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
            "message": "已开始分析，Case Viewer 实时刷新（身份→轨迹→行为→风险→干预）",
        }

    @app.post("/demo/scenario")
    async def switch_scenario(req: Request) -> dict[str, Any]:
        """P0-11.4 场景输入：按 scenario_id（或路径）热切换到预置场景（模拟场景 / CAVIAR 工程验证）。"""
        if not demo_settings.live_enabled:
            return JSONResponse(
                status_code=404,
                content={"error": "Live 入口未启用（DEMO_LIVE=1 才暴露 /live + WS + /demo/*）"},
            )
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001  # JSON 解析失败：回退到空 body
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
                content={
                    "error": f"场景不存在: {scn_id}（应在 {demo_settings.scenarios_dir}/ 下）"
                },
            )
        found = candidate
        try:
            sc = load_scenario(found)
            await gateway.switch_source(sc)
        except Exception as exc:  # noqa: BLE001  # 场景切换失败：返回 422
            return JSONResponse(status_code=422, content={"error": f"场景切换失败：{exc}"})
        return {
            "status": "ok",
            "scenario": sc.scenario_id,
            "source": sc.source,
            "source_type": sc.source_type,
            "frames": gateway.n_frames,
        }

    @app.post("/demo/reset")
    async def reset_demo() -> dict[str, Any]:
        """P0-11.3.5 Reset 生命周期：清空 pipeline / 状态 / 聚合，恢复干净会话。

        复用 ``switch_source(同场景)``：停旧循环 → 重建流水线（复用已加载 YOLO detector）
        → 清空闭环 store + 服务端聚合状态 → 重置帧索引 / 循环计数 / 会话计时 → 重开循环。
        广播 ``source_switched``（前端据此 ``resetSession()`` 清空跨帧累积）。
        比赛换组场景：点 Reset → ≤30s 内恢复干净状态可重跑。
        """
        if not demo_settings.live_enabled:
            return JSONResponse(
                status_code=404,
                content={"error": "Live 入口未启用（DEMO_LIVE=1 才暴露 /live + WS + /demo/*）"},
            )
        try:
            await gateway.switch_source(gateway.scenario)
        except Exception as exc:  # noqa: BLE001  # 重置中 pipeline 重建失败
            return JSONResponse(status_code=422, content={"error": f"重置失败：{exc}"})
        return {
            "status": "ok",
            "frame_index": 0,
            "loop_count": 0,
        }

    # 把 gateway 挂到 app.state，便于测试 / 调试访问
    app.state.gateway = gateway
    return app


# 模块级 app（uvicorn silver_demo.gateway:app 直接启动）
app: FastAPI | None = None


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
