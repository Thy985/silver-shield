"""ADR-0035 D3 · 编排层入参 schema（CLI 入口 / 8 阶段驱动配置）。

本模块只承载「编排配置」与「字幕线索」两类 pydantic 模型，**不**承载叙事语义
（语义层见 `narrative/`·`storyboard/`·`scene/`）。所有模型 `extra="forbid"`，
字段集硬锁，越界即 `ValidationError`（CI/review 可据此打回）。

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
    with_audio: bool = False  # D3-A 默认 False；D3-B 置 True 触发旁白（默认不产出）
    seed: int | None = None  # 确定性种子（None 时沿用 scenario.meta.seed）


# ── 字幕线索（caption 文本层的中间表示）──
# 由 ShotSpec.narration 按 shot 时间窗展开为带时间锚点的线索，
# 供 render/caption.py 逐帧绘制（D3-A captions-only，确定性）。


class NarrationCue(BaseModel):
    """带时间锚点的字幕线索（caption 文本层中间表示）。"""

    model_config = ConfigDict(extra="forbid")

    shot: str  # 所属 shot name（与 Storyboard.shots[].name 对应）
    text: str  # 字幕/旁白文本（由证据值 + 文案常量填充，非自由生成）
    start_s: float  # 该线索起始秒（相对视频起点）
    end_s: float  # 该线索结束秒
