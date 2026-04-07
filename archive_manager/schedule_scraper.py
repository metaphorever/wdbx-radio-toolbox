"""
Show discovery and sync using the Confessor API.

Replaces the old HTML/CSS scrapers:
  - HTML archive.wdbx.org dropdown  → confessor_client.get_all_shows()
  - CSS pixel geometry schedule page → removed entirely

New show records are added without schedule_day/schedule_time — those fields
are set automatically during the first episode sync (scraper.sync_episodes)
from the actual air_datetime of the first archive entry, so they always
reflect when the show currently airs rather than when it was originally
scheduled.
"""
import logging

from sqlmodel import Session, select

from archive_manager.confessor_client import get_all_shows, get_gone_shows
from shared.models import Show

logger = logging.getLogger(__name__)

EXCLUDE_KEYWORDS = {"tba", "open", "overnight", "filler"}


def _should_exclude(name: str) -> bool:
    low = name.lower()
    return any(kw in low for kw in EXCLUDE_KEYWORDS)


def sync_new_shows(session: Session) -> dict[str, int]:
    """
    Discover shows in Confessor not yet in the DB and add them.

    Uses get_all_shows() (single request) for slug, name, and duration.
    schedule_time is set from sh_shour when non-zero; otherwise left blank
    and populated on first archive entry download.
    schedule_day is always left blank here — set from air_datetime in scraper.py.

    Returns counts: {added, skipped, failed}.
    """
    counts = {"added": 0, "skipped": 0, "failed": 0}

    try:
        api_shows = get_all_shows()
    except Exception as e:
        logger.error("Failed to fetch show list from Confessor API: %s", e)
        counts["failed"] += 1
        return counts

    # Deduplicate by show_key, keeping first occurrence (duration is a show
    # property so it's consistent across day slots)
    unique: dict[str, object] = {}
    for s in api_shows:
        if s.show_key not in unique:
            unique[s.show_key] = s

    existing_keys = {row.show_key for row in session.exec(select(Show)).all()}

    for show_key, s in unique.items():
        if _should_exclude(s.display_name):
            continue
        if show_key in existing_keys:
            counts["skipped"] += 1
            continue

        duration_min = s.duration_minutes if s.duration_seconds > 0 else 120
        # sh_shour is 0 for midnight shows AND for shows with stale/missing data.
        # Only use it when non-zero to avoid incorrectly stamping midnight on real shows.
        schedule_time = s.start_time_str if s.start_seconds > 0 else None

        show = Show(
            show_key=show_key,
            display_name=s.display_name,
            archive_enabled=True,
            evergreen_default=True,
            expected_duration_min=duration_min,
            confirmed_by_manager=False,
            schedule_time=schedule_time,
            notes="Auto-discovered via Confessor API",
        )
        session.add(show)
        counts["added"] += 1
        logger.info("New show: '%s' (%s)", s.display_name, show_key)

    if counts["added"] > 0:
        session.commit()

    logger.info("sync_new_shows complete: %s", counts)
    return counts


def sync_gone_shows(session: Session) -> int:
    """
    Mark shows as is_gone=True if they appear in the Confessor getgone endpoint.

    getgone returns all 46 shows with sh_altid present — no fuzzy matching needed.
    Only marks shows gone; never un-marks them (manual process to restore a show).

    Returns count of newly-marked shows.
    """
    try:
        gone_data = get_gone_shows()
    except Exception as e:
        logger.error("Failed to fetch gone shows from Confessor API: %s", e)
        return 0

    gone_keys = {g["sh_altid"] for g in gone_data if g.get("sh_altid")}
    if not gone_keys:
        return 0

    shows = session.exec(
        select(Show).where(Show.is_gone == False)
    ).all()

    updated = 0
    for show in shows:
        if show.show_key in gone_keys:
            show.is_gone = True
            session.add(show)
            updated += 1
            logger.info("Marked gone: %s (%s)", show.show_key, show.display_name)

    if updated:
        session.commit()
        logger.info("sync_gone_shows: marked %d show(s) as gone", updated)

    return updated
