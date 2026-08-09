# ============================================================
#  DHIKR & TAHAJJUD TELEGRAM BOT
#  Built for your Islamic accountability group
# ============================================================

import os
import logging
import asyncio
import sqlite3
import datetime
from pathlib import Path
from typing import Any, Optional, cast
from contextlib import contextmanager

import pytz
from telegram import Bot, Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_path = Path(".env")
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# ============================================================
#  SETTINGS & CONFIGURATION
# ============================================================

TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "dhikr_records.db")
BD_TZ = pytz.timezone(os.getenv("BD_TZ", "Asia/Dhaka"))

RESPONSE_WINDOW_HOURS = 24
RESPONSE_DELETE_AFTER_SECONDS = 10
DAILY_REPORT_HOUR = 18
DAILY_REPORT_MINUTE = 30

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ============================================================
#  DATABASE HELPERS
# ============================================================

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS responses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                username    TEXT,
                full_name   TEXT,
                practice    TEXT NOT NULL,
                did_it      INTEGER NOT NULL,
                date        TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS active_polls (
                poll_id      TEXT PRIMARY KEY,
                practice_key TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
        """)
        conn.commit()
    log.info("Database ready.")

def delete_active_poll(poll_id: str):
    ACTIVE_POLLS.pop(poll_id, None)
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM active_polls WHERE poll_id=?", (poll_id,))
            conn.commit()
    except Exception as exc:
        log.warning(f"Could not delete active poll {poll_id}: {exc}")

def cleanup_old_active_polls(hours: int = 24):
    try:
        cutoff = (datetime.datetime.now(BD_TZ) - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM active_polls WHERE created_at < ?", (cutoff,))
            deleted = c.rowcount
            conn.commit()
        if deleted > 0:
            log.info(f"Cleaned up {deleted} old active poll(s).")
    except Exception as exc:
        log.warning(f"Could not cleanup old active polls: {exc}")

init_db()
cleanup_old_active_polls(24)

ACTIVE_POLLS: dict[str, str] = {}

def save_active_poll(poll_id: str, practice_key: str):
    ACTIVE_POLLS[poll_id] = practice_key
    try:
        with get_db() as conn:
            c = conn.cursor()
            now = datetime.datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("""
                INSERT OR REPLACE INTO active_polls (poll_id, practice_key, created_at)
                VALUES (?, ?, ?)
            """, (poll_id, practice_key, now))
            conn.commit()
    except Exception as exc:
        log.warning(f"Could not save active poll {poll_id}: {exc}")

def get_poll_practice(poll_id: str) -> Optional[str]:
    if poll_id in ACTIVE_POLLS:
        return ACTIVE_POLLS[poll_id]
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT practice_key FROM active_polls WHERE poll_id=?", (poll_id,))
            row = c.fetchone()
            if row:
                ACTIVE_POLLS[poll_id] = row[0]
                return row[0]
    except Exception as exc:
        log.warning(f"Could not read active poll {poll_id}: {exc}")
    return None

def save_response(user_id: int, username: str, full_name: str, practice: str, did_it: int):
    today = datetime.datetime.now(BD_TZ).strftime("%Y-%m-%d")
    now = datetime.datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        c = conn.cursor()
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

def get_weekly_summary():
    today = datetime.datetime.now(BD_TZ).date()
    week_ago = today - datetime.timedelta(days=6)

    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT full_name, practice, SUM(did_it) as completed, COUNT(*) as total
            FROM responses
            WHERE date BETWEEN ? AND ?
            GROUP BY user_id, practice
            ORDER BY full_name, practice
        """, (week_ago.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")))
        rows = c.fetchall()
    return rows

def get_daily_summary():
    now = datetime.datetime.now(BD_TZ)
    report_end = now.replace(
        hour=DAILY_REPORT_HOUR,
        minute=DAILY_REPORT_MINUTE,
        second=0,
        microsecond=0,
    )
    if now < report_end:
        report_end -= datetime.timedelta(days=1)
    report_start = report_end - datetime.timedelta(days=1)

    scheduled_practices = [
        "evening_dhikr",
        "salawat_on_rasulullah",
        "nightly_al_mulk",
        "nightly_as_sajdah",
        "nightly_al_baqarah_last_2",
        "nightly_33_tasbeeh",
    ]
    if report_start.weekday() == 3:
        scheduled_practices.append("surah_kahf")

    scheduled_practices.extend(["tahajjud", "morning_dhikr", "fazr_jamaat", "ishraq_salat", "salatud_duha"])
    if report_end.weekday() in (0, 3):
        scheduled_practices.append("sawm")

    placeholders = ",".join("?" for _ in scheduled_practices)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT full_name, practice, did_it
            FROM responses
            WHERE recorded_at > ? AND recorded_at <= ? AND practice IN ({placeholders})
            ORDER BY full_name, practice
            """,
            (
                report_start.strftime("%Y-%m-%d %H:%M:%S"),
                report_end.strftime("%Y-%m-%d %H:%M:%S"),
                *scheduled_practices,
            ),
        )
        rows = c.fetchall()

    summary: dict[str, dict[str, int]] = {}
    for full_name, practice, did_it in rows:
        summary.setdefault(full_name, {})[practice] = did_it

    return scheduled_practices, summary

# ============================================================
#  PRACTICE DEFINITIONS
# ============================================================

PRACTICES = {
    "morning_dhikr":  {"label": "Morning Adhkar",  "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"]},
    "fazr_jamaat":   {"label": "Fazr Jamaat",      "poll_options": ["Alhamdulillah, done", "Missed Jamaat"]},
    "ishraq_salat":   {"label": "Ishraq Salat",      "poll_options": ["Alhamdulillah, done", "Missed"]},
    "salatud_duha":   {"label": "Salatud Duha",      "poll_options": ["Alhamdulillah, done", "Missed"]},
    "evening_dhikr":  {"label": "Evening Adhkar",  "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"]},
    "salawat_on_rasulullah": {"label": "Salawat on Rasulullah", "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"]},
    "sawm":           {"label": "Sawm",           "poll_options": ["Alhamdulillah, fasting", "InshaAllah, next time"]},
    "surah_kahf":     {"label": "Surah Kahf",      "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"]},
    "tahajjud":       {"label": "Tahajjud Salat",  "poll_options": ["Alhamdulillah, done", "InshaAllah, next time"]},
    "nightly_al_mulk": {"label": "Surat Al-Mulk", "poll_options": ["Alhamdulillah, done", "Missed"]},
    "nightly_as_sajdah": {"label": "Surat As-Sajdah", "poll_options": ["Alhamdulillah, done", "Missed"]},
    "nightly_al_baqarah_last_2": {"label": "Surat Al-Baqarah (Last 2 ayats)", "poll_options": ["Alhamdulillah, done", "Missed"]},
    "nightly_33_tasbeeh": {"label": "33x SubhanAllah, 33x Alhamdulillah, 34x AllahuAkbar", "poll_options": ["Alhamdulillah, done", "Missed"]},
}

NIGHTLY_AMAL_OPTIONS = [
    "nightly_al_mulk",
    "nightly_as_sajdah",
    "nightly_al_baqarah_last_2",
    "nightly_33_tasbeeh",
]

GROUP_AMAL_LABELS = [
    "Morning Adhkar",
    "Fazr Jamaat",
    "Ishraq Salat",
    "Salatud Duha",
    "Evening Adhkar",
    "Salawat on Rasulullah",
    "Sawm",
    "Surah Kahf",
    "Tahajjud Salat",
    "Nightly Amal",
    "Surat Al-Mulk",
    "Surat As-Sajdah",
    "Surat Al-Baqarah (Last 2 ayats)",
    "33x SubhanAllah, 33x Alhamdulillah, 34x AllahuAkbar",
]

# ============================================================
#  MESSAGE SENDERS & POLL CLOSING
# ============================================================

async def send_checkin(bot: Bot, practice_key: str, job_queue=None):
    p = PRACTICES[practice_key]
    question = p["label"]
    sent_message = await bot.send_poll(
        chat_id=GROUP_CHAT_ID,
        question=question,
        options=p["poll_options"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    if sent_message.poll:
        save_active_poll(sent_message.poll.id, practice_key)

    if RESPONSE_WINDOW_HOURS > 0:
        log.info(f"Check-in poll for {p['label']} will stay open for {RESPONSE_WINDOW_HOURS} hour(s).")
        schedule_poll_close(job_queue, sent_message, p['label'])
    log.info(f"Sent check-in poll: {p['label']}")

async def send_nightly_amal(bot: Bot, job_queue=None):
    for key in NIGHTLY_AMAL_OPTIONS:
        await send_checkin(bot, key, job_queue)
        await asyncio.sleep(1)
    log.info("Sent all 4 Nightly Amal polls.")

async def send_weekly_report(bot: Bot):
    rows = get_weekly_summary()
    if not rows:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Weekly Report:\n━━━━━━━━━━━━━━━━━━━━\nNo responses recorded this week yet.",
            parse_mode="Markdown"
        )
        return

    report = "Weekly Report:\n━━━━━━━━━━━━━━━━━━━━\n"

    current_name = None
    for full_name, practice, completed, total in rows:
        if full_name != current_name:
            if current_name is not None:
                report += "━━━━━━━━━━━━━━━━━━━━\n"
            report += f"*{full_name}*\n"
            current_name = full_name

        p_info = PRACTICES.get(practice, {})
        label = p_info.get("label", practice)
        bar = "🟩" * completed + "⬜" * (total - completed)
        report += f"  -- {label}: {bar} {completed}/{total}\n"

    report += "━━━━━━━━━━━━━━━━━━━━"

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=report,
        parse_mode="Markdown"
    )
    log.info("Sent weekly report.")

async def send_daily_report(bot: Bot):
    scheduled_practices, summary = get_daily_summary()

    if not summary:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Daily Report:\n━━━━━━━━━━━━━━━━━━━━\nNo responses recorded today yet.",
            parse_mode="Markdown"
        )
        return

    report = "Daily Report:\n━━━━━━━━━━━━━━━━━━━━\n"

    for full_name in sorted(summary):
        report += f"*{full_name}*\n"
        for practice in scheduled_practices:
            p_info = PRACTICES.get(practice, {})
            label = p_info.get("label", practice)
            did_it = summary[full_name].get(practice, 0)
            status = "Done" if did_it else "Missed"
            report += f"  -- {label}: {status}\n"
        report += "━━━━━━━━━━━━━━━━━━━━\n"

    report = report.rstrip("\n")

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=report,
        parse_mode="Markdown"
    )
    log.info("Sent daily report.")

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

async def send_nightly_amal_job(context: ContextTypes.DEFAULT_TYPE):
    await send_nightly_amal(context.bot, context.job_queue)

async def send_weekly_report_job(context: ContextTypes.DEFAULT_TYPE):
    await send_weekly_report(context.bot)

async def send_daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    await send_daily_report(context.bot)

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

# ============================================================
#  SCHEDULER SETUP
# ============================================================

def setup_scheduler(app: Application):
    job_queue = app.job_queue
    if job_queue is None:
        raise RuntimeError("JobQueue is not available.")

    # Daily practice polls
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=5, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="morning_dhikr",
        name="morning_dhikr",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=5, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="fazr_jamaat",
        name="fazr_jamaat",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=5, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="ishraq_salat",
        name="ishraq_salat",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=10, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="salatud_duha",
        name="salatud_duha",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=19, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="salawat_on_rasulullah",
        name="salawat_on_rasulullah",
    )
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=19, minute=30, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="evening_dhikr",
        name="evening_dhikr",
    )

    # Fasting (Sawm) - Mondays & Thursdays at 4:00 AM
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=4, minute=0, tzinfo=BD_TZ),
        days=(1, 4),
        data="sawm",
        name="sawm",
    )

    # Surah Kahf - Thursdays at 7:00 PM
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=19, minute=0, tzinfo=BD_TZ),
        days=(4,),
        data="surah_kahf",
        name="surah_kahf",
    )

    # Tahajjud - Daily at 3:00 AM
    job_queue.run_daily(
        send_checkin_job,
        time=datetime.time(hour=3, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        data="tahajjud",
        name="tahajjud",
    )

    # Daily Report - Daily at 6:30 PM
    job_queue.run_daily(
        send_daily_report_job,
        time=datetime.time(hour=18, minute=30, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="daily_report",
    )

    # Weekly Report - Fridays at 6:30 PM
    job_queue.run_daily(
        send_weekly_report_job,
        time=datetime.time(hour=18, minute=30, tzinfo=BD_TZ),
        days=(5,),
        name="weekly_report",
    )

    # Nightly Amal - Daily at 10:00 PM
    job_queue.run_daily(
        send_nightly_amal_job,
        time=datetime.time(hour=22, minute=0, tzinfo=BD_TZ),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="nightly_amal",
    )

    log.info("Scheduler started. All jobs are active.")
    return job_queue

# ============================================================
#  MAIN ENTRYPOINT
# ============================================================

def main():
    if not TOKEN:
        print("\nERROR: BOT_TOKEN is missing in your environment or .env file!\n")
        return

    if GROUP_CHAT_ID == 0:
        print("\nERROR: GROUP_CHAT_ID is missing in your environment or .env file!\n")
        return

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))

    setup_scheduler(app)

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()