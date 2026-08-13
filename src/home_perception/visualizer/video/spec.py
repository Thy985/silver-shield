"""ADR-0035 D3 · 编排层入参 schema（CLI 入口 / 8 阶段驱动配置）。

本模块只承载「编排配置」这一类 pydantic 模型，**不**承载叙事语义
（语义层见 `narrative/`·`storyboard/`·`scene/`）。所有模型 `extra="forbid"`，
字段集硬锁，越界即 `ValidationError`（CI/review 可据此打回）。

字幕的时间锚点由 ``ShotSpec.narration`` 数组 + shot 时长在渲染期直接推导
（见 ``compiler._render_frames``），**不**存在独立的 cue 中间表示——D3-A 无需
为「同 shot 内精确到秒的字幕时间轴」建模；D3-B 若确需时间轴（音轨对齐）再引入，
届时必须同步落地生产者与消费者，避免留下无使用者的 schema。

见设计文档 §2.8（spec.py 落点）、§3（schema 总则）、§6（输出布局）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── 编排配置（CLI 入参 · D3-A 默认路径）──
# D3-A 默认纯视觉零音频：with_audio 默认 False（D3-B 才置 True）。
# background 默认 synthetic（确定性灰底，零 validation 依赖）；可选 validation 复用
# ADR-0032 render_frames（D3-1 单向例外）。


class CaseVideoSpec(BaseModel):
    """单条 case video 的编排配置（CLI 入参，纯数据）。

    字段集硬锁：除下列字段外不得出现任何额外字段。
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str  # 待编译的场景 id（须能在 artifact_dir 投影中解析）
    artifact_dir: Path  # EvidenceProjection 目录（loader.load_evidence_projection 输入）
    output_dir: Path  # 产物落盘根（见 §6：generated/demo_videos/<scenario_id>__v<ver>/）
    audience: str = "general"  # 受众维度（general/judges/investors/family...）
    template_name: str | None = None  # 显式指定 ScenarioTemplate；None 则按场景类别自动选
    background: Literal["synthetic", "validation"] = "synthetic"  # 背景层来源
    fps: float = 2.0  # 输出帧率（默认与 ADR-0032 camera.fps 默认一致）
    resolution: tuple[int, int] = (1280, 720)  # (width, height)
    version: int = Field(default=1, ge=1)  # 产物版本号（命名空间一部分）
    # D3-A 默认 False。置 True 会被 compiler 主入口以 NotImplementedError 拒绝
    # （D3-B 才落地 AudioComposer + ffmpeg mux）——绝不静默产出无声片充当有声片。
    with_audio: bool = False
    # 确定性种子。None 时确定性降级为 0：EvidenceProjection 投影**不含** meta/seed
    # 字段（已对真实 artifact 实测确认），故无「沿用场景 seed」的上游可读。
    seed: int | None = None


__all__ = ["CaseVideoSpec"]
