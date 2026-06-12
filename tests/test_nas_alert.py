"""
Tests for the NAS-down alert logic in the download job.

The alert must re-send every 24 hours while the NAS stays unwritable
(a one-shot flag meant the 2026-06-10 two-day outage produced a single
easy-to-miss email), and a one-time recovery email must go out on the
down→up transition.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlmodel import SQLModel, Session, create_engine

# Patch config so tests don't need a config.yaml on disk
MOCK_CONFIG = {
    "pacifica": {
        "download_delay_hours": 24,
    }
}


@pytest.fixture(autouse=True)
def patch_config():
    with patch("shared.config._config", MOCK_CONFIG):
        yield


import archive_manager.scheduler as scheduler
from shared.models import Episode


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def alerts(engine, monkeypatch):
    """Stub the engine and send_alert; reset alert state; collect sent alerts."""
    scheduler._nas_alert_last_sent = None
    scheduler._nas_outage_started = None
    sent = []
    monkeypatch.setattr(scheduler, "get_engine", lambda: engine)
    monkeypatch.setattr(
        scheduler, "send_alert",
        lambda subject, body: sent.append((subject, body)) or True,
    )
    yield sent
    scheduler._nas_alert_last_sent = None
    scheduler._nas_outage_started = None


def _run_download_job(monkeypatch, nas_ok: bool) -> None:
    monkeypatch.setattr(scheduler, "nas_is_writable", lambda: nas_ok)
    scheduler._download_job()


def test_first_failure_sends_alert(alerts, monkeypatch):
    _run_download_job(monkeypatch, nas_ok=False)
    assert len(alerts) == 1
    assert alerts[0][0] == "NAS unreachable"


def test_no_resend_within_24h(alerts, monkeypatch):
    _run_download_job(monkeypatch, nas_ok=False)
    _run_download_job(monkeypatch, nas_ok=False)
    assert len(alerts) == 1


def test_resends_after_24h(alerts, monkeypatch):
    _run_download_job(monkeypatch, nas_ok=False)
    # Age the state as if the outage started 25 hours ago
    scheduler._nas_alert_last_sent -= timedelta(hours=25)
    scheduler._nas_outage_started -= timedelta(hours=25)
    _run_download_job(monkeypatch, nas_ok=False)
    assert len(alerts) == 2
    assert "1 day, 1 hour" in alerts[1][1]


def test_recovery_sends_one_time_email(alerts, monkeypatch):
    _run_download_job(monkeypatch, nas_ok=False)
    _run_download_job(monkeypatch, nas_ok=True)
    assert [subject for subject, _ in alerts] == ["NAS unreachable", "NAS recovered"]
    # Further healthy runs stay quiet
    _run_download_job(monkeypatch, nas_ok=True)
    assert len(alerts) == 2


def test_no_recovery_email_without_prior_outage(alerts, monkeypatch):
    _run_download_job(monkeypatch, nas_ok=True)
    assert alerts == []


def test_alert_body_counts_stranded_episodes(alerts, engine, monkeypatch):
    with Session(engine) as session:
        # Stranded: downloaded locally, never copied to NAS
        session.add(Episode(
            show_key="islandreport", air_datetime=datetime(2026, 6, 1, 7),
            scheduled_duration_min=60, status="downloaded",
            local_path="/staging/a.mp3", nas_path=None,
        ))
        # Not stranded: already on the NAS
        session.add(Episode(
            show_key="islandreport", air_datetime=datetime(2026, 6, 8, 7),
            scheduled_duration_min=60, status="downloaded",
            local_path="/staging/b.mp3", nas_path="/nas/b.mp3",
        ))
        session.commit()
    _run_download_job(monkeypatch, nas_ok=False)
    assert "local staging: 1" in alerts[0][1]
