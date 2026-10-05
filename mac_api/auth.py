from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer = HTTPBearer(auto_error=False)


def require_api_key(
    request: Request,
    header_key: str | None = Security(_api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """Accept the key as `X-API-Key: <key>` or `Authorization: Bearer <key>`."""
    settings = request.app.state.settings
    if settings.auth_disabled:
        return
    provided = header_key or (bearer.credentials if bearer else None)
    if not provided or not secrets.compare_digest(provided.encode(), settings.api_key.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_write_access(request: Request) -> None:
    """Block endpoints that change anything when the server runs with --read-only."""
    if request.app.state.settings.read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mac-api is running in read-only mode")
