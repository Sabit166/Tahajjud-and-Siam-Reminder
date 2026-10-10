"""Shared fixtures: an in-memory stand-in for the Supabase client and a
fake Telegram bot, so the bot's real code paths run without network."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

# Must be set before config.py is imported; load_dotenv() never overrides
# variables that already exist, so the real .env is not used.
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_API_KEY"] = "test-key"
os.environ["WEB_APP_URL"] = "https://dashboard.example"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import db  # noqa: E402


class _Query:
    def __init__(self, store: "FakeSupabase", table: str):
        self.store = store
        self.table = table
        self.filters = []
        self.op = "select"
        self.payload = None
        self.on_conflict = None
        self.limit_n = None

    # --- builders ---
    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def is_(self, col, val):
        assert val == "null"
        self.filters.append(lambda r: r.get(col) is None)
        return self

    def gt(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) > str(val))
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) >= str(val))
        return self

    def lt(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) < str(val))
        return self

    def lte(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) <= str(val))
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload = "upsert", payload
        self.on_conflict = on_conflict or self.store.primary_keys.get(self.table)
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    # --- execution ---
    def _match(self, row):
        return all(f(row) for f in self.filters)

    def execute(self):
        if self.store.fail_tables.get(self.table) in (self.op, "*"):
            raise RuntimeError(f"simulated failure on {self.table}.{self.op}")
        rows = self.store.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            return SimpleNamespace(data=out[: self.limit_n] if self.limit_n else out)
        if self.op == "insert":
            rows.append(dict(self.payload))
            return SimpleNamespace(data=[dict(self.payload)])
        if self.op == "upsert":
            keys = [k.strip() for k in (self.on_conflict or "").split(",") if k.strip()]
            for r in rows:
                if keys and all(r.get(k) == self.payload.get(k) for k in keys):
                    r.update(self.payload)
                    return SimpleNamespace(data=[dict(r)])
            rows.append(dict(self.payload))
            return SimpleNamespace(data=[dict(self.payload)])
        if self.op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in hit])
        if self.op == "delete":
            hit = [r for r in rows if self._match(r)]
            self.store.tables[self.table] = [r for r in rows if not self._match(r)]
            return SimpleNamespace(data=hit)
        raise AssertionError(self.op)


class FakeSupabase:
    primary_keys = {
        "groups": "chat_id",
        "active_polls": "group_chat_id,poll_id",
        "poll_configs": "group_chat_id,id",
        "streaks": "group_chat_id,user_id,practice",
    }

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.fail_tables: dict[str, str] = {}

    def table(self, name):
        return _Query(self, name)

    def rows(self, name):
        return self.tables.get(name, [])


class FakeBot:
    def __init__(self):
        self.sent_messages: list[dict] = []
        self.sent_polls: list[dict] = []
        self._next_poll = 1000
        self._next_msg = 1

    async def send_poll(self, chat_id, question, options, **_k):
        self._next_poll += 1
        self._next_msg += 1
        self.sent_polls.append({"chat_id": chat_id, "question": question, "options": options})
        return SimpleNamespace(
            chat_id=chat_id,
            message_id=self._next_msg,
            poll=SimpleNamespace(id=f"tg-poll-{self._next_poll}"),
        )

    async def send_message(self, chat_id, text, **_k):
        self._next_msg += 1
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(chat_id=chat_id, message_id=self._next_msg)


GROUP_A = -1001
GROUP_B = -2002


def amal_config(poll_id, group, title=None, **extra):
    row = {
        "group_chat_id": group,
        "id": poll_id,
        "title": title or poll_id.replace("_", " ").title(),
        "poll_type": "amal_poll",
        "poll_options": ["Done", "Missed"],
        "weight": 10,
        "time_type": "fixed",
        "fixed_time": "10:00",
        "prayer_name": None,
        "prayer_offset_minutes": 0,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "is_active": True,
        "created_at": "2026-01-01T00:00:00+06:00",
    }
    row.update(extra)
    return row


@pytest.fixture
def fake_sb(monkeypatch):
    fake = FakeSupabase()
    fake.tables["groups"] = [
        {"chat_id": GROUP_A, "title": "A", "is_active": True},
        {"chat_id": GROUP_B, "title": "B", "is_active": True},
    ]
    monkeypatch.setattr(db, "_sb", lambda: fake)
    monkeypatch.setattr(db, "_SUPABASE_POLL_CONFIGS_AVAILABLE", None)
    db._POLL_CONFIGS_CACHE.clear()
    db._ACTIVE_POLL_CACHE.clear()
    db._current_group_id.set(0)
    yield fake
    db._POLL_CONFIGS_CACHE.clear()
    db._ACTIVE_POLL_CACHE.clear()
    db._current_group_id.set(0)


@pytest.fixture
def bot():
    return FakeBot()
