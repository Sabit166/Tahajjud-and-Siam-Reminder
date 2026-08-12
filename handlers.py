"""
Telegram update handlers: processing poll answers and welcoming new
group members.
"""

from telegram import Update
from telegram.ext import ContextTypes

from config import GROUP_CHAT_ID, BD_TZ, log
from practices import PRACTICES, GROUP_AMAL_LABELS
from db import get_poll_practice, save_response, update_streak_for_response
from scheduling import schedule_message_delete

# ============================================================
#  HANDLERS
# ============================================================

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if answer is None or answer.user is None:
        return

    user = answer.user
    full_name = user.full_name or user.username or str(user.id)
    username = user.username or ""
    poll_id = answer.poll_id

    practice_key = get_poll_practice(poll_id)

    if practice_key in PRACTICES:
        p_info = PRACTICES[practice_key]
        label = p_info.get("label", practice_key)

        if not answer.option_ids:
            log.info(f"User {full_name} retracted vote for {label}")
            return

        did_it = 1 if 0 in answer.option_ids else 0
        save_response(user.id, username, full_name, practice_key, did_it)

        # Update streak — pass the date the poll was meant for (today in BD_TZ).
        import datetime as _dt
        scheduled_date = _dt.datetime.now(BD_TZ).strftime("%Y-%m-%d")
        update_streak_for_response(user.id, practice_key, scheduled_date, bool(did_it))

        if did_it:
            reply = f"MashaAllah --- {full_name} --- {label}"
        else:
            reply = f"InshaAllah next time --- {full_name} --- {label}"

        response_message = await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=reply,
            parse_mode="Markdown",
        )
        schedule_message_delete(context.job_queue, response_message, f"poll_reply_{practice_key}")

    else:
        log.info(f"Received poll answer from {full_name} for untracked poll ID {poll_id}")

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message is None or not message.new_chat_members:
        return

    for member in message.new_chat_members:
        if member.is_bot:
            continue

        display_name = member.full_name or member.username or "brother"
        welcome_text = (
            f"Assalamu Alaikum wa Rahmatullahi wa Barakatuh, <b>{display_name}</b>\n\n"
            "Welcome to our little circle of remembrance and accountability. May Allah make your stay here beneficial, easy, and full of barakah.\n\n"
            "Here is the amal we follow together:\n"
        )
        for label in GROUP_AMAL_LABELS:
            welcome_text += f"• {label}\n"

        welcome_text += (
            "\nWe ask Allah to accept every effort, even the small ones, and to keep our hearts firm upon goodness. Ameen."
        )

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=welcome_text,
            parse_mode="HTML",
        )
