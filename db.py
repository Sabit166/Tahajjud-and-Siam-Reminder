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
        c.execute("""
            CREATE TABLE IF NOT EXISTS streaks (
                user_id          INTEGER NOT NULL,
                practice         TEXT    NOT NULL,
                current_streak   INTEGER NOT NULL DEFAULT 0,
                longest_streak   INTEGER NOT NULL DEFAULT 0,
                last_done_date   TEXT,
                last_scheduled_date TEXT,
                PRIMARY KEY (user_id, practice)
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


# Maximum possible weekly completions per practice.
# Most daily practices = 7 (once per day). Sawm = 2 (Mon+Thu). Surah Kahf = 1 (Fri).
WEEKLY_MAX: dict[str, int] = {
    "morning_dhikr": 7,
    "fazr_jamaat": 7,
    "ishraq_salat": 7,
    "salatud_duha": 7,
    "evening_dhikr": 7,
    "salawat_on_rasulullah": 7,
    "tahajjud": 7,
    "istighfar_100x": 7,
    "sawm": 2,
    "surah_kahf": 1,
    "nightly_al_mulk": 7,
    "nightly_as_sajdah": 7,
    "nightly_al_baqarah_last_2": 7,
    "nightly_33_tasbeeh": 7,
}

def get_daily_summary() -> tuple[list[str], dict[str, dict[str, int]], str, str]:
    """Return (scheduled_practices, summary, report_start_iso, report_end_iso)."""
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

    scheduled_practices.extend(["tahajjud", "morning_dhikr", "fazr_jamaat", "ishraq_salat", "salatud_duha", "istighfar_100x"])
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

    return (
        scheduled_practices,
        summary,
        report_start.strftime("%Y-%m-%d %H:%M:%S"),
        report_end.strftime("%Y-%m-%d %H:%M:%S"),
    )


# ============================================================
#  STREAK HELPERS
# ============================================================
#
# Streaks are counted FORWARD ONLY (Q3=A): existing history is not
# back-filled. The handler calls update_streak_for_response() on every
# poll answer with the *scheduled date* (the date the poll was meant
# for) and whether the user did it.
#
# For practices scheduled only on specific weekdays (sawm, surah_kahf),
# non-scheduled days do NOT break the streak. The caller is expected to
# pass the scheduled date, not "today", and we compare consecutive
# scheduled dates only.

def get_streak(user_id: int, practice: str) -> int:
    """Return the current streak count for a (user, practice). 0 if none."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT current_streak FROM streaks WHERE user_id=? AND practice=?",
            (user_id, practice),
        )
        row = c.fetchone()
        return int(row[0]) if row else 0


def get_all_streaks(user_id: int) -> dict[str, int]:
    """Return {practice: current_streak} for every tracked practice."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT practice, current_streak FROM streaks WHERE user_id=?",
            (user_id,),
        )
        return {practice: int(s) for practice, s in c.fetchall()}


def update_streak_for_response(
    user_id: int,
    practice: str,
    scheduled_date: str,    # "YYYY-MM-DD" the poll was meant for
    did_it: bool,
) -> int:
    """
    Update the streak for a (user, practice) given today's outcome.

    - If did_it: if last_done_date == yesterday (or for scheduled-only
      practices, the previous scheduled date), increment; else reset to 1.
    - If NOT did_it: reset to 0 (Q2=A).

    Returns the new current_streak.
    """
    today = scheduled_date
    new_streak = 0

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT current_streak, longest_streak, last_done_date FROM streaks "
            "WHERE user_id=? AND practice=?",
            (user_id, practice),
        )
        row = c.fetchone()

        if did_it:
            if row is None:
                new_streak = 1
                longest = 1
                last_done = today
            else:
                prev_streak, longest, last_done = row
                if last_done:
                    try:
                        prev_date = datetime.date.fromisoformat(last_done)
                        cur_date = datetime.date.fromisoformat(today)
                        delta_days = (cur_date - prev_date).days
                    except ValueError:
                        delta_days = None

                    if delta_days == 1:
                        new_streak = prev_streak + 1
                    elif delta_days is not None and delta_days > 1:
                        # Scheduled-only practices (sawm, kahf) skip days.
                        # If the gap exactly matches the weekday gap, allow
                        # it; otherwise reset.
                        new_streak = prev_streak + 1 if _is_consecutive_scheduled(
                            practice, prev_date, cur_date
                        ) else 1
                    else:
                        new_streak = 1
                else:
                    new_streak = 1
                longest = max(int(longest), new_streak)
                last_done = today
        else:
            # Missed: reset to 0 (Q2=A: immediate reset).
            new_streak = 0
            longest = int(row[1]) if row else 0
            last_done = row[2] if row else None

        c.execute(
            """
            INSERT INTO streaks (user_id, practice, current_streak, longest_streak, last_done_date, last_scheduled_date)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, practice) DO UPDATE SET
                current_streak=excluded.current_streak,
                longest_streak=excluded.longest_streak,
                last_done_date=excluded.last_done_date,
                last_scheduled_date=excluded.last_scheduled_date
            """,
            (user_id, practice, new_streak, longest, last_done, today),
        )
        conn.commit()
    return new_streak


def _is_consecutive_scheduled(practice: str, prev_date: datetime.date, cur_date: datetime.date) -> bool:
    """Return True if prev_date and cur_date are consecutive *scheduled*
    dates for this practice (i.e. weekday(prev)+1 == weekday(cur))."""
    from practices import SCHEDULED_WEEKDAYS
    allowed = SCHEDULED_WEEKDAYS.get(practice)
    if not allowed:
        return False
    prev_was_scheduled = prev_date.weekday() in allowed
    cur_was_scheduled = cur_date.weekday() in allowed
    return prev_was_scheduled and cur_was_scheduled and (cur_date - prev_date).days > 0


def get_streaks_for_user_on_date(user_id: int, on_date: str) -> dict[str, int]:
    """Return {practice: current_streak} for a user, only including rows
    whose last_done_date == on_date. Used by weekly report (Q10 = no
    streak on weekly, but kept here for future use)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT practice, current_streak FROM streaks "
            "WHERE user_id=? AND last_done_date=?",
            (user_id, on_date),
        )
        return {practice: int(s) for practice, s in c.fetchall()}


def get_daily_streaks(
    report_start: str, report_end: str, practices: list[str]
) -> dict[str, dict[str, int]]:
    """
    Build {full_name: {practice: current_streak}} for users who responded
    in the given [report_start, report_end) window, restricted to the
    given practices. Users/practices with no row in `streaks` are simply
    absent — callers should default to 0.

    Note: keyed by `full_name` to match the daily report grouping. If
    two Telegram users share a name, their streak rows are merged (last
    written wins per practice). Acceptable for the report's purposes.
    """
    placeholders = ",".join("?" for _ in practices)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT DISTINCT r.user_id, r.full_name
            FROM responses r
            WHERE r.recorded_at > ? AND r.recorded_at <= ?
              AND r.practice IN ({placeholders})
            """,
            (report_start, report_end, *practices),
        )
        user_rows = c.fetchall()

        result: dict[str, dict[str, int]] = {}
        for user_id, full_name in user_rows:
            c.execute(
                "SELECT practice, current_streak FROM streaks "
                "WHERE user_id=? AND practice IN ("
                + placeholders
                + ")",
                (user_id, *practices),
            )
            streaks = {p: int(s) for p, s in c.fetchall()}
            # Merge if a name appears under multiple user_ids.
            merged = result.setdefault(full_name, {})
            for p, s in streaks.items():
                # Prefer the higher of the two values — a streak is
                # something to be celebrated, not averaged down.
                merged[p] = max(merged.get(p, 0), s)
        return result