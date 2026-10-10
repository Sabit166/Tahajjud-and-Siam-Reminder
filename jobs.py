"""
JobQueue callback wrappers that trigger check-in polls and reports on a
schedule. See scheduling.py for the poll-close/message-delete job
callbacks.
"""

from __future__ import annotations

from typing import cast

from telegram.ext import ContextTypes

from config import log

from messaging import (
    send_checkin,
    _prayer_schedule_tick,
    _prayer_hadith_poll_tick,
)


async def send_checkin_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    await send_checkin(context.bot, cast(str, job.data), context.job_queue)


async def prayer_hadith_poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Every-five-minute tick that dispatches hadith reminders."""
    from db import list_groups, set_current_group
    for group in list_groups():
        set_current_group(int(group["chat_id"]))
        try:
            await _prayer_hadith_poll_tick(context)
        except Exception:
            # One group's failure must not stop the other groups' reminders.
            log.exception("Hadith tick failed for group %s", group["chat_id"])


async def prayer_schedule_job(context: ContextTypes.DEFAULT_TYPE):
    """Dispatch prayer-relative practices and reports once per event."""
    from db import list_groups, set_current_group
    for group in list_groups():
        set_current_group(int(group["chat_id"]))
        try:
            await _prayer_schedule_tick(context)
        except Exception:
            # One group's failure must not stop the other groups' polls.
            log.exception("Schedule tick failed for group %s", group["chat_id"])
