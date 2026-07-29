"""模块入口：加载配置 -> 装配设备 -> 启动流水线（P0-10 Demo 模式）。

> **P0-10 = 工程层问题（"怎么启动系统"）。** 本文件只做"配置可加载 + 日志可用 +
> 按 runtime.mode 路由到装配层"，不引入任何风险判定逻辑。
> 比赛 Demo 用 `runtime.mode: demo`（CAVIAR 复现）；realtime 接真实萤石留待 v1。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 便于脚本直接运行

from .common.logging import get_logger, setup_logging  # noqa: E402
from .core.config import Settings  # noqa: E402
from .runtime.lifecycle import run_demo  # noqa: E402

__version__ = "0.1.0"


def main() -> None:
    settings = Settings.load()
    setup_logging(settings.logging.level, settings.logging.json_logs)
    log = get_logger("main")
    log.info("home_perception.start", version=__version__)
    log.info("home_perception.mode", mode=settings.runtime.mode)

    if settings.runtime.mode == "demo":
        # P0-10 Demo：CAVIAR 三个场景端到端跑通（复用已验证组件，不验证逻辑）
        run_demo(settings)
    else:
        # realtime 模式（接真实萤石摄像头）留待 v1；当前显式未实现
        raise NotImplementedError(
            f"runtime.mode={settings.runtime.mode!r} 尚未实现；P0-10 仅支持 demo 模式"
        )


if __name__ == "__main__":
    main()
