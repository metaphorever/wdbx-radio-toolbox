"""
Tests for URL assembly logic.
Non-negotiable from day one per dev plan.

Confirmed URL pattern (Q3 resolved 2026-03-23):
  https://archive.wdbx.org/mp3/wdbx_260205_070000islandreport.mp3
"""
import pytest
from datetime import date
from unittest.mock import patch

# Patch config so tests don't need a config.yaml on disk
MOCK_CONFIG = {
    "pacifica": {
        "archive_base_url": "https://archive.wdbx.org/mp3/",
        "station_prefix": "wdbx",
    }
}


@pytest.fixture(autouse=True)
def patch_config():
    with patch("shared.config._config", MOCK_CONFIG):
        yield


from archive_manager.url import build_episode_url


def test_confirmed_url_example():
    """Regression test against the confirmed live URL example."""
    result = build_episode_url(
        air_date=date(2026, 2, 5),
        show_time="070000",
        slug="islandreport",
    )
    assert result == "https://archive.wdbx.org/mp3/wdbx_260205_070000islandreport.mp3"


def test_no_underscore_between_time_and_slug():
    """Time and slug are concatenated directly — no separator."""
    result = build_episode_url(
        air_date=date(2026, 2, 5),
        show_time="070000",
        slug="islandreport",
    )
    assert "_070000islandreport" in result
    assert "_070000_islandreport" not in result


def test_date_uses_two_digit_year():
    """Date component is YYMMDD (2-digit year)."""
    result = build_episode_url(
        air_date=date(2026, 1, 5),
        show_time="100000",
        slug="testshow",
    )
    assert "260105" in result
    assert "2026" not in result


def test_midnight_show_time():
    result = build_episode_url(
        air_date=date(2026, 3, 10),
        show_time="000000",
        slug="deadsnails",
    )
    assert result == "https://archive.wdbx.org/mp3/wdbx_260310_000000deadsnails.mp3"


def test_url_ends_with_mp3():
    result = build_episode_url(
        air_date=date(2026, 2, 5),
        show_time="070000",
        slug="islandreport",
    )
    assert result.endswith(".mp3")


def test_custom_station_prefix():
    result = build_episode_url(
        air_date=date(2026, 2, 5),
        show_time="070000",
        slug="testshow",
        station_prefix="kpfk",
    )
    assert result.startswith("https://archive.wdbx.org/mp3/kpfk_")


def test_slug_with_numbers():
    """Slugs can contain numbers (e.g. backblueg from Back 2 Bluegrass 2.0)."""
    result = build_episode_url(
        air_date=date(2026, 3, 20),
        show_time="100000",
        slug="backblueg",
    )
    assert "100000backblueg" in result
