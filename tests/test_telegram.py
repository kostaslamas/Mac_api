from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from telethon.tl.types import Channel, Chat, User

from mac_api import cli
from mac_api.app import create_app

from .helpers import API_KEY, call_tools, serve, text

MARIA = User(id=101, first_name="Μαρία", last_name="Π", username="maria", phone="306912345678")
BOT = User(id=102, first_name="Weather", bot=True, username="weatherbot")
FAMILY = Chat(id=42, title="Family", photo=None, participants_count=3, date=None, version=1)
NEWS = Channel(id=7, title="News", photo=None, date=None, broadcast=True, megagroup=False)
ME = User(id=1, is_self=True, first_name="Kostas", username="kostas", phone="306900000000")


def message(id, chat_id, text, *, out=False, sender=None, minute=0, **extra):
    fields = {"reply_to_msg_id": None, "media": None, **extra}
    return SimpleNamespace(
        id=id, chat_id=chat_id, message=text, out=out, sender=sender, sender_id=getattr(sender, "id", None),
        date=datetime(2026, 10, 5, 9, minute, tzinfo=timezone.utc), **fields,
    )


def dialog(entity, last, unread=0, *, group=False, channel=False):
    from telethon import utils

    return SimpleNamespace(id=entity.id, name=utils.get_display_name(entity), entity=entity, unread_count=unread,
                           message=last, is_user=isinstance(entity, User), is_group=group, is_channel=channel)


class FakeTelegramClient:
    """The subset of TelegramClient that mac-api uses."""

    def __init__(self) -> None:
        self.history = {
            101: [message(3, 101, "Τα λέμε αύριο", sender=MARIA, minute=3),
                  message(2, 101, "Ok", out=True, sender=ME, minute=2),
                  message(1, 101, "Πάμε για καφέ;", sender=MARIA, minute=1)],
            42: [message(10, 42, None, sender=MARIA, minute=5, photo=object(), media=object())],
        }
        self.dialogs = [
            dialog(FAMILY, self.history[42][0], unread=2, group=True),
            dialog(MARIA, self.history[101][0]),
            dialog(BOT, None),
            dialog(NEWS, None, unread=5, channel=True),
        ]
        self.dialogs_loaded = False
        self.calls: list[tuple] = []

    async def get_entity(self, target):
        by_key = {101: MARIA, "@maria": MARIA, "maria": MARIA, 7: NEWS}
        if self.dialogs_loaded:
            by_key[42] = FAMILY  # numeric ids of groups resolve only after the dialogs are loaded
        if target in by_key:
            return by_key[target]
        raise ValueError(f"Cannot find any entity corresponding to {target!r}")

    async def get_dialogs(self, limit=None):
        self.dialogs_loaded = True
        return self.dialogs[:limit] if limit else self.dialogs

    async def get_messages(self, entity, limit=None, offset_id=0, search=None):
        self.calls.append(("get_messages", getattr(entity, "id", entity), limit, offset_id, search))
        if search is not None:
            pool = [m for chat in self.history.values() for m in chat] if entity is None else self.history[entity.id]
            found = [m for m in pool if m.message and search.casefold() in m.message.casefold()]
            for m in found:
                m.chat = MARIA if m.chat_id == 101 else FAMILY
            return found[:limit]
        found = [m for m in self.history.get(getattr(entity, "id", entity), []) if not offset_id or m.id < offset_id]
        return found[:limit]

    async def send_message(self, entity, text, reply_to=None, parse_mode=()):
        self.calls.append(("send_message", getattr(entity, "id", entity), text, reply_to, parse_mode))
        return message(99, getattr(entity, "id", 1), text, out=True, sender=ME, minute=30)

    async def send_read_acknowledge(self, entity):
        self.calls.append(("read", entity.id))

    async def get_me(self):
        return ME

    async def disconnect(self):
        pass


@pytest.fixture
def telegram_settings(settings, tmp_path):
    settings.telegram_api_id = 12345
    settings.telegram_api_hash = "hash"
    settings.telegram_session = tmp_path / "telegram.session"
    return settings


@pytest.fixture
def fake_client() -> FakeTelegramClient:
    return FakeTelegramClient()


@pytest.fixture
def tg(telegram_settings, fake_client) -> TestClient:
    app = create_app(telegram_settings)
    app.state.telegram.client = fake_client
    return TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


def test_not_set_up(client):
    assert client.get("/telegram/status").json() == {
        "configured": False, "connected": False, "user": None, "error": "Telegram is not set up"}
    response = client.get("/telegram/chats")
    assert response.status_code == 503
    assert "--telegram-login" in response.json()["hint"]


def test_configured_but_not_logged_in(telegram_settings):
    with TestClient(create_app(telegram_settings), headers={"Authorization": f"Bearer {API_KEY}"}) as client:
        status = client.get("/telegram/status").json()
        assert (status["configured"], status["connected"], status["error"]) == (True, False, "Not logged in to Telegram")
        assert client.get("/diagnostics").json()["telegram"]["error"] == "Not logged in to Telegram"


class FlakyTelegramClient:
    """Stands in for TelegramClient: the first connection attempt fails."""

    attempts = 0
    authorized = True

    def __init__(self, *args, **kwargs):
        pass

    async def connect(self):
        FlakyTelegramClient.attempts += 1
        if FlakyTelegramClient.attempts == 1:
            raise OSError("network is unreachable")

    async def is_user_authorized(self):
        return FlakyTelegramClient.authorized

    async def disconnect(self):
        pass


@pytest.mark.parametrize("authorized", [True, False])
def test_background_connect_retries_and_detects_expired_sessions(telegram_settings, monkeypatch, authorized):
    import asyncio

    from mac_api.services import telegram

    telegram_settings.telegram_session.write_text("")
    monkeypatch.setattr(telegram, "TelegramClient", FlakyTelegramClient)
    monkeypatch.setattr(telegram, "RETRY_SECONDS", 0)
    monkeypatch.setattr(FlakyTelegramClient, "attempts", 0)
    monkeypatch.setattr(FlakyTelegramClient, "authorized", authorized)
    service = telegram.TelegramService(telegram_settings)

    async def run():
        await service.start()
        assert service.error == "Still connecting to Telegram"
        await service._task

    asyncio.run(run())
    assert FlakyTelegramClient.attempts == 2
    if authorized:
        assert service.client is not None and service.error is None
    else:
        assert service.client is None and service.needs_login
        with pytest.raises(Exception) as excinfo:
            service._require()
        assert "--telegram-login" in excinfo.value.hint


def test_status(tg):
    status = tg.get("/telegram/status").json()
    assert status["connected"] is True
    assert status["user"] == {"id": 1, "name": "Kostas", "username": "kostas", "phone": "306900000000"}


def test_chats(tg):
    chats = tg.get("/telegram/chats").json()
    assert [(c["name"], c["type"], c["unread_count"]) for c in chats] == [
        ("Family", "group", 2), ("Μαρία Π", "user", 0), ("Weather", "bot", 0), ("News", "channel", 5)]
    assert chats[0]["last_message"]["media"] == "photo"
    assert chats[1]["last_message"]["sender_name"] == "Μαρία Π"
    assert [c["name"] for c in tg.get("/telegram/chats", params={"unread_only": True}).json()] == ["Family", "News"]


def test_chat_messages(tg, fake_client):
    messages = tg.get("/telegram/chats/101/messages").json()
    assert [(m["text"], m["sender_name"]) for m in messages] == [
        ("Πάμε για καφέ;", "Μαρία Π"), ("Ok", "Me"), ("Τα λέμε αύριο", "Μαρία Π")]
    assert messages[0]["date"].startswith("2026-10-05")

    older = tg.get("/telegram/chats/@maria/messages", params={"before_id": 3, "limit": 1}).json()
    assert [m["id"] for m in older] == [2]
    assert fake_client.calls[-1] == ("get_messages", 101, 1, 3, None)

    # A group id is unknown until the dialogs are loaded; the service loads them and retries.
    assert [m["media"] for m in tg.get("/telegram/chats/42/messages").json()] == ["photo"]
    assert tg.get("/telegram/chats/999/messages").status_code == 404


def test_search(tg, fake_client):
    found = tg.get("/telegram/messages", params={"q": "ΚΑΦΈ"}).json()
    assert [(m["text"], m["chat_name"]) for m in found] == [("Πάμε για καφέ;", "Μαρία Π")]
    assert fake_client.calls[-1] == ("get_messages", None, 30, 0, "ΚΑΦΈ")
    assert tg.get("/telegram/messages").status_code == 422


def test_send_and_mark_read(tg, fake_client):
    response = tg.post("/telegram/send", json={"to": "@maria", "text": "**Γεια**", "reply_to": 3})
    assert response.status_code == 200
    assert response.json()["is_from_me"] is True
    assert fake_client.calls[-1] == ("send_message", 101, "**Γεια**", 3, None)  # sent literally, no Markdown

    assert tg.post("/telegram/send", json={"to": "@maria", "text": "  "}).status_code == 400
    assert tg.post("/telegram/send", json={"to": "@nobody", "text": "hi"}).status_code == 404

    assert tg.post("/telegram/chats/42/read").status_code == 204
    assert fake_client.calls[-1] == ("read", 42)

    tg.app.state.settings.read_only = True
    assert tg.post("/telegram/send", json={"to": "@maria", "text": "hi"}).status_code == 403


def test_mcp_tools(telegram_settings, fake_client):
    app = create_app(telegram_settings)
    app.state.telegram.client = fake_client
    url_iter = serve(app)
    url = next(url_iter)
    try:
        tools, (chats, sent) = call_tools(
            url, [("telegram_chats", {"unread_only": True}), ("telegram_send", {"to": "101", "text": "Έρχομαι"})]
        )
    finally:
        next(url_iter, None)
    assert {"telegram_chats", "telegram_read", "telegram_search", "telegram_send", "telegram_mark_read"} <= set(tools)
    assert [c["name"] for c in json.loads(text(chats))] == ["Family", "News"]
    assert not sent.is_error
    assert fake_client.calls[-1] == ("send_message", 101, "Έρχομαι", None, None)


def test_mcp_tools_absent_without_telegram(base_url):
    tools, _ = call_tools(base_url, [])
    assert not any(name.startswith("telegram_") for name in tools)


def test_cli_login_stores_credentials(monkeypatch, tmp_path, capsys):
    credentials = tmp_path / "telegram.json"
    session = tmp_path / "telegram.session"
    monkeypatch.setattr(cli, "TELEGRAM_CREDENTIALS_FILE", credentials)
    monkeypatch.setenv("MAC_API_TELEGRAM_SESSION", str(session))
    monkeypatch.delenv("MAC_API_TELEGRAM_API_ID", raising=False)
    monkeypatch.setattr("mac_api.config.TELEGRAM_CREDENTIALS_FILE", tmp_path / "absent.json")
    answers = iter(["12345", "abcdef"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    async def fake_login(settings):
        assert (settings.telegram_api_id, settings.telegram_api_hash) == (12345, "abcdef")
        settings.telegram_session.write_text("session")
        return "Kostas"

    monkeypatch.setattr("mac_api.services.telegram.login", fake_login)
    cli.main(["--api-key", "x", "--telegram-login"])

    assert json.loads(credentials.read_text()) == {"api_id": 12345, "api_hash": "abcdef"}
    assert credentials.stat().st_mode & 0o777 == 0o600
    assert session.stat().st_mode & 0o777 == 0o600
    assert "Logged in to Telegram as Kostas" in capsys.readouterr().out
