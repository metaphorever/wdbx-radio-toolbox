"""
Station manager onboarding wizard.

Single-page review of all shows. Manager sets archive_enabled, duration,
and notes per show, then marks each confirmed. New shows not in the
original schedule default to archive_enabled=True until explicitly disabled.
"""
import logging
from pathlib import Path
import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from shared.database import get_session
from shared.models import Show

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/onboarding")

_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_templates_dir),
    autoescape=jinja2.select_autoescape(),
    auto_reload=True,
    cache_size=0,
))

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@router.get("/", response_class=HTMLResponse)
def wizard(request: Request, session: Session = Depends(get_session)):
    shows_raw = session.exec(select(Show)).all()

    by_day: dict[str, list[Show]] = {d: [] for d in DAY_ORDER}
    unscheduled: list[Show] = []
    for show in shows_raw:
        if show.schedule_day and show.schedule_day in by_day:
            by_day[show.schedule_day].append(show)
        else:
            unscheduled.append(show)

    for day in by_day:
        by_day[day].sort(key=lambda s: s.schedule_time or "")

    total = len(shows_raw)
    confirmed = sum(1 for s in shows_raw if s.confirmed_by_manager)

    return templates.TemplateResponse(request, "onboarding.html", {
        "by_day": by_day,
        "day_order": DAY_ORDER,
        "unscheduled": unscheduled,
        "total": total,
        "confirmed": confirmed,
    })


@router.post("/save")
async def save(request: Request, session: Session = Depends(get_session)):
    """
    Process the full onboarding form. For each show in the DB, read
    the submitted fields and update. Unchecked checkboxes are absent
    from form data, so archive_enabled and confirmed default to False
    unless the checkbox field is present.
    """
    form = await request.form()
    shows = session.exec(select(Show)).all()
    updated = 0

    for show in shows:
        if show.is_gone:
            continue  # never overwrite gone show settings from the wizard
        key = show.show_key
        show.archive_enabled = form.get(f"enabled_{key}") == "on"
        show.confirmed_by_manager = form.get(f"confirmed_{key}") == "on"
        notes = (form.get(f"notes_{key}") or "").strip()
        show.notes = notes or None

        duration_raw = form.get(f"duration_{key}")
        if duration_raw:
            try:
                show.expected_duration_min = max(1, int(duration_raw))
            except ValueError:
                pass

        session.add(show)
        updated += 1

    session.commit()
    logger.info("Onboarding save: %d shows updated", updated)
    return RedirectResponse("/onboarding", status_code=303)


@router.post("/confirm-all")
def confirm_all(session: Session = Depends(get_session)):
    """Mark every show confirmed_by_manager=True without changing other settings."""
    shows = session.exec(select(Show)).all()
    for show in shows:
        show.confirmed_by_manager = True
        session.add(show)
    session.commit()
    logger.info("Confirm all: %d shows marked confirmed", len(shows))
    return RedirectResponse("/onboarding", status_code=303)
