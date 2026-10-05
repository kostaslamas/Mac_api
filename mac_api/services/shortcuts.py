"""Run Shortcuts (the `shortcuts` command-line tool), which reaches almost anything on the Mac."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from ..errors import MacAPIError
from ..runner import run_command


class ShortcutRun(BaseModel):
    input: str | None = Field(None, description="Text passed to the shortcut as its input")
    timeout: float = Field(120, gt=0, le=600, description="Seconds to wait for the shortcut")


class ShortcutResult(BaseModel):
    name: str
    output: str | None = Field(None, description="The shortcut's output, if it is text")
    output_base64: str | None = Field(None, description="The shortcut's output, if it is binary")


def list_shortcuts(folder: str | None = None) -> list[str]:
    cmd = ["shortcuts", "list"]
    if folder:
        cmd += ["--folder-name", folder]
    output = run_command(cmd, timeout=30).stdout
    return [line.strip() for line in output.splitlines() if line.strip()]


def run_shortcut(name: str, request: ShortcutRun) -> ShortcutResult:
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "output"
        cmd = ["shortcuts", "run", name, "--output-path", str(output_path)]
        if request.input is not None:
            input_path = Path(tmp) / "input.txt"
            input_path.write_text(request.input, encoding="utf-8")
            cmd += ["--input-path", str(input_path)]
        proc = run_command(cmd, timeout=request.timeout, check=False)
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout).strip() or f"Shortcut '{name}' failed"
            lowered = message.lower()
            missing = "couldn’t find" in lowered or "couldn't find" in lowered or "not be found" in lowered
            raise MacAPIError(404 if missing else 500, message)
        if not output_path.exists():
            return ShortcutResult(name=name)
        data = output_path.read_bytes()
    try:
        return ShortcutResult(name=name, output=data.decode("utf-8"))
    except UnicodeDecodeError:
        return ShortcutResult(name=name, output_base64=base64.b64encode(data).decode())
