"""
Message senders: check-in polls, nightly amal batch, daily and weekly
reports.
"""

import asyncio

from telegram import Bot

from config import GROUP_CHAT_ID, RESPONSE_WINDOW_HOURS, log
from practices import PRACTICES, NIGHTLY_AMAL_OPTIONS
from db import save_active_poll, get_weekly_summary, get_daily_summary
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

async def send_weekly_report(bot: Bot):
    rows = get_weekly_summary()
    if not rows:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Weekly Report:\n━━━━━━━━━━━━━━━━━━━━\nNo responses recorded this week yet.",
            parse_mode="Markdown"
        )
        return

    report = "Weekly Report:\n━━━━━━━━━━━━━━━━━━━━\n"

    current_name = None
    for full_name, practice, completed, total in rows:
        if full_name != current_name:
            if current_name is not None:
                report += "━━━━━━━━━━━━━━━━━━━━\n"
            report += f"*{full_name}*\n"
            current_name = full_name

        p_info = PRACTICES.get(practice, {})
        label = p_info.get("label", practice)
        bar = "🟩" * completed + "⬜" * (total - completed)
        report += f"  -- {label}: {bar} {completed}/{total}\n"

    report += "━━━━━━━━━━━━━━━━━━━━"

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=report,
        parse_mode="Markdown"
    )
    log.info("Sent weekly report.")

async def send_daily_report(bot: Bot):
    scheduled_practices, summary = get_daily_summary()

    if not summary:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Daily Report:\n━━━━━━━━━━━━━━━━━━━━\nNo responses recorded today yet.",
            parse_mode="Markdown"
        )
        return

    report = "Daily Report:\n━━━━━━━━━━━━━━━━━━━━\n"

    for full_name in sorted(summary):
        report += f"*{full_name}*\n"
        for practice in scheduled_practices:
            p_info = PRACTICES.get(practice, {})
            label = p_info.get("label", practice)
            did_it = summary[full_name].get(practice, 0)
            status = "Done" if did_it else "Missed"
            report += f"  -- {label}: {status}\n"
        report += "━━━━━━━━━━━━━━━━━━━━\n"

    report = report.rstrip("\n")

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=report,
        parse_mode="Markdown"
    )
    log.info("Sent daily report.")
