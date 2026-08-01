"""结构化日志配置（structlog）。

生产用 JSON 行日志，便于接入中心日志系统；本地调试可切 console。
"""
from __future__ import annotations

import logging
import sys

import structlog

_configured = False


def setup_logging(level: str = "INFO", json: bool = True) -> None:
    global _configured
    if _configured:
        return
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=lvl)
    if json:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(lvl),
        )
    else:
        structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    _configured = True


def get_logger(name: str):
    setup_logging()
    return structlog.get_logger(name)
