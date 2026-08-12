"""
Registers all recurring JobQueue jobs: practice check-in polls, daily
and weekly reports, and the nightly amal batch.
"""

import datetime

from telegram.ext import Application

from config import BD_TZ, log
from jobs import (
    send_checkin_job,
    send_nightly_amal_job,
    send_weekly_report_job,
    send_daily_report_job,
)

# ============================================================
#  SCHEDULER SETUP
# ============================================================

def setup_scheduler(app: Application):
    job_queue = app.job_queue
    if job_queue is None:
        raise RuntimeError("JobQueue is not available.")

    # Daily practice polls
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=5, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="morning_dhikr",
        name="morning_dhikr",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=5, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="fazr_jamaat",
        name="fazr_jamaat",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=5, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="ishraq_salat",
        name="ishraq_salat",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=10, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="salatud_duha",
        name="salatud_duha",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=19, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="salawat_on_rasulullah",
        name="salawat_on_rasulullah",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=19, minute=30, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="evening_dhikr",
        name="evening_dhikr",
    )

    # Fasting (Sawm) - Mondays & Thursdays at 4:00 AM
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=4, minute=0, tzinfo=BD_TZ),
        days=(1, 4),
        data="sawm",
        name="sawm",
    )

    # Surah Kahf - Thursdays at 7:00 PM
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=19, minute=0, tzinfo=BD_TZ),
        days=(4,),
        data="surah_kahf",
        name="surah_kahf",
    )

    # Tahajjud - Daily at 3:00 AM
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=3, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="tahajjud",
        name="tahajjud",
    )

    # Istighfar 100x - Daily at 12:00 PM (noon)
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=12, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="istighfar_100x",
        name="istighfar_100x",
    )

    # Daily Report - Daily at 6:30 PM
    job_queue.run_daily(
        send_daily_report_job,
        time=datetime.time(hour=18, minute=30, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="daily_report",
    )

    # Weekly Report - Fridays at 6:30 PM
    job_queue.run_daily(
        send_weekly_report_job,
        time=datetime.time(hour=18, minute=30, tzinfo=BD_TZ),
        days=(5,),
        name="weekly_report",
    )

    # Nightly Amal - Daily at 10:00 PM
    job_queue.run_daily(
        send_nightly_amal_job,
        time=datetime.time(hour=22, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="nightly_amal",
    )

    log.info("Scheduler started. All jobs are active.")
    return job_queue
