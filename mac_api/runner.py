"""Run macOS command-line tools, AppleScript and JavaScript for Automation (JXA).

Scripts are always static source code: user data is passed as arguments (JSON for
JXA, argv for AppleScript), so nothing a client sends is ever spliced into a script.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from .errors import AUTOMATION_HINT, MacAPIError

DEFAULT_TIMEOUT = 60.0

# Error numbers raised by Apple events, and the HTTP status they map to.
NOT_AUTHORIZED = -1743
NOT_FOUND = -1728
BAD_REQUEST = -50
_STATUS_BY_ERROR = {
    NOT_AUTHORIZED: 403,
    -10004: 403,  # privilege violation
    NOT_FOUND: 404,
    -1719: 404,  # invalid index
    BAD_REQUEST: 400,
    -1712: 504,  # Apple event timed out
}

# Helpers available to every JXA script, followed by the script body. The body runs
# inside a function that receives the parsed JSON arguments as `args`.
_JXA_TEMPLATE = r"""
function pad(n) { return (n < 10 ? '0' : '') + n; }
function iso(d) {
  if (!d) return null;
  const offset = -d.getTimezoneOffset();
  const sign = offset >= 0 ? '+' : '-';
  const abs = Math.abs(offset);
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
    'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) +
    sign + pad(Math.floor(abs / 60)) + ':' + pad(abs % 60);
}
function fail(code, message) { const e = new Error(message); e.errorNumber = code; throw e; }
function notFound(message) { fail(-1728, message); }
function badRequest(message) { fail(-50, message); }
function bulk(getter, count) {
  try { return getter(); } catch (e) { return new Array(count).fill(null); }
}
function run(argv) {
  const args = JSON.parse(argv[0] || '{}');
  try {
    const result = (function (args) {
__BODY__
    })(args);
    return JSON.stringify({ ok: true, result: result === undefined ? null : result });
  } catch (e) {
    return JSON.stringify({
      ok: false,
      error: String((e && e.message) || e),
      code: (e && e.errorNumber) || null,
    });
  }
}
"""


def set_default_timeout(seconds: float) -> None:
    global DEFAULT_TIMEOUT
    DEFAULT_TIMEOUT = seconds


def build_jxa(body: str) -> str:
    return _JXA_TEMPLATE.replace("__BODY__", body)


def run_command(
    cmd: list[str],
    *,
    input: str | None = None,
    timeout: float | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            cmd,
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
            env=env,
        )
    except FileNotFoundError:
        raise MacAPIError(501, f"'{cmd[0]}' is not available. This endpoint only works on macOS.")
    except subprocess.TimeoutExpired:
        raise MacAPIError(504, f"'{cmd[0]}' timed out")
    if check and proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip() or f"'{cmd[0]}' exited with {proc.returncode}"
        raise MacAPIError(500, message)
    return proc


def _error_from_code(code: int | None, message: str) -> MacAPIError:
    status = _STATUS_BY_ERROR.get(code or 0, 500)
    hint = AUTOMATION_HINT if status == 403 else None
    return MacAPIError(status, message, hint=hint)


def run_applescript(script: str, *args: str, timeout: float | None = None) -> str:
    """Run an AppleScript whose `on run argv` handler receives `args`; return stdout."""
    proc = run_command(["osascript", "-", *args], input=script, timeout=timeout, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        match = re.search(r"\((-?\d+)\)\s*$", stderr)
        code = int(match.group(1)) if match else None
        message = re.sub(r"^.*?execution error:\s*", "", stderr)
        raise _error_from_code(code, message or "AppleScript failed")
    return proc.stdout.rstrip("\n")


def run_jxa(body: str, args: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
    """Run a JXA script body with `args` and return its JSON-decoded result."""
    proc = run_command(
        ["osascript", "-l", "JavaScript", "-", json.dumps(args or {})],
        input=build_jxa(body),
        timeout=timeout,
        check=False,
    )
    output = proc.stdout.strip()
    if proc.returncode != 0 or not output:
        stderr = proc.stderr.strip()
        match = re.search(r"\((-?\d+)\)\s*$", stderr)
        raise _error_from_code(int(match.group(1)) if match else None, stderr or "JXA script failed")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        raise MacAPIError(500, f"Unexpected output from osascript: {output[:200]}")
    if not payload.get("ok"):
        raise _error_from_code(payload.get("code"), payload.get("error") or "JXA script failed")
    return payload.get("result")
