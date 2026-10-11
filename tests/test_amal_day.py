"""Amal day = Maghrib to Maghrib: poll closing, answer dating, and which
polls and answers each daily report covers. Maghrib is 17:40 in tests."""

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

MON, TUE, WED, THU, FRI = (dt.date(2026, 10, d) for d in (5, 6, 7, 8, 9))


def run(coro):
    return asyncio.run(coro)


def at(day, hh, mm):
    return BD_TZ.localize(dt.datetime.combine(day, dt.time(hh, mm)))


@pytest.fixture
def freeze(monkeypatch):
    """Freeze `datetime.now()` inside the given modules."""
    def _freeze(when, *modules):
        class Frozen(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return when.astimezone(tz) if tz else when

        shim = SimpleNamespace(datetime=Frozen, date=dt.date, time=dt.time,
                               timedelta=dt.timedelta, timezone=dt.timezone)
        for module in modules:
            monkeypatch.setattr(module, "_dt", shim)
    return _freeze


# ------------------------------------------------------------
#  Day boundaries
# ------------------------------------------------------------

@pytest.mark.parametrize("moment, expected", [
    (at(TUE, 4, 10), TUE),      # pre-dawn: still Tuesday's amal day
    (at(TUE, 17, 39), TUE),     # one minute before Maghrib
    (at(TUE, 17, 40), WED),     # Maghrib starts Wednesday's amal day
    (at(TUE, 23, 0), WED),
])
def test_amal_day_runs_maghrib_to_maghrib(moment, expected):
    assert run(messaging.amal_day_of(moment)) == expected


def test_amal_day_ends_at_daily_report_time():
    assert run(messaging.amal_day_end(TUE)) == at(TUE, 17, 42)


# ------------------------------------------------------------
#  Which polls belong to a day's report
# ------------------------------------------------------------

def _practices(fake_sb, configs, day):
    fake_sb.tables["poll_configs"] = configs
    db.init_db()

    async def go():
        db.set_current_group(GROUP_A)
        return await messaging.practices_for_amal_day(day)

    return run(go())


def test_weekday_poll_counts_only_for_the_day_it_is_sent(fake_sb):
    sawm = amal_config("sawm", GROUP_A, days_of_week=[0], time_type="prayer_relative",
                       prayer_name="fajr", prayer_offset_minutes=-30)   # Mon 04:30
    assert _practices(fake_sb, [sawm], MON) == ["sawm"]
    assert _practices(fake_sb, [sawm], TUE) == []   # previously also on Tuesday


def test_poll_sent_after_maghrib_counts_for_the_next_amal_day(fake_sb):
    thu_isha = amal_config("mulk", GROUP_A, days_of_week=[3], fixed_time="20:00")  # Thu 20:00
    assert _practices(fake_sb, [thu_isha], THU) == []
    assert _practices(fake_sb, [thu_isha], FRI) == ["mulk"]


def test_daily_polls_before_and_after_maghrib_are_both_in_the_day(fake_sb):
    configs = [
        amal_config("evening", GROUP_A, time_type="prayer_relative", prayer_name="maghrib",
                    prayer_offset_minutes=30),             # previous day 18:10
        amal_config("quran", GROUP_A, fixed_time="10:00"),  # same day 10:00
    ]
    assert sorted(_practices(fake_sb, configs, WED)) == ["evening", "quran"]


# ------------------------------------------------------------
#  Polls close when the day ends
# ------------------------------------------------------------

class RecordingJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, callback, when, data=None, name=None):
        self.jobs.append((callback.__name__, when))


@pytest.mark.parametrize("sent, closes", [
    (at(TUE, 4, 10), at(TUE, 17, 42)),   # Tahajjud: closes at Tuesday's report
    (at(TUE, 18, 10), at(WED, 17, 42)),  # sent after Maghrib: closes Wednesday
])
def test_poll_closes_when_its_amal_day_ends(fake_sb, bot, freeze, sent, closes):
    fake_sb.tables["poll_configs"] = [amal_config("tahajjud", GROUP_A)]
    db.init_db()
    freeze(sent, messaging)
    jq = RecordingJobQueue()

    async def go():
        db.set_current_group(GROUP_A)
        await messaging.send_checkin(bot, "tahajjud", jq)

    run(go())
    assert jq.jobs == [("close_poll_job", closes)]


# ------------------------------------------------------------
#  Answers are dated by the poll's amal day
# ------------------------------------------------------------

def _answer(fake_sb, bot, freeze, poll_id, sent, answered, practice="salawat", user=1, name="Alice"):
    fake_sb.tables.setdefault("active_polls", []).append(
        {"poll_id": poll_id, "practice_key": practice, "group_chat_id": GROUP_A,
         "created_at": sent.isoformat()}
    )
    freeze(answered, handlers)
    update = SimpleNamespace(poll_answer=SimpleNamespace(
        user=SimpleNamespace(id=user, full_name=name, username=None),
        poll_id=poll_id, option_ids=[0]))
    run(handlers.handle_poll_answer(update, SimpleNamespace(bot=bot, job_queue=None, job=None)))


def test_answers_from_two_amal_days_no_longer_overwrite_each_other(fake_sb, bot, freeze):
    fake_sb.tables["poll_configs"] = [amal_config("salawat", GROUP_A)]
    db.init_db()
    # Monday 18:10 poll answered Tuesday 09:00; Tuesday 18:10 poll answered Tuesday 20:00.
    _answer(fake_sb, bot, freeze, "p-mon", at(MON, 18, 10), at(TUE, 9, 0))
    _answer(fake_sb, bot, freeze, "p-tue", at(TUE, 18, 10), at(TUE, 20, 0))
    dates = sorted(r["response_date"] for r in fake_sb.rows("responses"))
    assert dates == [TUE.isoformat(), WED.isoformat()]   # previously one row


def test_answer_before_day_end_counts_and_late_answer_is_ignored(fake_sb, bot, freeze):
    fake_sb.tables["poll_configs"] = [amal_config("tahajjud", GROUP_A)]
    db.init_db()
    _answer(fake_sb, bot, freeze, "p1", at(TUE, 4, 10), at(TUE, 17, 41), practice="tahajjud")
    _answer(fake_sb, bot, freeze, "p2", at(TUE, 4, 10), at(TUE, 17, 50), practice="tahajjud", user=2, name="Bob")
    rows = fake_sb.rows("responses")
    assert [(r["full_name"], r["response_date"]) for r in rows] == [("Alice", TUE.isoformat())]


def test_streak_uses_the_polls_send_date(fake_sb, bot, freeze):
    fake_sb.tables["poll_configs"] = [amal_config("salawat", GROUP_A)]
    db.init_db()
    _answer(fake_sb, bot, freeze, "p-mon", at(MON, 18, 10), at(TUE, 9, 0))
    [streak] = fake_sb.rows("streaks")
    assert streak["last_scheduled_date"] == MON.isoformat()


# ------------------------------------------------------------
#  Daily report contents
# ------------------------------------------------------------

def test_daily_report_uses_that_amal_days_answers_and_member_ids(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_A, title="Quran", weight=7)]

    def resp(uid, name, day):
        return {"group_chat_id": GROUP_A, "user_id": uid, "username": "", "full_name": name,
                "practice": "quran", "did_it": True, "response_date": day.isoformat(),
                "recorded_at": at(day, 9, 0).isoformat()}

    fake_sb.tables["responses"] = [
        resp(1, "Abdullah", TUE), resp(2, "Abdullah", TUE),  # same name, two members
        resp(3, "Late Larry", WED),                            # another amal day
    ]
    db.init_db()

    async def go():
        db.set_current_group(GROUP_A)
        await messaging.send_daily_report(bot, report_day=TUE)

    run(go())
    [report] = [m["text"] for m in bot.sent_messages]
    assert report.count("Abdullah (Marks 7/7)") == 2
    assert "Larry" not in report


# ------------------------------------------------------------
#  Closing polls across restarts (active_polls.message_id)
# ------------------------------------------------------------

def test_sent_poll_records_its_message_id(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [amal_config("quran", GROUP_A)]
    db.init_db()

    async def go():
        db.set_current_group(GROUP_A)
        await messaging.send_checkin(bot, "quran")

    run(go())
    [row] = fake_sb.rows("active_polls")
    assert row["message_id"] == 2 and row["practice_key"] == "quran"


def test_poll_is_still_tracked_before_the_column_is_migrated(fake_sb):
    fake_sb.missing_columns["active_polls"] = {"message_id"}
    db.save_active_poll("p1", "quran", GROUP_A, message_id=55)
    assert fake_sb.rows("active_polls") == [
        {"poll_id": "p1", "practice_key": "quran", "group_chat_id": GROUP_A}
    ]


class StopRecordingBot:
    def __init__(self):
        self.stopped = []

    async def stop_poll(self, chat_id, message_id):
        self.stopped.append((chat_id, message_id))


def test_restart_closes_overdue_polls_and_rearms_the_rest(fake_sb, freeze):
    fake_sb.tables["active_polls"] = [
        # Monday's poll: its day ended Monday 17:42 -> close now.
        {"poll_id": "old", "practice_key": "quran", "group_chat_id": GROUP_A,
         "created_at": at(MON, 10, 0).isoformat(), "message_id": 11},
        # Sent Tuesday morning: closes Tuesday 17:42 -> schedule.
        {"poll_id": "open", "practice_key": "tahajjud", "group_chat_id": GROUP_A,
         "created_at": at(TUE, 4, 10).isoformat(), "message_id": 12},
        # Recorded before message ids existed: cannot be closed; keep it.
        {"poll_id": "legacy", "practice_key": "duha", "group_chat_id": GROUP_A,
         "created_at": at(MON, 9, 0).isoformat(), "message_id": None},
    ]
    freeze(at(TUE, 12, 0), messaging)
    bot, jq = StopRecordingBot(), RecordingJobQueue()

    run(messaging.restore_poll_closings(bot, jq))

    assert bot.stopped == [(GROUP_A, 11)]
    assert jq.jobs == [("close_poll_job", at(TUE, 17, 42))]
    assert sorted(r["poll_id"] for r in fake_sb.rows("active_polls")) == ["legacy", "open"]
