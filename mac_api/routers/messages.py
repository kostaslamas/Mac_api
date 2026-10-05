from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse

from ..auth import require_write_access
from ..services.messages import Chat, Message, MessagesStore, SendMessageRequest

router = APIRouter(prefix="/messages", tags=["messages"])
write = [Depends(require_write_access)]


def get_store(request: Request) -> MessagesStore:
    return request.app.state.messages


@router.get("/chats", response_model=list[Chat], summary="Conversations, most recent first")
def list_chats(
    limit: int = Query(30, ge=1, le=500),
    store: MessagesStore = Depends(get_store),
) -> list[Chat]:
    return store.list_chats(limit)


@router.get("/chats/{chat_id}", response_model=Chat)
def get_chat(chat_id: int, store: MessagesStore = Depends(get_store)) -> Chat:
    return store.get_chat(chat_id)


@router.get(
    "/chats/{chat_id}/messages",
    response_model=list[Message],
    summary="The latest messages of a conversation, oldest first",
    description="Page backwards with `before` set to the date of the first message you have.",
)
def get_chat_messages(
    chat_id: int,
    limit: int = Query(50, ge=1, le=500),
    before: datetime | None = None,
    since: datetime | None = None,
    include_reactions: bool = False,
    store: MessagesStore = Depends(get_store),
) -> list[Message]:
    store.get_chat(chat_id)  # 404 for unknown chats
    messages = store.list_messages(
        chat_id=chat_id, before=before, since=since, include_reactions=include_reactions, limit=limit
    )
    return list(reversed(messages))


@router.get("", response_model=list[Message], summary="Search and filter messages, newest first")
def list_messages(
    q: str | None = Query(None, description="Text to search for (case-insensitive for Latin letters only)"),
    handle: str | None = Query(None, description="Phone number or email of the other person"),
    chat_id: int | None = None,
    since: datetime | None = None,
    before: datetime | None = None,
    unread_only: bool = False,
    incoming_only: bool = False,
    include_reactions: bool = False,
    limit: int = Query(50, ge=1, le=500),
    store: MessagesStore = Depends(get_store),
) -> list[Message]:
    return store.list_messages(
        chat_id=chat_id,
        handle=handle,
        query=q,
        since=since,
        before=before,
        unread_only=unread_only,
        incoming_only=incoming_only,
        include_reactions=include_reactions,
        limit=limit,
    )


@router.get("/attachments/{attachment_id}", response_class=FileResponse, summary="Download an attachment")
def get_attachment(attachment_id: int, store: MessagesStore = Depends(get_store)) -> FileResponse:
    path, mime_type, filename = store.get_attachment_path(attachment_id)
    return FileResponse(path, media_type=mime_type, filename=filename or path.name)


@router.post(
    "/send",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=write,
    summary="Send an iMessage or SMS",
    description=(
        "Send to a person with `to` (phone number in international format or email), "
        "or to an existing conversation (including group chats) with `chat_guid` from `/messages/chats`. "
        "Messages.app accepts the message; delivery is not confirmed."
    ),
)
def send_message(data: SendMessageRequest, store: MessagesStore = Depends(get_store)) -> dict[str, str]:
    store.send(data)
    return {"status": "accepted"}
