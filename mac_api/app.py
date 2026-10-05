from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__, runner
from .auth import RequireAPIKey, require_api_key
from .config import Settings
from .errors import MacAPIError, mac_api_error_handler
from .mcp_server import build_mcp_server
from .network import AllowedNetworksMiddleware, parse_networks
from .routers import calendar, contacts, diagnostics, messages, notes, reminders, shortcuts, system
from .services.contacts import ContactsIndex
from .services.messages import MessagesStore

DESCRIPTION = """
Control and read your Mac over HTTP: Reminders, iMessage/SMS, Notes, Calendar,
Contacts, Shortcuts and system functions. AI assistants can use the same
services as MCP tools at `/mcp`.

Authenticate every request with `Authorization: Bearer <key>` or `X-API-Key: <key>`.
Run `GET /diagnostics` first to see which macOS permissions are still missing.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.auth_disabled and not settings.api_key:
        raise RuntimeError("Set an API key (MAC_API_KEY or --api-key), or disable auth explicitly.")
    networks = parse_networks(settings.allowed_networks)
    runner.set_default_timeout(settings.osascript_timeout)

    contacts_index = ContactsIndex(settings.addressbook_dir)
    messages_store = MessagesStore(settings.messages_db, settings.messages_attachments_dir, contacts_index)
    mcp_server = build_mcp_server(settings, messages_store, contacts_index) if settings.mcp_enabled else None
    mcp_app = None
    if mcp_server is not None:
        mcp_app = mcp_server.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,  # no sessions to lose when the server restarts
            json_response=True,
            # The SDK enables DNS-rebinding protection for localhost-only servers, which
            # rejects requests addressed to the LAN IP. The API key covers that threat.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if mcp_server is None:
            yield
            return
        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(title="Mac API", version=__version__, description=DESCRIPTION, lifespan=lifespan)
    app.state.settings = settings
    app.state.contacts = contacts_index
    app.state.messages = messages_store
    app.add_exception_handler(MacAPIError, mac_api_error_handler)
    if networks:
        app.add_middleware(AllowedNetworksMiddleware, networks=networks)

    @app.get("/health", tags=["meta"], summary="Liveness check (no API key needed)")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    protected = [Depends(require_api_key)]
    for module in (diagnostics, reminders, messages, contacts, notes, calendar, shortcuts, system):
        app.include_router(module.router, dependencies=protected)
    if mcp_app is not None:
        app.add_route("/mcp", RequireAPIKey(mcp_app, settings), include_in_schema=False)
    return app
