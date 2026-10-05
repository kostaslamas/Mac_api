"""Calendar.app via JavaScript for Automation.

Calendar's scripting interface returns a recurring event only once, with the date of
its first occurrence, so repeating events are fetched separately and their
occurrences in the requested range are computed from the RRULE.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any

from dateutil.rrule import rrulestr
from pydantic import BaseModel, Field, model_validator

from ..errors import MacAPIError
from ..runner import run_jxa


class CalendarInfo(BaseModel):
    name: str
    writable: bool | None = None
    description: str | None = None


class CalendarEvent(BaseModel):
    id: str = Field(..., description="The event's UID; shared by all occurrences of a recurring event")
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str | None = None
    notes: str | None = None
    url: str | None = None
    calendar: str | None = None
    recurrence: str | None = Field(None, description="iCalendar RRULE, for recurring events")


class EventList(BaseModel):
    start: datetime
    end: datetime
    events: list[CalendarEvent]
    warnings: list[str] = []


class EventCreate(BaseModel):
    title: str = Field(..., min_length=1)
    start: datetime = Field(..., description="Naive datetimes are local time on the Mac")
    end: datetime | None = Field(None, description="Defaults to start + duration_minutes")
    duration_minutes: int = Field(60, ge=1)
    calendar: str | None = Field(None, description="Defaults to the first writable calendar")
    location: str | None = None
    notes: str | None = None
    url: str | None = None
    all_day: bool = False

    @model_validator(mode="after")
    def _end_after_start(self) -> EventCreate:
        if self.end is None:
            length = timedelta(days=1) if self.all_day else timedelta(minutes=self.duration_minutes)
            self.end = self.start + length
        if self.end <= self.start:
            raise ValueError("'end' must be after 'start'")
        return self


_PRELUDE = r"""
const app = Application('Calendar');
function findCalendar(name) {
  const calendar = app.calendars.byName(name);
  if (!calendar.exists()) notFound('Calendar not found: ' + name);
  return calendar;
}
function serializeAll(spec, calendarName, withExclusions) {
  const uids = spec.uid();
  const n = uids.length;
  if (!n) return [];
  const titles = spec.summary(), starts = spec.startDate(), ends = spec.endDate();
  const allDay = bulk(() => spec.alldayEvent(), n), locations = bulk(() => spec.location(), n);
  const notes = bulk(() => spec.description(), n), urls = bulk(() => spec.url(), n);
  const recurrences = bulk(() => spec.recurrence(), n);
  return uids.map((uid, i) => {
    const event = {
      id: uid, title: titles[i] || '', start: iso(starts[i]), end: iso(ends[i]), all_day: !!allDay[i],
      location: locations[i] || null, notes: notes[i] || null, url: urls[i] || null,
      calendar: calendarName, recurrence: recurrences[i] || null,
    };
    if (withExclusions) {
      try { event.excluded_dates = (spec[i].excludedDates() || []).map(iso); } catch (e) { event.excluded_dates = []; }
    }
    return event;
  });
}
"""

_LIST_CALENDARS = r"""
const calendars = app.calendars;
const names = calendars.name();
const writable = bulk(() => calendars.writable(), names.length);
const descriptions = bulk(() => calendars.description(), names.length);
return names.map((name, i) => ({ name: name, writable: writable[i], description: descriptions[i] || null }));
"""

_LIST_EVENTS = r"""
const start = new Date(args.start), end = new Date(args.end);
const calendars = args.calendar ? [findCalendar(args.calendar)] : app.calendars();
const result = { events: [], recurring: [], warnings: [] };
calendars.forEach((calendar) => {
  const name = calendar.name();
  const inRange = calendar.events.whose({
    _and: [{ startDate: { _lessThan: end } }, { endDate: { _greaterThan: start } }],
  });
  result.events = result.events.concat(serializeAll(inRange, name, false));
  try {
    const repeating = calendar.events.whose({
      _and: [{ startDate: { _lessThan: end } }, { recurrence: { _beginsWith: 'FREQ' } }],
    });
    result.recurring = result.recurring.concat(serializeAll(repeating, name, true));
  } catch (e) {
    result.warnings.push('Could not read recurring events of "' + name + '": ' + e.message);
  }
});
return result;
"""

_CREATE_EVENT = r"""
let calendar;
if (args.calendar) {
  calendar = findCalendar(args.calendar);
} else {
  calendar = app.calendars().find((c) => { try { return c.writable(); } catch (e) { return false; } });
  if (!calendar) notFound('No writable calendar found');
}
const props = { summary: args.title, startDate: new Date(args.start), endDate: new Date(args.end) };
if (args.location) props.location = args.location;
if (args.notes) props.description = args.notes;
if (args.url) props.url = args.url;
if (args.all_day) props.alldayEvent = true;
const event = app.Event(props);
calendar.events.push(event);
return {
  id: event.uid(), title: args.title, start: iso(event.startDate()), end: iso(event.endDate()),
  all_day: !!args.all_day, location: args.location || null, notes: args.notes || null,
  url: args.url || null, calendar: calendar.name(), recurrence: null,
};
"""

_DELETE_EVENT = r"""
const calendars = args.calendar ? [findCalendar(args.calendar)] : app.calendars();
for (const calendar of calendars) {
  const matches = calendar.events.whose({ uid: args.id });
  if (matches.uid().length) {
    app.delete(matches[0]);
    return true;
  }
}
notFound('Event not found: ' + args.id);
"""

SCRIPTS = {
    "list_calendars": _LIST_CALENDARS,
    "list_events": _LIST_EVENTS,
    "create_event": _CREATE_EVENT,
    "delete_event": _DELETE_EVENT,
}

_UNTIL_UTC = re.compile(r"UNTIL=(\d{8}T\d{6})Z")


def _run(name: str, args: dict[str, Any] | None = None) -> Any:
    return run_jxa(_PRELUDE + SCRIPTS[name], args)


def _local_naive(dt: datetime) -> datetime:
    return dt.astimezone().replace(tzinfo=None)


def _localize_until(rule: str) -> str:
    """Rewrite UTC UNTIL values as local time, since the rule is expanded in naive local time."""

    def replace(match: re.Match[str]) -> str:
        until = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return "UNTIL=" + _local_naive(until).strftime("%Y%m%dT%H%M%S")

    return _UNTIL_UTC.sub(replace, rule)


def expand_recurring(event: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Occurrences of a recurring event that overlap [start, end).

    The rule is expanded in naive local time so that a 9:00 meeting stays at 9:00
    across daylight saving changes.
    """
    event_start = datetime.fromisoformat(event["start"])
    event_end = datetime.fromisoformat(event["end"])
    duration = event_end - event_start
    try:
        rule = rrulestr(_localize_until(event["recurrence"]), dtstart=_local_naive(event_start))
    except (ValueError, TypeError):
        return [event] if event_start < end and event_end > start else []
    excluded = {_local_naive(datetime.fromisoformat(d)) for d in event.get("excluded_dates") or [] if d}
    occurrences = []
    for occurrence in rule.between(_local_naive(start - duration), _local_naive(end), inc=True):
        if occurrence in excluded:
            continue
        occurrence_start = occurrence.astimezone()
        occurrence_end = (occurrence + duration).astimezone()
        if occurrence_end <= start or occurrence_start >= end:
            continue
        occurrences.append({**event, "start": occurrence_start.isoformat(), "end": occurrence_end.isoformat()})
    return occurrences


def resolve_range(start: datetime | None, end: datetime | None, days: int = 7) -> tuple[datetime, datetime]:
    """Default to today's midnight + `days`; naive datetimes are local time."""
    start = (start or datetime.combine(datetime.now().date(), time.min)).astimezone()
    end = end.astimezone() if end else start + timedelta(days=days)
    if end <= start:
        raise MacAPIError(400, "'end' must be after 'start'")
    if end - start > timedelta(days=366):
        raise MacAPIError(400, "The range can be at most 366 days")
    return start, end


def list_calendars() -> list[CalendarInfo]:
    return [CalendarInfo(**item) for item in _run("list_calendars")]


def list_events(start: datetime, end: datetime, calendar: str | None = None) -> EventList:
    start, end = start.astimezone(), end.astimezone()
    data = _run("list_events", {"start": start.isoformat(), "end": end.isoformat(), "calendar": calendar})
    recurring_ids = {event["id"] for event in data["recurring"]}
    events = [event for event in data["events"] if event["id"] not in recurring_ids]
    for event in data["recurring"]:
        events.extend(expand_recurring(event, start, end))
    parsed = sorted((CalendarEvent(**event) for event in events), key=lambda e: (e.start, e.title))
    return EventList(start=start, end=end, events=parsed, warnings=data["warnings"])


def create_event(data: EventCreate) -> CalendarEvent:
    return CalendarEvent(**_run("create_event", data.model_dump(mode="json")))


def delete_event(event_id: str, calendar: str | None = None) -> None:
    _run("delete_event", {"id": event_id, "calendar": calendar})
