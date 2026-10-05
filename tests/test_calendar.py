from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

from mac_api.services.calendar import expand_recurring


@pytest.fixture(autouse=True)
def athens_time():
    """Run in a zone with daylight saving time (it ends on 2026-10-25 in Europe/Athens)."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Athens"
    time.tzset()
    yield
    if previous is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = previous
    time.tzset()


def local(*args) -> datetime:
    return datetime(*args).astimezone()


def weekly(**fields):
    return {
        "id": "UID-1",
        "title": "Standup",
        "start": "2026-09-07T09:00:00+03:00",  # a Monday
        "end": "2026-09-07T09:30:00+03:00",
        "recurrence": "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO",
        "calendar": "Work",
        **fields,
    }


def test_weekly_occurrences_keep_local_time_across_dst():
    occurrences = expand_recurring(weekly(), local(2026, 10, 19), local(2026, 11, 3))
    assert [o["start"] for o in occurrences] == ["2026-10-19T09:00:00+03:00", "2026-10-26T09:00:00+02:00",
                                                 "2026-11-02T09:00:00+02:00"]
    assert occurrences[1]["end"] == "2026-10-26T09:30:00+02:00"


def test_excluded_dates_and_until():
    event = weekly(excluded_dates=["2026-10-12T09:00:00+03:00"],
                   recurrence="FREQ=WEEKLY;BYDAY=MO;UNTIL=20261020T060000Z")
    occurrences = expand_recurring(event, local(2026, 10, 1), local(2026, 12, 1))
    assert [o["start"][:10] for o in occurrences] == ["2026-10-05", "2026-10-19"]


def test_occurrence_overlapping_range_start_is_included():
    occurrences = expand_recurring(weekly(), local(2026, 10, 5, 9, 15), local(2026, 10, 5, 12))
    assert [o["start"] for o in occurrences] == ["2026-10-05T09:00:00+03:00"]


def test_unparseable_rule_falls_back_to_the_event_itself():
    event = weekly(recurrence="FREQ=NONSENSE")
    assert expand_recurring(event, local(2026, 9, 1), local(2026, 9, 30)) == [event]
    assert expand_recurring(event, local(2026, 10, 1), local(2026, 10, 30)) == []


def test_events_endpoint_merges_recurring_and_single_events(client, fake_jxa):
    single = {**weekly(), "id": "UID-2", "title": "Dentist", "recurrence": None,
              "start": "2026-10-06T15:00:00+03:00", "end": "2026-10-06T16:00:00+03:00"}
    master_in_range = weekly(start="2026-10-05T09:00:00+03:00", end="2026-10-05T09:30:00+03:00")
    fake_jxa.results.append({"events": [single, master_in_range], "recurring": [master_in_range], "warnings": []})

    response = client.get("/calendar/events", params={"start": "2026-10-05T00:00:00", "days": 8})
    assert response.status_code == 200
    events = response.json()["events"]
    assert [(e["title"], e["start"]) for e in events] == [
        ("Standup", "2026-10-05T09:00:00+03:00"),
        ("Dentist", "2026-10-06T15:00:00+03:00"),
        ("Standup", "2026-10-12T09:00:00+03:00"),
    ]
    args = fake_jxa.calls[-1][1]
    assert args["start"] == "2026-10-05T00:00:00+03:00"
    assert args["end"] == "2026-10-13T00:00:00+03:00"


def test_events_range_validation(client):
    assert client.get("/calendar/events", params={"start": "2026-10-05T00:00:00", "end": "2026-10-04T00:00:00"}).status_code == 400
    assert client.get("/calendar/events", params={"start": "2026-01-01T00:00:00", "end": "2027-06-01T00:00:00"}).status_code == 400


def test_create_event_defaults(client, fake_jxa):
    fake_jxa.results.append({"id": "NEW", "title": "Lunch", "start": "2026-10-06T13:00:00+03:00",
                             "end": "2026-10-06T13:45:00+03:00", "calendar": "Home"})
    response = client.post("/calendar/events", json={"title": "Lunch", "start": "2026-10-06T13:00:00",
                                                     "duration_minutes": 45})
    assert response.status_code == 201
    assert fake_jxa.calls[-1][1]["end"] == "2026-10-06T13:45:00"

    response = client.post("/calendar/events", json={"title": "Bad", "start": "2026-10-06T13:00:00",
                                                     "end": "2026-10-06T12:00:00"})
    assert response.status_code == 422
