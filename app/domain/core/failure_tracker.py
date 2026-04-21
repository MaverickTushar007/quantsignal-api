"""
app/domain/core/failure_tracker.py
Tracks consecutive failures per job and fires Telegram alert at threshold.
Resets on success. Alerts only once per streak to avoid spam.
"""
from __future__ import annotations
import logging
log = logging.getLogger(__name__)

ALERT_THRESHOLD = 3

_counters: dict[str, int] = {}
_alerted:  dict[str, bool] = {}


def record_failure(job_id: str) -> int:
    _counters[job_id] = _counters.get(job_id, 0) + 1
    count = _counters[job_id]
    log.warning(f"[FailureTracker] {job_id} failed — consecutive={count}")
    if should_alert(job_id):
        _send_alert(job_id, count)
    return count


def record_success(job_id: str) -> None:
    if _counters.get(job_id, 0) > 0:
        log.info(f"[FailureTracker] {job_id} recovered after {_counters[job_id]} failures")
    _counters[job_id] = 0
    _alerted[job_id]  = False


def should_alert(job_id: str) -> bool:
    count = _counters.get(job_id, 0)
    already = _alerted.get(job_id, False)
    if count >= ALERT_THRESHOLD and not already:
        _alerted[job_id] = True
        return True
    return False


def get_count(job_id: str) -> int:
    return _counters.get(job_id, 0)


def get_all_status() -> dict:
    all_jobs = set(list(_counters.keys()) + list(_alerted.keys()))
    return {
        job: {
            "consecutive_failures": _counters.get(job, 0),
            "alerted": _alerted.get(job, False),
            "healthy": _counters.get(job, 0) == 0,
        }
        for job in all_jobs
    }


def reset_all() -> None:
    _counters.clear()
    _alerted.clear()


def _send_alert(job_id: str, count: int) -> None:
    """Fire Telegram alert to admin when job hits failure threshold."""
    try:
        import os, requests
        token   = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            log.warning(f"[FailureTracker] No Telegram config — skipping alert for {job_id}")
            return
        msg = (
            f"🚨 QuantSignal Job Failure Alert\n\n"
            f"Job: {job_id}\n"
            f"Consecutive failures: {count}\n"
            f"Action: Check Railway logs immediately"
        )
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=5,
        )
        log.info(f"[FailureTracker] Telegram alert sent for {job_id}")
    except Exception as e:
        log.error(f"[FailureTracker] Alert send failed: {e}")
