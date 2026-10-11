"""
JobQueue callback wrappers that trigger check-in polls and reports on a
schedule. See scheduling.py for the poll-close/message-delete job
callbacks.
"""

from __future__ import annotations

from typing import cast

from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from config import log

from messaging import (
    send_checkin,
    fetch_today_timings,
    _prayer_schedule_tick,
    _prayer_hadith_poll_tick,
)


async def send_checkin_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    await send_checkin(context.bot, cast(str, job.data), context.job_queue)


def bot_removed_from_chat(exc: BaseException) -> bool:
    """True when Telegram says the bot can no longer post to the chat
    (kicked, removed, blocked, or the chat no longer exists)."""
    if isinstance(exc, Forbidden):
        return True
    return isinstance(exc, BadRequest) and "chat not found" in str(exc).lower()


async def refresh_groups(bot) -> None:
    """At startup, sync each active group with Telegram.

    Restores titles and deactivates groups the bot is no longer in (their
    leave update may have been missed while the bot was down).
    """
    from telegram.error import ChatMigrated
    from db import deactivate_group, list_groups, register_group

    refreshed = removed = 0
    for group in list_groups():
        chat_id = int(group["chat_id"])
        try:
            chat = await bot.get_chat(chat_id)
        except ChatMigrated as exc:
            # A basic group upgraded to a supergroup gets a new id; its data
            # stays under the old id and needs a manual move.
            log.warning("Group %s was upgraded to supergroup %s.", chat_id, exc.new_chat_id)
            continue
        except Exception as exc:
            if bot_removed_from_chat(exc):
                deactivate_group(chat_id, f"{type(exc).__name__}: {exc}")
                removed += 1
            else:
                log.warning("Could not refresh group %s: %s", chat_id, exc)
            continue
        register_group(chat_id, chat.title, getattr(chat, "username", None))
        refreshed += 1
    log.info("Groups refreshed: %d active, %d deactivated.", refreshed, removed)


def _handle_group_failure(kind: str, chat_id: int, exc: Exception) -> None:
    if bot_removed_from_chat(exc):
        # The leave update can be missed (e.g. during a restart); stop
        # scheduling for this group instead of failing every run.
        from db import deactivate_group
        deactivate_group(chat_id, f"{type(exc).__name__}: {exc}")
    else:
        # One group's failure must not stop the other groups.
        log.exception("%s tick failed for group %s", kind, chat_id)


async def prayer_hadith_poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Every-five-minute tick that dispatches hadith reminders."""
    from db import list_groups, set_current_group
    timings = await fetch_today_timings()  # once for all groups
    if timings is None:
        return
    for group in list_groups():
        set_current_group(int(group["chat_id"]))
        try:
            await _prayer_hadith_poll_tick(context, timings)
        except Exception as exc:
            _handle_group_failure("Hadith", int(group["chat_id"]), exc)


async def prayer_schedule_job(context: ContextTypes.DEFAULT_TYPE):
    """Dispatch prayer-relative practices and reports once per event."""
    from db import list_groups, set_current_group
    timings = await fetch_today_timings()  # once for all groups
    if timings is None:
        return
    for group in list_groups():
        set_current_group(int(group["chat_id"]))
        try:
            await _prayer_schedule_tick(context, timings)
        except Exception as exc:
            _handle_group_failure("Schedule", int(group["chat_id"]), exc)
