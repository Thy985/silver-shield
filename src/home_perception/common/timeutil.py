"""时间工具。"""
from __future__ import annotations

import time
from datetime import datetime, timezone


def now_ts() -> float:
    return time.time()


def now_dt() -> datetime:
    """当前 UTC 时间（时区感知），用于 VisitorTrack 等需要人类可读时间的领域对象。"""
    return datetime.now(timezone.utc)


def require_utc(dt: datetime, field_name: str) -> None:
    """校验 datetime 是 timezone-aware 且为 UTC（防御 naive 漏标，对齐 ADR-0007）。"""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(
            f"{field_name} 必须是 timezone-aware datetime（建议 UTC），收到 naive datetime: {dt!r}"
        )
