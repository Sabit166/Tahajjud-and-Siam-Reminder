"""New members are welcomed with their own group's amal list."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import db
import handlers
from tests.conftest import GROUP_A, GROUP_B, amal_config


def join(chat_id, *members):
    message = SimpleNamespace(chat_id=chat_id, new_chat_members=list(members))
    return SimpleNamespace(message=message)


def member(name, is_bot=False, user_id=None):
    return SimpleNamespace(id=user_id or abs(hash(name)) % 10**6, full_name=name, username=None, is_bot=is_bot)


def welcome(bot, chat_id, *members):
    ctx = SimpleNamespace(bot=bot, job_queue=None, job=None)
    asyncio.run(handlers.handle_new_member(join(chat_id, *members), ctx))
    return [m for m in bot.sent_messages if m["chat_id"] == chat_id]


def test_welcome_lists_only_this_groups_active_amals(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [
        amal_config("tahajjud", GROUP_A, title="Tahajjud Salat"),
        amal_config("sawm", GROUP_A, title="Sawm", days_of_week=[0, 3]),
        amal_config("duha", GROUP_A, title="Duha", is_active=False),
        amal_config("kahf", GROUP_B, title="Surah Kahf", days_of_week=[4]),
    ]
    db.init_db()

    [msg_a] = welcome(bot, GROUP_A, member("Alice"))
    assert "• Tahajjud Salat\n" in msg_a["text"]
    assert "• Sawm (Mon, Thu)\n" in msg_a["text"]
    assert "Duha" not in msg_a["text"]          # paused
    assert "Surah Kahf" not in msg_a["text"]    # other group

    [msg_b] = welcome(bot, GROUP_B, member("Bob"))
    assert "• Surah Kahf (Fri)\n" in msg_b["text"]
    assert "Tahajjud" not in msg_b["text"]


def test_welcome_without_amals_and_skips_bots(fake_sb, bot):
    db.init_db()
    msgs = welcome(bot, GROUP_B, member("Helper", is_bot=True), member("Bob"))
    assert len(msgs) == 1
    assert "amal list will be shared here soon" in msgs[0]["text"]
    assert "Morning Adhkar" not in msgs[0]["text"]  # no hardcoded list


def test_welcome_escapes_names_and_titles(fake_sb, bot):
    fake_sb.tables["poll_configs"] = [amal_config("q", GROUP_A, title="Quran <2 ayah>")]
    db.init_db()
    [msg] = welcome(bot, GROUP_A, member("<b>Eve</b>"))
    assert "&lt;b&gt;Eve&lt;/b&gt;" in msg["text"]
    assert "Quran &lt;2 ayah&gt;" in msg["text"]


# ------------------------------------------------------------
#  Joining members are recorded
# ------------------------------------------------------------

def test_joining_members_are_recorded_but_not_bots(fake_sb, bot):
    db.init_db()
    welcome(bot, GROUP_A, member("Helper", is_bot=True, user_id=50), member("Bob", user_id=51))
    assert fake_sb.rows("group_members") == [
        {"group_chat_id": GROUP_A, "user_id": 51, "full_name": "Bob", "username": ""}
    ]
    db.set_current_group(GROUP_A)
    assert db.get_known_members() == {51: "Bob"}


def test_welcome_still_works_if_members_table_is_missing(fake_sb, bot):
    fake_sb.fail_tables["group_members"] = "*"
    db.init_db()
    msgs = welcome(bot, GROUP_A, member("Bob", user_id=51))
    assert len(msgs) == 1                      # welcome sent anyway
    db.set_current_group(GROUP_A)
    assert db.get_known_members() == {}        # falls back to poll answers only


# ------------------------------------------------------------
#  Leaving members are forgotten
# ------------------------------------------------------------

def leave(bot, chat_id, user_id):
    bot.id = 999
    update = SimpleNamespace(message=SimpleNamespace(
        chat_id=chat_id, left_chat_member=SimpleNamespace(id=user_id)))
    asyncio.run(handlers.handle_left_member(update, SimpleNamespace(bot=bot)))


def test_leaving_member_is_removed_but_their_answers_stay(fake_sb, bot):
    fake_sb.tables["group_members"] = [
        {"group_chat_id": GROUP_A, "user_id": 51, "full_name": "Bob", "username": ""},
        {"group_chat_id": GROUP_B, "user_id": 51, "full_name": "Bob", "username": ""},  # other group
        {"group_chat_id": GROUP_A, "user_id": 52, "full_name": "Carol", "username": ""},
    ]
    fake_sb.tables["responses"] = [{"group_chat_id": GROUP_A, "user_id": 51, "full_name": "Bob",
                                    "practice": "q", "did_it": True, "response_date": "2026-10-16"}]
    leave(bot, GROUP_A, 51)
    assert [(r["group_chat_id"], r["user_id"]) for r in fake_sb.rows("group_members")] == [
        (GROUP_B, 51), (GROUP_A, 52)]
    assert len(fake_sb.rows("responses")) == 1


def test_bot_leaving_is_ignored_here(fake_sb, bot):
    fake_sb.tables["group_members"] = [{"group_chat_id": GROUP_A, "user_id": 999, "full_name": "x", "username": ""}]
    leave(bot, GROUP_A, 999)
    assert len(fake_sb.rows("group_members")) == 1


def test_leave_without_members_table_does_not_fail(fake_sb, bot):
    fake_sb.fail_tables["group_members"] = "*"
    leave(bot, GROUP_A, 51)   # logs a warning, no exception
