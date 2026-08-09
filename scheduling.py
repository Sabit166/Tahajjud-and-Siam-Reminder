"""
Low-level JobQueue scheduling helpers: scheduling a poll to close or a
message to be deleted after a delay, and the job callbacks that perform
those actions.
"""

import datetime
from typing import Any, cast

from telegram.ext import ContextTypes

from config import RESPONSE_WINDOW_HOURS, RESPONSE_DELETE_AFTER_SECONDS, log
from db import delete_active_poll

# ============================================================
#  JOB CALLBACKS
# ============================================================

async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    message = cast(dict[str, Any], job.data)
    try:
        await context.bot.delete_message(
            chat_id=message["chat_id"],
            message_id=message["message_id"],
        )
        log.info(f"Deleted message: {message.get('label', 'unknown')}")
    except Exception as exc:
        log.warning(f"Could not delete message {message.get('label', 'unknown')}: {exc}")

async def close_poll_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    message = cast(dict[str, Any], job.data)
    try:
        stopped = await context.bot.stop_poll(
            chat_id=message["chat_id"],
            message_id=message["message_id"],
        )
        if stopped and stopped.id:
            delete_active_poll(stopped.id)
        log.info(f"Closed poll: {message.get('label', 'unknown')}")
    except Exception as exc:
        log.warning(f"Could not close poll {message.get('label', 'unknown')}: {exc}")

# ============================================================
#  SCHEDULING HELPERS
# ============================================================

def schedule_poll_close(job_queue, sent_message, label):
    if job_queue is None or RESPONSE_WINDOW_HOURS <= 0:
        return

    job_queue.run_once(
        close_poll_job,
        when=datetime.timedelta(hours=RESPONSE_WINDOW_HOURS),
        data={
            "chat_id": sent_message.chat_id,
            "message_id": sent_message.message_id,
            "label": label,
        },
        name=f"close_poll_{label}_{sent_message.message_id}",
    )

def schedule_message_delete(job_queue, sent_message, label):
    if job_queue is None or RESPONSE_DELETE_AFTER_SECONDS <= 0:
        return

    job_queue.run_once(
        delete_message_job,
        when=datetime.timedelta(seconds=RESPONSE_DELETE_AFTER_SECONDS),
        data={
            "chat_id": sent_message.chat_id,
            "message_id": sent_message.message_id,
            "label": label,
        },
        name=f"delete_{label}_{sent_message.message_id}",
    )
