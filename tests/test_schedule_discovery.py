"""
New-show discovery must read the dated schedule (sched), not only the weekly
grid (getshows).

Regression: in Oct 2026 seven shows on the air — Big Elvis Show, Reelin' In
the Years and others — were missing from getshows and went unarchived for two
weeks; one 14-day episode expired before anyone noticed.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

MOCK_CONFIG = {
    "station": {"timezone": "America/Chicago"},
    "confessor": {"discovery_lookback_days": 35, "discovery_lookahead_days": 14},
}


@pytest.fixture(autouse=True)
def patch_config():
    with patch("shared.config._config", MOCK_CONFIG):
        yield


import archive_manager.confessor_client as client
import archive_manager.schedule_scraper as ss
from archive_manager.confessor_client import ScheduledAiring, ScheduledShow
from shared.models import Show

NOW = datetime(2026, 10, 19, 20, 0)  # naive UTC: Mon 15:00 CDT


def _airing(key, name, local_dt, minutes=120):
    """local_dt is Central Daylight Time (UTC-5)."""
    ts = int((local_dt + timedelta(hours=5)).replace(tzinfo=timezone.utc).timestamp())
    return ScheduledAiring(show_key=key, display_name=name, air_timestamp=ts,
                           duration_seconds=minutes * 60)


SCHEDULE = [
    _airing("bigelvisshow", "Big Elvis Show", datetime(2026, 10, 14, 13, 0)),
    _airing("bigelvisshow", "Big Elvis Show", datetime(2026, 10, 21, 13, 0)),
    # Thu 10pm local is Friday in UTC
    _airing("junkdrawer", "The Junk Drawer", datetime(2026, 10, 15, 22, 0)),
    _airing("earthriotradio", "Earth Riot Radio", datetime(2026, 10, 23, 0, 0), minutes=60),
    _airing("overnight", "WDBX Overnight", datetime(2026, 10, 18, 2, 0), minutes=180),
]
WEEKLY = [
    ScheduledShow(show_key="fiasco", display_name="Folk Fiasco", day="Wednesday", day_num=3,
                  start_seconds=11 * 3600, duration_seconds=7200),
    ScheduledShow(show_key="gridonly", display_name="Grid Only Show", day="Tuesday", day_num=2,
                  start_seconds=9 * 3600, duration_seconds=3600),
]


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        # Already known — must be left alone
        s.add(Show(show_key="fiasco", display_name="Folk Fiasco", expected_duration_min=120))
        s.commit()
        yield s


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(ss, "send_alert", lambda subject, body: sent.append((subject, body)) or True)
    return sent


def _sources(monkeypatch, schedule=SCHEDULE, weekly=WEEKLY):
    def _get(value):
        def fn(*args, **kwargs):
            if isinstance(value, Exception):
                raise value
            return value
        return fn
    monkeypatch.setattr(ss, "get_schedule", _get(schedule))
    monkeypatch.setattr(ss, "get_all_shows", _get(weekly))


def _shows(session):
    return {s.show_key: s for s in session.exec(select(Show)).all()}


def test_shows_only_in_dated_schedule_are_added(session, alerts, monkeypatch):
    _sources(monkeypatch)
    counts = ss.sync_new_shows(session, now=NOW)

    shows = _shows(session)
    elvis = shows["bigelvisshow"]
    assert elvis.archive_enabled
    assert (elvis.schedule_day, elvis.schedule_time, elvis.expected_duration_min) == ("Wednesday", "130000", 120)
    assert shows["earthriotradio"].expected_duration_min == 60  # only a future airing in the window
    assert counts == {"added": 5, "skipped": 1, "failed": 0}


def test_evening_show_gets_its_local_day(session, alerts, monkeypatch):
    _sources(monkeypatch)
    ss.sync_new_shows(session, now=NOW)
    junk = _shows(session)["junkdrawer"]
    assert (junk.schedule_day, junk.schedule_time) == ("Thursday", "220000")


def test_weekly_grid_still_used(session, alerts, monkeypatch):
    _sources(monkeypatch)
    ss.sync_new_shows(session, now=NOW)
    grid = _shows(session)["gridonly"]
    assert grid.schedule_day is None  # set from the first archive entry, as before
    assert (grid.schedule_time, grid.expected_duration_min) == ("090000", 60)


def test_placeholder_names_added_with_archiving_off(session, alerts, monkeypatch):
    _sources(monkeypatch)
    ss.sync_new_shows(session, now=NOW)
    assert _shows(session)["overnight"].archive_enabled is False
    assert "archiving OFF" in alerts[0][1]


@pytest.mark.parametrize("name", [
    "TBA", "TBA OPEN Friday Morning", "Wednesday Morning TBA", "Open Slot",
    "WDBX Opera Overnight", "Filler",
])
def test_placeholder_names(name):
    assert ss._looks_like_placeholder(name)


@pytest.mark.parametrize("name", [
    # A real show the old substring match ("open") silently never archived
    "Balcony Is Open", "Reopening Night", "Big Elvis Show", "Folk Fiasco",
])
def test_real_show_names_are_not_placeholders(name):
    assert not ss._looks_like_placeholder(name)


def test_one_email_lists_every_new_show(session, alerts, monkeypatch):
    _sources(monkeypatch)
    ss.sync_new_shows(session, now=NOW)
    assert len(alerts) == 1
    subject, body = alerts[0]
    assert subject == "New shows on the schedule"
    for name in ("Big Elvis Show", "The Junk Drawer", "Earth Riot Radio", "Grid Only Show"):
        assert name in body
    assert "Folk Fiasco" not in body


def test_no_email_when_nothing_new(session, alerts, monkeypatch):
    _sources(monkeypatch, schedule=[], weekly=WEEKLY[:1])
    assert ss.sync_new_shows(session, now=NOW)["added"] == 0
    assert alerts == []


def test_dated_schedule_down_falls_back_to_grid(session, alerts, monkeypatch):
    _sources(monkeypatch, schedule=RuntimeError("sched down"))
    counts = ss.sync_new_shows(session, now=NOW)
    assert counts["added"] == 1
    assert "gridonly" in _shows(session)


def test_both_sources_down_is_a_failure(session, alerts, monkeypatch):
    _sources(monkeypatch, schedule=RuntimeError("down"), weekly=RuntimeError("down"))
    assert ss.sync_new_shows(session, now=NOW) == {"added": 0, "skipped": 0, "failed": 1}
    assert alerts == []


def test_get_schedule_parses_response_and_decodes_names(monkeypatch):
    payload = {
        "reeliinyears": {
            "sh_altid": "reeliinyears",
            "sh_name": "Reelin&#039; In the Years",
            "sh_info": 128,
            "def_time": [{"dte": 1792411200, "day": 1, "len": 7200, "id": 214502}],
        },
        "noairings": {"sh_altid": "noairings", "sh_name": "Inside Carbondale", "def_time": []},
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(client.requests, "get", lambda url, timeout: FakeResponse())
    airings = client.get_schedule(datetime(2026, 10, 1), datetime(2026, 11, 1))
    assert airings == [ScheduledAiring("reeliinyears", "Reelin' In the Years", 1792411200, 7200)]
