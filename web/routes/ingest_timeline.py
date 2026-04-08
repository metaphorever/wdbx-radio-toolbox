"""
Show timeline — Phase 4.

Generates every expected air date for a show from the earliest known file
to today, then cross-references against CanonicalEpisode, IngestFile, and
Episode (archive downloads) to show coverage gaps and re-air chains.

Operators can annotate slots as confirmed_gap (DJ out, holiday) so real
gaps don't look like missing data.
"""
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select

from shared.database import get_session
from shared.models import CanonicalEpisode, Episode, IngestFile, ScheduleNote, Show

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest/timeline")
_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_templates_dir),
    autoescape=jinja2.select_autoescape(),
    auto_reload=True,
    cache_size=0,
))

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _weekday_index(day_name: str) -> int:
    """Monday=0 … Sunday=6, matching date.weekday()."""
    try:
        return _WEEKDAYS.index(day_name)
    except ValueError:
        return -1


def _expected_dates(show: Show, start: date, end: date) -> list[date]:
    """Generate every expected air date for a weekly show between start and end."""
    if not show.schedule_day:
        return []
    target_wd = _weekday_index(show.schedule_day)
    if target_wd == -1:
        return []
    dates = []
    # Advance start to the first occurrence of the target weekday
    delta = (target_wd - start.weekday()) % 7
    current = start + timedelta(days=delta)
    while current <= end:
        dates.append(current)
        current += timedelta(weeks=1)
    return dates


def _date_key(dt: datetime | date) -> date:
    """Normalise datetime or date to a date for bucketing."""
    if isinstance(dt, datetime):
        return dt.date()
    return dt


@router.get("/{show_key}", response_class=HTMLResponse)
def timeline_page(
    show_key: str,
    request: Request,
    session: Session = Depends(get_session),
):
    show = session.exec(select(Show).where(Show.show_key == show_key)).first()
    if not show:
        return RedirectResponse("/ingest", status_code=303)

    today = date.today()

    # Find earliest known date across all sources for this show
    earliest_ingest = session.exec(
        select(func.min(IngestFile.air_datetime)).where(
            IngestFile.show_key == show_key,
            IngestFile.air_datetime != None,
        )
    ).one()
    earliest_canonical = session.exec(
        select(func.min(CanonicalEpisode.true_air_date)).where(
            CanonicalEpisode.show_key == show_key
        )
    ).one()
    earliest_episode = session.exec(
        select(func.min(Episode.air_datetime)).where(Episode.show_key == show_key)
    ).one()

    candidates = [d for d in [earliest_ingest, earliest_canonical, earliest_episode] if d]
    if candidates:
        earliest = min(_date_key(d) for d in candidates)
    else:
        # No data yet — show last 52 weeks
        earliest = today - timedelta(weeks=52)

    expected = _expected_dates(show, earliest, today)

    # Load all relevant records indexed by date
    ingest_files = session.exec(
        select(IngestFile).where(
            IngestFile.show_key == show_key,
            IngestFile.air_datetime != None,
            IngestFile.status != "ignored",
        )
    ).all()
    ingest_by_date: dict[date, list[IngestFile]] = {}
    for f in ingest_files:
        dk = _date_key(f.air_datetime)
        ingest_by_date.setdefault(dk, []).append(f)

    canonicals = session.exec(
        select(CanonicalEpisode).where(CanonicalEpisode.show_key == show_key)
    ).all()
    canonical_by_date: dict[date, CanonicalEpisode] = {
        _date_key(c.true_air_date): c for c in canonicals
    }

    archive_eps = session.exec(
        select(Episode).where(Episode.show_key == show_key)
    ).all()
    archive_by_date: dict[date, list[Episode]] = {}
    for ep in archive_eps:
        dk = _date_key(ep.air_datetime)
        archive_by_date.setdefault(dk, []).append(ep)

    notes = session.exec(
        select(ScheduleNote).where(ScheduleNote.show_key == show_key)
    ).all()
    notes_by_date: dict[date, ScheduleNote] = {
        _date_key(n.expected_date): n for n in notes
    }

    # Build one row per expected date, newest first
    rows = []
    for d in reversed(expected):
        canonical = canonical_by_date.get(d)
        files = ingest_by_date.get(d, [])
        arc_eps = archive_by_date.get(d, [])
        note = notes_by_date.get(d)

        # Determine status
        if note and note.note_type == "confirmed_gap":
            status = "confirmed_gap"
        elif canonical and canonical.is_reair:
            status = "reair"
        elif canonical:
            status = "canonical"
        elif files:
            status = "ingest_only"
        elif arc_eps:
            status = "archive_only"
        else:
            status = "gap"

        # Find content file for canonical
        content_file = None
        if canonical:
            content_file = session.get(IngestFile, canonical.content_file_id)

        rows.append({
            "date": d,
            "status": status,
            "canonical": canonical,
            "content_file": content_file,
            "ingest_files": files,
            "archive_eps": arc_eps,
            "note": note,
        })

    # Summary counts
    summary = {s: 0 for s in ("canonical", "reair", "ingest_only", "archive_only", "gap", "confirmed_gap")}
    for row in rows:
        summary[row["status"]] += 1
    coverage_pct = 0
    if expected:
        covered = summary["canonical"] + summary["reair"] + summary["confirmed_gap"]
        coverage_pct = round(covered / len(expected) * 100)

    return templates.TemplateResponse(request, "ingest_timeline.html", {
        "show": show,
        "rows": rows,
        "summary": summary,
        "coverage_pct": coverage_pct,
        "total_expected": len(expected),
    })


@router.post("/{show_key}/note")
async def add_note(
    show_key: str,
    request: Request,
    session: Session = Depends(get_session),
):
    form = await request.form()
    date_str = (form.get("expected_date") or "").strip()
    note_type = (form.get("note_type") or "confirmed_gap").strip()
    notes_text = (form.get("notes") or "").strip() or None
    noted_by = (form.get("noted_by") or "operator").strip()

    try:
        expected_dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return RedirectResponse(f"/ingest/timeline/{show_key}", status_code=303)

    # Upsert — one note per show+date
    existing = session.exec(
        select(ScheduleNote).where(
            ScheduleNote.show_key == show_key,
            ScheduleNote.expected_date == expected_dt,
        )
    ).first()
    if existing:
        existing.note_type = note_type
        existing.notes = notes_text
        existing.noted_by = noted_by
        existing.noted_at = datetime.utcnow()
        session.add(existing)
    else:
        session.add(ScheduleNote(
            show_key=show_key,
            expected_date=expected_dt,
            note_type=note_type,
            notes=notes_text,
            noted_by=noted_by,
        ))
    session.commit()
    return RedirectResponse(f"/ingest/timeline/{show_key}", status_code=303)


@router.post("/{show_key}/note/delete")
async def delete_note(
    show_key: str,
    request: Request,
    session: Session = Depends(get_session),
):
    form = await request.form()
    date_str = (form.get("expected_date") or "").strip()
    try:
        expected_dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return RedirectResponse(f"/ingest/timeline/{show_key}", status_code=303)

    note = session.exec(
        select(ScheduleNote).where(
            ScheduleNote.show_key == show_key,
            ScheduleNote.expected_date == expected_dt,
        )
    ).first()
    if note:
        session.delete(note)
        session.commit()
    return RedirectResponse(f"/ingest/timeline/{show_key}", status_code=303)


@router.post("/{show_key}/mark-reair")
async def mark_reair(
    show_key: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """Mark an existing CanonicalEpisode as a re-air of an earlier date."""
    form = await request.form()
    date_str = (form.get("expected_date") or "").strip()
    original_str = (form.get("original_date") or "").strip()
    noted_by = (form.get("noted_by") or "operator").strip()

    try:
        air_dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return RedirectResponse(f"/ingest/timeline/{show_key}", status_code=303)

    canonical = session.exec(
        select(CanonicalEpisode).where(
            CanonicalEpisode.show_key == show_key,
            CanonicalEpisode.true_air_date == air_dt,
        )
    ).first()
    if canonical:
        canonical.is_reair = True
        canonical.decided_by = noted_by
        canonical.decided_at = datetime.utcnow()
        if original_str:
            try:
                orig_dt = datetime.strptime(original_str, "%Y-%m-%d")
                original_canonical = session.exec(
                    select(CanonicalEpisode).where(
                        CanonicalEpisode.show_key == show_key,
                        CanonicalEpisode.true_air_date == orig_dt,
                    )
                ).first()
                if original_canonical:
                    canonical.original_canonical_id = original_canonical.id
            except ValueError:
                pass
        session.add(canonical)
        session.commit()

    return RedirectResponse(f"/ingest/timeline/{show_key}", status_code=303)
