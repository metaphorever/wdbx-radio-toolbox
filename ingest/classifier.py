"""
Heuristics for determining whether an MP3 is an archive download
(re-encoded from stream) or a human-submitted source file.

Also handles filename parsing for show/date matching.
"""
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Encoder signatures ──────────────────────────────────────────────────────
# Tags that reliably indicate a stream re-encode (Lavf = FFmpeg/libav)
ARCHIVE_ENCODER_PATTERNS = [
    r"Lavf",           # FFmpeg libavformat — stream capture
    r"LAME\s*3\.[89]", # LAME older versions common in streaming
]
# Tags that indicate human-originated files
SOURCE_ENCODER_PATTERNS = [
    r"Audacity",
    r"Adobe",
    r"GarageBand",
    r"Logic",
    r"Pro Tools",
    r"audiojoiner",    # the specific web tool WDBX uses
    r"mp3joiner",
    r"Reaper",
    r"Hindenburg",
]

ARCHIVE_BITRATE_MAX = 160     # stream archive is ≤128 kbps; give some headroom
SOURCE_BITRATE_MIN  = 160     # original files often 192/256/320

# Duration deviation from nominal slot that suggests a human file
# Archive captures: ±15 seconds (7-sec overlap constant + variance)
ARCHIVE_DURATION_TOLERANCE_SEC = 20


def classify_origin(
    encoder_tag: str | None,
    bitrate_kbps: int | None,
    duration_sec: float | None,
    show_key: str | None,
    expected_duration_min: int | None,
) -> dict:
    """
    Returns {"origin": "archive"|"source_file"|"unknown", "confidence": "auto"|"none", "signals": [...]}
    """
    signals = []
    archive_score = 0
    source_score = 0

    if encoder_tag:
        enc = encoder_tag.strip()
        for pat in ARCHIVE_ENCODER_PATTERNS:
            if re.search(pat, enc, re.IGNORECASE):
                archive_score += 3
                signals.append(f"encoder:{enc!r} → archive")
                break
        for pat in SOURCE_ENCODER_PATTERNS:
            if re.search(pat, enc, re.IGNORECASE):
                source_score += 3
                signals.append(f"encoder:{enc!r} → source_file")
                break

    if bitrate_kbps is not None:
        if bitrate_kbps <= ARCHIVE_BITRATE_MAX:
            archive_score += 2
            signals.append(f"bitrate:{bitrate_kbps}kbps → archive")
        elif bitrate_kbps >= SOURCE_BITRATE_MIN:
            source_score += 2
            signals.append(f"bitrate:{bitrate_kbps}kbps → source_file")

    if duration_sec is not None and expected_duration_min is not None:
        slot_sec = expected_duration_min * 60
        deviation = abs(duration_sec - slot_sec)
        if deviation <= ARCHIVE_DURATION_TOLERANCE_SEC:
            archive_score += 2
            signals.append(f"duration:{duration_sec:.0f}s ≈ slot {slot_sec}s → archive")
        elif deviation > 60:
            source_score += 1
            signals.append(f"duration:{duration_sec:.0f}s deviates {deviation:.0f}s from slot → source_file hint")

    if archive_score > source_score and archive_score >= 2:
        return {"origin": "archive", "confidence": "auto", "signals": signals}
    if source_score > archive_score and source_score >= 2:
        return {"origin": "source_file", "confidence": "auto", "signals": signals}
    return {"origin": "unknown", "confidence": "none", "signals": signals}


# ── Filename parsing ─────────────────────────────────────────────────────────

# Pattern 1: wdbx_YYMMDD_HHMMSS{slug}.mp3  (raw archive download)
_ARCHIVE_RAW = re.compile(
    r"wdbx_(\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})([a-z0-9_-]*)\.mp3",
    re.IGNORECASE,
)

# Pattern 2: YYYY-MM-DD [ShowName] - WDBX.mp3  (new toolbox)
_TOOLBOX_NEW = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+\[([^\]]+)\]\s*-\s*WDBX\.mp3",
    re.IGNORECASE,
)

# Pattern 3: YYMMDD_HHMMSS_showname.mp3  (old toolbox variant)
_TOOLBOX_OLD = re.compile(
    r"(\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})_(.+)\.mp3",
    re.IGNORECASE,
)

# Pattern 4: ShowName YYYY-MM-DD.mp3 or ShowName_YYYY-MM-DD.mp3
_SHOW_DATE = re.compile(
    r"(.+?)[\s_-]+(\d{4}-\d{2}-\d{2})\.mp3",
    re.IGNORECASE,
)


def _normalize(s: str) -> str:
    """Lowercase, strip punctuation/spaces for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _match_show(
    candidate: str,
    known_show_keys: list[str],
    display_names: dict[str, str],
) -> tuple[str | None, str]:
    """Try to match a candidate string to a show key. Returns (show_key, confidence)."""
    norm_candidate = _normalize(candidate)
    if not norm_candidate:
        return None, "none"

    # Exact show_key match
    for key in known_show_keys:
        if norm_candidate == _normalize(key):
            return key, "filename_exact"

    # Exact display_name match
    for key, name in display_names.items():
        if norm_candidate == _normalize(name):
            return key, "filename_exact"

    # Substring: candidate contains show key or vice versa
    for key in known_show_keys:
        nk = _normalize(key)
        if nk and (nk in norm_candidate or norm_candidate in nk) and len(nk) >= 4:
            return key, "filename_fuzzy"

    for key, name in display_names.items():
        nn = _normalize(name)
        if nn and (nn in norm_candidate or norm_candidate in nn) and len(nn) >= 4:
            return key, "filename_fuzzy"

    return None, "none"


def _yymmdd_to_date(yy: str, mm: str, dd: str) -> datetime | None:
    try:
        year = 2000 + int(yy)
        return datetime(year, int(mm), int(dd))
    except ValueError:
        return None


def parse_filename(
    filename: str,
    known_show_keys: list[str],
    display_names: dict[str, str],
) -> dict:
    """
    Try to extract show_key and air_datetime from a filename.

    Returns dict with optional keys:
      show_key, show_key_confidence, air_datetime, air_date_confidence, slug
    """
    result: dict = {}

    # Pattern 1: raw archive
    m = _ARCHIVE_RAW.match(filename)
    if m:
        yy, mo, dd, hh, mi, ss, slug = m.groups()
        air_dt = _yymmdd_to_date(yy, mo, dd)
        if air_dt:
            air_dt = air_dt.replace(hour=int(hh), minute=int(mi), second=int(ss))
            result["air_datetime"] = air_dt
            result["air_date_confidence"] = "filename"
        if slug:
            show_key, conf = _match_show(slug, known_show_keys, display_names)
            if show_key:
                result["show_key"] = show_key
                result["show_key_confidence"] = conf
                result["slug"] = slug
        return result

    # Pattern 2: new toolbox
    m = _TOOLBOX_NEW.match(filename)
    if m:
        date_str, show_name = m.groups()
        try:
            result["air_datetime"] = datetime.strptime(date_str, "%Y-%m-%d")
            result["air_date_confidence"] = "filename"
        except ValueError:
            pass
        show_key, conf = _match_show(show_name, known_show_keys, display_names)
        if show_key:
            result["show_key"] = show_key
            result["show_key_confidence"] = conf
        return result

    # Pattern 3: old toolbox
    m = _TOOLBOX_OLD.match(filename)
    if m:
        yy, mo, dd, hh, mi, ss, name = m.groups()
        air_dt = _yymmdd_to_date(yy, mo, dd)
        if air_dt:
            air_dt = air_dt.replace(hour=int(hh), minute=int(mi), second=int(ss))
            result["air_datetime"] = air_dt
            result["air_date_confidence"] = "filename"
        show_key, conf = _match_show(name, known_show_keys, display_names)
        if show_key:
            result["show_key"] = show_key
            result["show_key_confidence"] = conf
        return result

    # Pattern 4: ShowName date
    m = _SHOW_DATE.match(filename)
    if m:
        name, date_str = m.groups()
        try:
            result["air_datetime"] = datetime.strptime(date_str, "%Y-%m-%d")
            result["air_date_confidence"] = "filename"
        except ValueError:
            pass
        show_key, conf = _match_show(name, known_show_keys, display_names)
        if show_key:
            result["show_key"] = show_key
            result["show_key_confidence"] = conf
        return result

    # No pattern matched — try show match on full stem
    stem = Path(filename).stem
    show_key, conf = _match_show(stem, known_show_keys, display_names)
    if show_key:
        result["show_key"] = show_key
        result["show_key_confidence"] = conf

    return result
