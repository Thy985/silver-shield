"""时间工具。统一使用 Unix 秒（浮点），时区以设备本地时区记录。"""
from __future__ import annotations

import time


def now_ts() -> float:
    return time.time()
