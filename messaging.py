"""
Message senders: check-in polls, nightly amal batch, daily and weekly
reports, and the per-prayer Qur'an ayah reminder.
"""

import asyncio
import datetime as _dt

from telegram import Bot

from config import GROUP_CHAT_ID, RESPONSE_WINDOW_HOURS, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE, BD_TZ, log
from practices import PRACTICES, NIGHTLY_AMAL_OPTIONS, JUMUAH_SUNNAHS
from db import save_active_poll, get_weekly_summary, get_daily_summary, get_daily_streaks, WEEKLY_MAX
from scheduling import schedule_poll_close
from quran import fetch_quran_ayah, format_ayah_message

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


def _format_jumuah_message() -> str:
    """Build the decorated Yaum al-Jumu'ah reminder text."""
    head = (
        "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌿 *Yaum al-Jumu'ah — Sunnahs & Recommended Acts* 🌿\n"
        "*The Best Day the Sun Rises Upon — Yaum al-Jumu'ah*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "May Allah grant us the ability to act upon these sunnahs. "
        "Ameen.\n\n"
        "📜 *Sunnahs of Yaum al-Jumu'ah:*\n"
    )
    body_lines = []
    for idx, (label, desc) in enumerate(JUMUAH_SUNNAHS, start=1):
        body_lines.append(f"  {idx:>2}. {label} — _{desc}_")
    body = "\n".join(body_lines)
    tail = (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📖 *\"Whoever perfects his ghusl on Friday, then goes to the masjid "
        "early, walks (rather than rides), sits close to the imam, and "
        "listens without crossing his legs or fidgeting — for every step, "
        "he gets the reward of fasting and praying at night for one year.\"*\n"
        "   — Tirmidhi\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📿 *اللَّهُمَّ صَلِّ عَلَى مُحَمَّدٍ وَعَلَى آلِ مُحَمَّدٍ*\n"
        "*(Allahumma salli 'ala Muhammadin wa 'ala ali Muhammad)*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return head + body + tail


async def send_jumuah_reminder(bot: Bot):
    """Send the Yaum al-Jumu'ah sunnah reminder to the group."""
    text = _format_jumuah_message()
    # Telegram message length cap is 4096 chars; this is well under that,
    # but be safe and split if needed.
    if len(text) <= 4096:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=text,
            parse_mode="Markdown",
        )
    else:
        # Fallback: send first chunk, then the rest
        first = text[:4000]
        rest = text[4000:]
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=first,
            parse_mode="Markdown",
        )
        if rest:
            await bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=rest,
                parse_mode="Markdown",
            )
    log.info("Sent Yaum al-Jumu'ah sunnah reminder.")

def _report_label(practice: str) -> str:
    """Return the display label used in reports."""
    if practice == "nightly_33_tasbeeh":
        return "33 - 33 - 34"
    if practice == "nightly_al_baqarah_last_2":
        return "Surat Al-Baqarah (Last 2)"
    p_info = PRACTICES.get(practice, {})
    return p_info.get("label", practice)


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
    rows = get_weekly_summary()
    if not rows:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Weekly Report:\n\n━━━━━━━━━━\nNo responses recorded this week yet.",
        )
        return

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
        # Sum of all practice counts. Each nightly sub-practice is its
        # own row (e.g., nightly_al_mulk + nightly_as_sajdah + ...
        # = up to 4 marks per night, up to 28 weekly).
        total = sum(data.values())
        user_weekly_marks[full_name] = total
        # Max possible marks = sum of WEEKLY_MAX for every practice the
        # user has a row for. Default to the full WEEKLY_MAX sum if no
        # rows present (e.g., brand new user with zero activity).
        present = set(data.keys())
        if present:
            user_max_marks[full_name] = sum(
                WEEKLY_MAX.get(p, 7) for p in present
            )
        else:
            user_max_marks[full_name] = sum(WEEKLY_MAX.values())

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
            max_n = WEEKLY_MAX.get(practice, 7)
            bar = "🟩" * completed + "⬜" * max(0, max_n - completed)
            block += f"  -- {label}:\n"
            block += f"  {bar} {completed}/{max_n}\n"
        block += "━━━━━━━━━━\n"
        blocks.append(block)

    chunks = _chunk_blocks("Weekly Report", "━━━━━━━━━━\n", blocks)
    for chunk in chunks:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=chunk,
        )
    log.info("Sent weekly report (%d message%s).", len(chunks), "s" if len(chunks) != 1 else "")

async def send_daily_report(bot: Bot):
    scheduled_practices, summary, report_start_iso, report_end_iso = get_daily_summary()

    if not summary:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Daily Report:\n\n━━━━━━━━━━\nNo responses recorded today yet.",
        )
        return

    # Compute per-user marks. Each amal = 1 mark, including each of the
    # 4 nightly sub-practices (so nightly amal can yield up to 4 marks).
    user_marks: dict[str, int] = {}
    for full_name, results in summary.items():
        marks = 0
        for practice in scheduled_practices:
            marks += 1 if results.get(practice, 0) else 0
        user_marks[full_name] = marks

    full_marks = len(scheduled_practices)
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
            streak_n = user_streaks.get(practice, 0)
            block += f"  -- {label}: {mark} ({streak_n})\n"
        block += "━━━━━━━━━━\n"
        blocks.append(block)

    chunks = _chunk_blocks("Daily Report", "━━━━━━━━━━\n", blocks)
    for chunk in chunks:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=chunk,
        )
    log.info("Sent daily report (%d message%s).", len(chunks), "s" if len(chunks) != 1 else "")


async def send_prayer_ayah(bot: Bot, prayer_name: str):
    """Send the Ayah-of-the-Hour reminder for a given prayer.

    Called by ``prayer_ayah_poll_job`` (see ``scheduler.py``) when the
    poll loop detects that ``prayer_name`` has just started.
    """
    try:
        bundle = await fetch_quran_ayah()
    except Exception as exc:  # network or parsing issue
        log.exception("Failed to fetch Quran ayah for %s: %s", prayer_name, exc)
        return
    text = format_ayah_message(prayer_name, bundle)
    await bot.send_message(chat_id=GROUP_CHAT_ID, text=text)
    log.info("Sent Ayah of the Hour for %s.", prayer_name)


# --------------------------------------------------------------------
#  Prayer-time polling loop
# --------------------------------------------------------------------
#
# Aladhan's timingsByCity returns *today's* times, so they change day
# to day and the JobQueue's run_daily (fixed clock-time) does not fit
# this use case. Instead, ``setup_scheduler`` registers a single
# repeating job that fires every 5 minutes; on each tick it checks
# which of the 5 prayers has just started (within the last 5 min) and
# dispatches one ayah reminder per prayer per day.

_PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
_DISPATCHED_TODAY: set[tuple[str, _dt.date]] = set()


async def _prayer_ayah_poll_tick(context):
    """Runs every 5 minutes; dispatches ayah reminders at prayer times."""
    from quran import fetch_prayer_times, dt_with_tz  # local import for clarity

    now = _dt.datetime.now(BD_TZ)
    today = now.date()

    # Reset dispatched tracker across days so the same prayer fires
    # again tomorrow.
    _DISPATCHED_TODAY.clear()

    try:
        timings = await fetch_prayer_times()
    except Exception as exc:
        log.warning("Prayer-time fetch failed; will retry next tick: %s", exc)
        return

    for prayer in _PRAYERS:
        prayer_at = dt_with_tz(getattr(timings, prayer), base=today)
        delta = (now - prayer_at).total_seconds()
        # 0 <= delta < 300 means the prayer started within the last 5 min
        if 0 <= delta < 300 and (prayer, today) not in _DISPATCHED_TODAY:
            _DISPATCHED_TODAY.add((prayer, today))
            log.info("Dispatching Ayah reminder for %s at %s", prayer, now)
            await send_prayer_ayah(context.bot, prayer)
