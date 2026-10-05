from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from ..auth import require_write_access
from ..services import notes
from ..services.notes import Note, NoteCreate, NoteFolder, NoteSummary, NoteUpdate

router = APIRouter(prefix="/notes", tags=["notes"])
write = [Depends(require_write_access)]


@router.get("/folders", response_model=list[NoteFolder])
def get_folders() -> list[NoteFolder]:
    return notes.list_folders()


@router.get("", response_model=list[NoteSummary], summary="List notes, most recently edited first")
def get_notes(
    folder: str | None = None,
    q: str | None = Query(None, description="Text to look for in the title or body"),
    limit: int = Query(50, ge=1, le=1000),
) -> list[NoteSummary]:
    return notes.list_notes(folder, q, limit)


@router.get("/{note_id}", response_model=Note)
def get_note(note_id: str) -> Note:
    return notes.get_note(note_id)


@router.post("", response_model=Note, status_code=status.HTTP_201_CREATED, dependencies=write)
def create_note(data: NoteCreate) -> Note:
    return notes.create_note(data)


@router.patch("/{note_id}", response_model=Note, dependencies=write, summary="Append to or replace a note")
def update_note(note_id: str, data: NoteUpdate) -> Note:
    return notes.update_note(note_id, data)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=write)
def delete_note(note_id: str) -> Response:
    notes.delete_note(note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
