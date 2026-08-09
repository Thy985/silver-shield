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

from .fingerprint_fields import FINGERPRINT_COMPONENT_FIELDS
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


def _strip_build_suffix(version: str) -> str:
    """归一化构建后缀（D4，跨 OS / 跨构建可比）。

    pip 在不同平台装出的同一语义版本带不同构建后缀：Windows CUDA 为
    ``2.11.0+cu130``、Linux CPU 为 ``2.11.0+cpu``、本地开发可能为 ``2.11.0+local``。
    这些后缀差异不代表依赖语义变化，却会让 ``runtime_dependencies`` 守恒(4/7) 把
    baseline（开发机生成）与 candidate（CI ubuntu 生成）判为不一致 → 每个 PR 误红。
    归一为 ``MAJOR.MINOR.PATCH`` 后，跨 OS / 跨构建可比，仍保留真实主/次/补丁级升级检测。
    """
    return version.split("+", 1)[0]


def _runtime_versions() -> dict[str, str]:
    """锁版本集合（D4）。

    复用 ADR-0032 ``validation.fingerprint._runtime_versions`` 取 numpy / opencv 版本
    （避免重复实现，review 1.5），键名适配为本 harness 的 ``numpy`` / ``opencv`` 风格，
    并补充可选 ``torch`` 版本（缺失记为 ``n/a``）。
    """
    from home_perception.validation.fingerprint import _runtime_versions as _imp_rt

    vr = _imp_rt()  # {numpy_version, opencv_version}
    deps: dict[str, str] = {
        "numpy": _strip_build_suffix(vr["numpy_version"]),
        "opencv": _strip_build_suffix(vr["opencv_version"]),
    }
    try:  # torch 为可选依赖，缺失不报错
        import torch

        deps["torch"] = _strip_build_suffix(torch.__version__)
    except ImportError:
        deps["torch"] = "n/a"
    return deps


def _generator_config_fingerprint(scenario: Scenario) -> str:
    """场景**集级** generator 配置指纹（D4，seed-independent）。

    语义区分（review 1.4）：
    - **集级指纹（本函数）**：仅由配置成分（``schema_version`` / ``renderer`` /
      ``code_version`` / numpy / opencv）决定，**剔除 per-scenario ``seed``**——代表"这批场景
      用什么生成器配置产生"，同一配置下不同 seed 产出**相同集级指纹**；
    - **单产物指纹**（``RunResult.fingerprint`` / ADR-0032 ``compute_fingerprint``）：含 seed，
      标识"某一次具体生成产物"。

    复用 ADR-0032 ``fingerprint_components`` 取成分后 ``pop("seed")`` 再哈希，即得到上述集级语义。
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
    """计算 ``harness_fingerprint``（D4 三元组，fail-closed）。

    成分集合由 ``FINGERPRINT_COMPONENT_FIELDS``（fingerprint_fields.py，Medium 9）单一
    来源驱动：先做**漂移守卫**（本函数入参与常量不一致即报错，防未来只改一处），再逐成分
    ``if not X`` 兜底（空字符串 / 空 dict 同判缺失）。
    """
    values: dict[str, Any] = {
        "scenario_set_id": scenario_set_id,
        "code_version": code_version,
        "generator_fingerprint": generator_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "model_fingerprint": model_fingerprint,
        "runtime_dependencies": runtime_dependencies,
    }
    # 漂移守卫：常量与入参集合必须完全一致（防新增成分时只改 signature 或只改常量）
    if set(values) != set(FINGERPRINT_COMPONENT_FIELDS):
        raise BenchmarkProvenanceError(
            "compute_harness_fingerprint 入参与 FINGERPRINT_COMPONENT_FIELDS 不一致"
            f"（实现={sorted(values)}，常量={sorted(FINGERPRINT_COMPONENT_FIELDS)}）"
        )
    for name in FINGERPRINT_COMPONENT_FIELDS:
        if not values[name]:
            raise BenchmarkProvenanceError(f"{name} 不能为空")
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

    注意（review 2.2）：``run()`` 仅对**代表场景 ``scenarios[0]``** 构造 pipeline 并抽取
    policy 指纹。Phase 1 假设**场景集共享单一 policy**（即集内所有场景用同一 decision policy），
    故抽取首个即代表全集；若未来引入 per-scenario policy，此处须改为聚合 / 校验一致性。
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
        """跑通 Scenario → Harness → ScenarioScore → BenchmarkReport（D1 最小闭环）。

        集合同源假设（review 2.1）：场景集须共享**单一 schema_version** 与**单一 mode**，
        因为 ``harness_fingerprint`` 是**集级指纹**（由代表场景 ``scenarios[0]`` 抽取），混源
        会让指纹静默折叠到首个场景。此处显式校验，不一致即 fail-closed 拒绝。
        """
        if not scenarios:
            raise BenchmarkProvenanceError("scenarios 为空，无法产出可复现报告")

        # 集合同源校验（fail-closed）：单一 schema_version + 单一 mode
        schema_versions = {s.meta.schema_version for s in scenarios}
        if len(schema_versions) > 1:
            raise BenchmarkProvenanceError(
                f"场景集 schema_version 不一致（{sorted(schema_versions)}）；"
                "harness_fingerprint 为集级指纹，要求集合同源（单一 schema_version）"
            )
        modes = {s.mode for s in scenarios}
        if len(modes) > 1:
            raise BenchmarkProvenanceError(
                f"场景集 mode 不一致（{sorted(modes)}）；集级指纹要求单一 mode"
            )

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
