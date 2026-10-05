from __future__ import annotations

import platform
import shutil
import sys

from fastapi import APIRouter, Query, Request

from .. import __version__
from ..errors import MacAPIError
from ..runner import run_jxa

router = APIRouter(tags=["meta"])

# A cheap call per app that needs Automation permission. Running these makes macOS
# show its "allow access?" prompts, which is handy while setting things up.
_AUTOMATION_PROBES = {
    "Reminders": "return Application('Reminders').lists.name().length;",
    "Notes": "return Application('Notes').folders.name().length;",
    "Calendar": "return Application('Calendar').calendars.name().length;",
    "Messages": "return Application('Messages').chats.id().length;",
    "System Events": "return Application('System Events').processes.name().length;",
}


def _check(fn) -> dict[str, object]:
    try:
        fn()
        return {"ok": True}
    except MacAPIError as exc:
        result: dict[str, object] = {"ok": False, "error": exc.detail}
        if exc.hint:
            result["hint"] = exc.hint
        return result


@router.get("/diagnostics", summary="Check which permissions and tools are available")
def diagnostics(
    request: Request,
    automation: bool = Query(
        False, description="Also probe Reminders, Notes, Calendar, Messages and System Events (may show prompts)"
    ),
) -> dict[str, object]:
    settings = request.app.state.settings

    def read_messages() -> None:
        with request.app.state.messages.connect() as conn:
            conn.execute("SELECT 1 FROM message LIMIT 1").fetchall()

    def read_contacts() -> None:
        request.app.state.contacts.refresh()
        request.app.state.contacts.all()

    report: dict[str, object] = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "macos": sys.platform == "darwin",
        "read_only": settings.read_only,
        "auth_enabled": not settings.auth_disabled,
        "commands": {
            cmd: shutil.which(cmd) is not None
            for cmd in ("osascript", "shortcuts", "screencapture", "say", "pbcopy", "pmset")
        },
        "full_disk_access": {"messages": _check(read_messages), "contacts": _check(read_contacts)},
    }
    if automation:
        report["automation"] = {app: _check(lambda body=body: run_jxa(body, timeout=30)) for app, body in _AUTOMATION_PROBES.items()}
    return report
