"""模块入口：加载配置 -> 装配设备 -> 启动流水线。

Phase 1 业务装配（Detector/Rules/Collector/Publisher 的具体实现）在对应 feature 分支完成；
本文件先保证"配置可加载、日志可用、契约可导入"，主分支始终可运行。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 便于脚本直接运行

from .common.logging import get_logger, setup_logging  # noqa: E402
from .core.config import Settings  # noqa: E402

__version__ = "0.1.0"


def main() -> None:
    settings = Settings.load()
    setup_logging(settings.logging.level, settings.logging.json_logs)
    log = get_logger("main")
    log.info("home_perception.start", version=__version__)
    log.info(
        "home_perception.bootstrapped",
        note="Phase 1 业务装配（YOLO/规则/取证/上报）见 docs/08_roadmap.md",
    )
    # TODO(Phase 1): 读取 config/devices.yaml -> 为每台设备构建
    #   EZVIZClient + FrameSource + YOLODetector + [Rules] + LocalClipCollector + MQTTPublisher
    #   -> Pipeline(device=..., ...).run(source)


if __name__ == "__main__":
    main()
