"""Read-only access to Contacts, straight from the AddressBook databases.

Reading the SQLite files is much faster than scripting Contacts.app, which matters
because every Messages response resolves phone numbers and emails to names.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel

from ..errors import FULL_DISK_ACCESS_HINT, MacAPIError

_LABEL_RE = re.compile(r"^_\$!<(.*)>!\$_$")


class LabeledValue(BaseModel):
    label: str | None = None
    value: str


class Contact(BaseModel):
    id: str
    name: str
    first_name: str | None = None
    last_name: str | None = None
    organization: str | None = None
    nickname: str | None = None
    phones: list[LabeledValue] = []
    emails: list[LabeledValue] = []


def clean_label(label: str | None) -> str | None:
    """Turn AddressBook's internal labels like `_$!<Mobile>!$_` into `Mobile`."""
    if not label:
        return None
    match = _LABEL_RE.match(label)
    return match.group(1) if match else label


def phone_key(value: str) -> str | None:
    """A comparison key for phone numbers that ignores formatting and country prefixes."""
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    return digits[-9:] if len(digits) >= 7 else digits


def handle_key(handle: str) -> str | None:
    """A comparison key for a Messages handle (a phone number or an email address)."""
    handle = handle.strip()
    if "@" in handle:
        return handle.lower()
    return phone_key(handle)


def fold(text: str) -> str:
    """Lowercase and strip accents so that e.g. `Κώστας` matches `κωστας`."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


class ContactsIndex:
    def __init__(self, addressbook_dir: Path, ttl: float = 300.0) -> None:
        self.addressbook_dir = addressbook_dir
        self.ttl = ttl
        self.error: MacAPIError | None = None
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self._contacts: list[Contact] = []
        self._by_handle: dict[str, Contact] = {}

    def database_paths(self) -> list[Path]:
        try:
            paths = sorted(self.addressbook_dir.glob("Sources/*/AddressBook-v22.abcddb"))
            root = self.addressbook_dir / "AddressBook-v22.abcddb"
            if root.exists():
                paths.append(root)
        except PermissionError:
            return []
        return paths

    def _load_database(self, path: Path) -> list[Contact]:
        source = path.parent.name if path.parent.parent.name == "Sources" else "local"
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
        try:
            record_columns = _columns(conn, "ZABCDRECORD")
            wanted = ["ZFIRSTNAME", "ZMIDDLENAME", "ZLASTNAME", "ZORGANIZATION", "ZNICKNAME"]
            name_columns = [c for c in wanted if c in record_columns]
            if not name_columns:
                return []
            records = conn.execute(f"SELECT Z_PK, {', '.join(name_columns)} FROM ZABCDRECORD").fetchall()
            phones: dict[int, list[LabeledValue]] = {}
            for owner, number, label in conn.execute(
                "SELECT ZOWNER, ZFULLNUMBER, ZLABEL FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL"
            ):
                phones.setdefault(owner, []).append(LabeledValue(label=clean_label(label), value=number))
            emails: dict[int, list[LabeledValue]] = {}
            for owner, address, label in conn.execute(
                "SELECT ZOWNER, ZADDRESS, ZLABEL FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL"
            ):
                emails.setdefault(owner, []).append(LabeledValue(label=clean_label(label), value=address))
        finally:
            conn.close()

        contacts = []
        for row in records:
            pk = row[0]
            fields = {column: (value or "").strip() or None for column, value in zip(name_columns, row[1:])}
            first, middle, last = fields.get("ZFIRSTNAME"), fields.get("ZMIDDLENAME"), fields.get("ZLASTNAME")
            organization, nickname = fields.get("ZORGANIZATION"), fields.get("ZNICKNAME")
            contact_phones, contact_emails = phones.get(pk, []), emails.get(pk, [])
            full_name = " ".join(part for part in (first, middle, last) if part)
            if not (full_name or organization or contact_phones or contact_emails):
                continue  # groups and other non-person records
            name = full_name or organization or nickname
            if not name:
                name = (contact_phones or contact_emails)[0].value
            contacts.append(
                Contact(
                    id=f"{source}:{pk}",
                    name=name,
                    first_name=first,
                    last_name=last,
                    organization=organization,
                    nickname=nickname,
                    phones=contact_phones,
                    emails=contact_emails,
                )
            )
        return contacts

    def _load(self) -> None:
        contacts: list[Contact] = []
        error: MacAPIError | None = None
        paths = self.database_paths()
        if not paths:
            error = MacAPIError(
                503, f"No Contacts databases found in {self.addressbook_dir}", hint=FULL_DISK_ACCESS_HINT
            )
        for path in paths:
            try:
                contacts.extend(self._load_database(path))
            except PermissionError:
                error = MacAPIError(403, "Permission denied reading the Contacts database", hint=FULL_DISK_ACCESS_HINT)
            except sqlite3.Error as exc:
                error = MacAPIError(500, f"Could not read {path.name}: {exc}", hint=FULL_DISK_ACCESS_HINT)

        # The same person can appear in several sources (e.g. iCloud and "On My Mac").
        seen: set[tuple] = set()
        unique: list[Contact] = []
        for contact in contacts:
            signature = (
                contact.name,
                frozenset(phone_key(p.value) for p in contact.phones),
                frozenset(e.value.lower() for e in contact.emails),
            )
            if signature not in seen:
                seen.add(signature)
                unique.append(contact)

        by_handle: dict[str, Contact] = {}
        for contact in unique:
            for value in [p.value for p in contact.phones] + [e.value for e in contact.emails]:
                key = handle_key(value)
                if key:
                    by_handle.setdefault(key, contact)

        self._contacts = unique
        self._by_handle = by_handle
        self.error = error if not unique else None
        self._loaded_at = time.monotonic()

    def _ensure_loaded(self) -> None:
        with self._lock:
            if not self._loaded_at or time.monotonic() - self._loaded_at > self.ttl:
                self._load()

    def refresh(self) -> None:
        with self._lock:
            self._load()

    def all(self) -> list[Contact]:
        self._ensure_loaded()
        if self.error:
            raise self.error
        return self._contacts

    def lookup(self, handle: str | None) -> Contact | None:
        """Find the contact behind a phone number or email. Never raises."""
        if not handle:
            return None
        self._ensure_loaded()
        key = handle_key(handle)
        return self._by_handle.get(key) if key else None

    def name_for(self, handle: str | None) -> str | None:
        contact = self.lookup(handle)
        return contact.name if contact else None

    def search(self, query: str) -> list[Contact]:
        contacts = self.all()
        needle = fold(query.strip())
        digits = re.sub(r"\D", "", query)
        key = phone_key(query) if len(digits) >= 7 else None

        def phone_matches(value: str) -> bool:
            return digits in re.sub(r"\D", "", value) or (key is not None and phone_key(value) == key)

        results = []
        for contact in contacts:
            haystack = fold(" ".join(filter(None, [contact.name, contact.nickname, contact.organization])))
            if needle and needle in haystack:
                results.append(contact)
            elif any(needle and needle in e.value.lower() for e in contact.emails):
                results.append(contact)
            elif len(digits) >= 3 and any(phone_matches(p.value) for p in contact.phones):
                results.append(contact)
        return results
