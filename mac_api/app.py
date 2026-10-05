from __future__ import annotations

from fastapi import Depends, FastAPI

from . import __version__, runner
from .auth import require_api_key
from .config import Settings
from .errors import MacAPIError, mac_api_error_handler
from .routers import calendar, contacts, diagnostics, messages, notes, reminders, shortcuts, system
from .services.contacts import ContactsIndex
from .services.messages import MessagesStore

DESCRIPTION = """
Control and read your Mac over HTTP: Reminders, iMessage/SMS, Notes, Calendar,
Contacts, Shortcuts and system functions.

Authenticate every request with `X-API-Key: <key>` or `Authorization: Bearer <key>`.
Run `GET /diagnostics` first to see which macOS permissions are still missing.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.auth_disabled and not settings.api_key:
        raise RuntimeError("Set an API key (MAC_API_KEY or --api-key), or disable auth explicitly.")
    runner.set_default_timeout(settings.osascript_timeout)

    app = FastAPI(title="Mac API", version=__version__, description=DESCRIPTION)
    app.state.settings = settings
    app.state.contacts = ContactsIndex(settings.addressbook_dir)
    app.state.messages = MessagesStore(settings.messages_db, settings.messages_attachments_dir, app.state.contacts)
    app.add_exception_handler(MacAPIError, mac_api_error_handler)

    @app.get("/health", tags=["meta"], summary="Liveness check (no API key needed)")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    protected = [Depends(require_api_key)]
    for module in (diagnostics, reminders, messages, contacts, notes, calendar, shortcuts, system):
        app.include_router(module.router, dependencies=protected)
    return app
