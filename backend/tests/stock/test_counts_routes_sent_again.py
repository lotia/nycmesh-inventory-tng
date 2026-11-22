"""A count form saved and sent again under the same key, as the browser's
Back sends it (stock/counts_routes.py, stock/store_counts.py): sent as it
was, it is answered as before; sent otherwise in anything, it is refused
whole, nothing is written, and Counts says it was saved already (Ali,
2026-11-22, inventory-tng-53im, inventory-tng-kro9)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.stock import LedgerTransaction, OpenPackage
from inventory_tng.stock.paths import COUNTS

from helpers.markup import read, text
from helpers.rows import add
from helpers.stock import Spools, count_form
from helpers.store_areas import Store, area, counts_at, found_on

SAVED_ALREADY = (
    "This count was saved already, so what you changed wasn't saved. To "
    "record it, start a new count."
)


class SentAgain:
    """Spools in a store with a shelf in it, and a drum of cable besides:
    the store's count form as shown, and how to send it."""

    def __init__(self, client: TestClient, session: Session) -> None:
        self.client, self.session = client, session
        self.spools = Spools(session)
        self.shelf = add(session, area("Shelf", self.spools.store))
        self.drum = add(
            session, ItemPackage(item_id=self.spools.cable.id, name="Drum", size=100)
        )
        self.show()

    def show(self, query: str = "") -> None:
        """The form shown, filtered by `query` if given."""
        self.path = f"{COUNTS}/{self.spools.store.id}{query}"
        self.shown = count_form(self.client.get(self.path))

    def send(self, typed: dict[str, str]) -> Response:
        return self.client.post(self.path, data=self.shown | typed)

    def written(self) -> tuple[int | None, list[int]]:
        """The ledger's latest transaction, and where the form's open
        package nobody recorded was made, if it was."""
        made = select(OpenPackage.location_id).where(
            OpenPackage.key == uuid.uuid5(uuid.UUID(self.shown["key"]), "found")
        )
        latest = select(func.max(LedgerTransaction.id))
        return self.session.scalar(latest), list(self.session.scalars(made))

    def refused(self, first: dict[str, str], again: dict[str, str]) -> None:
        """Saved with `first`, then sent again with `again`: refused, and
        nothing more written."""
        assert self.send(first).status_code == 303
        before = self.written()
        sent = self.send(again)
        assert sent.status_code == 303
        assert sent.headers["location"].startswith(
            f"{COUNTS}?notice=count_saved_already"
        )
        assert self.written() == before

    def as_before(self, first: dict[str, str], again: dict[str, str]) -> None:
        """Saved with `first`, then sent again with `again`: answered as
        the save was, and nothing more written."""
        saved = self.send(first)
        before = self.written()
        assert self.send(again).headers["location"] == saved.headers["location"]
        assert self.written() == before


@pytest.mark.parametrize("change", ["elsewhere", "another package", "none before"])
def test_a_count_saved_and_sent_again_finding_otherwise_writes_nothing_more(
    admin_client: TestClient, session: Session, change: str
) -> None:
    """Sent again with the open package nobody recorded found at another
    place, another package found, or one found where the first send found
    none: Counts says the count was saved already (inventory-tng-53im)."""
    made = SentAgain(admin_client, session)
    spool, store = made.spools.spool, made.spools.store
    first, again = {
        "elsewhere": (found_on(made.shelf, spool), found_on(store, spool)),
        "another package": (found_on(store, spool), found_on(store, made.drum)),
        "none before": ({}, found_on(made.shelf, spool)),
    }[change]
    made.refused(first, again)
    said = read(admin_client.get(f"{COUNTS}?notice=count_saved_already").text)
    assert said.one("p", role="status").text == SAVED_ALREADY


def test_a_count_saved_and_sent_again_with_other_figures_writes_nothing_more(
    admin_client: TestClient, session: Session
) -> None:
    made = SentAgain(admin_client, session)
    item = made.spools.place.item.id
    made.refused({f"counted_{item}": "1"}, {f"counted_{item}": "2"})


def test_a_count_saved_and_sent_again_without_the_package_found_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    made = SentAgain(admin_client, session)
    made.refused(found_on(made.shelf, made.spools.spool), {})


def test_a_filtered_count_saved_and_sent_again_finding_where_none_was_counted(
    admin_client: TestClient, session: Session
) -> None:
    """Filtered, the first send counts the store alone: sent again finding
    an open package on the shelf it left out, refused all the same."""
    made = SentAgain(admin_client, session)
    item = made.spools.place.item.id
    made.show(f"?item={item}")
    typed = {f"counted_{item}": "1"}
    made.refused(typed, typed | found_on(made.shelf, made.spools.spool))


def test_a_filtered_count_sent_again_counting_an_area_left_out_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """Filtered, the first send counts the store alone; sent again with the
    shelf typed at, the shelf is not counted (inventory-tng-kro9)."""
    made = SentAgain(admin_client, session)
    item = made.spools.place.item.id
    made.show(f"?item={item}")
    typed = {f"counted_{item}": "1"}
    made.refused(typed, typed | {f"at_{made.shelf.id}_counted_{item}": "2"})
    assert counts_at(session, made.shelf) == []


def test_a_count_sent_again_closing_an_open_package_leaves_it_open(
    admin_client: TestClient, session: Session
) -> None:
    """An open package ticked as used up only on the send again is not
    closed (inventory-tng-kro9)."""
    made = SentAgain(admin_client, session)
    opened = made.spools.opened()
    made.show()
    close = {f"opened_{opened.id}": "", f"state_{opened.id}": "used_up"}
    made.refused({}, close)
    session.refresh(opened)
    assert opened.closed_at is None


def test_a_count_saved_and_sent_again_unchanged_is_answered_as_before(
    admin_client: TestClient, session: Session
) -> None:
    made = SentAgain(admin_client, session)
    found = found_on(made.shelf, made.spools.spool)
    made.as_before(found, found)


def test_a_count_closing_an_open_package_sent_again_unchanged_is_as_before(
    admin_client: TestClient, session: Session
) -> None:
    """The open package it closed is closed already when it is sent again:
    as it said, so answered as before, not as closed meanwhile
    (inventory-tng-kro9)."""
    made = SentAgain(admin_client, session)
    opened = made.spools.opened()
    made.show()
    close = {f"opened_{opened.id}": "", f"state_{opened.id}": "used_up"}
    made.as_before(close, close)


def test_a_count_closing_an_open_package_sent_again_otherwise_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    made = SentAgain(admin_client, session)
    opened = made.spools.opened()
    made.show()
    item = made.spools.place.item.id
    close = {f"opened_{opened.id}": "", f"state_{opened.id}": "used_up"}
    made.refused(close | {f"counted_{item}": "1"}, close | {f"counted_{item}": "2"})


def test_a_count_sent_again_with_its_amounts_written_otherwise_is_unchanged(
    admin_client: TestClient, session: Session
) -> None:
    """5 sent again as 5.0, its lines in another order: the same count."""
    made = SentAgain(admin_client, session)
    item, cable = made.spools.place.item.id, made.spools.cable.id
    made.as_before(
        {f"counted_{item}": "5", f"at_{made.shelf.id}_counted_{cable}": "2"},
        {f"at_{made.shelf.id}_counted_{cable}": "2.0", f"counted_{item}": "5"},
    )


def test_a_count_of_one_place_sent_again_with_other_figures_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    path = f"{COUNTS}/{spools.van.id}"
    form = count_form(admin_client.get(path))
    item = spools.place.item.id
    first = admin_client.post(path, data=form | {f"counted_{item}": "1"})
    assert first.status_code == 303
    before = counts_at(session, spools.van)
    sent = admin_client.post(path, data=form | {f"counted_{item}": "2"})
    assert sent.headers["location"].startswith(f"{COUNTS}?notice=count_saved_already")
    assert counts_at(session, spools.van) == before


def test_a_count_held_back_in_part_and_sent_again_unchanged_is_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    """A radio moved from the shelf to the store as it was counted: the
    radios are asked for again, the rest saved. Sent again as it was, it
    is answered so again, not taken as changed, and nothing more is
    written."""
    made = Store(session)
    item, cable = made.place.item.id, made.place.cable.id
    shown = count_form(admin_client.get(made.path))
    made.move(session, made.shelf, made.store)
    typed = shown | {
        f"counted_{item}": "5",
        f"at_{made.bin.id}_counted_{cable}": "4",
    }
    first = admin_client.post(made.path, data=typed)
    assert first.status_code == 409
    assert "Saved 1 line. These moved while you counted" in text(first)
    before = [counts_at(session, place) for place in made.every_place]
    again = admin_client.post(made.path, data=typed)
    assert again.status_code == 409
    assert "Saved 1 line. These moved while you counted" in text(again)
    assert [counts_at(session, place) for place in made.every_place] == before
