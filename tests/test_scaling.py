"""Load-related behaviour: shared prayer-time fetches, batched streak
reads, and report queries that are not truncated at the row cap."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

import db
import jobs
import messaging
from config import BD_TZ
from prayer_times import PrayerTimes
from tests.conftest import GROUP_A, GROUP_B, amal_config


def run(coro):
    return asyncio.run(coro)


def ctx(bot):
    return SimpleNamespace(bot=bot, job_queue=None, job=None)


def response(user_id, practice, when, did_it=True, group=GROUP_A):
    return {
        "group_chat_id": group, "user_id": user_id, "username": "", "full_name": f"User {user_id:04d}",
        "practice": practice, "did_it": did_it,
        "response_date": when.date().isoformat(), "recorded_at": when.isoformat(),
    }


# ------------------------------------------------------------
#  Prayer times: one fetch per job run, shared by all groups
# ------------------------------------------------------------

@pytest.fixture
def counted_fetch(monkeypatch):
    calls = []

    async def fake_fetch(*_a, **kw):
        calls.append(kw.get("date"))
        t = dt.time(3, 0)  # nothing is due at the current time
        return PrayerTimes(sunrise=t, fajr=t, dhuhr=t, asr=t, maghrib=t, isha=t)

    monkeypatch.setattr(messaging, "fetch_prayer_times", fake_fetch)
    messaging._SCHEDULED_EVENTS.clear()
    yield calls
    messaging._SCHEDULED_EVENTS.clear()


def test_prayer_times_fetched_once_per_job_for_all_groups(fake_sb, bot, counted_fetch):
    fake_sb.tables["groups"] += [{"chat_id": -3003 - i, "title": f"G{i}", "is_active": True} for i in range(8)]
    db.init_db()

    run(jobs.prayer_schedule_job(ctx(bot)))
    run(jobs.prayer_schedule_job(ctx(bot)))

    assert len(counted_fetch) == 2  # one per run, not one per group (10 groups)
    assert counted_fetch == [dt.datetime.now(BD_TZ).date()] * 2  # Dhaka date


def test_prayer_time_fetch_failure_skips_the_run(fake_sb, bot, monkeypatch):
    async def failing(*_a, **_k):
        raise RuntimeError("Aladhan down")

    monkeypatch.setattr(messaging, "fetch_prayer_times", failing)
    db.init_db()
    run(jobs.prayer_schedule_job(ctx(bot)))  # must not raise
    assert bot.sent_polls == [] and bot.sent_messages == []


# ------------------------------------------------------------
#  Streaks: one query for all members
# ------------------------------------------------------------

def test_daily_streaks_use_one_query_for_all_members(fake_sb):
    now = dt.datetime.now(BD_TZ)
    users = range(1, 41)
    fake_sb.tables["responses"] = [response(u, "quran", now - dt.timedelta(hours=1)) for u in users]
    fake_sb.tables["streaks"] = [
        {"group_chat_id": GROUP_A, "user_id": u, "practice": "quran", "current_streak": u}
        for u in users
    ] + [{"group_chat_id": GROUP_B, "user_id": 1, "practice": "quran", "current_streak": 99}]
    db.set_current_group(GROUP_A)

    result = db.get_daily_streaks(list(users), ["quran"])

    # One page plus the empty page that ends pagination, independent of
    # the member count (was 40: one query per member).
    assert fake_sb.selects.count("streaks") == 2
    assert len(result) == 40
    assert result[1] == {"quran": 1}   # not group B's 99
    assert result[40] == {"quran": 40}


# ------------------------------------------------------------
#  Report queries read past the 1000-row cap
# ------------------------------------------------------------

@pytest.mark.parametrize("server_cap", [1000, 7])
def test_weekly_summary_counts_every_row(fake_sb, server_cap):
    fake_sb.max_rows = server_cap
    today = dt.datetime.now(BD_TZ)
    users = range(1, 101)
    practices = ["quran", "tahajjud", "duha"]
    # 100 members x 3 polls x 7 days = 2100 rows (old code saw only 1000).
    fake_sb.tables["responses"] = [
        response(u, p, today - dt.timedelta(days=d), did_it=(d % 2 == 0))
        for u in users for p in practices for d in range(7)
    ]
    db.set_current_group(GROUP_A)

    rows = db.get_weekly_summary()

    assert len(rows) == 300  # every (member, poll) pair
    assert all(total == 7 and done == 4 for _, _, _, done, total in rows)


def test_daily_summary_counts_every_row(fake_sb):
    now = dt.datetime.now(BD_TZ)
    fake_sb.tables["poll_configs"] = [amal_config(p, GROUP_A) for p in ("quran", "tahajjud")]
    fake_sb.tables["responses"] = [
        response(u, p, now - dt.timedelta(hours=1))
        for u in range(1, 701) for p in ("quran", "tahajjud")
    ]  # 1400 rows
    db.init_db()
    db.set_current_group(GROUP_A)

    names, summary = db.get_daily_summary(now.date(), ["quran", "tahajjud"])

    assert len(summary) == 700 and len(names) == 700
    assert all(v == {"quran": 1, "tahajjud": 1} for v in summary.values())
