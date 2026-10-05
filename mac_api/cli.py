from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .config import DEFAULT_KEY_FILE, Settings, load_or_create_api_key

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mac-api", description="Serve macOS services over a local HTTP API.")
    parser.add_argument("--host", default=os.environ.get("MAC_API_HOST", "127.0.0.1"),
                        help="Address to listen on (default: 127.0.0.1, this Mac only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MAC_API_PORT", "8765")))
    parser.add_argument("--api-key", help=f"API key to require (default: MAC_API_KEY, else {DEFAULT_KEY_FILE})")
    parser.add_argument("--no-auth", action="store_true", help="Do not require an API key (only for local testing)")
    parser.add_argument("--read-only", action="store_true", help="Reject every request that changes something")
    parser.add_argument("--print-key", action="store_true", help="Print the API key and exit")
    parser.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    parser.add_argument("--version", action="version", version=f"mac-api {__version__}")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.api_key:
        settings.api_key = args.api_key
    if args.no_auth:
        settings.auth_disabled = True
    if args.read_only:
        settings.read_only = True

    created = False
    if not settings.auth_disabled and not settings.api_key:
        settings.api_key, created = load_or_create_api_key()

    if args.print_key:
        print(settings.api_key or "Authentication is disabled.")
        return

    if sys.platform != "darwin":
        print("warning: not running on macOS; most endpoints will return 501.", file=sys.stderr)
    if settings.auth_disabled:
        print("warning: authentication is disabled.", file=sys.stderr)
        if args.host not in LOCAL_HOSTS:
            print(f"warning: anyone who can reach {args.host}:{args.port} can read your messages.", file=sys.stderr)
    elif created:
        print(f"Created an API key in {DEFAULT_KEY_FILE}:\n\n    {settings.api_key}\n")
        print("Send it as 'X-API-Key: <key>'. Show it again with: mac-api --print-key\n")

    host = f"[{args.host}]" if ":" in args.host else args.host
    print(f"Docs: http://{host}:{args.port}/docs   Permissions check: GET /diagnostics?automation=true\n")

    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
