"""
Bulk Ingestion & Classification — Phase 4.

Provides UI for:
  - Kicking off a NAS crawl (with optional show filter)
  - Reviewing auto-matched and unmatched files
  - Confirming or overriding canonical episode decisions
  - Triggering background fingerprinting
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select

from ingest.classifier import classify_origin
from ingest.crawler import crawl_directory
from shared.database import get_session
from shared.models import CanonicalEpisode, IngestFile, Show, SystemEvent



logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest")
_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_templates_dir),
    autoescape=jinja2.select_autoescape(),
    auto_reload=True,
    cache_size=0,
))


def _get_shows(session: Session) -> dict[str, str]:
    """Returns {show_key: display_name} for all non-gone shows."""
    shows = session.exec(select(Show).where(Show.is_gone == False)).all()
    return {s.show_key: s.display_name for s in shows}


@router.get("/", response_class=HTMLResponse)
def ingest_page(request: Request, show: str = "", session: Session = Depends(get_session)):
    shows = _get_shows(session)

    # Stats
    total = session.exec(select(func.count(IngestFile.id))).one()
    by_status = {}
    for status in ("pending", "matched", "needs_review", "canonical", "duplicate", "ignored"):
        count = session.exec(
            select(func.count(IngestFile.id)).where(IngestFile.status == status)
        ).one()
        by_status[status] = count

    # Review queue: needs_review + pending files ≥ 30 min, filtered by show if selected
    review_q = select(IngestFile).where(
        IngestFile.status.in_(["needs_review", "pending"])
    )
    if show:
        review_q = review_q.where(IngestFile.show_key == show)
    review_files = session.exec(review_q.order_by(IngestFile.duration_sec.desc()).limit(100)).all()

    # Matched files for selected show
    matched_files = []
    if show:
        matched_files = session.exec(
            select(IngestFile)
            .where(IngestFile.show_key == show, IngestFile.status.in_(["matched", "canonical"]))
            .order_by(IngestFile.air_datetime)
            .limit(200)
        ).all()

    # Canonical decisions for selected show
    canonicals = []
    if show:
        canonicals = session.exec(
            select(CanonicalEpisode)
            .where(CanonicalEpisode.show_key == show)
            .order_by(CanonicalEpisode.true_air_date)
        ).all()

    # Content files for canonical display
    canonical_files = {}
    for c in canonicals:
        f = session.get(IngestFile, c.content_file_id)
        if f:
            canonical_files[c.id] = f

    return templates.TemplateResponse(request, "ingest.html", {
        "shows": shows,
        "selected_show": show,
        "stats": by_status,
        "total": total,
        "review_files": review_files,
        "matched_files": matched_files,
        "canonicals": canonicals,
        "canonical_files": canonical_files,
    })


@router.post("/crawl")
async def start_crawl(request: Request, session: Session = Depends(get_session)):
    from shared.config import get as cfg_get
    form = await request.form()
    crawl_path = (form.get("crawl_path") or "").strip()
    filter_show = (form.get("filter_show") or "").strip()
    copy_to_staging = form.get("copy_to_staging") == "on"

    if not crawl_path:
        return RedirectResponse("/ingest", status_code=303)

    root = Path(crawl_path)
    if not root.exists() or not root.is_dir():
        session.add(SystemEvent(severity="error", message=f"Crawl path not found: {crawl_path}"))
        session.commit()
        return RedirectResponse("/ingest", status_code=303)

    copy_to: Path | None = None
    if copy_to_staging:
        staging_base = cfg_get("local_staging.path", "")
        if not staging_base:
            session.add(SystemEvent(
                severity="error",
                message="Copy to staging requested but local_staging.path is not configured in Settings."
            ))
            session.commit()
            return RedirectResponse("/ingest", status_code=303)
        copy_to = Path(staging_base) / "ingest_import"
        copy_to.mkdir(parents=True, exist_ok=True)

    shows = _get_shows(session)
    show_keys = [filter_show] if filter_show else list(shows.keys())

    try:
        counts = crawl_directory(
            root, session,
            show_keys=show_keys,
            show_display_names=shows,
            copy_to=copy_to,
        )
        copy_note = f", {counts.get('copied', 0)} copied to staging" if copy_to else ""
        msg = (f"Crawl complete: {root} — "
               f"{counts['created']} new, {counts['skipped']} skipped, "
               f"{counts['errors']} errors{copy_note}")
        session.add(SystemEvent(severity="info", message=msg))
        session.commit()
        logger.info(msg)
    except Exception as e:
        logger.exception("Crawl failed: %s", e)
        session.add(SystemEvent(severity="error", message=f"Crawl failed: {e}"))
        session.commit()

    redirect = f"/ingest?show={filter_show}" if filter_show else "/ingest"
    return RedirectResponse(redirect, status_code=303)


@router.post("/files/{file_id}/update")
async def update_file(file_id: int, request: Request, session: Session = Depends(get_session)):
    """Human review: update show_key, air_datetime, status for an IngestFile."""
    form = await request.form()
    f = session.get(IngestFile, file_id)
    if not f:
        return RedirectResponse("/ingest", status_code=303)

    show_key = (form.get("show_key") or "").strip() or None
    air_date_str = (form.get("air_date") or "").strip()
    air_time_str = (form.get("air_time") or "00:00").strip()
    new_status = (form.get("status") or "").strip()
    reviewer = (form.get("reviewer") or "").strip() or "operator"
    notes = (form.get("notes") or "").strip() or None

    if show_key:
        f.show_key = show_key
        f.show_key_confidence = "human"
    if air_date_str:
        try:
            dt = datetime.strptime(f"{air_date_str} {air_time_str}", "%Y-%m-%d %H:%M")
            f.air_datetime = dt
            f.air_date_confidence = "human"
        except ValueError:
            pass
    if new_status and new_status in ("matched", "needs_review", "ignored", "canonical", "duplicate"):
        f.status = new_status
    if notes:
        f.notes = notes

    f.reviewed_by = reviewer
    f.reviewed_at = datetime.utcnow()

    session.add(f)
    session.commit()

    show_filter = f"?show={f.show_key}" if f.show_key else ""
    return RedirectResponse(f"/ingest{show_filter}", status_code=303)


@router.post("/files/{file_id}/ignore")
def ignore_file(file_id: int, session: Session = Depends(get_session)):
    f = session.get(IngestFile, file_id)
    if f:
        f.status = "ignored"
        session.add(f)
        session.commit()
    return RedirectResponse("/ingest", status_code=303)


@router.post("/canonical/set")
async def set_canonical(request: Request, session: Session = Depends(get_session)):
    """Mark an IngestFile as the canonical version for its show + air date."""
    form = await request.form()
    file_id = int(form.get("file_id", 0))
    is_reair = form.get("is_reair") == "true"
    reviewer = (form.get("reviewer") or "").strip() or "operator"
    notes = (form.get("notes") or "").strip() or None

    f = session.get(IngestFile, file_id)
    if not f or not f.show_key or not f.air_datetime:
        return RedirectResponse("/ingest", status_code=303)

    # Check for existing canonical on same show+date
    existing = session.exec(
        select(CanonicalEpisode).where(
            CanonicalEpisode.show_key == f.show_key,
            CanonicalEpisode.true_air_date == f.air_datetime,
        )
    ).first()

    if existing:
        # Override
        old_file_id = existing.content_file_id
        existing.content_file_id = file_id
        existing.decision = "human_override"
        existing.decided_by = reviewer
        existing.decided_at = datetime.utcnow()
        existing.notes = notes
        existing.is_reair = is_reair
        session.add(existing)
        logger.info("Canonical overridden for %s %s: file %d → %d",
                    f.show_key, f.air_datetime.date(), old_file_id, file_id)
    else:
        canonical = CanonicalEpisode(
            show_key=f.show_key,
            true_air_date=f.air_datetime,
            content_file_id=file_id,
            decision="human_confirmed",
            is_reair=is_reair,
            notes=notes,
            decided_by=reviewer,
            decided_at=datetime.utcnow(),
        )
        session.add(canonical)

    f.status = "canonical"
    session.add(f)
    session.commit()

    return RedirectResponse(f"/ingest?show={f.show_key}", status_code=303)


@router.post("/auto-canonical")
async def auto_canonical(request: Request, session: Session = Depends(get_session)):
    """
    Auto-select canonical episodes for a show from matched files.
    For each unique (show_key, air_date): pick best file using heuristics.
    Only creates CanonicalEpisode where decision hasn't already been made.
    """
    form = await request.form()
    show_key = (form.get("show_key") or "").strip()
    if not show_key:
        return RedirectResponse("/ingest", status_code=303)

    matched = session.exec(
        select(IngestFile).where(
            IngestFile.show_key == show_key,
            IngestFile.status.in_(["matched", "canonical"]),
            IngestFile.air_datetime != None,
        )
    ).all()

    # Group by date (date only, ignore time)
    from collections import defaultdict
    date_groups: dict[str, list] = defaultdict(list)
    for f in matched:
        date_key = f.air_datetime.strftime("%Y-%m-%d")
        date_groups[date_key].append(f)

    created = 0
    for date_key, group in date_groups.items():
        # Check if canonical already exists
        sample_dt = group[0].air_datetime
        existing = session.exec(
            select(CanonicalEpisode).where(
                CanonicalEpisode.show_key == show_key,
                func.date(CanonicalEpisode.true_air_date) == date_key,
            )
        ).first()
        if existing:
            continue

        # Pick best: source_file > archive > longest duration
        def score(f):
            origin_score = {"source_file": 2, "archive": 1, "unknown": 0}.get(f.file_origin, 0)
            return (origin_score, f.duration_sec or 0)

        best = max(group, key=score)
        canonical = CanonicalEpisode(
            show_key=show_key,
            true_air_date=best.air_datetime,
            content_file_id=best.id,
            decision="auto",
        )
        session.add(canonical)
        best.status = "canonical"
        session.add(best)
        created += 1

    session.commit()
    logger.info("Auto-canonical: %d decisions created for %s", created, show_key)
    return RedirectResponse(f"/ingest?show={show_key}", status_code=303)


@router.post("/fingerprint-show")
async def fingerprint_show(request: Request, session: Session = Depends(get_session)):
    """
    Queue fingerprinting for all IngestFiles for a show that don't have one yet.
    Runs synchronously for small shows; warn if large.

    NOTE: For the full backlog this is a background job. Each 2hr file takes
    ~5-10s with fpcalc (sampling mode). Estimated time shown in UI.
    """
    form = await request.form()
    show_key = (form.get("show_key") or "").strip()
    if not show_key:
        return RedirectResponse("/ingest", status_code=303)

    files = session.exec(
        select(IngestFile).where(
            IngestFile.show_key == show_key,
            IngestFile.fingerprint == None,
            IngestFile.status != "ignored",
        )
    ).all()

    if not files:
        return RedirectResponse(f"/ingest?show={show_key}", status_code=303)

    from ingest.fingerprinter import fingerprint_file
    done = 0
    for f in files:
        fp, dur = fingerprint_file(Path(f.file_path))
        if fp:
            f.fingerprint = fp
            f.fingerprint_duration = dur
            session.add(f)
            done += 1

    session.commit()

    # After fingerprinting, check for hash-based duplicates
    all_files = session.exec(
        select(IngestFile).where(IngestFile.show_key == show_key, IngestFile.status != "ignored")
    ).all()
    from ingest.fingerprinter import find_duplicates_by_hash
    dup_groups = find_duplicates_by_hash(all_files)
    dup_count = 0
    for canonical, dupes in dup_groups:
        for d in dupes:
            if d.status not in ("canonical", "ignored"):
                d.status = "duplicate"
                d.duplicate_of_id = canonical.id
                session.add(d)
                dup_count += 1
    session.commit()

    msg = f"Fingerprinted {done}/{len(files)} files for {show_key}; {dup_count} duplicates marked"
    session.add(SystemEvent(severity="info", message=msg))
    session.commit()
    logger.info(msg)

    return RedirectResponse(f"/ingest?show={show_key}", status_code=303)


@router.post("/detect-reairs")
async def detect_reairs_route(request: Request, session: Session = Depends(get_session)):
    from ingest.reair_detector import detect_reairs
    form = await request.form()
    show_key = (form.get("show_key") or "").strip()
    if not show_key:
        return RedirectResponse("/ingest", status_code=303)
    counts = detect_reairs(session, show_key)
    msg = (f"Re-air detection for {show_key}: "
           f"{counts['marked_reair']} marked, {counts['gray_zone']} gray zone, "
           f"{counts['compared']} compared")
    session.add(SystemEvent(severity="info", message=msg))
    session.commit()
    return RedirectResponse(f"/ingest?show={show_key}", status_code=303)
