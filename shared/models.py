"""
SQLModel data models — WDBX Radio Toolbox
Schema based on Dev Plan v2.0. All JSON fields stored as strings (json.dumps/loads at app layer).
"""
from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class Show(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    show_key: str = Field(unique=True, index=True)
    display_name: str
    archive_enabled: bool = True
    evergreen_default: bool = True
    expected_duration_min: int
    expected_duration_override: Optional[int] = None   # minutes; overrides scheduled slot
    retention_days: Optional[int] = None
    confirmed_by_manager: bool = False
    notes: Optional[str] = None


class Episode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    show_key: str = Field(index=True)
    air_datetime: datetime
    scheduled_duration_min: int
    source_urls: Optional[str] = None        # JSON array of URLs (1 = normal, >1 = fragments)
    status: str = "pending"                  # pending | downloading | downloaded | failed | processing | processed
    local_path: Optional[str] = None
    nas_path: Optional[str] = None
    actual_duration_sec: Optional[int] = None
    fragment_count: int = 1
    is_fragmented: bool = False
    fragmented_source: bool = False          # True if assembled from restart fragments
    suspect_quality: bool = False            # True if duration significantly < scheduled slot
    fingerprint: Optional[str] = None


class AnalysisResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(index=True)
    eas_detected: bool = False
    quality_flags: Optional[str] = None                     # JSON list of flag strings
    talk_ratio_pct: Optional[float] = None
    evergreen_score: Optional[int] = None                   # 0–100
    underwriting_match_timestamps: Optional[str] = None     # JSON list of {start, end, fingerprint_hash}
    whisper_transcript_path: Optional[str] = None
    analysis_version: Optional[str] = None
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)


class SegmentFingerprint(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    fingerprint_hash: str = Field(unique=True, index=True)
    duration_sec: float
    first_seen_show_key: str
    occurrence_count: int = 1
    cross_show_count: int = 0
    # underwriting | theme | station_id | promo | safelist | unknown | pending_review
    classification: Optional[str] = None
    confirmed_by: Optional[str] = None   # None = provisional; set to reviewer name when confirmed


class ShowSegmentOverride(SQLModel, table=True):
    """Per-show action for a fingerprinted segment. Overrides global classification."""
    id: Optional[int] = Field(default=None, primary_key=True)
    show_key: str = Field(index=True)
    fingerprint_hash: str
    action: str          # keep | remove
    confirmed_by: str


class ScreeningDecision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(index=True)
    decision: str        # approved | approved_with_notes | needs_review | rejected
    reviewer_name: str
    reviewer_notes: Optional[str] = None
    decided_at: datetime = Field(default_factory=datetime.utcnow)


class ProcessedOutput(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(index=True)
    output_path: str
    target_runtime_sec: int
    actual_runtime_sec: int
    station_ids_used: Optional[str] = None     # JSON list of asset IDs
    padding_tracks_used: Optional[str] = None  # JSON list of asset IDs
    produced_at: datetime = Field(default_factory=datetime.utcnow)


class LibraryAsset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # underwriting | station_id | padding | safelist
    asset_type: str
    file_path: str
    fingerprint_hash: Optional[str] = None
    tags: Optional[str] = None               # JSON: {genre, mood, show_specific, etc.}
    show_key_association: Optional[str] = None
    usage_count: int = 0


class SystemEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    severity: str        # info | warning | error | critical
    message: str
    email_sent: bool = False
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
