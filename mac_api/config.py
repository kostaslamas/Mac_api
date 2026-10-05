from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "mac-api"
DEFAULT_KEY_FILE = CONFIG_DIR / "api_key"
TELEGRAM_CREDENTIALS_FILE = CONFIG_DIR / "telegram.json"


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
    notifications_db: Path = field(
        default_factory=lambda: Path.home() / "Library" / "Group Containers" / "group.com.apple.usernoted" / "db2" / "db"
    )
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session: Path = field(default_factory=lambda: CONFIG_DIR / "telegram.session")

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash)

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
            notifications_db=_env_path("MAC_API_NOTIFICATIONS_DB", defaults.notifications_db),
            **_telegram_settings(),
        )


def _telegram_settings() -> dict[str, object]:
    """From MAC_API_TELEGRAM_API_ID/_API_HASH, else from the file `--telegram-login` writes."""
    api_id = os.environ.get("MAC_API_TELEGRAM_API_ID")
    api_hash = os.environ.get("MAC_API_TELEGRAM_API_HASH")
    if not (api_id and api_hash) and TELEGRAM_CREDENTIALS_FILE.exists():
        try:
            stored = json.loads(TELEGRAM_CREDENTIALS_FILE.read_text())
            api_id, api_hash = stored.get("api_id"), stored.get("api_hash")
        except (OSError, ValueError):
            pass
    settings: dict[str, object] = {"telegram_api_id": int(api_id) if api_id else None, "telegram_api_hash": api_hash}
    if session := os.environ.get("MAC_API_TELEGRAM_SESSION"):
        settings["telegram_session"] = Path(session).expanduser()
    return settings


def write_private_file(path: Path, content: str) -> None:
    """Create or replace a file only the current user can read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.chmod(path, 0o600)  # in case the file already existed with wider permissions


def load_or_create_api_key(path: Path = DEFAULT_KEY_FILE, *, rotate: bool = False) -> tuple[str, bool]:
    """Return the stored API key, creating one on first use (or when rotating). The bool is True if created."""
    if path.exists() and not rotate:
        key = path.read_text().strip()
        if key:
            return key, False
    key = secrets.token_urlsafe(32)
    write_private_file(path, key + "\n")
    return key, True
