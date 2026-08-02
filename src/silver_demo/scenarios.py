"""场景配置加载（ADR-0015 §2.6）。

加载 ``config/demo/scenarios/*.yaml``，把人类可读的场景参数（source / start_time /
frame_interval_s / loop）转成网关可消费的 ``ScenarioConfig``。

边界：本文件只读 YAML + 解析时间字符串，**不**触碰 pipeline / rule / decision。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator


def _parse_iso(value: str) -> datetime:
    """解析 ISO 8601 时间（支持 'Z' 或 '+00:00' 时区后缀），失败抛 ValueError。

    与 runtime/lifecycle._parse_demo_clock_start 一致：配置错误在启动即暴露。
    """
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"非法 ISO 时间: {value!r}") from exc


class ScenarioConfig(BaseModel):
    """单个 Demo 场景的参数。

    - ``scenario_id``：场景标识（demo README / Dashboard 展示用）
    - ``source``：CAVIAR fixture 目录名（``source_type=caviar_jpg`` 时，位于 ``settings.runtime.caviar_base_dir`` 下）；
      或视频源的标识（``source_type=video_file`` 时作 device_id 用）
    - ``source_type``：帧源类型（``caviar_jpg`` 工程验证 / ``video_file`` 真实 MP4 产品展示），默认 ``caviar_jpg``
    - ``media_path``：``source_type=video_file`` 时的 MP4 路径（相对仓库根或绝对；建议放 ``data/demo/``，gitignore 不入库）
    - ``start_time``：DemoClock 起点（datetime，必须带时区）
    - ``frame_interval_s``：每帧推进的模拟时间（秒）
    - ``fps_target``：网关帧循环目标速率（0 = 不限速）
    - ``loop``：帧列表耗尽后是否循环
    - ``description``：人类可读说明
    """

    scenario_id: str
    source: str
    source_type: str = "caviar_jpg"
    media_path: str | None = None
    start_time: datetime
    frame_interval_s: float = 0.5
    fps_target: int = 8
    loop: bool = True
    description: str = ""
    rule_overrides: dict[str, Any] | None = None
    # 场景级实时风险开关覆盖（ADR-0021 Phase 1 · 演示层接入）：
    # 形如 {enabled: true, decision_enabled: true}，覆盖 settings.realtime_risk，
    # 使单个场景可开启实时风险旁路（如 CCTV 夜间场景），不影响全局默认与其他场景。
    realtime_risk: dict[str, Any] | None = None

    @field_validator("frame_interval_s")
    @classmethod
    def _positive_interval(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"frame_interval_s 必须 > 0，收到 {v!r}")
        return v

    @field_validator("fps_target")
    @classmethod
    def _nonneg_fps(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"fps_target 必须 >= 0，收到 {v!r}")
        return v


def load_scenario(path: str | Path) -> ScenarioConfig:
    """从 YAML 文件加载 ScenarioConfig。

    Args:
        path: scenario YAML 路径（如 config/demo/scenarios/night_visit.yaml）

    Returns:
        ScenarioConfig

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: YAML 结构错误或字段非法
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"场景配置不存在: {path!r}")
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if "start_time" in raw and isinstance(raw["start_time"], str):
        # 预解析为 datetime（pydantic 也能接 str，但提前解析可给出更清晰的错误信息）
        raw["start_time"] = _parse_iso(raw["start_time"])
    return ScenarioConfig(**raw)
