"""Restrict which client addresses may connect, and find this Mac's LAN address."""

from __future__ import annotations

import ipaddress
import socket
import subprocess
from collections.abc import Iterable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Home/office LANs, Tailscale (100.64.0.0/10) and their IPv6 equivalents.
PRIVATE_NETWORKS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "fc00::/7",
    "fe80::/10",
)
_ALWAYS_ALLOWED = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))


def parse_networks(values: Iterable[str]) -> list[IPNetwork]:
    """Parse CIDRs or single addresses; raises ValueError for anything else."""
    return [ipaddress.ip_network(value.strip(), strict=False) for value in values if value.strip()]


def address_allowed(host: str | None, networks: list[IPNetwork]) -> bool:
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped  # "::ffff:192.168.1.5" when listening on "::"
    return any(address in network for network in (*_ALWAYS_ALLOWED, *networks))


class AllowedNetworksMiddleware:
    """Reject clients outside the allowed networks before anything else runs. Loopback is always allowed."""

    def __init__(self, app: ASGIApp, networks: list[IPNetwork]) -> None:
        self.app = app
        self.networks = networks

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if not address_allowed(client[0] if client else None, self.networks):
                response = JSONResponse({"detail": "Your network address is not allowed"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def lan_address() -> str | None:
    """The address other devices on the LAN can reach this Mac at (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))  # TEST-NET-1; only selects the outgoing interface
            address = sock.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


def bonjour_name() -> str | None:
    """`<name>.local`, which Apple devices (and most others) resolve on the LAN."""
    try:
        name = subprocess.run(
            ["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{name}.local" if name else None
