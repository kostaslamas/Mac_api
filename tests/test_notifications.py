from __future__ import annotations

import plistlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mac_api.services.notifications import app_name, parse_payload

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def payload(bundle_id: str, title, body, subtitle=None) -> bytes:
    request = {"titl": title, "body": body}
    if subtitle is not None:
        request["subt"] = subtitle
    return plistlib.dumps({"app": bundle_id, "req": request, "uuid": uuid.uuid4().bytes}, fmt=plistlib.FMT_BINARY)


def seconds(hour: int, minute: int = 0) -> float:
    return (datetime(2026, 10, 5, hour, minute, tzinfo=timezone.utc) - APPLE_EPOCH).total_seconds()


@pytest.fixture
def notifications_db(settings) -> Path:
    """Shaped like ~/Library/Group Containers/group.com.apple.usernoted/db2/db on macOS 26."""
    path: Path = settings.notifications_db
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE app (app_id INTEGER PRIMARY KEY, identifier VARCHAR, badge INTEGER NULL);
        CREATE TABLE record (rec_id INTEGER PRIMARY KEY, app_id INTEGER, uuid BLOB, data BLOB,
            request_date REAL, request_last_date REAL, delivered_date REAL, presented Bool,
            style INTEGER, snooze_fire_date REAL);
        INSERT INTO app VALUES (1, 'com.viber.osx', NULL), (2, 'net.whatsapp.WhatsApp', NULL),
            (3, 'com.facebook.archon.developerID', NULL), (4, 'com.example.Weather', NULL);
        """
    )
    rows = [
        (1, payload("com.viber.osx", "Μαρία", "Θα αργήσω 10 λεπτά"), seconds(9)),
        (2, payload("net.whatsapp.WhatsApp", "Family", "Dinner at 8?", subtitle="Mom"), seconds(10)),
        (1, payload("com.viber.osx", "Γιώργος", "Ok!"), seconds(11)),
        # A localized field: [format, resolved, arguments]
        (3, payload("com.facebook.archon.developerID", ["%@ sent a photo", "Nikos sent a photo", ["Nikos"]], ""),
         seconds(12)),
        (4, payload("com.example.Weather", "Rain", "Rain expected at 17:00"), seconds(13)),
        (1, b"not a plist", seconds(8)),
        (1, payload("com.viber.osx", "Pending", "not delivered yet"), None),
    ]
    for app_id, data, delivered in rows:
        conn.execute(
            "INSERT INTO record (app_id, uuid, data, delivered_date) VALUES (?, ?, ?, ?)",
            (app_id, uuid.uuid4().bytes, data, delivered),
        )
    conn.commit()
    conn.close()
    return path


def test_parse_payload_and_names():
    assert parse_payload(payload("x", ["fmt", "resolved", []], "hi")) == {
        "bundle_id": "x", "title": "resolved", "subtitle": None, "body": "hi"}
    empty = {"bundle_id": None, "title": None, "subtitle": None, "body": None}
    assert parse_payload(b"garbage") == parse_payload(None) == empty
    assert app_name("com.viber.osx") == "Viber"
    assert app_name("com.example.Weather") == "Weather"


def test_list_newest_first(client, notifications_db):
    items = client.get("/notifications").json()
    assert [(n["app"], n["title"]) for n in items] == [
        ("Weather", "Rain"),
        ("Messenger", "Nikos sent a photo"),
        ("Viber", "Γιώργος"),
        ("WhatsApp", "Family"),
        ("Viber", "Μαρία"),
        ("Viber", None),  # unreadable payload: still listed, without text
    ]
    assert items[3]["subtitle"] == "Mom"
    assert items[0]["delivered_at"].startswith("2026-10-05")
    assert len(items[0]["id"]) == 36


def test_filters(client, notifications_db):
    def titles(**params):
        return [n["title"] for n in client.get("/notifications", params=params).json()]

    assert titles(app="viber") == ["Γιώργος", "Μαρία", None]
    assert titles(app="Messenger") == ["Nikos sent a photo"]  # by name, though the bundle id differs
    assert titles(app="telegram") == []
    assert titles(q="ΑΡΓΉΣΩ") == ["Μαρία"]
    assert titles(since="2026-10-05T10:30:00+00:00") == ["Rain", "Nikos sent a photo", "Γιώργος"]
    assert titles(limit=2) == ["Rain", "Nikos sent a photo"]


def test_apps(client, notifications_db):
    apps = client.get("/notifications/apps").json()
    assert [(a["app"], a["count"]) for a in apps] == [("Weather", 1), ("Messenger", 1), ("Viber", 3), ("WhatsApp", 1)]


def test_missing_database(client):
    response = client.get("/notifications")
    assert response.status_code == 503
    assert "Full Disk Access" in response.json()["hint"]
