"""
Telegram update handlers: processing poll answers and welcoming new
group members.
"""

from __future__ import annotations

import datetime as _dt
import html
from urllib.parse import quote

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import BD_TZ, log
from db import (
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

def _parse_ts(value) -> _dt.datetime | None:
    """Parse a Supabase timestamptz string; None if missing or invalid."""
    if not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else BD_TZ.localize(parsed)


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if answer is None or answer.user is None:
        return

    user = answer.user
    from db import get_active_poll
    full_name = user.full_name or user.username or str(user.id)
    username = user.username or ""
    poll_id = answer.poll_id
    poll = get_active_poll(poll_id)
    if not poll:
        log.info(f"Received poll answer from {full_name} for untracked poll ID {poll_id}")
        return
    chat_id = int(poll["group_chat_id"])
    set_current_group(chat_id)

    practice_key = poll.get("practice_key")

    if practice_key:
        from db import get_practice_info, get_practice_weight
        from messaging import amal_day_of, amal_day_end
        p_info = get_practice_info(practice_key)
        label = p_info.get("label") or p_info.get("title", practice_key)

        if not answer.option_ids:
            log.info(f"User {full_name} retracted vote for {label}")
            return

        # The answer belongs to the amal day the poll was sent in, and is
        # only accepted until that day ends (the poll is closed then; this
        # also covers polls whose close job was lost in a restart).
        now = _dt.datetime.now(BD_TZ)
        sent_at = _parse_ts(poll.get("created_at")) or now
        amal_day = await amal_day_of(sent_at)
        if now > await amal_day_end(amal_day):
            log.info(f"Ignored late answer from {full_name} for {label} (amal day {amal_day} has ended)")
            return

        did_it = 1 if 0 in answer.option_ids else 0
        save_response(user.id, username, full_name, practice_key, did_it, response_date=amal_day)

        # Streaks follow the calendar day the poll was sent, matching the
        # poll's configured weekdays.
        update_streak_for_response(
            user.id, practice_key, sent_at.astimezone(BD_TZ).date().isoformat(), bool(did_it)
        )

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
    # The link is bound to this admin: only they can redeem it.
    token = issue_setup_token(chat.id, user.id)
    await _send_setup_prompt(context.bot, chat.id, f"setup_{token}")


# Deep-link payload for the button posted when the bot joins a group. It
# is not bound to a user; handle_start checks that whoever opens it is an
# administrator of that group before issuing a personal setup token.
GROUP_SETUP_PREFIX = "admin_"

SETUP_PROMPT_TEXT = (
    "🌿 *Dhikr & Tahajjud Bot — Poll & Schedule Manager*\n\n"
    "Manage all present and new polls, fixed or prayer-relative times, "
    "and dispatch manual check-ins right from your mobile phone."
)


async def _send_setup_prompt(bot, chat_id: int, start_payload: str, intro: str = "") -> None:
    """Post the dashboard prompt with a button into the bot's private chat."""
    from config import WEB_APP_URL, WEB_PORT
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    text = intro + SETUP_PROMPT_TEXT
    if not WEB_APP_URL:
        text += f"\n\n🔗 Dashboard is running on port `{WEB_PORT}`.\nSet `WEB_APP_URL` in `.env` to enable one-tap Telegram Mini App button!"
        await bot.send_message(chat_id, text=text, parse_mode="Markdown")
        return

    me = await bot.get_me()
    if not me.username:
        await bot.send_message(chat_id, "I could not determine my Telegram username. Please contact the bot administrator.")
        return

    bot_username = me.username.lstrip("@").strip()
    private_link = (
        f"https://t.me/{quote(bot_username, safe='')}?"
        f"start={quote(start_payload, safe='')}"
    )
    reply_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Open private chat with bot", url=private_link)
    ]])
    text += (
        "\n\nOnly group administrators can open the dashboard. "
        "Telegram only allows the dashboard Mini App button in a private chat. "
        "Tap the button below, then press Start in the private chat."
    )

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    except BadRequest as exc:
        if "button_type_invalid" not in str(exc).lower():
            raise
        log.warning("Telegram rejected the setup URL button; sending a plain link instead.")
        await bot.send_message(
            chat_id=chat_id,
            text=f"{text}\n\nOpen this setup link:\n{private_link}",
            parse_mode="Markdown",
        )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the Mini App from a group-issued setup token in private chat."""
    from config import WEB_APP_URL
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type != "private":
        return

    payload = context.args[0] if context.args else ""
    token = ""
    if payload.startswith("setup_"):
        token = payload[len("setup_"):]
    elif payload.startswith(GROUP_SETUP_PREFIX):
        token = await _token_for_group_admin(context.bot, chat.id, user.id, payload[len(GROUP_SETUP_PREFIX):])
        if token is None:
            return  # the user has been told why

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


async def _token_for_group_admin(bot, private_chat_id: int, user_id: int, raw_group_id: str) -> str | None:
    """Issue a personal setup token if ``user_id`` administers the group.

    Used by the button posted when the bot joins a group: anyone can tap
    it, so the administrator check happens here. Returns None (after
    telling the user why) when access is refused.
    """
    from db import list_groups
    try:
        group_id = int(raw_group_id)
    except ValueError:
        group_id = 0
    group = next((g for g in list_groups() if int(g["chat_id"]) == group_id), None) if group_id else None
    if not group:
        await bot.send_message(
            private_chat_id,
            "This setup link is not valid. Please send /setup inside your Telegram group.",
        )
        return None
    title = group.get("title") or "this group"
    try:
        member = await bot.get_chat_member(group_id, user_id)
        is_admin = member.status in ("administrator", "creator")
    except Exception as exc:
        log.info("Admin check for user %s in group %s failed: %s", user_id, group_id, exc)
        is_admin = False
    if not is_admin:
        await bot.send_message(
            private_chat_id,
            f"Only administrators of {title} can open its dashboard.",
        )
        return None
    return issue_setup_token(group_id, user_id)


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.my_chat_member
    if not change or not change.chat or change.chat.type not in ("group", "supergroup"):
        return
    old_status = getattr(change.old_chat_member, "status", "")
    new_status = getattr(change.new_chat_member, "status", "")
    log.info("Bot status in group %s (%s): %s -> %s", change.chat.id, change.chat.title, old_status, new_status)
    if new_status in ("member", "administrator"):
        register_group(change.chat.id, change.chat.title, getattr(change.chat, "username", None))
        # Newly added (not e.g. promoted to admin): post the setup prompt
        # right away, as /setup would. Only administrators get through.
        if old_status in ("left", "kicked", ""):
            await _send_setup_prompt(
                context.bot,
                change.chat.id,
                f"{GROUP_SETUP_PREFIX}{change.chat.id}",
                intro="Assalamu Alaikum! Thank you for adding me to this group.\n\n",
            )
    elif new_status in ("left", "kicked"):
        from db import deactivate_group
        register_group(change.chat.id, change.chat.title, getattr(change.chat, "username", None))
        deactivate_group(change.chat.id, f"bot status changed to {new_status}")

async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log handler/job failures with enough context to trace them."""
    where = "job" if context.job else "update"
    detail = ""
    if isinstance(update, Update):
        if update.poll_answer:
            detail = f" poll_id={update.poll_answer.poll_id}"
        elif update.effective_chat:
            detail = f" chat_id={update.effective_chat.id}"
    elif context.job:
        detail = f" job={context.job.name}"
    log.error("Unhandled error in %s%s", where, detail, exc_info=context.error)


_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _group_amal_lines(chat_id: int) -> list[str]:
    """Bullet lines for this group's active amal polls, e.g. "• Sawm (Mon, Thu)"."""
    from db import get_all_poll_configs
    try:
        configs = get_all_poll_configs(active_only=True, group_chat_id=chat_id)
    except Exception as exc:
        log.warning("Could not load amal list for group %s: %s", chat_id, exc)
        return []
    lines = []
    for conf in configs:
        title = html.escape(str(conf.get("title") or conf["id"]))
        days = sorted(set(conf.get("days_of_week") or range(7)))
        if len(days) < 7:
            title += " (" + ", ".join(_WEEKDAY_NAMES[d] for d in days if 0 <= d < 7) + ")"
        lines.append(f"• {title}\n")
    return lines


async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message is None or not message.new_chat_members:
        return

    amal_lines = _group_amal_lines(message.chat_id)

    for member in message.new_chat_members:
        if member.is_bot:
            continue

        display_name = html.escape(member.full_name or member.username or "brother")
        welcome_text = (
            f"Assalamu Alaikum wa Rahmatullahi wa Barakatuh, <b>{display_name}</b>\n\n"
            "Welcome to our little circle of remembrance and accountability. May Allah make your stay here beneficial, easy, and full of barakah.\n\n"
        )
        if amal_lines:
            welcome_text += "Here is the amal we follow together:\n" + "".join(amal_lines)
        else:
            welcome_text += "Our group's amal list will be shared here soon, InshaAllah.\n"

        welcome_text += (
            "\nWe ask Allah to accept every effort, even the small ones, and to keep our hearts firm upon goodness. Ameen."
        )

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=welcome_text,
            parse_mode="HTML",
        )
