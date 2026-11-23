"""What a count of a store and its areas shows and saves at a place that
what came from a retired area emptied while it was counted then changed
at, before the form's moment (stock/count_arrivals.as_held,
inventory-tng-nlog): opened there, cut, used or moved on. The place's
own lines count what it held before the moves, in the forms it held
them in, never less than nothing; a count typed as they show it writes
nothing; and a move fixed is written in the forms it is held in."""

import uuid
from collections.abc import Callable
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.stock import LedgerTransaction, Movement, OpenPackage
from inventory_tng.stock import open_packages
from inventory_tng.stock.ledger import LOOSE, Entry, Form
from inventory_tng.stock.moments import at_place, expected_forms_at
from inventory_tng.stock.paths import COUNTS

from helpers.markup import read, text
from helpers.rows import add
from helpers.stock import Spools, count_form, retire
from helpers.store_areas import area, field


class Arrived:
    """A store of 305 m spools of cable, sealed and loose, with Shelf B and
    Shelf C, retired, in it. Shelf B's own stock first (`own`), then
    `spools` sealed spools and `metres` loose moved from the store to
    Shelf C before the count form is shown, and on to Shelf B while it is
    open; then what happens at Shelf B (`then`) before the form is
    refused and shown again with the moves. `tracked`, whether an opened
    spool is followed as an open package, else it is opened loose, as a
    box of connectors is."""

    def __init__(
        self,
        session: Session,
        client: TestClient,
        *,
        spools: int = 1,
        metres: int = 0,
        tracked: bool = True,
        own: Callable[[Arrived], object] = lambda _: None,
        then: Callable[[Arrived], object] = lambda _: None,
    ) -> None:
        self.client = client
        self.made = made = Spools(session, tracked=tracked)
        made.receive(uuid.uuid4(), spools=2)
        self.cable = made.cable.id
        self.store = made.store
        made.write(
            {"kind": "receipt", "to_location_id": self.store.id},
            Entry(self.cable, Decimal(40), LOOSE, LOOSE),
        )
        self.shelf, self.gone = (
            add(session, area(name, self.store)) for name in ("Shelf B", "Shelf C")
        )
        retire(session, self.gone)
        own(self)
        lines = [Entry(self.cable, Decimal(305 * spools), self.sealed, self.sealed)]
        if metres:
            lines.append(Entry(self.cable, Decimal(metres)))
        self.path = f"{COUNTS}/{self.store.id}"
        self.move(self.store.id, self.gone.id, *lines)
        shown = count_form(client.get(self.path))
        self.move(self.gone.id, self.shelf.id, *lines)
        then(self)
        refused = client.post(self.path, data=shown)
        assert refused.status_code == 409
        self.html = refused.text
        self.page = read(refused.text)
        self.form = count_form(refused)

    @property
    def sealed(self) -> Form:
        return self.made.sealed

    def move(self, here: int, there: int | None, *lines: Entry) -> None:
        header = {"kind": "move" if there else "install", "from_location_id": here}
        self.made.write(header | ({"to_location_id": there} if there else {}), *lines)

    def bring(self, spools: int = 0, metres: int = 0) -> None:
        """Shelf B's own: spools and metres from the store."""
        lines = []
        if spools:
            sealed = self.sealed
            lines.append(Entry(self.cable, Decimal(305 * spools), sealed, sealed))
        if metres:
            lines.append(Entry(self.cable, Decimal(metres)))
        self.move(self.store.id, self.shelf.id, *lines)

    def open(self) -> OpenPackage | None:
        """A sealed spool opened at Shelf B: the open package it made, or
        none, opened loose."""
        shelf = self.shelf.id
        made = None
        arrives = LOOSE
        if self.made.cable.track_opened:
            made = open_packages.make(
                self.made.session, uuid.uuid4(), self.made.spool.id, shelf
            )
            arrives = Form(open_package_id=made.id)
        self.made.write(
            {"kind": "open", "from_location_id": shelf, "to_location_id": shelf},
            Entry(self.cable, Decimal(305), self.sealed, arrives),
        )
        return made

    def cut(self, made: OpenPackage | None, metres: int) -> None:
        """`metres` installed from the open package, or loose, at Shelf B."""
        form = LOOSE if made is None else Form(open_package_id=made.id)
        self.move(self.shelf.id, None, Entry(self.cable, Decimal(metres), form))

    def field(self, name: str) -> str:
        return field(self.shelf, self.store, name)

    def shows(self, name: str) -> str | None:
        """What the form shows in Shelf B's field, None if it has none."""
        inputs = self.page.all("input", name=self.field(name))
        return inputs[0].attrs["value"] if inputs else None

    def opened_rows(self) -> list[str]:
        """Shelf B's rows of open packages, by field."""
        prefix = self.field("opened_")
        names = [each.attrs["name"] for each in self.page.all("input")]
        return [name for name in names if name.startswith(prefix)]

    def as_shown(self) -> dict[str, str]:
        """Shelf B's own lines typed as the form shows them, by field."""
        prefix = self.field("")
        return {
            name.removeprefix(prefix): each.attrs["value"]
            for each in self.page.all("input")
            if (name := each.attrs.get("name", "")).startswith(prefix)
            and not name.startswith(self.field("arrived_"))
            and name != self.field("items")
        }

    def save(self, typed: dict[str, str] | None = None) -> None:
        sent = self.form | {self.field(n): value for n, value in (typed or {}).items()}
        posted = self.client.post(self.path, data=sent, follow_redirects=False)
        assert posted.status_code == 303, text(posted)

    def at_shelf(self, session: Session) -> dict[tuple[int, Form], Decimal]:
        """What the records hold at Shelf B, by item and form."""
        forms = expected_forms_at(session, [self.shelf.id], 2**31 - 1, [self.cable])
        return forms.get(self.shelf.id, {})

    def counted(self, session: Session) -> list[Movement]:
        """The lines of the counts written at Shelf B."""
        return list(
            session.scalars(
                select(Movement)
                .join(LedgerTransaction)
                .where(LedgerTransaction.kind == "count", at_place(self.shelf.id))
            )
        )

    def arrived_box(self) -> str:
        return f"arrived_{self.gone.id}_sealed_{self.cable}_{self.made.spool.id}"


def opened_form(made: OpenPackage | None) -> Form:
    assert made is not None
    return Form(open_package_id=made.id)


def test_two_spools_arrived_one_opened(
    session: Session, admin_client: TestClient
) -> None:
    """Two spools to Shelf B, holding 2 m, one opened there: its line says
    the 2 m by its total, no spool row; saved so, nothing written."""
    opened: list[OpenPackage | None] = []
    moved = Arrived(
        session,
        admin_client,
        spools=2,
        own=lambda each: each.bring(metres=2),
        then=lambda each: opened.append(each.open()),
    )
    assert moved.shows(f"counted_{moved.cable}") == "2"
    assert moved.opened_rows() == []
    before = moved.at_shelf(session)
    moved.save({f"counted_{moved.cable}": "2"})
    assert moved.counted(session) == []
    assert moved.at_shelf(session) == before
    assert before[(moved.cable, opened_form(opened[0]))] == 305


def test_an_arrival_opened_and_moved_on_leaves_the_place_s_own_open_spool(
    session: Session, admin_client: TestClient
) -> None:
    """Shelf B's own spool opened there before the form was shown, 100 m
    cut from it; the spool that came opened and its open package moved on
    to the van: Shelf B's line shows its own open spool's 205 m, and
    saved so writes nothing."""
    own: list[OpenPackage | None] = []

    def opened_own(moved: Arrived) -> None:
        moved.bring(spools=1, metres=2)
        own.append(moved.open())
        moved.cut(own[0], 100)

    def moved_on(moved: Arrived) -> None:
        arrival = opened_form(moved.open())
        moved.move(
            moved.shelf.id,
            moved.made.van.id,
            Entry(moved.cable, Decimal(305), arrival, arrival),
        )

    moved = Arrived(session, admin_client, own=opened_own, then=moved_on)
    mine = opened_form(own[0]).open_package_id
    assert moved.shows(f"opened_{mine}") == "205"
    assert moved.shows(f"loose_{moved.cable}") == "2"
    moved.save({f"opened_{mine}": "205", f"loose_{moved.cable}": "2"})
    assert moved.counted(session) == []


def test_an_arrival_opened_and_cut_leaves_the_place_s_own_open_spool_whole(
    session: Session, admin_client: TestClient
) -> None:
    """Shelf B's own spool opened there before, untouched; the spool that
    came opened too, 50 m cut from it: Shelf B's own open spool shows its
    305 m, not what was cut from the other, and typed so writes nothing."""
    own: list[OpenPackage | None] = []

    def opened_own(moved: Arrived) -> None:
        moved.bring(spools=1)
        own.append(moved.open())

    def cut(moved: Arrived) -> None:
        moved.cut(moved.open(), 50)

    moved = Arrived(session, admin_client, own=opened_own, then=cut)
    mine = opened_form(own[0]).open_package_id
    assert moved.opened_rows() == [moved.field(f"opened_{mine}")]
    assert moved.shows(f"opened_{mine}") == "305"
    moved.save({f"opened_{mine}": "305", f"loose_{moved.cable}": "0"})
    assert moved.counted(session) == []


def test_loose_that_arrived_and_was_used_never_leaves_a_line_below_nothing(
    session: Session, admin_client: TestClient
) -> None:
    """A spool and 10 m loose to Shelf B, holding 2 m; the spool opened,
    8 m of loose installed: Shelf B holds 4 m loose and the spool. No
    line shows less than nothing, and the form saved as shown writes
    nothing."""

    def used(moved: Arrived) -> None:
        moved.open()
        moved.cut(None, 8)

    moved = Arrived(
        session,
        admin_client,
        metres=10,
        own=lambda each: each.bring(metres=2),
        then=used,
    )
    assert "less than nothing" not in moved.html
    before = moved.at_shelf(session)
    moved.save(moved.as_shown())
    assert moved.counted(session) == []
    assert moved.at_shelf(session) == before


def test_a_box_that_arrived_and_was_opened_loose_is_set_aside(
    session: Session, admin_client: TestClient
) -> None:
    """A spool not followed once opened, as a box of connectors is, to
    Shelf B, holding 2 m, and opened there loose: Shelf B's line says the
    2 m, and typed so writes nothing, no box lost."""
    moved = Arrived(
        session,
        admin_client,
        tracked=False,
        own=lambda each: each.bring(metres=2),
        then=lambda each: each.open(),
    )
    assert moved.shows(f"counted_{moved.cable}") == "2"
    moved.save({f"counted_{moved.cable}": "2"})
    assert moved.counted(session) == []
    assert moved.at_shelf(session) == {(moved.cable, LOOSE): Decimal(307)}


def test_a_fix_of_a_spool_opened_there_comes_off_its_open_package(
    session: Session, admin_client: TestClient
) -> None:
    """The spool that came opened at Shelf B, holding 2 m, and none said to
    have arrived: its open package is lost, no sealed spool goes below
    nothing."""
    opened: list[OpenPackage | None] = []
    moved = Arrived(
        session,
        admin_client,
        own=lambda each: each.bring(metres=2),
        then=lambda each: opened.append(each.open()),
    )
    moved.save({moved.arrived_box(): "0"})
    assert moved.at_shelf(session) == {(moved.cable, LOOSE): Decimal(2)}


def test_a_fix_of_a_spool_cut_there_never_leaves_its_open_package_below_nothing(
    session: Session, admin_client: TestClient
) -> None:
    """The spool that came opened at Shelf B, holding 2 m, and 50 m cut
    from it; none said to have arrived: its open package loses the 255 m
    left on it, not the 305 m moved, and the 2 m stay."""
    moved = Arrived(
        session,
        admin_client,
        own=lambda each: each.bring(metres=2),
        then=lambda each: each.cut(each.open(), 50),
    )
    moved.save({moved.arrived_box(): "0"})
    assert moved.at_shelf(session) == {(moved.cable, LOOSE): Decimal(2)}


def test_a_fix_of_two_spools_one_opened_comes_off_the_sealed_one_first(
    session: Session, admin_client: TestClient
) -> None:
    """Two spools came, one opened at Shelf B; one said to have arrived:
    the sealed one is lost, the open one kept."""
    opened: list[OpenPackage | None] = []
    moved = Arrived(
        session,
        admin_client,
        spools=2,
        then=lambda each: opened.append(each.open()),
    )
    moved.save({moved.arrived_box(): "1"})
    assert moved.at_shelf(session) == {(moved.cable, opened_form(opened[0])): 305}


def test_a_recount_says_what_the_place_holds_of_a_spool_cut_there(
    session: Session, admin_client: TestClient
) -> None:
    """The spool that came opened at Shelf B, holding 2 m, and 50 m cut;
    more cable came from the store after the form was shown again, so
    Shelf B's line is asked again: the 2 m typed and the 255 m left of
    the spool are what it said."""
    moved = Arrived(
        session,
        admin_client,
        own=lambda each: each.bring(metres=2),
        then=lambda each: each.cut(each.open(), 50),
    )
    moved.bring(metres=3)
    typed = {moved.field(f"counted_{moved.cable}"): "2"}
    page = admin_client.post(moved.path, data=moved.form | typed)
    assert page.status_code == 409
    assert "3 in, 0 out; you typed 257" in text(page)
