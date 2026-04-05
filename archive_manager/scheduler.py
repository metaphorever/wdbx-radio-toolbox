"""
APScheduler background jobs for the Archive Manager.

Jobs:
  scrape  — fetch archive listing and sync Episode records (every N hours)
  download — pick up pending episodes ready for download (every hour)

Both jobs are idempotent. The scrape interval comes from config.yaml
pacifica.scrape_interval_hours. Download delay from pacifica.download_delay_hours.
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from archive_manager.downloader import download_episode
from archive_manager.mailer import send_alert
from archive_manager.nas import nas_is_writable
from archive_manager.schedule_scraper import sync_new_shows
from archive_manager.scraper import sync_episodes
from shared.config import get
from shared.database import get_engine
from shared.models import Episode, SystemEvent

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# Track whether we've already emitted a NAS-unreachable event this cycle
# so we don't spam the event log on every hourly run.
_nas_alert_sent = False


def _scrape_job() -> None:
    logger.info("Scrape job started")
    try:
        with Session(get_engine()) as session:
            counts = sync_episodes(session)
        logger.info("Scrape job complete: %s", counts)
    except Exception as e:
        logger.error("Scrape job failed: %s", e)
        send_alert("Archive scrape failed", str(e))


def _schedule_sync_job() -> None:
    """Check Confessor schedule page for new shows not yet in the DB."""
    logger.info("Schedule sync job started")
    try:
        with Session(get_engine()) as session:
            counts = sync_new_shows(session)
        if counts["added"] > 0:
            logger.info("Schedule sync: %d new show(s) discovered", counts["added"])
        else:
            logger.info("Schedule sync: no new shows")
    except Exception as e:
        logger.error("Schedule sync job failed: %s", e)


def _download_job() -> None:
    global _nas_alert_sent

    delay_hours = int(get("pacifica.download_delay_hours", 24))
    cutoff = datetime.utcnow() - timedelta(hours=delay_hours)

    # NAS health check — emit one event and one email per outage
    nas_ok = nas_is_writable()
    if not nas_ok and not _nas_alert_sent:
        with Session(get_engine()) as session:
            # Only add an event if there isn't an unresolved one
            existing = session.exec(
                select(SystemEvent).where(
                    SystemEvent.severity == "warning",
                    SystemEvent.resolved_at == None,
                )
            ).first()
            if not existing:
                session.add(SystemEvent(
                    severity="warning",
                    message="NAS unreachable — downloads routing to local staging",
                ))
                session.commit()
        send_alert(
            "NAS unreachable",
            "The NAS mount is not writable. Downloads are routing to local staging. "
            "Use 'Copy to NAS' in the Archive Manager UI once the drive is back online.",
        )
        _nas_alert_sent = True
    elif nas_ok:
        _nas_alert_sent = False   # reset so we alert again if it goes down again

    with Session(get_engine()) as session:
        pending = session.exec(
            select(Episode).where(
                Episode.status == "pending",
                Episode.air_datetime <= cutoff,
            )
        ).all()

        if pending:
            logger.info("Download job: processing %d pending episode(s)", len(pending))
        for episode in pending:
            download_episode(episode, session)


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
        scrape_interval = int(get("pacifica.scrape_interval_hours", 6))
        schedule_sync_interval = int(get("confessor.schedule_sync_interval_hours", 24))
        _scheduler.add_job(_scrape_job, "interval", hours=scrape_interval, id="archive_scrape")
        _scheduler.add_job(_download_job, "interval", hours=1, id="archive_download")
        _scheduler.add_job(_schedule_sync_job, "interval", hours=schedule_sync_interval, id="schedule_sync")
        logger.info(
            "Scheduler configured: scrape every %dh, download check every 1h, schedule sync every %dh",
            scrape_interval, schedule_sync_interval,
        )
    return _scheduler


def start_scheduler() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        logger.info("Archive Manager scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Archive Manager scheduler stopped")
