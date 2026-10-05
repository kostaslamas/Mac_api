from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_write_access
from ..services import shortcuts
from ..services.shortcuts import ShortcutResult, ShortcutRun

router = APIRouter(prefix="/shortcuts", tags=["shortcuts"])


class ShortcutRunRequest(ShortcutRun):
    name: str


@router.get("", response_model=list[str], summary="Names of your shortcuts")
def list_shortcuts(folder: str | None = None) -> list[str]:
    return shortcuts.list_shortcuts(folder)


@router.post(
    "/run",
    response_model=ShortcutResult,
    dependencies=[Depends(require_write_access)],
    summary="Run a shortcut, optionally with text input",
)
def run_shortcut(data: ShortcutRunRequest) -> ShortcutResult:
    return shortcuts.run_shortcut(data.name, data)
