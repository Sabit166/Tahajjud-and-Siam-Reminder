"""Group titles survive re-registration; groups the bot was removed from
are deactivated instead of failing on every scheduled run."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, Forbidden

import db
import handlers
import jobs
import messaging
from config import BD_TZ
from tests.conftest import GROUP_A, GROUP_B, amal_config


def run(coro):
    return asyncio.run(coro)


def ctx(bot):
    return SimpleNamespace(bot=bot, job_queue=None, job=None)


def group(fake_sb, chat_id):
    return next(g for g in fake_sb.rows("groups") if g["chat_id"] == chat_id)


# ------------------------------------------------------------
#  Fix 3: titles are not erased
# ------------------------------------------------------------

def test_registering_without_a_title_keeps_the_stored_title(fake_sb):
    db.register_group(GROUP_A, "Circle A", "circle_a")
    db.register_group(GROUP_A)  # e.g. from dashboard login
    g = group(fake_sb, GROUP_A)
    assert (g["title"], g["username"], g["is_active"]) == ("Circle A", "circle_a", True)


def test_new_title_still_updates(fake_sb):
    db.register_group(GROUP_A, "Old name")
    db.register_group(GROUP_A, "New name")
    assert group(fake_sb, GROUP_A)["title"] == "New name"


def test_dashboard_login_keeps_the_group_title(fake_sb):
    import web_server
    from tests.test_web_auth import signed_init_data

    fake_sb.tables["groups"] = [{"chat_id": GROUP_A, "title": "Circle A", "is_active": True}]
    token = db.issue_setup_token(GROUP_A, 42)
    req = web_server.AuthRequest(telegram_init_data=signed_init_data(42), setup_token=token)
    run(web_server.verify_auth(req))
    assert group(fake_sb, GROUP_A)["title"] == "Circle A"


# ------------------------------------------------------------
#  Fix 2: removed groups are deactivated
# ------------------------------------------------------------

@pytest.fixture
def due_poll(fake_sb, monkeypatch):
    now = dt.datetime.now(BD_TZ)
    if now.hour == 0 and now.minute < 2:
        pytest.skip("too close to midnight for a same-day fixed time")
    due = (now - dt.timedelta(seconds=30)).strftime("%H:%M")
    fake_sb.tables["poll_configs"] = [
        amal_config("quran", GROUP_A, fixed_time=due),
        amal_config("quran", GROUP_B, fixed_time=due),
    ]
    db.init_db()
    messaging._SCHEDULED_EVENTS.clear()
    yield
    messaging._SCHEDULED_EVENTS.clear()


def failing_for(bot, chat_id, exc):
    real = bot.send_poll

    async def send_poll(*a, **k):
        if k.get("chat_id", a[0] if a else None) == chat_id:
            raise exc
        return await real(*a, **k)

    bot.send_poll = send_poll


@pytest.mark.parametrize("exc", [
    Forbidden("Forbidden: bot was kicked from the group chat"),
    Forbidden("Forbidden: bot is not a member of the supergroup chat"),
    BadRequest("Chat not found"),
])
def test_group_that_removed_the_bot_is_deactivated(fake_sb, bot, due_poll, exc):
    failing_for(bot, GROUP_B, exc)

    run(jobs.prayer_schedule_job(ctx(bot)))

    assert group(fake_sb, GROUP_B)["is_active"] is False
    assert group(fake_sb, GROUP_A)["is_active"] is True
    assert [p["chat_id"] for p in bot.sent_polls] == [GROUP_A]
    assert [g["chat_id"] for g in db.list_groups()] == [GROUP_A]  # skipped from now on


def test_other_errors_do_not_deactivate_the_group(fake_sb, bot, due_poll):
    failing_for(bot, GROUP_B, RuntimeError("network blip"))
    run(jobs.prayer_schedule_job(ctx(bot)))
    assert group(fake_sb, GROUP_B)["is_active"] is True


def test_hadith_job_also_deactivates_removed_groups(fake_sb, bot, monkeypatch):
    async def send_hadith(b, prayer):
        if db.current_group_id() == GROUP_B:
            raise Forbidden("Forbidden: bot was kicked from the group chat")

    async def all_prayers_now(*_a, **_k):
        from prayer_times import PrayerTimes
        t = (dt.datetime.now(BD_TZ) - dt.timedelta(minutes=1)).time().replace(second=0, microsecond=0)
        return PrayerTimes(sunrise=t, fajr=t, dhuhr=t, asr=t, maghrib=t, isha=t)

    monkeypatch.setattr(messaging, "send_prayer_hadith", send_hadith)
    monkeypatch.setattr(messaging, "fetch_prayer_times", all_prayers_now)
    messaging._DISPATCHED_TODAY.clear()
    if dt.datetime.now(BD_TZ).hour == 0 and dt.datetime.now(BD_TZ).minute < 2:
        pytest.skip("too close to midnight")

    run(jobs.prayer_hadith_poll_job(ctx(bot)))

    assert group(fake_sb, GROUP_B)["is_active"] is False
    assert group(fake_sb, GROUP_A)["is_active"] is True
    messaging._DISPATCHED_TODAY.clear()


def test_leave_update_deactivates_and_keeps_title(fake_sb, bot):
    chat = SimpleNamespace(id=GROUP_B, type="group", title="Test group", username=None)
    update = SimpleNamespace(my_chat_member=SimpleNamespace(
        chat=chat,
        old_chat_member=SimpleNamespace(status="member"),
        new_chat_member=SimpleNamespace(status="kicked"),
    ))
    run(handlers.handle_my_chat_member(update, ctx(bot)))
    g = group(fake_sb, GROUP_B)
    assert (g["is_active"], g["title"]) == (False, "Test group")
    assert bot.sent_messages == []


# ------------------------------------------------------------
#  Startup refresh
# ------------------------------------------------------------

def test_startup_refresh_restores_titles_and_deactivates_removed_groups(fake_sb):
    from telegram.error import ChatMigrated

    GROUP_C, GROUP_D = -3003, -4004
    fake_sb.tables["groups"] = [
        {"chat_id": g, "title": None, "is_active": True} for g in (GROUP_A, GROUP_B, GROUP_C, GROUP_D)
    ]

    class ChatBot:
        async def get_chat(self, chat_id):
            if chat_id == GROUP_B:
                raise Forbidden("Forbidden: bot was kicked from the group chat")
            if chat_id == GROUP_C:
                raise ChatMigrated(-1009999)
            if chat_id == GROUP_D:
                raise RuntimeError("timeout")
            return SimpleNamespace(title="Circle A", username="circle_a")

    run(jobs.refresh_groups(ChatBot()))

    assert (group(fake_sb, GROUP_A)["title"], group(fake_sb, GROUP_A)["is_active"]) == ("Circle A", True)
    assert group(fake_sb, GROUP_B)["is_active"] is False
    # Migrated or temporarily unreachable groups are left alone.
    assert group(fake_sb, GROUP_C)["is_active"] is True
    assert group(fake_sb, GROUP_D)["is_active"] is True
