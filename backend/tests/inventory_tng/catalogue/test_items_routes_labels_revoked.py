"""The item page's hints where only labels fix its unit or a package's size
(templates/catalogue/_revoke_labels.html): the reason first, until how many
printed labels are revoked, then a link to revoke them, each with their
count (Ali, 2026-11-25, inventory-tng-62qs)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.labels import labels
from inventory_tng.labels.paths import item_labels
from inventory_tng.models.catalogue import Category, Item

from helpers.items import NOW, new_item
from helpers.labels import labelled
from helpers.markup import read, words_on


def _revoke_links(page: str, words: str, item: Item) -> int:
    """How many links on the page say `words`, each to the item's labels."""
    found = read(page).all("a", words)
    assert {a.attrs["href"] for a in found} <= {item_labels(item.id)}
    return len(found)


def test_one_label_fixes_an_items_unit_until_revoked(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Counted each, the unit converts to no other: nothing recalculated."""
    item = new_item(session, radios, "LiteBeam")
    (label,) = labelled(session, item, 0)
    page = admin_client.get(f"{ITEMS}/{item.id}").text
    assert (
        "each: fixed until its printed label is revoked. Revoke its label to change it."
    ) in words_on(page)
    assert _revoke_links(page, "Revoke its label", item) == 1
    assert labels.revoke(session, label.id, NOW)
    page = admin_client.get(f"{ITEMS}/{item.id}").text
    assert "until its printed label" not in words_on(page)
    assert _revoke_links(page, "Revoke its label", item) == 0


def test_two_labels_fix_a_lengths_unit_until_both_are_revoked(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """In m, another length recalculates; revoking both changes it as it is.
    The spool's Holds counts only its own label, which fixes its size."""
    cable = new_item(session, radios, "Cat6 cable", unit="m")
    labelled(session, cable, 1)
    page = admin_client.get(f"{ITEMS}/{cable.id}").text
    words = words_on(page)
    assert (
        "only another length until both its printed labels are revoked: its "
        "recorded amounts are recalculated. Revoke its 2 labels to change it "
        "without recalculating its amounts."
    ) in words
    assert (
        "Holds 305 m: fixed until its printed label is revoked. "
        "Revoke its label to change it."
    ) in words
    assert _revoke_links(page, "Revoke its 2 labels", cable) == 1
    assert _revoke_links(page, "Revoke its label", cable) == 1


def test_several_labels_are_all_counted_until_all_are_revoked(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Its own and its packages' labels, counted once on the page; none of
    the hints once all are revoked."""
    cable = new_item(session, radios, "Cat6 cable", unit="m")
    made = labelled(session, cable, 4)
    page = admin_client.get(f"{ITEMS}/{cable.id}").text
    assert (
        "only another length until all 5 of its printed labels are revoked"
    ) in words_on(page)
    assert _revoke_links(page, "Revoke its 5 labels", cable) == 1
    assert _revoke_links(page, "Revoke its label", cable) == 4
    for label in made:
        assert labels.revoke(session, label.id, NOW)
    page = admin_client.get(f"{ITEMS}/{cable.id}").text
    assert "printed label" not in words_on(page)
    assert read(page).all("a", href=item_labels(cable.id)) == []


def test_labels_revoked_between_the_pages_reads_say_no_count(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revoked after the unit and sizes were found fixed, before they were
    counted: the unit and the spool's size are free, with no "0 labels"."""
    cable = new_item(session, radios, "Cat6 cable", unit="m")
    labelled(session, cable, 1)
    monkeypatch.setattr(labels, "current_of", lambda *_: [])
    page = admin_client.get(f"{ITEMS}/{cable.id}").text
    assert "printed label" not in words_on(page)
    assert "0 labels" not in words_on(page)
    assert read(page).all("a", href=item_labels(cable.id)) == []
