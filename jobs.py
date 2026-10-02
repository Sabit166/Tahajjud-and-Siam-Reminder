"""
JobQueue callback wrappers that trigger check-in polls and reports on a
schedule. See scheduling.py for the poll-close/message-delete job
callbacks.
"""

from __future__ import annotations

from typing import cast

from telegram.ext import ContextTypes

from messaging import (
    send_checkin,
    _prayer_schedule_tick,
    _prayer_ayah_poll_tick,
)


async def send_checkin_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    await send_checkin(context.bot, cast(str, job.data), context.job_queue)


async def prayer_ayah_poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Every-5-minute tick that dispatches Ayah-of-the-Hour reminders
    at each of the 5 prayer times. See ``messaging._prayer_ayah_poll_tick``."""
    await _prayer_ayah_poll_tick(context)


async def prayer_schedule_job(context: ContextTypes.DEFAULT_TYPE):
    """Dispatch prayer-relative practices and reports once per event."""
    await _prayer_schedule_tick(context)
