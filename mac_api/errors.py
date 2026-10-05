from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

FULL_DISK_ACCESS_HINT = (
    "Grant Full Disk Access to the app that runs mac-api (Terminal, iTerm, ...) in "
    "System Settings > Privacy & Security > Full Disk Access, then restart that app."
)
AUTOMATION_HINT = (
    "Allow the app that runs mac-api (Terminal, iTerm, ...) to control the target app in "
    "System Settings > Privacy & Security > Automation."
)


class MacAPIError(Exception):
    """An error with an HTTP status code and an optional hint on how to fix it."""

    def __init__(self, status_code: int, detail: str, hint: str | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.hint = hint


async def mac_api_error_handler(request: Request, exc: MacAPIError) -> JSONResponse:
    body: dict[str, str] = {"detail": exc.detail}
    if exc.hint:
        body["hint"] = exc.hint
    return JSONResponse(status_code=exc.status_code, content=body)
