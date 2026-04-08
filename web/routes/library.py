"""
Library Module — NAS asset source registry and per-show processing configuration.

Asset types:
  station_id   — station ID recordings, played at the top of every hour
  promo        — evergreen promos/spots, placed anywhere
  padding      — music filler to hit target runtime
  announcement — archival announcements ("This is a previously aired episode of...")

Sources are NAS folders. Configuration records which folders are active globally
and per-show. Shows with no config fall back to global defaults.
"""
import json
import logging
from pathlib import Path

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from shared.database import get_session
from shared.models import LibrarySource, Show, ShowLibraryConfig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/library")
_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_templates_dir),
    autoescape=jinja2.select_autoescape(),
    auto_reload=True,
    cache_size=0,
))

SOURCE_TYPES = ["station_id", "promo", "padding", "announcement"]
SOURCE_TYPE_LABELS = {
    "station_id": "Station ID",
    "promo": "Promo / Evergreen Spot",
    "padding": "Padding",
    "announcement": "Announcement",
}


def _get_config(session: Session, show_key: str | None) -> ShowLibraryConfig | None:
    if show_key:
        return session.exec(
            select(ShowLibraryConfig).where(ShowLibraryConfig.show_key == show_key)
        ).first()
    return session.exec(
        select(ShowLibraryConfig).where(ShowLibraryConfig.show_key == None)
    ).first()


def _checked_ids(config: ShowLibraryConfig | None) -> dict[str, set[int]]:
    result = {}
    for t in SOURCE_TYPES:
        raw = getattr(config, f"{t}_sources", None) if config else None
        result[t] = set(json.loads(raw)) if raw else set()
    return result


@router.get("/", response_class=HTMLResponse)
def library_page(request: Request, show: str = "", session: Session = Depends(get_session)):
    sources = session.exec(
        select(LibrarySource).order_by(LibrarySource.source_type, LibrarySource.label)
    ).all()
    sources_by_type = {t: [s for s in sources if s.source_type == t] for t in SOURCE_TYPES}

    global_config = _get_config(session, None)

    selected_show = None
    selected_config = None
    if show:
        selected_show = session.exec(select(Show).where(Show.show_key == show)).first()
        if selected_show:
            selected_config = _get_config(session, show)

    shows = session.exec(
        select(Show)
        .where(Show.is_gone == False)
        .order_by(Show.schedule_day, Show.schedule_time)
    ).all()

    return templates.TemplateResponse(request, "library.html", {
        "sources": sources,
        "sources_by_type": sources_by_type,
        "source_types": SOURCE_TYPES,
        "source_type_labels": SOURCE_TYPE_LABELS,
        "global_config": global_config,
        "global_checked": _checked_ids(global_config),
        "selected_show": selected_show,
        "selected_show_key": show,
        "selected_config": selected_config,
        "selected_checked": _checked_ids(selected_config),
        "shows": shows,
    })


@router.post("/sources/add")
async def add_source(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    label      = (form.get("label") or "").strip()
    nas_path   = (form.get("nas_path") or "").strip()
    source_type = (form.get("source_type") or "").strip()
    notes      = (form.get("notes") or "").strip() or None

    if label and nas_path and source_type in SOURCE_TYPES:
        session.add(LibrarySource(
            label=label, nas_path=nas_path, source_type=source_type, notes=notes,
        ))
        session.commit()
        logger.info("Library source added: %s (%s) → %s", label, source_type, nas_path)

    return RedirectResponse("/library", status_code=303)


@router.post("/sources/{source_id}/delete")
def delete_source(source_id: int, session: Session = Depends(get_session)):
    source = session.get(LibrarySource, source_id)
    if source:
        session.delete(source)
        session.commit()
        logger.info("Library source deleted: %s (%d)", source.label, source_id)
    return RedirectResponse("/library", status_code=303)


@router.post("/config/save")
async def save_config(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    config_for = (form.get("config_for") or "").strip() or None  # None = global default

    config = _get_config(session, config_for)
    if not config:
        config = ShowLibraryConfig(show_key=config_for)

    for t in SOURCE_TYPES:
        ids = [int(v) for v in form.getlist(f"{t}_sources") if str(v).isdigit()]
        setattr(config, f"{t}_sources", json.dumps(ids) if ids else None)

    session.add(config)
    session.commit()
    logger.info("Library config saved for: %s", config_for or "global default")

    redirect = f"/library?show={config_for}" if config_for else "/library"
    return RedirectResponse(redirect, status_code=303)


@router.post("/config/{show_key}/clear")
def clear_show_config(show_key: str, session: Session = Depends(get_session)):
    config = _get_config(session, show_key)
    if config:
        session.delete(config)
        session.commit()
        logger.info("Cleared library config for: %s (will fall back to global defaults)", show_key)
    return RedirectResponse("/library", status_code=303)
