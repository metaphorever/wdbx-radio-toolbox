"""
Processor routes — Phase 5.

/processor/                          — dashboard: queue, recent results, segment stats
/processor/analyze                   — POST: trigger analysis for a show
/processor/segments                  — browse + classify SegmentFingerprint table
/processor/segments/{hash}/classify  — POST: set classification on a segment
"""
import json
import logging
from pathlib import Path

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select

from processor.analyzer import analyze_episode, ANALYSIS_VERSION
from shared.database import get_session
from shared.models import AnalysisResult, Episode, SegmentFingerprint, Show, SystemEvent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/processor")
_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_templates_dir),
    autoescape=jinja2.select_autoescape(),
    auto_reload=True,
    cache_size=0,
))

CLASSIFICATIONS = [
    "underwriting", "theme", "station_id", "promo",
    "safelist", "unknown", "pending_review",
]


@router.get("/", response_class=HTMLResponse)
def processor_page(
    request: Request,
    show: str = "",
    session: Session = Depends(get_session),
):
    shows = session.exec(
        select(Show).where(Show.is_gone == False).order_by(Show.display_name)
    ).all()

    # IDs of already-analyzed episodes at current version
    analyzed_ids = set(session.exec(
        select(AnalysisResult.episode_id).where(
            AnalysisResult.analysis_version == ANALYSIS_VERSION
        )
    ).all())

    # Pending count
    pending_q = select(func.count(Episode.id)).where(Episode.status == "downloaded")
    if analyzed_ids:
        pending_q = pending_q.where(Episode.id.not_in(list(analyzed_ids)))
    pending_count = session.exec(pending_q).one()

    # Recent results (optionally filtered by show)
    results_q = (
        select(AnalysisResult, Episode)
        .join(Episode, AnalysisResult.episode_id == Episode.id)
        .order_by(AnalysisResult.analyzed_at.desc())
        .limit(50)
    )
    if show:
        results_q = results_q.where(Episode.show_key == show)
    recent_rows = session.exec(results_q).all()
    recent_results = [{"result": r, "episode": ep} for r, ep in recent_rows]

    # Segment library stats
    seg_total = session.exec(select(func.count(SegmentFingerprint.id))).one()
    seg_by_class = {}
    for cls in CLASSIFICATIONS:
        seg_by_class[cls] = session.exec(
            select(func.count(SegmentFingerprint.id)).where(
                SegmentFingerprint.classification == cls
            )
        ).one()

    return templates.TemplateResponse(request, "processor.html", {
        "shows": shows,
        "selected_show": show,
        "pending_count": pending_count,
        "recent_results": recent_results,
        "seg_total": seg_total,
        "seg_by_class": seg_by_class,
        "analysis_version": ANALYSIS_VERSION,
    })


@router.post("/analyze")
async def trigger_analysis(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    show_key = (form.get("show_key") or "").strip()
    limit = min(int(form.get("limit") or 5), 50)

    if not show_key:
        return RedirectResponse("/processor", status_code=303)

    analyzed_ids = set(session.exec(
        select(AnalysisResult.episode_id).where(
            AnalysisResult.analysis_version == ANALYSIS_VERSION
        )
    ).all())

    ep_q = select(Episode).where(
        Episode.show_key == show_key,
        Episode.status == "downloaded",
    )
    if analyzed_ids:
        ep_q = ep_q.where(Episode.id.not_in(list(analyzed_ids)))
    episodes = session.exec(ep_q.limit(limit)).all()

    done = 0
    for ep in episodes:
        if analyze_episode(ep, session):
            done += 1

    msg = f"Analysis complete: {done}/{len(episodes)} episodes processed for {show_key}"
    session.add(SystemEvent(severity="info", message=msg))
    session.commit()
    logger.info(msg)
    return RedirectResponse(f"/processor?show={show_key}", status_code=303)


@router.get("/segments", response_class=HTMLResponse)
def segments_page(
    request: Request,
    cls: str = "pending_review",
    offset: int = 0,
    session: Session = Depends(get_session),
):
    PAGE = 50
    segments = session.exec(
        select(SegmentFingerprint)
        .where(SegmentFingerprint.classification == cls)
        .order_by(SegmentFingerprint.occurrence_count.desc())
        .offset(offset)
        .limit(PAGE)
    ).all()
    total = session.exec(
        select(func.count(SegmentFingerprint.id)).where(
            SegmentFingerprint.classification == cls
        )
    ).one()
    prev_offset = max(offset - PAGE, 0)
    next_offset = offset + PAGE

    return templates.TemplateResponse(request, "processor_segments.html", {
        "segments": segments,
        "classifications": CLASSIFICATIONS,
        "selected_cls": cls,
        "total": total,
        "offset": offset,
        "page_size": PAGE,
        "prev_offset": prev_offset,
        "next_offset": next_offset,
    })


@router.post("/segments/{fingerprint_hash}/classify")
async def classify_segment(
    fingerprint_hash: str,
    request: Request,
    session: Session = Depends(get_session),
):
    form = await request.form()
    classification = (form.get("classification") or "").strip()
    confirmed_by = (form.get("confirmed_by") or "operator").strip()
    redirect_cls = (form.get("redirect_cls") or "pending_review").strip()

    if classification not in CLASSIFICATIONS:
        return RedirectResponse(f"/processor/segments?cls={redirect_cls}", status_code=303)

    seg = session.exec(
        select(SegmentFingerprint).where(
            SegmentFingerprint.fingerprint_hash == fingerprint_hash
        )
    ).first()
    if seg:
        seg.classification = classification
        seg.confirmed_by = confirmed_by
        session.add(seg)
        session.commit()
        logger.info("Segment %s… classified as %s by %s",
                    fingerprint_hash[:16], classification, confirmed_by)

    return RedirectResponse(f"/processor/segments?cls={redirect_cls}", status_code=303)
