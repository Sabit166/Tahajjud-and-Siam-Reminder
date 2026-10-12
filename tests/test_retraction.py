"""Retracting a vote removes it; weekly report shows (done/total)."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

import db
import handlers
import messaging
from config import BD_TZ
from tests.conftest import GROUP_A, amal_config

TUE = dt.date(2026, 10, 6)
SENT = BD_TZ.localize(dt.datetime(2026, 10, 6, 5, 54))     # sunrise poll, amal day TUE
VOTED = BD_TZ.localize(dt.datetime(2026, 10, 6, 6, 16))
USER = 7


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def poll(fake_sb, monkeypatch):
    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return VOTED

    monkeypatch.setattr(handlers, "_dt", SimpleNamespace(
        datetime=Frozen, date=dt.date, time=dt.time, timedelta=dt.timedelta, timezone=dt.timezone))
    db._STREAK_UNDO.clear()
    fake_sb.tables["poll_configs"] = [amal_config("morning", GROUP_A, title="Morning Adhkar")]
    fake_sb.tables["active_polls"] = [{"poll_id": "p", "practice_key": "morning",
                                      "group_chat_id": GROUP_A, "created_at": SENT.isoformat()}]
    db.init_db()
    yield fake_sb
    db._STREAK_UNDO.clear()


def vote(bot, option_ids):
    update = SimpleNamespace(poll_answer=SimpleNamespace(
        user=SimpleNamespace(id=USER, full_name="Rafin", username="rafin"),
        poll_id="p", option_ids=list(option_ids)))
    run(handlers.handle_poll_answer(update, SimpleNamespace(bot=bot, job_queue=None, job=None)))


def streak(fake_sb):
    rows = [r for r in fake_sb.rows("streaks") if r["user_id"] == USER]
    return rows[0] if rows else None


def existing_streak(fake_sb, current, last_done="2026-10-05"):
    fake_sb.tables["streaks"] = [{
        "group_chat_id": GROUP_A, "user_id": USER, "practice": "morning",
        "current_streak": current, "longest_streak": 9,
        "last_done_date": last_done, "last_scheduled_date": "2026-10-05",
    }]


def test_retracting_a_done_vote_removes_it_and_restores_the_streak(poll, bot):
    existing_streak(poll, 3)
    vote(bot, [0])
    assert streak(poll)["current_streak"] == 4
    vote(bot, [])   # retract (Rafin's case)

    assert poll.rows("responses") == []
    s = streak(poll)
    assert (s["current_streak"], s["longest_streak"], s["last_done_date"]) == (3, 9, "2026-10-05")


def test_retracting_a_missed_vote_restores_the_streak(poll, bot):
    existing_streak(poll, 5)
    vote(bot, [1])
    assert streak(poll)["current_streak"] == 0
    vote(bot, [])
    assert poll.rows("responses") == []
    assert streak(poll)["current_streak"] == 5


def test_first_ever_vote_retracted_leaves_no_streak(poll, bot):
    vote(bot, [0])
    vote(bot, [])
    assert poll.rows("responses") == [] and streak(poll) is None


def test_changing_a_vote_keeps_only_the_new_answer(poll, bot):
    existing_streak(poll, 3)
    vote(bot, [0])
    vote(bot, [])     # Telegram requires retracting before re-voting
    vote(bot, [1])
    [row] = poll.rows("responses")
    assert row["did_it"] is False and row["response_date"] == TUE.isoformat()
    assert streak(poll)["current_streak"] == 0


def test_retraction_after_restart_still_removes_the_answer(poll, bot):
    existing_streak(poll, 3)
    vote(bot, [0])
    db._STREAK_UNDO.clear()   # simulate a restart between vote and retraction
    vote(bot, [])
    assert poll.rows("responses") == []
    assert streak(poll)["current_streak"] == 4   # left as is (logged)


def test_retracted_vote_is_not_in_the_daily_report(poll, bot):
    vote(bot, [0])
    vote(bot, [])

    async def go():
        db.set_current_group(GROUP_A)
        await messaging.send_daily_report(bot, report_day=TUE)

    run(go())
    assert "No responses recorded" in bot.sent_messages[-1]["text"]


def test_weekly_report_shows_days_done_over_total(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [
        amal_config("morning", GROUP_A, title="Morning Adhkar", weight=8),
        amal_config("sawm", GROUP_A, title="Sawm", weight=8, days_of_week=[0, 3]),
    ]
    fake_sb.tables["responses"] = [
        {"group_chat_id": GROUP_A, "user_id": 1, "username": "", "full_name": "Alice",
         "practice": p, "did_it": done, "response_date": (TUE - dt.timedelta(days=d)).isoformat(),
         "recorded_at": "2026-10-06T00:00:00+06:00"}
        for p, d, done in [("morning", 0, True), ("morning", 1, True), ("morning", 2, False),
                           ("morning", 3, True), ("morning", 4, True), ("sawm", 1, True)]
    ]
    db.init_db()

    async def go():
        db.set_current_group(GROUP_A)
        await messaging.send_weekly_report(bot, report_day=TUE)

    run(go())
    [report] = [m["text"] for m in bot.sent_messages]
    assert "  -- Morning Adhkar: (4/7)\n" in report
    assert "  -- Sawm: (1/2)\n" in report
    assert "🟩" not in report and "⬜" not in report
    assert "Alice (Marks 40/72)" in report
