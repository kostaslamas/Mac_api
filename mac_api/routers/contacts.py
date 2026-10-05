from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..errors import MacAPIError
from ..services.contacts import Contact, ContactsIndex

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _index(request: Request) -> ContactsIndex:
    return request.app.state.contacts


@router.get("", response_model=list[Contact], summary="Search contacts by name, phone number or email")
def search_contacts(
    request: Request,
    q: str | None = Query(None, description="Leave empty to list everyone"),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> list[Contact]:
    index = _index(request)
    contacts = index.search(q) if q else sorted(index.all(), key=lambda c: c.name.casefold())
    return contacts[offset : offset + limit]


@router.get("/lookup", response_model=Contact, summary="Find who a phone number or email belongs to")
def lookup_contact(request: Request, handle: str) -> Contact:
    index = _index(request)
    index.all()  # surfaces permission problems instead of a misleading 404
    contact = index.lookup(handle)
    if contact is None:
        raise MacAPIError(404, f"No contact found for {handle}")
    return contact
