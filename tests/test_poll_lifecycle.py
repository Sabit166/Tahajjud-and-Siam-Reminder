"""Poll send -> answer -> report lifecycle across two groups."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

import db
import handlers
import jobs
import messaging
from config import BD_TZ
from prayer_times import PrayerTimes
from tests.conftest import GROUP_A, GROUP_B, amal_config


def run(coro):
    return asyncio.run(coro)


def answer_update(poll_id, user_id=42, name="Member One", option_ids=(0,)):
    user = SimpleNamespace(id=user_id, full_name=name, username="m1")
    return SimpleNamespace(
        poll_answer=SimpleNamespace(user=user, poll_id=poll_id, option_ids=list(option_ids))
    )


def ctx(bot):
    return SimpleNamespace(bot=bot, job_queue=None, job=None)


# ------------------------------------------------------------
#  Poll configs are read from Supabase after startup
# ------------------------------------------------------------

def test_init_db_enables_supabase_poll_configs(fake_sb):
    fake_sb.tables["poll_configs"] = [amal_config("tahajjud", GROUP_A)]
    # Regression: e78418d dropped the call that enabled this, leaving the
    # bot with an empty config cache for every group.
    db.init_db()
    ids = [c["id"] for c in db.get_all_poll_configs(group_chat_id=GROUP_A)]
    assert ids == ["tahajjud"]
    assert db.get_all_poll_configs(group_chat_id=GROUP_B) == []


# ------------------------------------------------------------
#  Sending a poll
# ------------------------------------------------------------

def test_send_checkin_maps_telegram_poll_id_to_config_and_group(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [amal_config("tahajjud", GROUP_A, title="Tahajjud Salat")]
    db.init_db()

    async def go():
        db.set_current_group(GROUP_A)
        await messaging.send_checkin(bot, "tahajjud")

    run(go())
    assert bot.sent_polls == [{"chat_id": GROUP_A, "question": "Tahajjud Salat", "options": ["Done", "Missed"]}]
    assert fake_sb.rows("active_polls") == [
        {"poll_id": "tg-poll-1001", "practice_key": "tahajjud", "group_chat_id": GROUP_A}
    ]


# ------------------------------------------------------------
#  Recording an answer
# ------------------------------------------------------------

def _register_poll(fake_sb, poll_id, practice, group):
    fake_sb.tables.setdefault("active_polls", []).append(
        {"poll_id": poll_id, "practice_key": practice, "group_chat_id": group}
    )


def test_poll_answer_is_recorded_for_the_polls_group(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [
        amal_config("tahajjud", GROUP_A, title="Tahajjud A", weight=15),
        # Same config id in another group must not be picked up.
        amal_config("tahajjud", GROUP_B, title="Tahajjud B", weight=3),
    ]
    _register_poll(fake_sb, "tg-1", "tahajjud", GROUP_A)
    db.init_db()

    run(handlers.handle_poll_answer(answer_update("tg-1"), ctx(bot)))

    responses = fake_sb.rows("responses")
    assert len(responses) == 1
    assert responses[0]["group_chat_id"] == GROUP_A
    assert responses[0]["practice"] == "tahajjud"
    assert responses[0]["did_it"] is True
    streaks = fake_sb.rows("streaks")
    assert [(s["group_chat_id"], s["current_streak"]) for s in streaks] == [(GROUP_A, 1)]
    assert bot.sent_messages[0]["chat_id"] == GROUP_A
    assert "Tahajjud A (+15)" in bot.sent_messages[0]["text"]


def test_missed_answer_is_recorded_as_not_done(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_B)]
    _register_poll(fake_sb, "tg-2", "quran", GROUP_B)
    db.init_db()

    run(handlers.handle_poll_answer(answer_update("tg-2", option_ids=(1,)), ctx(bot)))

    [row] = fake_sb.rows("responses")
    assert (row["group_chat_id"], row["did_it"]) == (GROUP_B, False)


def test_untracked_poll_answer_writes_nothing(fake_sb, bot):
    db.init_db()
    run(handlers.handle_poll_answer(answer_update("unknown"), ctx(bot)))
    assert fake_sb.rows("responses") == []
    assert bot.sent_messages == []


def test_retracted_vote_writes_nothing(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_A)]
    _register_poll(fake_sb, "tg-3", "quran", GROUP_A)
    db.init_db()
    run(handlers.handle_poll_answer(answer_update("tg-3", option_ids=()), ctx(bot)))
    assert fake_sb.rows("responses") == []


# ------------------------------------------------------------
#  Scheduler
# ------------------------------------------------------------

@pytest.fixture
def due_time(monkeypatch):
    now = dt.datetime.now(BD_TZ)
    if now.hour == 0 and now.minute < 2:
        pytest.skip("too close to midnight for a same-day fixed time")
    due = (now - dt.timedelta(seconds=30)).strftime("%H:%M")

    async def fake_fetch(*_a, **_k):
        t = dt.time(5, 0)
        return PrayerTimes(sunrise=t, fajr=t, dhuhr=t, asr=t, maghrib=t, isha=t)

    monkeypatch.setattr(messaging, "fetch_prayer_times", fake_fetch)
    messaging._SCHEDULED_EVENTS.clear()
    yield due
    messaging._SCHEDULED_EVENTS.clear()


def test_scheduler_sends_only_each_groups_own_configs(fake_sb, bot, due_time):
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_A, fixed_time=due_time)]
    db.init_db()

    run(jobs.prayer_schedule_job(ctx(bot)))

    # Group B has no configs: nothing is sent to it (no single-group fallback).
    assert [p["chat_id"] for p in bot.sent_polls] == [GROUP_A]
    assert fake_sb.rows("active_polls")[0]["group_chat_id"] == GROUP_A


def test_scheduler_failure_in_one_group_does_not_block_others(fake_sb, bot, due_time):
    fake_sb.tables["groups"] = [
        {"chat_id": GROUP_B, "title": "B", "is_active": True},
        {"chat_id": GROUP_A, "title": "A", "is_active": True},
    ]
    fake_sb.tables["poll_configs"] = [
        amal_config("quran", GROUP_A, fixed_time=due_time),
        amal_config("quran", GROUP_B, fixed_time=due_time),
    ]
    db.init_db()
    real_send = bot.send_poll

    async def flaky_send(chat_id, *a, **k):
        if chat_id == GROUP_B:
            raise RuntimeError("Telegram rejected the poll")
        return await real_send(chat_id, *a, **k)

    bot.send_poll = flaky_send
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert [p["chat_id"] for p in bot.sent_polls] == [GROUP_A]


def test_close_poll_job_removes_mapping_without_group_context(fake_sb):
    _register_poll(fake_sb, "tg-9", "quran", GROUP_A)
    import scheduling

    class StopBot:
        async def stop_poll(self, chat_id, message_id):
            return SimpleNamespace(id="tg-9")

    job = SimpleNamespace(data={"chat_id": GROUP_A, "message_id": 5, "label": "quran"})
    run(scheduling.close_poll_job(SimpleNamespace(job=job, bot=StopBot())))
    assert fake_sb.rows("active_polls") == []


# ------------------------------------------------------------
#  Leaderboards
# ------------------------------------------------------------

def _response(group, user_id, name, practice, did_it, when):
    return {
        "group_chat_id": group, "user_id": user_id, "username": "", "full_name": name,
        "practice": practice, "did_it": did_it,
        "response_date": when.date().isoformat(), "recorded_at": when.isoformat(),
    }


def test_daily_and_weekly_reports_are_per_group(fake_sb, bot):
    now = dt.datetime.now(BD_TZ)
    earlier = now - dt.timedelta(hours=1)
    fake_sb.tables["poll_configs"] = [
        amal_config("tahajjud", GROUP_A, title="Tahajjud", weight=15),
        amal_config("quran", GROUP_A, title="Quran", weight=7),
    ]
    fake_sb.tables["responses"] = [
        _response(GROUP_A, 1, "Alice", "tahajjud", True, earlier),
        _response(GROUP_A, 1, "Alice", "quran", False, earlier),
        _response(GROUP_B, 2, "Bob", "tahajjud", True, earlier),
    ]
    db.init_db()

    async def go():
        db.set_current_group(GROUP_A)
        await messaging.send_daily_report(bot, report_end=now)
        await messaging.send_weekly_report(bot)
        db.set_current_group(GROUP_B)
        await messaging.send_daily_report(bot, report_end=now)

    run(go())
    daily_a, weekly_a, daily_b = (m["text"] for m in bot.sent_messages)
    assert "Alice (Marks 15/22)" in daily_a and "Bob" not in daily_a
    assert "Alice (Marks 15/" in weekly_a and "Bob" not in weekly_a
    # Group B has no amal configs: it gets an empty report, not an error
    # and not group A's data.
    assert "No responses recorded" in daily_b
    assert [m["chat_id"] for m in bot.sent_messages] == [GROUP_A, GROUP_A, GROUP_B]


# ------------------------------------------------------------
#  Dashboard persistence
# ------------------------------------------------------------

def test_dashboard_writes_are_persisted_per_group(fake_sb):
    db.init_db()
    db.upsert_poll_config({"id": "duha", "title": "Duha", "poll_options": ["Y", "N"]}, group_chat_id=GROUP_A)
    db.upsert_poll_config({"id": "duha", "title": "Duha B", "poll_options": ["Y", "N"]}, group_chat_id=GROUP_B)
    assert sorted((r["group_chat_id"], r["title"]) for r in fake_sb.rows("poll_configs")) == [
        (GROUP_B, "Duha B"), (GROUP_A, "Duha"),
    ]

    # Simulate a restart: the cache is gone, the DB still has the rows.
    db._POLL_CONFIGS_CACHE.clear()
    assert db.get_poll_config("duha", group_chat_id=GROUP_A)["title"] == "Duha"

    assert db.delete_poll_config("duha", group_chat_id=GROUP_B) is True
    assert [(r["group_chat_id"], r["id"]) for r in fake_sb.rows("poll_configs")] == [(GROUP_A, "duha")]


def test_dashboard_write_failure_is_reported_not_silently_cached(fake_sb):
    db.init_db()
    fake_sb.fail_tables["poll_configs"] = "upsert"
    with pytest.raises(RuntimeError):
        db.upsert_poll_config({"id": "duha", "title": "Duha"}, group_chat_id=GROUP_A)
    fake_sb.fail_tables.clear()
    assert db.get_poll_config("duha", group_chat_id=GROUP_A) is None
