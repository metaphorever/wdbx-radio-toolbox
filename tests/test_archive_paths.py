"""
Folder and filename dates for downloads must use the station-local air date.

Regression: The Junk Drawer (Thursdays 10pm) was saved as
Friday/The Junk Drawer/2026-10-09 - ... for its 2026-10-08 broadcast,
because both the date and the show's schedule_day came from the UTC time.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

MOCK_CONFIG = {
    "station": {"timezone": "America/Chicago"},
    "archive": {"filename_template": "{date} - {show} - WDBX"},
    "nas": {"archive_path": "/nas/Shows/AutoArchive"},
}


@pytest.fixture(autouse=True)
def patch_config():
    with patch("shared.config._config", MOCK_CONFIG):
        yield


import archive_manager.nas as nas
from archive_manager.downloader import _build_filename
from shared.models import Episode, Show
from shared.station_time import station_date

# schedule_day as the old code left it: wrongly set to Friday from the UTC weekday
JUNK_DRAWER = Show(show_key="junkdrawer", display_name="The Junk Drawer",
                   expected_duration_min=120, schedule_day="Friday", schedule_time="220000")
# Thu 2026-10-08 22:00 CDT
EVENING = Episode(show_key="junkdrawer", air_datetime=datetime(2026, 10, 9, 3, 0),
                  scheduled_duration_min=120)


def test_filename_uses_station_date():
    assert _build_filename(EVENING, JUNK_DRAWER) == "2026-10-08 - The Junk Drawer - WDBX.mp3"


def test_folder_is_the_day_the_episode_aired(monkeypatch):
    monkeypatch.setattr(nas, "nas_is_writable", lambda: True)
    path = nas.get_archive_dir(JUNK_DRAWER, station_date(EVENING.air_datetime))
    assert path.parts[-2:] == ("Thursday", "The Junk Drawer")


def test_folder_falls_back_to_schedule_day_without_a_date(monkeypatch):
    monkeypatch.setattr(nas, "nas_is_writable", lambda: True)
    assert nas.get_archive_dir(JUNK_DRAWER, None).parts[-2] == "Friday"
