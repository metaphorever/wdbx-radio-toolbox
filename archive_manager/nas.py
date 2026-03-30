"""
NAS health check and archive path resolution.

Write probe before each download job. Falls back to local staging
when the NAS is unreachable so no episode is ever silently skipped.
"""
import logging
from datetime import date
from pathlib import Path

from shared.config import get

logger = logging.getLogger(__name__)


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


def get_archive_dir(show_slug: str, air_date: date) -> Path:
    """
    Return the directory to save files for this show/date.
    Structure: {base}/{year}/{show-slug}/
    Uses NAS if writable, otherwise local staging (logged as warning).
    """
    if nas_is_writable():
        base = Path(get("nas.archive_path", "/mnt/wdbx-share/Shows/AutoArchive"))
    else:
        base = Path(get("local_staging.path", "/tmp/wdbx-staging"))
        logger.warning("NAS unavailable — routing %s to local staging: %s", show_slug, base)

    return base / str(air_date.year) / show_slug
