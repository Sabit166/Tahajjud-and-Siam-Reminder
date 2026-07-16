# ============================================================
#  DHIKR & TAHAJJUD TELEGRAM BOT
#  Built for your Islamic accountability group
# ============================================================
#
#  SETUP — fill in these 2 values before running:
#
#    BOT_TOKEN       : get this from @BotFather on Telegram
#    GROUP_CHAT_ID: your group's ID (negative number, e.g. -1001234567890)
#                  To find it: add @userinfobot to your group,
#                  it will post the ID automatically, then remove it.
#
# ============================================================

import logging
import importlib
import sqlite3
import datetime
from pathlib import Path
from typing import Any, cast
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    PollAnswerHandler,
)
import pytz
import os

try:
    load_dotenv = importlib.import_module("dotenv").load_dotenv
except ImportError:
    def load_dotenv() -> None:
        env_path = Path(".env")
        if not env_path.exists():
            return

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# ============================================================
#  YOUR SETTINGS — EDIT THESE TWO LINES
# ============================================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "dhikr_records.db")
BD_TZ = pytz.timezone(os.getenv("BD_TZ", "Asia/Dhaka"))
RESPONSE_WINDOW_HOURS = int(os.getenv("RESPONSE_WINDOW_HOURS", "24"))
RESPONSE_DELETE_AFTER_MINUTES = int(os.getenv("RESPONSE_DELETE_AFTER_MINUTES", "10"))

# ============================================================
#  TIMEZONE — Bangladesh Standard Time (UTC+6)
# ============================================================


# ============================================================
#  LOGGING (shows activity in your terminal)
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ============================================================
#  DATABASE SETUP
#  Creates a file called dhikr_records.db in the same folder.
#  All responses are stored here automatically.
# ============================================================

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS responses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT,
            full_name   TEXT,
            practice    TEXT NOT NULL,
            did_it      INTEGER NOT NULL,   -- 1 = Yes, 0 = No
            date        TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    log.info("Database ready.")

def save_response(user_id, username, full_name, practice, did_it):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.datetime.now(BD_TZ).strftime("%Y-%m-%d")
    now   = datetime.datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # Prevent duplicate entries for same user + practice + day
    c.execute("""
        SELECT id FROM responses
        WHERE user_id=? AND practice=? AND date=?
    """, (user_id, practice, today))
    existing = c.fetchone()

    if existing:
        c.execute("""
            UPDATE responses SET did_it=?, recorded_at=?
            WHERE user_id=? AND practice=? AND date=?
        """, (did_it, now, user_id, practice, today))
        log.info(f"Updated response: {full_name} | {practice} | did_it={did_it}")
    else:
        c.execute("""
            INSERT INTO responses (user_id, username, full_name, practice, did_it, date, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, full_name, practice, did_it, today, now))
        log.info(f"New response: {full_name} | {practice} | did_it={did_it}")

    conn.commit()
    conn.close()

def get_weekly_summary():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Get the last 7 days
    today = datetime.datetime.now(BD_TZ).date()
    week_ago = today - datetime.timedelta(days=6)

    c.execute("""
        SELECT full_name, practice, SUM(did_it) as completed, COUNT(*) as total
        FROM responses
        WHERE date BETWEEN ? AND ?
        GROUP BY user_id, practice
        ORDER BY full_name, practice
    """, (week_ago.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")))

    rows = c.fetchall()
    conn.close()
    return rows

# ============================================================
#  CHECK-IN MESSAGES
#  Each practice has its own emoji, label, and callback code.
# ============================================================

PRACTICES = {
    "morning_dhikr":  {"label": "Morning Adhkar",  "emoji": "🌅", "arabic": "الأذكار الصباحية"},
    "ishraq_salat":   {"label": "Fazr Salat",      "emoji": "☀️", "arabic": "صلاة الفجر"},
    "evening_dhikr":  {"label": "Evening Adhkar",  "emoji": "🌆", "arabic": "الأذكار المسائية"},
    "salawat_on_rasulullah": {"label": "Salawat on Rasulullah", "emoji": "🤍", "arabic": "الصلاة على رسول الله"},
    "sawm":           {"label": "Sawm",           "emoji": "🌙", "arabic": "الصيام"},
    "surah_kahf":     {"label": "Surah Kahf",      "emoji": "📖", "arabic": "سورة الكهف"},
    "tahajjud":       {"label": "Tahajjud Salat",  "emoji": "🌙", "arabic": "صلاة التهجد"},
    "nightly_amal":   {"label": "Nightly Amal",    "emoji": "🌙", "arabic": "الأعمال الليلية"},
    "nightly_al_mulk": {"label": "Surat Al-Mulk", "emoji": "📖", "arabic": "Nightly Amal"},
    "nightly_as_sajdah": {"label": "Surat As-Sajdah", "emoji": "📖", "arabic": "Nightly Amal"},
    "nightly_al_baqarah_last_2": {"label": "Surat Al-Baqarah (Last 2 ayats)", "emoji": "📖", "arabic": "Nightly Amal"},
    "nightly_33_tasbeeh": {"label": "33x SubhanAllah, 33x Alhamdulillah, 34x AllahuAkbar", "emoji": "📿", "arabic": "Nightly Amal"},
}

NIGHTLY_AMAL_OPTIONS = [
    "nightly_al_mulk",
    "nightly_as_sajdah",
    "nightly_al_baqarah_last_2",
    "nightly_33_tasbeeh",
]

def make_checkin_keyboard(practice_key):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅  Yes, alhamdulillah", callback_data=f"yes_{practice_key}"),
            InlineKeyboardButton("❌  Not yet",            callback_data=f"no_{practice_key}"),
        ]
    ])

# ============================================================
#  SCHEDULED MESSAGE SENDERS
# ============================================================

async def send_checkin(bot: Bot, practice_key: str, job_queue=None):
    p = PRACTICES[practice_key]
    text = (
        f"{p['emoji']}  *{p['label']}*  |  {p['arabic']}\n\n"
        f"Assalamu Alaikum brothers! Did you complete your *{p['label']}* today?\n\n"
        f"Tap a button below to record your response. "
        f"Your answer is saved to the weekly tracker. 📊"
    )
    sent_message = await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=make_checkin_keyboard(practice_key)
    )
    if RESPONSE_WINDOW_HOURS > 0:
        log.info(f"Check-in for {p['label']} will stay open for {RESPONSE_WINDOW_HOURS} hour(s).")
        schedule_reminder_close(job_queue, sent_message, p['label'])
    log.info(f"Sent check-in: {p['label']}")


async def send_tahajjud_alert(bot: Bot, job_queue=None):
    text = (
        "🌙  *Tahajjud Time!*  |  وقت التهجد\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Brothers, it is time for *Tahajjud* prayer! 🤲\n\n"
        "Rise, make wudu, and stand before Allah in the blessed last third of the night.\n\n"
        "_\"The Lord descends every night to the lowest heaven when one-third of the night remains "
        "and says: 'Who will call upon Me so that I may answer? Who will ask of Me so that I may give? "
        "Who will seek My forgiveness so that I may forgive?'\"_\n"
        "*(Bukhari & Muslim)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "After praying, come back and mark your attendance below 👇"
    )
    sent_message = await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=make_checkin_keyboard("tahajjud")
    )
    if RESPONSE_WINDOW_HOURS > 0:
        log.info(f"Tahajjud alert will stay open for {RESPONSE_WINDOW_HOURS} hour(s).")
        schedule_reminder_close(job_queue, sent_message, "tahajjud")
    log.info("Sent tahajjud alert.")


async def send_nightly_amal(bot: Bot, job_queue=None):
    options = [PRACTICES[key]["label"] for key in NIGHTLY_AMAL_OPTIONS]
    sent_message = await bot.send_poll(
        chat_id=GROUP_CHAT_ID,
        question="🌙 Nightly Amal - fill your night with Barakah!",
        options=options,
        is_anonymous=False,
        allows_multiple_answers=True,
    )
    if RESPONSE_WINDOW_HOURS > 0:
        log.info(f"Nightly Amal poll will stay open for {RESPONSE_WINDOW_HOURS} hour(s).")
        schedule_poll_close(job_queue, sent_message, "Nightly Amal")
    log.info("Sent Nightly Amal poll.")


async def send_weekly_report(bot: Bot):
    rows = get_weekly_summary()

    if not rows:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="📊 *Weekly Report*\n\nNo responses recorded this week yet.",
            parse_mode="Markdown"
        )
        return

    # Build report text
    report = "📊  *Weekly Dhikr & Salat Report*\n"
    report += f"_{datetime.datetime.now(BD_TZ).strftime('%d %B %Y')}_\n"
    report += "\n*Al-Quran 17:14*\n"
    report += "ٱقْرَأْ كِتَٰبَكَ كَفَىٰ بِنَفْسِكَ ٱلْيَوْمَ عَلَيْكَ حَسِيبًا\n"
    report += "_Read! You yourself are sufficient as an accountant on yourself_\n"
    report += "━━━━━━━━━━━━━━━━━━━━\n\n"

    current_name = None
    for full_name, practice, completed, total in rows:
        if full_name != current_name:
            if current_name is not None:
                report += "\n"
            report += f"👤 *{full_name}*\n"
            current_name = full_name

        p_info = PRACTICES.get(practice, {})
        emoji  = p_info.get("emoji", "•")
        label  = p_info.get("label", practice)
        bar    = "🟩" * completed + "⬜" * (total - completed)
        report += f"  {emoji} {label}: {bar} {completed}/{total}\n"

    report += "\n━━━━━━━━━━━━━━━━━━━━\n"
    report += "May Allah accept from all of us. آمين 🤲"

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=report,
        parse_mode="Markdown"
    )
    log.info("Sent weekly report.")


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


async def send_checkin_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    await send_checkin(context.bot, cast(str, job.data), context.job_queue)


async def send_tahajjud_job(context: ContextTypes.DEFAULT_TYPE):
    await send_tahajjud_alert(context.bot, context.job_queue)


async def send_nightly_amal_job(context: ContextTypes.DEFAULT_TYPE):
    await send_nightly_amal(context.bot, context.job_queue)


async def send_weekly_report_job(context: ContextTypes.DEFAULT_TYPE):
    await send_weekly_report(context.bot)


async def close_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    message = cast(dict[str, Any], job.data)
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=message["chat_id"],
            message_id=message["message_id"],
            reply_markup=None,
        )
        log.info(f"Closed reminder: {message.get('label', 'unknown')}")
    except Exception as exc:
        log.warning(f"Could not close reminder {message.get('label', 'unknown')}: {exc}")


async def close_poll_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if job is None:
        return
    message = cast(dict[str, Any], job.data)
    try:
        await context.bot.stop_poll(
            chat_id=message["chat_id"],
            message_id=message["message_id"],
        )

        today = datetime.datetime.now(BD_TZ).strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            """
            SELECT COUNT(*)
            FROM responses
            WHERE practice LIKE ? AND date=?
            """,
            ("nightly_%", today),
        )
        response_count = c.fetchone()[0]
        conn.close()

        if response_count == 0:
            save_response(0, "", "Nightly Amal", "nightly_amal", 0)
            missed_message = await context.bot.send_message(
                chat_id=message["chat_id"],
                text="📝 Nightly Amal was missed tonight. Nightly Amal fills your night and sleep with Barakah. Try not to miss it again! 🌙",
            )
            schedule_message_delete(context.job_queue, missed_message, "nightly_amal_missed")

        log.info(f"Closed poll: {message.get('label', 'unknown')}")
    except Exception as exc:
        log.warning(f"Could not close poll {message.get('label', 'unknown')}: {exc}")


def schedule_reminder_close(job_queue, sent_message, label):
    if job_queue is None or RESPONSE_WINDOW_HOURS <= 0:
        return

    job_queue.run_once(
        close_reminder_job,
        when=datetime.timedelta(hours=RESPONSE_WINDOW_HOURS),
        data={
            "chat_id": sent_message.chat_id,
            "message_id": sent_message.message_id,
            "label": label,
        },
        name=f"close_{label}_{sent_message.message_id}",
    )


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
    if job_queue is None or RESPONSE_DELETE_AFTER_MINUTES <= 0:
        return

    job_queue.run_once(
        delete_message_job,
        when=datetime.timedelta(minutes=RESPONSE_DELETE_AFTER_MINUTES),
        data={
            "chat_id": sent_message.chat_id,
            "message_id": sent_message.message_id,
            "label": label,
        },
        name=f"delete_{label}_{sent_message.message_id}",
    )

# ============================================================
#  BUTTON PRESS HANDLER
#  Runs when a member taps Yes or No on any check-in message.
# ============================================================

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    if query is None:
        return
    user     = query.from_user
    if user is None or query.data is None:
        return
    data     = cast(str, query.data)   # e.g. "yes_morning_dhikr" or "no_fazr_salat"

    await query.answer()    # removes the loading spinner on the button

    parts    = data.split("_", 1)
    response = parts[0]           # "yes" or "no"
    practice = parts[1]           # e.g. "morning_dhikr"
    did_it   = 1 if response == "yes" else 0

    full_name = user.full_name or user.username or str(user.id)
    username  = user.username or ""

    save_response(user.id, username, full_name, practice, did_it)

    p_info = PRACTICES.get(practice, {})
    label  = p_info.get("label", practice)
    emoji  = p_info.get("emoji", "")

    if did_it:
        reply = f"✅ Jazakallahu Khayran, *{full_name}*! Your *{label}* has been recorded. {emoji}\nA small deed with pure intentions bear huge weight before Allah. May Allah accept it from you. 🤲"
    else:
        reply = f"📝 Noted, *{full_name}*. You missed *{label}* this time, but don't let this refrain you from other acts of worship! May Allah make it easy for you. 💪"

    response_message = await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=reply,
        parse_mode="Markdown",
    )
    schedule_message_delete(context.job_queue, response_message, f"button_reply_{practice}")


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if answer is None:
        return
    user = answer.user
    if user is None:
        return

    full_name = user.full_name or user.username or str(user.id)
    username = user.username or ""
    selected_options = [NIGHTLY_AMAL_OPTIONS[index] for index in answer.option_ids]

    for practice_key in selected_options:
        save_response(user.id, username, full_name, practice_key, 1)

    labels = [PRACTICES[key]["label"] for key in selected_options if key in PRACTICES]
    if labels:
        response_message = await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=(
                f"✅ Jazakallahu Khayran, *{full_name}*!\n"
                f"Recorded: {', '.join(labels)}"
            ),
            parse_mode="Markdown",
        )
        schedule_message_delete(context.job_queue, response_message, "nightly_amal_reply")

# ============================================================
#  SCHEDULER — sets up all timed jobs
#
#  TIMES ARE IN BANGLADESH TIME (Asia/Dhaka, UTC+6)
#  Adjust the hour/minute values below to your preference.
#
#  Current schedule:
#    Morning adhkar  → 6:30 AM  daily
#    Fazr salat      → 7:30 AM  daily
#    Evening adhkar  → 5:30 PM  daily
#    Tahajjud alert  → 3:30 AM  Friday & Saturday nights only
#    Weekly report   → 9:00 AM  every Friday
#    0 -> 6 : Sunday -> Saturday
# ============================================================

def setup_scheduler(app: Application):
    job_queue = app.job_queue

    if job_queue is None:
        raise RuntimeError(
            "JobQueue is not available. Install python-telegram-bot with job-queue support."
        )

    # Morning adhkar — 6:30 AM every day
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=6, minute=30, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="morning_dhikr",
        name="morning_dhikr",
    )

    # Fazr salat — 7:30 AM every day
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=7, minute=30, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="ishraq_salat",
        name="ishraq_salat",
    )

    # Evening adhkar — 5:30 PM every day
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=18, minute=40, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="evening_dhikr",
        name="evening_dhikr",
    )

    # Salawat on Rasulullah — 7:00 PM every day
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=19, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="salawat_on_rasulullah",
        name="salawat_on_rasulullah",
    )

    # Sawm — 4:00 AM on Monday and Thursday
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=4, minute=0, tzinfo=BD_TZ),
        days=(1, 4),
        data="sawm",
        name="sawm",
    )

    # Surah Kahf — 9:00 AM on Friday
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=9, minute=0, tzinfo=BD_TZ),
        days=(4,),
        data="surah_kahf",
        name="surah_kahf",
    )

    # Tahajjud alert — 3:30 AM on Friday & Saturday nights
    # (In Python/Telegram JobQueue, Monday=1 so Friday=5 and Saturday=6)
    job_queue.run_daily(
        send_tahajjud_job,
        time=datetime.time(hour=3, minute=00, tzinfo=BD_TZ),
        days=(1, 4, 5, 6),
        name="tahajjud",
    )

    # Weekly report — every Friday at 9:00 AM
    job_queue.run_daily(
        send_weekly_report_job,
        time=datetime.time(hour=9, minute=0, tzinfo=BD_TZ),
        days=(5,),
        name="weekly_report",
    )

    # Nightly Amal — 10:30 PM every day
    job_queue.run_daily(
        send_nightly_amal_job,
        time=datetime.time(hour=22, minute=00, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="nightly_amal",
    )

    log.info("Scheduler started. All jobs are active.")
    return job_queue

# ============================================================
#  MAIN — starts everything
# ============================================================

def main():
    # Safety check
    if not TOKEN:
        print("\n❌  ERROR: You forgot to paste your bot token!")
        print("   Set BOT_TOKEN in your environment or .env file.\n")
        return

    if GROUP_CHAT_ID == 0:
        print("\n❌  ERROR: You forgot to set your GROUP_CHAT_ID!")
        print("   Set GROUP_CHAT_ID in your environment or .env file.\n")
        return

    # Initialize database
    init_db()

    # Build the bot application
    app = Application.builder().token(TOKEN).build()

    # Register button handler
    app.add_handler(CallbackQueryHandler(handle_button))

    # Register poll answer handler
    app.add_handler(PollAnswerHandler(handle_poll_answer))

    # Setup scheduler
    setup_scheduler(app)

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()