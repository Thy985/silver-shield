"""ADR-0034 Phase C · run_integration_validation 辅助函数单测（DoD C7，评审 D3）。

覆盖 ``_runtime_provenance`` 的运行时血缘构造：
- 缺失依赖 → ``n/a``（不因可选依赖缺失而崩）；
- PEP 440 构建后缀归一化（``1.0+abc`` → ``1.0``，跨 OS / 跨构建可比）；
- git 不可用 → ``code_version`` 回退（``home_perception.__version__`` / ``unknown``）。

ADR-0036 P0-2 扩展：覆盖 ``_build_runner`` 的场景级运行时覆盖（``meta.clock_start`` /
``meta.rule_overrides``）：
- 未知 ``rule_overrides`` 键 → ``ValueError``（fail-closed，对齐 ADR-0034
  "静默丢弃 = 失败"姿态，绝不静默忽略）；
- 已知键 + clock_start → 正确注入 ``IntegrationRunnerConfig.clock_start`` /
  ``ThresholdConfig``；
- 缺省场景 → 与历史行为逐字节一致（默认时钟 + 默认阈值）。
"""

from __future__ import annotations

import importlib.metadata
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

import run_integration_validation as riv

from home_perception.integration.loop.context import DEFAULT_CLOCK_START
from home_perception.validation.scenario.scenario import (
    CameraSpec,
    MetaSpec,
    Scenario,
)


def test_normalize_version_strips_build_suffix():
    """PEP 440 构建后缀剥离：跨 OS / 跨构建可比（对齐 harness.normalize_version）。"""
    assert riv._normalize_version("1.0+abc") == "1.0"
    assert riv._normalize_version("2.11.0+cpu") == "2.11.0"
    assert riv._normalize_version("2.11.0") == "2.11.0"


def test_runtime_provenance_missing_packages_are_na(monkeypatch):
    """依赖缺失 → 记 'n/a'，不因可选依赖缺失而崩。"""
    def fake_version(pkg: str) -> str:
        raise importlib.metadata.PackageNotFoundError(pkg)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)
    p = riv._runtime_provenance()
    assert p["numpy"] == "n/a"
    assert p["opencv-python"] == "n/a"
    assert p["torch"] == "n/a"
    assert p["python"]  # platform.python_version() 恒有值


def test_code_version_falls_back_when_git_unavailable(monkeypatch):
    """git 不可用 → 回退 home_perception.__version__ / unknown（不崩溃）。"""

    def fake_run(*args, **kwargs):
        raise OSError("git not available")

    monkeypatch.setattr(riv.subprocess, "run", fake_run)
    v = riv._code_version()
    assert v  # 非空：__version__ 或 "unknown"
    assert isinstance(v, str)


def test_code_version_prefers_git_hash(monkeypatch):
    """git 可用 → 短哈希优先（DoD C7：失败可溯源到提交）。"""

    class _Out:
        returncode = 0
        stdout = "abc1234\n"

    monkeypatch.setattr(riv.subprocess, "run", lambda *a, **k: _Out())
    assert riv._code_version() == "abc1234"


# ============================================================================
# ADR-0036 P0-2 · _build_runner 场景级运行时覆盖（meta.clock_start / rule_overrides）
# ============================================================================


def _make_scenario(**meta_kwargs) -> Scenario:
    """构造最小合法 Scenario（MetaSpec 可覆盖；camera 满足加载期必填）。"""
    meta_defaults = {
        "schema_version": "1.0",
        "scenario_id": "sw_ut_runner",
        "version": 1,
        "seed": 7,
        "duration_frames": 10,
    }
    meta_defaults.update(meta_kwargs)
    return Scenario(
        meta=MetaSpec(**meta_defaults),
        mode="detections",
        camera=CameraSpec(resolution=[384, 288], fps=2),
    )


def test_build_runner_rejects_unknown_rule_override_key():
    """``meta.rule_overrides`` 含未知阈值键 → ValueError（fail-closed，绝不静默忽略）。

    未知键只可能在装配边界被拒（``_build_runner`` 对照 ``ThresholdConfig`` 字段）；
    场景 schema 层不反向 import 分析包，故此处测装配层。
    """
    scn = _make_scenario(rule_overrides={"nonsense_key_xyz_999": 99})
    with pytest.raises(ValueError, match="nonsense_key_xyz_999"):
        riv._build_runner(scn)


def test_build_runner_applies_clock_start_and_rule_overrides():
    """声明 clock_start + 已知 rule_overrides → 注入 runner config / thresholds。"""
    scn = _make_scenario(
        clock_start=1784501970.0,  # 2026-07-19 22:59:30 UTC（与 high_risk fixture 同口径）
        rule_overrides={"long_duration_seconds": 15.0},
    )
    runner = riv._build_runner(scn)
    assert runner.config.clock_start == datetime(2026, 7, 19, 22, 59, 30, tzinfo=UTC)
    assert runner.thresholds is not None
    assert runner.thresholds.long_duration_seconds == 15.0


def test_build_runner_defaults_unchanged():
    """缺省场景（未声明 clock_start / rule_overrides）→ 默认时钟 + 默认阈值（向后兼容）。"""
    scn = _make_scenario()
    runner = riv._build_runner(scn)
    assert runner.config.clock_start == DEFAULT_CLOCK_START
    assert runner.thresholds is None


def test_load_scenario_rejects_nonpositive_clock_start(tmp_path):
    """``meta.clock_start <= 0`` → 加载期 ValueError（fail-closed，场景结构校验）。"""
    from home_perception.validation.scenario import load_scenario

    p = tmp_path / "bad_clock.yaml"
    p.write_text(
        """meta:
  schema_version: "1.0"
  scenario_id: sw_ut_bad_clock
  version: 1
  seed: 7
  duration_frames: 10
  clock_start: -1.0
mode: detections
camera:
  resolution: [384, 288]
  fps: 2
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="clock_start"):
        load_scenario(p)
