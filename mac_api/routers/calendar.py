from __future__ import annotations

from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Query, Response, status

from ..auth import require_write_access
from ..errors import MacAPIError
from ..services import calendar
from ..services.calendar import CalendarEvent, CalendarInfo, EventCreate, EventList

router = APIRouter(prefix="/calendar", tags=["calendar"])
write = [Depends(require_write_access)]


@router.get("/calendars", response_model=list[CalendarInfo])
def get_calendars() -> list[CalendarInfo]:
    return calendar.list_calendars()


@router.get("/events", response_model=EventList, summary="Events in a time range")
def get_events(
    start: datetime | None = Query(None, description="Defaults to the start of today"),
    end: datetime | None = Query(None, description="Defaults to start + days"),
    days: int = Query(7, ge=1, le=366),
    calendar_name: str | None = Query(None, alias="calendar"),
) -> EventList:
    # Naive datetimes are local time; make everything timezone-aware before comparing.
    start = (start or datetime.combine(datetime.now().date(), time.min)).astimezone()
    end = end.astimezone() if end else start + timedelta(days=days)
    if end <= start:
        raise MacAPIError(400, "'end' must be after 'start'")
    if end - start > timedelta(days=366):
        raise MacAPIError(400, "The range can be at most 366 days")
    return calendar.list_events(start, end, calendar_name)


@router.post("/events", response_model=CalendarEvent, status_code=status.HTTP_201_CREATED, dependencies=write)
def create_event(data: EventCreate) -> CalendarEvent:
    return calendar.create_event(data)


@router.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=write,
    summary="Delete an event (all occurrences, for a recurring event)",
)
def delete_event(event_id: str, calendar_name: str | None = Query(None, alias="calendar")) -> Response:
    calendar.delete_event(event_id, calendar_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
