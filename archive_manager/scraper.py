"""
Archive scraper using the Confessor archive API.

Replaces the old HTML directory listing approach. Calls:
  https://archive.wdbx.org/_sh_do_api.php?req={show_key}&num={n}&json=1

for each enabled show to get direct MP3 URLs, air timestamps, and expiry info.
"""
import json
import logging
from datetime import datetime, timezone

import requests
from sqlmodel import Session, select

from shared.models import Episode, Show

logger = logging.getLogger(__name__)

ARCHIVE_API = "https://archive.wdbx.org/_sh_do_api.php"
EPISODES_PER_SHOW = 20
EPISODES_BACKLOG  = 100   # "give me everything" — API caps at what's available


def fetch_show_episodes(show_key: str, num: int = EPISODES_PER_SHOW) -> list[dict]:
    """Fetch recent episodes for one show from the Confessor archive API."""
    url = f"{ARCHIVE_API}?req={show_key}&num={num}&json=1"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        logger.warning("Unexpected API response for %s: %s", show_key, type(data))
        return []
    return data


def sync_episodes(session: Session, num: int = EPISODES_PER_SHOW) -> dict[str, int]:
    """
    Fetch recent episodes for all enabled shows and create Episode records
    for any not already in the DB.

    Returns counts dict: {created, skipped, failed}.
    """
    shows = session.exec(
        select(Show).where(Show.archive_enabled == True, Show.is_gone == False)
    ).all()
    counts: dict[str, int] = {"created": 0, "skipped": 0, "failed": 0}

    for show in shows:
        try:
            entries = fetch_show_episodes(show.show_key, num=num)
        except Exception as e:
            logger.warning("Failed to fetch episodes for %s: %s", show.show_key, e)
            counts["failed"] += 1
            continue

        # Group entries by def_time — fragments of the same broadcast share a timestamp.
        # Each group becomes one Episode record with all fragment URLs in source_urls.
        groups: dict[datetime, list[dict]] = {}
        for entry in entries:
            mp3_url = entry.get("mp3")
            def_time = entry.get("def_time")
            if not mp3_url or not def_time:
                logger.debug("Skipping entry with missing mp3/def_time: %s", entry)
                continue
            air_dt = datetime.fromtimestamp(def_time, tz=timezone.utc).replace(tzinfo=None)
            groups.setdefault(air_dt, []).append(entry)

        # Track the most recent entry's weekday for schedule_day self-correction.
        # API returns newest-first, so the first key we encounter is the most recent.
        newest_air_day: str | None = None

        for air_dt, group in groups.items():
            if newest_air_day is None:
                newest_air_day = air_dt.strftime("%A")

            # Sort fragments chronologically by URL (filename encodes HHMMSS start time)
            group.sort(key=lambda e: e.get("mp3", ""))
            urls = [e["mp3"] for e in group]
            is_fragmented = len(urls) > 1

            # Use earliest expiry across all fragments — if any piece expires, assembly fails
            expires_at: datetime | None = None
            for e in group:
                if e.get("expires"):
                    exp = datetime.fromtimestamp(int(e["expires"]), tz=timezone.utc).replace(tzinfo=None)
                    if expires_at is None or exp < expires_at:
                        expires_at = exp

            existing = session.exec(
                select(Episode).where(
                    Episode.show_key == show.show_key,
                    Episode.air_datetime == air_dt,
                )
            ).first()

            if existing:
                # If we now see more fragments than the record has, update it.
                # This handles the case where the restart file appears after the initial scrape.
                existing_urls = json.loads(existing.source_urls or "[]")
                if set(urls) != set(existing_urls) and len(urls) > len(existing_urls):
                    existing.source_urls = json.dumps(urls)
                    existing.fragment_count = len(urls)
                    existing.is_fragmented = is_fragmented
                    if existing.status == "pending":
                        session.add(existing)
                        logger.info(
                            "Fragments updated for %s %s: %d → %d URL(s)",
                            show.show_key, air_dt.strftime("%Y-%m-%d"),
                            len(existing_urls), len(urls),
                        )
                counts["skipped"] += 1
                continue

            episode = Episode(
                show_key=show.show_key,
                air_datetime=air_dt,
                scheduled_duration_min=show.expected_duration_min,
                source_urls=json.dumps(urls),
                status="pending",
                fragment_count=len(urls),
                is_fragmented=is_fragmented,
                expires_at=expires_at,
            )
            session.add(episode)
            counts["created"] += 1
            logger.info(
                "Queued: %s %s%s",
                show.show_key, air_dt.strftime("%Y-%m-%d"),
                f" ({len(urls)} fragments)" if is_fragmented else "",
            )

        # Self-correct schedule_day if the most recent archive entry says otherwise.
        # Catches shows that moved days (e.g. Island Report: Tuesday → Thursday)
        # and newly discovered shows that have no schedule_day yet.
        if newest_air_day is not None and show.schedule_day != newest_air_day:
            old_day = show.schedule_day or "unset"
            logger.info(
                "Schedule day updated for %s: %s → %s (from archive)",
                show.show_key, old_day, newest_air_day,
            )
            show.schedule_day = newest_air_day
            session.add(show)

    session.commit()
    logger.info("sync_episodes complete: %s", counts)
    return counts
