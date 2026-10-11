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


def member(name, is_bot=False):
    return SimpleNamespace(full_name=name, username=None, is_bot=is_bot)


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
