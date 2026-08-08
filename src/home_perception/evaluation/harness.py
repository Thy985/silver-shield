"""ADR-0033 Phase 1：``BenchmarkHarness``（最小闭环编排，D1 / D2 / D4）。

编排 ADR-0032 三组件 → 单场景打分 → 跨场景聚合：
``ScenarioCompiler`` → ``ScenarioRunner.run`` → ``ScenarioValidator.validate`` →
``build_scenario_score`` → ``BenchmarkReport.aggregate``。只编排、不重写（D2）。

``build_pipeline`` 是注入接缝：每个场景的 ``SyntheticInput`` 携带专属 ``detector``
（detections 通道），由调用方构造注入了对应 detector 的 pipeline（与 ADR-0032
``test_validation_runner.py`` 的 ``build_torchfree_pipeline(synth.detector, ...)`` 同范式）。
本模块**不** import ``runtime`` / ``analysis`` 重链的装配逻辑（仅消费 ``validation`` 与
``analysis.decision_trace`` / ``analysis.warning`` 的纯函数），保持轻量、守护 D8 零行为变化。

指纹（D4 三元组）：
``harness_fingerprint = sha256(canonical({scenario_set_id, code_version,
generator_fingerprint, policy_fingerprint, model_fingerprint, runtime_dependencies}))``
缺任一成分即 fail-closed 报错（不静默）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from home_perception.analysis.decision_trace import compute_policy_fingerprint
from home_perception.validation import (
    ScenarioCompiler,
    ScenarioRunner,
    ScenarioValidator,
)
from home_perception.validation.fingerprint import (
    RENDERER_VERSION,
    fingerprint_components,
)
from home_perception.validation.scenario.scenario import Scenario
from home_perception.validation.synthetic_input import SyntheticInput

from .metrics import build_scenario_score
from .report import BenchmarkReport


class BenchmarkProvenanceError(Exception):
    """指纹成分缺失（fail-closed，D4）。"""


def _resolve_code_version() -> str:
    """解析 code_version（fail-closed）：优先 git 短哈希，回退 home_perception.__version__。

    二者皆不可得则报错（不静默降级）。
    """
    git_hash = ""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parents[2],
        )
        if out.returncode == 0:
            git_hash = out.stdout.strip()
    except OSError:
        git_hash = ""  # git 不可用走回退
    if git_hash:
        return git_hash
    try:
        import home_perception

        return home_perception.__version__
    except ImportError as exc:  # pragma: no cover
        raise BenchmarkProvenanceError(
            "无法解析 code_version（git 与 home_perception.__version__ 均不可用）"
        ) from exc


def _runtime_versions() -> dict[str, str]:
    """锁版本集合（替代原 numpy_version / opencv_version 散字段，D4）。"""
    import cv2
    import numpy as np

    deps: dict[str, str] = {
        "numpy": np.__version__,
        "opencv": cv2.__version__,
    }
    try:  # torch 为可选依赖，缺失不报错
        import torch

        deps["torch"] = torch.__version__
    except ImportError:
        deps["torch"] = "n/a"
    return deps


def _generator_config_fingerprint(scenario: Scenario) -> str:
    """场景集共享的 generator 配置指纹（D4，剔除 per-scenario seed 以保证集级一致）。

    复用 ADR-0032 ``fingerprint_components``，仅取配置成分（schema_version / renderer /
    code_version / numpy / opencv）重哈希；seed 随场景变化、不进入集级指纹（与
    ``RunResult.fingerprint`` 同源但 seed-independent，代表"生成器配置"而非"单次生成产物"）。
    """
    import home_perception

    comps = fingerprint_components(
        schema_version=scenario.meta.schema_version,
        renderer_version=RENDERER_VERSION,
        seed=scenario.meta.seed or 0,
        code_version=home_perception.__version__,
    )
    comps.pop("seed", None)
    canonical = json.dumps(comps, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_model_fingerprint(mode: str) -> dict[str, str]:
    """组件级模型指纹（D6，覆盖非 git 管理的权重 / 版本）。

    Phase 1 默认走 ADR-0032 ``detections`` 零模型通道：detector 为确定性回放器、无权重文件。
    启用 ``frames`` + 真实 detector（opt-in）时由调用方注入权重哈希。
    """
    if mode == "detections":
        return {
            "detector": "scenario-detection-detector",
            "tracker": "visitor_tracker",
            "event_extractor": "feature_extractor",
        }
    return {
        "detector": "unknown-real-detector",
        "tracker": "visitor_tracker",
        "event_extractor": "feature_extractor",
    }


def compute_harness_fingerprint(
    *,
    scenario_set_id: str,
    code_version: str,
    generator_fingerprint: str,
    policy_fingerprint: str,
    model_fingerprint: Mapping[str, str],
    runtime_dependencies: Mapping[str, str],
) -> str:
    """计算 ``harness_fingerprint``（D4 三元组，fail-closed）。"""
    if not scenario_set_id:
        raise BenchmarkProvenanceError("scenario_set_id 不能为空")
    if not code_version:
        raise BenchmarkProvenanceError("code_version 不能为空")
    if not generator_fingerprint:
        raise BenchmarkProvenanceError("generator_fingerprint 不能为空")
    if not policy_fingerprint:
        raise BenchmarkProvenanceError("policy_fingerprint 不能为空")
    if not model_fingerprint:
        raise BenchmarkProvenanceError("model_fingerprint 不能为空")
    if not runtime_dependencies:
        raise BenchmarkProvenanceError("runtime_dependencies 不能为空")
    parts: dict[str, Any] = {
        "scenario_set_id": scenario_set_id,
        "code_version": code_version,
        "generator_fingerprint": generator_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "model_fingerprint": dict(model_fingerprint),
        "runtime_dependencies": dict(runtime_dependencies),
    }
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_policy_fingerprint(pipeline: Any) -> str:
    """从 pipeline 抽取 policy.fingerprint（fail-closed，D4）。

    鸭子类型经 ``pipeline.decision_engine.policy.routing_table``（与 ADR-0031
    ``compute_policy_fingerprint`` 同输入）。抽不到即报错（不静默）。
    """
    try:
        engine = pipeline.decision_engine
        policy = engine.policy
        routing_table = policy.routing_table
    except AttributeError as exc:
        raise BenchmarkProvenanceError(
            "无法从 pipeline 抽取 policy.routing_table 以计算 policy_fingerprint；"
            "请显式传入 policy_fingerprint 参数（fail-closed）"
        ) from exc
    return compute_policy_fingerprint(routing_table)


class BenchmarkHarness:
    """最小闭环编排器（D1 / D2）。"""

    def __init__(
        self,
        compiler: ScenarioCompiler | None = None,
        runner: ScenarioRunner | None = None,
        validator: ScenarioValidator | None = None,
    ) -> None:
        self._compiler = compiler or ScenarioCompiler()
        self._runner = runner or ScenarioRunner()
        self._validator = validator or ScenarioValidator()

    def run(
        self,
        scenarios: Sequence[Scenario],
        build_pipeline: Callable[[SyntheticInput], Any],
        *,
        scenario_set_id: str,
        frame_interval_s: float = 0.5,
        code_version: str | None = None,
        policy_fingerprint: str | None = None,
        model_fingerprint: Mapping[str, str] | None = None,
        runtime_dependencies: Mapping[str, str] | None = None,
        generated_at: str = "",
    ) -> BenchmarkReport:
        """跑通 Scenario → Harness → ScenarioScore → BenchmarkReport（D1 最小闭环）。"""
        if not scenarios:
            raise BenchmarkProvenanceError("scenarios 为空，无法产出可复现报告")

        # —— 指纹成分收集（D4，fail-closed）——
        rep = scenarios[0]
        rep_synth = self._compiler.compile(rep, mode=rep.mode)
        rep_pipeline = build_pipeline(rep_synth)  # 构建一次以抽取指纹成分
        generator_fingerprint = _generator_config_fingerprint(rep)
        resolved_code_version = code_version or _resolve_code_version()
        resolved_policy_fingerprint = (
            policy_fingerprint
            if policy_fingerprint is not None
            else _resolve_policy_fingerprint(rep_pipeline)
        )
        resolved_model_fingerprint = (
            model_fingerprint if model_fingerprint is not None else default_model_fingerprint(rep.mode)
        )
        resolved_runtime = (
            runtime_dependencies if runtime_dependencies is not None else _runtime_versions()
        )
        harness_fingerprint = compute_harness_fingerprint(
            scenario_set_id=scenario_set_id,
            code_version=resolved_code_version,
            generator_fingerprint=generator_fingerprint,
            policy_fingerprint=resolved_policy_fingerprint,
            model_fingerprint=resolved_model_fingerprint,
            runtime_dependencies=resolved_runtime,
        )
        provenance = {
            "scenario_set_id": scenario_set_id,
            "code_version": resolved_code_version,
            "generator_fingerprint": generator_fingerprint,
            "policy_fingerprint": resolved_policy_fingerprint,
            "model_fingerprint": dict(resolved_model_fingerprint),
            "runtime_dependencies": dict(resolved_runtime),
        }

        # —— 逐场景打分（D2 编排 ADR-0032 三组件）——
        scores = []
        for scn in scenarios:
            synth = self._compiler.compile(scn, mode=scn.mode)
            pipeline = build_pipeline(synth)
            run_result = self._runner.run(synth, pipeline, frame_interval_s=frame_interval_s)
            validation_result = self._validator.validate(run_result, scn)
            scores.append(build_scenario_score(scn, run_result, validation_result))

        return BenchmarkReport.aggregate(
            scenario_set_id=scenario_set_id,
            harness_fingerprint=harness_fingerprint,
            scores=scores,
            generated_at=generated_at,
            provenance=provenance,
        )


__all__ = [
    "BenchmarkHarness",
    "BenchmarkProvenanceError",
    "compute_harness_fingerprint",
    "default_model_fingerprint",
]
