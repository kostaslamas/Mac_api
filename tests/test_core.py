from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from mac_api import runner
from mac_api.app import create_app
from mac_api.config import Settings, load_or_create_api_key
from mac_api.errors import MacAPIError
from mac_api.services import calendar, notes, reminders, system
from mac_api.services.system import parse_pmset

from .conftest import API_KEY


def test_auth(settings):
    client = TestClient(create_app(settings))
    assert client.get("/health").status_code == 200
    assert client.get("/contacts").status_code == 401
    assert client.get("/contacts", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/contacts", headers={"X-API-Key": API_KEY}).status_code == 200
    assert client.get("/contacts", headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 200


def test_auth_can_be_disabled(settings):
    settings.api_key = None
    with pytest.raises(RuntimeError):
        create_app(settings)
    settings.auth_disabled = True
    assert TestClient(create_app(settings)).get("/contacts").status_code == 200


def test_api_key_file_is_created_once(tmp_path):
    path = tmp_path / "mac-api" / "api_key"
    key, created = load_or_create_api_key(path)
    assert created and len(key) > 30
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_or_create_api_key(path) == (key, False)


class FakeProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_run_jxa_passes_json_and_decodes_result(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["input"] = cmd, kwargs["input"]
        return FakeProcess(stdout=json.dumps({"ok": True, "result": [1, 2]}) + "\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert runner.run_jxa("return 1;", {"name": "Ελένη \"quoted\""}) == [1, 2]
    assert seen["cmd"][:4] == ["osascript", "-l", "JavaScript", "-"]
    assert json.loads(seen["cmd"][4]) == {"name": "Ελένη \"quoted\""}
    assert "return 1;" in seen["input"]


@pytest.mark.parametrize(
    ("code", "status"), [(-1743, 403), (-1728, 404), (-50, 400), (-1712, 504), (-2700, 500), (None, 500)]
)
def test_run_jxa_maps_error_numbers(monkeypatch, code, status):
    output = json.dumps({"ok": False, "error": "boom", "code": code})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeProcess(stdout=output))
    with pytest.raises(MacAPIError) as excinfo:
        runner.run_jxa("return 1;")
    assert excinfo.value.status_code == status
    assert (excinfo.value.hint is not None) == (status == 403)


def test_run_applescript_parses_errors(monkeypatch):
    stderr = "123:456: execution error: Not authorized to send Apple events to Messages. (-1743)\n"
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeProcess(returncode=1, stderr=stderr))
    with pytest.raises(MacAPIError) as excinfo:
        runner.run_applescript("on run argv\nend run", "a")
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Not authorized to send Apple events to Messages. (-1743)"


def test_missing_tool_is_501(monkeypatch):
    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(MacAPIError) as excinfo:
        runner.run_jxa("return 1;")
    assert excinfo.value.status_code == 501


def test_parse_pmset():
    laptop = parse_pmset(
        "Now drawing from 'Battery Power'\n"
        " -InternalBattery-0 (id=1234567)\t85%; discharging; 4:32 remaining present: true\n"
    )
    assert (laptop.power_source, laptop.percent, laptop.state, laptop.time_remaining) == (
        "Battery Power", 85, "discharging", "4:32")
    charging = parse_pmset(
        "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t40%; charging; (no estimate) present: true\n"
    )
    assert (charging.percent, charging.state, charging.time_remaining) == (40, "charging", None)
    desktop = parse_pmset("Now drawing from 'AC Power'\n")
    assert (desktop.power_source, desktop.percent) == ("AC Power", None)


def all_jxa_scripts() -> dict[str, str]:
    scripts = {}
    for module in (reminders, notes, calendar):
        for name, body in module.SCRIPTS.items():
            scripts[f"{module.__name__}.{name}"] = module._PRELUDE + body
    for name in ("_NOTIFY", "_GET_VOLUME"):
        scripts[f"system.{name}"] = getattr(system, name)
    return scripts


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(("name", "body"), sorted(all_jxa_scripts().items()))
def test_jxa_scripts_are_valid_javascript(tmp_path, name, body):
    path = tmp_path / "script.js"
    path.write_text(runner.build_jxa(body))
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_jxa_helpers_behave(tmp_path):
    body = r"""
if (args.fail) notFound('missing thing');
return { date: iso(new Date(2026, 0, 2, 3, 4, 5)), none: iso(null), fallback: bulk(() => { throw 1; }, 2) };
"""
    path = tmp_path / "script.js"
    path.write_text(runner.build_jxa(body) + "\nconsole.log(run([process.argv[2]]));\n")

    def run(args):
        out = subprocess.run(["node", str(path), json.dumps(args)], capture_output=True, text=True,
                             env={"TZ": "Europe/Athens", "PATH": ""}, executable=shutil.which("node"))
        return json.loads(out.stdout)

    assert run({}) == {"ok": True, "result": {"date": "2026-01-02T03:04:05+02:00", "none": None,
                                              "fallback": [None, None]}}
    assert run({"fail": True}) == {"ok": False, "error": "missing thing", "code": -1728}
