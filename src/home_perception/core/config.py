"""配置加载与校验。

- 从 YAML 读取默认配置（config/default.yaml）
- 支持 ${ENV_VAR:-default} 形式引用环境变量（凭证走 .env，不入库）
- 使用 pydantic 做结构化校验，缺失项回退到默认值
"""
from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def _repl(m: re.Match) -> str:
            var, default = m.group(1), m.group(2)
            return os.environ.get(var, default if default is not None else "")

        return _ENV_PATTERN.sub(_repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_logs: bool = True


class ReconnectConfig(BaseModel):
    max_retries: int = 5
    backoff_s: int = 3


class IngestionConfig(BaseModel):
    protocol: str = "rtsp"  # rtsp=低延迟(默认) | hls=已验证回退
    quality: int = 1
    channel_no: int = 1
    reconnect: ReconnectConfig = ReconnectConfig()
    fps_target: int = 8


class ImgszProfile(str, Enum):
    """推理分辨率预设（见 docs/09 / P0-4 实测结论）。

    P0-4 在 CPU 边缘机实测（yolo11n / 1080p 合成帧）：
    - accuracy(640)：推理 ~124ms / ~8FPS —— 精度收益有限但延迟高，不适合实时
    - balanced(480)：推理 ~86ms / ~11.6FPS —— 满足 <100ms 且 >10FPS，门前场景折中
    - realtime(416)：推理 ~47ms / ~21.5FPS —— 裕量大，但 cell phone 等小目标精度下降
    """

    ACCURACY = "accuracy"
    BALANCED = "balanced"
    REALTIME = "realtime"

    @property
    def imgsz(self) -> int:
        return {"accuracy": 640, "balanced": 480, "realtime": 416}[self.value]

    @classmethod
    def resolve(cls, profile: "ImgszProfile | str | None", explicit_imgsz: Optional[int]) -> int:
        """解析最终 imgsz：显式 imgsz 优先；否则用 profile；再否则回退 balanced(480)。"""
        if explicit_imgsz:
            return int(explicit_imgsz)
        if profile is None:
            return cls.BALANCED.imgsz
        if isinstance(profile, ImgszProfile):
            return profile.imgsz
        try:
            return cls(str(profile).lower()).imgsz
        except ValueError:
            return cls.BALANCED.imgsz


class DetectionConfig(BaseModel):
    model: str = "yolo11n.pt"  # 第一阶段默认小模型：CPU 可跑、延迟低
    conf_threshold: float = 0.45
    # 仅第一阶段 4 类：person / backpack / handbag / cell phone（COCO id）
    classes: list[int] = Field(default_factory=lambda: [0, 24, 26, 67])
    device: str = "cpu"  # cpu | cuda:0
    # P0-4 实测结论：纯 CPU 边缘机 yolo11n@640 推理 ~124ms 未达实时目标；
    # MVP 默认 480（balanced）满足 <100ms 且 >10FPS。详见 docs/09。
    imgsz: int = ImgszProfile.BALANCED.imgsz  # 480
    imgsz_profile: ImgszProfile = ImgszProfile.BALANCED  # accuracy=640 / balanced=480 / realtime=416
    enable_track: bool = False  # P0-3 关闭；P0-5 逗留/重复识别时开启
    tracker: str = "botsort"  # bytetrack | botsort（enable_track=True 时生效）


class AnalysisConfig(BaseModel):
    dwell_threshold_s: int = 30
    odd_hour_start: int = 23
    odd_hour_end: int = 6
    cooldown_s: int = 60


class EvidenceConfig(BaseModel):
    store: str = "local"
    local_dir: str = "data/evidence"
    clip_seconds: int = 10
    snapshot: bool = True


class MqttConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 1883
    topic: str = "silvershield/home/{device_id}/events"


class BufferConfig(BaseModel):
    enabled: bool = True
    max_items: int = 200


class OutputConfig(BaseModel):
    transport: str = "mqtt"
    mqtt: MqttConfig = MqttConfig()
    buffer: BufferConfig = BufferConfig()


class Settings(BaseModel):
    logging: LoggingConfig = LoggingConfig()
    ingestion: IngestionConfig = IngestionConfig()
    detection: DetectionConfig = DetectionConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    evidence: EvidenceConfig = EvidenceConfig()
    output: OutputConfig = OutputConfig()

    @classmethod
    def load(cls, path: str | os.PathLike = "config/default.yaml") -> "Settings":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw = _expand_env(raw)
        return cls(**raw)
