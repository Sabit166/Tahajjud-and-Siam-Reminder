"""Members inactive for 30 poll days are kicked (warned from day 27)."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest

import db
import messaging
from tests.conftest import GROUP_A, amal_config

DAY = dt.date(2026, 10, 15)
LAZY, WARNED, ADMIN, ACTIVE = 31, 32, 33, 34


def run(coro):
    return asyncio.run(coro)


def answered(uid, name, days_ago):
    day = DAY - dt.timedelta(days=days_ago)
    return {"group_chat_id": GROUP_A, "user_id": uid, "username": "", "full_name": name,
            "practice": "quran", "did_it": True, "response_date": day.isoformat(),
            "recorded_at": f"{day.isoformat()}T10:00:00+06:00"}


class KickBot:
    def __init__(self, can_ban=True):
        self.can_ban = can_ban
        self.sent, self.banned, self.unbanned = [], [], []
        self.members = {LAZY: "Lazy", WARNED: "Warned", ADMIN: "Admin", ACTIVE: "Active"}

    async def get_chat_member(self, chat_id, user_id):
        status = "administrator" if user_id == ADMIN else "member"
        return SimpleNamespace(status=status, user=SimpleNamespace(
            id=user_id, full_name=self.members[user_id], is_bot=False))

    async def get_chat_administrators(self, chat_id):
        return [SimpleNamespace(user=SimpleNamespace(id=ADMIN, full_name="Admin", is_bot=False))]

    async def ban_chat_member(self, chat_id, user_id):
        if not self.can_ban:
            raise BadRequest("Not enough rights to restrict/ban chat member")
        self.banned.append(user_id)

    async def unban_chat_member(self, chat_id, user_id, only_if_banned=False):
        assert only_if_banned
        self.unbanned.append(user_id)

    async def send_message(self, chat_id, text, **_k):
        self.sent.append(text)


@pytest.fixture
def group(fake_sb):
    """Active answers every day for 40 days. Lazy last answered 31 days ago
    (31-day streak), Warned 28 days ago (28), Admin 40+ days ago."""
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_A, title="Quran", fixed_time="10:00")]
    fake_sb.tables["responses"] = (
        [answered(ACTIVE, "Active", n) for n in range(41)]
        + [answered(LAZY, "Lazy", 31), answered(WARNED, "Warned", 28), answered(ADMIN, "Admin", 45)]
    )
    fake_sb.tables["group_members"] = [
        {"group_chat_id": GROUP_A, "user_id": uid, "full_name": n, "username": "",
         "joined_at": "2026-08-01T10:00:00+06:00"}
        for uid, n in ((LAZY, "Lazy"), (WARNED, "Warned"), (ADMIN, "Admin"), (ACTIVE, "Active"))
    ]
    db.init_db()
    db.set_current_group(GROUP_A)
    return fake_sb


def callout(bot):
    run(messaging.send_inactivity_callout(bot, report_day=DAY))


def test_member_inactive_30_days_is_kicked_and_announced(group, bot=None):
    bot = KickBot()
    callout(bot)

    listing, announcement = bot.sent
    assert '<a href="tg://user?id=31">Lazy</a> (31 days, removed)' in listing
    assert bot.banned == [LAZY] and bot.unbanned == [LAZY]      # kick, not a permanent ban
    assert announcement.startswith("Removed for 30 days of inactivity:")
    assert "Lazy" in announcement and "rejoin" in announcement
    assert LAZY not in [r["user_id"] for r in group.rows("group_members")]
    assert any(r["user_id"] == LAZY for r in group.rows("responses"))   # history kept


def test_members_from_day_27_are_warned_not_removed(group):
    bot = KickBot()
    callout(bot)
    assert '<a href="tg://user?id=32">Warned</a> (28 days, will be removed at 30)' in bot.sent[0]
    assert WARNED not in bot.banned


def test_admins_are_never_removed_or_warned(group):
    bot = KickBot()
    callout(bot)
    line = next(l for l in bot.sent[0].splitlines() if "Admin" in l)
    assert "removed" not in line
    assert ADMIN not in bot.banned


def test_without_ban_permission_nobody_is_removed_and_admins_are_told(group):
    bot = KickBot(can_ban=False)
    callout(bot)

    listing, notice = bot.sent
    assert notice.startswith("Admins: these members have been inactive for 30 days")
    assert "Lazy" in notice and "Ban users" in notice
    assert bot.banned == []
    assert LAZY in [r["user_id"] for r in group.rows("group_members")]


def test_days_when_nobody_answered_do_not_count(group):
    # Remove every answer from 10-20 days ago: those days look like a bot
    # outage and are skipped, so Lazy drops below 30 and is only warned.
    group.tables["responses"] = [
        r for r in group.rows("responses")
        if not (10 <= (DAY - dt.date.fromisoformat(r["response_date"])).days <= 20)
    ]
    bot = KickBot()
    callout(bot)
    assert bot.banned == []
    assert "(20 days)" in bot.sent[0]   # 31 - 11 skipped days
