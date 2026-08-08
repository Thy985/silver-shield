"""ADR-0032 D7：``generator.fingerprint``（产出血缘指纹，类比 ADR-0031 D5 ``policy.fingerprint``）。

对 ``{schema_version, renderer_version, seed, code_version, numpy_version, opencv_version}``
的稳定哈希。下游 ADR-0033 Benchmark Harness 据此区分"v1 渲染结果"与"v2 渲染结果"
（及不同 numpy/opencv 基线产物），否则跨版本回归不可解释。

**隐私边界（评审 S2）**：指纹仅由**渲染产物可复现性要素**构成，**不含任何设备 ID /
家庭 ID / 用户标识**——它是"渲染产物指纹"而非"使用记录指纹"。

**fail-closed**：缺字段即报错（写时聚合，缺键抛 ``KeyError``），不静默。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# 本 ADR 渲染器版本（与 schema_version 解耦：schema 不变也能升级渲染基元）。
RENDERER_VERSION = "1.0.0"


def _runtime_versions() -> dict[str, str]:
    """取 numpy / opencv 版本（CI 在 numpy>=1.24 / opencv-python>=4.8 锁版本基线下断言）。"""
    import cv2
    import numpy as np

    return {
        "numpy_version": np.__version__,
        "opencv_version": cv2.__version__,
    }


def compute_fingerprint(
    *,
    schema_version: str,
    renderer_version: str,
    seed: int,
    code_version: str,
) -> str:
    """计算渲染产物指纹（sha256 hex）。

    入参缺任意一项都将导致下方字典缺键 → 抛 ``KeyError``（fail-closed，不静默降级）。
    """
    parts: dict[str, Any] = {
        "schema_version": schema_version,
        "renderer_version": renderer_version,
        "seed": seed,
        "code_version": code_version,
    }
    parts.update(_runtime_versions())
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint_components(
    *,
    schema_version: str,
    renderer_version: str,
    seed: int,
    code_version: str,
) -> dict[str, str]:
    """返回指纹的组成要素（便于审计 / 调试，不直接用于比较）。"""
    parts: dict[str, str] = {
        "schema_version": schema_version,
        "renderer_version": renderer_version,
        "seed": str(seed),
        "code_version": code_version,
    }
    parts.update(_runtime_versions())
    return parts
