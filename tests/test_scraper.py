"""
Archive scraping: the show's air day must come from station-local time, and
shows marked gone must still be scraped so their last episodes are kept.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

MOCK_CONFIG = {"station": {"timezone": "America/Chicago"}}


@pytest.fixture(autouse=True)
def patch_config():
    with patch("shared.config._config", MOCK_CONFIG):
        yield


import archive_manager.scraper as scraper
from shared.models import Episode, Show


def _ts(*utc):
    return int(datetime(*utc, tzinfo=timezone.utc).timestamp())


# Thu 2026-10-15 22:00 CDT == Fri 03:00 UTC
THU_10PM = _ts(2026, 10, 16, 3, 0)


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _archive(monkeypatch, entries_by_show):
    calls = []

    def fake_fetch(show_key, num=20):
        calls.append(show_key)
        return entries_by_show.get(show_key, [])

    monkeypatch.setattr(scraper, "fetch_show_episodes", fake_fetch)
    return calls


def _entry(slug, ts):
    return {"mp3": f"https://archive.wdbx.org/mp3/wdbx_x_{slug}.mp3", "def_time": ts,
            "expires": ts + 14 * 86400}


def test_evening_show_day_is_station_local(session, monkeypatch):
    session.add(Show(show_key="junkdrawer", display_name="The Junk Drawer",
                     expected_duration_min=120, schedule_day="Friday"))
    session.commit()
    _archive(monkeypatch, {"junkdrawer": [_entry("junkdrawer", THU_10PM)]})

    scraper.sync_episodes(session)

    show = session.exec(select(Show).where(Show.show_key == "junkdrawer")).one()
    assert show.schedule_day == "Thursday"


def test_gone_shows_are_still_scraped(session, monkeypatch):
    session.add(Show(show_key="finale", display_name="Last Show", expected_duration_min=120,
                     archive_enabled=True, is_gone=True))
    session.add(Show(show_key="off", display_name="Disabled", expected_duration_min=120,
                     archive_enabled=False))
    session.commit()
    calls = _archive(monkeypatch, {"finale": [_entry("finale", THU_10PM)]})

    counts = scraper.sync_episodes(session)

    assert calls == ["finale"]  # disabled shows are still skipped
    assert counts["created"] == 1
    assert session.exec(select(Episode)).one().show_key == "finale"
