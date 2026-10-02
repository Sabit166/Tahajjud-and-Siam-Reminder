"""
Registers all recurring JobQueue jobs: practice check-in polls, daily
and weekly reports, and the nightly amal batch.
"""

from __future__ import annotations

import datetime

from telegram.ext import Application

from config import BD_TZ, log
from jobs import (
    send_checkin_job,
    prayer_schedule_job,
    prayer_ayah_poll_job,
)

# ============================================================
#  SCHEDULER SETUP
# ============================================================

def setup_scheduler(app: Application):
    job_queue = app.job_queue
    if job_queue is None:
        raise RuntimeError("JobQueue is not available.")

    # Prayer-relative practices and reports are dispatched by the repeating
    # prayer schedule poll below.
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=10, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="quran",
        name="quran",
    )

    # Istighfar 100x - Daily at 12:00 PM (noon)
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=12, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="istighfar_100x",
        name="istighfar_100x",
    )

    # Ayah-of-the-Hour prayer-time poll — every 5 minutes the bot
    # re-fetches Aladhan's prayer times and dispatches one ayah reminder
    # per prayer per day (see messaging._prayer_ayah_poll_tick).
    job_queue.run_repeating(
        prayer_schedule_job,
        interval=datetime.timedelta(minutes=1),
        first=10,
        name="prayer_schedule",
    )
    job_queue.run_repeating(
        prayer_ayah_poll_job,
        interval=datetime.timedelta(minutes=5),
        first=10,  # seconds after scheduler starts
        name="prayer_ayah_poll",
    )

    log.info("Scheduler started. All jobs are active.")
    return job_queue
