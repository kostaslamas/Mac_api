from __future__ import annotations

from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Query, Response, status

from ..auth import require_write_access
from ..services import reminders
from ..services.reminders import Reminder, ReminderCreate, ReminderList, ReminderUpdate

router = APIRouter(prefix="/reminders", tags=["reminders"])
write = [Depends(require_write_access)]


@router.get("/lists", response_model=list[ReminderList], summary="List Reminders lists")
def get_lists() -> list[ReminderList]:
    return reminders.list_lists()


@router.get("", response_model=list[Reminder], summary="List reminders, soonest due first")
def get_reminders(
    list_name: str | None = Query(None, description="Only this list"),
    include_completed: bool = False,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    q: str | None = Query(None, description="Text to look for in the title or notes"),
    limit: int = Query(200, ge=1, le=2000),
) -> list[Reminder]:
    return reminders.list_reminders(list_name, include_completed, due_before, due_after, q)[:limit]


@router.get("/today", response_model=list[Reminder], summary="Open reminders due today or overdue")
def get_today() -> list[Reminder]:
    end_of_today = datetime.combine(datetime.now().date() + timedelta(days=1), time.min)
    return reminders.list_reminders(due_before=end_of_today)


@router.get("/{reminder_id}", response_model=Reminder)
def get_reminder(reminder_id: str) -> Reminder:
    return reminders.get_reminder(reminder_id)


@router.post("", response_model=Reminder, status_code=status.HTTP_201_CREATED, dependencies=write)
def create_reminder(data: ReminderCreate) -> Reminder:
    return reminders.create_reminder(data)


@router.patch("/{reminder_id}", response_model=Reminder, dependencies=write)
def update_reminder(reminder_id: str, data: ReminderUpdate) -> Reminder:
    return reminders.update_reminder(reminder_id, data)


@router.post("/{reminder_id}/complete", response_model=Reminder, dependencies=write)
def complete_reminder(reminder_id: str) -> Reminder:
    return reminders.update_reminder(reminder_id, ReminderUpdate(completed=True))


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=write)
def delete_reminder(reminder_id: str) -> Response:
    reminders.delete_reminder(reminder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
