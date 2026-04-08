"""
Episode analyzer — orchestrates EAS detection, segment matching,
and Evergreen Score computation. Writes AnalysisResult records.

Evergreen Score (0–100):
  Start at 100. Deduct for:
    -30 if EAS detected
    -10 per underwriting segment found (max -40)
    -20 if suspect_quality flagged on episode
  Clamp to 0.

Analysis version bumped when algorithm changes so old results can be
re-run selectively.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from processor.eas_detector import detect_eas
from processor.segment_matcher import (
    extract_segment_fingerprints,
    match_against_library,
    merge_overlapping,
    register_new_segments,
)
from shared.config import get
from shared.models import AnalysisResult, Episode, Show, SystemEvent

logger = logging.getLogger(__name__)

ANALYSIS_VERSION = "1.0"


def _episode_audio_path(episode: Episode) -> Path | None:
    """Return the best available local audio file for an episode."""
    if episode.nas_path and Path(episode.nas_path).exists():
        return Path(episode.nas_path)
    if episode.local_path and Path(episode.local_path).exists():
        return Path(episode.local_path)
    return None


def _compute_evergreen_score(
    eas_detected: bool,
    underwriting_segments: list[dict],
    suspect_quality: bool,
) -> int:
    score = 100
    if eas_detected:
        score -= 30
    score -= min(len(underwriting_segments) * 10, 40)
    if suspect_quality:
        score -= 20
    return max(0, score)


def analyze_episode(episode: Episode, session: Session) -> AnalysisResult | None:
    """
    Run full analysis on one episode. Returns the AnalysisResult record,
    or None if the audio file is not available.
    Skips re-analysis if an up-to-date result already exists.
    """
    existing = session.exec(
        select(AnalysisResult).where(AnalysisResult.episode_id == episode.id)
    ).first()
    if existing and existing.analysis_version == ANALYSIS_VERSION:
        logger.debug("Episode %d already analyzed (v%s), skipping", episode.id, ANALYSIS_VERSION)
        return existing

    audio_path = _episode_audio_path(episode)
    if not audio_path:
        logger.warning("No audio file for episode %d (%s %s)",
                       episode.id, episode.show_key,
                       episode.air_datetime.strftime("%Y-%m-%d"))
        return None

    logger.info("Analyzing episode %d: %s", episode.id, audio_path.name)

    # EAS detection
    eas_freq1 = int(get("processing.eas_freq_1_hz", 1050))
    eas_freq2 = int(get("processing.eas_freq_2_hz", 853))
    eas_hits = detect_eas(audio_path, freq1=eas_freq1, freq2=eas_freq2)
    eas_detected = len(eas_hits) > 0

    quality_flags = []
    if eas_detected:
        quality_flags.append("eas_detected")
    if episode.suspect_quality:
        quality_flags.append("suspect_quality")

    # Segment fingerprinting + underwriting matching
    uw_matches: list[dict] = []
    segments = extract_segment_fingerprints(audio_path)
    if segments:
        from shared.models import SegmentFingerprint
        existing_hashes = set(session.exec(
            select(SegmentFingerprint.fingerprint_hash)
        ).all())
        register_new_segments(segments, session, episode.show_key, existing_hashes)

        raw_matches = match_against_library(segments, session, episode.show_key)
        uw_raw = [m for m in raw_matches if m.get("classification") == "underwriting"]
        uw_matches = merge_overlapping(uw_raw)
        if uw_matches:
            quality_flags.append("underwriting_detected")

    evergreen_score = _compute_evergreen_score(
        eas_detected=eas_detected,
        underwriting_segments=uw_matches,
        suspect_quality=episode.suspect_quality,
    )

    result = existing or AnalysisResult(episode_id=episode.id)
    result.eas_detected = eas_detected
    result.quality_flags = json.dumps(quality_flags) if quality_flags else None
    result.evergreen_score = evergreen_score
    result.underwriting_match_timestamps = json.dumps(uw_matches) if uw_matches else None
    result.analysis_version = ANALYSIS_VERSION
    result.analyzed_at = datetime.utcnow()

    session.add(result)

    episode.status = "processed"
    session.add(episode)

    if eas_detected:
        session.add(SystemEvent(
            severity="warning",
            message=(f"EAS detected — {episode.show_key} {episode.air_datetime:%Y-%m-%d} "
                     f"({len(eas_hits)} hit(s))"),
        ))

    session.commit()
    logger.info("Episode %d analyzed: EAS=%s, underwriting=%d segments, score=%d",
                episode.id, eas_detected, len(uw_matches), evergreen_score)
    return result
