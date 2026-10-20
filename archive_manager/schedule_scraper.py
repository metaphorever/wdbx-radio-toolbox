"""
Show discovery and sync using the Confessor API.

Replaces the old HTML/CSS scrapers:
  - HTML archive.wdbx.org dropdown  → confessor_client.get_all_shows()
  - CSS pixel geometry schedule page → removed entirely

New shows are discovered from two sources, merged:
  - getshows: the recurring weekly grid
  - sched:    every dated airing in a window around today. Shows scheduled as
              individual dates appear only here — in Oct 2026 seven shows,
              Big Elvis Show among them, were on the air for two weeks without
              being archived because they never appeared in getshows.

Every newly added show is emailed so someone can confirm its details. Names
that look like placeholder slots (TBA, Open, Overnight, Filler) are added with
archiving off instead of being skipped, so a real show with one of those words
in its name gets flagged rather than silently never archived.

schedule_day/schedule_time come from the show's latest dated airing (station
local time). Shows found only in the weekly grid get schedule_day from their
first archive entry in scraper.py, as before.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from archive_manager.confessor_client import get_all_shows, get_gone_shows, get_schedule
from archive_manager.mailer import send_alert
from shared.config import get
from shared.models import Show
from shared.station_time import to_station

logger = logging.getLogger(__name__)

# "Open" only as the first word: the old substring match silently dropped real
# shows like "Balcony Is Open". Live placeholders look like "TBA OPEN Friday Morning".
PLACEHOLDER_PATTERN = re.compile(r"\b(tba|overnight|filler)\b|^\s*open\b", re.IGNORECASE)
DEFAULT_DURATION_MIN = 120


def _looks_like_placeholder(name: str) -> bool:
    return bool(PLACEHOLDER_PATTERN.search(name))


def _discover(now: datetime) -> dict[str, dict]:
    """
    Merge both schedule sources into {show_key: {name, duration_min, day, time}}.

    `now` is naive UTC. Raises only if both sources fail.
    """
    found: dict[str, dict] = {}
    errors: list[Exception] = []

    lookback = int(get("confessor.discovery_lookback_days", 35))
    lookahead = int(get("confessor.discovery_lookahead_days", 14))
    try:
        airings = get_schedule(now - timedelta(days=lookback), now + timedelta(days=lookahead))
    except Exception as e:
        logger.warning("Dated schedule (sched) unavailable: %s", e)
        errors.append(e)
        airings = []

    by_show: dict[str, list] = {}
    for a in airings:
        by_show.setdefault(a.show_key, []).append(a)
    for show_key, group in by_show.items():
        # Latest airing that has happened, else the next one coming up
        past = [a for a in group if a.air_datetime <= now]
        ref = max(past, key=lambda a: a.air_timestamp) if past else min(group, key=lambda a: a.air_timestamp)
        local = to_station(ref.air_datetime)
        found[show_key] = {
            "name": ref.display_name or show_key,
            "duration_min": ref.duration_seconds // 60 if ref.duration_seconds > 0 else None,
            "day": local.strftime("%A"),
            "time": local.strftime("%H%M%S"),
        }

    try:
        weekly = get_all_shows()
    except Exception as e:
        logger.warning("Weekly grid (getshows) unavailable: %s", e)
        errors.append(e)
        weekly = []

    for s in weekly:
        if s.show_key in found:
            continue  # the dated schedule is more precise
        found[s.show_key] = {
            "name": s.display_name or s.show_key,
            "duration_min": s.duration_minutes if s.duration_seconds > 0 else None,
            "day": None,
            # sh_shour is 0 for midnight shows AND for shows with stale/missing data.
            # Only use it when non-zero to avoid incorrectly stamping midnight on real shows.
            "time": s.start_time_str if s.start_seconds > 0 else None,
        }

    if len(errors) == 2:
        raise errors[-1]
    return found


def _send_new_show_alert(shows: list[Show]) -> None:
    lines = []
    for s in shows:
        when = " ".join(p for p in (s.schedule_day, s.schedule_time and f"{s.schedule_time[:2]}:{s.schedule_time[2:4]}") if p)
        status = ("archiving ON" if s.archive_enabled else
                  "archiving OFF: the name looks like a placeholder slot (TBA/Open/Overnight/Filler). "
                  "Enable it on the Archive page if it's a real show")
        lines.append(f"  - {s.display_name} ({s.show_key}), {when or 'time unknown'}, "
                     f"{s.expected_duration_min} min: {status}")
    plural = "s" if len(shows) != 1 else ""
    send_alert(
        f"New show{plural} on the schedule",
        f"Found {len(shows)} new show{plural} on the Confessor schedule and added "
        f"{'them' if plural else 'it'} to the Archive Manager:\n\n" + "\n".join(lines) +
        "\n\nPlease check the name, duration and archiving setting on the Archive page.",
    )


def sync_new_shows(session: Session, now: datetime | None = None) -> dict[str, int]:
    """
    Discover shows in Confessor not yet in the DB and add them.

    `now` (naive UTC) defaults to the current time.
    Returns counts: {added, skipped, failed}.
    """
    counts = {"added": 0, "skipped": 0, "failed": 0}

    try:
        found = _discover(now or datetime.now(timezone.utc).replace(tzinfo=None))
    except Exception as e:
        logger.error("Failed to fetch the schedule from the Confessor API: %s", e)
        counts["failed"] += 1
        return counts

    existing_keys = {row.show_key for row in session.exec(select(Show)).all()}

    added: list[Show] = []
    for show_key, info in sorted(found.items()):
        if show_key in existing_keys:
            counts["skipped"] += 1
            continue

        placeholder = _looks_like_placeholder(info["name"])
        notes = "Auto-discovered via Confessor API"
        if placeholder:
            notes += "; name looks like a placeholder slot, archiving off until confirmed"
        show = Show(
            show_key=show_key,
            display_name=info["name"],
            archive_enabled=not placeholder,
            evergreen_default=True,
            expected_duration_min=info["duration_min"] or DEFAULT_DURATION_MIN,
            confirmed_by_manager=False,
            schedule_day=info["day"],
            schedule_time=info["time"],
            notes=notes,
        )
        session.add(show)
        added.append(show)
        counts["added"] += 1
        logger.info("New show: '%s' (%s)%s", show.display_name, show_key,
                    " — archiving off, looks like a placeholder" if placeholder else "")

    if added:
        session.commit()
        _send_new_show_alert(added)

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
