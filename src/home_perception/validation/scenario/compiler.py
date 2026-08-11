"""ADR-0032 Slice D（部分）：``ScenarioCompiler`` —— YAML → ``SyntheticInput``（D1/D4/D5）。

对应 ``validation/scenario/`` 子包（D5）。把单一声明式 ``Scenario`` 编译为**两通道之一**
的 ``SyntheticInput``：
- ``mode: detections`` → 复用现有 ``Detection`` 的 ``list[list[Detection]]``（通道一，零模型）；
- ``mode: frames``      → OpenCV 程序化 BGR 帧 ``list[np.ndarray]``（通道二）。

并计算 ``generator.fingerprint``（D7/T11）。**不**反向依赖业务规则层 ``analysis/rule_engine``。
"""

from __future__ import annotations

from datetime import UTC, datetime

import home_perception

from ..fingerprint import RENDERER_VERSION, compute_fingerprint
from ..scenario.scenario import Scenario, ensure_synthesizable
from ..simulation.generator import ScenarioDetectionDetector, emit_detections
from ..simulation.renderer import render_frames
from ..synthetic_input import SyntheticInput


class ScenarioCompiler:
    """把声明式 ``Scenario`` 编译为 ``SyntheticInput``（D1 两通道之一）。"""

    def compile(self, scenario: Scenario, mode: str | None = None) -> SyntheticInput:
        """编译为指定通道的 ``SyntheticInput``。

        Args:
            scenario: 已加载 + 校验的 ``Scenario``（身份字段 + 结构已在加载期校验）。
            mode: 覆盖 ``scenario.mode``；``detections`` / ``frames`` 二选一。

        Raises:
            ValueError: 生成期必填 ``seed`` / ``duration_frames`` 缺失（fail-closed）。
        """
        ensure_synthesizable(scenario)  # 生成期补充校验（seed / duration_frames）
        chosen = mode or scenario.mode
        w, h = scenario.camera.resolution

        if chosen == "frames":
            frames = render_frames(scenario)
            detector = None
            n_frames = len(frames)
        elif chosen == "detections":
            per_frame = emit_detections(scenario)
            detector = ScenarioDetectionDetector(per_frame, source_size=(h, w))
            frames = None
            n_frames = len(per_frame)
        else:
            raise ValueError(f"未知 mode={chosen!r}；必须为 'detections' 或 'frames'")

        fingerprint = compute_fingerprint(
            schema_version=scenario.meta.schema_version,
            renderer_version=RENDERER_VERSION,
            seed=scenario.meta.seed,  # type: ignore[arg-type]
            code_version=home_perception.__version__,
        )

        # ADR-0034 Phase B.2：把音频声明编译为正式 AudioPerceptionEvent。
        # created_at 由 timestamp（Unix 秒）推导为 UTC datetime，确定性、可回放。
        # 延迟 import 音频事件模型：避免编译器加载期拉起 audio 子包重链。
        from home_perception.audio.event import AudioPerceptionEvent

        audio_events: list[AudioPerceptionEvent] = []
        for i, spec in enumerate(scenario.audio):
            audio_events.append(
                AudioPerceptionEvent(
                    event_id=spec.event_id or f"aev-{i}",
                    timestamp=spec.timestamp,
                    kind=spec.kind,
                    score=spec.score,
                    confidence=spec.confidence,
                    source_segment_ids=list(spec.source_segment_ids),
                    labels=list(spec.labels),
                    created_at=datetime.fromtimestamp(spec.timestamp, tz=UTC),
                )
            )

        return SyntheticInput(
            scenario_id=scenario.meta.scenario_id,
            mode=chosen,
            detector=detector,
            frames=frames,
            n_frames=n_frames,
            fingerprint=fingerprint,
            seed=scenario.meta.seed,  # type: ignore[arg-type]
            scenario=scenario,
            audio_events=tuple(audio_events),
        )
