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
    is_gone: bool = False                 # True if show is no longer airing; hides from UI by default
    schedule_day: Optional[str] = None    # e.g. "Monday" — from showst.txt / onboarding wizard
    schedule_time: Optional[str] = None   # HHMMSS — scheduled air time
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
    expires_at: Optional[datetime] = None   # Archive expiry from API — None if not provided


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


class LibrarySource(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str                    # Human name, e.g. "Morning Station IDs"
    nas_path: str                 # Absolute path on NAS to the folder
    source_type: str              # station_id | promo | padding | announcement
    notes: Optional[str] = None


class ShowLibraryConfig(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    show_key: Optional[str] = Field(default=None, index=True)  # None = global default
    station_id_sources: Optional[str] = None   # JSON list of LibrarySource IDs
    promo_sources: Optional[str] = None        # JSON list of LibrarySource IDs
    padding_sources: Optional[str] = None      # JSON list of LibrarySource IDs
    announcement_sources: Optional[str] = None # JSON list of LibrarySource IDs


class ScheduleNote(SQLModel, table=True):
    """Operator annotation on an expected schedule slot.
    Used to mark confirmed no-shows (DJ out, holiday) so gaps don't look like missing data.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    show_key: str = Field(index=True)
    expected_date: datetime                  # the Thursday / Monday / etc. this note applies to
    note_type: str                           # confirmed_gap | uncertain
    notes: Optional[str] = None
    noted_by: str = "operator"
    noted_at: datetime = Field(default_factory=datetime.utcnow)


class IngestFile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    file_path: str = Field(unique=True, index=True)   # absolute path on NAS
    file_size_bytes: int = 0
    duration_sec: Optional[float] = None
    show_key: Optional[str] = Field(default=None, index=True)   # matched show
    show_key_confidence: str = "none"      # filename_exact | filename_fuzzy | human | none
    air_datetime: Optional[datetime] = None
    air_date_confidence: str = "none"      # filename | archive_record | human | none
    file_origin: str = "unknown"           # archive | source_file | unknown
    origin_confidence: str = "none"        # auto | human
    encoder_tag: Optional[str] = None      # TENC/TSSE from ID3
    bitrate_kbps: Optional[int] = None
    file_hash: Optional[str] = None        # MD5 of first+last 64KB (fast near-dedup)
    fingerprint: Optional[str] = None      # chromaprint whole-file (slow, nullable)
    fingerprint_duration: Optional[float] = None
    status: str = "pending"                # pending | matched | needs_review | canonical | duplicate | ignored
    duplicate_of_id: Optional[int] = None  # IngestFile.id of canonical copy
    crawl_root: Optional[str] = None       # which NAS path this crawl started from
    source_path: Optional[str] = None      # original path if file was copied from removable media
    crawled_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    notes: Optional[str] = None


class CanonicalEpisode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    show_key: str = Field(index=True)
    true_air_date: datetime              # earliest known broadcast date
    content_file_id: int                 # IngestFile.id of best file to use
    decision: str = "auto"               # auto | human_confirmed | human_override
    is_reair: bool = False               # True if this date is a re-broadcast
    original_canonical_id: Optional[int] = None  # → CanonicalEpisode.id of first broadcast
    notes: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
