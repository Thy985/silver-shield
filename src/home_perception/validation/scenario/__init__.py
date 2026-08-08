"""ADR-0032 ``validation/scenario`` 子包：``Scenario`` schema + 加载 + ``ScenarioCompiler``。"""

from __future__ import annotations

from .compiler import ScenarioCompiler
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

__all__ = [
    "ACTOR_TYPES",
    "KNOWN_SCHEMA_VERSIONS",
    "ActorSpec",
    "CameraSpec",
    "EnvironmentSpec",
    "EventGroundTruth",
    "ExpectsSpec",
    "MetaSpec",
    "RegionSpec",
    "Scenario",
    "ScenarioCompiler",
    "StaticObjectSpec",
    "TrackKeyframe",
    "ensure_synthesizable",
    "load_scenario",
    "load_scenarios_dir",
    "validate_scenario_structure",
]
