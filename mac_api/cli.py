from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .config import DEFAULT_KEY_FILE, Settings, load_or_create_api_key
from .network import LOOPBACK_HOSTS, PRIVATE_NETWORKS, bonjour_name, lan_address, parse_networks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mac-api", description="Serve macOS services over HTTP and MCP.")
    network = parser.add_argument_group("network")
    network.add_argument("--lan", action="store_true",
                         help="Listen on all interfaces and only accept private (LAN/Tailscale) addresses")
    network.add_argument("--host", default=os.environ.get("MAC_API_HOST"),
                         help="Address to listen on (default: 127.0.0.1, or 0.0.0.0 with --lan)")
    network.add_argument("--port", type=int, default=int(os.environ.get("MAC_API_PORT", "8765")))
    network.add_argument("--allow", action="append", metavar="CIDR",
                         help="Only accept clients from this network, e.g. 192.168.1.0/24 (repeatable)")
    network.add_argument("--ssl-certfile", help="Serve HTTPS with this certificate (e.g. made with mkcert)")
    network.add_argument("--ssl-keyfile", help="Private key for --ssl-certfile")

    security = parser.add_argument_group("security")
    security.add_argument("--api-key", help=f"Token to require (default: MAC_API_KEY, else {DEFAULT_KEY_FILE})")
    security.add_argument("--rotate-key", action="store_true", help="Replace the stored token with a new one and exit")
    security.add_argument("--read-only", action="store_true", help="Reject everything that changes something")
    security.add_argument("--no-auth", action="store_true", help="Do not require a token (only allowed on 127.0.0.1)")

    other = parser.add_argument_group("other")
    other.add_argument("--no-mcp", action="store_true", help="Do not serve the MCP endpoint at /mcp")
    other.add_argument("--print-key", action="store_true", help="Print the token and exit")
    other.add_argument("--print-mcp-config", action="store_true", help="Print MCP client settings and exit")
    other.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    other.add_argument("--version", action="version", version=f"mac-api {__version__}")
    return parser


def mcp_client_config(url: str, key: str) -> str:
    """Instructions for the common MCP clients, ready to paste."""
    auth = f"Bearer {key}"
    generic = {"mcpServers": {"mac": {"type": "http", "url": url, "headers": {"Authorization": auth}}}}
    remote_args = ["-y", "mcp-remote", url, "--header", "Authorization:${MAC_API_AUTH}"]
    if url.startswith("http://"):
        remote_args.insert(3, "--allow-http")
    desktop = {"mcpServers": {"mac": {"command": "npx", "args": remote_args, "env": {"MAC_API_AUTH": auth}}}}
    return "\n".join([
        f"MCP endpoint: {url}",
        f"Token:        {key}",
        "",
        "Claude Code:",
        f'  claude mcp add --transport http mac {url} --header "Authorization: {auth}"',
        "",
        "Clients configured with a URL and headers (Cursor, LM Studio, a project .mcp.json, ...):",
        json.dumps(generic, indent=2),
        "",
        "Claude Desktop (claude_desktop_config.json; needs Node.js on that computer):",
        json.dumps(desktop, indent=2),
    ])


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    settings = Settings.from_env()
    if args.api_key:
        settings.api_key = args.api_key
    if args.no_auth:
        settings.auth_disabled = True
    if args.read_only:
        settings.read_only = True
    if args.no_mcp:
        settings.mcp_enabled = False
    if args.allow:
        settings.allowed_networks = args.allow
    elif args.lan and not settings.allowed_networks:
        settings.allowed_networks = list(PRIVATE_NETWORKS)
    try:
        parse_networks(settings.allowed_networks)
    except ValueError as exc:
        parser.error(f"invalid network: {exc}")
    if settings.auth_disabled and host not in LOOPBACK_HOSTS:
        parser.error("--no-auth is only allowed when listening on 127.0.0.1")
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("--ssl-certfile and --ssl-keyfile go together")

    if args.rotate_key:
        key, _ = load_or_create_api_key(rotate=True)
        print(f"New token saved to {DEFAULT_KEY_FILE}:\n{key}\nRestart mac-api and update your clients.")
        return

    created = False
    if not settings.auth_disabled and not settings.api_key:
        settings.api_key, created = load_or_create_api_key()

    scheme = "https" if args.ssl_certfile else "http"
    local_url = f"{scheme}://127.0.0.1:{args.port}"
    lan_ip = lan_address()
    lan_url = f"{scheme}://{lan_ip}:{args.port}" if lan_ip else None

    if args.print_key:
        print(settings.api_key or "Authentication is disabled.")
        return
    if args.print_mcp_config:
        if settings.auth_disabled:
            parser.error("--print-mcp-config needs a token; drop --no-auth")
        print(mcp_client_config(f"{lan_url or local_url}/mcp", settings.api_key))
        if host in LOOPBACK_HOSTS:
            print("\nStart the server with `mac-api --lan` so other devices can reach it.")
        return

    if sys.platform != "darwin":
        print("warning: not running on macOS; most endpoints will return 501.", file=sys.stderr)
    if settings.auth_disabled:
        print("warning: authentication is disabled.", file=sys.stderr)
    elif created:
        print(f"Created a token in {DEFAULT_KEY_FILE}:\n\n    {settings.api_key}\n")
        print("Send it as 'Authorization: Bearer <token>'. Show it again with: mac-api --print-key\n")

    urls = [local_url]
    if host not in LOOPBACK_HOSTS:
        bonjour = bonjour_name()
        urls += [url for url in (lan_url, f"{scheme}://{bonjour}:{args.port}" if bonjour else None) if url]
        if scheme == "http":
            print("note: plain HTTP; the token is readable by anyone who can watch your network traffic.\n"
                  "      Use --ssl-certfile/--ssl-keyfile, or a VPN such as Tailscale, on networks you don't control.\n")
    for url in urls:
        print(f"API docs: {url}/docs    MCP: {url}/mcp" if settings.mcp_enabled else f"API docs: {url}/docs")
    if settings.allowed_networks:
        print(f"Accepting clients from: {', '.join(settings.allowed_networks)} (and this Mac)")
    if settings.mcp_enabled and not settings.auth_disabled:
        print("MCP client setup: mac-api --print-mcp-config")
    print()

    import uvicorn

    from .app import create_app

    uvicorn.run(
        create_app(settings),
        host=host,
        port=args.port,
        log_level=args.log_level,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
    )


if __name__ == "__main__":
    main()
