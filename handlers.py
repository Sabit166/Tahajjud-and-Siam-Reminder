"""
Telegram update handlers: processing poll answers and welcoming new
group members.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import BD_TZ, log
from practices import GROUP_AMAL_LABELS
from db import (
    get_poll_practice,
    save_response,
    update_streak_for_response,
    set_current_group,
    register_group,
    issue_setup_token,
    get_setup_token,
)
from scheduling import schedule_message_delete

# ============================================================
#  HANDLERS
# ============================================================

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if answer is None or answer.user is None:
        return

    user = answer.user
    from db import get_poll_group
    chat_id = get_poll_group(answer.poll_id)
    set_current_group(chat_id)
    full_name = user.full_name or user.username or str(user.id)
    username = user.username or ""
    poll_id = answer.poll_id

    practice_key = get_poll_practice(poll_id, chat_id)

    if practice_key:
        from db import get_practice_info, get_practice_weight
        p_info = get_practice_info(practice_key)
        label = p_info.get("label") or p_info.get("title", practice_key)

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
            points = get_practice_weight(practice_key)
            reply = f"MashaAllah --- {full_name} --- {label} (+{points})"
        else:
            reply = f"InshaAllah next time --- {full_name} --- {label}"

        response_message = await context.bot.send_message(
            chat_id=chat_id,
            text=reply,
            parse_mode="Markdown",
        )
        schedule_message_delete(context.job_queue, response_message, f"poll_reply_{practice_key}")

    else:
        log.info(f"Received poll answer from {full_name} for untracked poll ID {poll_id}")


async def handle_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Issue a group-scoped setup link for the administrator."""
    from config import WEB_APP_URL, WEB_PORT
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in ("administrator", "creator"):
            await context.bot.send_message(chat.id, "Only Telegram group administrators can open the dashboard.")
            return
    except Exception:
        await context.bot.send_message(chat.id, "I could not verify your administrator status.")
        return
    register_group(chat.id, chat.title, getattr(chat, "username", None))
    token = issue_setup_token(chat.id, user.id)

    text = (
        "🌿 *Dhikr & Tahajjud Bot — Poll & Schedule Manager*\n\n"
        "Manage all present and new polls, fixed or prayer-relative times, "
        "and dispatch manual check-ins right from your mobile phone."
    )

    if not WEB_APP_URL:
        text += f"\n\n🔗 Dashboard is running on port `{WEB_PORT}`.\nSet `WEB_APP_URL` in `.env` to enable one-tap Telegram Mini App button!"
        await context.bot.send_message(chat.id, text=text, parse_mode="Markdown")
        return

    bot = await context.bot.get_me()
    if not bot.username:
        await context.bot.send_message(chat.id, "I could not determine my Telegram username. Please contact the bot administrator.")
        return

    private_link = f"https://t.me/{bot.username}?start=setup_{token}"
    reply_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Open private chat with bot", url=private_link)
    ]])
    text += (
        "\n\nTelegram only allows the dashboard Mini App button in a private chat. "
        "Tap the button below, then press Start in the private chat."
    )

    await context.bot.send_message(
        chat_id=chat.id,
        text=text,
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the Mini App from a group-issued setup token in private chat."""
    from config import WEB_APP_URL
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type != "private":
        return

    token = ""
    if context.args and context.args[0].startswith("setup_"):
        token = context.args[0][len("setup_"):]

    setup = get_setup_token(token, user.id) if token else None
    if not setup:
        await context.bot.send_message(
            chat.id,
            "Please use the dashboard link generated by /setup inside your Telegram group.",
        )
        return
    if not WEB_APP_URL:
        await context.bot.send_message(
            chat.id,
            "The dashboard URL is not configured yet. Please contact the bot administrator.",
        )
        return

    await context.bot.send_message(
        chat.id,
        "Your group setup link is verified. Tap below to open the group dashboard.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📱 Open Poll Dashboard",
                web_app=WebAppInfo(url=f"{WEB_APP_URL}?setup_token={token}"),
            )
        ]]),
    )


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.my_chat_member
    if not change or not change.chat or change.chat.type not in ("group", "supergroup"):
        return
    new_status = getattr(change.new_chat_member, "status", "")
    if new_status in ("member", "administrator"):
        register_group(change.chat.id, change.chat.title, getattr(change.chat, "username", None))
    elif new_status in ("left", "kicked"):
        register_group(change.chat.id, change.chat.title, getattr(change.chat, "username", None))
        from db import _sb
        try:
            _sb().table("groups").update({"is_active": False}).eq("chat_id", change.chat.id).execute()
        except Exception:
            pass

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
