"""The Mac's services as MCP tools, so AI assistants can use them.

Served by the main app at `/mcp` (Streamable HTTP) behind the same API key.
Tools that change something are not registered at all in read-only mode.
"""

# No `from __future__ import annotations`: the MCP SDK builds each tool's input
# schema from its signature, and the tools are closures.

import contextlib
import functools
import inspect
import json
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .config import Settings
from .errors import MacAPIError
from .services import calendar as calendar_service
from .services import notes as notes_service
from .services import reminders as reminders_service
from .services import shortcuts as shortcuts_service
from .services import system as system_service
from .services.contacts import ContactsIndex
from .services.messages import MessagesStore, SendMessageRequest
from .services.notifications import NotificationsStore
from .services.telegram import TelegramSend, TelegramService

INSTRUCTIONS = """\
Tools that act on the user's own Mac: Reminders, iMessage/SMS, Telegram, Contacts, Notes, Calendar, \
Shortcuts, notifications and system functions. Incoming messages from apps without their own \
tools (Viber, WhatsApp, Messenger, ...) can be read with notifications_recent.

- Datetimes without a timezone are the Mac's local time; results include the UTC offset.
- Messages and contacts are private. Only bring up what the user asked about.
- Before sending a message, deleting anything, quitting an app or running a shortcut, \
make sure the user asked for exactly that; for messages, confirm the recipient and text.
- If a tool reports a missing macOS permission, tell the user the hint it gives.
"""

def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _to_text(value: Any) -> str:
    """Compact JSON (no nulls, real Unicode) keeps tool results small for the model."""
    return json.dumps(_jsonable(value), ensure_ascii=False, default=str)


@contextlib.contextmanager
def _tool_errors() -> Iterator[None]:
    """Turn our errors into ToolErrors: the SDK hides the message of any other exception."""
    try:
        yield
    except MacAPIError as exc:
        raise ToolError(f"{exc.detail} Hint: {exc.hint}" if exc.hint else exc.detail) from exc
    except ValidationError as exc:
        raise ToolError(str(exc)) from exc


def build_mcp_server(
    settings: Settings,
    *,
    messages: MessagesStore,
    contacts: ContactsIndex,
    notifications: NotificationsStore,
    telegram: TelegramService,
) -> MCPServer:
    server = MCPServer(name="mac-api", title="Mac", version=__version__, instructions=INSTRUCTIONS)

    def tool(*, writes: bool = False, destructive: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            if writes and settings.read_only:
                return fn

            def finish(result: Any) -> Any:
                return result if isinstance(result, (str, Image)) else _to_text(result)

            # Sync tools run in a worker thread; async ones (Telegram) on the server's event loop.
            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def wrapper(*args: Any, **kwargs: Any) -> Any:
                    with _tool_errors():
                        return finish(await fn(*args, **kwargs))

            else:

                @functools.wraps(fn)
                def wrapper(*args: Any, **kwargs: Any) -> Any:
                    with _tool_errors():
                        return finish(fn(*args, **kwargs))

            annotations = ToolAnnotations(
                read_only_hint=not writes,
                destructive_hint=destructive if writes else None,
                open_world_hint=False,
            )
            server.tool(annotations=annotations, structured_output=False)(wrapper)
            return fn

        return register

    # -- Reminders ----------------------------------------------------------------

    @tool()
    def reminders_lists() -> list[reminders_service.ReminderList]:
        """List the Reminders lists, with how many open reminders each has."""
        return reminders_service.list_lists()

    @tool()
    def reminders_list(
        list_name: Annotated[str | None, Field(description="Only this list")] = None,
        query: Annotated[str | None, Field(description="Text to look for in the title or notes")] = None,
        due_before: Annotated[datetime | None, Field(description="Only reminders due before this time")] = None,
        include_completed: bool = False,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[reminders_service.Reminder]:
        """List reminders, soonest due first (open ones only, unless include_completed)."""
        return reminders_service.list_reminders(list_name, include_completed, due_before, None, query)[:limit]

    @tool()
    def reminders_today() -> list[reminders_service.Reminder]:
        """Open reminders that are due today or overdue."""
        return reminders_service.list_reminders(due_before=calendar_service.resolve_range(None, None, 1)[1])

    @tool(writes=True)
    def reminders_create(
        title: str,
        due_date: Annotated[datetime | None, Field(description="When it is due; the user is notified then")] = None,
        notes: Annotated[str | None, Field(description="Extra details")] = None,
        list_name: Annotated[str | None, Field(description="Defaults to the default list")] = None,
        priority: Annotated[Literal[0, 1, 5, 9], Field(description="0 none, 1 high, 5 medium, 9 low")] = 0,
    ) -> reminders_service.Reminder:
        """Create a reminder."""
        data = reminders_service.ReminderCreate(
            title=title, due_date=due_date, notes=notes, list_name=list_name, priority=priority
        )
        return reminders_service.create_reminder(data)

    @tool(writes=True)
    def reminders_update(
        reminder_id: str,
        title: str | None = None,
        notes: str | None = None,
        due_date: Annotated[datetime | None, Field(description="New due date; the alert moves with it")] = None,
        clear_due_date: bool = False,
        priority: Annotated[Literal[0, 1, 5, 9] | None, Field(description="0 none, 1 high, 5 medium, 9 low")] = None,
        completed: bool | None = None,
    ) -> reminders_service.Reminder:
        """Change a reminder. Only the arguments you pass are changed."""
        fields: dict[str, Any] = {"title": title, "notes": notes, "priority": priority, "completed": completed}
        fields = {key: value for key, value in fields.items() if value is not None}
        if due_date is not None:
            fields["due_date"] = fields["remind_me_date"] = due_date
        elif clear_due_date:
            fields["due_date"] = fields["remind_me_date"] = None
        if not fields:
            raise ToolError("Nothing to change: pass at least one field")
        return reminders_service.update_reminder(reminder_id, reminders_service.ReminderUpdate(**fields))

    @tool(writes=True)
    def reminders_complete(reminder_id: str) -> reminders_service.Reminder:
        """Mark a reminder as done."""
        return reminders_service.update_reminder(reminder_id, reminders_service.ReminderUpdate(completed=True))

    @tool(writes=True, destructive=True)
    def reminders_delete(reminder_id: str) -> str:
        """Delete a reminder permanently."""
        reminders_service.delete_reminder(reminder_id)
        return "Deleted."

    # -- Messages -------------------------------------------------------------------

    @tool()
    def messages_chats(limit: Annotated[int, Field(ge=1, le=100)] = 20) -> list[Any]:
        """Recent iMessage/SMS conversations, newest first: participants (with contact names),
        unread count and the last message. Pass a chat's `id` to messages_read."""
        return messages.list_chats(limit)

    @tool()
    def messages_read(
        chat_id: int,
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
        before: Annotated[datetime | None, Field(description="To read further back: the date of the oldest message you have")] = None,
    ) -> list[Any]:
        """The latest messages of one conversation, oldest first."""
        messages.get_chat(chat_id)
        return list(reversed(messages.list_messages(chat_id=chat_id, before=before, limit=limit)))

    @tool()
    def messages_search(
        query: Annotated[str | None, Field(description="Text to find (case-insensitive only for Latin letters)")] = None,
        handle: Annotated[str | None, Field(description="Phone number or email of the other person")] = None,
        since: datetime | None = None,
        unread_only: bool = False,
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
    ) -> list[Any]:
        """Search and filter messages across all conversations, newest first.
        With no arguments, returns the latest messages overall."""
        return messages.list_messages(query=query, handle=handle, since=since, unread_only=unread_only, limit=limit)

    @tool(writes=True)
    def messages_send(
        text: str,
        to: Annotated[str | None, Field(description="Phone number (international format) or email")] = None,
        chat_guid: Annotated[str | None, Field(description="`guid` of an existing conversation, e.g. a group chat")] = None,
        service: Literal["iMessage", "SMS"] = "iMessage",
    ) -> str:
        """Send an iMessage or SMS, to a person (`to`) or an existing conversation (`chat_guid`).
        Only send what the user explicitly asked to send."""
        messages.send(SendMessageRequest(to=to, chat_guid=chat_guid, text=text, service=service))
        return "Messages.app accepted the message."

    # -- Contacts ---------------------------------------------------------------------

    @tool()
    def contacts_search(
        query: Annotated[str, Field(description="Part of a name, phone number or email")],
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[Any]:
        """Search the user's contacts."""
        return contacts.search(query)[:limit]

    # -- Notifications (Viber, WhatsApp, Messenger, ...) ----------------------------------

    @tool()
    def notifications_recent(
        app: Annotated[str | None, Field(description="Part of the app name, e.g. 'viber', 'whatsapp', 'messenger'")] = None,
        query: Annotated[str | None, Field(description="Text to look for in the title or body")] = None,
        since: datetime | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
    ) -> list[Any]:
        """Notifications shown on the Mac, newest first. This is how to read incoming messages
        from apps that have no tools of their own here (Viber, WhatsApp, Messenger, Signal,
        Slack, ...): usually the title is the sender or chat and the body is the message.
        Covers only what Notification Center still holds; there is no way to reply."""
        return notifications.list(app=app, since=since, query=query, limit=limit)

    @tool()
    def notifications_apps() -> list[Any]:
        """Apps that have notifications stored, with counts and the latest time."""
        return notifications.apps()

    # -- Telegram -------------------------------------------------------------------------

    if telegram.configured:

        @tool()
        async def telegram_chats(
            limit: Annotated[int, Field(ge=1, le=100)] = 20, unread_only: bool = False
        ) -> list[Any]:
            """Telegram chats, most recent first, with unread counts and the last message.
            Pass a chat's `id` to telegram_read."""
            return await telegram.chats(limit, unread_only)

        @tool()
        async def telegram_read(
            chat: Annotated[str, Field(description="Chat id, @username, a contact's phone number, or 'me'")],
            limit: Annotated[int, Field(ge=1, le=200)] = 30,
            before_id: Annotated[int | None, Field(description="To read further back: the oldest message id you have")] = None,
        ) -> list[Any]:
            """The latest messages of a Telegram chat, oldest first."""
            return await telegram.messages(chat, limit, before_id)

        @tool()
        async def telegram_search(
            query: str,
            chat: Annotated[str | None, Field(description="Only this chat; all chats by default")] = None,
            limit: Annotated[int, Field(ge=1, le=100)] = 30,
        ) -> list[Any]:
            """Search Telegram messages, newest first."""
            return await telegram.search(query, chat, limit)

        @tool(writes=True)
        async def telegram_send(
            to: Annotated[str, Field(description="Chat id, @username, a contact's phone number, or 'me'")],
            text: str,
            reply_to: Annotated[int | None, Field(description="Id of the message to reply to")] = None,
        ) -> Any:
            """Send a Telegram message as the user. Only send what the user explicitly asked to send."""
            return await telegram.send(TelegramSend(to=to, text=text, reply_to=reply_to))

        @tool(writes=True)
        async def telegram_mark_read(chat: str) -> str:
            """Mark a Telegram chat as read."""
            await telegram.mark_read(chat)
            return "Marked as read."

    # -- Notes ------------------------------------------------------------------------

    @tool()
    def notes_folders() -> list[notes_service.NoteFolder]:
        """List the Notes folders."""
        return notes_service.list_folders()

    @tool()
    def notes_list(
        folder: str | None = None,
        query: Annotated[str | None, Field(description="Text to look for in the title or body")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
    ) -> list[notes_service.NoteSummary]:
        """List notes, most recently edited first. Use notes_read for the content."""
        return notes_service.list_notes(folder, query, limit)

    @tool()
    def notes_read(note_id: str) -> notes_service.Note:
        """Read a note's text."""
        return notes_service.get_note(note_id).model_copy(update={"body_html": None})

    @tool(writes=True)
    def notes_create(
        title: str,
        body: Annotated[str, Field(description="Plain text; line breaks are kept")] = "",
        folder: Annotated[str | None, Field(description="Defaults to the default folder")] = None,
    ) -> notes_service.Note:
        """Create a note."""
        note = notes_service.create_note(notes_service.NoteCreate(title=title, body=body, folder=folder))
        return note.model_copy(update={"body_html": None})

    @tool(writes=True)
    def notes_append(note_id: str, text: str) -> notes_service.Note:
        """Add plain text to the end of a note (e.g. an item to a shopping list)."""
        return notes_service.update_note(note_id, notes_service.NoteUpdate(append=text)).model_copy(update={"body_html": None})

    @tool(writes=True, destructive=True)
    def notes_delete(note_id: str) -> str:
        """Delete a note (it goes to Recently Deleted)."""
        notes_service.delete_note(note_id)
        return "Deleted."

    # -- Calendar -----------------------------------------------------------------------

    @tool()
    def calendar_calendars() -> list[calendar_service.CalendarInfo]:
        """List the calendars and whether they can be written to."""
        return calendar_service.list_calendars()

    @tool()
    def calendar_events(
        start: Annotated[datetime | None, Field(description="Defaults to the start of today")] = None,
        end: Annotated[datetime | None, Field(description="Defaults to start + days")] = None,
        days: Annotated[int, Field(ge=1, le=366)] = 7,
        calendar: Annotated[str | None, Field(description="Only this calendar")] = None,
    ) -> calendar_service.EventList:
        """Events in a time range, including occurrences of repeating events."""
        range_start, range_end = calendar_service.resolve_range(start, end, days)
        return calendar_service.list_events(range_start, range_end, calendar)

    @tool(writes=True)
    def calendar_create_event(
        title: str,
        start: datetime,
        end: Annotated[datetime | None, Field(description="Defaults to start + duration_minutes")] = None,
        duration_minutes: Annotated[int, Field(ge=1)] = 60,
        calendar: Annotated[str | None, Field(description="Defaults to the first writable calendar")] = None,
        location: str | None = None,
        notes: str | None = None,
        all_day: bool = False,
    ) -> calendar_service.CalendarEvent:
        """Create a calendar event."""
        data = calendar_service.EventCreate(
            title=title, start=start, end=end, duration_minutes=duration_minutes, calendar=calendar,
            location=location, notes=notes, all_day=all_day,
        )
        return calendar_service.create_event(data)

    @tool(writes=True, destructive=True)
    def calendar_delete_event(
        event_id: str, calendar: str | None = None
    ) -> str:
        """Delete an event. For a repeating event this deletes the whole series."""
        calendar_service.delete_event(event_id, calendar)
        return "Deleted."

    # -- Shortcuts ------------------------------------------------------------------------

    @tool()
    def shortcuts_list() -> list[str]:
        """Names of the user's Shortcuts."""
        return shortcuts_service.list_shortcuts()

    @tool(writes=True)
    def shortcuts_run(
        name: str,
        input: Annotated[str | None, Field(description="Text passed to the shortcut")] = None,
    ) -> shortcuts_service.ShortcutResult:
        """Run one of the user's Shortcuts and return its output.
        Shortcuts can do anything, so only run ones the user asked for."""
        return shortcuts_service.run_shortcut(name, shortcuts_service.ShortcutRun(input=input))

    # -- System ---------------------------------------------------------------------------

    @tool()
    def mac_status() -> dict[str, Any]:
        """Hostname, macOS version, uptime, battery and volume."""
        status: dict[str, Any] = {"system": system_service.system_info()}
        for key, getter in (("battery", system_service.battery), ("volume", system_service.get_volume)):
            try:
                status[key] = getter()
            except MacAPIError as exc:
                status[key] = {"error": exc.detail}
        return status

    @tool()
    def mac_running_apps() -> list[str]:
        """Names of the apps that are running."""
        return system_service.running_apps()

    @tool()
    def mac_screenshot() -> Image:
        """A screenshot of the main display (scaled down)."""
        return Image(data=system_service.screenshot(image_format="jpg", max_size=1568), format="jpeg")

    @tool()
    def clipboard_get() -> str:
        """The text on the clipboard."""
        return system_service.get_clipboard().text

    @tool(writes=True)
    def clipboard_set(text: str) -> str:
        """Put text on the clipboard."""
        system_service.set_clipboard(system_service.ClipboardContent(text=text))
        return "Copied."

    @tool(writes=True)
    def mac_notify(message: str, title: str = "mac-api", subtitle: str | None = None) -> str:
        """Show a notification on the Mac."""
        system_service.notify(system_service.Notification(message=message, title=title, subtitle=subtitle))
        return "Shown."

    @tool(writes=True)
    def mac_say(text: str, voice: str | None = None) -> str:
        """Speak text out loud on the Mac."""
        system_service.say(system_service.Speech(text=text, voice=voice))
        return "Speaking."

    @tool(writes=True)
    def mac_set_volume(
        level: Annotated[int | None, Field(ge=0, le=100)] = None, muted: bool | None = None
    ) -> system_service.Volume:
        """Set the output volume (0-100) and/or mute."""
        return system_service.set_volume(system_service.VolumeUpdate(output_volume=level, muted=muted))

    @tool(writes=True)
    def mac_open(
        target: Annotated[str | None, Field(description="A URL or file path")] = None,
        app: Annotated[str | None, Field(description="An app name, e.g. 'Safari'")] = None,
    ) -> str:
        """Open a URL, file or app on the Mac."""
        system_service.open_target(system_service.OpenRequest(target=target, app=app))
        return "Opened."

    @tool(writes=True, destructive=True)
    def mac_quit_app(name: str) -> str:
        """Quit a running app."""
        system_service.quit_app(name)
        return "Quit."

    return server
