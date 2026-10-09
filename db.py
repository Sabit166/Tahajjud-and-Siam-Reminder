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

from __future__ import annotations

import datetime
import json
import secrets
import contextvars
from pathlib import Path
from typing import Optional

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_API_KEY, BD_TZ, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE, GROUP_CHAT_ID, log


# ============================================================
#  CLIENT
# ============================================================

# Lazy-init so the module imports cleanly even when env vars are
# missing (e.g. during `python -c "import db"` smoke tests). The first
# real DB call will raise a clear error if creds are wrong.
_client: Optional[Client] = None
_current_group_id: contextvars.ContextVar[int] = contextvars.ContextVar(
    "telegram_group_id", default=GROUP_CHAT_ID
)


def set_current_group(group_chat_id: int):
    """Set the tenant used by legacy helpers that do not receive a group id."""
    return _current_group_id.set(int(group_chat_id))


def current_group_id() -> int:
    return int(_current_group_id.get() or GROUP_CHAT_ID)


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
    """Verify connectivity and seed/load dynamic poll configurations."""
    try:
        # 1-row read forces a real round trip and surfaces auth errors.
        _sb().table("groups").select("chat_id").limit(1).execute()
        log.info("Database (Supabase) ready.")
    except Exception as exc:
        log.error("Supabase connectivity check failed: %s", exc)
        raise
    if GROUP_CHAT_ID:
        register_group(GROUP_CHAT_ID, "Legacy configured group")
        try:
            # Rows created by the single-group release used no tenant key.
            for table in ("responses", "active_polls", "streaks", "poll_configs"):
                _sb().table(table).update({"group_chat_id": GROUP_CHAT_ID}).eq("group_chat_id", 0).execute()
        except Exception as exc:
            log.warning("Legacy tenant backfill skipped: %s", exc)
    seed_initial_poll_configs()


def register_group(chat_id: int, title: str | None = None, username: str | None = None) -> dict:
    """Register/update a Telegram group. The configured legacy group is seeded too."""
    payload = {"chat_id": int(chat_id), "title": title, "username": username, "is_active": True}
    try:
        result = (_sb().table("groups").upsert(payload, on_conflict="chat_id").execute().data or [payload])[0]
        configs = _sb().table("poll_configs").select("id").eq("group_chat_id", int(chat_id)).limit(1).execute()
        if not configs.data:
            for item in DEFAULT_POLL_CONFIGS:
                _sb().table("poll_configs").upsert(
                    {**item, "group_chat_id": int(chat_id)},
                    on_conflict="group_chat_id,id",
                ).execute()
        return result
    except Exception as exc:
        log.warning("Could not register group %s: %s", chat_id, exc)
        return payload


def list_groups() -> list[dict]:
    try:
        return _sb().table("groups").select("*").eq("is_active", True).execute().data or []
    except Exception:
        return [{"chat_id": GROUP_CHAT_ID, "is_active": True}] if GROUP_CHAT_ID else []


def issue_setup_token(chat_id: int, user_id: int, ttl_minutes: int = 10) -> str:
    token = secrets.token_urlsafe(32)
    payload = {
        "token": token, "chat_id": int(chat_id), "issued_by": int(user_id),
        "expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ttl_minutes)).isoformat(),
    }
    _sb().table("setup_tokens").insert(payload).execute()
    return token


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

POLL_CONFIGS_FILE = Path(__file__).parent / "poll_configs.json"
_POLL_CONFIGS_CACHE: dict[str, dict] = {}
_POLL_CONFIGS_CACHE_GROUP: int | None = None
_SUPABASE_POLL_CONFIGS_AVAILABLE: bool | None = None

DEFAULT_POLL_CONFIGS: list[dict] = [
    {
        "id": "morning_dhikr",
        "title": "Morning Adhkar",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"],
        "weight": 8,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "sunrise",
        "prayer_offset_minutes": 0,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "fazr_jamaat",
        "title": "Fazr Jamaat",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Missed Jamaat"],
        "weight": 20,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "sunrise",
        "prayer_offset_minutes": 0,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "ishraq_salat",
        "title": "Ishraq Salat",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Missed"],
        "weight": 7,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "sunrise",
        "prayer_offset_minutes": 0,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "quran",
        "title": "Read 2 ayah of the Quran",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Missed"],
        "weight": 7,
        "time_type": "fixed",
        "fixed_time": "10:00",
        "prayer_name": None,
        "prayer_offset_minutes": 0,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "istighfar_100x",
        "title": "Istighfar 100x",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Missed"],
        "weight": 12,
        "time_type": "fixed",
        "fixed_time": "12:00",
        "prayer_name": None,
        "prayer_offset_minutes": 0,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "salawat_on_rasulullah",
        "title": "Salawat on Rasulullah",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"],
        "weight": 9,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "maghrib",
        "prayer_offset_minutes": 30,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "evening_dhikr",
        "title": "Evening Adhkar",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"],
        "weight": 8,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "maghrib",
        "prayer_offset_minutes": 30,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "tahajjud",
        "title": "Tahajjud Salat",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "InshaAllah, next time"],
        "weight": 15,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "fajr",
        "prayer_offset_minutes": -30,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "sawm",
        "title": "Sawm",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, fasting", "InshaAllah, next time"],
        "weight": 8,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "fajr",
        "prayer_offset_minutes": -30,
        "days_of_week": [0, 3],
        "is_active": True,
    },
    {
        "id": "surah_kahf",
        "title": "Surah Kahf",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"],
        "weight": 12,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "dhuhr",
        "prayer_offset_minutes": 0,
        "days_of_week": [4],
        "is_active": True,
    },
    {
        "id": "nightly_al_mulk",
        "title": "Surat Al-Mulk",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Missed"],
        "weight": 5,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "isha",
        "prayer_offset_minutes": 30,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "nightly_as_sajdah",
        "title": "Surat As-Sajdah",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Missed"],
        "weight": 5,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "isha",
        "prayer_offset_minutes": 30,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "nightly_al_baqarah_last_2",
        "title": "Surat Al-Baqarah (Last 2 ayats)",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Missed"],
        "weight": 10,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "isha",
        "prayer_offset_minutes": 30,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "nightly_33_tasbeeh",
        "title": "33x SubhanAllah, 33x Alhamdulillah, 34x AllahuAkbar",
        "poll_type": "amal_poll",
        "poll_options": ["Alhamdulillah, done", "Missed"],
        "weight": 3,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "isha",
        "prayer_offset_minutes": 30,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "daily_report",
        "title": "Daily Summary Report",
        "poll_type": "report",
        "poll_options": [],
        "weight": 0,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "maghrib",
        "prayer_offset_minutes": 2,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
    },
    {
        "id": "weekly_report",
        "title": "Weekly Summary Report",
        "poll_type": "report",
        "poll_options": [],
        "weight": 0,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "maghrib",
        "prayer_offset_minutes": 5,
        "days_of_week": [4],
        "is_active": True,
    },
    {
        "id": "jumuah_reminder",
        "title": "Yaum al-Jumu'ah Sunnahs Reminder",
        "poll_type": "reminder",
        "poll_options": [],
        "weight": 0,
        "time_type": "prayer_relative",
        "fixed_time": None,
        "prayer_name": "maghrib",
        "prayer_offset_minutes": 10,
        "days_of_week": [3],
        "is_active": True,
    },
]

def _load_local_poll_configs() -> dict[str, dict]:
    if POLL_CONFIGS_FILE.exists():
        try:
            with open(POLL_CONFIGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {item["id"]: item for item in data if "id" in item}
                elif isinstance(data, dict):
                    return data
        except Exception as exc:
            log.warning("Could not read local poll_configs.json: %s", exc)

    # Initialize from defaults
    result = {item["id"]: dict(item) for item in DEFAULT_POLL_CONFIGS}
    _save_local_poll_configs(result)
    return result

def _save_local_poll_configs(configs: dict[str, dict]):
    try:
        with open(POLL_CONFIGS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(configs.values()), f, indent=2, ensure_ascii=False)
    except Exception as exc:
        log.warning("Could not write local poll_configs.json: %s", exc)

def seed_initial_poll_configs():
    global _SUPABASE_POLL_CONFIGS_AVAILABLE, _POLL_CONFIGS_CACHE, _POLL_CONFIGS_CACHE_GROUP
    local_configs = _load_local_poll_configs()
    _POLL_CONFIGS_CACHE = local_configs.copy()
    _POLL_CONFIGS_CACHE_GROUP = current_group_id()

    try:
        res = _sb().table("poll_configs").select("id").eq("group_chat_id", current_group_id()).limit(1).execute()
        _SUPABASE_POLL_CONFIGS_AVAILABLE = True

        count_res = _sb().table("poll_configs").select("*").eq("group_chat_id", current_group_id()).execute()
        rows = count_res.data or []
        if not rows:
            log.info("Seeding initial %d poll configs to Supabase...", len(DEFAULT_POLL_CONFIGS))
            for item in DEFAULT_POLL_CONFIGS:
                _sb().table("poll_configs").upsert({**item, "group_chat_id": current_group_id()}, on_conflict="group_chat_id,id").execute()
            _POLL_CONFIGS_CACHE = {item["id"]: dict(item) for item in DEFAULT_POLL_CONFIGS}
        else:
            _POLL_CONFIGS_CACHE = {row["id"]: row for row in rows}
            _save_local_poll_configs(_POLL_CONFIGS_CACHE)
        log.info("Supabase poll_configs synchronized (%d items).", len(_POLL_CONFIGS_CACHE))
    except Exception as exc:
        _SUPABASE_POLL_CONFIGS_AVAILABLE = False
        log.info(
            "Supabase table 'poll_configs' not yet migrated; using local poll_configs.json (%d items). Notice: run supabase_schema.sql to enable Supabase cloud table sync.",
            len(_POLL_CONFIGS_CACHE),
        )

def get_all_poll_configs(active_only: bool = False, group_chat_id: int | None = None) -> list[dict]:
    global _POLL_CONFIGS_CACHE, _POLL_CONFIGS_CACHE_GROUP, _SUPABASE_POLL_CONFIGS_AVAILABLE
    group_id = _group_id(group_chat_id)
    if _SUPABASE_POLL_CONFIGS_AVAILABLE:
        try:
            res = _sb().table("poll_configs").select("*").eq("group_chat_id", group_id).order("created_at").execute()
            _POLL_CONFIGS_CACHE = {row["id"]: row for row in (res.data or [])}
            _POLL_CONFIGS_CACHE_GROUP = group_id
            if _POLL_CONFIGS_CACHE:
                _save_local_poll_configs(_POLL_CONFIGS_CACHE)
        except Exception as exc:
            log.warning("Failed to refresh poll_configs from Supabase, using cache: %s", exc)

    if _POLL_CONFIGS_CACHE_GROUP != group_id or not _POLL_CONFIGS_CACHE:
        _POLL_CONFIGS_CACHE = _load_local_poll_configs()
        _POLL_CONFIGS_CACHE_GROUP = group_id

    items = list(_POLL_CONFIGS_CACHE.values())
    if active_only:
        items = [i for i in items if i.get("is_active", True)]
    return items

def get_poll_config(poll_id: str, group_chat_id: int | None = None) -> Optional[dict]:
    group_id = _group_id(group_chat_id)
    if _POLL_CONFIGS_CACHE_GROUP != group_id:
        get_all_poll_configs(group_chat_id=group_id)
    return _POLL_CONFIGS_CACHE.get(poll_id)

def upsert_poll_config(data: dict, group_chat_id: int | None = None) -> dict:
    global _POLL_CONFIGS_CACHE, _POLL_CONFIGS_CACHE_GROUP, _SUPABASE_POLL_CONFIGS_AVAILABLE
    group_id = _group_id(group_chat_id)
    poll_id = data.get("id")
    if not poll_id:
        raise ValueError("Poll configuration must have an 'id'")

    if _POLL_CONFIGS_CACHE_GROUP != group_id:
        _POLL_CONFIGS_CACHE = {}
        _POLL_CONFIGS_CACHE_GROUP = group_id
    now_iso = datetime.datetime.now(BD_TZ).isoformat()
    clean_data = {
        "group_chat_id": group_id,
        "id": poll_id,
        "title": str(data.get("title", "")),
        "poll_type": str(data.get("poll_type", "amal_poll")),
        "poll_options": list(data.get("poll_options") or ["Alhamdulillah, done", "Incomplete/Missed"]),
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
        clean_data["created_at"] = _POLL_CONFIGS_CACHE.get(poll_id, {}).get("created_at", now_iso)

    _POLL_CONFIGS_CACHE[poll_id] = clean_data
    _save_local_poll_configs(_POLL_CONFIGS_CACHE)

    if _SUPABASE_POLL_CONFIGS_AVAILABLE:
        try:
            _sb().table("poll_configs").upsert(clean_data, on_conflict="group_chat_id,id").execute()
        except Exception as exc:
            log.warning("Could not persist poll_config %s to Supabase: %s", poll_id, exc)

    return clean_data

def delete_poll_config(poll_id: str, group_chat_id: int | None = None) -> bool:
    global _POLL_CONFIGS_CACHE, _POLL_CONFIGS_CACHE_GROUP, _SUPABASE_POLL_CONFIGS_AVAILABLE
    group_id = _group_id(group_chat_id)
    if _POLL_CONFIGS_CACHE_GROUP != group_id:
        get_all_poll_configs(group_chat_id=group_id)
    existed = poll_id in _POLL_CONFIGS_CACHE
    _POLL_CONFIGS_CACHE.pop(poll_id, None)
    _save_local_poll_configs(_POLL_CONFIGS_CACHE)

    if _SUPABASE_POLL_CONFIGS_AVAILABLE:
        try:
            _sb().table("poll_configs").delete().eq("id", poll_id).eq("group_chat_id", group_id).execute()
        except Exception as exc:
            log.warning("Could not delete poll_config %s from Supabase: %s", poll_id, exc)
    return existed

def get_practice_info(practice_key: str) -> dict:
    conf = get_poll_config(practice_key)
    if conf:
        return {
            "label": conf.get("title", practice_key),
            "title": conf.get("title", practice_key),
            "poll_options": conf.get("poll_options") or ["Alhamdulillah, done", "Incomplete/Missed"],
            "weight": conf.get("weight", 1),
            "poll_type": conf.get("poll_type", "amal_poll"),
        }
    from practices import PRACTICES
    p = PRACTICES.get(practice_key, {})
    return {
        "label": p.get("label", practice_key),
        "title": p.get("label", practice_key),
        "poll_options": p.get("poll_options", ["Alhamdulillah, done", "Incomplete/Missed"]),
        "weight": get_practice_weight(practice_key),
        "poll_type": "amal_poll",
    }

def get_practice_weight(practice_key: str) -> int:
    conf = get_poll_config(practice_key)
    if conf and conf.get("weight") is not None:
        return int(conf["weight"])
    from practices import AMAL_WEIGHTS
    return AMAL_WEIGHTS.get(practice_key, 1)

def get_scheduled_weekdays(practice_key: str) -> set[int]:
    conf = get_poll_config(practice_key)
    if conf and conf.get("days_of_week") is not None:
        return set(conf["days_of_week"])
    from practices import SCHEDULED_WEEKDAYS
    return SCHEDULED_WEEKDAYS.get(practice_key, {0, 1, 2, 3, 4, 5, 6})

def get_weekly_max(practice_key: str) -> int:
    days = get_scheduled_weekdays(practice_key)
    if days:
        return len(days)
    return WEEKLY_MAX.get(practice_key, 7)


# ============================================================
#  ACTIVE POLLS
# ============================================================

# In-process cache, mirrors the SQLite-era one. Writes go to Supabase
# immediately; the cache is purely an optimization for the hot path
# (poll answer -> lookup practice).
_ACTIVE_POLL_CACHE: dict[tuple[int, str], str] = {}


def save_active_poll(poll_id: str, practice_key: str, group_chat_id: int | None = None):
    group_id = _group_id(group_chat_id)
    _ACTIVE_POLL_CACHE[(group_id, poll_id)] = practice_key
    try:
        _sb().table("active_polls").upsert(
            {"poll_id": poll_id, "practice_key": practice_key, "group_chat_id": group_id}
        ).execute()
    except Exception as exc:
        log.warning("Could not save active poll %s: %s", poll_id, exc)


def get_poll_practice(poll_id: str, group_chat_id: int | None = None) -> Optional[str]:
    if (_group_id(group_chat_id), poll_id) in _ACTIVE_POLL_CACHE:
        return _ACTIVE_POLL_CACHE[(_group_id(group_chat_id), poll_id)]
    try:
        res = (
            _sb()
            .table("active_polls")
            .select("practice_key")
            .eq("poll_id", poll_id)
            .eq("group_chat_id", _group_id(group_chat_id))
            .limit(1)
            .execute()
        )
        if res.data:
            key = res.data[0]["practice_key"]
            _ACTIVE_POLL_CACHE[(_group_id(group_chat_id), poll_id)] = key
            return key
    except Exception as exc:
        log.warning("Could not read active poll %s: %s", poll_id, exc)
    return None


def get_poll_group(poll_id: str) -> int:
    try:
        row = (_sb().table("active_polls").select("group_chat_id").eq("poll_id", poll_id).limit(1).execute().data or [None])[0]
        return int(row["group_chat_id"]) if row else GROUP_CHAT_ID
    except Exception:
        return GROUP_CHAT_ID


def delete_active_poll(poll_id: str, group_chat_id: int | None = None):
    _ACTIVE_POLL_CACHE.pop((_group_id(group_chat_id), poll_id), None)
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
):
    """Upsert today's response for (user, practice). did_it is 0/1 for
    backward compatibility with the handler; we coerce to a real bool
    and store in the boolean column."""
    today = datetime.datetime.now(BD_TZ).date().isoformat()
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
            .eq("group_chat_id", _group_id())
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


def get_daily_summary(report_end: datetime.datetime | None = None) -> tuple[list[str], dict[str, dict[str, int]], str, str]:
    """Return (scheduled_practices, summary, report_start_iso, report_end_iso).

    summary is keyed by full_name -> {practice: did_it_bool}.
    """
    now = datetime.datetime.now(BD_TZ)
    report_end = report_end or now.replace(
        hour=DAILY_REPORT_HOUR,
        minute=DAILY_REPORT_MINUTE,
        second=0,
        microsecond=0,
    )
    if now < report_end:
        report_end -= datetime.timedelta(days=1)
    report_start = report_end - datetime.timedelta(days=1)

    # Build scheduled practices dynamically from active poll configs
    active_configs = get_all_poll_configs(active_only=True)
    scheduled_practices: list[str] = []
    for c in active_configs:
        if c.get("poll_type", "amal_poll") != "amal_poll":
            continue
        days = c.get("days_of_week") or [0, 1, 2, 3, 4, 5, 6]
        if (report_end.weekday() in days) or (report_start.weekday() in days):
            scheduled_practices.append(c["id"])

    # Fallback if no configs loaded yet
    if not scheduled_practices:
        scheduled_practices = [
            "evening_dhikr",
            "salawat_on_rasulullah",
            "nightly_al_mulk",
            "nightly_as_sajdah",
            "nightly_al_baqarah_last_2",
            "nightly_33_tasbeeh",
            "tahajjud",
            "morning_dhikr",
            "fazr_jamaat",
            "ishraq_salat",
            "quran",
            "istighfar_100x",
        ]
        if report_start.weekday() == 3:
            scheduled_practices.append("surah_kahf")
        if report_end.weekday() in (0, 3):
            scheduled_practices.append("sawm")

    try:
        res = (
            _sb()
            .table("responses")
            .select("full_name,practice,did_it")
            .gt("recorded_at", report_start.isoformat())
            .lte("recorded_at", report_end.isoformat())
            .in_("practice", scheduled_practices)
            .eq("group_chat_id", _group_id())
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

def get_streak(user_id: int, practice: str, group_chat_id: int | None = None) -> int:
    try:
        res = (
            _sb()
            .table("streaks")
            .select("current_streak")
            .eq("user_id", user_id)
            .eq("practice", practice)
            .eq("group_chat_id", _group_id(group_chat_id))
            .limit(1)
            .execute()
        )
        if res.data:
            return int(res.data[0]["current_streak"])
    except Exception as exc:
        log.warning("Could not read streak: %s", exc)
    return 0


def get_all_streaks(user_id: int, group_chat_id: int | None = None) -> dict[str, int]:
    try:
        res = (
            _sb()
            .table("streaks")
            .select("practice,current_streak")
            .eq("user_id", user_id)
            .eq("group_chat_id", _group_id(group_chat_id))
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
            .select("current_streak,longest_streak,last_done_date")
            .eq("user_id", user_id)
            .eq("practice", practice)
            .eq("group_chat_id", _group_id(group_chat_id))
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
            .eq("group_chat_id", _group_id())
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
                .eq("group_chat_id", _group_id())
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
    "quran_page": 7,
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