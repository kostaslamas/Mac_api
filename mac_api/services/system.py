"""System-level helpers: notifications, speech, clipboard, volume, battery, apps, screenshots."""

from __future__ import annotations

import os
import platform
import re
import socket
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..errors import MacAPIError
from ..runner import run_applescript, run_command, run_jxa

# pbcopy/pbpaste pick the text encoding from the locale; force UTF-8 so Greek etc. survive.
_UTF8_ENV = {**os.environ, "LANG": "en_US.UTF-8", "LC_CTYPE": "UTF-8"}


class SystemInfo(BaseModel):
    hostname: str
    macos_version: str | None = None
    model: str | None = None
    architecture: str
    booted_at: datetime | None = None


class Battery(BaseModel):
    power_source: str | None = None
    percent: int | None = None
    state: str | None = None
    time_remaining: str | None = None


class Volume(BaseModel):
    output_volume: int | None = None
    input_volume: int | None = None
    alert_volume: int | None = None
    muted: bool | None = None


class VolumeUpdate(BaseModel):
    output_volume: int | None = Field(None, ge=0, le=100)
    muted: bool | None = None


class Notification(BaseModel):
    message: str
    title: str = "mac-api"
    subtitle: str | None = None
    sound: str | None = Field(None, description="A sound name such as 'Glass', 'Ping' or 'Submarine'")


class Speech(BaseModel):
    text: str = Field(..., min_length=1)
    voice: str | None = Field(None, description="Run `say -v ?` on the Mac to list voices")
    rate: int | None = Field(None, ge=50, le=500, description="Words per minute")
    wait: bool = Field(False, description="Wait until speaking has finished")


class ClipboardContent(BaseModel):
    text: str


class OpenRequest(BaseModel):
    target: str | None = Field(None, description="A URL or a file path")
    app: str | None = Field(None, description="An application name, e.g. 'Safari'")


_NOTIFY = r"""
const app = Application.currentApplication();
app.includeStandardAdditions = true;
const options = { withTitle: args.title };
if (args.subtitle) options.subtitle = args.subtitle;
if (args.sound) options.soundName = args.sound;
app.displayNotification(args.message, options);
return true;
"""

_GET_VOLUME = r"""
const app = Application.currentApplication();
app.includeStandardAdditions = true;
const s = app.getVolumeSettings();
return { output_volume: s.outputVolume, input_volume: s.inputVolume, alert_volume: s.alertVolume, muted: s.outputMuted };
"""

_SET_OUTPUT_VOLUME = """
on run argv
    set volume output volume ((item 1 of argv) as integer)
end run
"""

_SET_MUTED = """
on run argv
    set volume output muted ((item 1 of argv) is "true")
end run
"""


def _sysctl(name: str) -> str | None:
    try:
        return run_command(["sysctl", "-n", name], timeout=5).stdout.strip() or None
    except MacAPIError:
        return None


def system_info() -> SystemInfo:
    macos_version = platform.mac_ver()[0] or None
    booted_at = None
    boottime = _sysctl("kern.boottime")  # "{ sec = 1700000000, usec = 0 } ..."
    if boottime and (match := re.search(r"sec = (\d+)", boottime)):
        booted_at = datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc).astimezone()
    return SystemInfo(
        hostname=socket.gethostname(),
        macos_version=macos_version,
        model=_sysctl("hw.model"),
        architecture=platform.machine(),
        booted_at=booted_at,
    )


def parse_pmset(output: str) -> Battery:
    battery = Battery()
    if match := re.search(r"drawing from '([^']+)'", output):
        battery.power_source = match.group(1)
    if match := re.search(r"(\d+)%;\s*([^;]+);\s*([^\n]*)", output):
        battery.percent = int(match.group(1))
        battery.state = match.group(2).strip()
        remaining = re.search(r"(\d+:\d+) remaining", match.group(3))
        battery.time_remaining = remaining.group(1) if remaining else None
    return battery


def battery() -> Battery:
    return parse_pmset(run_command(["pmset", "-g", "batt"], timeout=10).stdout)


def get_volume() -> Volume:
    return Volume(**run_jxa(_GET_VOLUME))


def set_volume(update: VolumeUpdate) -> Volume:
    if update.output_volume is not None:
        run_applescript(_SET_OUTPUT_VOLUME, str(update.output_volume))
    if update.muted is not None:
        run_applescript(_SET_MUTED, "true" if update.muted else "false")
    return get_volume()


def notify(notification: Notification) -> None:
    run_jxa(_NOTIFY, notification.model_dump())


def say(speech: Speech) -> None:
    cmd = ["say", "-f", "-"]
    if speech.voice:
        cmd += ["-v", speech.voice]
    if speech.rate:
        cmd += ["-r", str(speech.rate)]
    if speech.wait:
        run_command(cmd, input=speech.text, timeout=600)
    else:
        threading.Thread(target=run_command, args=(cmd,), kwargs={"input": speech.text, "timeout": 600}, daemon=True).start()


def get_clipboard() -> ClipboardContent:
    return ClipboardContent(text=run_command(["pbpaste"], timeout=10, env=_UTF8_ENV).stdout)


def set_clipboard(content: ClipboardContent) -> None:
    run_command(["pbcopy"], input=content.text, timeout=10, env=_UTF8_ENV)


def open_target(request: OpenRequest) -> None:
    if not request.target and not request.app:
        raise MacAPIError(400, "Provide 'target' (URL or path), 'app', or both")
    cmd = ["open"]
    if request.app:
        cmd += ["-a", request.app]
    if request.target:
        cmd.append(request.target)
    run_command(cmd, timeout=30)


def screenshot(display: int | None = None) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "screenshot.png"
        cmd = ["screencapture", "-x", "-t", "png"]
        if display:
            cmd += ["-D", str(display)]
        run_command([*cmd, str(path)], timeout=30)
        if not path.exists():
            raise MacAPIError(500, "screencapture did not produce an image", hint="Grant Screen Recording permission.")
        return path.read_bytes()


def running_apps() -> list[str]:
    script = r"""
const se = Application('System Events');
return se.processes.whose({ backgroundOnly: false }).name();
"""
    return sorted(run_jxa(script), key=str.lower)


def quit_app(name: str) -> None:
    run_applescript('on run argv\n    tell application (item 1 of argv) to quit\nend run\n', name)


def sleep_display() -> None:
    run_command(["pmset", "displaysleepnow"], timeout=10)

