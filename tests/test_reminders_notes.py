from __future__ import annotations

import pytest
from pydantic import ValidationError

from mac_api.errors import MacAPIError
from mac_api.services.notes import NoteUpdate, decode_id, encode_id, text_to_html


def reminder(**fields):
    return {"id": "ABC", "title": "Milk", "completed": False, "priority": 0, "list_name": "Groceries", **fields}


def test_list_reminders_sorts_and_filters(client, fake_jxa):
    items = [
        reminder(id="1", title="No date"),
        reminder(id="2", title="Later", due_date="2026-10-09T09:00:00+03:00"),
        reminder(id="3", title="Soon", due_date="2026-10-06T09:00:00+03:00", notes="buy oat milk"),
    ]
    fake_jxa.results.append(items)
    assert [r["id"] for r in client.get("/reminders").json()] == ["3", "2", "1"]
    assert fake_jxa.calls[-1][1] == {"list_name": None, "include_completed": False}

    fake_jxa.results.append(items)
    response = client.get("/reminders", params={"due_before": "2026-10-07T00:00:00+03:00"})
    assert [r["id"] for r in response.json()] == ["3"]

    fake_jxa.results.append(items)
    assert [r["id"] for r in client.get("/reminders", params={"q": "OAT"}).json()] == ["3"]


def test_create_reminder_alerts_at_due_date_by_default(client, fake_jxa):
    fake_jxa.results.append(reminder(due_date="2026-10-06T09:00:00+03:00"))
    response = client.post(
        "/reminders", json={"title": "Milk", "list_name": "Groceries", "due_date": "2026-10-06T09:00:00"}
    )
    assert response.status_code == 201
    args = fake_jxa.calls[-1][1]
    assert args["due_date"] == args["remind_me_date"] == "2026-10-06T09:00:00"
    assert args["list_name"] == "Groceries"

    fake_jxa.results.append(reminder())
    client.post("/reminders", json={"title": "Milk", "due_date": "2026-10-06T09:00:00", "alert": False})
    assert fake_jxa.calls[-1][1]["remind_me_date"] is None

    assert client.post("/reminders", json={"title": ""}).status_code == 422


def test_update_sends_only_given_fields(client, fake_jxa):
    fake_jxa.results.append(reminder())
    client.patch("/reminders/ABC", json={"due_date": None, "title": "Oat milk"})
    assert fake_jxa.calls[-1][1] == {"id": "ABC", "fields": {"due_date": None, "title": "Oat milk"}}

    fake_jxa.results.append(reminder(completed=True))
    assert client.post("/reminders/ABC/complete").json()["completed"] is True
    assert fake_jxa.calls[-1][1] == {"id": "ABC", "fields": {"completed": True}}


def test_not_found_and_delete(client, fake_jxa):
    fake_jxa.results.append(MacAPIError(404, "Reminder not found: nope"))
    assert client.get("/reminders/nope").status_code == 404

    fake_jxa.results.append(True)
    assert client.delete("/reminders/ABC").status_code == 204


def test_read_only_mode_blocks_changes(client, fake_jxa):
    client.app.state.settings.read_only = True
    assert client.post("/reminders", json={"title": "Milk"}).status_code == 403
    assert client.delete("/reminders/ABC").status_code == 403
    assert client.post("/messages/send", json={"to": "x", "text": "hi"}).status_code == 403
    fake_jxa.results.append([])
    assert client.get("/reminders").status_code == 200
    assert len(fake_jxa.calls) == 1


def test_note_ids_round_trip():
    raw = "x-coredata://1234-ABCD/ICNote/p42"
    token = encode_id(raw)
    assert "/" not in token and "=" not in token
    assert decode_id(token) == raw
    with pytest.raises(MacAPIError):
        decode_id("not base64!")


def test_text_to_html():
    assert text_to_html("a < b\n\nend") == "<div>a &lt; b</div><div><br></div><div>end</div>"


def test_note_update_needs_exactly_one_change():
    with pytest.raises(ValidationError):
        NoteUpdate()
    with pytest.raises(ValidationError):
        NoteUpdate(append="x", body_html="<div>x</div>")


def test_create_and_append_note(client, fake_jxa):
    raw_id = "x-coredata://1234/ICNote/p1"
    note = {"id": raw_id, "title": "Shopping", "folder": "Notes", "body_html": "", "body_text": "Shopping"}
    fake_jxa.results.append(note)
    response = client.post("/notes", json={"title": "Shopping", "body": "eggs\nbread"})
    assert response.status_code == 201
    assert response.json()["id"] == encode_id(raw_id)
    assert fake_jxa.calls[-1][1]["body_html"] == "<div><h1>Shopping</h1></div><div>eggs</div><div>bread</div>"

    fake_jxa.results.append(note)
    client.patch(f"/notes/{encode_id(raw_id)}", json={"append": "milk"})
    assert fake_jxa.calls[-1][1] == {"id": raw_id, "body_html": None, "append_html": "<div>milk</div>"}


def test_list_notes_newest_first(client, fake_jxa):
    fake_jxa.results.append(
        [
            {"id": "a", "title": "Old", "folder": "Notes", "modified_at": "2026-01-01T10:00:00+02:00"},
            {"id": "b", "title": "New", "folder": "Notes", "modified_at": "2026-10-01T10:00:00+03:00"},
        ]
    )
    assert [n["title"] for n in client.get("/notes", params={"limit": 1}).json()] == ["New"]
