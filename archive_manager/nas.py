"""
NAS health check and archive path resolution.

Write probe before each download job. Falls back to local staging
when the NAS is unreachable so no episode is ever silently skipped.
"""
import logging
import re
from datetime import date
from pathlib import Path

from shared.config import get

logger = logging.getLogger(__name__)


def sanitize_show_name(display_name: str) -> str:
    """
    Convert a show display name into a filesystem-safe folder/filename component.
    Strips special characters, preserves letters/numbers/spaces.
    e.g. "Isn't It Queer?" → "Isnt It Queer"
         "808 Jazz" → "808 Jazz"
    """
    name = re.sub(r"[^\w\s]", "", display_name)   # remove non-word non-space chars
    name = re.sub(r"\s+", " ", name).strip()        # collapse whitespace
    return name


def nas_is_writable() -> bool:
    """Return True if the NAS archive path exists and accepts a write probe."""
    mount = get("nas.mount_point", "/mnt/wdbx-share")
    archive_path = get("nas.archive_path", "/mnt/wdbx-share/Shows/AutoArchive")

    if not Path(mount).exists():
        logger.error("NAS mount point does not exist: %s", mount)
        return False

    try:
        archive = Path(archive_path)
        archive.mkdir(parents=True, exist_ok=True)
        probe = archive / ".write_probe"
        probe.write_text("probe")
        probe.unlink()
        return True
    except Exception as e:
        logger.error("NAS write probe failed: %s", e)
        return False


def get_archive_dir(show, air_date: date) -> Path:
    """
    Return the directory to save files for this show/date.
    Structure: {base}/{weekday}/{sanitized_show_name}/
    Uses NAS if writable, otherwise local staging (logged as warning).

    `show` may be a Show model instance or a plain slug string (legacy fallback).
    """
    if nas_is_writable():
        base = Path(get("nas.archive_path", "/mnt/wdbx-share/Shows/AutoArchive"))
    else:
        # show may be None or a string slug in fallback scenarios
        slug = getattr(show, "show_key", show) if show else "unknown"
        base = Path(get("local_staging.path", "/tmp/wdbx-staging"))
        logger.warning("NAS unavailable — routing %s to local staging: %s", slug, base)

    # Determine weekday folder and sanitized show name
    if hasattr(show, "schedule_day") and show.schedule_day:
        day_folder = show.schedule_day
    else:
        day_folder = "Unknown"

    if hasattr(show, "display_name") and show.display_name:
        show_folder = sanitize_show_name(show.display_name)
    else:
        # Fallback: show is a plain string slug
        show_folder = str(show)

    return base / day_folder / show_folder
