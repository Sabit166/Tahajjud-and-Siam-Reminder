# ============================================================
#  DHIKR & TAHAJJUD TELEGRAM BOT
#  Built for your Islamic accountability group
# ============================================================
#
#  SETUP — fill in these 2 values before running:
#
#    TOKEN       : get this from @BotFather on Telegram
#    GROUP_CHAT_ID: your group's ID (negative number, e.g. -1001234567890)
#                  To find it: add @userinfobot to your group,
#                  it will post the ID automatically, then remove it.
#
# ============================================================

import logging
import sqlite3
import datetime
from pathlib import Path
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
)
import pytz
import os
from dotenv import load_dotenv

# ============================================================
#  YOUR SETTINGS — EDIT THESE TWO LINES
# ============================================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "dhikr_records.db")
BD_TZ = pytz.timezone(os.getenv("BD_TZ", "Asia/Dhaka"))

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
    "ishraq_salat":   {"label": "Ishraq Salat",    "emoji": "☀️", "arabic": "صلاة الإشراق"},
    "evening_dhikr":  {"label": "Evening Adhkar",  "emoji": "🌆", "arabic": "الأذكار المسائية"},
    "tahajjud":       {"label": "Tahajjud Salat",  "emoji": "🌙", "arabic": "صلاة التهجد"},
}

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

async def send_checkin(bot: Bot, practice_key: str):
    p = PRACTICES[practice_key]
    text = (
        f"{p['emoji']}  *{p['label']}*  |  {p['arabic']}\n\n"
        f"Assalamu Alaikum brothers! Did you complete your *{p['label']}* today?\n\n"
        f"Tap a button below to record your response. "
        f"Your answer is saved to the weekly tracker. 📊"
    )
    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=make_checkin_keyboard(practice_key)
    )
    log.info(f"Sent check-in: {p['label']}")


async def send_tahajjud_alert(bot: Bot):
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
    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=make_checkin_keyboard("tahajjud")
    )
    log.info("Sent tahajjud alert.")


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


async def send_checkin_job(context: ContextTypes.DEFAULT_TYPE):
    await send_checkin(context.bot, context.job.data)


async def send_tahajjud_job(context: ContextTypes.DEFAULT_TYPE):
    await send_tahajjud_alert(context.bot)


async def send_weekly_report_job(context: ContextTypes.DEFAULT_TYPE):
    await send_weekly_report(context.bot)

# ============================================================
#  BUTTON PRESS HANDLER
#  Runs when a member taps Yes or No on any check-in message.
# ============================================================

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    user     = query.from_user
    data     = query.data   # e.g. "yes_morning_dhikr" or "no_ishraq_salat"

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
        reply = f"✅ Jazakallahu Khayran, *{full_name}*! Your *{label}* has been recorded. {emoji}\nMay Allah accept it from you. 🤲"
    else:
        reply = f"📝 Noted, *{full_name}*. Don't worry — there is still time! May Allah make it easy for you. 💪"

    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=reply,
        parse_mode="Markdown"
    )

# ============================================================
#  SCHEDULER — sets up all timed jobs
#
#  TIMES ARE IN BANGLADESH TIME (Asia/Dhaka, UTC+6)
#  Adjust the hour/minute values below to your preference.
#
#  Current schedule:
#    Morning adhkar  → 6:30 AM  daily
#    Ishraq salat    → 7:30 AM  daily
#    Evening adhkar  → 5:30 PM  daily
#    Tahajjud alert  → 3:30 AM  Friday & Saturday nights only
#    Weekly report   → 9:00 AM  every Friday
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

    # Ishraq salat — 7:30 AM every day
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
        time=datetime.time(hour=17, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="evening_dhikr",
        name="evening_dhikr",
    )

    # Tahajjud alert — 3:30 AM on Friday & Saturday nights
    # (In Python/Telegram JobQueue, Monday=0 so Friday=4 and Saturday=5)
    job_queue.run_daily(
        send_tahajjud_job,
        time=datetime.time(hour=3, minute=30, tzinfo=BD_TZ),
        days=(4, 5),
        name="tahajjud",
    )

    # Weekly report — every Friday at 9:00 AM
    job_queue.run_daily(
        send_weekly_report_job,
        time=datetime.time(hour=9, minute=0, tzinfo=BD_TZ),
        days=(4,),
        name="weekly_report",
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

    # Setup scheduler
    setup_scheduler(app)

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()