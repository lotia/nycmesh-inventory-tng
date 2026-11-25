"""Every sum of the ledger reads each line in its item's unit: one in
another length converted (Ali, 2026-11-24, inventory-tng-y50b), and one in
a unit that does not convert refused, so units are never mixed unawares
(line_amounts; inventory-tng-2rlr)."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import exc, func, select, true
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.stock import Movement
from inventory_tng.stock import counts, moments, open_packages, queries
from inventory_tng.stock.balance import balance, by_form
from inventory_tng.stock.ledger import LOOSE, Entry, Form
from inventory_tng.stock.moved_out import moved_out

from helpers.rows import add
from helpers.sign_in import new_admin, new_admin_id
from helpers.stock import Place, Spools, held_by_form, received, write

# Each reader of the ledger's sums, of the place's store, after its cable
# came in and some left.
READERS: dict[str, Callable[[Session, Place], object]] = {
    "balance": lambda session, place: queries.item_total(session, place.cable.id),
    "as of a moment": lambda session, place: moments.expected_at(
        session, [place.store.id], queries.latest_transaction_id(session)
    ),
    "flows": lambda session, place: moments.flows(session, place.store.id, true()),
    "by form": lambda session, place: moments.form_flows(
        session, place.store.id, [true()], [place.cable.id]
    ),
    "moved out": lambda session, place: moved_out(session, [place.store.id], 0),
}
# Each reader of an open package's lines, of the store, after a spool of
# its cable was opened there and then made loose.
OPEN_READERS: dict[str, Callable[[Session, Spools, int], object]] = {
    "left on it": lambda session, _, open_id: moments.left_at(
        session, open_id, queries.latest_transaction_id(session)
    ),
    "openings": lambda session, spools, _: queries.openings_at(
        session,
        [spools.store.id],
        [spools.cable.id],
        0,
        queries.latest_transaction_id(session),
    ),
    "made loose": lambda session, spools, _: queries.loosened_at(
        session,
        [spools.store.id],
        [spools.cable.id],
        0,
        queries.latest_transaction_id(session),
    ),
}


def _moved(session: Session, place: Place) -> None:
    """50 m of cable into the store, then 10 m on to the van."""
    received(session, place, [_cable(place, "50")])
    write(session, place, lines=[_cable(place, "10")])


def _cable(place: Place, metres: str) -> dict[str, object]:
    return {"item_id": place.cable.id, "quantity": metres, "unit": "m"}


def test_lines_in_their_items_unit_are_summed(session: Session, place: Place) -> None:
    _moved(session, place)
    assert queries.item_total(session, place.cable.id) == Decimal(50)
    assert moments.expected(session, place.store.id) == {place.cable.id: Decimal(40)}


@pytest.mark.parametrize("read", READERS.values(), ids=READERS.keys())
def test_a_sum_across_units_that_do_not_convert_is_refused(
    session: Session, place: Place, read: Callable[[Session, Place], object]
) -> None:
    """The cable switched to each straight in the database, as a moved
    item's unit never changes between each and a length: its lines in m
    are never added up as each."""
    _moved(session, place)
    place.cable.unit = "each"
    session.flush()
    with pytest.raises(exc.DataError, match=r"ledger line \d+ in m summed in each"):
        read(session, place)


@pytest.mark.parametrize("read", OPEN_READERS.values(), ids=OPEN_READERS.keys())
def test_an_open_packages_lines_in_units_that_do_not_convert_are_refused(
    session: Session,
    spools: Spools,
    read: Callable[[Session, Spools, int], object],
) -> None:
    """As above, for the readers of what an open package held."""
    admin, _ = new_admin(session)
    open_id = spools.opened().id
    assert spools.unfollow(admin.id, datetime.now(UTC)) == 1
    spools.cable.unit = "each"
    session.flush()
    with pytest.raises(exc.DataError, match=r"ledger line \d+ in m summed in each"):
        read(session, spools, open_id)


def test_lines_in_another_length_are_read_converted(
    session: Session, place: Place
) -> None:
    """The cable's 50 m in and 10 m out read in ft once it is in ft, as a
    confirmed change makes it (catalogue/items.save): 50 / 0.3048 is
    164.04199..., kept to thousandths. The lines keep their unit."""
    _moved(session, place)
    place.cable.unit = "ft"
    session.flush()
    assert queries.item_total(session, place.cable.id) == Decimal("164.042")
    assert moments.expected(session, place.store.id) == {
        place.cable.id: Decimal("131.234")
    }
    (_, flow), *_ = moments.flows(session, place.store.id, true())[0].items()
    assert flow == (Decimal("164.042"), Decimal("32.808"))
    (out,) = moved_out(session, [place.store.id], 0)
    assert out.quantity == Decimal("32.808")
    units = session.scalars(
        select(Movement.unit).where(Movement.item_id == place.cable.id)
    )
    assert set(units) == {"m"}


def test_a_sum_converts_each_line_exactly_and_rounds_once(
    session: Session, place: Place
) -> None:
    """Three lines of 0.5 m are 4.921 ft (4.92125...), not three of 1.640
    ft, each rounded first: a sum does not drift by its lines."""
    for _ in range(3):
        received(session, place, [_cable(place, "0.5")])
    place.cable.unit = "ft"
    session.flush()
    assert queries.item_total(session, place.cable.id) == Decimal("4.921")


def test_sealed_packages_convert_by_package_and_leave_whole(
    session: Session, spools: Spools
) -> None:
    """Five spools of 305 m, received before the cable is in ft, are five of
    1000.656 ft, as the ledger takes them since: 5003.280 ft, not 5003.281
    (1525 m converted), so the five leaving in ft leave nothing over. The
    spool keeps its size in m."""
    spools.receive(uuid.uuid4(), spools=3)
    spools.cable.unit = "ft"
    session.flush()
    session.refresh(spools.spool)
    assert (spools.spool.size, spools.spool.unit) == (Decimal(305), "m")
    assert spools.spool.size_in_items_unit == Decimal("1000.656")
    sealed = (spools.cable.id, spools.sealed)
    assert held_by_form(session, spools.store.id, spools.cable.id) == {
        sealed: Decimal("5003.280")
    }
    spools.move(
        Entry(spools.cable.id, Decimal("5003.280"), spools.sealed, spools.sealed)
    )
    # Nothing over: none held, not a part of one.
    assert held_by_form(session, spools.store.id, spools.cable.id) == {}
    assert held_by_form(session, spools.van.id, spools.cable.id) == {
        sealed: Decimal("5003.280")
    }


def test_an_open_package_opened_before_reads_converted(
    session: Session, spools: Spools
) -> None:
    """A spool opened in m, then cut in ft: what is left on it is read in
    ft, its opening by the package, its cut as it was recorded."""
    open_id = spools.opened().id
    spools.cable.unit = "ft"
    session.flush()
    cut = Entry(spools.cable.id, Decimal("0.656"), Form(open_package_id=open_id), LOOSE)
    spools.move(cut)
    latest = queries.latest_transaction_id(session)
    assert moments.left_at(session, open_id, latest) == Decimal(1000)
    ((*_, opened),) = queries.openings_at(
        session, [spools.store.id], [spools.cable.id], 0, latest
    )
    assert opened == Decimal("1000.656")


def _opened_and_cut(spools: Spools, package: ItemPackage, how_many: int) -> None:
    """`how_many` of `package`, sealed at the store, each opened there and
    cut whole, in the cable's unit, to the van's loose stock."""
    whole = Decimal(package.size)
    sealed = Form(package_id=package.id)
    for _ in range(how_many):
        made = open_packages.make(
            spools.session, uuid.uuid4(), package.id, spools.store.id
        )
        opened = Form(open_package_id=made.id)
        spools.write(
            {
                "kind": "open",
                "from_location_id": spools.store.id,
                "to_location_id": spools.store.id,
            },
            Entry(spools.cable.id, whole, sealed, opened),
        )
        spools.move(Entry(spools.cable.id, whole, opened, LOOSE))


def _metres_to_feet(session: Session) -> tuple[Spools, str]:
    """The bead's example (inventory-tng-8s2c): six spools of 305 m opened
    at the store and cut whole to the van, then the cable in ft. Each
    opening gives its open spool 1000.656 ft, by its package; each cut
    takes 1000.65617 ft from it, exactly. The van holds 1830 m loose:
    6003.937 ft (6003.93701...)."""
    spools = Spools(session)
    spools.receive(uuid.uuid4(), spools=4)
    _opened_and_cut(spools, spools.spool, 6)
    spools.cable.unit = "ft"
    session.flush()
    return spools, "6003.937"


def _feet_to_metres(session: Session) -> tuple[Spools, str]:
    """The other way: six drums of 1001 ft, each 305.105 m as kept
    (305.1048), opened and cut whole, then the cable in m. The two spools
    of 305 ft it starts with leave sealed first. The van holds 6006 ft
    loose: 1830.629 m (1830.6288), and the two spools, 185.928 m."""
    spools = Spools(session, cable_unit="ft")
    spools.move(Entry(spools.cable.id, Decimal(610), spools.sealed, spools.sealed))
    drum = add(session, ItemPackage(item_id=spools.cable.id, name="Drum", size=1001))
    spools.write(
        {"kind": "receipt", "to_location_id": spools.store.id},
        Entry(spools.cable.id, Decimal(6006), LOOSE, Form(package_id=drum.id)),
    )
    _opened_and_cut(spools, drum, 6)
    spools.cable.unit = "m"
    session.flush()
    return spools, "2016.557"


CHANGES = {"m to ft": _metres_to_feet, "ft to m": _feet_to_metres}


@pytest.mark.parametrize("change", CHANGES.values(), ids=CHANGES.keys())
def test_a_place_holds_the_sum_of_its_forms_after_a_change(
    session: Session, change: Callable[[Session], tuple[Spools, str]]
) -> None:
    """Each open package nets a part of a thousandth after the change, as
    its opening converts by its package and its cuts exactly. Each form is
    rounded as kept, so each reads 0.000, and a place reads the sum of its
    forms: the store nothing, not less than nothing; the van all of it."""
    spools, at_van = change(session)
    store, van, cable = spools.store.id, spools.van.id, spools.cable.id
    latest = queries.latest_transaction_id(session)
    assert held_by_form(session, store, cable) == {}
    assert moments.expected(session, store) == {}
    assert moments.expected_at(session, [store], latest) == {}
    assert moments.expected(session, van) == {cable: Decimal(at_van)}
    assert moments.expected_at(session, [van], latest) == {
        van: {cable: Decimal(at_van)}
    }
    assert queries.item_total(session, cable) == Decimal(at_van)
    forms = (
        select(by_form.c.location_id, func.sum(by_form.c.quantity))
        .where(by_form.c.item_id == cable)
        .group_by(by_form.c.location_id)
    )
    totals = select(balance.c.location_id, balance.c.quantity).where(
        balance.c.item_id == cable
    )
    assert dict(session.execute(totals).all()) == dict(session.execute(forms).all())


@pytest.mark.parametrize("change", CHANGES.values(), ids=CHANGES.keys())
def test_a_count_of_nothing_where_nothing_is_held_writes_nothing(
    session: Session, change: Callable[[Session], tuple[Spools, str]]
) -> None:
    """A count of the store after the change, of none of the cable, finds
    the records right: it writes no part of a thousandth to make up the
    open packages' residue."""
    spools, _ = change(session)
    store, cable = spools.store.id, spools.cable.id
    done = counts.record_count(
        session,
        uuid.uuid4(),
        new_admin_id(session),
        store,
        [(cable, Decimal(0))],
        moments.moment(session, store),
    )
    assert not done.differed
