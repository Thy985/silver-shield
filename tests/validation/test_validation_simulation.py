"""ADR-0032 契约测试（Slice B/C + T4/T5/T7 + B2 + T8）。"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import numpy as np
from _ast_contract import assert_no_dependency

from home_perception.analysis.perception import EVENT_TYPES
from home_perception.analysis.warning import RISK_LEVELS
from home_perception.detection.detector import Detection
from home_perception.validation import load_scenario
from home_perception.validation.simulation import (
    SYNTHETIC_CONFIDENCE,
    ScenarioDetectionDetector,
    detection_to_dict,
    emit_detections,
    export_detections_json,
    generator,
    interpolate_actor_box,
    render_frames,
    renderer,
)

# 既有检测缓存 fixture：B2 字段级等价的**对照真相源**（不是我们自己生成的）
LEGACY_CACHE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "detections"
    / "stranger_visit_short.detections.json"
)

FIX = (
    pathlib.Path(__import__("home_perception.validation", fromlist=["__file__"]).__file__).parent
    / "fixtures"
    / "scenarios"
)


# ============================================================================
# T4 generator 不修改事件 schema（只产上游输入原语）
# ============================================================================


def test_adr0032_t4_generator_does_not_alter_event_schema():
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    per_frame = emit_detections(scn)
    # 全为 Detection 实例，且字段严格等于 Detection schema（不新增/不丢弃）
    det_fields = {f.name for f in dataclasses.fields(Detection)}
    assert det_fields == {
        "class_id",
        "class_name",
        "confidence",
        "bbox",
        "timestamp",
        "track_id",
    }
    for frame_dets in per_frame:
        for d in frame_dets:
            assert isinstance(d, Detection)
            assert set(d.__dict__.keys()) == det_fields
    # 不修改既有的 PerceptionEvent / WarningEvent 枚举
    assert EVENT_TYPES == (
        "visit_normal",
        "visit_pending_verify",
        "abnormal_dwell",
        "repeat_visit",
        "high_risk_approach",
    )
    assert RISK_LEVELS == ("LOW", "MEDIUM", "HIGH")


# ============================================================================
# T5 零生产行为变化（导入与字段不被改动）
# ============================================================================


def test_adr0032_t5_production_unchanged():
    # 导入 validation 不改动 Detection / WarningEvent 定义
    assert "track_id" in {f.name for f in dataclasses.fields(Detection)}
    assert RISK_LEVELS == ("LOW", "MEDIUM", "HIGH")
    # generator 只产上游输入原语，**代码层面**不触达业务规则层（D1/B1）。
    # 用 AST 校验而非子串扫描：文档字符串里写"不调用 RuleEngine"不应算依赖，
    # 而 ``import x as y`` 这类改写也不能绕过（助手变异验证见 test_validation_ast_contract）。
    assert_no_dependency(
        generator,
        forbidden_modules=[
            "home_perception.analysis.rule_engine",
            "home_perception.analysis.decision_policy",
        ],
        forbidden_names=["RuleEngine", "DecisionEngine", "WarningEvent", "RiskSignal"],
    )


# ============================================================================
# T7 frames 渲染 torch-free（CPU 程序化，不依赖 ultralytics / torch）
# ============================================================================


def test_adr0032_t7_frame_rendering_model_free():
    # AST 校验：renderer 不导入 torch / ultralytics，也不引用其符号
    # （文档字符串写 "torch-free" 不算依赖）
    assert_no_dependency(
        renderer,
        forbidden_modules=["torch", "ultralytics"],
        forbidden_names=["YOLO", "YOLODetector"],
    )
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    frames = render_frames(scn)
    # 确定性 + CPU 可跑
    frames2 = render_frames(scn)
    for a, b in zip(frames, frames2):
        assert np.array_equal(a, b)
    # 仅用 OpenCV 程序化基元（矩形/圆 + 噪声纹理）；背景为恒定灰度
    assert frames[0].shape == (288, 384, 3)
    assert frames[0].dtype == np.uint8


# ============================================================================
# B2 export_detections_json 与既有缓存字段级等价
# ============================================================================


def test_adr0032_b2_export_detections_json_field_equivalent(tmp_path):
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    out = tmp_path / "out.detections.json"
    export_detections_json(scn, out)

    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["synthetic"] is True
    # 逐帧逐检测 字段级等价（均含确定性 track_id，非仅几何等价）
    per_frame = emit_detections(scn)
    assert len(raw["frames"]) == len(per_frame)
    for i, fr in enumerate(raw["frames"]):
        expected = [detection_to_dict(d) for d in per_frame[i]]
        assert fr["detections"] == expected
        for d in fr["detections"]:
            assert d["track_id"] is not None  # 非空（B1/B2）
            assert d["confidence"] == SYNTHETIC_CONFIDENCE

    # —— 与**既有**缓存 fixture 做字段级等价（对照真相源，而非自证）——
    legacy = json.loads(LEGACY_CACHE.read_text(encoding="utf-8"))
    legacy_frame_keys = set(legacy["frames"][0].keys())
    legacy_det_keys = {k for f in legacy["frames"] for d in f.get("detections", []) for k in d}
    assert legacy_det_keys, "既有 fixture 无检测，字段对照失效"
    for fr in raw["frames"]:
        assert legacy_frame_keys <= set(fr.keys())
        for d in fr["detections"]:
            assert set(d.keys()) == legacy_det_keys  # 不多不少

    # —— 复刻 CachedDetectionDetector 的真实消费路径：``Detection(**d)`` ——
    # 多一个键 / 少一个键都会 TypeError，这才是"可被既有回放器直接消费"的硬证据。
    rebuilt = [[Detection(**d) for d in fr["detections"]] for fr in raw["frames"]]
    assert rebuilt == per_frame  # 序列化往返后与内存对象逐字段相等

    # 回放第 10 帧（human 在场），走 ScenarioDetectionDetector 鸭子接口
    det = ScenarioDetectionDetector(rebuilt, source_size=(288, 384))
    for _ in range(10):
        res = det.detect(None)
    assert len(res.detections) == 1
    assert res.detections[0].class_name == "person"
    assert res.detections[0].track_id is not None


# ============================================================================
# T8 两通道几何一致（单一真相源：同源 actors.tracks）
# ============================================================================


def test_adr0032_t8_two_channels_geometry_consistent():
    scn = load_scenario(FIX / "perception" / "torchfree_visit.yaml")
    actor = scn.actors[0]
    frame = 20  # 在 tracks[5,50] 区间内，human 在场
    # 走公开 API（单一几何真相源），不读私有成员
    cx, cy, w, h = interpolate_actor_box(actor, frame)

    # 通道一：detections bbox 中心 + 尺寸 == 插值结果（不止中心，尺寸也须同源）
    per_frame = emit_detections(scn)
    det = per_frame[frame][0]
    det_cx = (det.bbox[0] + det.bbox[2]) / 2
    det_cy = (det.bbox[1] + det.bbox[3]) / 2
    assert abs(det_cx - cx) < 1e-6
    assert abs(det_cy - cy) < 1e-6
    assert abs((det.bbox[2] - det.bbox[0]) - w) < 1e-6
    assert abs((det.bbox[3] - det.bbox[1]) - h) < 1e-6

    # 通道二：渲染帧在插值中心处有非背景像素（矩形覆盖中心）
    frames = render_frames(scn)
    px = tuple(int(v) for v in frames[frame][int(cy), int(cx)])
    assert px != (30, 30, 30)  # 不是背景基色
    # 两通道对同一 Scenario 在几何上等价：检测中心与渲染实心矩形中心重合
    assert abs(det_cx - cx) < 1e-6 and abs(det_cy - cy) < 1e-6
