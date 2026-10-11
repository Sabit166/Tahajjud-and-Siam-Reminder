"""
Registers the recurring JobQueue jobs: the per-minute schedule tick
(amal polls and daily/weekly reports) and the prayer-time hadith tick.
"""

from __future__ import annotations

import datetime

from telegram.ext import Application

from config import log
from jobs import (
    prayer_schedule_job,
    prayer_hadith_poll_job,
)

# ============================================================
#  SCHEDULER SETUP
# ============================================================

def setup_scheduler(app: Application):
    job_queue = app.job_queue
    if job_queue is None:
        raise RuntimeError("JobQueue is not available.")

    # Amal polls and the built-in daily/weekly reports are evaluated every minute
    # by prayer_schedule_job based on poll_configs and prayer times.

    # Prayer-time hadith poll — every 5 minutes the bot re-fetches
    # Aladhan's prayer times and dispatches one hadith reminder
    # per prayer per day (see messaging._prayer_hadith_poll_tick).
    job_queue.run_repeating(
        prayer_schedule_job,
        interval=datetime.timedelta(minutes=1),
        first=10,
        name="prayer_schedule",
    )
    job_queue.run_repeating(
        prayer_hadith_poll_job,
        interval=datetime.timedelta(minutes=5),
        first=10,  # seconds after scheduler starts
        name="prayer_hadith_poll",
    )

    log.info("Scheduler started. All jobs are active.")
    return job_queue
