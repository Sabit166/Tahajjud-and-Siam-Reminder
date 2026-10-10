"""Mini App setup authentication and per-group dashboard authorization."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import db
import web_server
from config import TOKEN
from tests.conftest import GROUP_A, GROUP_B, amal_config


def signed_init_data(user_id=42, auth_date=None, token=TOKEN):
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": "Admin"}),
    }
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def verify(setup_token, init_data):
    req = web_server.AuthRequest(telegram_init_data=init_data, setup_token=setup_token)
    return asyncio.run(web_server.verify_auth(req))


def test_valid_init_data_is_accepted():
    # Regression: e78418d removed `import hashlib`, so this raised NameError.
    assert web_server.validate_telegram_init_data(signed_init_data(77))["id"] == 77


@pytest.mark.parametrize("init_data", [
    signed_init_data(token="999:OTHER-BOT"),
    signed_init_data(auth_date=time.time() - 2 * 86400),
    signed_init_data().replace("Admin", "Mallory"),
])
def test_forged_or_expired_init_data_is_rejected(init_data):
    with pytest.raises(HTTPException) as exc:
        web_server.validate_telegram_init_data(init_data)
    assert exc.value.status_code == 401


def test_setup_token_opens_the_originating_group(fake_sb):
    token = db.issue_setup_token(GROUP_A, 42)
    result = verify(token, signed_init_data(42))
    assert result["authenticated"] is True
    assert result["group_chat_id"] == GROUP_A
    [session] = fake_sb.rows("web_sessions")
    assert (session["chat_id"], session["user_id"], session["token"]) == (GROUP_A, 42, result["token"])

    # Tokens are single use.
    with pytest.raises(HTTPException) as exc:
        verify(token, signed_init_data(42))
    assert exc.value.status_code == 403


def test_setup_token_cannot_be_used_by_another_user(fake_sb):
    token = db.issue_setup_token(GROUP_A, 42)
    with pytest.raises(HTTPException) as exc:
        verify(token, signed_init_data(1001))
    assert exc.value.status_code == 403
    assert fake_sb.rows("web_sessions") == []


@pytest.fixture
def client(fake_sb, monkeypatch):
    async def no_timings():
        raise RuntimeError("offline")

    monkeypatch.setattr(web_server, "get_today_prayer_timings", no_timings)
    fake_sb.tables["poll_configs"] = [
        amal_config("quran", GROUP_A, title="Quran A"),
        amal_config("kahf", GROUP_B, title="Kahf B"),
    ]
    db.init_db()
    return TestClient(web_server.app)


def test_session_only_sees_and_edits_its_own_group(client):
    token_a = db.issue_session(GROUP_A, 42)
    headers = {"Authorization": f"Bearer {token_a}"}

    polls = client.get("/api/polls", headers=headers).json()["polls"]
    assert [p["title"] for p in polls] == ["Quran A"]

    body = {"title": "Hijacked", "poll_options": ["Y", "N"]}
    assert client.put("/api/polls/kahf", json=body, headers=headers).status_code == 404
    assert client.delete("/api/polls/kahf", headers=headers).status_code == 404
    assert [r["title"] for r in db._sb().rows("poll_configs") if r["group_chat_id"] == GROUP_B] == ["Kahf B"]


def test_dashboard_rejects_missing_or_invalid_session(client):
    assert client.get("/api/polls").status_code == 401
    assert client.get("/api/polls", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_session_creates_and_dispatches_in_its_own_group(client):
    import web_server as ws
    from tests.conftest import FakeBot

    bot = FakeBot()
    ws.set_bot_instance(bot, None)
    try:
        headers = {"Authorization": f"Bearer {db.issue_session(GROUP_B, 7)}"}
        body = {"title": "Ishraq", "poll_options": ["Done", "Missed"]}
        created = client.post("/api/polls", json=body, headers=headers).json()["poll"]
        assert created["group_chat_id"] == GROUP_B
        assert any(r["id"] == "ishraq" and r["group_chat_id"] == GROUP_B for r in db._sb().rows("poll_configs"))

        assert client.post("/api/polls/ishraq/trigger", headers=headers).status_code == 200
        assert [p["chat_id"] for p in bot.sent_polls] == [GROUP_B]
    finally:
        ws.set_bot_instance(None, None)
