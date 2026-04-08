"""
Re-air detection — compare fingerprints within a show to find re-broadcasts.

Logic:
  - For each show, load all IngestFile records that have a fingerprint
  - Compare pairwise by date order
  - If similarity > REAIR_THRESHOLD: mark later date as re-air of earlier
  - Gray zone (GRAY_LOW..REAIR_THRESHOLD): flag for human review
  - Updates CanonicalEpisode.is_reair and original_canonical_id
  - Returns summary counts
"""
import logging
from collections import defaultdict
from datetime import datetime

from sqlmodel import Session, select

from ingest.fingerprinter import compare_fingerprints, SIMILARITY_REAIR_THRESHOLD, SIMILARITY_GRAY_ZONE_LOW
from shared.models import CanonicalEpisode, IngestFile, ScheduleNote

logger = logging.getLogger(__name__)


def detect_reairs(session: Session, show_key: str) -> dict[str, int]:
    """
    Run re-air detection for one show.
    Returns {"marked_reair": N, "gray_zone": N, "compared": N}
    """
    # Load fingerprinted files for this show, sorted by air_datetime ascending
    files = session.exec(
        select(IngestFile).where(
            IngestFile.show_key == show_key,
            IngestFile.fingerprint != None,
            IngestFile.air_datetime != None,
            IngestFile.status != "ignored",
        )
    ).all()
    files = sorted(files, key=lambda f: f.air_datetime)

    counts = {"marked_reair": 0, "gray_zone": 0, "compared": 0}
    if len(files) < 2:
        return counts

    # Group by date (date only) to get one representative per broadcast date
    by_date: dict[str, IngestFile] = {}
    for f in files:
        dk = f.air_datetime.strftime("%Y-%m-%d")
        # Prefer canonical/source_file; otherwise keep first seen
        existing = by_date.get(dk)
        if not existing:
            by_date[dk] = f
        elif f.file_origin == "source_file" and existing.file_origin != "source_file":
            by_date[dk] = f

    date_keys = sorted(by_date.keys())
    representatives = [by_date[dk] for dk in date_keys]

    # Compare each episode against all earlier ones
    # Stop at the first match above threshold — the earliest match is the original
    for i, later in enumerate(representatives[1:], 1):
        best_score = 0.0
        best_original: IngestFile | None = None

        for earlier in representatives[:i]:
            if not earlier.fingerprint or not later.fingerprint:
                continue
            counts["compared"] += 1
            score = compare_fingerprints(earlier.fingerprint, later.fingerprint)
            if score > best_score:
                best_score = score
                best_original = earlier

        if best_original is None:
            continue

        if best_score >= SIMILARITY_REAIR_THRESHOLD:
            # Mark canonical as re-air
            canonical = session.exec(
                select(CanonicalEpisode).where(
                    CanonicalEpisode.show_key == show_key,
                    CanonicalEpisode.true_air_date == later.air_datetime,
                )
            ).first()
            if canonical and not canonical.is_reair:
                canonical.is_reair = True
                # Find original canonical
                orig_canonical = session.exec(
                    select(CanonicalEpisode).where(
                        CanonicalEpisode.show_key == show_key,
                        CanonicalEpisode.true_air_date == best_original.air_datetime,
                    )
                ).first()
                if orig_canonical:
                    canonical.original_canonical_id = orig_canonical.id
                session.add(canonical)
                counts["marked_reair"] += 1
                logger.info(
                    "Re-air detected: %s %s is re-air of %s (score %.2f)",
                    show_key,
                    later.air_datetime.strftime("%Y-%m-%d"),
                    best_original.air_datetime.strftime("%Y-%m-%d"),
                    best_score,
                )

        elif best_score >= SIMILARITY_GRAY_ZONE_LOW:
            counts["gray_zone"] += 1
            logger.info(
                "Gray zone: %s %s vs %s score=%.2f — needs human review",
                show_key,
                later.air_datetime.strftime("%Y-%m-%d"),
                best_original.air_datetime.strftime("%Y-%m-%d"),
                best_score,
            )

    session.commit()
    return counts
