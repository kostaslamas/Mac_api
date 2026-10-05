"""Telegram through its official user API (MTProto, via Telethon).

Unlike the Bot API this acts as the user's own account: it reads and sends in their
personal chats. It needs an api_id/api_hash from https://my.telegram.org and a
one-time login (`mac-api --telegram-login`), which stores a session file that grants
full access to the account.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from telethon import TelegramClient, utils

from ..config import Settings
from ..errors import MacAPIError

log = logging.getLogger(__name__)

LOGIN_HINT = "Run `mac-api --telegram-login` on the Mac, then restart mac-api."
NETWORK_HINT = "Check the Mac's internet connection; mac-api keeps retrying every minute."
CONNECT_TIMEOUT = 30.0
RETRY_SECONDS = 60.0
_MEDIA_KINDS = ("photo", "voice", "video_note", "video", "gif", "sticker", "audio", "document", "contact", "geo", "poll")


class TelegramMessage(BaseModel):
    id: int
    chat_id: int | None = None
    chat_name: str | None = None
    date: datetime | None = None
    text: str | None = None
    is_from_me: bool = False
    sender_id: int | None = None
    sender_name: str | None = None
    reply_to_id: int | None = None
    media: str | None = None


class TelegramChat(BaseModel):
    id: int
    name: str
    type: str
    username: str | None = None
    unread_count: int = 0
    last_message: TelegramMessage | None = None


class TelegramUser(BaseModel):
    id: int
    name: str
    username: str | None = None
    phone: str | None = None


class TelegramStatus(BaseModel):
    configured: bool
    connected: bool
    user: TelegramUser | None = None
    error: str | None = None


class TelegramSend(BaseModel):
    to: str
    text: str
    reply_to: int | None = None


def display_name(entity: Any) -> str | None:
    return (utils.get_display_name(entity) or None) if entity is not None else None


def to_message(message: Any, chat_name: str | None = None) -> TelegramMessage:
    media = next((kind for kind in _MEDIA_KINDS if getattr(message, kind, None)), None)
    if media is None and getattr(message, "media", None) is not None:
        media = "other"
    date = getattr(message, "date", None)
    return TelegramMessage(
        id=message.id,
        chat_id=getattr(message, "chat_id", None),
        chat_name=chat_name,
        date=date.astimezone() if date else None,
        text=getattr(message, "message", None) or None,
        is_from_me=bool(getattr(message, "out", False)),
        sender_id=getattr(message, "sender_id", None),
        sender_name="Me" if getattr(message, "out", False) else display_name(getattr(message, "sender", None)),
        reply_to_id=getattr(message, "reply_to_msg_id", None),
        media=media,
    )


def to_chat(dialog: Any) -> TelegramChat:
    entity = dialog.entity
    if dialog.is_group:
        kind = "group"
    elif dialog.is_channel:
        kind = "channel"
    elif getattr(entity, "bot", False):
        kind = "bot"
    else:
        kind = "user"
    return TelegramChat(
        id=dialog.id,
        name=dialog.name or "",
        type=kind,
        username=getattr(entity, "username", None),
        unread_count=dialog.unread_count or 0,
        last_message=to_message(dialog.message) if dialog.message else None,
    )


class TelegramService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: Any = None
        self.error: str | None = None if settings.telegram_configured else "Telegram is not set up"
        self.needs_login = not settings.telegram_configured
        self._task: asyncio.Task[None] | None = None

    @property
    def configured(self) -> bool:
        return self.settings.telegram_configured

    # -- lifecycle ---------------------------------------------------------------------

    async def start(self) -> None:
        """Connect in the background so a slow or offline network never delays startup."""
        if not self.configured or self.client is not None:
            return
        if not self.settings.telegram_session.exists():
            self.error, self.needs_login = "Not logged in to Telegram", True
            return
        self.error = "Still connecting to Telegram"
        self._task = asyncio.create_task(self._connect())

    async def _connect(self) -> None:
        """Keep trying until connected (e.g. the Mac started before Wi-Fi), unless the session is invalid."""
        while True:
            client = TelegramClient(
                str(self.settings.telegram_session),
                self.settings.telegram_api_id,
                self.settings.telegram_api_hash,
                receive_updates=False,
            )
            try:
                await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                authorized = await client.is_user_authorized()
            except Exception as exc:  # timeouts, network errors, ...
                reason = str(exc) or type(exc).__name__
                log.warning("Could not reach Telegram: %s", reason)
                self.error = f"Could not reach Telegram ({reason})"
                await _disconnect_quietly(client)
                await asyncio.sleep(RETRY_SECONDS)
                continue
            if not authorized:
                self.error, self.needs_login = "The Telegram session is no longer valid", True
                await _disconnect_quietly(client)
                return
            self.client, self.error = client, None
            return

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self.client is not None:
            await self.client.disconnect()
            self.client = None

    def _require(self) -> Any:
        if self.client is None:
            hint = LOGIN_HINT if self.needs_login else NETWORK_HINT
            raise MacAPIError(503, self.error or "Telegram is not connected", hint=hint)
        return self.client

    async def _entity(self, chat: str | int) -> Any:
        """A chat from its numeric id, @username, phone number of a contact, or 'me'."""
        client = self._require()
        target: str | int = chat
        if isinstance(chat, str):
            chat = chat.strip()
            target = int(chat) if re.fullmatch(r"-?\d+", chat) else chat
        if target in ("me", "self"):
            return "me"
        try:
            return await client.get_entity(target)
        except ValueError:
            pass
        # Ids are only resolvable once Telethon has seen the chat; loading the dialogs caches them.
        await client.get_dialogs()
        try:
            return await client.get_entity(target)
        except ValueError:
            raise MacAPIError(404, f"Telegram chat not found: {chat}")

    # -- reading -----------------------------------------------------------------------

    async def status(self) -> TelegramStatus:
        if self.client is None:
            return TelegramStatus(configured=self.configured, connected=False, error=self.error)
        me = await self.client.get_me()
        user = TelegramUser(id=me.id, name=display_name(me) or "", username=me.username, phone=me.phone)
        return TelegramStatus(configured=True, connected=True, user=user)

    async def chats(self, limit: int = 30, unread_only: bool = False) -> list[TelegramChat]:
        """Chats ordered by their latest message. With unread_only, the unread ones among the latest 200."""
        dialogs = await self._require().get_dialogs(limit=200 if unread_only else limit)
        chats = [to_chat(dialog) for dialog in dialogs]
        if unread_only:
            chats = [chat for chat in chats if chat.unread_count]
        return chats[:limit]

    async def messages(self, chat: str | int, limit: int = 50, before_id: int | None = None) -> list[TelegramMessage]:
        """The latest messages of a chat, oldest first."""
        entity = await self._entity(chat)
        found = await self._require().get_messages(entity, limit=limit, offset_id=before_id or 0)
        return [to_message(message) for message in reversed(found)]

    async def search(self, query: str, chat: str | int | None = None, limit: int = 30) -> list[TelegramMessage]:
        """Messages containing `query`, newest first; across all chats unless `chat` is given."""
        entity = await self._entity(chat) if chat is not None else None
        found = await self._require().get_messages(entity, limit=limit, search=query)
        return [to_message(message, chat_name=display_name(getattr(message, "chat", None))) for message in found]

    # -- acting ------------------------------------------------------------------------

    async def send(self, request: TelegramSend) -> TelegramMessage:
        if not request.text.strip():
            raise MacAPIError(400, "Message text is empty")
        entity = await self._entity(request.to)
        # parse_mode=None sends the text as written instead of interpreting Markdown.
        sent = await self._require().send_message(entity, request.text, reply_to=request.reply_to, parse_mode=None)
        return to_message(sent)

    async def mark_read(self, chat: str | int) -> None:
        entity = await self._entity(chat)
        await self._require().send_read_acknowledge(entity)


async def _disconnect_quietly(client: Any) -> None:
    try:
        await client.disconnect()
    except Exception:
        pass


async def login(settings: Settings) -> str:
    """Interactive first login: prompts for the phone number, the code and the 2FA password."""
    client = TelegramClient(str(settings.telegram_session), settings.telegram_api_id, settings.telegram_api_hash)
    await client.start()
    try:
        me = await client.get_me()
        return display_name(me) or str(me.id)
    finally:
        await client.disconnect()


async def logout(settings: Settings) -> None:
    """End the session on Telegram's side and delete the local session file."""
    client = TelegramClient(str(settings.telegram_session), settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        await client.disconnect()
    settings.telegram_session.unlink(missing_ok=True)
