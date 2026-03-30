"""
Archive Manager web routes.

All mutating endpoints use POST → 303 redirect (PRG pattern)
so browser refresh doesn't re-submit forms.
"""
import logging
from datetime import datetime
from pathlib import Path

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from archive_manager.downloader import copy_episode_to_nas
from archive_manager.nas import nas_is_writable
from archive_manager.scraper import sync_episodes
from archive_manager.seeder import seed_from_file
from shared.database import get_session
from shared.models import Episode, Show, SystemEvent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/archive")
_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_templates_dir),
    autoescape=jinja2.select_autoescape(),
    auto_reload=True,
    cache_size=0,
))

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def archive_dashboard(request: Request, session: Session = Depends(get_session)):
    nas_ok = nas_is_writable()

    shows_raw = session.exec(select(Show)).all()

    # Group shows by day for the schedule grid
    by_day: dict[str, list[Show]] = {d: [] for d in DAY_ORDER}
    ungrouped: list[Show] = []
    for show in shows_raw:
        if show.schedule_day and show.schedule_day in by_day:
            by_day[show.schedule_day].append(show)
        else:
            ungrouped.append(show)

    # Sort each day by air time
    for day in by_day:
        by_day[day].sort(key=lambda s: s.schedule_time or "")

    recent = session.exec(
        select(Episode).order_by(Episode.air_datetime.desc()).limit(30)
    ).all()

    # Build show_key → display_name map for episode list
    show_names = {s.show_key: s.display_name for s in shows_raw}

    events = session.exec(
        select(SystemEvent)
        .where(SystemEvent.resolved_at == None)
        .order_by(SystemEvent.created_at.desc())
        .limit(10)
    ).all()

    # Stats
    stats = {
        "total_shows": len(shows_raw),
        "enabled": sum(1 for s in shows_raw if s.archive_enabled),
        "pending": sum(1 for e in recent if e.status == "pending"),
        "downloaded": sum(1 for e in recent if e.status == "downloaded"),
        "failed": sum(1 for e in recent if e.status == "failed"),
    }

    return templates.TemplateResponse(request, "archive.html", {
        "nas_ok": nas_ok,
        "by_day": by_day,
        "day_order": DAY_ORDER,
        "ungrouped": ungrouped,
        "recent_episodes": recent,
        "show_names": show_names,
        "events": events,
        "stats": stats,
    })


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@router.post("/scrape")
def trigger_scrape(session: Session = Depends(get_session)):
    """Manually trigger an archive listing scrape and episode sync."""
    try:
        counts = sync_episodes(session)
        logger.info("Manual scrape triggered: %s", counts)
    except Exception as e:
        logger.error("Manual scrape failed: %s", e)
    return RedirectResponse("/archive", status_code=303)


@router.post("/seed")
def trigger_seed(session: Session = Depends(get_session)):
    """Import shows from reference/showst.txt into the Show table."""
    try:
        counts = seed_from_file(session)
        logger.info("Manual seed triggered: %s", counts)
    except Exception as e:
        logger.error("Manual seed failed: %s", e)
    return RedirectResponse("/archive", status_code=303)


@router.post("/shows/{show_key}/toggle")
def toggle_show(show_key: str, session: Session = Depends(get_session)):
    """Toggle archive_enabled for a show."""
    show = session.exec(select(Show).where(Show.show_key == show_key)).first()
    if show and not show.confirmed_by_manager:
        show.archive_enabled = not show.archive_enabled
        session.add(show)
        session.commit()
    return RedirectResponse("/archive", status_code=303)


@router.post("/episodes/{episode_id}/copy-to-nas")
def copy_to_nas(episode_id: int, session: Session = Depends(get_session)):
    """Copy a locally-staged episode to NAS (used after NAS outage recovery)."""
    episode = session.get(Episode, episode_id)
    if episode:
        copy_episode_to_nas(episode, session)
    return RedirectResponse("/archive", status_code=303)


@router.post("/episodes/{episode_id}/retry")
def retry_episode(episode_id: int, session: Session = Depends(get_session)):
    """Reset a failed episode to pending so the next download job picks it up."""
    episode = session.get(Episode, episode_id)
    if episode and episode.status == "failed":
        episode.status = "pending"
        session.add(episode)
        session.commit()
    return RedirectResponse("/archive", status_code=303)


@router.post("/events/{event_id}/resolve")
def resolve_event(event_id: int, session: Session = Depends(get_session)):
    """Dismiss a system event."""
    event = session.get(SystemEvent, event_id)
    if event:
        event.resolved_at = datetime.utcnow()
        session.add(event)
        session.commit()
    return RedirectResponse("/archive", status_code=303)
