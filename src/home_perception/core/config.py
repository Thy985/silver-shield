"""配置加载与校验。

- 从 YAML 读取默认配置（config/default.yaml）
- 支持 ${ENV_VAR:-default} 形式引用环境变量（凭证走 .env，不入库）
- 使用 pydantic 做结构化校验，缺失项回退到默认值
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

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


class DetectionConfig(BaseModel):
    model: str = "yolo11n.pt"  # 第一阶段默认小模型：CPU 可跑、延迟低
    conf_threshold: float = 0.45
    # 仅第一阶段 4 类：person / backpack / handbag / cell phone（COCO id）
    classes: list[int] = Field(default_factory=lambda: [0, 24, 26, 67])
    device: str = "cpu"  # cpu | cuda:0
    imgsz: int = 640  # 1080p 帧先 resize 到该尺寸再推理，控制 CPU 算力
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
