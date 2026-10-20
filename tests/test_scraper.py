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


def _show(session, key):
    return session.exec(select(Show).where(Show.show_key == key)).one()


def test_evening_show_day_is_station_local(session, monkeypatch):
    session.add(Show(show_key="junkdrawer", display_name="The Junk Drawer",
                     expected_duration_min=120, schedule_day="Friday"))
    session.commit()
    _archive(monkeypatch, {"junkdrawer": [_entry("junkdrawer", THU_10PM)]})

    scraper.sync_episodes(session)

    show = _show(session, "junkdrawer")
    assert (show.schedule_day, show.schedule_time) == ("Thursday", "220000")


def test_moved_time_is_corrected(session, monkeypatch):
    # Down Home Cookin': seeded Wednesday 13:00, now airs Thursday 11:00
    session.add(Show(show_key="downhomecooki", display_name="Down Home Cookin'",
                     expected_duration_min=120, schedule_day="Wednesday", schedule_time="130000"))
    session.commit()
    _archive(monkeypatch, {"downhomecooki": [_entry("downhomecooki", _ts(2026, 10, 15, 16, 0))]})

    scraper.sync_episodes(session)

    show = _show(session, "downhomecooki")
    assert (show.schedule_day, show.schedule_time) == ("Thursday", "110000")


def test_midnight_show_belongs_to_the_day_it_starts(session, monkeypatch):
    # Chillin with Bob Dylan: Thu 00:00 CDT (late Wednesday night) == Thu 05:00 UTC
    session.add(Show(show_key="chillibobdylan", display_name="Chillin with Bob Dylan",
                     expected_duration_min=120, schedule_day="Monday", schedule_time="200000"))
    session.commit()
    _archive(monkeypatch, {"chillibobdylan": [_entry("chillibobdylan", _ts(2026, 10, 15, 5, 0))]})

    scraper.sync_episodes(session)

    show = _show(session, "chillibobdylan")
    assert (show.schedule_day, show.schedule_time) == ("Thursday", "000000")


def test_schedule_follows_newest_entry_whatever_the_api_order(session, monkeypatch):
    # Island Report moved Tuesday → Thursday; entries listed oldest-first here
    session.add(Show(show_key="islandreport", display_name="Island Report", expected_duration_min=120))
    session.commit()
    _archive(monkeypatch, {"islandreport": [
        _entry("islandreport-old", _ts(2026, 10, 6, 12, 0)),    # Tue 07:00 CDT
        _entry("islandreport-new", _ts(2026, 10, 15, 12, 0)),   # Thu 07:00 CDT
    ]})

    scraper.sync_episodes(session)

    show = _show(session, "islandreport")
    assert (show.schedule_day, show.schedule_time) == ("Thursday", "070000")


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
