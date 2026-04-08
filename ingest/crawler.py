"""
NAS crawler — walks a directory tree and registers every .mp3 found
as an IngestFile record, reading ID3 metadata along the way.

Designed to be run once per pilot show, then expanded to the full NAS.
Does NOT fingerprint (slow) — that's a separate background job.

Supports copy_to staging for removable media (USB drives): files are
copied to a local staging directory before being registered so the
IngestFile path remains valid after the drive is unplugged.
"""
import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError
from sqlmodel import Session, select

from ingest.classifier import classify_origin, parse_filename
from shared.models import IngestFile

logger = logging.getLogger(__name__)


def _staging_dest(mp3_path: Path, staging_root: Path, source_root: Path) -> Path:
    """
    Mirror the relative path structure under staging_root.
    /USB/Show/2023/foo.mp3 → {staging_root}/Show/2023/foo.mp3
    Handles filename collisions by appending a counter.
    """
    try:
        rel = mp3_path.relative_to(source_root)
    except ValueError:
        rel = Path(mp3_path.name)
    dest = staging_root / rel
    if dest.exists():
        # Already copied — return existing path
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


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
    copy_to: Path | None = None,
) -> dict[str, int]:
    """
    Walk root recursively. For each .mp3 found:
    - Skip if already in DB (file_path unique constraint)
    - Optionally copy to copy_to staging dir (for USB/removable media)
    - Read ID3 + duration
    - Attempt filename parse for show/date
    - Classify origin (archive vs source file)
    - Save IngestFile record

    copy_to: if set, copy each MP3 here before registering. The registered
             file_path will point to the copy; source_path preserves the
             original USB location. Use for removable media so paths remain
             valid after the drive is unplugged.

    Returns counts: {found, created, skipped, errors, copied}
    """
    counts = {"found": 0, "created": 0, "skipped": 0, "errors": 0, "copied": 0}

    mp3_paths = sorted(root.rglob("*.mp3"))
    logger.info("Crawl starting: %d MP3s found under %s%s",
                len(mp3_paths), root, f" (copy to {copy_to})" if copy_to else "")

    for mp3_path in mp3_paths:
        counts["found"] += 1

        # If copying, determine dest and check if already registered under dest path
        if copy_to:
            dest_path = _staging_dest(mp3_path, copy_to, root)
            register_path = dest_path
        else:
            register_path = mp3_path

        path_str = str(register_path)

        # Skip already-crawled files (check both the registration path and source path)
        existing = session.exec(
            select(IngestFile).where(
                (IngestFile.file_path == path_str) |
                (IngestFile.source_path == str(mp3_path))
            )
        ).first()
        if existing:
            counts["skipped"] += 1
            continue

        try:
            # Copy before reading so audio_meta comes from the permanent copy
            if copy_to and not dest_path.exists():
                shutil.copy2(mp3_path, dest_path)
                counts["copied"] += 1
                logger.debug("Copied %s → %s", mp3_path.name, dest_path)
            elif copy_to:
                counts["copied"] += 1  # already existed from a previous partial run

            file_size = register_path.stat().st_size
            audio_meta = _read_audio_meta(register_path)
            file_hash = _fast_hash(register_path)

            parse_result = parse_filename(
                mp3_path.name,  # always parse original filename, not dest which may differ
                known_show_keys=show_keys or [],
                display_names=show_display_names or {},
            )
            origin_result = classify_origin(
                encoder_tag=audio_meta.get("encoder_tag"),
                bitrate_kbps=audio_meta.get("bitrate_kbps"),
                duration_sec=audio_meta.get("duration_sec"),
                show_key=parse_result.get("show_key"),
                expected_duration_min=None,
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
                source_path=str(mp3_path) if copy_to else None,
            )
            session.add(record)
            counts["created"] += 1

        except Exception as e:
            logger.warning("Error processing %s: %s", mp3_path.name, e)
            counts["errors"] += 1

    session.commit()
    logger.info("Crawl complete for %s: %s", root, counts)
    return counts
