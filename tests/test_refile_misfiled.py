"""
tools/refile_misfiled.py: moves episodes filed under the wrong day or date to
where the toolbox files them now, never overwriting, and can undo itself.
"""
import importlib.util
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

_spec = importlib.util.spec_from_file_location(
    "refile_misfiled", Path(__file__).resolve().parents[1] / "tools" / "refile_misfiled.py")
refile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refile)

from shared.models import Episode, Show


@pytest.fixture
def base(tmp_path):
    b = tmp_path / "AutoArchive"
    b.mkdir()
    cfg = {
        "station": {"timezone": "America/Chicago"},
        "archive": {"filename_template": "{date} - {show} - WDBX"},
        "nas": {"archive_path": str(b)},
    }
    with patch("shared.config._config", cfg):
        yield b


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        for key, name in [("poodle", "DJ Warren Show"), ("callwild", "Call Of The Wild"),
                          ("democracynow", "Democracy Now"), ("nightly", "Nightly")]:
            s.add(Show(show_key=key, display_name=name, expected_duration_min=120))
        s.commit()
        yield s


def _episode(session, base, key, utc, rel, content):
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    ep = Episode(show_key=key, air_datetime=utc, scheduled_duration_min=120, status="downloaded",
                 local_path=str(path), nas_path=str(path))
    session.add(ep)
    session.commit()
    return ep.id


@pytest.fixture
def archive(session, base):
    ids = {
        # Sun 2026-04-12 22:00 CDT, filed under Monday with Monday's date
        "evening": _episode(session, base, "poodle", datetime(2026, 4, 13, 3, 0),
                            "Monday/DJ Warren Show/2026-04-13 [DJ Warren Show] - WDBX.mp3", "evening"),
        # Already right: bracketed name, right folder and date — must stay put
        "correct": _episode(session, base, "poodle", datetime(2026, 10, 19, 3, 0),
                            "Sunday/DJ Warren Show/2026-10-18 [DJ Warren Show] - WDBX.mp3", "correct"),
        # Early March 2026 layout, UTC time in the name
        "old_layout": _episode(session, base, "callwild", datetime(2026, 3, 30, 23, 0),
                               "2026/callwild/callwild_260330_230000.mp3", "old"),
        # Fri 17:00 filed under Monday; its target is already taken by another file
        "collision": _episode(session, base, "democracynow", datetime(2026, 4, 3, 22, 0),
                              "Monday/Democracy Now/2026-04-03 [Democracy Now] - WDBX.mp3", "dn"),
        # A nightly 10pm show: Monday's episode must move into the spot Sunday's vacates
        "chain_sun": _episode(session, base, "nightly", datetime(2026, 4, 13, 3, 0),
                              "Monday/Nightly/2026-04-13 - Nightly - WDBX.mp3", "sun"),
        "chain_mon": _episode(session, base, "nightly", datetime(2026, 4, 14, 3, 0),
                              "Tuesday/Nightly/2026-04-14 - Nightly - WDBX.mp3", "mon"),
    }
    blocker = base / "Friday/Democracy Now/2026-04-03 - Democracy Now - WDBX.mp3"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("someone else's copy")
    return ids


def _nas_path(session, episode_id):
    session.expire_all()
    return Path(session.get(Episode, episode_id).nas_path)


def test_plan_finds_misfiled_and_skips_correct(session, base, archive):
    moves, _ = refile.plan(session, base)
    by_id = {m.episode_id: m for m in moves}
    assert archive["correct"] not in by_id
    assert by_id[archive["evening"]].new == base / "Sunday/DJ Warren Show/2026-04-12 - DJ Warren Show - WDBX.mp3"
    assert by_id[archive["old_layout"]].new == base / "Monday/Call Of The Wild/2026-03-30 - Call Of The Wild - WDBX.mp3"
    assert by_id[archive["collision"]].problem == "target already exists"
    assert by_id[archive["chain_mon"]].problem == ""   # its target is freed by chain_sun


def test_dry_run_changes_nothing(session, base, archive):
    before = sorted(p for p in base.rglob("*") if p.is_file())
    refile.plan(session, base)
    assert sorted(p for p in base.rglob("*") if p.is_file()) == before


def test_apply_moves_files_and_updates_db(session, base, archive, tmp_path):
    moves, _ = refile.plan(session, base)
    done, stuck = refile.apply(session, moves, base, tmp_path / "log.tsv")

    assert done == 4 and stuck == []
    evening = base / "Sunday/DJ Warren Show/2026-04-12 - DJ Warren Show - WDBX.mp3"
    assert evening.read_text() == "evening"
    assert _nas_path(session, archive["evening"]) == evening
    assert session.get(Episode, archive["evening"]).local_path == str(evening)
    # Chain resolved: each nightly episode ends up under its own day and date
    assert (base / "Sunday/Nightly/2026-04-12 - Nightly - WDBX.mp3").read_text() == "sun"
    assert (base / "Monday/Nightly/2026-04-13 - Nightly - WDBX.mp3").read_text() == "mon"
    # Never overwrites: both files of the collision are untouched
    assert (base / "Monday/Democracy Now/2026-04-03 [Democracy Now] - WDBX.mp3").read_text() == "dn"
    assert (base / "Friday/Democracy Now/2026-04-03 - Democracy Now - WDBX.mp3").read_text() == "someone else's copy"
    # The correct file stayed, and the emptied old-layout folders are gone
    assert (base / "Sunday/DJ Warren Show/2026-10-18 [DJ Warren Show] - WDBX.mp3").exists()
    assert not (base / "2026").exists()


def test_undo_restores_everything(session, base, archive, tmp_path):
    before = {p.relative_to(base): p.read_text() for p in base.rglob("*") if p.is_file()}
    moves, _ = refile.plan(session, base)
    refile.apply(session, moves, base, tmp_path / "log.tsv")

    back, skipped = refile.undo(session, tmp_path / "log.tsv")

    assert (back, skipped) == (4, 0)
    assert {p.relative_to(base): p.read_text() for p in base.rglob("*") if p.is_file()} == before
    assert _nas_path(session, archive["old_layout"]) == base / "2026/callwild/callwild_260330_230000.mp3"
