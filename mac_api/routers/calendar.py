from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status

from ..auth import require_write_access
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
    start, end = calendar.resolve_range(start, end, days)
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
