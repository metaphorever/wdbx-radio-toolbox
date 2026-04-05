"""
Show discovery — cross-references two sources to find shows not yet in the DB.

PRIMARY SOURCE  — archive.wdbx.org show dropdown
  Contains every archived show as <option value="{slug}">{display name}</option>.
  This is the authoritative slug source; slug-guessing from the display name
  is unreliable (e.g. "Musical Kaleidoscope" → "musicalkaleidosco", not
  "musicalkaleidoscope").

SECONDARY SOURCE — confessor.wdbx.org/playlist/pub_sched.php
  CSS-positioned div layout; used to enrich new shows with schedule_day,
  schedule_time, and duration_min. Optional — new shows are added without
  these fields if the name can't be matched to the schedule.

Schedule page geometry:
  Column left-px → day: 0=Sun, 110=Mon, 220=Tue, 330=Wed, 440=Thu, 550=Fri, 660=Sat
  Time: top:0px = 5:00 AM, 80px = 1 hour
"""
import logging
import re
from html import unescape

import requests
from bs4 import BeautifulSoup
from sqlmodel import Session, select

from shared.config import get
from shared.models import Show

logger = logging.getLogger(__name__)

ARCHIVE_INDEX_URL = "https://archive.wdbx.org/"
SCHEDULE_URL = "https://confessor.wdbx.org/playlist/pub_sched.php"

COLUMN_MAP = {
    0:   "Sunday",
    110: "Monday",
    220: "Tuesday",
    330: "Wednesday",
    440: "Thursday",
    550: "Friday",
    660: "Saturday",
}
SCHEDULE_START_HOUR = 5
PX_PER_HOUR = 80

# Skip these even if they appear as options or show cells
EXCLUDE_KEYWORDS = {"tba", "open", "overnight", "filler"}
# The category options in the dropdown are numeric values — filter them out
_NUMERIC_RE = re.compile(r"^\d+$")


# ---------------------------------------------------------------------------
# Archive index scraper — authoritative slug list
# ---------------------------------------------------------------------------

def fetch_archive_show_list() -> list[dict]:
    """
    Scrape archive.wdbx.org and return all show {slug, display_name} pairs
    from the show-filter dropdown.

    The page contains a <select id="sh_altid"> with one <option> per archived
    show. Category options have numeric values and are filtered out.
    """
    url = get("archive.index_url", ARCHIVE_INDEX_URL)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    shows: list[dict] = []
    for m in re.finditer(r'<option[^>]*value=["\'](\w+)["\'][^>]*>([^<]+)', resp.text):
        slug = m.group(1)
        name = unescape(m.group(2).strip())

        if _NUMERIC_RE.match(slug):
            continue  # category option, not a show
        if _should_exclude(name):
            continue

        shows.append({"slug": slug, "display_name": name})

    logger.info("Archive index: found %d shows", len(shows))
    return shows


# ---------------------------------------------------------------------------
# Schedule page scraper — day/time enrichment
# ---------------------------------------------------------------------------

def _should_exclude(name: str) -> bool:
    low = name.lower()
    return any(kw in low for kw in EXCLUDE_KEYWORDS)


def _px_to_day(left_px: int) -> str | None:
    for col_px, day in COLUMN_MAP.items():
        if abs(left_px - col_px) <= 5:
            return day
    return None


def _px_to_time(top_px: int) -> str:
    total_minutes = SCHEDULE_START_HOUR * 60 + round(top_px / PX_PER_HOUR * 60)
    total_minutes %= 24 * 60
    h, m = divmod(total_minutes, 60)
    return f"{h:02d}{m:02d}00"


def _px_to_duration(height_px: int) -> int:
    raw = round(height_px / PX_PER_HOUR * 60)
    return max(30, round(raw / 30) * 30)


def _normalize_name(name: str) -> str:
    """Strip punctuation and lowercase for loose name matching."""
    return re.sub(r"[^\w\s]", "", name.lower()).strip()


def fetch_schedule_info() -> dict[str, dict]:
    """
    Scrape the Confessor schedule page and return a map of
    normalized_display_name → {day, schedule_time, duration_min}.
    Used to enrich new shows with schedule metadata.
    """
    url = get("confessor.schedule_url", SCHEDULE_URL)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    cat_pattern = re.compile(r"^cat_\d+$")
    result: dict[str, dict] = {}

    for div in soup.find_all("div", class_=cat_pattern):
        style = div.get("style", "")
        left_m = re.search(r"left:(\d+)px", style)
        top_m = re.search(r"top:(\d+)px", style)
        height_m = re.search(r"height:(\d+)px", style)
        if not (left_m and top_m and height_m):
            continue

        day = _px_to_day(int(left_m.group(1)))
        if day is None:
            continue

        inner = div.find_all("div", recursive=False)
        if not inner:
            continue
        name = inner[0].get_text(strip=True)
        if not name or _should_exclude(name):
            continue

        norm = _normalize_name(name)
        if norm not in result:
            result[norm] = {
                "day": day,
                "schedule_time": _px_to_time(int(top_m.group(1))),
                "duration_min": _px_to_duration(int(height_m.group(1))),
            }

    logger.info("Schedule page: found %d show entries", len(result))
    return result


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

def sync_new_shows(session: Session) -> dict[str, int]:
    """
    Compare archive show list against the DB and add any missing shows.

    Slugs come from archive.wdbx.org (authoritative).
    Day/time/duration enriched from the schedule page where the name matches.
    New shows are added with confirmed_by_manager=False so they appear in
    the onboarding wizard for review.

    Returns counts: {added, skipped, failed}
    """
    counts = {"added": 0, "skipped": 0, "failed": 0}

    try:
        archive_shows = fetch_archive_show_list()
    except Exception as e:
        logger.error("Failed to fetch archive show list: %s", e)
        counts["failed"] += 1
        return counts

    # Fetch schedule info for enrichment; non-fatal if it fails
    schedule_info: dict[str, dict] = {}
    try:
        schedule_info = fetch_schedule_info()
    except Exception as e:
        logger.warning("Could not fetch schedule page for enrichment: %s", e)

    existing = session.exec(select(Show)).all()
    existing_keys = {s.show_key for s in existing}
    existing_norm_names = {_normalize_name(s.display_name) for s in existing}

    for entry in archive_shows:
        slug = entry["slug"]
        display_name = entry["display_name"]

        if slug in existing_keys or _normalize_name(display_name) in existing_norm_names:
            counts["skipped"] += 1
            continue

        # Enrich with schedule page data if available
        sched = schedule_info.get(_normalize_name(display_name), {})

        show = Show(
            show_key=slug,
            display_name=display_name,
            archive_enabled=True,
            evergreen_default=True,
            expected_duration_min=sched.get("duration_min", 120),
            confirmed_by_manager=False,
            schedule_day=sched.get("day"),
            schedule_time=sched.get("schedule_time"),
            notes=(
                None if sched
                else "Auto-discovered from archive — set day/time in onboarding"
            ),
        )
        session.add(show)
        counts["added"] += 1

        logger.info(
            "New show: '%s' (%s) — %s %s",
            display_name, slug,
            sched.get("day", "day unknown"),
            sched.get("schedule_time", ""),
        )

    if counts["added"] > 0:
        session.commit()

    logger.info("sync_new_shows complete: %s", counts)
    return counts
