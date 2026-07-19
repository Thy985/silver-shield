"""时间工具。"""
from __future__ import annotations

import time
from datetime import datetime, timezone


def now_ts() -> float:
    return time.time()


def now_dt() -> datetime:
    """当前 UTC 时间（时区感知），用于 VisitorTrack 等需要人类可读时间的领域对象。"""
    return datetime.now(timezone.utc)
