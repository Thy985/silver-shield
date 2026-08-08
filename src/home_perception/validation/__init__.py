"""场景仿真层（Perception Validation Infrastructure · ADR-0032）。

本包属**感知测试基础设施**，不是生产仿真运行时。它把"声明式场景"编译为
pipeline 的两类上游输入（detections / frames），并执行 + 对照期望校验，
为 ADR-0033 Benchmark Harness 提供可复现、隐私安全的输入源。

子包 / 模块：
- ``scenario``     ：``Scenario`` schema + YAML 加载 + ``ScenarioCompiler``（YAML → ``SyntheticInput``）
- ``simulation``   ：``generator``（detections 发射器）+ ``renderer``（frames 渲染器）
- ``runner``       ：``ScenarioRunner``（输入 → pipeline → ``RunResult``）+ ``ScenarioValidator``
- ``fixtures``     ：声明式 scenario YAML（D8 Scenario Registry 资产）
- ``demo_adapter`` ：合成帧源 → ``silver_demo`` 网关的适配层（Slice E，**刻意不在此急切导入**，
  由组装层显式 import 并注册，见下）

设计铁律（与音频 ``audio/tts`` 对称，但**不反向依赖**业务规则层 ``analysis/rule_engine``）：
- generator 只产上游输入（frames / ``Detection``），不调用 ``RuleEngine``、不替下游算期望；
- 确定性是契约（D2）：同 Scenario + 同代码/numpy/opencv 版本 → 字节级可复现；
- 零生产行为变化（D3）：经 ADR-0014 L2 既有接缝（``Detector``）与 demo 侧
  ``register_frame_source`` **依赖倒置钩子**注入。注意这里与 ADR-0032 原文
  「``build_frame_source`` 直接调 ``render_frames``」有意偏离——直接调用会让
  ``silver_demo`` import 本包，撞上 ADR-0015 §5 冻结 import 白名单；改用钩子后
  白名单无需放宽，且不注册时 demo 行为与今天完全一致。
"""

from __future__ import annotations

from .fingerprint import RENDERER_VERSION, compute_fingerprint, fingerprint_components
from .runner import (
    RunResult,
    ScenarioRunner,
    ScenarioValidator,
    SyntheticInput,
    ValidationResult,
)
from .scenario import (
    ACTOR_TYPES,
    KNOWN_SCHEMA_VERSIONS,
    ActorSpec,
    CameraSpec,
    EnvironmentSpec,
    EventGroundTruth,
    ExpectsSpec,
    MetaSpec,
    RegionSpec,
    Scenario,
    StaticObjectSpec,
    TrackKeyframe,
    ensure_synthesizable,
    load_scenario,
    load_scenarios_dir,
    validate_scenario_structure,
)
from .scenario.compiler import ScenarioCompiler
from .simulation.generator import (
    ACTOR_TYPE_TO_CLASS,
    SYNTHETIC_CONFIDENCE,
    ScenarioDetectionDetector,
    emit_detections,
    export_detections_json,
    interpolate_actor_box,
)
from .simulation.renderer import export_mp4, render_frames

__all__ = [
    "ACTOR_TYPES",
    "ACTOR_TYPE_TO_CLASS",
    "KNOWN_SCHEMA_VERSIONS",
    "RENDERER_VERSION",
    "SYNTHETIC_CONFIDENCE",
    "ActorSpec",
    "CameraSpec",
    "EnvironmentSpec",
    "EventGroundTruth",
    "ExpectsSpec",
    "MetaSpec",
    "RegionSpec",
    "RunResult",
    "Scenario",
    "ScenarioCompiler",
    "ScenarioDetectionDetector",
    "ScenarioRunner",
    "ScenarioValidator",
    "StaticObjectSpec",
    "SyntheticInput",
    "TrackKeyframe",
    "ValidationResult",
    "compute_fingerprint",
    "emit_detections",
    "ensure_synthesizable",
    "export_detections_json",
    "export_mp4",
    "fingerprint_components",
    "interpolate_actor_box",
    "load_scenario",
    "load_scenarios_dir",
    "render_frames",
    "validate_scenario_structure",
]
