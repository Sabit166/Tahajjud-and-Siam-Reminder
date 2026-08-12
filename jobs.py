"""
JobQueue callback wrappers that trigger check-in polls and reports on a
schedule. See scheduling.py for the poll-close/message-delete job
callbacks.
"""

from typing import cast

from telegram.ext import ContextTypes

from messaging import send_checkin, send_nightly_amal, send_weekly_report, send_daily_report, send_jumuah_reminder

async def send_checkin_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    await send_checkin(context.bot, cast(str, job.data), context.job_queue)

async def send_nightly_amal_job(context: ContextTypes.DEFAULT_TYPE):
    await send_nightly_amal(context.bot, context.job_queue)

async def send_weekly_report_job(context: ContextTypes.DEFAULT_TYPE):
    await send_weekly_report(context.bot)

async def send_daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    await send_daily_report(context.bot)

async def send_jumuah_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    await send_jumuah_reminder(context.bot)
