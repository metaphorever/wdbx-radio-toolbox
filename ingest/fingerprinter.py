"""
Audio fingerprinting for episode-level dedup and re-air detection.

Uses chromaprint (via pyacoustid + fpcalc binary) for fingerprinting.
Episode-level: whole-file fingerprint for exact/near dedup.
Segment-level: sliding window on 30-second chunks (slow — background job).

Performance estimate:
  Episode-level: ~5-10s per 2hr file (fpcalc samples, doesn't read full file)
  Segment-level: ~6-12 min per 2hr file (reads entire audio)
  Full backlog (7800 eps × 2hr): segment fingerprinting takes weeks overnight.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import acoustid
    _ACOUSTID_AVAILABLE = True
except ImportError:
    _ACOUSTID_AVAILABLE = False
    logger.warning("pyacoustid not available — fingerprinting disabled")


SIMILARITY_REAIR_THRESHOLD = 0.82   # above this → likely re-air of same content
SIMILARITY_GRAY_ZONE_LOW   = 0.60   # 0.60–0.82 → needs human review


def fingerprint_file(path: Path) -> tuple[str | None, float | None]:
    """
    Compute chromaprint fingerprint for a file using fpcalc.
    Returns (fingerprint_string, duration_seconds) or (None, None) on failure.

    fpcalc samples the audio (not full file) — fast enough for episode-level dedup.
    """
    if not _ACOUSTID_AVAILABLE:
        return None, None
    try:
        duration, fingerprint = acoustid.fingerprint_file(str(path))
        return fingerprint, duration
    except Exception as e:
        logger.warning("Fingerprint failed for %s: %s", path.name, e)
        return None, None


def compare_fingerprints(fp1: str, fp2: str) -> float:
    """
    Compare two chromaprint fingerprint strings.
    Returns similarity score 0.0–1.0.
    Uses bit-level hamming distance on the decoded integer arrays.
    """
    if not _ACOUSTID_AVAILABLE:
        return 0.0
    try:
        import chromaprint
        raw1 = chromaprint.decode_fingerprint(fp1)[0]
        raw2 = chromaprint.decode_fingerprint(fp2)[0]
        if not raw1 or not raw2:
            return 0.0
        length = min(len(raw1), len(raw2))
        if length == 0:
            return 0.0
        # Hamming similarity: fraction of matching bits across all 32-bit integers
        matching_bits = sum(32 - bin(a ^ b).count("1") for a, b in zip(raw1[:length], raw2[:length]))
        return matching_bits / (length * 32)
    except Exception as e:
        logger.debug("Fingerprint comparison failed: %s", e)
        return 0.0


def find_duplicates_by_hash(ingest_files: list) -> list[tuple]:
    """
    Group IngestFile records by file_hash.
    Returns list of (canonical, [duplicates]) tuples.
    canonical is chosen by: source_file > archive > longest duration.
    """
    from collections import defaultdict
    hash_groups: dict[str, list] = defaultdict(list)
    for f in ingest_files:
        if f.file_hash:
            hash_groups[f.file_hash].append(f)

    result = []
    for file_hash, group in hash_groups.items():
        if len(group) < 2:
            continue
        canonical = _pick_canonical(group)
        dupes = [f for f in group if f.id != canonical.id]
        result.append((canonical, dupes))
    return result


def _pick_canonical(files: list) -> object:
    """
    From a group of files with the same content, pick the best canonical.
    Priority: source_file > archive > longest duration > earliest crawled.
    """
    def score(f):
        origin_score = {"source_file": 2, "archive": 1, "unknown": 0}.get(f.file_origin, 0)
        duration = f.duration_sec or 0
        return (origin_score, duration)

    return max(files, key=score)
