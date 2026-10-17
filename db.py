"""
Database helpers (Supabase/PostgREST). Every table is keyed by the
Telegram group's chat id; see supabase_schema.sql for the schema.
"""

from __future__ import annotations

import datetime
import secrets
import contextvars
from typing import Optional

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_API_KEY, BD_TZ, log


# ============================================================
#  CLIENT
# ============================================================

# Lazy-init so the module imports cleanly even when env vars are
# missing (e.g. during `python -c "import db"` smoke tests). The first
# real DB call will raise a clear error if creds are wrong.
_client: Optional[Client] = None
_current_group_id: contextvars.ContextVar[int] = contextvars.ContextVar(
    "telegram_group_id", default=0
)


def clear_current_group() -> None:
    """Drop any stale per-request group context before switching tenants."""
    previous_group = int(_current_group_id.get())
    if previous_group:
        _POLL_CONFIGS_CACHE.pop(previous_group, None)
    _current_group_id.set(0)


def set_current_group(group_chat_id: int):
    """Set the tenant used by legacy helpers that do not receive a group id."""
    next_group = int(group_chat_id)
    previous_group = int(_current_group_id.get())
    if previous_group and previous_group != next_group:
        _POLL_CONFIGS_CACHE.pop(previous_group, None)
    return _current_group_id.set(next_group)


def current_group_id() -> int:
    group_id = int(_current_group_id.get())
    if not group_id:
        raise RuntimeError("No Telegram group context is active")
    return group_id


def _group_id(value: int | None = None) -> int:
    return int(value if value is not None else current_group_id())


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
    """Verify connectivity and enable Supabase-backed poll configurations."""
    global _SUPABASE_POLL_CONFIGS_AVAILABLE
    try:
        # 1-row read forces a real round trip and surfaces auth errors.
        _sb().table("groups").select("chat_id").limit(1).execute()
        log.info("Database (Supabase) ready.")
    except Exception as exc:
        log.error("Supabase connectivity check failed: %s", exc)
        raise
    # Poll configs are per group and read on demand, but every read and
    # write is gated on this flag, so it must be set before any job runs.
    try:
        _sb().table("poll_configs").select("id").limit(1).execute()
        _SUPABASE_POLL_CONFIGS_AVAILABLE = True
        log.info("Supabase poll_configs table available.")
    except Exception as exc:
        log.error("Supabase table 'poll_configs' is unavailable: %s", exc)
        raise


def register_group(chat_id: int, title: str | None = None, username: str | None = None) -> dict:
    """Register/update a Telegram group without creating default poll configs.

    Title and username are only written when given, so callers that only
    know the chat id (e.g. dashboard login) don't erase the stored name.
    """
    payload: dict = {"chat_id": int(chat_id), "is_active": True}
    if title is not None:
        payload["title"] = title
    if username is not None:
        payload["username"] = username
    try:
        return (_sb().table("groups").upsert(payload, on_conflict="chat_id").execute().data or [payload])[0]
    except Exception as exc:
        log.warning("Could not register group %s: %s", chat_id, exc)
        return payload


def deactivate_group(chat_id: int, reason: str) -> None:
    """Stop scheduling for a group the bot can no longer post to."""
    try:
        _sb().table("groups").update({"is_active": False}).eq("chat_id", int(chat_id)).execute()
        log.warning("Deactivated group %s: %s", chat_id, reason)
    except Exception as exc:
        log.error("Could not deactivate group %s (%s): %s", chat_id, reason, exc)


def list_groups() -> list[dict]:
    try:
        return _sb().table("groups").select("*").eq("is_active", True).execute().data or []
    except Exception as exc:
        log.error("Could not list active groups: %s", exc)
        raise


def issue_setup_token(chat_id: int, user_id: int, ttl_minutes: int = 10) -> str:
    token = secrets.token_urlsafe(32)
    payload = {
        "token": token, "chat_id": int(chat_id), "issued_by": int(user_id),
        "expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ttl_minutes)).isoformat(),
    }
    _sb().table("setup_tokens").insert(payload).execute()
    return token


def get_setup_token(token: str, user_id: int) -> dict | None:
    try:
        row = (
            _sb()
            .table("setup_tokens")
            .select("*")
            .eq("token", token)
            .eq("issued_by", int(user_id))
            .is_("consumed_at", "null")
            .limit(1)
            .execute()
            .data
            or [None]
        )[0]
        if not row:
            return None
        expires_at = datetime.datetime.fromisoformat(
            row["expires_at"].replace("Z", "+00:00")
        )
        if expires_at <= datetime.datetime.now(datetime.timezone.utc):
            return None
        return row
    except Exception:
        return None


def consume_setup_token(token: str, user_id: int | None = None) -> dict | None:
    try:
        query = _sb().table("setup_tokens").select("*").eq("token", token).is_("consumed_at", "null")
        if user_id is not None:
            query = query.eq("issued_by", int(user_id))
        result = query.limit(1).execute()
        row = (result.data or [None])[0]
        if not row or datetime.datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= datetime.datetime.now(datetime.timezone.utc):
            return None
        consume_query = (
            _sb().table("setup_tokens")
            .update({"consumed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
            .eq("token", token).is_("consumed_at", "null")
        )
        if user_id is not None:
            consume_query = consume_query.eq("issued_by", int(user_id))
        consumed = consume_query.select("*").execute()
        return (consumed.data or [None])[0]
    except Exception:
        return None


def issue_session(chat_id: int, user_id: int, ttl_minutes: int = 60) -> str:
    token = secrets.token_urlsafe(32)
    _sb().table("web_sessions").insert({
        "token": token, "chat_id": int(chat_id), "user_id": int(user_id),
        "expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ttl_minutes)).isoformat(),
    }).execute()
    return token


def get_session(token: str) -> dict | None:
    try:
        row = (_sb().table("web_sessions").select("*").eq("token", token).limit(1).execute().data or [None])[0]
        if not row or datetime.datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= datetime.datetime.now(datetime.timezone.utc):
            return None
        return row
    except Exception:
        return None


# ============================================================
#  POLL CONFIGURATIONS (DYNAMIC SCHEDULES & PRACTICES)
# ============================================================

_POLL_CONFIGS_CACHE: dict[int, dict[str, dict]] = {}
_SUPABASE_POLL_CONFIGS_AVAILABLE: bool | None = None


def _poll_cache_for_group(group_chat_id: int | None = None) -> dict[str, dict]:
    group_id = _group_id(group_chat_id)
    if group_id not in _POLL_CONFIGS_CACHE:
        _POLL_CONFIGS_CACHE[group_id] = {}
    return _POLL_CONFIGS_CACHE[group_id]


DEFAULT_BINARY_OPTIONS = ["Alhamdulillah, done", "Incomplete/Missed"]


def _binary_options(options: object) -> list[str]:
    values = [str(option).strip() for option in (options or []) if str(option).strip()]
    return values[:2] if len(values) >= 2 else list(DEFAULT_BINARY_OPTIONS)


def get_all_poll_configs(active_only: bool = False, group_chat_id: int | None = None) -> list[dict]:
    group_id = _group_id(group_chat_id)
    cache = _poll_cache_for_group(group_id)

    if _SUPABASE_POLL_CONFIGS_AVAILABLE:
        try:
            res = _sb().table("poll_configs").select("*").eq("group_chat_id", group_id).order("created_at").execute()
            cache.clear()
            for row in res.data or []:
                # Only amal polls are configurable; legacy report/reminder
                # rows are ignored (reports are built in, see messaging.py).
                if row.get("poll_type", "amal_poll") == "amal_poll":
                    cache[row["id"]] = row
        except Exception as exc:
            log.warning("Failed to refresh poll_configs from Supabase: %s", exc)

    items = list(cache.values())
    for item in items:
        item["poll_options"] = _binary_options(item.get("poll_options"))
    if active_only:
        items = [i for i in items if i.get("is_active", True)]
    return items

def get_poll_config(poll_id: str, group_chat_id: int | None = None) -> Optional[dict]:
    group_id = _group_id(group_chat_id)
    cache = _poll_cache_for_group(group_id)
    if not cache:
        get_all_poll_configs(group_chat_id=group_id)
    return cache.get(poll_id)

def upsert_poll_config(data: dict, group_chat_id: int | None = None) -> dict:
    group_id = _group_id(group_chat_id)
    poll_id = data.get("id")
    if not poll_id:
        raise ValueError("Poll configuration must have an 'id'")

    cache = _poll_cache_for_group(group_id)
    now_iso = datetime.datetime.now(BD_TZ).isoformat()
    clean_data = {
        "group_chat_id": group_id,
        "id": poll_id,
        "title": str(data.get("title", "")),
        "poll_type": "amal_poll",
        "poll_options": _binary_options(data.get("poll_options")),
        "weight": int(data.get("weight", 1)),
        "time_type": str(data.get("time_type", "prayer_relative")),
        "fixed_time": data.get("fixed_time") if data.get("time_type") == "fixed" else None,
        "prayer_name": data.get("prayer_name") if data.get("time_type") == "prayer_relative" else None,
        "prayer_offset_minutes": int(data.get("prayer_offset_minutes", 0)),
        "days_of_week": list(data.get("days_of_week") if data.get("days_of_week") is not None else [0, 1, 2, 3, 4, 5, 6]),
        "is_active": bool(data.get("is_active", True)),
        "updated_at": now_iso,
    }
    if "created_at" in data:
        clean_data["created_at"] = data["created_at"]
    else:
        clean_data["created_at"] = cache.get(poll_id, {}).get("created_at", now_iso)

    if not _SUPABASE_POLL_CONFIGS_AVAILABLE:
        raise RuntimeError("Supabase poll_configs is not available; change was not saved")
    try:
        _sb().table("poll_configs").upsert(clean_data, on_conflict="group_chat_id,id").execute()
    except Exception as exc:
        log.error("Could not persist poll_config %s to Supabase: %s", poll_id, exc)
        raise
    cache[poll_id] = clean_data

    return clean_data

def delete_poll_config(poll_id: str, group_chat_id: int | None = None) -> bool:
    group_id = _group_id(group_chat_id)
    cache = _poll_cache_for_group(group_id)
    if not _SUPABASE_POLL_CONFIGS_AVAILABLE:
        raise RuntimeError("Supabase poll_configs is not available; poll was not deleted")
    try:
        res = (
            _sb().table("poll_configs").delete()
            .eq("id", poll_id).eq("group_chat_id", group_id).eq("poll_type", "amal_poll")
            .execute()
        )
    except Exception as exc:
        log.error("Could not delete poll_config %s from Supabase: %s", poll_id, exc)
        raise
    existed = poll_id in cache or bool(res.data)
    cache.pop(poll_id, None)
    return existed

def get_practice_info(practice_key: str) -> dict:
    conf = get_poll_config(practice_key)
    if not conf:
        raise KeyError(f"Poll configuration '{practice_key}' does not exist for the active group")
    return {
        "label": conf.get("title", practice_key),
        "title": conf.get("title", practice_key),
        "poll_options": _binary_options(conf.get("poll_options")),
        "weight": conf.get("weight", 1),
        "poll_type": conf.get("poll_type", "amal_poll"),
    }

def get_practice_weight(practice_key: str) -> int:
    conf = get_poll_config(practice_key)
    if not conf or conf.get("weight") is None:
        raise KeyError(f"Poll configuration '{practice_key}' does not exist for the active group")
    return int(conf["weight"])

def get_scheduled_weekdays(practice_key: str) -> set[int]:
    conf = get_poll_config(practice_key)
    if not conf or conf.get("days_of_week") is None:
        return set()
    return set(conf["days_of_week"])

def get_weekly_max(practice_key: str) -> int:
    days = get_scheduled_weekdays(practice_key)
    if days:
        return len(days)
    return 0


# ============================================================
#  ACTIVE POLLS
# ============================================================

def save_active_poll(
    poll_id: str,
    practice_key: str,
    group_chat_id: int | None = None,
    message_id: int | None = None,
):
    """Record a sent poll. message_id lets the bot close it after a restart."""
    group_id = _group_id(group_chat_id)
    payload = {"poll_id": poll_id, "practice_key": practice_key, "group_chat_id": group_id}
    if message_id is not None:
        payload["message_id"] = int(message_id)
    try:
        _sb().table("active_polls").upsert(payload, on_conflict="group_chat_id,poll_id").execute()
        log.info("Registered active poll %s for group %s (%s).", poll_id, group_id, practice_key)
    except Exception as exc:
        log.error("Could not save active poll %s for group %s: %s", poll_id, group_id, exc)


def list_active_polls() -> list[dict]:
    """All tracked polls with their send time and Telegram message id."""
    try:
        return _select_all(lambda: (
            _sb().table("active_polls")
            .select("poll_id,practice_key,group_chat_id,created_at,message_id")
            .order("group_chat_id").order("poll_id")
        ))
    except Exception as exc:
        log.warning("Could not list active polls: %s", exc)
        return []


def get_active_poll(poll_id: str) -> Optional[dict]:
    """Return {group_chat_id, practice_key, created_at} for a sent poll.

    Telegram poll ids are globally unique, so no group filter is needed;
    created_at is when the poll was sent.
    """
    try:
        rows = (
            _sb().table("active_polls")
            .select("group_chat_id,practice_key,created_at")
            .eq("poll_id", poll_id).limit(1).execute().data
        )
        return rows[0] if rows else None
    except Exception as exc:
        log.warning("Could not read active poll %s: %s", poll_id, exc)
        return None


def delete_active_poll(poll_id: str, group_chat_id: int | None = None):
    try:
        _sb().table("active_polls").delete().eq("poll_id", poll_id).eq("group_chat_id", _group_id(group_chat_id)).execute()
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
    group_chat_id: int | None = None,
    response_date: datetime.date | None = None,
):
    """Upsert the response for (user, practice, amal day).

    ``response_date`` is the amal day (Maghrib to Maghrib) of the poll
    that was answered; it defaults to today's calendar date. did_it is
    0/1 and is stored as a boolean.
    """
    today = (response_date or datetime.datetime.now(BD_TZ).date()).isoformat()
    now = datetime.datetime.now(BD_TZ).isoformat()

    payload = {
        "group_chat_id": _group_id(group_chat_id),
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
            on_conflict="group_chat_id,user_id,practice,response_date",
        ).execute()
        log.info(
            "Saved response: %s | %s | did_it=%s",
            full_name, practice, did_it,
        )
    except Exception as exc:
        log.error("Could not save response: %s", exc)
        raise


_PAGE_SIZE = 1000


def _select_all(build_query) -> list[dict]:
    """Fetch every row of a query page by page.

    PostgREST caps each response (Supabase default: 1000 rows) and drops
    the rest silently, so report queries must paginate. ``build_query``
    returns a fresh, deterministically ordered query on each call. Reads
    until an empty page so a server cap below _PAGE_SIZE is also safe.
    """
    rows: list[dict] = []
    while True:
        page = build_query().range(len(rows), len(rows) + _PAGE_SIZE - 1).execute().data or []
        if not page:
            return rows
        rows.extend(page)


def delete_response(
    user_id: int, practice: str, response_date: datetime.date, group_chat_id: int | None = None
) -> bool:
    """Remove a member's answer for one amal day (used when a vote is retracted)."""
    try:
        res = (
            _sb().table("responses").delete()
            .eq("group_chat_id", _group_id(group_chat_id)).eq("user_id", int(user_id))
            .eq("practice", practice).eq("response_date", response_date.isoformat())
            .execute()
        )
        return bool(res.data)
    except Exception as exc:
        log.error("Could not delete response of %s for %s: %s", user_id, practice, exc)
        return False


def record_member(chat_id: int, user_id: int, full_name: str, username: str | None = None) -> None:
    """Remember a member who joined the group (table group_members)."""
    payload = {
        "group_chat_id": int(chat_id), "user_id": int(user_id),
        "full_name": full_name, "username": username or "",
    }
    try:
        _sb().table("group_members").upsert(payload, on_conflict="group_chat_id,user_id").execute()
        log.info("Recorded member %s (%s) in group %s.", user_id, full_name, chat_id)
    except Exception as exc:
        log.warning("Could not record member %s in group %s (is group_members created?): %s",
                    user_id, chat_id, exc)


def remove_member(chat_id: int, user_id: int) -> None:
    """Forget a member who left or was removed. Their past poll answers stay
    (reports and streak history need them)."""
    try:
        (_sb().table("group_members").delete()
         .eq("group_chat_id", int(chat_id)).eq("user_id", int(user_id)).execute())
        log.info("Removed member %s from group %s records.", user_id, chat_id)
    except Exception as exc:
        log.warning("Could not remove member %s from group %s records: %s", user_id, chat_id, exc)


def get_recent_activity(
    since_day: datetime.date, group_chat_id: int | None = None
) -> tuple[dict[int, set[datetime.date]], set[datetime.date], dict[int, datetime.date]]:
    """Activity since ``since_day`` for inactivity streaks.

    Returns (days each member answered, days anyone in the group answered,
    day each recorded member joined).
    """
    group_id = _group_id(group_chat_id)
    answered: dict[int, set[datetime.date]] = {}
    group_days: set[datetime.date] = set()
    joined: dict[int, datetime.date] = {}
    try:
        for row in _select_all(lambda: (
            _sb().table("responses")
            .select("user_id,response_date")
            .eq("group_chat_id", group_id)
            .gte("response_date", since_day.isoformat())
            .order("id")
        )):
            day = datetime.date.fromisoformat(row["response_date"])
            answered.setdefault(int(row["user_id"]), set()).add(day)
            group_days.add(day)
    except Exception as exc:
        log.error("Could not load recent activity of group %s: %s", group_id, exc)
    try:
        for row in _select_all(lambda: (
            _sb().table("group_members")
            .select("user_id,joined_at")
            .eq("group_chat_id", group_id)
            .order("user_id")
        )):
            if row.get("joined_at"):
                joined_at = datetime.datetime.fromisoformat(str(row["joined_at"]).replace("Z", "+00:00"))
                joined[int(row["user_id"])] = joined_at.astimezone(BD_TZ).date()
    except Exception as exc:
        log.warning("Could not load join dates of group %s: %s", group_id, exc)
    return answered, group_days, joined


def get_known_members(group_chat_id: int | None = None) -> dict[int, str]:
    """{user_id: full_name} of everyone the bot knows in the group: members
    recorded when they joined, plus everyone who has ever answered a poll.
    Telegram does not let bots list group members, so this is the bot's
    view of the roster."""
    group_id = _group_id(group_chat_id)
    members: dict[int, str] = {}
    try:
        for row in _select_all(lambda: (
            _sb().table("group_members")
            .select("user_id,full_name")
            .eq("group_chat_id", group_id)
            .order("user_id")
        )):
            members[int(row["user_id"])] = row["full_name"]
    except Exception as exc:
        log.warning("Could not load recorded members of group %s: %s", group_id, exc)
    try:
        rows = _select_all(lambda: (
            _sb().table("responses")
            .select("user_id,full_name")
            .eq("group_chat_id", group_id)
            .order("id")
        ))
    except Exception as exc:
        log.error("Could not load poll participants of group %s: %s", group_id, exc)
        return members
    for row in rows:   # ordered by id, so the latest name wins
        members[int(row["user_id"])] = row["full_name"]
    return members


def get_weekly_summary(end_day: datetime.date | None = None):
    """Return [(user_id, full_name, practice, completed, total), ...] for
    the 7 amal days ending with ``end_day`` (default: today)."""
    today = end_day or datetime.datetime.now(BD_TZ).date()
    week_ago = today - datetime.timedelta(days=6)
    group_id = _group_id()

    try:
        # PostgREST has no SUM/COUNT here, so fetch the window's rows and
        # aggregate in Python.
        rows = _select_all(lambda: (
            _sb()
            .table("responses")
            .select("user_id,full_name,practice,did_it,response_date")
            .gte("response_date", week_ago.isoformat())
            .lte("response_date", today.isoformat())
            .eq("group_chat_id", group_id)
            .order("id")
        ))
    except Exception as exc:
        log.error("Could not load weekly summary: %s", exc)
        return []

    # Aggregate: (user_id, practice) -> (done_days, total_days)
    agg: dict[tuple[int, str], tuple[int, int]] = {}
    name_by_user: dict[int, str] = {}
    for row in rows:
        key = (row["user_id"], row["practice"])
        done, total = agg.get(key, (0, 0))
        if row["did_it"]:
            done += 1
        agg[key] = (done, total + 1)
        name_by_user[row["user_id"]] = row["full_name"]

    out = []
    for (uid, practice), (done, total) in agg.items():
        out.append((uid, name_by_user[uid], practice, done, total))
    out.sort(key=lambda r: (r[1], r[0], r[2]))
    return out


def get_daily_summary(
    amal_day: datetime.date, practices: list[str]
) -> tuple[dict[int, str], dict[int, dict[str, int]]]:
    """Return (names, done) for one amal day (Maghrib to Maghrib).

    names: {user_id: full_name}; done: {user_id: {practice: 0/1}}.
    Members are keyed by id so two people with the same name stay apart.
    """
    if not practices:
        return {}, {}
    group_id = _group_id()
    try:
        rows = _select_all(lambda: (
            _sb()
            .table("responses")
            .select("user_id,full_name,practice,did_it")
            .eq("response_date", amal_day.isoformat())
            .in_("practice", practices)
            .eq("group_chat_id", group_id)
            .order("id")
        ))
    except Exception as exc:
        log.error("Could not load daily summary: %s", exc)
        return {}, {}

    names: dict[int, str] = {}
    done: dict[int, dict[str, int]] = {}
    for row in rows:
        names[row["user_id"]] = row["full_name"]
        done.setdefault(row["user_id"], {})[row["practice"]] = 1 if row["did_it"] else 0
    return names, done


# ============================================================
#  STREAKS
# ============================================================


# (group, user, practice) -> (streak date of the last vote, streak row before
# it, or None if there was none). In memory only: a retraction after a
# restart cannot roll the streak back (see undo_streak_for_retraction).
_STREAK_UNDO: dict[tuple[int, int, str], tuple[str, Optional[dict]]] = {}


def update_streak_for_response(
    user_id: int,
    practice: str,
    scheduled_date: str,
    did_it: bool,
    group_chat_id: int | None = None,
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
            .select("current_streak,longest_streak,last_done_date,last_scheduled_date")
            .eq("user_id", user_id)
            .eq("practice", practice)
            .eq("group_chat_id", _group_id(group_chat_id))
            .limit(1)
            .execute()
        )
        row = existing.data[0] if existing.data else None
        # Remember the state before this vote so a retraction can undo it.
        _STREAK_UNDO[(_group_id(group_chat_id), int(user_id), practice)] = (today, row)

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
            "group_chat_id": _group_id(group_chat_id),
            "user_id": int(user_id),
            "practice": practice,
            "current_streak": int(new_streak),
            "longest_streak": int(longest),
            "last_done_date": last_done,
            "last_scheduled_date": today,
        }
        _sb().table("streaks").upsert(
            payload, on_conflict="group_chat_id,user_id,practice"
        ).execute()
        return new_streak
    except Exception as exc:
        log.error("Could not update streak: %s", exc)
        return 0


def undo_streak_for_retraction(
    user_id: int, practice: str, scheduled_date: str, group_chat_id: int | None = None
) -> bool:
    """Restore the streak to what it was before the vote on ``scheduled_date``.

    Returns False when the pre-vote state is unknown (e.g. the bot restarted
    between the vote and the retraction); the streak is then left as is.
    """
    group_id = _group_id(group_chat_id)
    undo = _STREAK_UNDO.pop((group_id, int(user_id), practice), None)
    if undo is None or undo[0] != scheduled_date:
        return False
    previous = undo[1]
    try:
        if previous is None:
            (_sb().table("streaks").delete()
             .eq("group_chat_id", group_id).eq("user_id", int(user_id)).eq("practice", practice)
             .execute())
        else:
            _sb().table("streaks").upsert(
                {"group_chat_id": group_id, "user_id": int(user_id), "practice": practice, **previous},
                on_conflict="group_chat_id,user_id,practice",
            ).execute()
        return True
    except Exception as exc:
        log.error("Could not restore streak for %s/%s: %s", user_id, practice, exc)
        return False


def _is_consecutive_scheduled(
    practice: str, prev_date: datetime.date, cur_date: datetime.date
) -> bool:
    """Return True if prev_date and cur_date are consecutive *scheduled*
    dates for this practice (e.g. consecutive Mon/Thu for sawm)."""
    allowed = get_scheduled_weekdays(practice)
    if not allowed or len(allowed) == 7:
        return False
    prev_was_scheduled = prev_date.weekday() in allowed
    cur_was_scheduled = cur_date.weekday() in allowed
    return prev_was_scheduled and cur_was_scheduled and (cur_date - prev_date).days > 0


def get_daily_streaks(
    user_ids: list[int], practices: list[str]
) -> dict[int, dict[str, int]]:
    """Return {user_id: {practice: current_streak}} in one query.

    Users/practices with no row in `streaks` are simply absent.
    """
    if not user_ids or not practices:
        return {}
    group_id = _group_id()
    try:
        rows = _select_all(lambda: (
            _sb()
            .table("streaks")
            .select("user_id,practice,current_streak")
            .in_("user_id", list(user_ids))
            .in_("practice", practices)
            .eq("group_chat_id", group_id)
            .order("user_id")
            .order("practice")
        ))
    except Exception as exc:
        log.warning("Could not load streaks: %s", exc)
        return {}
    result: dict[int, dict[str, int]] = {}
    for r in rows:
        result.setdefault(r["user_id"], {})[r["practice"]] = int(r["current_streak"])
    return result
