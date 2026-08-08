"""ADR-0032 契约测试（Slice A + 部分 T 系）。

测试文件以 ``test_validation_`` 命名（评审 T1），避免与 ADR-0033 Benchmark Harness 的
scenario 测试在 pytest 命名空间冲突。所有不变式测试名带 ``adr0032`` 前缀。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml
from _ast_contract import assert_no_dependency

from home_perception.validation import (
    ACTOR_TYPES,
    KNOWN_SCHEMA_VERSIONS,
    Scenario,
    ScenarioCompiler,
    compute_fingerprint,
    fingerprint_components,
    load_scenario,
    simulation,  # noqa: F401  (确保模块可导入)
    validate_scenario_structure,
)
from home_perception.validation.scenario.scenario import CameraSpec, MetaSpec
from home_perception.validation.simulation import generator, renderer

FIXTURE_DIR = __import__("home_perception.validation", fromlist=["__file__"]).__file__
import pathlib

FIX = pathlib.Path(FIXTURE_DIR).parent / "fixtures" / "scenarios"


# ============================================================================
# Slice A：schema + 加载器
# ============================================================================


def _minimal_meta(**overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "scenario_id": "mini",
        "version": 1,
        "seed": 1,
        "duration_frames": 10,
    }
    base.update(overrides)
    return base


def test_scenario_roundtrip(tmp_path):
    """加载 → 重写出 → 再加载，语义一致。"""
    data = {
        "meta": _minimal_meta(),
        "mode": "detections",
        "camera": {"resolution": [384, 288], "fps": 2},
        "actors": [
            {
                "id": "v1",
                "actor_type": "human",
                "tracks": [
                    {"frame": 1, "pos": [100.0, 100.0], "size": [40.0, 120.0]},
                    {"frame": 5, "pos": [200.0, 100.0], "size": [40.0, 120.0]},
                ],
            }
        ],
        "expects": {"emitted_event_types": ["visit_normal"], "min_risk_level": "LOW"},
    }
    p = tmp_path / "mini.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    scn = load_scenario(p)
    assert scn.meta.scenario_id == "mini"
    assert scn.actors[0].actor_type == "human"
    assert scn.expects.min_risk_level == "LOW"


def test_scenario_validation_errors():
    """加载期 fail-closed：缺身份字段 / 帧序倒挂 / 越界 / 未知 schema_version。"""
    # 枚举契约（fail-closed 的判据本身也须钉死，防止悄悄放宽）
    assert KNOWN_SCHEMA_VERSIONS == frozenset({"1.0"})
    assert ACTOR_TYPES == ("human", "vehicle", "pet", "object")
    # 缺 scenario_id（pydantic 必填）
    with pytest.raises(ValueError):
        Scenario(
            meta=MetaSpec(schema_version="1.0", version=1),
            camera=CameraSpec(resolution=[384, 288]),
        )
    # 未知 schema_version
    scn = Scenario(
        meta=MetaSpec(schema_version="9.9", scenario_id="x", version=1),
        camera=CameraSpec(resolution=[384, 288]),
    )
    with pytest.raises(ValueError):
        validate_scenario_structure(scn)
    # 帧序倒挂
    bad = Scenario(
        meta=MetaSpec(schema_version="1.0", scenario_id="x", version=1),
        camera=CameraSpec(resolution=[384, 288]),
        actors=[
            {
                "id": "v1",
                "actor_type": "human",
                "tracks": [
                    {"frame": 5, "pos": [100.0, 100.0], "size": [40.0, 120.0]},
                    {"frame": 3, "pos": [200.0, 100.0], "size": [40.0, 120.0]},
                ],
            }
        ],
    )
    with pytest.raises(ValueError):
        validate_scenario_structure(bad)
    # 越界（duration_frames=10，frame=10 越界）
    oob = Scenario(
        meta=MetaSpec(schema_version="1.0", scenario_id="x", version=1, duration_frames=10),
        camera=CameraSpec(resolution=[384, 288]),
        actors=[
            {
                "id": "v1",
                "actor_type": "human",
                "tracks": [{"frame": 10, "pos": [100.0, 100.0], "size": [40.0, 120.0]}],
            }
        ],
    )
    with pytest.raises(ValueError):
        validate_scenario_structure(oob)
    # 非法 actor_type
    bad_type = Scenario(
        meta=MetaSpec(schema_version="1.0", scenario_id="x", version=1),
        camera=CameraSpec(resolution=[384, 288]),
        actors=[{"id": "v1", "actor_type": "ghost", "tracks": []}],
    )
    with pytest.raises(ValueError):
        validate_scenario_structure(bad_type)


# ============================================================================
# T1 确定性（跨次编译一致）
# ============================================================================


def test_adr0032_t1_deterministic_reproducible():
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    compiler = ScenarioCompiler()

    det_a = compiler.compile(scn, mode="detections")
    det_b = compiler.compile(scn, mode="detections")
    assert det_a.n_frames == det_b.n_frames == scn.meta.duration_frames
    # 逐帧 Detection 相等（顺序敏感）。走公开 ``detect()`` 回放，不读私有成员。
    for _ in range(det_a.n_frames):
        assert det_a.detector.detect(None).detections == (det_b.detector.detect(None).detections)

    # frames 通道逐帧 np.array_equal
    fr_a = compiler.compile(scn, mode="frames")
    fr_b = compiler.compile(scn, mode="frames")
    import numpy as np

    for fa, fb in zip(fr_a.frames, fr_b.frames):
        assert np.array_equal(fa, fb)


_PROBE = """
import json, os, sys
sys.path.insert(0, os.path.join(r"{root}", "src"))
from home_perception.validation import ScenarioCompiler, load_scenario, emit_detections

scn = load_scenario(r"{yaml}")
per_frame = emit_detections(scn)
synth = ScenarioCompiler().compile(scn, mode="detections")
print(json.dumps({{
    "track_ids": sorted({{d.track_id for f in per_frame for d in f}}),
    "digest": [
        [[d.class_id, d.class_name, d.confidence, d.bbox, d.track_id] for d in f]
        for f in per_frame
    ],
    "fingerprint": synth.fingerprint,
}}))
"""


def test_adr0032_t1_deterministic_across_processes():
    """跨进程确定性：不同 ``PYTHONHASHSEED`` 下产物必须字节级一致。

    这条直接检验"track_id 用 sorted(actor.id) 序位而非 ``hash()``"的设计决策——
    若改回 ``hash()``，两次子进程的 track_id 会分歧，本测试失败（变异可检出）。
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    yaml_path = FIX / "perception" / "torchfree_visit.yaml"
    code = _PROBE.format(root=str(root), yaml=str(yaml_path))

    outputs = []
    for seed in ("0", "1", "424242"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            check=False,  # 自行断言 returncode，以便把 stderr 带进失败信息
        )
        assert proc.returncode == 0, f"PYTHONHASHSEED={seed} 子进程失败：{proc.stderr}"
        outputs.append(json.loads(proc.stdout))

    first = outputs[0]
    assert first["track_ids"], "场景未产出任何 track_id，确定性断言失效"
    for other in outputs[1:]:
        assert other["track_ids"] == first["track_ids"]
        assert other["digest"] == first["digest"]
        assert other["fingerprint"] == first["fingerprint"]


# ============================================================================
# T2 隐私（无真实媒体 / 抽象拓扑，不泄露真实户型）
# ============================================================================


def test_adr0032_t2_no_real_media_or_topology_leak():
    import numpy as np

    scn = load_scenario(FIX / "regression" / "stranger_repeat.yaml")
    frames = renderer.render_frames(scn)
    assert isinstance(frames, list)
    assert all(f.shape == (288, 384, 3) for f in frames)
    assert all(f.dtype == np.uint8 for f in frames)
    # regions 是抽象分类标签，非真实户型坐标（T2/S1）
    assert set(scn.environment.regions.keys()) == {"living_room"}


# ============================================================================
# T3 无真实媒体依赖（generator 不读 VideoCapture / data/demo）
# ============================================================================


def test_adr0032_t3_no_external_media():
    # AST 校验：不打开真实视频、不引用真实媒体目录（字面量层面也查，排除文档字符串）
    for mod in (generator, renderer):
        assert_no_dependency(
            mod,
            forbidden_names=["VideoCapture", "imread"],
            forbidden_literal_substrings=["data/demo", "data\\demo"],
        )
    # 不读外部文件也能生成（纯内存）
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    dets = generator.emit_detections(scn)
    assert len(dets) == scn.meta.duration_frames
    frames = renderer.render_frames(scn)
    assert len(frames) == scn.meta.duration_frames


# ============================================================================
# T11 产出血缘可溯源（generator.fingerprint）
# ============================================================================


def test_adr0032_t11_synthesized_input_carries_fingerprint():
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    synth = ScenarioCompiler().compile(scn, mode="detections")
    fp = synth.fingerprint
    assert isinstance(fp, str) and len(fp) == 64  # sha256 hex
    assert all(c in "0123456789abcdef" for c in fp)
    # 确定性：两次编译指纹一致
    fp2 = ScenarioCompiler().compile(scn, mode="detections").fingerprint
    assert fp == fp2
    # 组成要素含渲染可复现性版本（不含设备/家庭/用户标识，评审 S2）
    comp = fingerprint_components(
        schema_version=scn.meta.schema_version,
        renderer_version="1.0.0",
        seed=scn.meta.seed,
        code_version="0.1.0",
    )
    assert set(comp.keys()) == {
        "schema_version",
        "renderer_version",
        "seed",
        "code_version",
        "numpy_version",
        "opencv_version",
    }
    # S2：指纹要素中不得混入设备 / 家庭 / 用户标识。
    # （不能断言 "device" not in fp —— fp 是 sha256 十六进制串，该断言恒真、无检出力。）
    identity_markers = ("device", "home_id", "elder", "user", "household")
    for key, value in comp.items():
        blob = f"{key}={value}".lower()
        for marker in identity_markers:
            assert marker not in blob, f"指纹要素 {key!r} 疑似含身份标识 {marker!r}"
    # 改变 seed → 指纹变化（fail-closed：seed 纳入哈希）
    other = Scenario(
        meta=MetaSpec(
            schema_version="1.0",
            scenario_id="mini",
            version=1,
            seed=999,
            duration_frames=10,
        ),
        camera=CameraSpec(resolution=[384, 288]),
    )
    fp_other = compute_fingerprint(
        schema_version=other.meta.schema_version,
        renderer_version="1.0.0",
        seed=other.meta.seed,
        code_version="0.1.0",
    )
    assert other.meta.seed != scn.meta.seed  # 前提：seed 确实不同
    assert fp_other != fp
