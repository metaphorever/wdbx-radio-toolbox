"""
Fragment-aware download engine.

Downloads one or more source URLs for an episode.
If multiple URLs (restart fragments): downloads all to a /fragments
subdirectory, then concatenates with ffmpeg in chronological order.
Retries each file up to 3 times with exponential backoff.
All state changes written to the Episode record immediately so jobs
are safe to restart after a crash.
"""
import json
import logging
import subprocess
import time
from pathlib import Path

import requests
from sqlmodel import Session

from archive_manager.nas import get_archive_dir
from shared.config import get
from shared.models import Episode, SystemEvent

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
CHUNK_SIZE = 65_536  # 64 KB


def _download_file(url: str, dest: Path) -> bool:
    """Stream-download url to dest. Returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("Downloading %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            resp = requests.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    f.write(chunk)
            size = dest.stat().st_size
            logger.info("Saved %s (%s bytes)", dest.name, f"{size:,}")
            return True
        except Exception as e:
            logger.warning("Attempt %d failed for %s: %s", attempt, url, e)
            if dest.exists():
                dest.unlink()          # don't leave a partial file
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    logger.error("All %d attempts failed: %s", MAX_RETRIES, url)
    return False


def _concat_with_ffmpeg(fragment_paths: list[Path], output: Path) -> bool:
    """Concatenate audio fragments using ffmpeg concat demuxer (stream copy, lossless)."""
    concat_list = fragment_paths[0].parent / "concat.txt"
    with open(concat_list, "w") as f:
        for p in fragment_paths:
            f.write(f"file '{p.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    concat_list.unlink(missing_ok=True)

    if result.returncode != 0:
        logger.error("ffmpeg concat failed:\n%s", result.stderr.decode(errors="replace"))
        return False

    logger.info("Assembled %d fragment(s) → %s", len(fragment_paths), output.name)
    return True


def _fail_episode(session: Session, episode: Episode, message: str) -> None:
    """Mark episode failed, create a SystemEvent, and commit."""
    episode.status = "failed"
    session.add(episode)
    session.add(SystemEvent(
        severity="error",
        message=f"Download failed — {episode.show_key} {episode.air_datetime:%Y-%m-%d %H:%M}: {message}",
    ))
    session.commit()


def download_episode(episode: Episode, session: Session) -> bool:
    """
    Download (and if needed assemble) one episode.
    Updates episode.status throughout. Returns True on success.
    The session is committed on each status change so restarts are safe.
    """
    if not episode.source_urls:
        logger.error("Episode %d has no source_urls", episode.id)
        return False

    urls: list[str] = json.loads(episode.source_urls)
    dest_dir = get_archive_dir(episode.show_key, episode.air_datetime.date())
    dest_dir.mkdir(parents=True, exist_ok=True)

    date_tag = episode.air_datetime.strftime("%y%m%d_%H%M%S")
    final_filename = f"{episode.show_key}_{date_tag}.mp3"
    final_path = dest_dir / final_filename

    # Mark in-progress immediately
    episode.status = "downloading"
    session.add(episode)
    session.commit()

    try:
        if len(urls) == 1:
            ok = _download_file(urls[0], final_path)
        else:
            # Fragment episode: download each to a /fragments subdir, then concat
            frag_dir = dest_dir / "fragments"
            frag_dir.mkdir(parents=True, exist_ok=True)
            frag_paths: list[Path] = []

            for i, url in enumerate(urls, 1):
                frag_dest = frag_dir / Path(url).name
                if not _download_file(url, frag_dest):
                    _fail_episode(session, episode, f"Fragment {i}/{len(urls)} failed: {url}")
                    return False
                frag_paths.append(frag_dest)

            ok = _concat_with_ffmpeg(frag_paths, final_path)

        if not ok:
            _fail_episode(session, episode, "Download or concat failed after all retries")
            return False

        # Success
        episode.status = "downloaded"
        episode.local_path = str(final_path)
        episode.fragmented_source = episode.is_fragmented

        nas_base = get("nas.archive_path", "")
        if nas_base and str(final_path).startswith(nas_base):
            episode.nas_path = str(final_path)

        session.add(episode)
        session.commit()
        logger.info("Episode %d downloaded: %s", episode.id, final_path)
        return True

    except Exception as e:
        logger.exception("Unexpected error downloading episode %d", episode.id)
        _fail_episode(session, episode, str(e))
        return False


def copy_episode_to_nas(episode: Episode, session: Session) -> bool:
    """
    Copy a locally-staged episode to NAS.
    Used for the 'Copy to NAS' button when NAS was offline at download time.
    """
    if not episode.local_path:
        return False

    from archive_manager.nas import nas_is_writable, get_archive_dir
    if not nas_is_writable():
        logger.error("NAS still not writable — cannot copy episode %d", episode.id)
        return False

    src = Path(episode.local_path)
    if not src.exists():
        logger.error("Source file missing: %s", src)
        return False

    nas_dir = get_archive_dir(episode.show_key, episode.air_datetime.date())
    nas_dir.mkdir(parents=True, exist_ok=True)
    dest = nas_dir / src.name

    import shutil
    try:
        shutil.copy2(src, dest)
        episode.nas_path = str(dest)
        session.add(episode)
        session.commit()
        logger.info("Copied %s → %s", src.name, dest)
        return True
    except Exception as e:
        logger.error("Copy to NAS failed: %s", e)
        return False
