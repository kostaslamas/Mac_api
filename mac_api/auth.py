from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED = {"detail": "Invalid or missing API key"}
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def key_is_valid(settings: Settings, provided: str | None) -> bool:
    if settings.auth_disabled:
        return True
    if not provided or not settings.api_key:
        return False
    return secrets.compare_digest(provided.encode(), settings.api_key.encode())


def key_from_headers(headers: Headers) -> str | None:
    """The key from `X-API-Key: <key>` or `Authorization: Bearer <key>`."""
    if key := headers.get("x-api-key"):
        return key
    scheme, _, token = headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


def require_api_key(
    request: Request,
    header_key: str | None = Security(_api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """FastAPI dependency. Declared with Security() so the docs page gets an Authorize button."""
    provided = header_key or (bearer.credentials if bearer else None)
    if not key_is_valid(request.app.state.settings, provided):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_UNAUTHORIZED["detail"], headers=_CHALLENGE)


def require_write_access(request: Request) -> None:
    """Block endpoints that change anything when the server runs with --read-only."""
    if request.app.state.settings.read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mac-api is running in read-only mode")


class RequireAPIKey:
    """ASGI wrapper applying the same key check to a mounted app (the MCP endpoint)."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not key_is_valid(self.settings, key_from_headers(Headers(scope=scope))):
            response = JSONResponse(_UNAUTHORIZED, status_code=status.HTTP_401_UNAUTHORIZED, headers=_CHALLENGE)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
