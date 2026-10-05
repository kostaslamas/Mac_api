from __future__ import annotations

import json

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from mac_api import cli
from mac_api.app import create_app
from mac_api.network import PRIVATE_NETWORKS

from .helpers import API_KEY, call_tools, serve, text


def test_mcp_requires_the_token(base_url):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    assert httpx.post(f"{base_url}/mcp", json=request, headers=headers).status_code == 401
    wrong = {**headers, "Authorization": "Bearer nope"}
    response = httpx.post(f"{base_url}/mcp", json=request, headers=wrong)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_mcp_tools_end_to_end(base_url, messages_db, fake_jxa):
    fake_jxa.results.append({"id": "R1", "title": "Γάλα", "completed": False, "priority": 0})
    tools, (contacts, chats, created, chat) = call_tools(
        base_url,
        [
            ("contacts_search", {"query": "κωστας"}),
            ("messages_chats", {"limit": 2}),
            ("reminders_create", {"title": "Γάλα", "notes": "1 lt", "due_date": "2026-10-06T09:00:00"}),
            ("messages_read", {"chat_id": messages_db["chat1"], "limit": 2}),
        ],
    )
    assert len(tools) == 36
    assert {"messages_send", "reminders_delete", "mac_screenshot"} <= set(tools)

    assert json.loads(text(contacts))[0]["name"] == "Κώστας Λάμπρου"
    assert "Κώστας" in text(contacts)  # real Unicode, not \u escapes
    assert [c["name"] for c in json.loads(text(chats))] == ["Friends", "Κώστας Λάμπρου"]
    assert "null" not in text(chats)

    assert not created.is_error
    args = fake_jxa.calls[-1][1]
    assert args["notes"] == "1 lt"  # the `notes` argument reaches the service
    assert args["remind_me_date"] == args["due_date"] == "2026-10-06T09:00:00"

    assert [m.get("text") for m in json.loads(text(chat))] == [None, None]  # the two photo messages


def test_mcp_errors_carry_the_hint(base_url):
    _, (result,) = call_tools(base_url, [("messages_chats", {})])
    assert result.is_error
    assert "Full Disk Access" in text(result)


def test_mcp_wrong_token_fails(base_url):
    with pytest.raises(Exception):
        call_tools(base_url, [], token="wrong")


def test_read_only_mode_hides_write_tools(settings):
    settings.read_only = True
    url_iter = serve(settings)
    url = next(url_iter)
    try:
        tools, _ = call_tools(url, [])
    finally:
        next(url_iter, None)
    assert "messages_send" not in tools and "reminders_delete" not in tools
    assert {"messages_read", "reminders_list", "calendar_events"} <= set(tools)


def test_mcp_can_be_disabled(settings):
    settings.mcp_enabled = False
    with TestClient(create_app(settings), headers={"Authorization": f"Bearer {API_KEY}"}) as client:
        assert client.post("/mcp", json={}).status_code == 404


@pytest.mark.parametrize(
    ("address", "allowed"),
    [("192.168.1.20", True), ("::ffff:192.168.1.20", True), ("100.101.102.103", True), ("127.0.0.1", True),
     ("203.0.113.9", False), ("testclient", False)],
)
def test_allowed_networks(settings, address, allowed):
    settings.allowed_networks = list(PRIVATE_NETWORKS)
    client = TestClient(create_app(settings), client=(address, 50000))
    assert client.get("/health").status_code == (200 if allowed else 403)


def test_cli_refuses_no_auth_on_the_network(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--lan", "--no-auth"])
    assert "--no-auth is only allowed" in capsys.readouterr().err


def test_cli_lan_mode(monkeypatch, capsys):
    started = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: started.update(app=app, **kwargs))
    cli.main(["--lan", "--api-key", "secret", "--port", "9000"])
    assert started["host"] == "0.0.0.0"
    assert started["port"] == 9000
    assert started["app"].state.settings.allowed_networks == list(PRIVATE_NETWORKS)
    assert "MCP: http://127.0.0.1:9000/mcp" in capsys.readouterr().out


def test_cli_prints_mcp_client_config(monkeypatch, capsys):
    monkeypatch.setattr(cli, "lan_address", lambda: "192.168.1.20")
    cli.main(["--api-key", "secret", "--print-mcp-config"])
    out = capsys.readouterr().out
    assert 'claude mcp add --transport http mac http://192.168.1.20:8765/mcp --header "Authorization: Bearer secret"' in out
    assert '"Authorization": "Bearer secret"' in out
    assert '"--allow-http"' in out
    assert "mac-api --lan" in out


def test_mcp_screenshot_is_an_image(base_url, monkeypatch):
    captured = {}

    def fake_screenshot(display=None, image_format="png", max_size=None):
        captured.update(image_format=image_format, max_size=max_size)
        return b"\xff\xd8fake-jpeg"

    monkeypatch.setattr("mac_api.services.system.screenshot", fake_screenshot)
    _, (result,) = call_tools(base_url, [("mac_screenshot", {})])
    assert not result.is_error
    image = result.content[0]
    assert (image.type, image.mime_type) == ("image", "image/jpeg")
    assert captured == {"image_format": "jpg", "max_size": 1568}
