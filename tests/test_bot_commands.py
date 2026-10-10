"""The '/' command menu exposes /setup to group administrators only."""

from __future__ import annotations

import asyncio

from telegram import BotCommandScopeAllChatAdministrators

import main


class RecordingBot:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def set_my_commands(self, commands, scope=None, **_k):
        if self.fail:
            raise RuntimeError("Telegram unavailable")
        self.calls.append((commands, scope))
        return True


def test_setup_command_is_registered_for_group_admins():
    bot = RecordingBot()
    asyncio.run(main.register_bot_commands(bot))
    [(commands, scope)] = bot.calls
    assert [c.command for c in commands] == ["setup"]
    assert isinstance(scope, BotCommandScopeAllChatAdministrators)


def test_command_registration_failure_does_not_stop_startup():
    asyncio.run(main.register_bot_commands(RecordingBot(fail=True)))
