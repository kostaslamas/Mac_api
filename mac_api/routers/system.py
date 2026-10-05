from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from ..auth import require_write_access
from ..services import system
from ..services.system import (
    Battery,
    ClipboardContent,
    Notification,
    OpenRequest,
    Speech,
    SystemInfo,
    Volume,
    VolumeUpdate,
)

router = APIRouter(prefix="/system", tags=["system"])
write = [Depends(require_write_access)]
NO_CONTENT = status.HTTP_204_NO_CONTENT


@router.get("/info", response_model=SystemInfo)
def get_info() -> SystemInfo:
    return system.system_info()


@router.get("/battery", response_model=Battery)
def get_battery() -> Battery:
    return system.battery()


@router.get("/volume", response_model=Volume)
def get_volume() -> Volume:
    return system.get_volume()


@router.put("/volume", response_model=Volume, dependencies=write)
def set_volume(data: VolumeUpdate) -> Volume:
    return system.set_volume(data)


@router.post("/notify", status_code=NO_CONTENT, dependencies=write, summary="Show a notification on the Mac")
def notify(data: Notification) -> Response:
    system.notify(data)
    return Response(status_code=NO_CONTENT)


@router.post("/say", status_code=NO_CONTENT, dependencies=write, summary="Speak text out loud")
def say(data: Speech) -> Response:
    system.say(data)
    return Response(status_code=NO_CONTENT)


@router.get("/clipboard", response_model=ClipboardContent)
def get_clipboard() -> ClipboardContent:
    return system.get_clipboard()


@router.put("/clipboard", status_code=NO_CONTENT, dependencies=write)
def set_clipboard(data: ClipboardContent) -> Response:
    system.set_clipboard(data)
    return Response(status_code=NO_CONTENT)


@router.post("/open", status_code=NO_CONTENT, dependencies=write, summary="Open a URL, file or app")
def open_target(data: OpenRequest) -> Response:
    system.open_target(data)
    return Response(status_code=NO_CONTENT)


@router.get(
    "/screenshot",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
    summary="Take a screenshot (needs Screen Recording permission)",
)
def screenshot(display: int | None = Query(None, ge=1, description="Display number; main display by default")) -> Response:
    return Response(content=system.screenshot(display), media_type="image/png")


@router.get("/apps", response_model=list[str], summary="Names of running apps")
def running_apps() -> list[str]:
    return system.running_apps()


@router.post("/apps/{name}/quit", status_code=NO_CONTENT, dependencies=write)
def quit_app(name: str) -> Response:
    system.quit_app(name)
    return Response(status_code=NO_CONTENT)


@router.post("/display/sleep", status_code=NO_CONTENT, dependencies=write, summary="Turn the display off")
def sleep_display() -> Response:
    system.sleep_display()
    return Response(status_code=NO_CONTENT)
