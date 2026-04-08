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
from archive_manager.schedule_scraper import sync_gone_shows, sync_new_shows
from archive_manager.scraper import sync_episodes
from shared.config import get
from shared.database import get_engine
from shared.models import AnalysisResult, Episode, IngestFile, SystemEvent

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
    """Sync new shows and gone shows from the Confessor API."""
    logger.info("Schedule sync job started")
    try:
        with Session(get_engine()) as session:
            counts = sync_new_shows(session)
        if counts["added"] > 0:
            logger.info("Schedule sync: %d new show(s) discovered", counts["added"])
        else:
            logger.info("Schedule sync: no new shows")
    except Exception as e:
        logger.error("Schedule sync (new shows) failed: %s", e)

    try:
        with Session(get_engine()) as session:
            gone_count = sync_gone_shows(session)
        if gone_count > 0:
            logger.info("Schedule sync: %d show(s) marked gone", gone_count)
    except Exception as e:
        logger.error("Schedule sync (gone shows) failed: %s", e)


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
            ).order_by(Episode.expires_at.is_(None), Episode.expires_at)
        ).all()

        if pending:
            logger.info("Download job: processing %d pending episode(s)", len(pending))
        for episode in pending:
            download_episode(episode, session)


def _segment_fingerprint_job() -> None:
    """Overnight job: fingerprint a batch of unfingerprinted IngestFiles."""
    from ingest.fingerprinter import fingerprint_file
    from pathlib import Path
    BATCH = 20
    logger.info("Segment fingerprint job started (batch=%d)", BATCH)
    try:
        with Session(get_engine()) as session:
            files = session.exec(
                select(IngestFile).where(
                    IngestFile.fingerprint == None,
                    IngestFile.status.in_(["matched", "canonical"]),
                )
                .limit(BATCH)
            ).all()
            done = 0
            for f in files:
                fp, dur = fingerprint_file(Path(f.file_path))
                if fp:
                    f.fingerprint = fp
                    f.fingerprint_duration = dur
                    session.add(f)
                    done += 1
            session.commit()
            logger.info("Fingerprint job: %d/%d files processed", done, len(files))
    except Exception as e:
        logger.error("Fingerprint job failed: %s", e)


def _reair_detection_job() -> None:
    """Overnight job: detect re-air chains for all shows with fingerprinted files."""
    from ingest.reair_detector import detect_reairs
    from sqlmodel import distinct
    logger.info("Re-air detection job started")
    try:
        with Session(get_engine()) as session:
            # Find distinct show_keys that have fingerprinted files
            show_keys = session.exec(
                select(distinct(IngestFile.show_key)).where(
                    IngestFile.fingerprint != None,
                    IngestFile.show_key != None,
                )
            ).all()
            total = {"marked_reair": 0, "gray_zone": 0, "compared": 0}
            for show_key in show_keys:
                counts = detect_reairs(session, show_key)
                for k in total:
                    total[k] += counts.get(k, 0)
            logger.info("Re-air detection complete: %s", total)
    except Exception as e:
        logger.error("Re-air detection job failed: %s", e)


def _analysis_job() -> None:
    """Overnight job: run analysis on downloaded episodes that haven't been analyzed yet."""
    from processor.analyzer import analyze_episode, ANALYSIS_VERSION
    BATCH = 5   # analysis is slow; small batch, run nightly
    logger.info("Analysis job started (batch=%d)", BATCH)
    try:
        with Session(get_engine()) as session:
            # Find downloaded episodes with no analysis result
            analyzed_ids = session.exec(
                select(AnalysisResult.episode_id).where(
                    AnalysisResult.analysis_version == ANALYSIS_VERSION
                )
            ).all()
            analyzed_id_list = list(set(analyzed_ids))
            if analyzed_id_list:
                episodes = session.exec(
                    select(Episode).where(
                        Episode.status == "downloaded",
                        Episode.id.not_in(analyzed_id_list),
                    ).limit(BATCH)
                ).all()
            else:
                episodes = session.exec(
                    select(Episode).where(
                        Episode.status == "downloaded",
                    ).limit(BATCH)
                ).all()
            done = 0
            for ep in episodes:
                result = analyze_episode(ep, session)
                if result:
                    done += 1
            logger.info("Analysis job: %d/%d episodes processed", done, len(episodes))
    except Exception as e:
        logger.error("Analysis job failed: %s", e)


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
        scrape_interval = int(get("pacifica.scrape_interval_hours", 6))
        schedule_sync_interval = int(get("confessor.schedule_sync_interval_hours", 24))
        _scheduler.add_job(_scrape_job, "interval", hours=scrape_interval, id="archive_scrape")
        _scheduler.add_job(_download_job, "interval", hours=1, id="archive_download")
        _scheduler.add_job(_schedule_sync_job, "interval", hours=schedule_sync_interval, id="schedule_sync")
        _scheduler.add_job(_segment_fingerprint_job, "cron", hour=2, minute=0, id="segment_fingerprint")
        _scheduler.add_job(_reair_detection_job, "cron", hour=3, minute=0, id="reair_detection")
        _scheduler.add_job(_analysis_job, "cron", hour=4, minute=0, id="episode_analysis")
        logger.info(
            "Scheduler configured: scrape every %dh, download check every 1h, schedule sync every %dh, "
            "fingerprint@2am, reair@3am, analysis@4am",
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
