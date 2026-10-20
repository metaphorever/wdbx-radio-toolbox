"""
Station-local time conversion — the root of the wrong-day-folder bug.

Episode times are stored as UTC. A Thursday 10pm Central show is already
Friday in UTC, so anything dated or filed from the raw UTC value lands a day
late. 6pm shows only cross midnight in standard time, so they flip in November.
"""
from datetime import date, datetime
from unittest.mock import patch

import pytest

MOCK_CONFIG = {"station": {"timezone": "America/Chicago"}}


@pytest.fixture(autouse=True)
def patch_config():
    with patch("shared.config._config", MOCK_CONFIG):
        yield


from shared.station_time import from_timestamp, station_date, station_weekday, to_station


def test_evening_show_in_daylight_time():
    # The Junk Drawer: Thu 2026-10-08 22:00 CDT is Fri 03:00 UTC
    utc = datetime(2026, 10, 9, 3, 0)
    assert to_station(utc) == datetime(2026, 10, 8, 22, 0)
    assert station_date(utc) == date(2026, 10, 8)
    assert station_weekday(utc) == "Thursday"


def test_six_pm_show_crosses_midnight_utc_only_in_standard_time():
    # 6pm CDT is 23:00 UTC the same day...
    assert station_weekday(datetime(2026, 10, 19, 23, 0)) == "Monday"
    # ...but after DST ends on Nov 1, 6pm CST is 00:00 UTC the next day
    assert station_weekday(datetime(2026, 11, 3, 0, 0)) == "Monday"
    assert station_date(datetime(2026, 11, 3, 0, 0)) == date(2026, 11, 2)


def test_morning_show_unchanged():
    # 7am CDT
    assert station_date(datetime(2026, 10, 19, 12, 0)) == date(2026, 10, 19)


def test_from_timestamp():
    # def_time of Reelin' In the Years, Mon 2026-10-19 07:00 CDT
    assert from_timestamp(1792411200) == datetime(2026, 10, 19, 7, 0)
