"""
Archive listing scraper for archive.wdbx.org.

PRIMARY data source per dev plan rule #1.
Fetches the Pacifica MP3 directory listing and discovers all WDBX files,
including restart fragments (multiple files for the same show+date).
URL construction in url.py is fallback only — this module is authoritative.
"""
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import requests
from sqlmodel import Session, select

from shared.config import get
from shared.models import Episode, Show

logger = logging.getLogger(__name__)

# Confirmed pattern (Q3 resolved 2026-03-23): wdbx_{YYMMDD}_{HHMMSS}{slug}.mp3
# No underscore between time component and slug.
FILENAME_RE = re.compile(r"^wdbx_(\d{6})_(\d{6})([a-z0-9]+)\.mp3$", re.IGNORECASE)


@dataclass
class ArchiveFile:
    filename: str
    date_str: str       # YYMMDD
    time_str: str       # HHMMSS
    slug: str
    air_datetime: datetime
    url: str


def parse_archive_listing(html: str, base_url: str) -> list[ArchiveFile]:
    """
    Extract all WDBX MP3 entries from a directory listing HTML page.
    Handles Apache-style listings (href="filename.mp3" or href="/mp3/filename.mp3").
    """
    files: list[ArchiveFile] = []
    # Match href values that end in a wdbx .mp3 filename
    for filename in re.findall(
        r'href="(?:[^"]*/)?(wdbx_[^"]+\.mp3)"', html, re.IGNORECASE
    ):
        m = FILENAME_RE.match(filename)
        if not m:
            logger.debug("Skipping unrecognized filename: %s", filename)
            continue

        date_str, time_str, slug = m.group(1), m.group(2), m.group(3)
        try:
            air_dt = datetime.strptime(f"{date_str}_{time_str}", "%y%m%d_%H%M%S")
        except ValueError:
            logger.warning("Could not parse datetime from filename: %s", filename)
            continue

        files.append(
            ArchiveFile(
                filename=filename,
                date_str=date_str,
                time_str=time_str,
                slug=slug,
                air_datetime=air_dt,
                url=base_url.rstrip("/") + "/" + filename,
            )
        )

    return files


def fetch_archive_listing() -> list[ArchiveFile]:
    """Fetch the Pacifica archive directory and return all parseable WDBX files."""
    base_url = get("pacifica.archive_base_url", "https://archive.wdbx.org/mp3/")
    logger.info("Fetching archive listing from %s", base_url)
    resp = requests.get(base_url, timeout=30)
    resp.raise_for_status()
    files = parse_archive_listing(resp.text, base_url)
    logger.info("Parsed %d WDBX MP3 files from archive listing", len(files))
    return files


def sync_episodes(session: Session) -> dict[str, int]:
    """
    Fetch the archive listing and create or update Episode records.

    - Groups files by (slug, date_str) to detect fragments.
    - Creates new Episode for each unseen group.
    - Updates fragment_count on existing pending episodes if new fragments appear.
    - Skips shows with archive_enabled=False.

    Returns counts dict: {created, updated, skipped, no_show}.
    """
    files = fetch_archive_listing()

    # Build slug → Show lookup
    shows = {s.show_key: s for s in session.exec(select(Show)).all()}

    counts: dict[str, int] = {"created": 0, "updated": 0, "skipped": 0, "no_show": 0}

    # Group by (slug, date_str) — each group is one logical episode (or its fragments)
    groups: dict[tuple[str, str], list[ArchiveFile]] = defaultdict(list)
    for f in files:
        groups[(f.slug, f.date_str)].append(f)

    for (slug, date_str), group_files in groups.items():
        show = shows.get(slug)
        if show is None:
            logger.debug("No show record for slug '%s' — skipping", slug)
            counts["no_show"] += 1
            continue

        if not show.archive_enabled:
            counts["skipped"] += 1
            continue

        # Sort fragments chronologically; earliest = canonical scheduled start
        group_files.sort(key=lambda f: f.air_datetime)
        canonical_dt = group_files[0].air_datetime
        is_fragmented = len(group_files) > 1
        source_urls = json.dumps([f.url for f in group_files])

        existing = session.exec(
            select(Episode).where(
                Episode.show_key == slug,
                Episode.air_datetime == canonical_dt,
            )
        ).first()

        if existing:
            # Update fragment info if we've discovered more fragments since last scrape
            if existing.status == "pending" and existing.fragment_count < len(group_files):
                existing.source_urls = source_urls
                existing.fragment_count = len(group_files)
                existing.is_fragmented = True
                session.add(existing)
                counts["updated"] += 1
                logger.info(
                    "Updated fragment count: %s %s → %d parts",
                    slug, date_str, len(group_files),
                )
            else:
                counts["skipped"] += 1
            continue

        episode = Episode(
            show_key=slug,
            air_datetime=canonical_dt,
            scheduled_duration_min=show.expected_duration_min,
            source_urls=source_urls,
            status="pending",
            fragment_count=len(group_files),
            is_fragmented=is_fragmented,
        )
        session.add(episode)
        counts["created"] += 1

        if is_fragmented:
            logger.info(
                "Fragment episode discovered: %s %s (%d parts)",
                slug, date_str, len(group_files),
            )

    session.commit()
    logger.info("sync_episodes complete: %s", counts)
    return counts
