from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_KEY_FILE = Path.home() / ".config" / "mac-api" / "api_key"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


@dataclass
class Settings:
    api_key: str | None = None
    auth_disabled: bool = False
    read_only: bool = False
    mcp_enabled: bool = True
    allowed_networks: list[str] = field(default_factory=list)  # empty: no address restriction
    osascript_timeout: float = 60.0
    messages_db: Path = field(default_factory=lambda: Path.home() / "Library" / "Messages" / "chat.db")
    messages_attachments_dir: Path = field(
        default_factory=lambda: Path.home() / "Library" / "Messages" / "Attachments"
    )
    addressbook_dir: Path = field(
        default_factory=lambda: Path.home() / "Library" / "Application Support" / "AddressBook"
    )

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        return cls(
            api_key=os.environ.get("MAC_API_KEY") or None,
            auth_disabled=_env_bool("MAC_API_NO_AUTH"),
            read_only=_env_bool("MAC_API_READ_ONLY"),
            mcp_enabled=not _env_bool("MAC_API_NO_MCP"),
            allowed_networks=[n for n in os.environ.get("MAC_API_ALLOWED_NETWORKS", "").split(",") if n.strip()],
            osascript_timeout=float(os.environ.get("MAC_API_OSASCRIPT_TIMEOUT", defaults.osascript_timeout)),
            messages_db=_env_path("MAC_API_MESSAGES_DB", defaults.messages_db),
            messages_attachments_dir=_env_path(
                "MAC_API_MESSAGES_ATTACHMENTS_DIR", defaults.messages_attachments_dir
            ),
            addressbook_dir=_env_path("MAC_API_ADDRESSBOOK_DIR", defaults.addressbook_dir),
        )


def load_or_create_api_key(path: Path = DEFAULT_KEY_FILE, *, rotate: bool = False) -> tuple[str, bool]:
    """Return the stored API key, creating one on first use (or when rotating). The bool is True if created."""
    if path.exists() and not rotate:
        key = path.read_text().strip()
        if key:
            return key, False
    key = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    return key, True
