"""
Message senders: check-in polls, daily and weekly reports, and the
per-prayer hadith reminder.
"""

from __future__ import annotations

import datetime as _dt

from telegram import Bot

from config import RESPONSE_WINDOW_HOURS, BD_TZ, log
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
        save_active_poll(sent_message.poll.id, practice_key)

    if RESPONSE_WINDOW_HOURS > 0:
        log.info(f"Check-in poll for {question} will stay open for {RESPONSE_WINDOW_HOURS} hour(s).")
        schedule_poll_close(job_queue, sent_message, question)
    log.info(f"Sent check-in poll: {question}")


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


async def send_weekly_report(bot: Bot):
    from db import get_all_poll_configs

    # Only this group's own amal polls count; answers to polls the group
    # has since deleted are left out (their weights no longer exist).
    group_amals = {c["id"] for c in get_all_poll_configs()}
    rows = [r for r in get_weekly_summary() if r[1] in group_amals]
    if not rows:
        await bot.send_message(
            chat_id=_chat_id(),
            text="Weekly Report:\n\n━━━━━━━━━━\nNo responses recorded this week yet.",
        )
        return

    from db import get_practice_weight, get_weekly_max

    # Group rows by user. Each amal (including each nightly sub-practice)
    # counts as its OWN mark — no grouping/collapsing.
    user_data: dict[str, dict[str, int]] = {}
    for full_name, practice, completed, total in rows:
        user_data.setdefault(full_name, {})
        user_data[full_name][practice] = int(completed)

    # Per-user weekly marks (Q8=A: one combined leaderboard).
    user_weekly_marks: dict[str, int] = {}
    user_max_marks: dict[str, int] = {}
    for full_name, data in user_data.items():
        # Sum completed practices using each amal's leaderboard weight.
        total = sum(
            completed * get_practice_weight(practice)
            for practice, completed in data.items()
        )
        user_weekly_marks[full_name] = total
        # Max possible weighted marks = sum of each practice's weekly
        # limit multiplied by its leaderboard weight.
        present = set(data.keys())
        if present:
            user_max_marks[full_name] = sum(
                get_weekly_max(p) * get_practice_weight(p)
                for p in present
            )
        else:
            active_amals = [
                c["id"]
                for c in get_all_poll_configs(active_only=True)
                if c.get("poll_type", "amal_poll") == "amal_poll"
            ]
            user_max_marks[full_name] = sum(
                get_weekly_max(p) * get_practice_weight(p)
                for p in active_amals
            ) or 100

    # Sort users by marks desc, then name asc for stable tie-breaking.
    sorted_users = sorted(
        user_weekly_marks.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )

    blocks: list[str] = []
    for rank, (full_name, marks) in enumerate(sorted_users, start=1):
        block = f"{rank}. {full_name} (Marks {marks}/{user_max_marks[full_name]})\n"
        for practice, completed in sorted(user_data[full_name].items()):
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

async def send_daily_report(bot: Bot, report_end: _dt.datetime | None = None):
    scheduled_practices, summary, report_start_iso, report_end_iso = get_daily_summary(report_end)

    if not summary:
        await bot.send_message(
            chat_id=_chat_id(),
            text="Daily Report:\n\n━━━━━━━━━━\nNo responses recorded today yet.",
        )
        return

    # Compute weighted per-user marks. Each practice contributes its configured weight.
    from db import get_practice_weight
    user_marks: dict[str, int] = {}
    for full_name, results in summary.items():
        marks = 0
        for practice in scheduled_practices:
            if results.get(practice, 0):
                marks += get_practice_weight(practice)
        user_marks[full_name] = marks

    full_marks = sum(
        get_practice_weight(practice) for practice in scheduled_practices
    )
    sorted_users = sorted(user_marks.items(), key=lambda kv: (-kv[1], kv[0]))

    # Pull current streaks for users who responded in this report window,
    # so each practice line can show "✅ (🔥N)" / "❌ (🔥0)". Missed
    # entries reset to 0; done entries are at least 1.
    streaks_by_user = get_daily_streaks(
        report_start_iso, report_end_iso, scheduled_practices,
    )

    # Build one block per user (never split a user across messages),
    # then pack blocks into messages that stay under Telegram's 4096-char
    # limit. Each message gets a "(i/N)" header.
    blocks: list[str] = []
    for rank, (full_name, marks) in enumerate(sorted_users, start=1):
        user_streaks = streaks_by_user.get(full_name, {})
        block = f"{rank}. {full_name} (Marks {marks}/{full_marks})\n"
        for practice in scheduled_practices:
            label = _report_label(practice)
            did_it = summary[full_name].get(practice, 0)
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
            await send_daily_report(bot, report_end=event_at)
        elif event == WEEKLY_REPORT_EVENT:
            await send_weekly_report(bot)
        else:
            await send_checkin(bot, event, context.job_queue)
        log.info("Dispatched scheduled event %s at %s.", event, event_at)


async def fetch_today_timings():
    """Fetch today's (Dhaka) prayer times once for a whole job run.

    Every group uses the same city, so the per-minute and per-5-minute
    jobs fetch once and hand the result to each group's tick. Returns
    None on failure; the job simply retries on its next run.
    """
    try:
        return await fetch_prayer_times(date=_dt.datetime.now(BD_TZ).date())
    except Exception as exc:
        log.warning("Prayer-time fetch failed; will retry next run: %s", exc)
        return None


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
        if days is None:
            days = [0, 1, 2, 3, 4, 5, 6]
        if today.weekday() not in days:
            continue

        cid = conf["id"]
        time_type = conf.get("time_type", "prayer_relative")

        if time_type == "fixed" and conf.get("fixed_time"):
            try:
                parts = str(conf["fixed_time"]).strip().split(":")
                hour, minute = int(parts[0]), int(parts[1])
                target_naive = _dt.datetime.combine(today, _dt.time(hour, minute))
                target_dt = BD_TZ.localize(target_naive)
                events.append((cid, target_dt))
            except Exception as exc:
                log.warning("Invalid fixed_time '%s' for %s: %s", conf.get("fixed_time"), cid, exc)
        elif time_type == "prayer_relative" and conf.get("prayer_name"):
            p_name = str(conf["prayer_name"]).lower().strip()
            offset_mins = int(conf.get("prayer_offset_minutes", 0))
            try:
                target_dt = at(p_name, _dt.timedelta(minutes=offset_mins))
                events.append((cid, target_dt))
            except Exception as exc:
                log.warning("Error calculating prayer time for %s: %s", cid, exc)

    # Reports go to every group, including ones without any amal polls.
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
