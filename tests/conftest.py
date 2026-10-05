from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mac_api.app import create_app
from mac_api.config import Settings
from mac_api.services.messages import datetime_to_apple_time

API_KEY = "test-key"


def make_attributed_body(text: str) -> bytes:
    """Build an attributedBody blob shaped like the ones Messages writes."""
    encoded = text.encode("utf-8")
    if len(encoded) < 0x80:
        length = bytes([len(encoded)])
    else:
        length = b"\x81" + len(encoded).to_bytes(2, "little")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
        + length
        + encoded
        + b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01"
    )


MESSAGES_SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, service TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT, style INTEGER,
    chat_identifier TEXT, service_name TEXT, display_name TEXT);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT, text TEXT, attributedBody BLOB,
    handle_id INTEGER DEFAULT 0, service TEXT, date INTEGER, date_read INTEGER DEFAULT 0,
    is_from_me INTEGER DEFAULT 0, is_read INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0,
    associated_message_type INTEGER DEFAULT 0, associated_message_guid TEXT,
    thread_originator_guid TEXT, item_type INTEGER DEFAULT 0);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER, message_date INTEGER);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT, filename TEXT,
    mime_type TEXT, transfer_name TEXT, total_bytes INTEGER);
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
"""


def at(hour: int, minute: int = 0, day: int = 1) -> int:
    return datetime_to_apple_time(datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc))


class MessagesDB:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.executescript(MESSAGES_SCHEMA)

    def handle(self, identifier: str) -> int:
        return self.conn.execute("INSERT INTO handle (id, service) VALUES (?, 'iMessage')", (identifier,)).lastrowid

    def chat(self, guid: str, handles: list[int], style: int = 45, display_name: str = "") -> int:
        chat_id = self.conn.execute(
            "INSERT INTO chat (guid, style, chat_identifier, service_name, display_name) VALUES (?, ?, ?, 'iMessage', ?)",
            (guid, style, guid.split(";")[-1], display_name),
        ).lastrowid
        for handle_id in handles:
            self.conn.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (chat_id, handle_id))
        return chat_id

    def message(self, chat_id: int, handle_id: int, date: int, text: str | None = None, **fields) -> int:
        columns = {"guid": f"msg-{date}-{handle_id}", "text": text, "handle_id": handle_id, "date": date,
                   "service": "iMessage", **fields}
        names = ", ".join(columns)
        marks = ", ".join("?" * len(columns))
        message_id = self.conn.execute(f"INSERT INTO message ({names}) VALUES ({marks})", list(columns.values())).lastrowid
        self.conn.execute("INSERT INTO chat_message_join VALUES (?, ?, ?)", (chat_id, message_id, date))
        return message_id

    def attachment(self, message_id: int, filename: str, mime_type: str, transfer_name: str) -> int:
        attachment_id = self.conn.execute(
            "INSERT INTO attachment (guid, filename, mime_type, transfer_name, total_bytes) VALUES (?, ?, ?, ?, 3)",
            (f"att-{message_id}", filename, mime_type, transfer_name),
        ).lastrowid
        self.conn.execute("INSERT INTO message_attachment_join VALUES (?, ?)", (message_id, attachment_id))
        self.conn.execute("UPDATE message SET cache_has_attachments = 1 WHERE ROWID = ?", (message_id,))
        return attachment_id

    def commit(self) -> None:
        self.conn.commit()
        self.conn.close()


ADDRESSBOOK_SCHEMA = """
CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZMIDDLENAME TEXT, ZLASTNAME TEXT,
    ZORGANIZATION TEXT, ZNICKNAME TEXT);
CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT, ZLABEL TEXT);
CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT, ZLABEL TEXT);
"""


def make_addressbook(directory: Path, source: str, records: list[dict]) -> Path:
    path = directory / "Sources" / source / "AddressBook-v22.abcddb"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(ADDRESSBOOK_SCHEMA)
    for pk, record in enumerate(records, start=1):
        conn.execute(
            "INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION) VALUES (?, ?, ?, ?)",
            (pk, record.get("first"), record.get("last"), record.get("org")),
        )
        for number in record.get("phones", []):
            conn.execute(
                "INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER, ZLABEL) VALUES (?, ?, '_$!<Mobile>!$_')",
                (pk, number),
            )
        for address in record.get("emails", []):
            conn.execute(
                "INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZADDRESS, ZLABEL) VALUES (?, ?, '_$!<Home>!$_')",
                (pk, address),
            )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def addressbook_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "AddressBook"
    make_addressbook(
        directory,
        "AAAA-1111",
        [
            {"first": "Κώστας", "last": "Λάμπρου", "phones": ["+30 691 234 5678"]},
            {"first": "Maria", "last": "Smith", "emails": ["Maria@Example.com"], "phones": ["(555) 010-9999"]},
            {"org": "Pizza Place", "phones": ["210 555 1234"]},
            {},  # a group record: no name, phone or email
        ],
    )
    # The same person synced from a second account must not show up twice.
    make_addressbook(directory, "BBBB-2222", [{"first": "Maria", "last": "Smith", "emails": ["maria@example.com"],
                                              "phones": ["+1 555 010 9999"]}])
    return directory


@pytest.fixture
def messages_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "Messages"
    (directory / "Attachments" / "ab").mkdir(parents=True)
    (directory / "Attachments" / "ab" / "photo.jpg").write_bytes(b"jpg")
    return directory


@pytest.fixture
def messages_db(messages_dir: Path) -> dict[str, int]:
    """A small conversation history:

    chat 1: 1:1 with Kostas (+306912345678); one message only in attributedBody, a reaction, a photo
    chat 2: group "Friends" with Kostas and maria@example.com, newest activity
    chat 3: 1:1 with an unknown number
    """
    db = MessagesDB(messages_dir / "chat.db")
    kostas = db.handle("+306912345678")
    maria = db.handle("maria@example.com")
    stranger = db.handle("+447700900123")

    chat1 = db.chat("iMessage;-;+306912345678", [kostas])
    db.message(chat1, kostas, at(9), "Καλημέρα!", is_read=1)
    db.message(chat1, kostas, at(9, 5), None, attributedBody=make_attributed_body("Are we still on for lunch?"),
               is_read=1)
    reply = db.message(chat1, kostas, at(9, 10), "Yes, 13:00", is_from_me=1, is_read=1)
    db.message(chat1, kostas, at(9, 11), None, associated_message_type=2001,
               associated_message_guid=f"p:0/msg-{at(9, 10)}-{kostas}")
    photo = db.message(chat1, kostas, at(9, 20), "￼", is_read=0)
    attachment = db.attachment(photo, str(messages_dir / "Attachments" / "ab" / "photo.jpg"), "image/jpeg",
                               "photo.jpg")
    outside = db.message(chat1, kostas, at(9, 21), "￼", is_read=1)
    outside_attachment = db.attachment(outside, "/etc/passwd", "text/plain", "passwd")

    chat2 = db.chat("iMessage;+;chat123", [kostas, maria], style=43, display_name="Friends")
    db.message(chat2, maria, at(11), "Pizza tonight?", is_read=0)
    db.message(chat2, kostas, at(11, 1), None, attributedBody=make_attributed_body("Pizza " * 40), is_read=0)
    db.message(chat2, 0, at(11, 2), None, item_type=2)  # "renamed the conversation"

    chat3 = db.chat("SMS;-;+447700900123", [stranger])
    db.message(chat3, stranger, at(8), "Your code is 123456", is_read=1)
    db.commit()
    return {"chat1": chat1, "chat2": chat2, "chat3": chat3, "reply": reply, "attachment": attachment,
            "outside_attachment": outside_attachment}


@pytest.fixture
def settings(tmp_path: Path, messages_dir: Path, addressbook_dir: Path) -> Settings:
    return Settings(
        api_key=API_KEY,
        messages_db=messages_dir / "chat.db",
        messages_attachments_dir=messages_dir / "Attachments",
        addressbook_dir=addressbook_dir,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), headers={"X-API-Key": API_KEY})


@pytest.fixture
def fake_jxa(monkeypatch):
    """Replace osascript for JXA calls; queue results with `fake_jxa.results.append(...)`."""

    class FakeJXA:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []
            self.results: list = []

        def __call__(self, body: str, args=None, *, timeout=None):
            self.calls.append((body, args or {}))
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    fake = FakeJXA()
    for module in ("reminders", "notes", "calendar", "system"):
        monkeypatch.setattr(f"mac_api.services.{module}.run_jxa", fake)
    return fake
