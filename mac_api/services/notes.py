"""Notes.app via JavaScript for Automation."""

from __future__ import annotations

import base64
import html
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..errors import MacAPIError
from ..runner import run_jxa


class NoteFolder(BaseModel):
    name: str
    account: str | None = None
    note_count: int | None = None


class NoteSummary(BaseModel):
    id: str = Field(..., description="Opaque id; pass it back as-is")
    title: str
    folder: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None


class Note(NoteSummary):
    body_html: str | None = None
    body_text: str | None = None


class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1)
    body: str | None = Field(None, description="Plain text; newlines are kept")
    body_html: str | None = Field(None, description="Raw HTML, used instead of body")
    folder: str | None = Field(None, description="Defaults to the default Notes folder")


class NoteUpdate(BaseModel):
    append: str | None = Field(None, description="Plain text to add at the end of the note")
    body_html: str | None = Field(None, description="Replace the whole note with this HTML")

    @model_validator(mode="after")
    def _one_change(self) -> NoteUpdate:
        if (self.append is None) == (self.body_html is None):
            raise ValueError("Send exactly one of 'append' or 'body_html'")
        return self


def encode_id(raw: str) -> str:
    """Note ids look like `x-coredata://<uuid>/ICNote/p123`; make them URL-safe."""
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_id(token: str) -> str:
    try:
        return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise MacAPIError(400, "Invalid note id")


def text_to_html(text: str) -> str:
    """Convert plain text into the `<div>` per line markup Notes uses."""
    lines = text.splitlines() or [""]
    return "".join(f"<div>{html.escape(line) if line else '<br>'}</div>" for line in lines)


_PRELUDE = r"""
const app = Application('Notes');
function findFolder(name) {
  const folder = app.folders.byName(name);
  if (!folder.exists()) notFound('Notes folder not found: ' + name);
  return folder;
}
function findNote(id) {
  const note = app.notes.byId(id);
  if (!note.exists()) notFound('Note not found');
  return note;
}
function serializeNote(n, withBody) {
  let folder = null;
  try { folder = n.container().name(); } catch (e) {}
  const note = {
    id: n.id(), title: n.name(), folder: folder,
    created_at: iso(n.creationDate()), modified_at: iso(n.modificationDate()),
  };
  if (withBody) {
    note.body_html = n.body();
    try { note.body_text = n.plaintext(); } catch (e) { note.body_text = null; }
  }
  return note;
}
"""

_LIST_FOLDERS = r"""
const result = [];
app.accounts().forEach((account) => {
  const accountName = account.name();
  account.folders().forEach((folder) => {
    result.push({ name: folder.name(), account: accountName, note_count: folder.notes.id().length });
  });
});
return result;
"""

_LIST_NOTES = r"""
// Walk folders recursively so notes in sub-folders are included, skipping duplicates.
const seenFolders = {}, seenNotes = {}, result = [];
function visit(folder) {
  const folderId = folder.id();
  if (seenFolders[folderId]) return;
  seenFolders[folderId] = true;
  let spec = folder.notes;
  if (args.query) {
    spec = spec.whose({ _or: [{ name: { _contains: args.query } }, { body: { _contains: args.query } }] });
  }
  const ids = spec.id();
  if (ids.length) {
    const names = spec.name(), created = spec.creationDate(), modified = spec.modificationDate();
    const folderName = folder.name();
    ids.forEach((id, i) => {
      if (seenNotes[id]) return;
      seenNotes[id] = true;
      result.push({
        id: id, title: names[i], folder: folderName,
        created_at: iso(created[i]), modified_at: iso(modified[i]),
      });
    });
  }
  folder.folders().forEach(visit);
}
(args.folder ? [findFolder(args.folder)] : app.folders()).forEach(visit);
return result;
"""

_GET_NOTE = r"""
return serializeNote(findNote(args.id), true);
"""

_CREATE_NOTE = r"""
let folder;
if (args.folder) {
  folder = findFolder(args.folder);
} else {
  try { folder = app.defaultAccount().defaultFolder(); } catch (e) { folder = app.folders[0]; }
}
const note = app.Note({ body: args.body_html });
folder.notes.push(note);
return serializeNote(note, true);
"""

_UPDATE_NOTE = r"""
const note = findNote(args.id);
if (args.body_html != null) {
  note.body = args.body_html;
} else {
  note.body = note.body() + args.append_html;
}
return serializeNote(note, true);
"""

_DELETE_NOTE = r"""
app.delete(findNote(args.id));
return true;
"""

SCRIPTS = {
    "list_folders": _LIST_FOLDERS,
    "list_notes": _LIST_NOTES,
    "get_note": _GET_NOTE,
    "create_note": _CREATE_NOTE,
    "update_note": _UPDATE_NOTE,
    "delete_note": _DELETE_NOTE,
}


def _run(name: str, args: dict[str, Any] | None = None) -> Any:
    return run_jxa(_PRELUDE + SCRIPTS[name], args)


def _note(item: dict[str, Any]) -> Note:
    return Note(**{**item, "id": encode_id(item["id"])})


def list_folders() -> list[NoteFolder]:
    return [NoteFolder(**item) for item in _run("list_folders")]


def list_notes(folder: str | None = None, query: str | None = None, limit: int = 50) -> list[NoteSummary]:
    items = _run("list_notes", {"folder": folder, "query": query})
    notes = [NoteSummary(**{**item, "id": encode_id(item["id"])}) for item in items]
    notes.sort(key=lambda n: n.modified_at.timestamp() if n.modified_at else 0, reverse=True)
    return notes[:limit]


def get_note(note_id: str) -> Note:
    return _note(_run("get_note", {"id": decode_id(note_id)}))


def create_note(data: NoteCreate) -> Note:
    content = data.body_html if data.body_html is not None else text_to_html(data.body or "")
    body_html = f"<div><h1>{html.escape(data.title)}</h1></div>{content}"
    return _note(_run("create_note", {"folder": data.folder, "body_html": body_html}))


def update_note(note_id: str, data: NoteUpdate) -> Note:
    args: dict[str, Any] = {"id": decode_id(note_id), "body_html": data.body_html}
    if data.append is not None:
        args["append_html"] = text_to_html(data.append)
    return _note(_run("update_note", args))


def delete_note(note_id: str) -> None:
    _run("delete_note", {"id": decode_id(note_id)})
