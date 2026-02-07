from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from dateutil import tz


@dataclass(frozen=True)
class DatedValue:
    asof: date
    value: float


def to_epoch_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return int(dt.timestamp())


def parse_ymd(s: str) -> date:
    return date.fromisoformat(s)


def ymd_range_epoch_utc(target: date) -> tuple[int, int]:
    # Use a slightly wider window to avoid edge cases where the exchange timezone
    # shifts the candle across UTC boundaries.
    start = datetime(target.year, target.month, target.day, tzinfo=timezone.utc) - timedelta(days=2)
    end = datetime(target.year, target.month, target.day, tzinfo=timezone.utc) + timedelta(days=3)
    return to_epoch_seconds(start), to_epoch_seconds(end)


def epoch_to_exchange_date(ts: int, exchange_tz: Optional[str]) -> date:
    z = tz.gettz(exchange_tz) if exchange_tz else timezone.utc
    return datetime.fromtimestamp(ts, tz=z).date()
