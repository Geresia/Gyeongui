"""Time helpers: KST, service date, and statistic buckets."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# Trains running after midnight belong to the previous operating day.
SERVICE_DAY_CUTOFF_HOUR = 3


class DayType(str, Enum):
    WEEKDAY = "WEEKDAY"
    SATURDAY = "SATURDAY"
    SUNDAY_HOLIDAY = "SUNDAY_HOLIDAY"


class TimeBand(str, Enum):
    EARLY = "EARLY"          # 05-07
    AM_PEAK = "AM_PEAK"      # 07-09
    MIDDAY = "MIDDAY"        # 09-17
    PM_PEAK = "PM_PEAK"      # 17-20
    EVENING = "EVENING"      # 20-05 (includes after-midnight service)


def to_kst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; attach a timezone first")
    return dt.astimezone(KST)


def service_date(dt: datetime) -> date:
    return (to_kst(dt) - timedelta(hours=SERVICE_DAY_CUTOFF_HOUR)).date()


def day_type(dt: datetime, holidays: frozenset[date] = frozenset()) -> DayType:
    # TODO(D10): load the public holiday calendar; until then holidays count as weekdays.
    d = service_date(dt)
    if d in holidays or d.weekday() == 6:
        return DayType.SUNDAY_HOLIDAY
    if d.weekday() == 5:
        return DayType.SATURDAY
    return DayType.WEEKDAY


def time_band(dt: datetime) -> TimeBand:
    h = to_kst(dt).hour
    if 5 <= h < 7:
        return TimeBand.EARLY
    if 7 <= h < 9:
        return TimeBand.AM_PEAK
    if 9 <= h < 17:
        return TimeBand.MIDDAY
    if 17 <= h < 20:
        return TimeBand.PM_PEAK
    return TimeBand.EVENING


def parse_seoul_timestamp(value: str | None) -> datetime | None:
    """Parse a Seoul API timestamp such as '2026-09-26 08:31:05' (KST, no offset).

    Returns None for anything that does not look like a timestamp; the raw value is
    preserved in the raw payload regardless.
    """
    if not value:
        return None
    text = value.strip()
    # Some Seoul APIs append '.0' fractional seconds.
    if "." in text:
        text = text.split(".", 1)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=KST)
        except ValueError:
            continue
    return None
