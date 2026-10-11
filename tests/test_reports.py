"""Built-in daily/weekly reports and the amal-poll-only dashboard."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import db
import jobs
import messaging
import web_server
from config import BD_TZ
from prayer_times import PrayerTimes
from tests.conftest import GROUP_A, GROUP_B, amal_config

FRIDAY = dt.date(2026, 10, 16)
THURSDAY = dt.date(2026, 10, 15)
MAGHRIB = dt.time(17, 40)


def run(coro):
    return asyncio.run(coro)


def ctx(bot):
    return SimpleNamespace(bot=bot, job_queue=None, job=None)


@pytest.fixture
def clock(monkeypatch):
    """Freeze messaging's clock and serve a fixed Maghrib time."""
    assert FRIDAY.weekday() == 4 and THURSDAY.weekday() == 3

    async def fake_fetch(*_a, **_k):
        t = dt.time(5, 0)
        return PrayerTimes(sunrise=t, fajr=t, dhuhr=t, asr=t, maghrib=MAGHRIB, isha=t)

    def set_now(day, minutes_after_maghrib, seconds=30):
        now = BD_TZ.localize(dt.datetime.combine(day, MAGHRIB)) + dt.timedelta(
            minutes=minutes_after_maghrib, seconds=seconds
        )

        class FrozenDatetime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(
            messaging,
            "_dt",
            SimpleNamespace(datetime=FrozenDatetime, date=dt.date, time=dt.time, timedelta=dt.timedelta),
        )

    monkeypatch.setattr(messaging, "fetch_prayer_times", fake_fetch)
    messaging._SCHEDULED_EVENTS.clear()
    yield set_now
    messaging._SCHEDULED_EVENTS.clear()


def report_chats(bot, title):
    return [m["chat_id"] for m in bot.sent_messages if m["text"].startswith(title)]


def test_daily_report_goes_to_every_group_two_minutes_after_maghrib(fake_sb, bot, clock):
    # Group A has amal polls, group B has none: both still get a report.
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_A, fixed_time="03:00")]
    db.init_db()

    clock(THURSDAY, 2)
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert sorted(report_chats(bot, "Daily Report")) == sorted([GROUP_A, GROUP_B])
    assert report_chats(bot, "Weekly Report") == []

    # Sent once per day, not on every tick.
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert len(report_chats(bot, "Daily Report")) == 2


def test_weekly_report_goes_to_every_group_on_friday_only(fake_sb, bot, clock):
    db.init_db()

    clock(THURSDAY, 5)
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert report_chats(bot, "Weekly Report") == []

    clock(FRIDAY, 5)
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert sorted(report_chats(bot, "Weekly Report")) == sorted([GROUP_A, GROUP_B])


def test_legacy_report_rows_are_not_sent_as_polls(fake_sb, bot, clock):
    # Rows created before reports became built in.
    fake_sb.tables["poll_configs"] = [
        amal_config("daily_report", GROUP_A, title="Daily Summary Report", poll_type="report",
                    time_type="prayer_relative", prayer_name="maghrib", prayer_offset_minutes=2),
        amal_config("jumuah_reminder", GROUP_A, title="Jumuah", poll_type="reminder",
                    time_type="prayer_relative", prayer_name="maghrib", prayer_offset_minutes=2),
    ]
    db.init_db()

    clock(THURSDAY, 2)
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert bot.sent_polls == []
    assert report_chats(bot, "Daily Report").count(GROUP_A) == 1
    assert not any("Jumu" in m["text"] for m in bot.sent_messages)


def test_weekly_report_ignores_answers_to_deleted_polls(fake_sb, bot):
    today = dt.datetime.now(BD_TZ)
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_A, title="Quran", weight=7)]
    fake_sb.tables["responses"] = [
        {"group_chat_id": GROUP_A, "user_id": 1, "username": "", "full_name": "Alice",
         "practice": practice, "did_it": True,
         "response_date": today.date().isoformat(), "recorded_at": today.isoformat()}
        for practice in ("quran", "deleted_poll")
    ]
    db.init_db()

    async def go():
        db.set_current_group(GROUP_A)
        await messaging.send_weekly_report(bot)

    run(go())  # previously raised KeyError for 'deleted_poll'
    [report] = [m["text"] for m in bot.sent_messages]
    assert "Alice (Marks 7/49)" in report
    assert "Quran" in report and "deleted_poll" not in report


@pytest.fixture
def client(fake_sb, monkeypatch):
    async def no_timings():
        raise RuntimeError("offline")

    monkeypatch.setattr(web_server, "get_today_prayer_timings", no_timings)
    fake_sb.tables["poll_configs"] = [
        amal_config("quran", GROUP_A, title="Quran"),
        amal_config("daily_report", GROUP_A, title="Daily Summary Report", poll_type="report"),
        amal_config("jumuah_reminder", GROUP_A, title="Jumuah", poll_type="reminder"),
    ]
    db.init_db()
    return TestClient(web_server.app), {"Authorization": f"Bearer {db.issue_session(GROUP_A, 42)}"}


def test_dashboard_lists_and_edits_amal_polls_only(client):
    http, headers = client
    assert [p["id"] for p in http.get("/api/polls", headers=headers).json()["polls"]] == ["quran"]

    body = {"title": "Changed", "poll_options": ["Y", "N"]}
    for poll_id in ("daily_report", "jumuah_reminder"):
        assert http.put(f"/api/polls/{poll_id}", json=body, headers=headers).status_code == 404
        assert http.post(f"/api/polls/{poll_id}/toggle", headers=headers).status_code == 404
        assert http.post(f"/api/polls/{poll_id}/trigger", headers=headers).status_code == 404
        assert http.delete(f"/api/polls/{poll_id}", headers=headers).status_code == 404


def test_dashboard_rejects_report_and_reminder_types(client):
    http, headers = client
    for poll_type in ("report", "reminder"):
        body = {"title": "Weekly Report", "poll_type": poll_type}
        assert http.post("/api/polls", json=body, headers=headers).status_code == 422


def test_new_poll_cannot_overwrite_a_legacy_report_row(client):
    http, headers = client
    body = {"title": "Daily Report", "poll_options": ["Done", "Missed"]}
    created = http.post("/api/polls", json=body, headers=headers).json()["poll"]
    assert created["id"] != "daily_report" and created["poll_type"] == "amal_poll"
    legacy = [r for r in db._sb().rows("poll_configs") if r["id"] == "daily_report"]
    assert [r["poll_type"] for r in legacy] == ["report"]
