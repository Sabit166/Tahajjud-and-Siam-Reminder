"""
Message senders: check-in polls, daily and weekly reports, and the
per-prayer hadith reminder.
"""

from __future__ import annotations

import datetime as _dt

from telegram import Bot

import time as _time

from config import BD_TZ, log
from db import save_active_poll, get_weekly_summary, get_daily_summary, get_daily_streaks, WEEKLY_MAX, current_group_id
from scheduling import schedule_poll_close
from hadith import fetch_hadith, format_hadith_message
from prayer_times import (
    ALADHAN_BASE,
    DEFAULT_CITY,
    DEFAULT_COUNTRY,
    DEFAULT_METHOD,
    fetch_prayer_times,
    dt_with_tz,
)

# ============================================================
#  MESSAGE SENDERS & POLL CLOSING
# ============================================================

def _chat_id() -> int:
    return current_group_id()


# ============================================================
#  AMAL DAY (MAGHRIB TO MAGHRIB)
# ============================================================
#
# An amal day D runs from Maghrib on the previous calendar day to Maghrib
# on day D. Its daily report is sent at Maghrib(D) + DAY_END_GRACE, and
# that is also when every poll belonging to day D closes. A poll belongs
# to the amal day in which it is sent.

DAY_END_GRACE = _dt.timedelta(minutes=2)   # = the daily report offset
_TIMINGS_TTL_SECONDS = 3600
_TIMINGS_CACHE: dict[_dt.date, tuple[float, object]] = {}


async def timings_for(day: _dt.date):
    """Prayer times for ``day``, cached for an hour. None if unavailable."""
    cached = _TIMINGS_CACHE.get(day)
    if cached and _time.monotonic() - cached[0] < _TIMINGS_TTL_SECONDS:
        return cached[1]
    try:
        timings = await fetch_prayer_times(date=day)
    except Exception as exc:
        log.warning("Prayer-time fetch for %s failed: %s", day, exc)
        return cached[1] if cached else None
    _TIMINGS_CACHE[day] = (_time.monotonic(), timings)
    return timings


async def maghrib_at(day: _dt.date) -> _dt.datetime:
    """Maghrib on ``day``; falls back to the nearest known day's time, then 18:00."""
    timings = await timings_for(day)
    if timings is None and _TIMINGS_CACHE:
        nearest = min(_TIMINGS_CACHE, key=lambda d: abs((d - day).days))
        timings = _TIMINGS_CACHE[nearest][1]
    if timings is None:
        log.warning("No prayer times for %s; assuming Maghrib at 18:00.", day)
        return BD_TZ.localize(_dt.datetime.combine(day, _dt.time(18, 0)))
    return dt_with_tz(timings.maghrib, base=day)


async def amal_day_of(moment: _dt.datetime) -> _dt.date:
    """The amal day that ``moment`` falls in (the day whose Maghrib ends it)."""
    moment = moment.astimezone(BD_TZ)
    day = moment.date()
    return day if moment < await maghrib_at(day) else day + _dt.timedelta(days=1)


async def amal_day_end(day: _dt.date) -> _dt.datetime:
    """When amal day ``day`` ends: its report is sent and its polls close."""
    return await maghrib_at(day) + DAY_END_GRACE


async def poll_close_time(sent_at: _dt.datetime) -> _dt.datetime:
    """A poll closes when the amal day it was sent in ends."""
    return await amal_day_end(await amal_day_of(sent_at))


async def restore_poll_closings(bot: Bot, job_queue) -> None:
    """Re-arm poll closing after a restart (close jobs live only in memory).

    Polls whose amal day has already ended are closed now; the rest get a
    close job at their day's end. Polls recorded before message ids were
    stored cannot be closed in Telegram, but late answers to them are
    still ignored by the answer handler.
    """
    from db import list_active_polls, delete_active_poll
    from handlers import _parse_ts
    from scheduling import close_poll_job

    now = _dt.datetime.now(BD_TZ)
    closed = scheduled = skipped = 0
    for row in list_active_polls():
        if not row.get("message_id"):
            skipped += 1
            continue
        sent_at = _parse_ts(row.get("created_at")) or now
        close_at = await poll_close_time(sent_at)
        chat_id, message_id = int(row["group_chat_id"]), int(row["message_id"])
        if close_at <= now:
            try:
                await bot.stop_poll(chat_id=chat_id, message_id=message_id)
            except Exception as exc:  # already closed or deleted in Telegram
                log.info("Could not close overdue poll %s: %s", row["poll_id"], exc)
            delete_active_poll(row["poll_id"], chat_id)
            closed += 1
        elif job_queue is not None:
            job_queue.run_once(
                close_poll_job,
                when=close_at,
                data={"chat_id": chat_id, "message_id": message_id, "label": row["practice_key"]},
                name=f"close_poll_{row['practice_key']}_{message_id}",
            )
            scheduled += 1
    log.info(
        "Poll closing restored: %d closed (overdue), %d scheduled, %d without message id.",
        closed, scheduled, skipped,
    )


def poll_send_time(conf: dict, day: _dt.date, timings) -> _dt.datetime | None:
    """When an amal poll config is sent on calendar ``day`` (ignores weekdays)."""
    if conf.get("time_type", "prayer_relative") == "fixed":
        try:
            hour, minute = (int(x) for x in str(conf.get("fixed_time") or "").strip().split(":")[:2])
            return BD_TZ.localize(_dt.datetime.combine(day, _dt.time(hour, minute)))
        except Exception as exc:
            log.warning("Invalid fixed_time '%s' for %s: %s", conf.get("fixed_time"), conf.get("id"), exc)
            return None
    prayer = str(conf.get("prayer_name") or "").lower().strip()
    if not prayer or timings is None:
        return None
    try:
        offset = _dt.timedelta(minutes=int(conf.get("prayer_offset_minutes", 0)))
        return dt_with_tz(getattr(timings, prayer), base=day) + offset
    except Exception as exc:
        log.warning("Error calculating prayer time for %s: %s", conf.get("id"), exc)
        return None


async def practices_for_amal_day(day: _dt.date) -> list[str]:
    """Active amal polls of the current group that are sent during amal day ``day``.

    A poll is in day D if, on the previous or the same calendar day (when
    that weekday is selected), it is sent between Maghrib(D-1) and
    Maghrib(D). So Monday's pre-dawn Sawm counts only for Monday, and a
    poll sent after Maghrib on Thursday counts for Friday.
    """
    from db import get_all_poll_configs
    start = await maghrib_at(day - _dt.timedelta(days=1))
    end = await maghrib_at(day)
    today_timings = await timings_for(day)
    practices = []
    for conf in get_all_poll_configs(active_only=True):
        days = conf.get("days_of_week")
        days = [0, 1, 2, 3, 4, 5, 6] if days is None else days
        for cal_day in (day - _dt.timedelta(days=1), day):
            if cal_day.weekday() not in days:
                continue
            sent = poll_send_time(conf, cal_day, await timings_for(cal_day) or today_timings)
            if sent is not None and start <= sent < end:
                practices.append(conf["id"])
                break
    return practices

async def send_checkin(bot: Bot, practice_key: str, job_queue=None):
    from db import get_practice_info
    p = get_practice_info(practice_key)
    question = p.get("label") or p.get("title", practice_key)
    options = p.get("poll_options") or ["Alhamdulillah, done", "Incomplete/Missed"]
    sent_message = await bot.send_poll(
        chat_id=_chat_id(),
        question=question,
        options=options,
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    if sent_message.poll:
        save_active_poll(sent_message.poll.id, practice_key, message_id=sent_message.message_id)

    # The poll stays open until its amal day ends (Maghrib + 2 min).
    close_at = await poll_close_time(_dt.datetime.now(BD_TZ))
    schedule_poll_close(job_queue, sent_message, question, close_at)
    log.info(f"Sent check-in poll: {question} (open until {close_at:%Y-%m-%d %H:%M})")


def _report_label(practice: str) -> str:
    """Return the display label used in reports."""
    if practice == "nightly_33_tasbeeh":
        return "33 - 33 - 34"
    if practice == "nightly_al_baqarah_last_2":
        return "Surat Al-Baqarah (Last 2)"
    from db import get_practice_info
    p_info = get_practice_info(practice)
    return p_info.get("label") or p_info.get("title", practice)


# Telegram caps send_message at 4096 chars. We split a long report by
# packing whole user-blocks into messages that stay under the cap.
TG_MAX_LEN = 4000  # leave headroom for headers and trailing whitespace


def _chunk_blocks(title: str, header_separator: str, blocks: list[str]) -> list[str]:
    """Pack `blocks` into one-or-more messages under Telegram's char cap.

    Each message is prefixed with ``"<title> (i/N):\n"`` (or just the
    title when there's only one chunk). A block is never split — if a
    single block is larger than the cap it goes out on its own and we
    log a warning so it can be fixed in source.
    """
    if not blocks:
        return [f"{title}:\n\n{header_separator}No data."]

    # Peek at total length once. If it fits, no header suffix needed.
    total = sum(len(b) for b in blocks)
    if total <= TG_MAX_LEN:
        return [f"{title}:\n\n{header_separator}" + "".join(blocks).rstrip("\n")]

    # Need multi-message. Greedily pack blocks while each message stays
    # under the cap; we leave room for "(i/N)" header plus separator.
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    n = len(blocks)
    for blk in blocks:
        if len(blk) > TG_MAX_LEN:
            log.warning("Block of %d chars exceeds Telegram limit; sending as-is.", len(blk))
        # Worst-case header: "(99/99):\n\n" + separator ~= 16 chars
        budget = TG_MAX_LEN - 16 - len(header_separator)
        if current and current_len + len(blk) > budget:
            chunks.append(current)
            current = [blk]
            current_len = len(blk)
        else:
            current.append(blk)
            current_len += len(blk)
    if current:
        chunks.append(current)

    n_chunks = len(chunks)
    out: list[str] = []
    for i, c in enumerate(chunks, start=1):
        head = f"{title} ({i}/{n_chunks}):\n\n{header_separator}"
        out.append(head + "".join(c).rstrip("\n"))
    return out


async def send_weekly_report(bot: Bot, report_day: _dt.date | None = None):
    """Weekly leaderboard for the 7 amal days ending with ``report_day``."""
    from db import get_all_poll_configs

    # Only this group's own amal polls count; answers to polls the group
    # has since deleted are left out (their weights no longer exist).
    group_amals = {c["id"] for c in get_all_poll_configs()}
    rows = [r for r in get_weekly_summary(report_day) if r[2] in group_amals]
    if not rows:
        await bot.send_message(
            chat_id=_chat_id(),
            text="Weekly Report:\n\n━━━━━━━━━━\nNo responses recorded this week yet.",
        )
        return

    from db import get_practice_weight, get_weekly_max

    # Group rows by member id (names can repeat). Each amal counts as its
    # OWN mark — no grouping/collapsing.
    names: dict[int, str] = {}
    user_data: dict[int, dict[str, int]] = {}
    for user_id, full_name, practice, completed, total in rows:
        names[user_id] = full_name
        user_data.setdefault(user_id, {})[practice] = int(completed)

    # Per-user weekly marks and the maximum for the amals they took part in.
    user_weekly_marks: dict[int, int] = {}
    user_max_marks: dict[int, int] = {}
    for user_id, data in user_data.items():
        user_weekly_marks[user_id] = sum(
            completed * get_practice_weight(practice)
            for practice, completed in data.items()
        )
        user_max_marks[user_id] = sum(
            get_weekly_max(p) * get_practice_weight(p) for p in data
        )

    # Sort users by marks desc, then name asc for stable tie-breaking.
    sorted_users = sorted(
        user_weekly_marks.items(),
        key=lambda kv: (-kv[1], names[kv[0]], kv[0]),
    )

    blocks: list[str] = []
    for rank, (user_id, marks) in enumerate(sorted_users, start=1):
        block = f"{rank}. {names[user_id]} (Marks {marks}/{user_max_marks[user_id]})\n"
        for practice, completed in sorted(user_data[user_id].items()):
            label = _report_label(practice)
            max_n = get_weekly_max(practice)
            weight = get_practice_weight(practice)
            bar = "🟩" * completed + "⬜" * max(0, max_n - completed)
            block += f"  -- {label}:\n"
            block += f"  {bar} {completed}/{max_n} ({completed * weight}/{max_n * weight} marks)\n"
        block += "━━━━━━━━━━\n"
        blocks.append(block)

    chunks = _chunk_blocks("Weekly Report", "━━━━━━━━━━\n", blocks)
    for chunk in chunks:
        await bot.send_message(
            chat_id=_chat_id(),
            text=chunk,
        )
    log.info("Sent weekly report (%d message%s).", len(chunks), "s" if len(chunks) != 1 else "")

async def send_daily_report(bot: Bot, report_day: _dt.date | None = None):
    """Daily leaderboard for one amal day (previous Maghrib to this Maghrib).

    Only polls actually sent during that amal day are scored, and only
    answers recorded for that amal day count.
    """
    report_day = report_day or _dt.datetime.now(BD_TZ).date()
    scheduled_practices = await practices_for_amal_day(report_day)
    names, summary = get_daily_summary(report_day, scheduled_practices)

    if not summary:
        await bot.send_message(
            chat_id=_chat_id(),
            text="Daily Report:\n\n━━━━━━━━━━\nNo responses recorded today yet.",
        )
        return

    # Compute weighted per-user marks. Each practice contributes its configured weight.
    from db import get_practice_weight
    user_marks: dict[int, int] = {}
    for user_id, results in summary.items():
        user_marks[user_id] = sum(
            get_practice_weight(practice)
            for practice in scheduled_practices
            if results.get(practice, 0)
        )

    full_marks = sum(
        get_practice_weight(practice) for practice in scheduled_practices
    )
    sorted_users = sorted(user_marks.items(), key=lambda kv: (-kv[1], names[kv[0]], kv[0]))

    # Current streaks, so each practice line can show "✅ (N)" / "❌ (0)".
    # Missed entries reset to 0; done entries are at least 1.
    streaks_by_user = get_daily_streaks(list(summary), scheduled_practices)

    # Build one block per user (never split a user across messages),
    # then pack blocks into messages that stay under Telegram's 4096-char
    # limit. Each message gets a "(i/N)" header.
    blocks: list[str] = []
    for rank, (user_id, marks) in enumerate(sorted_users, start=1):
        user_streaks = streaks_by_user.get(user_id, {})
        block = f"{rank}. {names[user_id]} (Marks {marks}/{full_marks})\n"
        for practice in scheduled_practices:
            label = _report_label(practice)
            did_it = summary[user_id].get(practice, 0)
            mark = "✅" if did_it else "❌"
            streak_n = user_streaks.get(practice, 0) if did_it else 0
            block += f"  -- {label}: {mark} ({streak_n})\n"
        block += "━━━━━━━━━━\n"
        blocks.append(block)

    chunks = _chunk_blocks("Daily Report", "━━━━━━━━━━\n", blocks)
    for chunk in chunks:
        await bot.send_message(
            chat_id=_chat_id(),
            text=chunk,
        )
    log.info("Sent daily report (%d message%s).", len(chunks), "s" if len(chunks) != 1 else "")


async def send_prayer_hadith(bot: Bot, prayer_name: str):
    """Send one HadithAPI hadith for a given prayer time.

    Called by ``prayer_hadith_poll_job`` (see ``scheduler.py``) when the
    poll loop detects that ``prayer_name`` has just started.
    """
    try:
        hadith = await fetch_hadith()
    except Exception as exc:  # network or parsing issue
        log.exception("Failed to fetch hadith for %s: %s", prayer_name, exc)
        return
    text = format_hadith_message(prayer_name, hadith)
    await bot.send_message(chat_id=_chat_id(), text=text)
    log.info("Sent hadith for %s.", prayer_name)


# --------------------------------------------------------------------
#  Prayer-time polling loop
# --------------------------------------------------------------------
#
# Aladhan's timingsByCity returns *today's* times, so they change day
# to day and the JobQueue's run_daily (fixed clock-time) does not fit
# this use case. Instead, ``setup_scheduler`` registers a single
# repeating job that fires every 5 minutes; on each tick it checks
# which of the 5 prayers has just started (within the last 5 min) and
# dispatches one hadith reminder per prayer per day.

_PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
_DISPATCHED_TODAY: set[tuple[int, str, _dt.date]] = set()
_SCHEDULED_EVENTS: set[tuple[int, str, _dt.date]] = set()


# Built-in reports sent to every active group. They are not configurable
# from the dashboard: (event, prayer, offset minutes, weekdays).
DAILY_REPORT_EVENT = "report:daily"
WEEKLY_REPORT_EVENT = "report:weekly"
BUILTIN_REPORTS = (
    (DAILY_REPORT_EVENT, "maghrib", 2, (0, 1, 2, 3, 4, 5, 6)),
    (WEEKLY_REPORT_EVENT, "maghrib", 5, (4,)),  # Friday
)


async def _dispatch_scheduled_event(context, event: str, event_at: _dt.datetime):
    """Dispatch one event after it occurs, at most once for its local date.

    ``event`` is either a built-in report event or an amal poll config id.
    """
    key = (current_group_id(), event, event_at.date())
    if key in _SCHEDULED_EVENTS:
        return
    now = _dt.datetime.now(BD_TZ)
    delta = (now - event_at).total_seconds()
    if 0 <= delta < 120:
        _SCHEDULED_EVENTS.add(key)
        bot = context.bot
        if event == DAILY_REPORT_EVENT:
            await send_daily_report(bot, report_day=event_at.date())
        elif event == WEEKLY_REPORT_EVENT:
            await send_weekly_report(bot, report_day=event_at.date())
        else:
            await send_checkin(bot, event, context.job_queue)
        log.info("Dispatched scheduled event %s at %s.", event, event_at)


async def fetch_today_timings():
    """Fetch today's (Dhaka) prayer times once for a whole job run.

    Every group uses the same city, so the per-minute and per-5-minute
    jobs fetch once and hand the result to each group's tick. Returns
    None on failure; the job simply retries on its next run.
    """
    today = _dt.datetime.now(BD_TZ).date()
    try:
        timings = await fetch_prayer_times(date=today)
    except Exception as exc:
        log.warning("Prayer-time fetch failed; will retry next run: %s", exc)
        return None
    _TIMINGS_CACHE[today] = (_time.monotonic(), timings)  # reuse for amal-day math
    return timings


async def _prayer_schedule_tick(context, timings=None):
    """Dispatch the group's amal polls and the built-in daily/weekly reports."""
    now = _dt.datetime.now(BD_TZ)
    today = now.date()
    if timings is None:
        timings = await fetch_today_timings()
        if timings is None:
            return

    def at(name: str, offset: _dt.timedelta = _dt.timedelta()) -> _dt.datetime:
        return dt_with_tz(getattr(timings, name.lower()), base=today) + offset

    from db import get_all_poll_configs
    configs = get_all_poll_configs(active_only=True)

    events: list[tuple[str, _dt.datetime]] = []

    for conf in configs:
        days = conf.get("days_of_week")
        days = [0, 1, 2, 3, 4, 5, 6] if days is None else days
        if today.weekday() in days:
            sent_at = poll_send_time(conf, today, timings)
            if sent_at is not None:
                events.append((conf["id"], sent_at))

    # Reports go to every group, including ones without any amal polls.
    # The daily report time equals amal_day_end(today), when polls close.
    for event, prayer, offset_mins, weekdays in BUILTIN_REPORTS:
        if today.weekday() in weekdays:
            events.append((event, at(prayer, _dt.timedelta(minutes=offset_mins))))

    for event, event_at in events:
        await _dispatch_scheduled_event(context, event, event_at)

    # Keep only recent dates in memory while allowing late-started jobs to fire.
    cutoff = today - _dt.timedelta(days=2)
    _SCHEDULED_EVENTS.difference_update(
        {key for key in _SCHEDULED_EVENTS if key[2] < cutoff}
    )


async def _prayer_hadith_poll_tick(context, timings=None):
    """Runs every 5 minutes; dispatches hadith reminders at prayer times."""
    now = _dt.datetime.now(BD_TZ)
    today = now.date()

    # Reset dispatched tracker across days so the same prayer fires
    # again tomorrow.
    cutoff = today - _dt.timedelta(days=2)
    _DISPATCHED_TODAY.difference_update(
        {key for key in _DISPATCHED_TODAY if key[2] < cutoff}
    )

    if timings is None:
        timings = await fetch_today_timings()
        if timings is None:
            return

    for prayer in _PRAYERS:
        prayer_at = dt_with_tz(getattr(timings, prayer), base=today)
        delta = (now - prayer_at).total_seconds()
        # 0 <= delta < 300 means the prayer started within the last 5 min
        key = (current_group_id(), prayer, today)
        if 0 <= delta < 300 and key not in _DISPATCHED_TODAY:
            _DISPATCHED_TODAY.add(key)
            log.info("Dispatching hadith reminder for %s at %s", prayer, now)
            await send_prayer_hadith(context.bot, prayer)
