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


def fetch_show_episodes(show_key: str) -> list[dict]:
    """Fetch recent episodes for one show from the Confessor archive API."""
    url = f"{ARCHIVE_API}?req={show_key}&num={EPISODES_PER_SHOW}&json=1"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        logger.warning("Unexpected API response for %s: %s", show_key, type(data))
        return []
    return data


def sync_episodes(session: Session) -> dict[str, int]:
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
            entries = fetch_show_episodes(show.show_key)
        except Exception as e:
            logger.warning("Failed to fetch episodes for %s: %s", show.show_key, e)
            counts["failed"] += 1
            continue

        for entry in entries:
            mp3_url = entry.get("mp3")
            def_time = entry.get("def_time")

            if not mp3_url or not def_time:
                logger.debug("Skipping entry with missing mp3/def_time: %s", entry)
                continue

            # Convert Unix timestamp to naive UTC datetime (consistent with rest of codebase)
            air_dt = datetime.fromtimestamp(def_time, tz=timezone.utc).replace(tzinfo=None)

            existing = session.exec(
                select(Episode).where(
                    Episode.show_key == show.show_key,
                    Episode.air_datetime == air_dt,
                )
            ).first()

            if existing:
                counts["skipped"] += 1
                continue

            episode = Episode(
                show_key=show.show_key,
                air_datetime=air_dt,
                scheduled_duration_min=show.expected_duration_min,
                source_urls=json.dumps([mp3_url]),
                status="pending",
                fragment_count=1,
                is_fragmented=False,
            )
            session.add(episode)
            counts["created"] += 1
            logger.info("Queued: %s %s", show.show_key, air_dt.strftime("%Y-%m-%d"))

    session.commit()
    logger.info("sync_episodes complete: %s", counts)
    return counts
