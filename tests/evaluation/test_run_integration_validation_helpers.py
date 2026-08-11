"""ADR-0034 Phase C · run_integration_validation 辅助函数单测（DoD C7，评审 D3）。

覆盖 ``_runtime_provenance`` 的运行时血缘构造：
- 缺失依赖 → ``n/a``（不因可选依赖缺失而崩）；
- PEP 440 构建后缀归一化（``1.0+abc`` → ``1.0``，跨 OS / 跨构建可比）；
- git 不可用 → ``code_version`` 回退（``home_perception.__version__`` / ``unknown``）。
"""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

import run_integration_validation as riv


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
