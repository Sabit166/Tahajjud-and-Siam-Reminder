"""
FastAPI web server providing REST API and mobile-optimized web interface
for managing Dhikr & Tahajjud Bot polls, schedules, and prayer-time triggers.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from telegram import Bot

from config import (
    BD_TZ,
    ADMIN_PIN,
    ADMIN_USER_IDS,
    GROUP_CHAT_ID,
    log,
)
from db import (
    get_all_poll_configs,
    get_poll_config,
    upsert_poll_config,
    delete_poll_config,
    get_practice_info,
)
from prayer_times import fetch_prayer_times, dt_with_tz

# Static directory
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

# Shared bot & job_queue references (populated by main.py on startup)
_bot_instance: Optional[Bot] = None
_job_queue_instance: Optional[Any] = None


def set_bot_instance(bot: Bot, job_queue: Any = None):
    global _bot_instance, _job_queue_instance
    _bot_instance = bot
    _job_queue_instance = job_queue
    log.info("Web server registered Telegram Bot and JobQueue instances.")


def get_bot() -> Bot:
    if _bot_instance is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram Bot instance is not yet ready.",
        )
    return _bot_instance


# ------------------------------------------------------------
#  FastAPI App
# ------------------------------------------------------------
app = FastAPI(
    title="Dhikr & Tahajjud Bot - Poll Manager",
    description="Mobile-friendly frontend API for configuring bot polls and prayer schedules.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------
#  Authentication Models & Dependency
# ------------------------------------------------------------
class AuthRequest(BaseModel):
    pin: Optional[str] = None
    telegram_user_id: Optional[int] = None
    telegram_init_data: Optional[str] = None


def verify_admin(authorization: Optional[str] = Header(None)) -> bool:
    """Simple authorization check using Bearer token (hashed or plain ADMIN_PIN)."""
    if not ADMIN_PIN:
        return True  # If no PIN configured, allow access
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    token = authorization.replace("Bearer ", "").strip()
    expected_token = hashlib.sha256(ADMIN_PIN.encode("utf-8")).hexdigest()
    if token == ADMIN_PIN or token == expected_token:
        return True

    # Check if token is authorized telegram user id
    if token.isdigit() and int(token) in ADMIN_USER_IDS:
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin credentials",
    )


# ------------------------------------------------------------
#  Poll Config Schemas
# ------------------------------------------------------------
class PollConfigPayload(BaseModel):
    id: Optional[str] = None
    title: str = Field(..., min_length=2, max_length=150)
    poll_type: str = Field("amal_poll", pattern="^(amal_poll|report|reminder)$")
    poll_options: list[str] = Field(default_factory=lambda: ["Alhamdulillah, done", "Incomplete/Missed"])
    weight: int = Field(1, ge=0, le=100)
    time_type: str = Field("prayer_relative", pattern="^(prayer_relative|fixed)$")
    fixed_time: Optional[str] = None  # "10:00"
    prayer_name: Optional[str] = None  # "fajr", "sunrise", etc.
    prayer_offset_minutes: int = Field(0, ge=-180, le=180)
    days_of_week: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    is_active: bool = True


# ------------------------------------------------------------
#  Prayer Times Calculation Helper
# ------------------------------------------------------------
async def get_today_prayer_timings() -> dict[str, str]:
    now = _dt.datetime.now(BD_TZ)
    today = now.date()
    timings = await fetch_prayer_times(date=today)
    return {
        "fajr": timings.fajr.strftime("%H:%M"),
        "sunrise": timings.sunrise.strftime("%H:%M"),
        "dhuhr": timings.dhuhr.strftime("%H:%M"),
        "asr": timings.asr.strftime("%H:%M"),
        "maghrib": timings.maghrib.strftime("%H:%M"),
        "isha": timings.isha.strftime("%H:%M"),
    }


def calculate_poll_time_for_today(conf: dict, timings_dict: dict[str, str]) -> tuple[Optional[str], Optional[str]]:
    """Returns (calculated_clock_time 'HH:MM', readable_description) for today."""
    now = _dt.datetime.now(BD_TZ)
    today = now.date()

    time_type = conf.get("time_type", "prayer_relative")
    if time_type == "fixed":
        fixed = conf.get("fixed_time")
        if fixed:
            return fixed, f"Fixed at {fixed}"
        return None, "Fixed time not set"

    prayer_name = (conf.get("prayer_name") or "").lower().strip()
    offset = int(conf.get("prayer_offset_minutes", 0))

    if prayer_name in timings_dict:
        raw_time = timings_dict[prayer_name]
        try:
            h, m = map(int, raw_time.split(":"))
            base_dt = BD_TZ.localize(_dt.datetime.combine(today, _dt.time(h, m)))
            calc_dt = base_dt + _dt.timedelta(minutes=offset)
            clock_str = calc_dt.strftime("%H:%M")

            if offset == 0:
                desc = f"At {prayer_name.capitalize()}"
            elif offset > 0:
                desc = f"{offset}m after {prayer_name.capitalize()}"
            else:
                desc = f"{abs(offset)}m before {prayer_name.capitalize()}"
            return clock_str, desc
        except Exception:
            pass

    return None, f"Relative to {prayer_name or 'prayer'}"


# ------------------------------------------------------------
#  API Endpoints
# ------------------------------------------------------------
@app.post("/api/auth/verify")
async def verify_auth(req: AuthRequest):
    """Verify Admin PIN or Telegram authentication."""
    if not ADMIN_PIN:
        return {"authenticated": True, "token": "open"}

    if req.pin and req.pin.strip() == ADMIN_PIN.strip():
        token = hashlib.sha256(ADMIN_PIN.encode("utf-8")).hexdigest()
        return {"authenticated": True, "token": token}

    if req.telegram_user_id and req.telegram_user_id in ADMIN_USER_IDS:
        return {"authenticated": True, "token": str(req.telegram_user_id)}

    raise HTTPException(status_code=401, detail="Invalid PIN or unauthorized user.")


@app.get("/api/prayer-times")
async def get_prayer_times_api():
    """Get today's prayer times for Dhaka (BD_TZ) with current time."""
    now = _dt.datetime.now(BD_TZ)
    try:
        timings = await get_today_prayer_timings()
        return {
            "date": now.strftime("%Y-%m-%d"),
            "now_time": now.strftime("%H:%M:%S"),
            "now_iso": now.isoformat(),
            "timezone": "Asia/Dhaka",
            "timings": timings,
        }
    except Exception as exc:
        log.exception("Prayer times API fetch failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/polls")
async def list_polls():
    """List all configured polls and schedules with calculated times for today."""
    configs = get_all_poll_configs()
    now = _dt.datetime.now(BD_TZ)
    today_weekday = now.weekday()

    try:
        timings = await get_today_prayer_timings()
    except Exception:
        timings = {}

    results = []
    for conf in configs:
        c = dict(conf)
        days = c.get("days_of_week") or [0, 1, 2, 3, 4, 5, 6]
        c["is_scheduled_today"] = today_weekday in days
        calc_time, readable_schedule = calculate_poll_time_for_today(c, timings)
        c["calculated_time_today"] = calc_time
        c["readable_schedule"] = readable_schedule
        results.append(c)

    return {"polls": results, "now_weekday": today_weekday}


@app.post("/api/polls")
async def create_poll(payload: PollConfigPayload, _=Depends(verify_admin)):
    """Create a new poll or scheduled item."""
    data = payload.model_dump()
    poll_id = data.get("id")
    if not poll_id:
        # Generate safe slug id
        slug = (
            data["title"]
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace("'", "")
            .replace('"', "")
        )
        safe_slug = "".join(ch for ch in slug if ch.isalnum() or ch == "_").strip("_")
        poll_id = safe_slug or f"poll_{int(_dt.datetime.now().timestamp())}"

    # Check for duplicate
    existing = get_poll_config(poll_id)
    if existing:
        poll_id = f"{poll_id}_{int(_dt.datetime.now().timestamp())}"

    data["id"] = poll_id
    saved = upsert_poll_config(data)
    log.info("Created new poll config: %s (%s)", saved["title"], saved["id"])
    return {"success": True, "poll": saved}


@app.put("/api/polls/{poll_id}")
async def update_poll(poll_id: str, payload: PollConfigPayload, _=Depends(verify_admin)):
    """Update an existing poll or scheduled item."""
    existing = get_poll_config(poll_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Poll '{poll_id}' not found")

    data = payload.model_dump()
    data["id"] = poll_id
    saved = upsert_poll_config(data)
    log.info("Updated poll config: %s (%s)", saved["title"], saved["id"])
    return {"success": True, "poll": saved}


@app.delete("/api/polls/{poll_id}")
async def remove_poll(poll_id: str, _=Depends(verify_admin)):
    """Delete a poll or scheduled item."""
    success = delete_poll_config(poll_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Poll '{poll_id}' not found")
    log.info("Deleted poll config: %s", poll_id)
    return {"success": True, "deleted_id": poll_id}


@app.post("/api/polls/{poll_id}/toggle")
async def toggle_poll_active(poll_id: str, _=Depends(verify_admin)):
    """Quickly toggle the active state of a poll."""
    conf = get_poll_config(poll_id)
    if not conf:
        raise HTTPException(status_code=404, detail=f"Poll '{poll_id}' not found")

    conf["is_active"] = not conf.get("is_active", True)
    saved = upsert_poll_config(conf)
    return {"success": True, "is_active": saved["is_active"]}


@app.post("/api/polls/{poll_id}/trigger")
async def trigger_poll_now(poll_id: str, _=Depends(verify_admin)):
    """Immediately dispatch a poll, report, or reminder to the Telegram group on demand."""
    from messaging import (
        send_checkin,
        send_nightly_amal,
        send_daily_report,
        send_weekly_report,
        send_jumuah_reminder,
    )

    bot = get_bot()
    job_queue = _job_queue_instance

    conf = get_poll_config(poll_id)
    title = conf.get("title", poll_id) if conf else poll_id

    try:
        if poll_id == "daily_report":
            await send_daily_report(bot)
        elif poll_id == "weekly_report":
            await send_weekly_report(bot)
        elif poll_id == "jumuah_reminder" or poll_id == "jumuah":
            await send_jumuah_reminder(bot)
        elif poll_id == "nightly_amal":
            await send_nightly_amal(bot, job_queue)
        else:
            await send_checkin(bot, poll_id, job_queue)

        log.info("Manually triggered poll '%s' (%s) from Web UI.", title, poll_id)
        return {
            "success": True,
            "message": f"Successfully sent '{title}' to Telegram!",
        }
    except Exception as exc:
        log.exception("Failed to dispatch poll '%s': %s", poll_id, exc)
        raise HTTPException(status_code=500, detail=f"Dispatch failed: {str(exc)}")


@app.get("/api/status")
async def get_system_status():
    """System health check and overview."""
    now = _dt.datetime.now(BD_TZ)
    polls = get_all_poll_configs()
    return {
        "bot_active": _bot_instance is not None,
        "group_chat_id": GROUP_CHAT_ID,
        "dhaka_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "total_polls": len(polls),
        "active_polls": sum(1 for p in polls if p.get("is_active", True)),
    }


# ------------------------------------------------------------
#  Static Files & SPA Frontend Serving
# ------------------------------------------------------------
@app.get("/")
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"message": "Frontend static file index.html is being prepared."})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
