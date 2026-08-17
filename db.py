"""
Database helpers — Supabase/PostgREST implementation.

Public API is intentionally identical to the old SQLite version so the
other modules (handlers, messaging, scheduling, main) don't need
changes. All work is done through the supabase client.

Tables (created by supabase_schema.sql):
    responses     -- one row per (user_id, practice, date)
    active_polls  -- poll_id -> practice_key
    streaks       -- one row per (user_id, practice)
"""

import datetime
from typing import Optional

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_API_KEY, BD_TZ, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE, log


# ============================================================
#  CLIENT
# ============================================================

# Lazy-init so the module imports cleanly even when env vars are
# missing (e.g. during `python -c "import db"` smoke tests). The first
# real DB call will raise a clear error if creds are wrong.
_client: Optional[Client] = None


def _sb() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_API_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_API_KEY must be set in .env"
            )
        # The supabase client expects the bare project URL, not the
        # PostgREST path. Strip "/rest/v1" and trailing slashes.
        url = SUPABASE_URL.rstrip("/")
        for suffix in ("/rest/v1", "/rest"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
                break
        _client = create_client(url, SUPABASE_API_KEY)
    return _client


# ============================================================
#  INIT
# ============================================================

def init_db():
    """No-op for Supabase. The schema is created via the SQL editor
    (see supabase_schema.sql). We still verify connectivity so the bot
    fails fast on a bad key."""
    try:
        # 1-row read forces a real round trip and surfaces auth errors.
        _sb().table("responses").select("id").limit(1).execute()
        log.info("Database (Supabase) ready.")
    except Exception as exc:
        log.error("Supabase connectivity check failed: %s", exc)
        raise


# ============================================================
#  ACTIVE POLLS
# ============================================================

# In-process cache, mirrors the SQLite-era one. Writes go to Supabase
# immediately; the cache is purely an optimization for the hot path
# (poll answer -> lookup practice).
_ACTIVE_POLL_CACHE: dict[str, str] = {}


def save_active_poll(poll_id: str, practice_key: str):
    _ACTIVE_POLL_CACHE[poll_id] = practice_key
    try:
        _sb().table("active_polls").upsert(
            {"poll_id": poll_id, "practice_key": practice_key}
        ).execute()
    except Exception as exc:
        log.warning("Could not save active poll %s: %s", poll_id, exc)


def get_poll_practice(poll_id: str) -> Optional[str]:
    if poll_id in _ACTIVE_POLL_CACHE:
        return _ACTIVE_POLL_CACHE[poll_id]
    try:
        res = (
            _sb()
            .table("active_polls")
            .select("practice_key")
            .eq("poll_id", poll_id)
            .limit(1)
            .execute()
        )
        if res.data:
            key = res.data[0]["practice_key"]
            _ACTIVE_POLL_CACHE[poll_id] = key
            return key
    except Exception as exc:
        log.warning("Could not read active poll %s: %s", poll_id, exc)
    return None


def delete_active_poll(poll_id: str):
    _ACTIVE_POLL_CACHE.pop(poll_id, None)
    try:
        _sb().table("active_polls").delete().eq("poll_id", poll_id).execute()
    except Exception as exc:
        log.warning("Could not delete active poll %s: %s", poll_id, exc)


def cleanup_old_active_polls(hours: int = 24):
    try:
        cutoff = (
            datetime.datetime.now(BD_TZ) - datetime.timedelta(hours=hours)
        ).isoformat()
        res = (
            _sb()
            .table("active_polls")
            .delete()
            .lt("created_at", cutoff)
            .execute()
        )
        deleted = len(res.data or [])
        if deleted:
            log.info("Cleaned up %d old active poll(s).", deleted)
    except Exception as exc:
        log.warning("Could not cleanup old active polls: %s", exc)


# ============================================================
#  RESPONSES
# ============================================================

def save_response(
    user_id: int,
    username: str,
    full_name: str,
    practice: str,
    did_it: int,
):
    """Upsert today's response for (user, practice). did_it is 0/1 for
    backward compatibility with the handler; we coerce to a real bool
    and store in the boolean column."""
    today = datetime.datetime.now(BD_TZ).date().isoformat()
    now = datetime.datetime.now(BD_TZ).isoformat()

    payload = {
        "user_id": int(user_id),
        "username": username or "",
        "full_name": full_name,
        "practice": practice,
        "did_it": bool(did_it),
        "response_date": today,
        "recorded_at": now,
    }

    try:
        _sb().table("responses").upsert(
            payload,
            on_conflict="user_id,practice,response_date",
        ).execute()
        log.info(
            "Saved response: %s | %s | did_it=%s",
            full_name, practice, did_it,
        )
    except Exception as exc:
        log.error("Could not save response: %s", exc)
        raise


def get_weekly_summary():
    """Return [(full_name, practice, completed, total), ...] for the
    last 7 days (inclusive of today)."""
    today = datetime.datetime.now(BD_TZ).date()
    week_ago = today - datetime.timedelta(days=6)

    try:
        # PostgREST doesn't have SUM/COUNT over PostgREST directly,
        # but we can fetch the rows for the window and aggregate in
        # Python. Volume is small (active group), so this is fine.
        res = (
            _sb()
            .table("responses")
            .select("user_id,full_name,practice,did_it,response_date")
            .gte("response_date", week_ago.isoformat())
            .lte("response_date", today.isoformat())
            .execute()
        )
    except Exception as exc:
        log.error("Could not load weekly summary: %s", exc)
        return []

    # Aggregate: (user_id, practice) -> (done_days, total_days)
    agg: dict[tuple[int, str], tuple[int, int]] = {}
    name_by_user: dict[int, str] = {}
    for row in res.data or []:
        key = (row["user_id"], row["practice"])
        done, total = agg.get(key, (0, 0))
        if row["did_it"]:
            done += 1
        agg[key] = (done, total + 1)
        name_by_user[row["user_id"]] = row["full_name"]

    out = []
    for (uid, practice), (done, total) in agg.items():
        out.append((name_by_user[uid], practice, done, total))
    out.sort(key=lambda r: (r[0], r[1]))
    return out


def get_daily_summary() -> tuple[list[str], dict[str, dict[str, int]], str, str]:
    """Return (scheduled_practices, summary, report_start_iso, report_end_iso).

    summary is keyed by full_name -> {practice: did_it_bool}.
    """
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
    if report_start.weekday() == 3:    # Thursday
        scheduled_practices.append("surah_kahf")
    scheduled_practices.extend(
        ["tahajjud", "morning_dhikr", "fazr_jamaat", "ishraq_salat", "salatud_duha", "istighfar_100x"]
    )
    if report_end.weekday() in (0, 3):  # Sun, Thu
        scheduled_practices.append("sawm")

    try:
        res = (
            _sb()
            .table("responses")
            .select("full_name,practice,did_it")
            .gt("recorded_at", report_start.isoformat())
            .lte("recorded_at", report_end.isoformat())
            .in_("practice", scheduled_practices)
            .execute()
        )
    except Exception as exc:
        log.error("Could not load daily summary: %s", exc)
        return (
            scheduled_practices,
            {},
            report_start.isoformat(),
            report_end.isoformat(),
        )

    summary: dict[str, dict[str, int]] = {}
    for row in res.data or []:
        summary.setdefault(row["full_name"], {})[row["practice"]] = (
            1 if row["did_it"] else 0
        )

    return (
        scheduled_practices,
        summary,
        report_start.isoformat(),
        report_end.isoformat(),
    )


# ============================================================
#  STREAKS
# ============================================================

def get_streak(user_id: int, practice: str) -> int:
    try:
        res = (
            _sb()
            .table("streaks")
            .select("current_streak")
            .eq("user_id", user_id)
            .eq("practice", practice)
            .limit(1)
            .execute()
        )
        if res.data:
            return int(res.data[0]["current_streak"])
    except Exception as exc:
        log.warning("Could not read streak: %s", exc)
    return 0


def get_all_streaks(user_id: int) -> dict[str, int]:
    try:
        res = (
            _sb()
            .table("streaks")
            .select("practice,current_streak")
            .eq("user_id", user_id)
            .execute()
        )
        return {r["practice"]: int(r["current_streak"]) for r in (res.data or [])}
    except Exception as exc:
        log.warning("Could not read all streaks: %s", exc)
        return {}


def update_streak_for_response(
    user_id: int,
    practice: str,
    scheduled_date: str,
    did_it: bool,
) -> int:
    """Update the streak for (user, practice) given today's outcome.

    Returns the new current_streak.
    """
    today = scheduled_date
    new_streak = 0

    try:
        existing = (
            _sb()
            .table("streaks")
            .select("current_streak,longest_streak,last_done_date")
            .eq("user_id", user_id)
            .eq("practice", practice)
            .limit(1)
            .execute()
        )
        row = existing.data[0] if existing.data else None

        if did_it:
            if row is None:
                new_streak = 1
                longest = 1
                last_done = today
            else:
                prev_streak = int(row["current_streak"])
                longest = int(row["longest_streak"])
                last_done = row["last_done_date"]
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
                        new_streak = (
                            prev_streak + 1
                            if _is_consecutive_scheduled(practice, prev_date, cur_date)
                            else 1
                        )
                    else:
                        new_streak = 1
                else:
                    new_streak = 1
                longest = max(longest, new_streak)
                last_done = today
        else:
            new_streak = 0
            longest = int(row["longest_streak"]) if row else 0
            last_done = row["last_done_date"] if row else None

        payload = {
            "user_id": int(user_id),
            "practice": practice,
            "current_streak": int(new_streak),
            "longest_streak": int(longest),
            "last_done_date": last_done,
            "last_scheduled_date": today,
        }
        _sb().table("streaks").upsert(
            payload, on_conflict="user_id,practice"
        ).execute()
        return new_streak
    except Exception as exc:
        log.error("Could not update streak: %s", exc)
        return 0


def _is_consecutive_scheduled(
    practice: str, prev_date: datetime.date, cur_date: datetime.date
) -> bool:
    """Return True if prev_date and cur_date are consecutive *scheduled*
    dates for this practice (e.g. consecutive Mon/Thu for sawm)."""
    from practices import SCHEDULED_WEEKDAYS
    allowed = SCHEDULED_WEEKDAYS.get(practice)
    if not allowed:
        return False
    prev_was_scheduled = prev_date.weekday() in allowed
    cur_was_scheduled = cur_date.weekday() in allowed
    return prev_was_scheduled and cur_was_scheduled and (cur_date - prev_date).days > 0


def get_daily_streaks(
    report_start: str, report_end: str, practices: list[str]
) -> dict[str, dict[str, int]]:
    """
    Build {full_name: {practice: current_streak}} for users who responded
    in the [report_start, report_end) window, restricted to `practices`.

    Users/practices with no row in `streaks` are simply absent.
    """
    try:
        # Distinct (user_id, full_name) pairs that responded in the window.
        user_rows = (
            _sb()
            .table("responses")
            .select("user_id,full_name")
            .gt("recorded_at", report_start)
            .lte("recorded_at", report_end)
            .in_("practice", practices)
            .execute()
        )
    except Exception as exc:
        log.error("Could not load daily streaks: %s", exc)
        return {}

    # De-dup (one user_id may appear on multiple practices)
    seen: dict[int, str] = {}
    for r in user_rows.data or []:
        seen.setdefault(r["user_id"], r["full_name"])

    result: dict[str, dict[str, int]] = {}
    for uid, full_name in seen.items():
        try:
            res = (
                _sb()
                .table("streaks")
                .select("practice,current_streak")
                .eq("user_id", uid)
                .in_("practice", practices)
                .execute()
            )
        except Exception as exc:
            log.warning("Could not load streaks for user %s: %s", uid, exc)
            continue
        merged = result.setdefault(full_name, {})
        for r in res.data or []:
            merged[r["practice"]] = max(
                merged.get(r["practice"], 0), int(r["current_streak"])
            )
    return result


# ============================================================
#  WEEKLY MAX (unchanged from SQLite version)
# ============================================================

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