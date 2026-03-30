"""
Seed the Show table from the legacy showst.txt schedule file.

Runs once (or on demand) to bootstrap the schedule. The onboarding wizard
refines durations and marks shows confirmed_by_manager=True.
Records that have been confirmed by the manager are never overwritten.
"""
import logging
from pathlib import Path

from sqlmodel import Session, select

from shared.config import get
from shared.models import Show

logger = logging.getLogger(__name__)

# Pulled from config.yaml known_duration_overrides — hardcoded here as bootstrap values.
# Source of truth after first run is the shows table.
KNOWN_DURATION_OVERRIDES: dict[str, int] = {
    "timecapsul": 180,  # The Time Capsule — legitimately 3 hours
}

DEFAULT_DURATION_MIN = 120  # Most WDBX shows are 2 hours

# Slugs or display name fragments that indicate fill/overnight slots
EXCLUDE_NAME_KEYWORDS = {"tba", "open", "overnight", "filler"}


def _should_disable(display_name: str) -> bool:
    name_lower = display_name.lower()
    return any(kw in name_lower for kw in EXCLUDE_NAME_KEYWORDS)


def seed_from_file(session: Session, path: str | None = None) -> dict[str, int]:
    """
    Parse showst.txt and insert/update Show records.
    - Existing records not yet confirmed by manager: display_name and archive_enabled updated.
    - Confirmed records: untouched.
    - New records: created with defaults.

    Returns counts: {created, updated, skipped}.
    """
    if path is None:
        ref = Path(__file__).parent.parent / "reference" / "showst.txt"
        path = str(ref)

    counts = {"created": 0, "updated": 0, "skipped": 0}

    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    for line in lines:
        # Format: day,HHMMSS,slug,display_name,flag
        parts = line.split(",", 4)
        if len(parts) < 5:
            logger.warning("Malformed showst.txt line — skipping: %s", line)
            continue

        day, time_str, slug, display_name, flag = (p.strip() for p in parts)
        archive_enabled = flag == "1" and not _should_disable(display_name)
        duration_min = KNOWN_DURATION_OVERRIDES.get(slug, DEFAULT_DURATION_MIN)

        existing = session.exec(select(Show).where(Show.show_key == slug)).first()

        if existing:
            if existing.confirmed_by_manager:
                counts["skipped"] += 1
                continue
            existing.display_name = display_name
            existing.archive_enabled = archive_enabled
            existing.schedule_day = day
            existing.schedule_time = time_str
            session.add(existing)
            counts["updated"] += 1
        else:
            show = Show(
                show_key=slug,
                display_name=display_name,
                archive_enabled=archive_enabled,
                evergreen_default=True,
                expected_duration_min=duration_min,
                confirmed_by_manager=False,
                schedule_day=day,
                schedule_time=time_str,
            )
            session.add(show)
            counts["created"] += 1

    session.commit()
    logger.info("Seeder complete: %s", counts)
    return counts
