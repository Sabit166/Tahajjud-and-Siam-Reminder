"""
Registers the recurring JobQueue job: the per-minute schedule tick that
sends amal polls, prayer-time hadiths, and the daily/weekly reports.
"""

from __future__ import annotations

import datetime

from telegram.ext import Application

from config import log
from jobs import (
    prayer_schedule_job,
)

# ============================================================
#  SCHEDULER SETUP
# ============================================================

def setup_scheduler(app: Application):
    job_queue = app.job_queue
    if job_queue is None:
        raise RuntimeError("JobQueue is not available.")

    # Amal polls, prayer hadiths, and the built-in reports are evaluated
    # every minute by prayer_schedule_job, in time order per group, so they
    # never overlap (see messaging.BUILTIN_REPORTS).
    job_queue.run_repeating(
        prayer_schedule_job,
        interval=datetime.timedelta(minutes=1),
        first=10,
        name="prayer_schedule",
    )

    log.info("Scheduler started. All jobs are active.")
    return job_queue
