"""
Message senders: check-in polls, nightly amal batch, daily and weekly
reports.
"""

import asyncio

from telegram import Bot

from config import GROUP_CHAT_ID, RESPONSE_WINDOW_HOURS, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE, BD_TZ, log
from practices import PRACTICES, NIGHTLY_AMAL_OPTIONS, JUMUAH_SUNNAHS
from db import save_active_poll, get_weekly_summary, get_daily_summary, get_daily_streaks, WEEKLY_MAX
from scheduling import schedule_poll_close

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


async def send_weekly_report(bot: Bot):
    rows = get_weekly_summary()
    if not rows:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Weekly Report:\n\n━━━━━━━━━━\nNo responses recorded this week yet.",
            parse_mode="Markdown"
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

    report = "Weekly Report:\n\n━━━━━━━━━━\n"

    for full_name in sorted(user_data):
        report += f"*{full_name} (Marks {user_weekly_marks[full_name]}/{user_max_marks[full_name]})*\n"
        for practice, completed in sorted(user_data[full_name].items()):
            label = _report_label(practice)
            max_n = WEEKLY_MAX.get(practice, 7)
            bar = "🟩" * completed + "⬜" * max(0, max_n - completed)
            report += f"  -- {label}:\n"
            report += f"  {bar} {completed}/{max_n}\n"
        report += "━━━━━━━━━━\n"

    report = report.rstrip("\n")

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=report,
        parse_mode="Markdown"
    )

    # Weekly leaderboard (Q8=A: combined across all categories).
    await send_weekly_leaderboard(bot, user_weekly_marks, user_max_marks)
    log.info("Sent weekly report.")


async def send_weekly_leaderboard(bot: Bot, user_weekly_marks: dict, user_max_marks: dict):
    """Send a separate weekly leaderboard message (combined ranking,
    ties share positions)."""
    if not user_weekly_marks:
        return
    sorted_users = sorted(user_weekly_marks.items(), key=lambda kv: -kv[1])
    last_score = None
    last_pos = 0
    board_lines: list[str] = []
    for idx, (name, score) in enumerate(sorted_users, start=1):
        if score != last_score:
            last_pos = idx
            last_score = score
        board_lines.append(f"{last_pos}. {name} ({score}/{user_max_marks.get(name, '?')})")
    text = "Weekly Leaderboard:\n━━━━━━━━━\n" + "\n".join(board_lines)
    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=text,
        parse_mode="Markdown",
    )
    log.info("Sent weekly leaderboard.")

async def send_daily_report(bot: Bot):
    scheduled_practices, summary, report_start_iso, report_end_iso = get_daily_summary()

    if not summary:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Daily Report:\n\n━━━━━━━━━━\nNo responses recorded today yet.",
            parse_mode="Markdown"
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

    report = "Daily Report:\n\n━━━━━━━━━━\n"

    for full_name in sorted(summary):
        user_streaks = streaks_by_user.get(full_name, {})
        report += f"*{full_name} (Marks {user_marks[full_name]}/{full_marks})*\n"
        for practice in scheduled_practices:
            label = _report_label(practice)
            did_it = summary[full_name].get(practice, 0)
            mark = "✅" if did_it else "❌"
            streak_n = user_streaks.get(practice, 0)
            report += f"  -- {label}: {mark} (🔥{streak_n})\n"
        report += "━━━━━━━━━━\n"

    report = report.rstrip("\n")

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=report,
        parse_mode="Markdown"
    )

    await send_daily_leaderboard(bot, sorted_users, full_marks)
    log.info("Sent daily report.")


async def send_daily_leaderboard(bot: Bot, sorted_users: list, full_marks: int):
    """Send a separate leaderboard message after the daily report."""
    if not sorted_users:
        return

    # Combined ranking: ties share the same position number.
    last_score = None
    last_pos = 0
    board_lines: list[str] = []
    for idx, (name, score) in enumerate(sorted_users, start=1):
        if score != last_score:
            last_pos = idx
            last_score = score
        board_lines.append(f"{last_pos}. {name} ({score}/{full_marks})")

    text = "Daily Leaderboard:\n━━━━━━━━━\n" + "\n".join(board_lines)
    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=text,
        parse_mode="Markdown",
    )
    log.info("Sent daily leaderboard.")
