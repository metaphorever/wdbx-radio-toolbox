"""
Email alert utility — SMTP via Python smtplib.

All SMTP settings come from config.yaml. If smtp.host is empty,
alerts are logged but not sent (graceful no-op).
"""
import logging
import smtplib
from email.mime.text import MIMEText

from shared.config import get

logger = logging.getLogger(__name__)


def send_alert(subject: str, body: str) -> bool:
    """
    Send an alert email to the station manager.
    Returns True on success. Returns False (without raising) if SMTP is
    not configured or the send fails.
    """
    host = get("smtp.host", "")
    if not host:
        logger.debug("SMTP not configured — skipping alert: %s", subject)
        return False

    port = int(get("smtp.port", 587))
    user = get("smtp.user", "")
    password = get("smtp.password", "")
    from_addr = get("smtp.from_addr", "") or user
    to_addr = get("smtp.to_addr", "")

    if not to_addr:
        logger.warning("smtp.to_addr not set — cannot send alert: %s", subject)
        return False

    msg = MIMEText(body)
    msg["Subject"] = f"[WDBX Toolbox] {subject}"
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        logger.info("Alert sent: %s", subject)
        return True
    except Exception as e:
        logger.error("Failed to send alert '%s': %s", subject, e)
        return False
