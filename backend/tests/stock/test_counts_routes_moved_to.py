"""What a count of a store and its areas saves of the moves from a retired
area emptied while it was counted (stock/counts_routes.py,
stock/count_arrivals.py; Ali, 2026-11-22, inventory-tng-ygk5): a move left
alone writes nothing; How many arrived, fixed, records the difference as
lost or found where it went, nothing against the area it left; and the
place's own lines count what it held before the moves."""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, Movement
from inventory_tng.stock.ledger import LOOSE, Entry, Form
from inventory_tng.stock.moments import expected, expected_forms_at
from inventory_tng.stock.paths import COUNTS

from helpers.markup import problem, read, text
from helpers.rows import add
from helpers.stock import Spools, count_form, retire, write
from helpers.store_areas import Store, area, counts_at, field


class Moved:
    """Store, and in it Shelf C, retired, holding two radios; its count form
    shown, then the radios moved to `there` (Shelf B, empty, if none), so
    the form is refused and shown again with the move."""

    def __init__(
        self,
        session: Session,
        client: TestClient,
        there: str = "empty",
        *,
        only: bool = False,
    ) -> None:
        self.made = made = Store(session)
        self.item = made.place.item.id
        self.gone = add(session, area("Shelf C", made.store))
        retire(session, self.gone)
        self.send(session, made.store, self.gone, "2")
        self.path = made.path + (f"?item={self.item}" if only else "")
        shown = count_form(client.get(self.path))
        self.there: Location = getattr(made, there)
        self.send(session, self.gone, self.there, "2")
        # Filtered, Shelf C counted, so it is refused for it.
        typed = {field(self.gone, made.store, f"counted_{self.item}"): "2"}
        refused = client.post(self.path, data=shown | (typed if only else {}))
        assert refused.status_code == 409
        self.page = read(refused.text)
        self.form = count_form(refused)

    def send(self, session: Session, here: Location, there: Location, n: str) -> None:
        write(
            session,
            self.made.place,
            from_location_id=here.id,
            to_location_id=there.id,
            lines=[{"item_id": self.item, "quantity": n}],
        )

    def arrived(self, area_id: int | None = None) -> str:
        name = f"arrived_{area_id or self.gone.id}_loose_{self.item}"
        return field(self.there, self.made.store, name)

    def counted(self) -> str:
        return field(self.there, self.made.store, f"counted_{self.item}")


def saved(client: TestClient, path: str, form: dict[str, str]) -> None:
    assert client.post(path, data=form, follow_redirects=False).status_code == 303


def test_a_move_left_alone_writes_nothing(
    admin_client: TestClient, session: Session
) -> None:
    moved = Moved(session, admin_client)
    saved(admin_client, moved.path, moved.form)
    assert expected(session, moved.there.id) == {moved.item: Decimal(2)}
    # Shelf B counted as it was before the move, nothing found or lost there.
    lines = session.scalars(
        select(Movement)
        .join(LedgerTransaction)
        .where(LedgerTransaction.kind == "count")
        .where(LedgerTransaction.to_location_id == moved.there.id)
    ).all()
    assert lines == []
    assert counts_at(session, moved.gone) == []


def test_fewer_arrived_is_lost_where_they_went(
    admin_client: TestClient, session: Session
) -> None:
    moved = Moved(session, admin_client)
    saved(admin_client, moved.path, moved.form | {moved.arrived(): "1"})
    assert expected(session, moved.there.id) == {moved.item: Decimal(1)}
    (lost,) = counts_at(session, moved.there)[-1:]
    assert lost.from_location_id == moved.there.id
    assert counts_at(session, moved.gone) == []


def test_none_arrived_leaves_nothing_there(
    admin_client: TestClient, session: Session
) -> None:
    moved = Moved(session, admin_client)
    saved(admin_client, moved.path, moved.form | {moved.arrived(): "0"})
    assert expected(session, moved.there.id) == {}


def test_more_arrived_is_found_where_they_went(
    admin_client: TestClient, session: Session
) -> None:
    moved = Moved(session, admin_client)
    saved(admin_client, moved.path, moved.form | {moved.arrived(): "3"})
    assert expected(session, moved.there.id) == {moved.item: Decimal(3)}
    assert counts_at(session, moved.gone) == []


def test_the_place_s_own_line_counts_what_it_held_before(
    admin_client: TestClient, session: Session
) -> None:
    """Shelf A held three radios before two came from Shelf C: its line
    says three; counted two, and one of the move arrived, it holds three."""
    moved = Moved(session, admin_client, "shelf")
    typed = {moved.counted(): "2", moved.arrived(): "1"}
    saved(admin_client, moved.path, moved.form | typed)
    assert expected(session, moved.there.id) == {moved.item: Decimal(3)}


def test_the_place_s_own_line_as_shown_and_the_move_left_alone_keep_it(
    admin_client: TestClient, session: Session
) -> None:
    moved = Moved(session, admin_client, "shelf")
    line = moved.page.one("input", name=moved.counted()).attrs["value"]
    assert line == "3"
    saved(admin_client, moved.path, moved.form | {moved.counted(): line})
    assert expected(session, moved.there.id) == {moved.item: Decimal(5)}


def test_moves_from_two_areas_are_fixed_each_on_its_own(
    admin_client: TestClient, session: Session
) -> None:
    """Two radios to Shelf A from Shelf C and one from Shelf D: Shelf A's
    three counted, two from Shelf C arrived as moved, none from Shelf D."""
    made = Store(session)
    item = made.place.item.id
    gone = [add(session, area(name, made.store)) for name in ("Shelf C", "Shelf D")]
    retire(session, *gone)
    lines = [{"item_id": item, "quantity": "2"}]
    for each in gone:
        write(
            session,
            made.place,
            from_location_id=made.store.id,
            to_location_id=each.id,
            lines=lines,
        )
    shown = count_form(admin_client.get(made.path))
    for each in gone:
        write(
            session,
            made.place,
            from_location_id=each.id,
            to_location_id=made.shelf.id,
            lines=lines,
        )
    form = count_form(admin_client.post(made.path, data=shown))
    fix = {field(made.shelf, made.store, f"arrived_{gone[1].id}_loose_{item}"): "0"}
    saved(admin_client, made.path, form | fix)
    assert expected(session, made.shelf.id) == {item: Decimal(5)}


def test_a_fix_alone_saves_a_filtered_count(
    admin_client: TestClient, session: Session
) -> None:
    """A count of radios only, nothing typed but the fix: saved."""
    moved = Moved(session, admin_client, only=True)
    saved(admin_client, moved.path, moved.form | {moved.arrived(): "1"})
    assert expected(session, moved.there.id) == {moved.item: Decimal(1)}


def test_a_fix_sent_twice_is_written_once(
    admin_client: TestClient, session: Session
) -> None:
    moved = Moved(session, admin_client)
    form = moved.form | {moved.arrived(): "1"}
    saved(admin_client, moved.path, form)
    saved(admin_client, moved.path, form)
    assert expected(session, moved.there.id) == {moved.item: Decimal(1)}


def test_a_fix_that_is_no_number_is_said_beside_its_box(
    admin_client: TestClient, session: Session
) -> None:
    moved = Moved(session, admin_client)
    page = admin_client.post(moved.path, data=moved.form | {moved.arrived(): "1.5"})
    assert page.status_code == 400
    (details,) = [
        each
        for each in read(page.text).all("details")
        if each.all("input", name=moved.arrived())
    ]
    assert "open" in details.attrs
    assert "whole" in text(page).lower()
    assert expected(session, moved.there.id) == {moved.item: Decimal(2)}


def test_a_box_for_no_such_move_is_left_alone(
    admin_client: TestClient, session: Session
) -> None:
    """A hand-made box of a move there was not, or of no item at all."""
    moved = Moved(session, admin_client)
    store = moved.made.store
    made_up = {
        field(
            moved.there, store, f"arrived_{moved.made.shelf.id}_loose_{moved.item}"
        ): "9",
        field(moved.there, store, "arrived_x_1"): "9",
    }
    saved(admin_client, moved.path, moved.form | made_up)
    assert expected(session, moved.there.id) == {moved.item: Decimal(2)}
    nothing = {field(moved.there, store, f"arrived_{moved.gone.id}_loose_999999"): "1"}
    page = admin_client.post(moved.path, data=moved.form | nothing)
    assert page.status_code == 400


class SpoolsMoved:
    """The store's two sealed spools of cable and 10 m loose, and Shelf B
    and Shelf C in it, C retired; `spools` sealed and `metres` loose moved
    to Shelf C, each form a move of its own, before the count form is
    shown, then on to Shelf B while it is open, so the form is refused and
    shown again with the moves."""

    def __init__(
        self, session: Session, client: TestClient, spools: int, metres: int = 0
    ) -> None:
        self.spools = made = Spools(session)
        store, sealed = made.store, made.sealed
        self.cable = cable = made.cable.id
        made.write(
            {"kind": "receipt", "to_location_id": store.id},
            Entry(cable, Decimal(10), LOOSE, LOOSE),
        )
        self.shelf, self.gone = (
            add(session, area(name, store)) for name in ("Shelf B", "Shelf C")
        )
        retire(session, self.gone)
        lines = [Entry(cable, Decimal(305 * spools), sealed, sealed)] + (
            [Entry(cable, Decimal(metres), LOOSE, LOOSE)] if metres else []
        )
        self.path = f"{COUNTS}/{store.id}"
        self._move(store, self.gone, lines)
        shown = count_form(client.get(self.path))
        self._move(self.gone, self.shelf, lines)
        refused = client.post(self.path, data=shown)
        assert refused.status_code == 409
        self.page = read(refused.text)
        self.form = count_form(refused)

    def _move(self, here: Location, there: Location, lines: list[Entry]) -> None:
        moving = {"kind": "move", "from_location_id": here.id}
        for line in lines:
            self.spools.write(moving | {"to_location_id": there.id}, line)

    def box(self, form: str) -> str:
        """The count form's name for how much cable arrived at Shelf B from
        Shelf C in a form, by the count form's own name of that form."""
        return f"at_{self.shelf.id}_arrived_{self.gone.id}_{form}"

    @property
    def sealed(self) -> str:
        return self.box(f"sealed_{self.cable}_{self.spools.spool.id}")

    @property
    def loose(self) -> str:
        return self.box(f"loose_{self.cable}")

    def at_shelf(self, session: Session) -> dict[tuple[int, Form], Decimal]:
        """What the records hold at Shelf B, by item and form."""
        forms = expected_forms_at(session, [self.shelf.id], 2**31 - 1, [self.cable])
        return forms.get(self.shelf.id, {})


def test_a_sealed_arrival_is_fixed_in_whole_packages(
    session: Session, admin_client: TestClient
) -> None:
    """Two spools moved sealed: How many arrived counts spools, labelled as
    the count form's sealed lines are; one arrived, so one is lost sealed
    and no loose cable goes below nothing (coordinator, 2026-11-22)."""
    moved = SpoolsMoved(session, admin_client, spools=2)
    box = moved.page.one("input", name=moved.sealed)
    assert (box.attrs["value"], box.attrs["inputmode"]) == ("2", "numeric")
    assert (box.attrs["data-per"], box.attrs["data-unset"]) == ("305", "2")
    label = box.parent
    assert label is not None
    assert "Spool, sealed: how many" in label.get_text()
    assert moved.page.all("input", name=moved.loose) == []
    saved(admin_client, moved.path, moved.form | {moved.sealed: "1"})
    assert moved.at_shelf(session) == {(moved.cable, moved.spools.sealed): 305}
    (lost,) = session.scalars(
        select(Movement)
        .join(LedgerTransaction)
        .where(LedgerTransaction.from_location_id == moved.shelf.id)
        .where(LedgerTransaction.kind == "count")
    ).all()
    assert (lost.from_package_id, lost.quantity) == (moved.spools.spool.id, 305)


def test_a_mixed_arrival_is_fixed_in_each_form(
    session: Session, admin_client: TestClient
) -> None:
    """A spool and 10 m loose moved: a box for each, as the count form's
    lines split them, each filled with what arrived in it. No spool and
    4 m arrived: the spool is lost sealed, 6 m lost loose."""
    moved = SpoolsMoved(session, admin_client, spools=1, metres=10)
    sealed = moved.page.one("input", name=moved.sealed)
    loose = moved.page.one("input", name=moved.loose)
    assert (sealed.attrs["value"], loose.attrs["value"]) == ("1", "10")
    assert "data-per" not in loose.attrs
    assert loose.attrs["inputmode"] == "decimal"
    (details,) = [
        each
        for each in moved.page.all("details")
        if each.all("input", name=moved.loose)
    ]
    assert "How many arrived?" in details.text
    assert "Spool, sealed: how many" in details.text
    assert "Loose" in details.text
    assert "open" not in details.attrs
    typed = {moved.sealed: "0", moved.loose: "4"}
    saved(admin_client, moved.path, moved.form | typed)
    assert moved.at_shelf(session) == {(moved.cable, LOOSE): 4}


def test_a_package_count_that_is_not_whole_is_said_beside_its_box(
    session: Session, admin_client: TestClient
) -> None:
    """1.5 spools: refused, the box shown open with what was typed."""
    moved = SpoolsMoved(session, admin_client, spools=2)
    page = admin_client.post(moved.path, data=moved.form | {moved.sealed: "1.5"})
    assert page.status_code == 400
    shown = read(page.text)
    (details,) = [
        each for each in shown.all("details") if each.all("input", name=moved.sealed)
    ]
    assert "open" in details.attrs
    assert details.one("input", name=moved.sealed).attrs["value"] == "1.5"
    assert problem(shown, moved.sealed) == "A whole number of packages."
    assert moved.at_shelf(session) == {(moved.cable, moved.spools.sealed): 610}


def test_a_recount_says_what_was_typed_with_what_arrived(
    admin_client: TestClient, session: Session
) -> None:
    """Shelf A held three radios before two came from Shelf C; one more
    came from the store after the form was shown again, so Shelf A's line
    is withheld and asked again. Three typed for what it held before, and
    one of the move arrived: the recount says four were typed, as the
    records now hold the move and its fix."""
    moved = Moved(session, admin_client, "shelf")
    moved.made.move(session, moved.made.store, moved.there)
    typed = {moved.counted(): "3", moved.arrived(): "1"}
    page = admin_client.post(moved.path, data=moved.form | typed)
    assert page.status_code == 409
    assert "1 in, 0 out; you typed 4" in text(page)
    assert expected(session, moved.there.id) == {moved.item: Decimal(5)}
    # Refused for a bad number, it says so still (inventory-tng-up4h).
    refused = admin_client.post(
        moved.path, data=count_form(page) | {moved.counted(): "x"}
    )
    assert refused.status_code == 400
    assert "1 in, 0 out; you typed 4" in text(refused)


def test_a_place_counted_by_form_counts_its_own_spools_apart(
    session: Session, admin_client: TestClient
) -> None:
    """The store held two sealed spools and 10 m loose; one spool went to
    Shelf C before the form was shown, and back while it was counted. The
    store's own lines say one spool, sealed boxes adding whole spools to
    the running total, and saved as shown it holds both and the 10 m."""
    spools = Spools(session)
    store, cable, sealed = spools.store, spools.cable.id, spools.sealed
    spools.write(
        {"kind": "receipt", "to_location_id": store.id},
        Entry(cable, Decimal(10), LOOSE, LOOSE),
    )
    gone = add(session, area("Shelf C", store))
    retire(session, gone)
    spool = Entry(cable, Decimal(305), sealed, sealed)
    spools.write(
        {"kind": "move", "from_location_id": store.id, "to_location_id": gone.id},
        spool,
    )
    path = f"{COUNTS}/{store.id}"
    shown = count_form(admin_client.get(path))
    spools.write(
        {"kind": "move", "from_location_id": gone.id, "to_location_id": store.id},
        spool,
    )
    refused = admin_client.post(path, data=shown)
    assert refused.status_code == 409
    page = read(refused.text)
    box = page.one("input", name=f"sealed_{cable}_{spools.spool.id}").attrs
    assert (box["value"], box["data-per"]) == ("1", "305")
    assert "data-per" not in page.one("input", name=f"loose_{cable}").attrs
    saved(admin_client, path, count_form(refused))
    assert expected_forms_at(session, [store.id], 2**31 - 1, [cable]) == {
        store.id: {(cable, sealed): Decimal(610), (cable, LOOSE): Decimal(10)}
    }
