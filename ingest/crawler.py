"""
NAS crawler — walks a directory tree and registers every .mp3 found
as an IngestFile record, reading ID3 metadata along the way.

Designed to be run once per pilot show, then expanded to the full NAS.
Does NOT fingerprint (slow) — that's a separate background job.
"""
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError
from sqlmodel import Session, select

from ingest.classifier import classify_origin, parse_filename
from shared.models import IngestFile

logger = logging.getLogger(__name__)


def _fast_hash(path: Path) -> str:
    """MD5 of first 64KB + last 64KB. Fast proxy for whole-file dedup."""
    h = hashlib.md5()
    chunk = 65536
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        try:
            f.seek(-chunk, 2)
        except OSError:
            pass
        h.update(f.read(chunk))
    return h.hexdigest()


def _read_audio_meta(path: Path) -> dict:
    """Read duration, bitrate, encoder tag from MP3. Returns {} on failure."""
    meta = {}
    try:
        audio = MP3(str(path))
        meta["duration_sec"] = audio.info.length
        meta["bitrate_kbps"] = audio.info.bitrate // 1000
    except Exception as e:
        logger.debug("Could not read audio info for %s: %s", path.name, e)
    try:
        tags = ID3(str(path))
        for frame_id in ("TENC", "TSSE"):
            frame = tags.get(frame_id)
            if frame:
                meta["encoder_tag"] = str(frame)
                break
    except (ID3NoHeaderError, Exception):
        pass
    return meta


def crawl_directory(
    root: Path,
    session: Session,
    show_keys: list[str] | None = None,
    show_display_names: dict[str, str] | None = None,
) -> dict[str, int]:
    """
    Walk root recursively. For each .mp3 found:
    - Skip if already in DB (file_path unique constraint)
    - Read ID3 + duration
    - Attempt filename parse for show/date
    - Classify origin (archive vs source file)
    - Save IngestFile record

    show_keys: if provided, only auto-match against these show keys
    show_display_names: {show_key: display_name} for fuzzy matching

    Returns counts: {found, created, skipped, errors}
    """
    counts = {"found": 0, "created": 0, "skipped": 0, "errors": 0}

    mp3_paths = sorted(root.rglob("*.mp3"))
    logger.info("Crawl starting: %d MP3s found under %s", len(mp3_paths), root)

    for mp3_path in mp3_paths:
        counts["found"] += 1
        path_str = str(mp3_path)

        # Skip already-crawled files
        existing = session.exec(
            select(IngestFile).where(IngestFile.file_path == path_str)
        ).first()
        if existing:
            counts["skipped"] += 1
            continue

        try:
            file_size = mp3_path.stat().st_size
            audio_meta = _read_audio_meta(mp3_path)
            file_hash = _fast_hash(mp3_path)

            parse_result = parse_filename(
                mp3_path.name,
                known_show_keys=show_keys or [],
                display_names=show_display_names or {},
            )
            origin_result = classify_origin(
                encoder_tag=audio_meta.get("encoder_tag"),
                bitrate_kbps=audio_meta.get("bitrate_kbps"),
                duration_sec=audio_meta.get("duration_sec"),
                show_key=parse_result.get("show_key"),
                expected_duration_min=None,  # caller can enrich later
            )

            # Determine initial status
            has_show = bool(parse_result.get("show_key"))
            has_date = bool(parse_result.get("air_datetime"))
            if has_show and has_date:
                status = "matched"
            elif has_show or has_date or audio_meta.get("duration_sec", 0) >= 1800:
                status = "needs_review"
            else:
                status = "pending"

            record = IngestFile(
                file_path=path_str,
                file_size_bytes=file_size,
                duration_sec=audio_meta.get("duration_sec"),
                show_key=parse_result.get("show_key"),
                show_key_confidence=parse_result.get("show_key_confidence", "none"),
                air_datetime=parse_result.get("air_datetime"),
                air_date_confidence=parse_result.get("air_date_confidence", "none"),
                file_origin=origin_result["origin"],
                origin_confidence=origin_result["confidence"],
                encoder_tag=audio_meta.get("encoder_tag"),
                bitrate_kbps=audio_meta.get("bitrate_kbps"),
                file_hash=file_hash,
                status=status,
                crawl_root=str(root),
            )
            session.add(record)
            counts["created"] += 1

        except Exception as e:
            logger.warning("Error processing %s: %s", mp3_path.name, e)
            counts["errors"] += 1

    session.commit()
    logger.info("Crawl complete for %s: %s", root, counts)
    return counts
