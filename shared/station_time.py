"""
Station-local time helpers.

Episode.air_datetime is stored as naive UTC (converted from the archive API's
Unix timestamps). Anything a person sees or files by — weekday folders,
filename dates, timelines, log messages — must use station-local time.
Taking the weekday or date straight from the UTC value puts every show that
starts after ~7pm Central on the next day.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from shared.config import get

DEFAULT_TIMEZONE = "America/Chicago"


def station_tz() -> ZoneInfo:
    return ZoneInfo(get("station.timezone", DEFAULT_TIMEZONE))


def to_station(dt_utc: datetime) -> datetime:
    """Naive UTC datetime → naive station-local datetime."""
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(station_tz()).replace(tzinfo=None)


def from_timestamp(ts: int | float) -> datetime:
    """Unix timestamp → naive station-local datetime."""
    return datetime.fromtimestamp(ts, tz=station_tz()).replace(tzinfo=None)


def station_date(dt_utc: datetime) -> date:
    """The calendar date a naive-UTC moment falls on at the station."""
    return to_station(dt_utc).date()


def station_weekday(dt_utc: datetime) -> str:
    """Weekday name ("Monday") a naive-UTC moment falls on at the station."""
    return to_station(dt_utc).strftime("%A")
