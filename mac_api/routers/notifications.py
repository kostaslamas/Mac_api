from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request

from ..services.notifications import AppNotification, NotificationApp, NotificationsStore

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _store(request: Request) -> NotificationsStore:
    return request.app.state.notifications


@router.get(
    "",
    response_model=list[AppNotification],
    summary="Notifications shown on this Mac, newest first",
    description=(
        "Incoming messages from apps without their own endpoints here (Viber, WhatsApp, Messenger, "
        "Signal, Slack, ...) arrive as notifications. Filter with `app=viber`. Only covers what "
        "Notification Center still holds, with text only when the app shows previews."
    ),
)
def list_notifications(
    request: Request,
    app: str | None = Query(None, description="Part of the app name or bundle id, e.g. 'viber'"),
    since: datetime | None = None,
    q: str | None = Query(None, description="Text to look for in the title, subtitle or body"),
    limit: int = Query(50, ge=1, le=1000),
) -> list[AppNotification]:
    return _store(request).list(app=app, since=since, query=q, limit=limit)


@router.get("/apps", response_model=list[NotificationApp], summary="Apps with stored notifications")
def list_apps(request: Request) -> list[NotificationApp]:
    return _store(request).apps()
