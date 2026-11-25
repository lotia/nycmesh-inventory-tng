"""The note beside the unit after an administrator answers No to "Were
the recorded amounts really in m?": it says the unit is unchanged, what a
change would do with one of the item's own amounts, then links the way to
put right a unit wrong from the start (Ali, 2026-11-25,
inventory-tng-aknv). Converting itself is
test_items_routes_unit_conversion.py's."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, object_session

from inventory_tng.catalogue import unit_changes
from inventory_tng.catalogue.paths import ITEMS, unit_recount
from inventory_tng.labels import labels
from inventory_tng.labels.paths import item_labels
from inventory_tng.models.catalogue import Item, ItemPackage, VendorOffer

from helpers.items import unit_answered
from helpers.markup import problem, read
from helpers.rows import add
from helpers.stock import Place, converted_reel, received, retire, write


def _declined(client: TestClient, item: Item, unit: str) -> str:
    """The page answering No to changing the item to `unit`, its rows
    read again as stored (a package's size typed as 305 reads 305.000)."""
    session = object_session(item)
    assert session is not None
    session.expire_all()
    page = client.post(f"{ITEMS}/{item.id}", data=unit_answered(item, "no", unit=unit))
    assert page.status_code == 400
    return page.text


def _labelled(session: Session, item: Item, count: int) -> None:
    """`count` labels printed: the item's own, then one for each of as
    many new packages as that leaves, as an item has one current label."""
    assert labels.make_for_item(session, item.id) is not None
    for n in range(count - 1):
        package = add(
            session, ItemPackage(item_id=item.id, name=f"Spool {n}", size=Decimal(305))
        )
        assert labels.make_for_item(session, item.id, package.id) is not None


def _offer(item: Item, units_per_order: Decimal) -> None:
    """A vendor offer of `units_per_order` in the item's unit."""
    session = object_session(item)
    assert session is not None
    add(
        session,
        VendorOffer(
            item_id=item.id,
            url="https://example.org",
            units_per_order=units_per_order,
            unit=item.unit,
        ),
    )


def _revoke_links(page: str, count: int) -> list[str]:
    """The words of each link on the page to revoke all `count` of the
    item's labels, in any case: the package hints' links revoke one."""
    return [
        a.text
        for a in read(page).all("a")
        if a.text.lower() == f"revoke its {count} labels"
    ]


def _moved(session: Session, unit: str, held: str, taken: str) -> Place:
    """A Place whose cable in `unit` was received, `held` of it, into the
    store, and `taken` of it moved on to the van."""
    place = Place(session, cable_unit=unit)
    cable = place.cable
    received(session, place, [{"item_id": cable.id, "quantity": held, "unit": unit}])
    write(
        session, place, lines=[{"item_id": cable.id, "quantity": taken, "unit": unit}]
    )
    return place


@pytest.mark.parametrize(
    ("unit", "held", "into", "becomes"),
    [("m", "300", "ft", "984.252"), ("ft", "990", "m", "301.752")],
)
def test_stock_moved_gives_a_place_and_links_to_the_recount(
    admin_client: TestClient,
    session: Session,
    unit: str,
    held: str,
    into: str,
    becomes: str,
) -> None:
    """The example is the first place holding any, in the stock pages'
    order: the store before the van. Its labels are not spoken of, as the
    recount revokes none (Ali, 2026-11-25, inventory-tng-8ema)."""
    place = _moved(session, unit, str(Decimal(held) + 5), "5")
    cable = place.cable
    _labelled(session, cable, 2)
    page = _declined(admin_client, cable, into)
    assert problem(page, "unit") == (
        f"The unit is still {unit}. Changing it to {into} recalculates every "
        f"recorded amount from {unit}: the {held} {unit} at {place.store.name} "
        f"would become {becomes} {into}. If the {held} was really {into}, "
        f"recount it in {into}: its records are set to nothing, its unit "
        f"becomes {into}, and you count each place in {into}."
    )
    note = read(page).one("span", id="unit-problem")
    note.one("a", f"recount it in {into}", href=unit_recount(cable.id, into))
    assert note.all("a", href=item_labels(cable.id)) == []


def test_stock_moved_none_left_leaves_the_example_out(
    admin_client: TestClient, session: Session
) -> None:
    """All of it installed: no place holds any to give as the example."""
    place = Place(session)
    cable = place.cable
    lines: list[dict[str, object]] = [
        {"item_id": cable.id, "quantity": "305", "unit": "m"}
    ]
    received(session, place, lines)
    write(session, place, kind="install", to_location_id=None, lines=lines)
    page = _declined(admin_client, cable, "ft")
    assert problem(page, "unit") == (
        "The unit is still m. Changing it to ft recalculates every recorded "
        "amount from m. If they were really in ft, recount it in ft: its "
        "records are set to nothing, its unit becomes ft, and you count each "
        "place in ft."
    )
    note = read(page).one("span", id="unit-problem")
    note.one("a", "recount it in ft", href=unit_recount(cable.id, "ft"))


def test_stock_moved_gives_a_place_holding_some_not_one_short(
    admin_client: TestClient, session: Session
) -> None:
    """5 m moved from a store that held none leaves it short (-5), and
    first in the stock pages' order: the van, holding the 5, is the
    example."""
    place = Place(session)
    cable = place.cable
    write(session, place, lines=[{"item_id": cable.id, "quantity": "5", "unit": "m"}])
    page = _declined(admin_client, cable, "ft")
    assert problem(page, "unit").startswith(
        "The unit is still m. Changing it to ft recalculates every recorded "
        f"amount from m: the 5 m at {place.van.name} would become 16.404 ft. "
        "If the 5 was really ft,"
    )


def test_no_to_a_change_not_between_lengths_asks_afresh(
    admin_client: TestClient, session: Session
) -> None:
    """No sent about a change to each, which no question asks, as from a
    page drawn before the item changed into each: nothing to recalculate,
    so no note, and no server error."""
    place = _moved(session, "m", "300", "5")
    cable = place.cable
    answered = unit_answered(cable, "no", unit="each")
    page = admin_client.post(f"{ITEMS}/{cable.id}", data=answered)
    assert page.status_code == 200
    assert read(page.text).all("span", id="unit-problem") == []
    session.refresh(cable)
    assert cable.unit == "m"


def test_labels_revoked_since_the_unit_was_read_asks_afresh(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its last label revoked between reading why its unit is fixed and
    counting its labels, as a request at once can: no note of 0 labels."""
    cable = Place(session).cable
    monkeypatch.setattr(unit_changes, "unit_fixed", lambda *_: "labelled")
    answered = unit_answered(cable, "no", unit="ft")
    page = admin_client.post(f"{ITEMS}/{cable.id}", data=answered)
    assert page.status_code == 200
    assert read(page.text).all("span", id="unit-problem") == []


@pytest.mark.parametrize(
    ("stored", "example"),
    [
        ("minimum", "its Reorder at of 100 would become 328.084 ft"),
        ("on_order", "the 50 on order would become 164.042 ft"),
        ("offer", "the vendor offer's 305 would become 1000.656 ft"),
    ],
)
def test_labels_only_give_a_stored_amount_and_link_to_revoke_the_label(
    admin_client: TestClient, session: Session, stored: str, example: str
) -> None:
    place = Place(session)
    cable = place.cable
    if stored == "offer":
        _offer(cable, Decimal(305))
    else:
        setattr(cable, stored, Decimal(100 if stored == "minimum" else 50))
    _labelled(session, cable, 1)
    page = _declined(admin_client, cable, "ft")
    assert problem(page, "unit") == (
        "The unit is still m. Until its printed label is revoked, changing it "
        f"recalculates its amounts from m: {example}. If they were really in "
        "ft, revoke its label, then change its unit to ft; the amounts stay "
        "as they are."
    )
    note = read(page).one("span", id="unit-problem")
    note.one("a", "revoke its label", href=item_labels(cable.id))


def test_two_labels_and_no_stored_amount_leave_the_example_out(
    admin_client: TestClient, session: Session
) -> None:
    """The revoke link once on the page, not in the unit's hint as well
    (inventory-tng-62qs)."""
    cable = Place(session).cable
    _labelled(session, cable, 2)
    page = _declined(admin_client, cable, "ft")
    assert problem(page, "unit") == (
        "The unit is still m. Until both its printed labels are revoked, "
        "changing it recalculates its amounts from m. If they were really in "
        "ft, revoke its 2 labels, then change its unit to ft; the amounts "
        "stay as they are."
    )
    assert _revoke_links(page, 2) == ["revoke its 2 labels"]
    note = read(page).one("span", id="unit-problem")
    note.one("a", "revoke its 2 labels", href=item_labels(cable.id))


def test_more_labels_say_how_many_from_ft_to_m(
    admin_client: TestClient, session: Session
) -> None:
    cable = Place(session, cable_unit="ft").cable
    cable.minimum = Decimal(1000)
    _labelled(session, cable, 5)
    page = _declined(admin_client, cable, "m")
    assert problem(page, "unit") == (
        "The unit is still ft. Until all 5 of its printed labels are revoked, "
        "changing it recalculates its amounts from ft: its Reorder at of 1000 "
        "would become 304.8 m. If they were really in m, revoke its 5 labels, "
        "then change its unit to m; the amounts stay as they are."
    )
    assert _revoke_links(page, 5) == ["revoke its 5 labels"]


@pytest.mark.parametrize("reel_retired", [False, True])
def test_converted_with_packages_says_their_sizes_are_given_again(
    admin_client: TestClient, session: Session, reel_retired: bool
) -> None:
    """The bead's example: in ft, converted from m, its Reel in ft, a
    retired one too, as the recount asks its size again (Ali, 2026-11-25,
    inventory-tng-l26i)."""
    place, _, reel = converted_reel(session)
    cable = place.cable
    _offer(cable, Decimal("1000.656"))
    if reel_retired:
        retire(session, reel)
    page = _declined(admin_client, cable, "m")
    assert problem(page, "unit") == (
        "The unit is still ft. Changing it to m recalculates its amounts from "
        "ft: the vendor offer's 1000.656 would become 305 m. If they were "
        "really in m, recount it in m: you give its amounts and package sizes "
        "again in m, and its unit becomes m."
    )
    note = read(page).one("span", id="unit-problem")
    note.one("a", "recount it in m", href=unit_recount(cable.id, "m"))


def test_converted_with_no_stored_amount_or_package_in_its_unit(
    admin_client: TestClient, session: Session
) -> None:
    """From m to ft: its only package, the Spool, is in m already, not
    asked again; it stores no amount, so no example."""
    place, _, reel = converted_reel(session)
    cable = place.cable
    session.delete(reel)
    session.flush()
    page = _declined(admin_client, cable, "m")
    assert problem(page, "unit") == (
        "The unit is still ft. Changing it to m recalculates its amounts from "
        "ft. If they were really in m, recount it in m: you give its amounts "
        "again in m, and its unit becomes m."
    )


def test_a_retired_item_whose_stock_moved_ends_with_no_link(
    admin_client: TestClient, session: Session
) -> None:
    """Answered No from a page drawn before the item was retired: it may
    not be recounted, so its note ends where it is."""
    place = _moved(session, "m", "310", "5")
    answered = unit_answered(place.cable, "no", unit="ft")
    retire(session, place.cable)
    declined = admin_client.post(f"{ITEMS}/{place.cable.id}", data=answered)
    assert problem(declined.text, "unit") == (
        "The unit is still m. Changing it to ft recalculates every recorded "
        f"amount from m: the 305 m at {place.store.name} would become "
        "1000.656 ft."
    )
    assert read(declined.text).all("a", href=unit_recount(place.cable.id, "ft")) == []


def test_a_retired_labelled_item_ends_with_no_link(
    admin_client: TestClient, session: Session
) -> None:
    """Its labels are inactive, so no link offers to revoke them."""
    cable = Place(session).cable
    _labelled(session, cable, 1)
    answered = unit_answered(cable, "no", unit="ft")
    retire(session, cable)
    declined = admin_client.post(f"{ITEMS}/{cable.id}", data=answered)
    assert problem(declined.text, "unit") == (
        "The unit is still m. Until its printed label is revoked, changing it "
        "recalculates its amounts from m."
    )
    assert read(declined.text).all("a", href=item_labels(cable.id)) == []
