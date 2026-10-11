"""Setup prompt when the bot joins a group, and admin-only access to it."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

import handlers
from tests.conftest import GROUP_A

ADMIN, MEMBER = 42, 77


class SetupBot:
    def __init__(self, admins=(ADMIN,)):
        self.admins = set(admins)
        self.sent = []

    async def get_me(self):
        return SimpleNamespace(username="amal_bot")

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator" if user_id in self.admins else "member")

    async def send_message(self, chat_id, text=None, reply_markup=None, **_k):
        self.sent.append({"chat_id": chat_id, "text": text, "markup": reply_markup})
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.sent))


def run(coro):
    return asyncio.run(coro)


def ctx(bot, args=None):
    return SimpleNamespace(bot=bot, args=args or [], job_queue=None, job=None)


def button(message):
    return message["markup"].inline_keyboard[0][0]


def start_payload(message):
    return parse_qs(urlparse(button(message).url).query)["start"][0]


def bot_added(old="left", new="member", chat_id=GROUP_A):
    chat = SimpleNamespace(id=chat_id, type="supergroup", title="Circle A", username=None)
    return SimpleNamespace(my_chat_member=SimpleNamespace(
        chat=chat,
        old_chat_member=SimpleNamespace(status=old),
        new_chat_member=SimpleNamespace(status=new),
    ))


def private_start(user_id):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=user_id, type="private"),
        effective_user=SimpleNamespace(id=user_id),
    )


@pytest.fixture
def groups(fake_sb):
    fake_sb.tables["groups"] = []
    return fake_sb


def test_adding_the_bot_posts_the_setup_prompt(groups):
    bot = SetupBot()
    run(handlers.handle_my_chat_member(bot_added(), ctx(bot)))

    [msg] = bot.sent
    assert msg["chat_id"] == GROUP_A
    assert "Thank you for adding me" in msg["text"]
    assert "Only group administrators can open the dashboard" in msg["text"]
    assert button(msg).text == "💬 Open private chat with bot"
    assert start_payload(msg) == f"admin_{GROUP_A}"
    assert [g["chat_id"] for g in groups.rows("groups")] == [GROUP_A]


def test_promoting_the_bot_does_not_repost_the_prompt(groups):
    bot = SetupBot()
    run(handlers.handle_my_chat_member(bot_added(old="member", new="administrator"), ctx(bot)))
    assert bot.sent == []


def test_admin_opening_the_join_link_gets_their_own_dashboard_button(groups):
    bot = SetupBot()
    run(handlers.handle_my_chat_member(bot_added(), ctx(bot)))
    payload = start_payload(bot.sent[0])

    run(handlers.handle_start(private_start(ADMIN), ctx(bot, [payload])))

    reply = bot.sent[-1]
    assert reply["chat_id"] == ADMIN
    assert "verified" in reply["text"]
    [token] = groups.rows("setup_tokens")
    assert (token["chat_id"], token["issued_by"]) == (GROUP_A, ADMIN)
    assert button(reply).web_app.url.endswith(f"?setup_token={token['token']}")


def test_non_admin_opening_the_join_link_is_refused(groups):
    bot = SetupBot()
    run(handlers.handle_my_chat_member(bot_added(), ctx(bot)))
    payload = start_payload(bot.sent[0])

    run(handlers.handle_start(private_start(MEMBER), ctx(bot, [payload])))

    assert bot.sent[-1]["text"] == "Only administrators of Circle A can open its dashboard."
    assert bot.sent[-1]["markup"] is None
    assert groups.rows("setup_tokens") == []


@pytest.mark.parametrize("payload", ["admin_not-a-number", "admin_-999", "admin_"])
def test_join_link_for_unknown_group_is_refused(groups, payload):
    bot = SetupBot()
    run(handlers.handle_my_chat_member(bot_added(), ctx(bot)))  # registers GROUP_A only

    run(handlers.handle_start(private_start(ADMIN), ctx(bot, [payload])))

    assert "not valid" in bot.sent[-1]["text"]
    assert groups.rows("setup_tokens") == []


def test_setup_command_still_binds_the_link_to_the_admin(groups):
    bot = SetupBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=ADMIN),
        effective_chat=SimpleNamespace(id=GROUP_A, title="Circle A", username=None),
    )
    run(handlers.handle_admin_command(update, ctx(bot)))

    [msg] = bot.sent
    [token] = groups.rows("setup_tokens")
    assert start_payload(msg) == f"setup_{token['token']}"
    assert token["issued_by"] == ADMIN
