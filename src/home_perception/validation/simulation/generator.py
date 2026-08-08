"""ADR-0032 Slice B：通道一 detections 发射器（零行为变化，torch-free）。

``emit_detections(scenario) -> list[list[Detection]]`` 从 ``actors.tracks`` **确定性**派生
每帧 ``Detection``，**复用现有** ``detection/detector.py`` 的 ``Detection`` 类型
（不新增 ``RawDetection``，评审 B1），``track_id`` 按 ``actor.id`` **确定性回填**（非 None，
否则 ``VisitorTracker`` 丢弃，与 ``CachedDetectionDetector`` 缓存同语义）。

generator **只产输入层感知原语**，不调用 ``RuleEngine``、不替下游算期望、不含任何风险/业务
判定（D1/B1）。替换既有 ``CachedDetectionDetector`` 的手工 JSON，并提供
``export_detections_json`` 向后兼容（字段级等价，评审 B2）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...detection.detector import Detection, DetectionResult
from ..scenario.scenario import ActorSpec, Scenario

# 合成置信度（恒定，无真实抖动；与既有 synthetic fixture 一致）。
SYNTHETIC_CONFIDENCE = 0.9

# actor_type → (class_id, class_name)。human 映射为 person（tracker 仅处理 person）；
# 其余类型仍产出输入原语，但 VisitorTracker 守卫会跳过（与既有行为一致，不引入新判定）。
ACTOR_TYPE_TO_CLASS: dict[str, tuple[int, str]] = {
    "human": (0, "person"),
    "object": (24, "backpack"),
    "vehicle": (2, "car"),
    "pet": (16, "dog"),
}


def _assign_track_ids(actors: list[ActorSpec]) -> dict[str, int]:
    """按 actor.id 稳定排序分配确定性 track_id（从 1 起，避免 0）。

    用**排序序位**而非 ``hash(actor.id)``（builtin ``hash`` 受 ``PYTHONHASHSEED`` 影响、
    跨进程不稳，违反 T1 确定性）。同一 ``scenario_id`` 跨进程恒定。
    """
    return {a.id: i + 1 for i, a in enumerate(sorted(actors, key=lambda a: a.id))}


def interpolate_actor_box(actor: ActorSpec, frame: int) -> tuple[float, float, float, float] | None:
    """在给定 ``frame`` 插值 actor 的 (cx, cy, w, h)；不在 [首帧, 末帧] 区间则返回 None。

    **两通道的单一几何真相源**（T8）：``emit_detections``（通道一）与 ``render_frames``
    （通道二）都只经此函数取几何，因此两通道对同一 Scenario 恒等价。公开导出（而非
    ``_interpolate``）以便契约测试无需读私有成员即可验证 T8。
    """
    kfs = actor.tracks
    if not kfs:
        return None
    if frame < kfs[0].frame or frame > kfs[-1].frame:
        return None
    # 落在关键帧上
    for kf in kfs:
        if kf.frame == frame:
            return (kf.pos[0], kf.pos[1], kf.size[0], kf.size[1])
    # 区间插值：找 a.frame <= frame <= b.frame
    a = kfs[0]
    b = kfs[-1]
    for i in range(len(kfs) - 1):
        if kfs[i].frame <= frame <= kfs[i + 1].frame:
            a, b = kfs[i], kfs[i + 1]
            break
    if b.frame == a.frame:
        return (a.pos[0], a.pos[1], a.size[0], a.size[1])
    t = (frame - a.frame) / (b.frame - a.frame)
    cx = a.pos[0] + (b.pos[0] - a.pos[0]) * t
    cy = a.pos[1] + (b.pos[1] - a.pos[1]) * t
    w = a.size[0] + (b.size[0] - a.size[0]) * t
    h = a.size[1] + (b.size[1] - a.size[1]) * t
    return (cx, cy, w, h)


def emit_detections(scenario: Scenario) -> list[list[Detection]]:
    """从 ``actors.tracks`` 确定性派生每帧 ``Detection`` 列表（长度 = ``duration_frames``）。

    返回 ``list[list[Detection]]``：第 i 个元素为第 i 帧的检测列表（空列表 = 该帧无目标）。
    确定性：仅依赖场景数据 + 稳定 track_id 分配，无随机性（D2/T1）。
    """
    if scenario.meta.duration_frames is None:
        raise ValueError(
            f"场景 {scenario.meta.scenario_id!r} 缺少 meta.duration_frames，"
            "无法派生逐帧 detections（生成期 fail-closed）"
        )
    n = scenario.meta.duration_frames
    track_map = _assign_track_ids(scenario.actors)
    frames: list[list[Detection]] = []
    for f in range(n):
        dets: list[Detection] = []
        for actor in scenario.actors:
            box = interpolate_actor_box(actor, f)
            if box is None:
                continue
            cx, cy, w, h = box
            class_id, class_name = ACTOR_TYPE_TO_CLASS.get(actor.actor_type, (0, "person"))
            dets.append(
                Detection(
                    class_id=class_id,
                    class_name=class_name,
                    confidence=SYNTHETIC_CONFIDENCE,
                    bbox=[cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
                    timestamp=0.0,  # 合成；确定性恒定
                    track_id=track_map[actor.id],  # 确定性回填，非 None（B1）
                )
            )
        frames.append(dets)
    return frames


class ScenarioDetectionDetector:
    """经 ``Detector`` 接缝注入的确定性检测回放器（替代 ``CachedDetectionDetector``）。

    实现 pipeline 依赖的 ``detect(frame)`` 鸭子接口；``frame`` 透传忽略（与
    ``CachedDetectionDetector`` 一致），只回放 ``emit_detections`` 产出的缓存。
    证明「事件经 detector→tracker→event_builder 真实进入链路」，torch-free（CI 合约）。
    """

    def __init__(
        self,
        frames: list[list[Detection]],
        source_size: tuple[int, int] = (288, 384),
    ):
        self._frames = frames
        self._source_size = source_size
        self._i = 0

    def detect(self, frame) -> DetectionResult:
        if not self._frames or self._i >= len(self._frames):
            return DetectionResult(
                detections=[],
                timestamp=0.0,
                inference_ms=0.0,
                source_size=self._source_size,
                inference_size=self._source_size,
                model="scenario",
            )
        dets = list(self._frames[self._i])
        self._i += 1
        return DetectionResult(
            detections=dets,
            timestamp=0.0,
            inference_ms=0.0,
            source_size=self._source_size,
            inference_size=self._source_size,
            model="scenario",
        )

    def reset(self) -> None:
        """重置回放位置（多场景复用同一实例时调用）。"""
        self._i = 0


def detection_to_dict(d: Detection) -> dict[str, Any]:
    """``Detection`` → 与既有 ``detections.json`` 同 schema 的 dict（B2 字段级等价）。"""
    return {
        "class_id": d.class_id,
        "class_name": d.class_name,
        "confidence": d.confidence,
        "bbox": [float(v) for v in d.bbox],
        "timestamp": d.timestamp,
        "track_id": d.track_id,
    }


def export_detections_json(
    scenario: Scenario,
    path: str | Path,
    *,
    source_video: str | None = None,
    frame_interval_s: float = 0.5,
) -> Path:
    """按既有 ``detections.json`` schema 重新生成检测缓存（D6/B2 字段级等价）。

    与 ``tests/fixtures/detections/*.json`` 的 ``frames[].detections[]`` 同键结构，
    可被 ``CachedDetectionDetector.load_cached_detections`` 直接消费，向后兼容既有测试。
    """
    per_frame = emit_detections(scenario)
    out: dict[str, Any] = {
        "schema_version": 1,
        "source_video": source_video or f"{scenario.meta.scenario_id}.synthetic",
        "model": "synthetic-fixture",
        "tracker": "synthetic-fixture",
        "synthetic": True,
        "scenario_id": scenario.meta.scenario_id,
        "scenario_version": scenario.meta.version,
        "frame_interval_s": frame_interval_s,
        "frames": [
            {
                "frame_index": i,
                "timestamp": float(i) * frame_interval_s,
                "detections": [detection_to_dict(d) for d in per_frame[i]],
            }
            for i in range(len(per_frame))
        ],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
