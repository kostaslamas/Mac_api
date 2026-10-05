"""Notifications delivered on this Mac, read from the Notification Center database.

Apps without a usable API for personal accounts (Viber, whose desktop database is
encrypted, WhatsApp, Messenger, Signal, ...) still post a notification for every
incoming message, so this is how their messages reach the API. It is read-only,
covers only what Notification Center still holds, and contains message text only
when the app shows previews.

Layout (verified on macOS 26): tables `app(app_id, identifier)` and
`record(rec_id, app_id, uuid, data, delivered_date, ...)`. `data` is a binary
plist whose `req` dictionary holds `titl`, `subt` and `body`.
"""

from __future__ import annotations

import plistlib
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel

from ..errors import FULL_DISK_ACCESS_HINT, MacAPIError
from .messages import apple_time_to_datetime, datetime_to_apple_time

KNOWN_APPS = {
    "com.viber.osx": "Viber",
    "net.whatsapp.WhatsApp": "WhatsApp",
    "desktop.WhatsApp": "WhatsApp",
    "com.facebook.archon": "Messenger",
    "com.facebook.archon.developerID": "Messenger",
    "ru.keepcoder.Telegram": "Telegram",
    "org.telegram.desktop": "Telegram",
    "org.whispersystems.signal-desktop": "Signal",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.hnc.Discord": "Discord",
    "com.microsoft.teams2": "Microsoft Teams",
    "com.apple.MobileSMS": "Messages",
    "com.apple.mail": "Mail",
}


class AppNotification(BaseModel):
    id: str
    app: str
    bundle_id: str | None = None
    title: str | None = None
    subtitle: str | None = None
    body: str | None = None
    delivered_at: datetime | None = None


class NotificationApp(BaseModel):
    app: str
    bundle_id: str
    count: int
    latest_at: datetime | None = None


def app_name(bundle_id: str | None) -> str:
    if not bundle_id:
        return "Unknown"
    if bundle_id in KNOWN_APPS:
        return KNOWN_APPS[bundle_id]
    return bundle_id.rsplit(".", 1)[-1]


def _text(value: Any) -> str | None:
    """A field is either a string or a localized `[format, resolved, arguments]` array."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        for candidate in (value[1] if len(value) > 1 else None, value[0] if value else None):
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def parse_payload(data: bytes | None) -> dict[str, Any]:
    """The app's bundle id and the title, subtitle and body; None for anything unreadable."""
    try:
        root = plistlib.loads(data) if data else {}
    except (plistlib.InvalidFileException, ValueError, TypeError):
        root = {}
    if not isinstance(root, dict):
        root = {}
    request = root.get("req") if isinstance(root.get("req"), dict) else {}
    return {
        "bundle_id": root.get("app") if isinstance(root.get("app"), str) else None,
        "title": _text(request.get("titl")),
        "subtitle": _text(request.get("subt")),
        "body": _text(request.get("body")),
    }


class NotificationsStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            with open(self.db_path, "rb"):
                pass
        except FileNotFoundError:
            raise MacAPIError(
                503,
                f"Notification Center database not found at {self.db_path}",
                hint="It exists on macOS 15 (Sequoia) and later. " + FULL_DISK_ACCESS_HINT,
            )
        except PermissionError:
            raise MacAPIError(403, "Permission denied reading notifications", hint=FULL_DISK_ACCESS_HINT)
        conn = sqlite3.connect(f"file:{quote(str(self.db_path))}?mode=ro", uri=True)
        try:
            yield conn
        except sqlite3.Error as exc:
            raise MacAPIError(500, f"Notification database error: {exc}")
        finally:
            conn.close()

    def _matching_bundle_ids(self, conn: sqlite3.Connection, app: str) -> list[str]:
        """Bundle ids whose id or name contains `app`, so 'viber' finds 'com.viber.osx'."""
        needle = app.strip().casefold()
        identifiers = [row[0] for row in conn.execute("SELECT identifier FROM app WHERE identifier IS NOT NULL")]
        return [i for i in identifiers if needle in i.casefold() or needle in app_name(i).casefold()]

    def list(
        self,
        app: str | None = None,
        since: datetime | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> list[AppNotification]:
        """Notifications, newest first."""
        with self.connect() as conn:
            clauses = ["r.delivered_date IS NOT NULL"]
            params: list[Any] = []
            if app:
                bundle_ids = self._matching_bundle_ids(conn, app)
                if not bundle_ids:
                    return []
                clauses.append(f"a.identifier IN ({','.join('?' * len(bundle_ids))})")
                params.extend(bundle_ids)
            if since is not None:
                clauses.append("r.delivered_date >= ?")
                params.append(datetime_to_apple_time(since) / 1_000_000_000)
            cursor = conn.execute(
                "SELECT r.rec_id, r.uuid, r.data, r.delivered_date, a.identifier "
                "FROM record r LEFT JOIN app a ON a.app_id = r.app_id "
                f"WHERE {' AND '.join(clauses)} ORDER BY r.delivered_date DESC",
                params,
            )
            needle = query.casefold() if query else None
            results: list[AppNotification] = []
            for rec_id, raw_uuid, data, delivered, identifier in cursor:
                fields = parse_payload(data)
                if needle and not any(needle in (fields[k] or "").casefold() for k in ("title", "subtitle", "body")):
                    continue
                bundle_id = identifier or fields["bundle_id"]
                has_uuid = isinstance(raw_uuid, bytes) and len(raw_uuid) == 16
                results.append(
                    AppNotification(
                        id=str(uuid.UUID(bytes=raw_uuid)) if has_uuid else str(rec_id),
                        app=app_name(bundle_id),
                        bundle_id=bundle_id,
                        title=fields["title"],
                        subtitle=fields["subtitle"],
                        body=fields["body"],
                        delivered_at=apple_time_to_datetime(delivered),
                    )
                )
                if len(results) >= limit:
                    break
            return results

    def apps(self) -> list[NotificationApp]:
        """Apps that have notifications stored, most recent first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT a.identifier, COUNT(*), MAX(r.delivered_date) FROM record r JOIN app a ON a.app_id = r.app_id "
                "WHERE r.delivered_date IS NOT NULL GROUP BY a.identifier ORDER BY MAX(r.delivered_date) DESC"
            ).fetchall()
        return [
            NotificationApp(
                app=app_name(bundle_id), bundle_id=bundle_id, count=count, latest_at=apple_time_to_datetime(latest)
            )
            for bundle_id, count, latest in rows
        ]
