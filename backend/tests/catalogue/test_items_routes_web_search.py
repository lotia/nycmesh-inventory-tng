"""The Search the web link beside an item's manufacturer and part number
(catalogue/_web_search.html, inventory-tng-8i4.35): a DuckDuckGo search for
them, opened in a new tab in the administrator's own browser, hidden while
both are blank."""

from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category

from helpers.items import item_form, new_item
from helpers.markup import Element, read
from helpers.rows import shown


def search(page: str) -> Element:
    """The page's search link's paragraph."""
    return read(page).one("p", data_web_search="")


def searched_for(found: Element) -> str:
    """What the link searches DuckDuckGo for."""
    address = urlsplit(found.one("a").attrs["href"])
    assert (address.scheme, address.netloc, address.path) == (
        "https",
        "duckduckgo.com",
        "/",
    )
    return parse_qs(address.query).get("q", [""])[0]


def test_an_items_page_searches_for_its_manufacturer_and_part_number(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    item.manufacturer = "Ubiquiti"
    item.part_number = "LBE-5AC/Gen2 & co?"
    session.flush()
    found = search(admin_client.get(f"{ITEMS}/{item.id}").text)
    assert "hidden" not in found.attrs
    link = found.one("a")
    assert link.text == "Search the web"
    assert link.attrs["target"] == "_blank"
    assert link.attrs["rel"].split() == ["noopener", "noreferrer"]
    # The hint, which says it opens a new tab, is read with the link.
    hint = found.one("span", id=link.attrs["aria-describedby"])
    assert "in a new tab" in hint.text
    # Encoded as a form's query is, nothing left as it was typed.
    assert link.attrs["href"].endswith("?q=Ubiquiti+LBE-5AC%2FGen2+%26+co%3F")
    assert searched_for(found) == "Ubiquiti LBE-5AC/Gen2 & co?"


def test_either_field_alone_is_searched_for(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    item.part_number = " RB5009*1~2 "
    session.flush()
    found = search(admin_client.get(f"{ITEMS}/{item.id}").text)
    assert "hidden" not in found.attrs
    assert searched_for(found) == "RB5009*1~2"


def test_it_is_hidden_while_both_are_blank(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    assert "hidden" in search(admin_client.get(f"{ITEMS}/{item.id}").text).attrs
    assert "hidden" in search(admin_client.get(ITEMS).text).attrs


def test_a_form_sent_back_searches_for_what_was_typed(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Refused, the new item's form and an item's own keep what was typed,
    and so does the link."""
    typed = {"manufacturer": "MikroTik", "part_number": "RB5009UG+S+IN"}
    refused = admin_client.post(ITEMS, data={"name": "", "unit": "each"} | typed)
    assert refused.status_code == 400
    found = search(refused.text)
    assert "hidden" not in found.attrs
    assert searched_for(found) == "MikroTik RB5009UG+S+IN"

    item = new_item(session, radios)
    item.manufacturer = "Ubiquiti"
    session.flush()
    refused = admin_client.post(
        f"{ITEMS}/{item.id}",
        data=item_form(radios, version=shown(item), name="") | typed,
    )
    assert refused.status_code == 400
    assert searched_for(search(refused.text)) == "MikroTik RB5009UG+S+IN"
