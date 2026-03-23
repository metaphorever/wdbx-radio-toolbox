"""
URL assembly for archive.wdbx.org episodes.

IMPORTANT: The archive listing page is the PRIMARY data source for discovering files,
including restart fragments. URL construction here is a FALLBACK only.

Confirmed URL pattern (Q3 resolved 2026-03-23):
  https://archive.wdbx.org/mp3/wdbx_{YYMMDD}_{HHMMSS}{slug}.mp3
  Example: https://archive.wdbx.org/mp3/wdbx_260205_070000islandreport.mp3
  Note: NO underscore between the time component and the show slug.
"""
from datetime import date as Date
from shared.config import get


def build_episode_url(air_date: Date, show_time: str, slug: str, station_prefix: str | None = None) -> str:
    """
    Build the expected MP3 URL for a single episode segment.

    Args:
        air_date:   Date the show aired.
        show_time:  Time string in HHMMSS format (e.g. "070000").
        slug:       Show slug as it appears in the URL (e.g. "islandreport").
        station_prefix: Override config value if needed.

    Returns:
        Full URL string.
    """
    prefix = station_prefix or get("pacifica.station_prefix", "wdbx")
    base = get("pacifica.archive_base_url", "https://archive.wdbx.org/mp3/")
    date_str = air_date.strftime("%y%m%d")
    return f"{base}{prefix}_{date_str}_{show_time}{slug}.mp3"
