from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status

from ..auth import require_write_access
from ..services.telegram import TelegramChat, TelegramMessage, TelegramSend, TelegramService, TelegramStatus

router = APIRouter(prefix="/telegram", tags=["telegram"])
write = [Depends(require_write_access)]


def get_telegram(request: Request) -> TelegramService:
    return request.app.state.telegram


@router.get("/status", response_model=TelegramStatus, summary="Whether Telegram is set up and connected")
async def get_status(telegram: TelegramService = Depends(get_telegram)) -> TelegramStatus:
    return await telegram.status()


@router.get("/chats", response_model=list[TelegramChat], summary="Chats, most recent first")
async def list_chats(
    limit: int = Query(30, ge=1, le=200),
    unread_only: bool = False,
    telegram: TelegramService = Depends(get_telegram),
) -> list[TelegramChat]:
    return await telegram.chats(limit, unread_only)


@router.get(
    "/chats/{chat}/messages",
    response_model=list[TelegramMessage],
    summary="The latest messages of a chat, oldest first",
    description="`chat` is a chat id, an @username, a contact's phone number, or `me` for Saved Messages. "
    "Page backwards with `before_id` set to the id of the oldest message you have.",
)
async def chat_messages(
    chat: str,
    limit: int = Query(50, ge=1, le=500),
    before_id: int | None = None,
    telegram: TelegramService = Depends(get_telegram),
) -> list[TelegramMessage]:
    return await telegram.messages(chat, limit, before_id)


@router.get("/messages", response_model=list[TelegramMessage], summary="Search messages, newest first")
async def search_messages(
    q: str = Query(..., min_length=1),
    chat: str | None = Query(None, description="Only this chat; all chats by default"),
    limit: int = Query(30, ge=1, le=200),
    telegram: TelegramService = Depends(get_telegram),
) -> list[TelegramMessage]:
    return await telegram.search(q, chat, limit)


@router.post("/send", response_model=TelegramMessage, dependencies=write, summary="Send a message")
async def send_message(data: TelegramSend, telegram: TelegramService = Depends(get_telegram)) -> TelegramMessage:
    return await telegram.send(data)


@router.post("/chats/{chat}/read", status_code=status.HTTP_204_NO_CONTENT, dependencies=write,
             summary="Mark a chat as read")
async def mark_read(chat: str, telegram: TelegramService = Depends(get_telegram)) -> Response:
    await telegram.mark_read(chat)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
