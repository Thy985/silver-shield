"""音频合成基础设施（Audio Synthetic Infrastructure / TTS Fixture Generation）。

> 本包属 Testing / Evaluation Infrastructure，不是 Audio Perception Chain 的一环。
> 包含：TTS Provider 抽象（provider）、可组合增强效果库（effects）、
> 场景驱动生成器（generator，读 scenario.yaml）。默认被 ``scripts/gen_audio_fixtures.py`` 使用；
> 不被音频感知运行时 import（保持运行时零 TTS 依赖）。
"""

from .effects import EFFECTS, apply_effects
from .generator import Scenario, generate_all, generate_scenario, load_scenarios
from .provider import AzureTTSProvider, EdgeTTSProvider, TTSProvider
from .scenario_runner import (
    PerceptionScenario,
    ScenarioRun,
    ValidationResult,
    load_scenario,
    load_scenarios_dir,
    run,
    run_scenario,
    synthesize,
    validate_scenario,
)

__all__ = [
    "EFFECTS",
    "AzureTTSProvider",
    "EdgeTTSProvider",
    "PerceptionScenario",
    "Scenario",
    "ScenarioRun",
    "TTSProvider",
    "ValidationResult",
    "apply_effects",
    "generate_all",
    "generate_scenario",
    "load_scenario",
    "load_scenarios",
    "load_scenarios_dir",
    "run",
    "run_scenario",
    "synthesize",
    "validate_scenario",
]
