# ADR-0013 · P0-10 装配联调 · Demo 模式 / runtime 包 / 保持 Mock

## 状态

Accepted · 2026-07-20

## 背景

P0 Integration Validation（ADR-0012）已通过：274 测试全绿，6 Golden Scenarios + 状态机
+ 故障注入 + CAVIAR 3 场景端到端全部 PASS。准入条件 8/8 项满足。

进入 P0-10（装配与联调）后，核心工程问题从"逻辑是否正确"变为"系统怎么启动"：
1. **7 层组件已有独立验证**，但缺少一个"一键启动"入口把所有层串起来
2. `main.py` 是 stub（仅打印 mode），`core/pipeline.py` 是废弃 ABC（不对应当前架构）
3. CAVIAR fixtures 是静态抽帧图（无真实帧时间戳），直接跑 Demo 时 tracker 用墙钟 →
   帧间毫秒级间隔远低于 `absence_gap_s=5s` → **0 VisitorEvent**（Demo 空转）
4. CAVIAR meet_walk_together 场景鱼眼俯拍小目标人物 YOLO conf 仅 0.05–0.11（低于生产阈值）
5. 规则阈值按生产值设（300s 停留 / 23:00-06:00 异常时段），CAVIAR 短片无法触发任何规则

Owner 明确界定：**P0-10 = 工程层问题（"怎么启动系统"），不再验证逻辑正确性。**

## 决策

### 决策 1：新建 `runtime/` 包，不修改废弃的 `core/pipeline.py`

`core/pipeline.py` 含旧的 ABC 抽象基类（`BasePipeline`），与新架构（7 层明确依赖链）
不兼容。新建 `src/home_perception/runtime/` 作为装配层包：

| 文件 | 职责 |
|---|---|
| `__init__.py` | 公共导出 |
| `pipeline.py` | `PerceptionPipeline`（7 层装配器）、`DemoClock`、`FrameResult`、`RunSummary` |
| `config.py` | config-to-component helpers（build_threshold_config 等）、`read_caviar_frames` |
| `observability.py` | `PipelineMetrics` 数据class（frames/detections/events/warnings/errors 计数器）|
| `lifecycle.py` | `run_demo(settings)` 主流程、shutdown handler、汇总日志 |

理由：旧 pipeline.py 的 ABC 与新架构语义冲突；重写不如新建包干净。

### 决策 2：DemoClock 注入模拟时间线驱动 tracker 离场判定

CAVIAR fixtures 是静态 JPG 抽帧，无真实帧率/时间戳。若用墙钟（`time.time()`），
50 帧 ~25ms 处理完，tracker 从未看到超过 5s 的 absence_gap → 无 leave → 0 VisitorEvent。

解决方案：`DemoClock(start, interval_s=0.5)` 可调用时钟对象：
- `__call__() -> datetime` 兼容组件 `now_provider()` 约定（组件调 `self._now()` 即可）
- `tick(dt)` 推进模拟时间
- `run()` 每帧调用 `self._clock.tick(0.5)` → 50 帧 = 25 秒模拟时间线
- DemoClock 起点由 `runtime.demo_clock_start`（ISO 8601，默认 `2026-07-19T23:30:00+00:00`）
  **配置驱动** → 更换场景 / 调整异常时段无需改源码；默认值落在 odd_hour_set 内 → OddHourRule 自然触发

### 决策 3：保持 Mock Publisher / Notifier（MVP 不接真实通道）

Owner 在 P0-9 已决策保持 Mock。P0-10 继承此约束：
- `MockPublisher(output_path=var/mock_mqtt_demo.jsonl)` — 落盘 JSONL 供审计
- `MockNotifier()` — 内存计数（family_count/community_count）
- v1 接 paho-mqtt / 短信网关时实现 Protocol 接口即可替换

### 决策 4：Demo 专用配置覆盖（不污染生产默认值）

`config/default.yaml` 中 `runtime:` 和 `rule:` 段提供 Demo 专用调优：

| 字段 | 生产值 | Demo 值 | 理由 |
|---|---|---|---|
| `runtime.detector_conf` | null（用 detection.conf_threshold=0.45） | **0.10** | 鱼眼俯拍小目标需低阈值；class_filter 过滤噪声类 |
| `rule.long_duration_seconds` | 300.0 | **1.5** | CAVIAR 片段 ~25s 总长，生产阈值永远不触发 |
| `runtime.demo_clock_start` | — | **2026-07-19T23:30:00+00:00** | 配置驱动；属 odd_hour_set → OddHourRule 自然触发 |

这些值仅在 `mode: demo` 下生效；realtime 模式用 production 默认。

### 决策 5：每场景独立状态（跨场景共享 detector 实例）

`run_demo()` 循环中：
- `shared_detector` = 全局唯一 `YOLODetector`（`model.track(persist=True)` 要求同实例）
- 每个 scenario 创建独立的 `PerceptionPipeline`（tracker/builder/feature/rule/decision/executor 各自新建）
- 保证场景间状态零污染（track_id/visitor_id/frequency window 不串）

### 决策 6：优雅关闭（SIGINT/SIGTERM → KeyboardInterrupt）

注册信号处理器将 SIGINT/SIGTERM 转为 `KeyboardInterrupt`；
`run()` 捕获 `KeyboardInterrupt` 后停止处理循环，返回已处理部分的 `RunSummary`。
非主线程/不支持的平台静默跳过（不阻塞启动）。

## 交付物

### 新增文件
- `src/home_perception/runtime/__init__.py`
- `src/home_perception/runtime/pipeline.py`（PerceptionPipeline + DemoClock + FrameResult + RunSummary）
- `src/home_perception/runtime/config.py`
- `src/home_perception/runtime/observability.py`
- `src/home_perception/runtime/lifecycle.py`
- `tests/test_runtime.py`（15 测试）

### 修改文件
- `src/home_perception/main.py`（demo 路由入口）
- `src/home_perception/core/config.py`（新增 rule/action/runtime 配置段）
- `config/default.yaml`（rule/action/runtime 段 + Demo 调优值）
- `src/home_perception/detection/tracker.py`（now_provider fallback fix）
- `src/home_perception/analysis/event_builder.py`（now_provider fallback fix）
- `src/home_perception/detection/detector.py`（新增 `unload()` 公共方法）
- `.gitignore`（var/）

### Demo 验收结果（scripts/run.py EXIT=0）

| 场景 | 帧 | 检测 | 访客事件 | 感知事件 | 告警 | 指令 | 错误 |
|---|---|---|---|---|---|---|---|
| one_stop_enter | 50 | 58 | 8 | 10 (7 normal + 1 dwell) | 7 LOW | 7 LOG_ONLY | 0 |
| one_leave_reenter | 30 | 49 | 1 | 2 (1 dwell + 1 normal) | 1 LOW | 1 LOG_ONLY | 0 |
| meet_walk_together | 50 | 7 | 0 | 0 | 0 | 0 | 0 |
| **合计** | **130** | **114** | **9** | **12** | **8** | **8** | **0** |

### 验收指标
- `ruff check src tests`: All checks passed ✅
- `compileall -q src/home_perception`: OK ✅
- `pytest tests/ -q`: **289 passed** ✅（274 prior + 15 runtime）

## 修订记录

### 2026-07-20（代码评审加固 · 7 项）

Owner 评审 P0-10 交付后提出的改进项，已在本分支追加提交修复，未改变 Demo 行为：

| # | 严重度 | 文件 | 问题 | 修复 |
|---|---|---|---|---|
| 1 | 🟡 | `lifecycle.py` / `core/config.py` / `default.yaml` | DemoClock 起始时间硬编码 | 新增 `runtime.demo_clock_start`（ISO 8601，默认 23:30 UTC），`lifecycle._parse_demo_clock_start()` 解析，YAML 可覆盖 |
| 2 | 🟡 | `pipeline.py` | `hasattr(self._clock,"tick")` duck-typing 脆弱 | 定义 `NowProvider` / `TickableNowProvider`（`runtime_checkable` Protocol），`run()` 改用 `isinstance(self._clock, TickableNowProvider)`；`now_provider` 类型标注 `Optional[NowProvider]` |
| 3 | 🟡 | `detector.py` / `lifecycle.py` / `pipeline.py` | 直接写 `detector._model` 私有属性 | `YOLODetector.unload()` 公共方法；`lifecycle.finally` 与 `pipeline.close()` 改调 `unload()` |
| 4 | 🟢 | `pipeline.py` | `FrameResult` 仅存首个 warning | `warning` → `warnings: List[WarningEvent]`，`process_frame` 收集全部（运维可追迹） |
| 5 | 🟢 | `pipeline.py` | `process_frame` 4 层嵌套 | 抽取 `_act_on_event()` 私有方法，单帧逻辑降到 1 层循环 |
| 6 | 🟢 | `config.py` / `__init__.py` | `build_family_contact` 暴露为公共 API | 改名 `_build_family_contact`（模块私有），移出 `runtime.__all__` |
| 7 | 🟢 | `tests/test_runtime.py` | `ManualClock` 无 `__call__` | 增加 `__call__()`，统一 `now_provider` 签名为 `Callable[[], datetime]` |

加固验收：`ruff` 全绿 / `compileall` OK / `pytest` **289 passed** / `scripts/run.py` EXIT=0（130 帧 / 9 事件 / 8 告警 / 8 指令 / 0 错误）。Demo 全链路行为一致（各场景稳定产出事件/感知/告警/指令）；具体感知计数因 YOLO 推理在 CPU 上的微小浮动在 10–12 间，属正常区间。

### 第二轮审查（2026-07-20 · 7 项：3 中 + 4 轻）

Owner 第二轮代码审查提出的改进项，已在本分支追加提交修复，**未改变 Demo 行为**（全链路计数稳定；YOLO 在 CPU 上微小浮动属正常区间）：

| # | 严重度 | 文件 | 问题 | 修复 |
|---|---|---|---|---|
| 1 | 🟡 | `runtime/config.py` | `demo_scenario_paths` 死代码（未导出/未调用） | 彻底删除该函数及已无用的 `Settings` 导入 |
| 2 | 🟡 | `runtime/lifecycle.py` | `from_settings(settings).detector` 浪费式构造完整 7 层流水线只为取 detector | 直接 `YOLODetector(...)` 按相同参数构造复用实例（demo 少构造 1 次完整流水线） |
| 3 | 🟡 | `tests/test_runtime.py` | `_build_pipeline` 传 `clock.now`（bound method）而非协议实例 | 统一改传 `clock`（ManualClock 已具 `__call__`，满足 `NowProvider`） |
| 4 | 🟢 | `runtime/pipeline.py` | `process_frame` `except Exception` 只记 `str(exc)`，丢 traceback | 改用 `log.exception(...)` 保留完整堆栈（仍按 AGENTS §2.5 不崩溃流水线） |
| 5 | 🟢 | `runtime/pipeline.py` | `DemoClock` 起点 `None` 静默回退墙钟，破坏 Demo 确定性 | 回退分支加 `log.warning("demo_clock.start_unset_fallback_wallclock")` 暴露配置缺失 |
| 6 | 🟢 | `analysis/event_builder.py` | `created_at=self._now()` 与事件时间语义混淆 | 补注释：创建时间（≈处理时间）；事件发生时间见 `leave_time` |
| 7 | 🟢 | `runtime/lifecycle.py` | 全部场景缺失时仅 INFO skip，看似"启动正常" | `_emit_demo_summary` 在 `scenarios_run==0` 时升级为 `log.warning("demo.all_scenarios_skipped")` |

第二轮验收：`ruff` 全绿 / `compileall` OK / `pytest` **289 passed** / `scripts/run.py` EXIT=0（130 帧 / 9 事件 / 8 告警 / 8 指令 / 0 错误）。

## 已知限制

1. **meet_walk_together 0 visitor events**: 人物仍在视频末尾被检测到（从未正式 "left"），
   语义正确但 Demo 展示效果弱。v1 可考虑 post-run flush 或截取子序列。
2. **Demo 阈值非生产值**: `detector_conf=0.10` / `long_duration_seconds=1.5` 仅适用于
   CAVIAR 短片 Demo；realtime 模式使用 production 默认。
3. **YOLO11n + CPU + 384×288 鱼眼**: 小目标检测置信度天然偏低，非算法缺陷。
   真实萤石摄像头（1080p 正视角）预期表现显著更好。

## 后续路径

- v1.0: 真实 MQTTPublisher（paho-mqtt）+ NotificationAdapter（短信网关）
- v1.1: 家属 App push（极光/友盟）
- v1.2: 社区工单系统 API
- v1.3: 持久化幂等（Redis/SQLite 替代 in-memory set）
- P2: Risk Digital Twin / 多端 App / 真实萤石 / Trust Layer / LLM 解释
