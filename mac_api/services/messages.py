"""iMessage/SMS: read from the Messages database, send through Messages.app."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel

from ..errors import FULL_DISK_ACCESS_HINT, MacAPIError
from ..runner import run_applescript
from .contacts import ContactsIndex, phone_key

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
OBJECT_REPLACEMENT = "￼"  # placeholder Messages puts in the text where an attachment sits
REACTIONS = {
    2000: "loved",
    2001: "liked",
    2002: "disliked",
    2003: "laughed",
    2004: "emphasized",
    2005: "questioned",
    2006: "reacted",
}
_OPTIONAL_MESSAGE_COLUMNS = (
    "attributedBody",
    "date_read",
    "is_read",
    "cache_has_attachments",
    "associated_message_type",
    "associated_message_guid",
    "thread_originator_guid",
    "item_type",
)


class Attachment(BaseModel):
    id: int
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    path: str | None = None


class Message(BaseModel):
    id: int
    guid: str
    chat_id: int | None = None
    text: str | None = None
    date: datetime | None = None
    date_read: datetime | None = None
    is_from_me: bool
    is_read: bool | None = None
    sender: str | None = None
    sender_name: str | None = None
    service: str | None = None
    attachments: list[Attachment] = []
    reaction: str | None = None
    reaction_to: str | None = None
    reply_to: str | None = None


class Participant(BaseModel):
    handle: str
    name: str | None = None


class Chat(BaseModel):
    id: int
    guid: str
    name: str
    display_name: str | None = None
    identifier: str | None = None
    service: str | None = None
    is_group: bool
    participants: list[Participant] = []
    unread_count: int = 0
    last_message: Message | None = None


class SendMessageRequest(BaseModel):
    to: str | None = None
    chat_guid: str | None = None
    text: str
    service: Literal["iMessage", "SMS"] = "iMessage"


_SEND_TO_HANDLE = """
on run argv
    set recipientHandle to item 1 of argv
    set messageText to item 2 of argv
    set serviceKind to item 3 of argv
    tell application "Messages"
        if serviceKind is "SMS" then
            set targetAccount to first account whose service type is SMS
        else
            set targetAccount to first account whose service type is iMessage
        end if
        send messageText to participant recipientHandle of targetAccount
    end tell
end run
"""

_SEND_TO_CHAT = """
on run argv
    set chatId to item 1 of argv
    set messageText to item 2 of argv
    tell application "Messages"
        send messageText to chat id chatId
    end tell
end run
"""


def apple_time_to_datetime(value: int | float | None) -> datetime | None:
    """Messages stores times since 2001-01-01, in nanoseconds (or seconds on very old macOS)."""
    if not value:
        return None
    seconds = value / 1_000_000_000 if abs(value) > 10_000_000_000 else value
    return (APPLE_EPOCH + timedelta(seconds=seconds)).astimezone()


def datetime_to_apple_time(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive datetimes are local time
    return int((dt - APPLE_EPOCH).total_seconds() * 1_000_000_000)


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Extract the plain text from a message's `attributedBody`.

    Since macOS Ventura the `text` column is often empty and the text only lives in
    this typedstream-serialized NSAttributedString. The string follows the `NSString`
    class name and a `+` type marker, prefixed by its byte length: one byte, or 0x81
    followed by a 16-bit length, or 0x82 followed by a 32-bit length.
    """
    if not blob:
        return None
    marker = blob.find(b"NSString")
    if marker == -1:
        return None
    data = blob[marker + len(b"NSString") :]
    plus = data.find(b"+")
    if plus == -1 or plus > 16:
        return None
    data = data[plus + 1 :]
    if not data:
        return None
    if data[0] == 0x81:
        length, offset = int.from_bytes(data[1:3], "little"), 3
    elif data[0] == 0x82:
        length, offset = int.from_bytes(data[1:5], "little"), 5
    else:
        length, offset = data[0], 1
    return data[offset : offset + length].decode("utf-8", errors="replace")


def _strip_associated_guid(guid: str | None) -> str | None:
    # Reactions point at e.g. "p:0/<guid>" (part 0 of the message) or "bp:<guid>".
    if not guid:
        return None
    guid = guid.split("/", 1)[-1]
    return guid[3:] if guid.startswith("bp:") else guid


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class MessagesStore:
    def __init__(self, db_path: Path, attachments_dir: Path, contacts: ContactsIndex | None = None) -> None:
        self.db_path = db_path
        self.attachments_dir = attachments_dir
        self.contacts = contacts
        self._columns_cache: dict[str, set[str]] = {}

    # -- database plumbing -------------------------------------------------------

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            with open(self.db_path, "rb"):
                pass
        except FileNotFoundError:
            raise MacAPIError(
                503,
                f"Messages database not found at {self.db_path}",
                hint="Sign in to Messages on this Mac. " + FULL_DISK_ACCESS_HINT,
            )
        except PermissionError:
            raise MacAPIError(403, "Permission denied reading the Messages database", hint=FULL_DISK_ACCESS_HINT)
        try:
            conn = sqlite3.connect(f"file:{quote(str(self.db_path))}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise MacAPIError(500, f"Could not open the Messages database: {exc}", hint=FULL_DISK_ACCESS_HINT)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        except sqlite3.Error as exc:
            raise MacAPIError(500, f"Messages database error: {exc}")
        finally:
            conn.close()

    def _columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        if table not in self._columns_cache:
            self._columns_cache[table] = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        return self._columns_cache[table]

    def _message_select(self, conn: sqlite3.Connection) -> str:
        columns = self._columns(conn, "message")
        optional = ", ".join(f"m.{c}" if c in columns else f"NULL AS {c}" for c in _OPTIONAL_MESSAGE_COLUMNS)
        return (
            "SELECT m.ROWID AS id, m.guid, m.text, m.date, m.is_from_me, m.service, "
            f"{optional}, h.id AS handle, cmj.chat_id "
            "FROM message m "
            "LEFT JOIN handle h ON h.ROWID = m.handle_id "
            "LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
        )

    def _visible_clauses(self, conn: sqlite3.Connection, include_reactions: bool = False) -> list[str]:
        """Hide group events (renames, members joining) and, by default, tapback reactions."""
        columns = self._columns(conn, "message")
        clauses = []
        if "item_type" in columns:
            clauses.append("m.item_type = 0")
        if not include_reactions and "associated_message_type" in columns:
            clauses.append("COALESCE(m.associated_message_type, 0) NOT BETWEEN 2000 AND 3999")
        return clauses

    def _name(self, handle: str | None) -> str | None:
        return self.contacts.name_for(handle) if self.contacts else None

    # -- messages ----------------------------------------------------------------

    def _attachments_for(self, conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list[Attachment]]:
        if not message_ids:
            return {}
        placeholders = ",".join("?" * len(message_ids))
        rows = conn.execute(
            "SELECT maj.message_id, a.ROWID AS id, a.filename, a.mime_type, a.transfer_name, a.total_bytes "
            "FROM message_attachment_join maj JOIN attachment a ON a.ROWID = maj.attachment_id "
            f"WHERE maj.message_id IN ({placeholders}) ORDER BY a.ROWID",
            message_ids,
        ).fetchall()
        result: dict[int, list[Attachment]] = {}
        for row in rows:
            path = str(Path(row["filename"]).expanduser()) if row["filename"] else None
            result.setdefault(row["message_id"], []).append(
                Attachment(
                    id=row["id"],
                    filename=row["transfer_name"] or (Path(path).name if path else None),
                    mime_type=row["mime_type"],
                    size=row["total_bytes"],
                    path=path,
                )
            )
        return result

    def _build_messages(self, conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[Message]:
        with_attachments = [row["id"] for row in rows if row["cache_has_attachments"]]
        attachments = self._attachments_for(conn, with_attachments)
        messages = []
        for row in rows:
            text = row["text"] or decode_attributed_body(row["attributedBody"])
            if text:
                text = text.replace(OBJECT_REPLACEMENT, "").strip() or None
            is_from_me = bool(row["is_from_me"])
            handle = None if is_from_me else row["handle"]
            associated = row["associated_message_type"] or 0
            reaction = None
            if 2000 <= associated < 3000:
                reaction = REACTIONS.get(associated, "reacted")
            elif 3000 <= associated < 4000:
                reaction = "removed " + REACTIONS.get(associated - 1000, "reaction")
            messages.append(
                Message(
                    id=row["id"],
                    guid=row["guid"],
                    chat_id=row["chat_id"],
                    text=text,
                    date=apple_time_to_datetime(row["date"]),
                    date_read=apple_time_to_datetime(row["date_read"]),
                    is_from_me=is_from_me,
                    is_read=None if row["is_read"] is None else bool(row["is_read"]),
                    sender=handle,
                    sender_name="Me" if is_from_me else self._name(handle),
                    service=row["service"],
                    attachments=attachments.get(row["id"], []),
                    reaction=reaction,
                    reaction_to=_strip_associated_guid(row["associated_message_guid"]) if reaction else None,
                    reply_to=row["thread_originator_guid"],
                )
            )
        return messages

    def list_messages(
        self,
        *,
        chat_id: int | None = None,
        handle: str | None = None,
        query: str | None = None,
        since: datetime | None = None,
        before: datetime | None = None,
        unread_only: bool = False,
        incoming_only: bool = False,
        include_reactions: bool = False,
        limit: int = 50,
    ) -> list[Message]:
        """Messages matching the filters, newest first."""
        with self.connect() as conn:
            columns = self._columns(conn, "message")
            clauses = self._visible_clauses(conn, include_reactions)
            params: list[object] = []
            if chat_id is not None:
                clauses.append("cmj.chat_id = ?")
                params.append(chat_id)
            if handle:
                digits = re.sub(r"\D", "", handle)
                if "@" not in handle and len(digits) >= 7:
                    clauses.append("h.id LIKE ?")
                    params.append(f"%{phone_key(handle)}")
                else:
                    clauses.append("h.id = ? COLLATE NOCASE")
                    params.append(handle.strip())
            if query:
                if "attributedBody" in columns:
                    clauses.append(
                        "(m.text LIKE ? ESCAPE '\\' OR (m.text IS NULL AND instr(m.attributedBody, ?) > 0))"
                    )
                    params.extend([f"%{_escape_like(query)}%", query.encode("utf-8")])
                else:
                    clauses.append("m.text LIKE ? ESCAPE '\\'")
                    params.append(f"%{_escape_like(query)}%")
            if since is not None:
                clauses.append("m.date >= ?")
                params.append(datetime_to_apple_time(since))
            if before is not None:
                clauses.append("m.date < ?")
                params.append(datetime_to_apple_time(before))
            if incoming_only or unread_only:
                clauses.append("m.is_from_me = 0")
            if unread_only and "is_read" in columns:
                clauses.append("m.is_read = 0")
            where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
            sql = self._message_select(conn) + where + "ORDER BY m.date DESC, m.ROWID DESC LIMIT ?"
            rows = conn.execute(sql, [*params, limit]).fetchall()
            return self._build_messages(conn, rows)

    def get_attachment_path(self, attachment_id: int) -> tuple[Path, str | None, str | None]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT filename, mime_type, transfer_name FROM attachment WHERE ROWID = ?", (attachment_id,)
            ).fetchone()
        if row is None or not row["filename"]:
            raise MacAPIError(404, f"Attachment {attachment_id} not found")
        path = Path(row["filename"]).expanduser().resolve()
        if not path.is_relative_to(self.attachments_dir.expanduser().resolve()):
            raise MacAPIError(403, "Attachment is outside the Messages attachments folder")
        if not path.is_file():
            raise MacAPIError(404, "Attachment file is not on this Mac (it may be stored only in iCloud)")
        return path, row["mime_type"], row["transfer_name"]

    # -- chats -------------------------------------------------------------------

    def _chat_rows(self, conn: sqlite3.Connection, chat_id: int | None, limit: int) -> list[sqlite3.Row]:
        chat_columns = self._columns(conn, "chat")
        style = "c.style" if "style" in chat_columns else "NULL"
        visible = self._visible_clauses(conn)
        inner_where = f"WHERE {' AND '.join(visible)} " if visible else ""
        # SQLite returns the bare column `message_id` from the row holding MAX(m.date).
        sql = (
            f"SELECT c.ROWID AS id, c.guid, c.chat_identifier, c.display_name, c.service_name, {style} AS style, "
            "last.message_id AS last_message_id "
            "FROM chat c "
            "LEFT JOIN (SELECT cmj.chat_id, cmj.message_id, MAX(m.date) AS last_date "
            "           FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id "
            f"           {inner_where}GROUP BY cmj.chat_id) last ON last.chat_id = c.ROWID "
        )
        if chat_id is not None:
            return conn.execute(sql + "WHERE c.ROWID = ?", (chat_id,)).fetchall()
        return conn.execute(
            sql + "WHERE last.message_id IS NOT NULL ORDER BY last.last_date DESC LIMIT ?", (limit,)
        ).fetchall()

    def _build_chats(self, conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[Chat]:
        if not rows:
            return []
        chat_ids = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(chat_ids))

        participants: dict[int, list[Participant]] = {}
        for chat_id, handle in conn.execute(
            "SELECT chj.chat_id, h.id FROM chat_handle_join chj JOIN handle h ON h.ROWID = chj.handle_id "
            f"WHERE chj.chat_id IN ({placeholders}) ORDER BY h.ROWID",
            chat_ids,
        ):
            participants.setdefault(chat_id, []).append(Participant(handle=handle, name=self._name(handle)))

        unread: dict[int, int] = {}
        if "is_read" in self._columns(conn, "message"):
            clauses = self._visible_clauses(conn) + ["m.is_from_me = 0", "m.is_read = 0"]
            for chat_id, count in conn.execute(
                "SELECT cmj.chat_id, COUNT(*) FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id "
                f"WHERE cmj.chat_id IN ({placeholders}) AND {' AND '.join(clauses)} GROUP BY cmj.chat_id",
                chat_ids,
            ):
                unread[chat_id] = count

        last_ids = [row["last_message_id"] for row in rows if row["last_message_id"] is not None]
        last_messages: dict[int, Message] = {}
        if last_ids:
            message_rows = conn.execute(
                self._message_select(conn) + f"WHERE m.ROWID IN ({','.join('?' * len(last_ids))})", last_ids
            ).fetchall()
            for message in self._build_messages(conn, message_rows):
                last_messages[message.id] = message

        chats = []
        for row in rows:
            people = participants.get(row["id"], [])
            is_group = row["style"] == 43 if row["style"] is not None else len(people) > 1
            name = row["display_name"] or ", ".join(p.name or p.handle for p in people) or row["chat_identifier"]
            last = last_messages.get(row["last_message_id"])
            if last is not None:
                last = last.model_copy(update={"chat_id": row["id"]})
            chats.append(
                Chat(
                    id=row["id"],
                    guid=row["guid"],
                    name=name or row["guid"],
                    display_name=row["display_name"] or None,
                    identifier=row["chat_identifier"],
                    service=row["service_name"],
                    is_group=is_group,
                    participants=people,
                    unread_count=unread.get(row["id"], 0),
                    last_message=last,
                )
            )
        return chats

    def list_chats(self, limit: int = 30) -> list[Chat]:
        """Chats ordered by their most recent message."""
        with self.connect() as conn:
            return self._build_chats(conn, self._chat_rows(conn, None, limit))

    def get_chat(self, chat_id: int) -> Chat:
        with self.connect() as conn:
            chats = self._build_chats(conn, self._chat_rows(conn, chat_id, 1))
        if not chats:
            raise MacAPIError(404, f"Chat {chat_id} not found")
        return chats[0]

    # -- sending -----------------------------------------------------------------

    def send(self, request: SendMessageRequest) -> None:
        if not request.text.strip():
            raise MacAPIError(400, "Message text is empty")
        if request.chat_guid:
            run_applescript(_SEND_TO_CHAT, request.chat_guid, request.text)
        elif request.to:
            run_applescript(_SEND_TO_HANDLE, request.to.strip(), request.text, request.service)
        else:
            raise MacAPIError(400, "Provide either 'to' (phone number or email) or 'chat_guid'")
