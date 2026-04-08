"""
Segment fingerprinting and underwriting detection.

Sliding window approach:
  - Extract 30-second audio chunks every 15 seconds (50% overlap)
  - Compute chromaprint for each chunk
  - Compare against SegmentFingerprint table
  - Matches above threshold → candidate underwriting/station-ID segments
  - Merge overlapping matches into contiguous timestamp ranges

Also builds the SegmentFingerprint table from new audio (populates it
on first run, increments occurrence_count on subsequent runs).

Performance warning: ~6-12 minutes per 2hr file on modest CPU.
Designed to run as an overnight background job.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import librosa
    _LIBROSA_AVAILABLE = True
except ImportError:
    _LIBROSA_AVAILABLE = False
    logger.warning("librosa/numpy not available — segment matching disabled")

try:
    import chromaprint
    _CHROMAPRINT_AVAILABLE = True
except ImportError:
    _CHROMAPRINT_AVAILABLE = False

WINDOW_SEC = 30
HOP_SEC = 15
MATCH_THRESHOLD = 0.85
SEGMENT_SR = 22050


def _fingerprint_chunk(y: "np.ndarray", sr: int) -> str | None:
    """Compute chromaprint fingerprint for a numpy audio array."""
    if not _CHROMAPRINT_AVAILABLE:
        return None
    try:
        return chromaprint.fingerprint(y.astype(np.float32), sr)
    except Exception as e:
        logger.debug("Chunk fingerprint failed: %s", e)
        return None


def extract_segment_fingerprints(audio_path: Path) -> list[dict]:
    """
    Slide a window across the file.
    Returns list of {"start_sec": float, "end_sec": float, "fingerprint": str}.
    Returns [] if librosa/chromaprint unavailable.
    """
    if not _LIBROSA_AVAILABLE or not _CHROMAPRINT_AVAILABLE:
        return []
    try:
        y, sr = librosa.load(str(audio_path), sr=SEGMENT_SR, mono=True)
    except Exception as e:
        logger.warning("Could not load %s: %s", audio_path.name, e)
        return []

    window_samples = int(WINDOW_SEC * sr)
    hop_samples = int(HOP_SEC * sr)
    segments = []
    pos = 0
    while pos + window_samples <= len(y):
        fp = _fingerprint_chunk(y[pos: pos + window_samples], sr)
        if fp:
            segments.append({
                "start_sec": pos / sr,
                "end_sec": (pos + window_samples) / sr,
                "fingerprint": fp,
            })
        pos += hop_samples
    return segments


def match_against_library(
    segments: list[dict],
    session,
    show_key: str,
) -> list[dict]:
    """
    Compare extracted segment fingerprints against the SegmentFingerprint table.
    Returns matched segments: {"start_sec", "end_sec", "fingerprint_hash",
                                "classification", "similarity"}.
    Updates occurrence_count and cross_show_count in the DB.
    """
    from sqlmodel import select
    from ingest.fingerprinter import compare_fingerprints
    from shared.models import SegmentFingerprint

    if not segments:
        return []
    library = session.exec(select(SegmentFingerprint)).all()
    if not library:
        return []

    matches = []
    for seg in segments:
        best_score = 0.0
        best_lib = None
        for lib_fp in library:
            score = compare_fingerprints(seg["fingerprint"], lib_fp.fingerprint_hash)
            if score > best_score:
                best_score = score
                best_lib = lib_fp
        if best_lib and best_score >= MATCH_THRESHOLD:
            best_lib.occurrence_count += 1
            if best_lib.first_seen_show_key != show_key:
                best_lib.cross_show_count += 1
            session.add(best_lib)
            matches.append({
                "start_sec": seg["start_sec"],
                "end_sec": seg["end_sec"],
                "fingerprint_hash": best_lib.fingerprint_hash,
                "classification": best_lib.classification or "unknown",
                "similarity": best_score,
            })
    session.commit()
    return matches


def register_new_segments(
    segments: list[dict],
    session,
    show_key: str,
    existing_hashes: set[str] | None = None,
) -> int:
    """
    Add segments to SegmentFingerprint table if not already present.
    Returns count of newly added segments.
    """
    from shared.models import SegmentFingerprint
    from sqlmodel import select

    if existing_hashes is None:
        existing_hashes = set(session.exec(
            select(SegmentFingerprint.fingerprint_hash)
        ).all())

    added = 0
    for seg in segments:
        fp = seg["fingerprint"]
        if fp not in existing_hashes:
            session.add(SegmentFingerprint(
                fingerprint_hash=fp,
                duration_sec=seg["end_sec"] - seg["start_sec"],
                first_seen_show_key=show_key,
                classification="pending_review",
            ))
            existing_hashes.add(fp)
            added += 1
    if added:
        session.commit()
    return added


def merge_overlapping(matches: list[dict]) -> list[dict]:
    """
    Merge adjacent/overlapping match windows into contiguous ranges.
    """
    if not matches:
        return []
    sorted_m = sorted(matches, key=lambda m: m["start_sec"])
    merged = [dict(sorted_m[0])]
    for m in sorted_m[1:]:
        last = merged[-1]
        if m["start_sec"] <= last["end_sec"] + HOP_SEC:
            last["end_sec"] = max(last["end_sec"], m["end_sec"])
        else:
            merged.append(dict(m))
    return merged
