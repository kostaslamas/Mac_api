from __future__ import annotations

import pytest

from mac_api.errors import MacAPIError
from mac_api.services.contacts import ContactsIndex, clean_label, fold, phone_key


def test_helpers():
    assert clean_label("_$!<Mobile>!$_") == "Mobile"
    assert clean_label("Work phone") == "Work phone"
    assert phone_key("+30 691 234 5678") == phone_key("6912345678") == "912345678"
    assert fold("Κώστας") == fold("ΚΩΣΤΑΣ") == fold("κωστας")


def test_index_dedupes_and_skips_groups(addressbook_dir):
    contacts = ContactsIndex(addressbook_dir).all()
    assert sorted(c.name for c in contacts) == ["Maria Smith", "Pizza Place", "Κώστας Λάμπρου"]
    kostas = next(c for c in contacts if c.first_name == "Κώστας")
    assert kostas.phones[0].label == "Mobile"


def test_lookup_by_handle(addressbook_dir):
    index = ContactsIndex(addressbook_dir)
    assert index.name_for("+306912345678") == "Κώστας Λάμπρου"
    assert index.name_for("MARIA@example.com") == "Maria Smith"
    assert index.name_for("+15550109999") == "Maria Smith"
    assert index.name_for("+447700900123") is None
    assert index.name_for(None) is None


def test_search_endpoint(client):
    def names(**params):
        return [c["name"] for c in client.get("/contacts", params=params).json()]

    assert names(q="κωστας") == ["Κώστας Λάμπρου"]  # accent and case insensitive
    assert names(q="pizza") == ["Pizza Place"]
    assert names(q="+30 6912345678") == ["Κώστας Λάμπρου"]
    assert names(q="example.com") == ["Maria Smith"]
    assert names() == ["Maria Smith", "Pizza Place", "Κώστας Λάμπρου"]
    assert names(limit=1, offset=1) == ["Pizza Place"]


def test_lookup_endpoint(client):
    assert client.get("/contacts/lookup", params={"handle": "2105551234"}).json()["name"] == "Pizza Place"
    assert client.get("/contacts/lookup", params={"handle": "nobody@example.com"}).status_code == 404


def test_missing_addressbook(tmp_path):
    index = ContactsIndex(tmp_path / "missing")
    assert index.name_for("+306912345678") is None  # name lookups never fail
    with pytest.raises(MacAPIError) as excinfo:
        index.all()
    assert excinfo.value.status_code == 503
