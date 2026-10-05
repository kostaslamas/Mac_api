"""Reminders.app via JavaScript for Automation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..runner import run_jxa


class ReminderList(BaseModel):
    id: str
    name: str
    open_count: int | None = None


class Reminder(BaseModel):
    id: str
    title: str
    notes: str | None = None
    completed: bool = False
    due_date: datetime | None = None
    remind_me_date: datetime | None = None
    priority: int = Field(0, description="0 = none, 1 = high, 5 = medium, 9 = low")
    list_name: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None
    completed_at: datetime | None = None


class ReminderCreate(BaseModel):
    title: str = Field(..., min_length=1)
    notes: str | None = None
    list_name: str | None = Field(None, description="Defaults to the default Reminders list")
    due_date: datetime | None = Field(None, description="Naive datetimes are local time on the Mac")
    remind_me_date: datetime | None = Field(
        None, description="When to show a notification. Defaults to due_date when alert is true."
    )
    alert: bool = Field(True, description="Notify at due_date if remind_me_date is not given")
    priority: int = Field(0, ge=0, le=9)


class ReminderUpdate(BaseModel):
    """Only the fields you send are changed. Send null for a date to clear it."""

    title: str | None = Field(None, min_length=1)
    notes: str | None = None
    due_date: datetime | None = None
    remind_me_date: datetime | None = None
    priority: int | None = Field(None, ge=0, le=9)
    completed: bool | None = None


_PRELUDE = r"""
const app = Application('Reminders');
const PREFIX = 'x-apple-reminder://';
function shortId(id) { return id.indexOf(PREFIX) === 0 ? id.slice(PREFIX.length) : id; }
function fullId(id) { return id.indexOf('://') === -1 ? PREFIX + id : id; }
function findList(name) {
  const list = app.lists.byName(name);
  if (!list.exists()) notFound('Reminders list not found: ' + name);
  return list;
}
function findReminder(id) {
  const reminder = app.reminders.byId(fullId(id));
  if (!reminder.exists()) notFound('Reminder not found: ' + id);
  return reminder;
}
function serializeAll(spec, listName) {
  const ids = spec.id();
  const n = ids.length;
  if (!n) return [];
  const names = spec.name(), bodies = spec.body(), completed = spec.completed(), priorities = spec.priority();
  const due = bulk(() => spec.dueDate(), n), remind = bulk(() => spec.remindMeDate(), n);
  const created = bulk(() => spec.creationDate(), n), modified = bulk(() => spec.modificationDate(), n);
  const completedAt = bulk(() => spec.completionDate(), n);
  return ids.map((id, i) => ({
    id: shortId(id), title: names[i], notes: bodies[i] || null, completed: completed[i],
    due_date: iso(due[i]), remind_me_date: iso(remind[i]), priority: priorities[i] || 0,
    list_name: listName, created_at: iso(created[i]), modified_at: iso(modified[i]),
    completed_at: iso(completedAt[i]),
  }));
}
function serializeOne(r) {
  let listName = null;
  try { listName = r.container().name(); } catch (e) {}
  return {
    id: shortId(r.id()), title: r.name(), notes: r.body() || null, completed: r.completed(),
    due_date: iso(r.dueDate()), remind_me_date: iso(r.remindMeDate()), priority: r.priority() || 0,
    list_name: listName, created_at: iso(r.creationDate()), modified_at: iso(r.modificationDate()),
    completed_at: iso(r.completionDate()),
  };
}
"""

_LIST_LISTS = r"""
const lists = app.lists();
return lists.map((list) => ({
  id: list.id(),
  name: list.name(),
  open_count: list.reminders.whose({ completed: false }).id().length,
}));
"""

_LIST_REMINDERS = r"""
const lists = args.list_name ? [findList(args.list_name)] : app.lists();
let result = [];
lists.forEach((list) => {
  const spec = args.include_completed ? list.reminders : list.reminders.whose({ completed: false });
  result = result.concat(serializeAll(spec, list.name()));
});
return result;
"""

_GET_REMINDER = r"""
return serializeOne(findReminder(args.id));
"""

_CREATE_REMINDER = r"""
const list = args.list_name ? findList(args.list_name) : app.defaultList();
const props = { name: args.title };
if (args.notes) props.body = args.notes;
if (args.due_date) props.dueDate = new Date(args.due_date);
if (args.remind_me_date) props.remindMeDate = new Date(args.remind_me_date);
if (args.priority) props.priority = args.priority;
const reminder = app.Reminder(props);
list.reminders.push(reminder);
return serializeOne(reminder);
"""

_UPDATE_REMINDER = r"""
const r = findReminder(args.id);
const f = args.fields;
if (f.title != null) r.name = f.title;
if ('notes' in f) r.body = f.notes || '';
if ('due_date' in f) r.dueDate = f.due_date ? new Date(f.due_date) : null;
if ('remind_me_date' in f) r.remindMeDate = f.remind_me_date ? new Date(f.remind_me_date) : null;
if (f.priority != null) r.priority = f.priority;
if (f.completed != null) r.completed = f.completed;
return serializeOne(r);
"""

_DELETE_REMINDER = r"""
app.delete(findReminder(args.id));
return true;
"""

SCRIPTS = {
    "list_lists": _LIST_LISTS,
    "list_reminders": _LIST_REMINDERS,
    "get_reminder": _GET_REMINDER,
    "create_reminder": _CREATE_REMINDER,
    "update_reminder": _UPDATE_REMINDER,
    "delete_reminder": _DELETE_REMINDER,
}


def _run(name: str, args: dict[str, Any] | None = None) -> Any:
    return run_jxa(_PRELUDE + SCRIPTS[name], args)


def _sort_key(reminder: Reminder) -> tuple:
    due = reminder.due_date
    return (due is None, due.timestamp() if due else 0, reminder.title.lower())


def list_lists() -> list[ReminderList]:
    return [ReminderList(**item) for item in _run("list_lists")]


def list_reminders(
    list_name: str | None = None,
    include_completed: bool = False,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    query: str | None = None,
) -> list[Reminder]:
    items = _run("list_reminders", {"list_name": list_name, "include_completed": include_completed})
    reminders = [Reminder(**item) for item in items]
    if due_before is not None:
        limit = due_before.astimezone()
        reminders = [r for r in reminders if r.due_date and r.due_date <= limit]
    if due_after is not None:
        limit = due_after.astimezone()
        reminders = [r for r in reminders if r.due_date and r.due_date >= limit]
    if query:
        needle = query.casefold()
        reminders = [r for r in reminders if needle in r.title.casefold() or needle in (r.notes or "").casefold()]
    return sorted(reminders, key=_sort_key)


def get_reminder(reminder_id: str) -> Reminder:
    return Reminder(**_run("get_reminder", {"id": reminder_id}))


def create_reminder(data: ReminderCreate) -> Reminder:
    args = data.model_dump(mode="json")
    if args["remind_me_date"] is None and data.alert:
        args["remind_me_date"] = args["due_date"]
    return Reminder(**_run("create_reminder", args))


def update_reminder(reminder_id: str, data: ReminderUpdate) -> Reminder:
    fields = data.model_dump(mode="json", exclude_unset=True)
    return Reminder(**_run("update_reminder", {"id": reminder_id, "fields": fields}))


def delete_reminder(reminder_id: str) -> None:
    _run("delete_reminder", {"id": reminder_id})
