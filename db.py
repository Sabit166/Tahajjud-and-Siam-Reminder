"""
Database helpers: connection management, schema, active-poll tracking,
and response/report queries.
"""

import sqlite3
import datetime
from pathlib import Path
from typing import Optional, Iterator
from contextlib import contextmanager

from config import DB_PATH, BD_TZ, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE, log

# ============================================================
#  DATABASE HELPERS
# ============================================================

@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
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

def get_daily_summary() -> tuple[list[str], dict[str, dict[str, int]]]:
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