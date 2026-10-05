from __future__ import annotations

from datetime import datetime, timezone

from mac_api.errors import MacAPIError
from mac_api.services.messages import apple_time_to_datetime, datetime_to_apple_time, decode_attributed_body

from .conftest import make_attributed_body


def test_decode_attributed_body_short_and_long_strings():
    assert decode_attributed_body(make_attributed_body("hello")) == "hello"
    long_text = "Γεια σου! " * 50  # > 255 bytes, so the length uses the 0x81 two-byte form
    assert decode_attributed_body(make_attributed_body(long_text)) == long_text
    assert decode_attributed_body(None) is None
    assert decode_attributed_body(b"no string here") is None


def test_apple_time_round_trip():
    moment = datetime(2026, 10, 5, 12, 30, tzinfo=timezone.utc)
    assert apple_time_to_datetime(datetime_to_apple_time(moment)) == moment
    # Very old databases store seconds rather than nanoseconds.
    assert apple_time_to_datetime(0) is None
    assert apple_time_to_datetime(1) == datetime(2001, 1, 1, 0, 0, 1, tzinfo=timezone.utc)


def test_list_chats(client, messages_db):
    chats = client.get("/messages/chats").json()
    assert [c["id"] for c in chats] == [messages_db["chat2"], messages_db["chat1"], messages_db["chat3"]]

    group, direct, unknown = chats
    assert group["name"] == "Friends"
    assert group["is_group"] is True
    assert {p["name"] for p in group["participants"]} == {"Κώστας Λάμπρου", "Maria Smith"}
    assert group["unread_count"] == 2
    assert group["last_message"]["text"].startswith("Pizza Pizza")
    assert group["last_message"]["sender_name"] == "Κώστας Λάμπρου"

    assert direct["name"] == "Κώστας Λάμπρου"
    assert direct["is_group"] is False
    assert direct["unread_count"] == 1  # the photo; the reaction is not counted

    assert unknown["name"] == "+447700900123"
    assert unknown["participants"][0]["name"] is None


def test_chat_messages_are_chronological_without_reactions(client, messages_db):
    messages = client.get(f"/messages/chats/{messages_db['chat1']}/messages").json()
    texts = [m["text"] for m in messages]
    assert texts == ["Καλημέρα!", "Are we still on for lunch?", "Yes, 13:00", None, None]
    reply = messages[2]
    assert reply["is_from_me"] is True
    assert reply["sender"] is None
    assert reply["sender_name"] == "Me"
    photo = messages[3]
    assert photo["attachments"][0]["mime_type"] == "image/jpeg"
    assert photo["attachments"][0]["filename"] == "photo.jpg"


def test_chat_messages_with_reactions_and_paging(client, messages_db):
    messages = client.get(
        f"/messages/chats/{messages_db['chat1']}/messages", params={"include_reactions": True}
    ).json()
    reaction = next(m for m in messages if m["reaction"])
    assert reaction["reaction"] == "liked"
    assert reaction["reaction_to"] == next(m["guid"] for m in messages if m["text"] == "Yes, 13:00")

    latest_two = client.get(f"/messages/chats/{messages_db['chat1']}/messages", params={"limit": 2}).json()
    older = client.get(
        f"/messages/chats/{messages_db['chat1']}/messages",
        params={"limit": 2, "before": latest_two[0]["date"]},
    ).json()
    assert [m["text"] for m in older] == ["Are we still on for lunch?", "Yes, 13:00"]


def test_unknown_chat_is_404(client, messages_db):
    assert client.get("/messages/chats/999").status_code == 404
    assert client.get("/messages/chats/999/messages").status_code == 404


def test_search_covers_text_and_attributed_body(client, messages_db):
    assert [m["text"] for m in client.get("/messages", params={"q": "lunch"}).json()] == [
        "Are we still on for lunch?"
    ]
    assert [m["text"] for m in client.get("/messages", params={"q": "13:00"}).json()] == ["Yes, 13:00"]
    assert [m["text"] for m in client.get("/messages", params={"q": "καλημέρα"}).json()] == []  # case-sensitive
    assert len(client.get("/messages", params={"q": "100%"}).json()) == 0  # LIKE wildcards are escaped


def test_filters(client, messages_db):
    by_handle = client.get("/messages", params={"handle": "+30 691 234 5678"}).json()
    assert {m["sender"] for m in by_handle} == {"+306912345678", None}

    unread = client.get("/messages", params={"unread_only": True}).json()
    assert [m["chat_id"] for m in unread] == [messages_db["chat2"], messages_db["chat2"], messages_db["chat1"]]

    since = client.get("/messages", params={"since": "2026-10-01T10:00:00+00:00"}).json()
    assert {m["chat_id"] for m in since} == {messages_db["chat2"]}

    incoming = client.get("/messages", params={"incoming_only": True, "chat_id": messages_db["chat1"]}).json()
    assert all(not m["is_from_me"] for m in incoming)


def test_attachment_download(client, messages_db):
    response = client.get(f"/messages/attachments/{messages_db['attachment']}")
    assert response.status_code == 200
    assert response.content == b"jpg"
    assert response.headers["content-type"] == "image/jpeg"

    assert client.get(f"/messages/attachments/{messages_db['outside_attachment']}").status_code == 403
    assert client.get("/messages/attachments/999").status_code == 404


def test_missing_database_explains_itself(client):
    response = client.get("/messages/chats")
    assert response.status_code == 503
    assert "Full Disk Access" in response.json()["hint"]


def test_send_message(client, monkeypatch):
    calls = []
    monkeypatch.setattr("mac_api.services.messages.run_applescript", lambda script, *args: calls.append(args))

    response = client.post("/messages/send", json={"to": " +306912345678 ", "text": "Γεια!"})
    assert response.status_code == 202
    response = client.post("/messages/send", json={"chat_guid": "iMessage;+;chat123", "text": "hi all"})
    assert response.status_code == 202
    assert calls == [("+306912345678", "Γεια!", "iMessage"), ("iMessage;+;chat123", "hi all")]

    assert client.post("/messages/send", json={"text": "nobody"}).status_code == 400
    assert client.post("/messages/send", json={"to": "x", "text": "  "}).status_code == 400


def test_send_message_reports_automation_denial(client, monkeypatch):
    def denied(*args):
        raise MacAPIError(403, "Not authorized to send Apple events to Messages.", hint="allow it")

    monkeypatch.setattr("mac_api.services.messages.run_applescript", denied)
    response = client.post("/messages/send", json={"to": "+306912345678", "text": "hi"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Not authorized to send Apple events to Messages.", "hint": "allow it"}
