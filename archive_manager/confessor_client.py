"""
Typed client for the Confessor schedule and archive APIs.

Two endpoints:
  Schedule — https://confessor.wdbx.org/_do_api.php
  Archive  — https://archive.wdbx.org/_sh_do_api.php

All methods return typed dataclasses or lists thereof.
get_gone_shows() returns raw dicts until the response shape is confirmed
by the live test suite.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

SCHEDULE_API = "https://confessor.wdbx.org/_do_api.php"
ARCHIVE_API  = "https://archive.wdbx.org/_sh_do_api.php"
DEFAULT_TIMEOUT = 30

# The API returns a list-of-lists for getshows; outer index is day number
_DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class ScheduledShow:
    show_key: str           # sh_altid — matches archive API slug
    display_name: str       # sh_name
    day: str                # "Monday", "Tuesday", etc.
    day_num: int            # 0=Sun … 6=Sat
    start_seconds: int      # sh_shour: seconds since midnight (e.g. 25200 = 7:00 AM)
    duration_seconds: int   # sh_len: total show length in seconds
    dj_name: str = ""       # sh_djname
    description: str = ""   # sh_desc

    @property
    def start_time_str(self) -> str:
        """Return start time as HHMMSS string, matching Show.schedule_time format."""
        h, rem = divmod(self.start_seconds, 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}{m:02d}{s:02d}"

    @property
    def duration_minutes(self) -> int:
        return self.duration_seconds // 60


@dataclass
class ArchiveEntry:
    show_key: str           # idkey — matches sh_altid from schedule
    mp3_url: str            # direct download URL
    air_timestamp: int      # def_time: Unix timestamp of broadcast
    expires_timestamp: int  # expires: Unix timestamp — 0 if missing
    duration_seconds: int   # lsecs: actual recorded length — 0 if missing
    title: str = ""
    day: str = ""
    category: str = ""

    @property
    def air_datetime(self) -> datetime:
        """Naive UTC datetime, consistent with Episode.air_datetime in the DB."""
        return datetime.fromtimestamp(self.air_timestamp, tz=timezone.utc).replace(tzinfo=None)

    @property
    def expires_datetime(self) -> datetime | None:
        if not self.expires_timestamp:
            return None
        return datetime.fromtimestamp(self.expires_timestamp, tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Schedule API
# ---------------------------------------------------------------------------

def get_all_shows(*, timeout: int = DEFAULT_TIMEOUT) -> list[ScheduledShow]:
    """
    Fetch all currently scheduled shows.

    The PHP docs show a day-keyed dict, but the live API returns a list.
    Handles both shapes:
      - list of show dicts (flat)
      - list of lists (one per day)
      - dict keyed by day number string
    """
    resp = requests.get(f"{SCHEDULE_API}?req=getshows&json=1", timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    def _parse_show(s: dict, day_num: int) -> ScheduledShow | None:
        show_key = s.get("sh_altid", "").strip()
        if not show_key:
            return None
        # The 'day' field inside each show dict is unreliable (shows "Sunday" for all).
        # Use the authoritative day name from the sub-list position instead.
        day_str = _DAY_NAMES[day_num] if 0 <= day_num <= 6 else s.get("day", "").strip()
        return ScheduledShow(
            show_key=show_key,
            display_name=s.get("sh_name", "").strip(),
            day=day_str,
            day_num=day_num,
            start_seconds=int(s.get("sh_shour") or 0),
            duration_seconds=int(s.get("sh_len") or 0),
            dj_name=s.get("sh_djname", "").strip(),
            description=s.get("sh_desc", "").strip(),
        )

    shows: list[ScheduledShow] = []

    if isinstance(data, dict):
        # Day-keyed dict {"0": [...], "1": [...], ...}
        for day_num_str, day_shows in data.items():
            try:
                day_num = int(day_num_str)
            except ValueError:
                continue
            if not isinstance(day_shows, list):
                continue
            for s in day_shows:
                if isinstance(s, dict):
                    if parsed := _parse_show(s, day_num):
                        shows.append(parsed)

    elif isinstance(data, list):
        if data and isinstance(data[0], list):
            # List of 7 lists — outer index is the authoritative day number
            for day_num, day_shows in enumerate(data):
                if not isinstance(day_shows, list):
                    continue
                for s in day_shows:
                    if isinstance(s, dict):
                        if parsed := _parse_show(s, day_num):
                            shows.append(parsed)
        else:
            # Flat list of show dicts — day_num not known from position
            for s in data:
                if isinstance(s, dict):
                    if parsed := _parse_show(s, -1):
                        shows.append(parsed)

    else:
        logger.warning("getshows: unexpected response type %s", type(data))

    return shows


def get_gone_shows(*, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """
    Fetch shows marked as gone/off-air.

    Returns raw dicts — the response shape is not yet confirmed.
    See tests/test_confessor_api_live.py for shape documentation.
    """
    resp = requests.get(f"{SCHEDULE_API}?req=getgone&json=1", timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # May be keyed by day like getshows — flatten to a list
        flat: list[dict] = []
        for v in data.values():
            if isinstance(v, list):
                flat.extend(v)
            elif isinstance(v, dict):
                flat.append(v)
        return flat
    return []


def get_show_altids(*, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    """
    Fetch the list of all show altids (slugs) known to the schedule system.

    Note: the PHP reference has a bug where the json path is overwritten,
    so this handles multiple possible response shapes defensively.
    """
    resp = requests.get(f"{SCHEDULE_API}?req=altids&json=1", timeout=timeout)
    resp.raise_for_status()

    try:
        data = resp.json()
    except Exception:
        # Fall back to treating the body as a raw string list
        logger.warning("altids: response was not valid JSON, raw: %.200s", resp.text)
        return []

    if isinstance(data, list):
        result = []
        for item in data:
            if isinstance(item, dict):
                # altids returns [{sh_altid: "...", sh_name: "..."}, ...]
                key = item.get("sh_altid", "").strip()
                if key:
                    result.append(key)
            elif isinstance(item, str) and item.strip():
                result.append(item.strip())
        return result
    if isinstance(data, dict):
        return [str(k).strip() for k in data.keys() if k]
    return []


def get_show_by_key(show_key: str, *, timeout: int = DEFAULT_TIMEOUT) -> ScheduledShow | None:
    """Fetch a single show's schedule info by its altid."""
    resp = requests.get(f"{SCHEDULE_API}?req=key&key={show_key}&json=1", timeout=timeout)
    resp.raise_for_status()
    s = resp.json()
    if not isinstance(s, dict) or not s.get("sh_altid"):
        return None
    return ScheduledShow(
        show_key=s.get("sh_altid", "").strip(),
        display_name=s.get("sh_name", "").strip(),
        day=s.get("day", s.get("sh_big_days", "")).strip(),
        day_num=-1,  # not returned by this endpoint
        start_seconds=int(s.get("sh_shour") or 0),
        duration_seconds=int(s.get("sh_len") or 0),
        dj_name=s.get("sh_djname", "").strip(),
        description=s.get("sh_desc", "").strip(),
    )


# ---------------------------------------------------------------------------
# Archive API
# ---------------------------------------------------------------------------

def get_archive_entries(
    show_key: str,
    num: int = 20,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[ArchiveEntry]:
    """
    Fetch recent archive entries for one show.

    Returns an empty list for shows with no entries or unknown slugs.
    The response shape for missing shows is documented in the live tests.
    """
    resp = requests.get(
        f"{ARCHIVE_API}?req={show_key}&num={num}&json=1",
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list):
        logger.debug("archive entries for %s: unexpected type %s", show_key, type(data))
        return []

    entries: list[ArchiveEntry] = []
    for e in data:
        mp3 = (e.get("mp3") or "").strip()
        def_time = e.get("def_time")
        if not mp3 or not def_time:
            continue
        entries.append(ArchiveEntry(
            show_key=e.get("idkey", show_key).strip(),
            mp3_url=mp3,
            air_timestamp=int(def_time),
            expires_timestamp=int(e["expires"]) if e.get("expires") else 0,
            duration_seconds=int(e["lsecs"]) if e.get("lsecs") else 0,
            title=(e.get("title") or "").strip(),
            day=(e.get("day") or "").strip(),
            category=(e.get("category") or "").strip(),
        ))
    return entries
