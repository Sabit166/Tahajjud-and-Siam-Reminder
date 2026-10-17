"""Inactivity callout after the daily report, and the order of the
messages around Maghrib (Maghrib is 17:40 in tests)."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

import db
import jobs
import messaging
from config import BD_TZ
from tests.conftest import GROUP_A, GROUP_B, amal_config

THU, FRI = dt.date(2026, 10, 15), dt.date(2026, 10, 16)
MAGHRIB = dt.time(17, 40)


def run(coro):
    return asyncio.run(coro)


def ctx(bot):
    return SimpleNamespace(bot=bot, job_queue=None, job=None)


@pytest.fixture
def clock(monkeypatch):
    def set_now(day, minutes_after_maghrib, seconds=10):
        now = BD_TZ.localize(dt.datetime.combine(day, MAGHRIB)) + dt.timedelta(
            minutes=minutes_after_maghrib, seconds=seconds)

        class Frozen(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(messaging, "_dt", SimpleNamespace(
            datetime=Frozen, date=dt.date, time=dt.time, timedelta=dt.timedelta))

    messaging._SCHEDULED_EVENTS.clear()
    yield set_now
    messaging._SCHEDULED_EVENTS.clear()


def answer(uid, name, practice, day, did_it=True, group=GROUP_A):
    return {"group_chat_id": group, "user_id": uid, "username": "", "full_name": name,
            "practice": practice, "did_it": did_it, "response_date": day.isoformat(),
            "recorded_at": f"{day.isoformat()}T10:00:00+06:00"}


@pytest.fixture
def group_a(fake_sb, bot):
    """Group A with a 10:00 Quran poll. On Thursday: Alice did it, Bob
    answered 'missed', Carol and Dave (who has left) did not answer.
    Imam is an admin who never answered; a bot admin must be ignored."""
    fake_sb.tables["groups"] = [{"chat_id": GROUP_A, "title": "A", "is_active": True}]
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_A, title="Quran", fixed_time="10:00")]
    wed = THU - dt.timedelta(days=1)
    fake_sb.tables["responses"] = [
        answer(1, "Alice", "quran", THU),
        answer(2, "Bob", "quran", THU, did_it=False),
        answer(3, "Carol", "quran", wed),
        answer(4, "Dave", "quran", wed),
        answer(9, "Zed", "quran", THU, group=GROUP_B),   # other group
    ]
    bot.members = {3: ("member", "Carol Khan", False), 4: ("left", "Dave", False),
                   6: ("administrator", "Imam", False)}
    bot.admins = [(6, "Imam", False), (7, "SomeBot", True)]
    db.init_db()
    return fake_sb


def texts(bot):
    return [m["text"] for m in bot.sent_messages]


def test_callout_lists_members_who_answered_nothing(group_a, bot, clock):
    clock(THU, 2)
    run(jobs.prayer_schedule_job(ctx(bot)))      # daily report
    clock(THU, 3)
    run(jobs.prayer_schedule_job(ctx(bot)))      # callout

    daily, callout = texts(bot)
    assert daily.startswith("Daily Report")
    assert callout.startswith("Inactivity Callout")
    assert '<a href="tg://user?id=3">Carol Khan</a>' in callout   # fresh name, mentioned
    assert '<a href="tg://user?id=6">Imam</a>' in callout          # admin who never answered
    for absent in ("Alice", "Bob", "Dave", "SomeBot", "Zed"):
        assert absent not in callout   # answered / missed / left / bot / other group
    assert bot.sent_messages[-1]["parse_mode"] == "HTML"


def test_callout_is_skipped_if_the_daily_report_was_not_sent(group_a, bot, clock):
    clock(THU, 4)   # bot (re)started after the daily report's window had passed
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert texts(bot) == []
    assert not any(t.startswith("Inactivity Callout") for t in texts(bot))


def test_everyone_participated(group_a, bot, clock):
    group_a.tables["responses"] += [answer(3, "Carol", "quran", THU), answer(6, "Imam", "quran", THU)]
    clock(THU, 2)
    run(jobs.prayer_schedule_job(ctx(bot)))
    clock(THU, 3)
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert "everyone took part" in texts(bot)[-1]


def test_no_callout_on_a_day_without_polls(group_a, bot, clock):
    group_a.tables["poll_configs"] = [amal_config("kahf", GROUP_A, fixed_time="12:00", days_of_week=[4])]
    clock(THU, 2)
    run(jobs.prayer_schedule_job(ctx(bot)))
    clock(THU, 3)
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert not any(t.startswith("Inactivity Callout") for t in texts(bot))


def test_messages_around_maghrib_never_overlap(group_a, bot, clock):
    """Friday: hadith (+0), daily (+2), callout (+3), weekly (+5)."""
    sent = []
    for minute in range(0, 7):
        clock(FRI, minute)
        before = len(bot.sent_messages)
        run(jobs.prayer_schedule_job(ctx(bot)))
        sent += [(minute, t.split(":")[0].split(" (")[0]) for t in texts(bot)[before:]]
    assert sent == [
        (0, "Hadith for maghrib"),
        (2, "Daily Report"),
        (3, "Inactivity Callout"),
        (5, "Weekly Report"),
    ]


def test_late_start_still_sends_in_order(group_a, bot, clock):
    clock(FRI, 3, seconds=40)   # bot (re)started late: daily and callout both due now
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert [t.split(":")[0] for t in texts(bot)] == ["Daily Report", "Inactivity Callout"]
