"""
CLI script to immediately trigger any poll or reminder manually from the terminal.

Usage:
    python send_now.py quran_page
    python send_now.py ayyam_beej
    python send_now.py <practice_or_action>
    python send_now.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from telegram import Bot

from config import TOKEN, GROUP_CHAT_ID, log
from practices import PRACTICES
from messaging import (
    send_checkin,
    send_nightly_amal,
    send_weekly_report,
    send_daily_report,
    send_jumuah_reminder,
    send_ayyam_beej_reminder,
    check_and_send_ayyam_beej_reminder,
)
from db import init_db


SPECIAL_ACTIONS = {
    "ayyam_beej": "Send Ayyam al-Bid (13, 14, 15) fasting reminder message",
    "ayyam_beej_check": "Check Hijri date and send Ayyam-E-Beej reminder only if today is the 12th Hijri",
    "jumuah": "Send Yaum al-Jumu'ah Sunnahs reminder message",
    "nightly_amal": "Send all 4 Nightly Amal check-in polls",
    "daily_report": "Send today's daily summary report",
    "weekly_report": "Send this week's summary report",
}


async def main_async(target: str, force: bool = False):
    if not TOKEN:
        print("ERROR: BOT_TOKEN is not set in your .env file or environment!", file=sys.stderr)
        sys.exit(1)
    if not GROUP_CHAT_ID:
        print("ERROR: GROUP_CHAT_ID is not set in your .env file or environment!", file=sys.stderr)
        sys.exit(1)

    init_db()
    bot = Bot(token=TOKEN)

    print(f"[*] Dispatching '{target}' to Telegram group {GROUP_CHAT_ID}...")

    if target == "ayyam_beej":
        await send_ayyam_beej_reminder(bot)
        print("✅ Successfully sent Ayyam al-Bid (Ayyam-E-Beej) reminder!")
    elif target == "ayyam_beej_check":
        await check_and_send_ayyam_beej_reminder(bot, force=force)
        print("✅ Ayyam-E-Beej Hijri date check completed!")
    elif target == "jumuah":
        await send_jumuah_reminder(bot)
        print("✅ Successfully sent Yaum al-Jumu'ah reminder!")
    elif target == "nightly_amal":
        await send_nightly_amal(bot)
        print("✅ Successfully sent 4 Nightly Amal polls!")
    elif target == "daily_report":
        await send_daily_report(bot)
        print("✅ Successfully sent Daily Report!")
    elif target == "weekly_report":
        await send_weekly_report(bot)
        print("✅ Successfully sent Weekly Report!")
    elif target in PRACTICES:
        label = PRACTICES[target]["label"]
        await send_checkin(bot, target)
        print(f"✅ Successfully sent '{label}' check-in poll!")
    else:
        print(f"ERROR: Unknown target '{target}'. Use --list to see available options.", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Manually dispatch polls and reminders to the Telegram group.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python send_now.py quran_page     # Send "Read 1 page of the Quran" poll
  python send_now.py ayyam_beej     # Send Ayyam-E-Beej fasting reminder
  python send_now.py tahajjud       # Send Tahajjud poll
  python send_now.py daily_report   # Send daily report
  python send_now.py --list         # List all available options
        """,
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="The practice key or reminder action to trigger (e.g. 'quran_page', 'ayyam_beej').",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available practice keys and special reminder actions.",
    )

    args = parser.parse_args()

    if args.list or not args.target:
        print("\n=== Special Actions / Reminders ===")
        for act, desc in SPECIAL_ACTIONS.items():
            print(f"  {act:<20} - {desc}")

        print("\n=== Practice Polls ===")
        for key, info in PRACTICES.items():
            print(f"  {key:<20} - {info['label']}")
        print()
        if not args.target:
            sys.exit(0)

    asyncio.run(main_async(args.target))


if __name__ == "__main__":
    main()
