"""The note beside the unit after an administrator answers No to "Were
the recorded amounts really in m?": it says the unit is unchanged, what a
change would do with one of the item's own amounts, then links the way to
put right a unit wrong from the start (Ali, 2026-11-25,
inventory-tng-aknv). Converting itself is
test_items_routes_unit_conversion.py's."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session, object_session

from inventory_tng.catalogue import unit_changes
from inventory_tng.catalogue.paths import ITEMS, unit_recount
from inventory_tng.labels import labels
from inventory_tng.labels.paths import item_labels
from inventory_tng.models.catalogue import Item, Vendor, VendorOffer
from inventory_tng.web.forms import CHANGED, RULES

from helpers.items import unit_answered
from helpers.labels import labelled
from helpers.markup import described_and_invalid, problem, read, text
from helpers.rows import add
from helpers.stock import Place, Spools, converted_reel, received, retire, write


def _declined(client: TestClient, item: Item, unit: str) -> str:
    """The page answering No to changing the item to `unit`, its rows
    read again as stored (a package's size typed as 305 reads 305.000)."""
    session = object_session(item)
    assert session is not None
    session.expire_all()
    page = client.post(f"{ITEMS}/{item.id}", data=unit_answered(item, "no", unit=unit))
    assert page.status_code == 400
    return page.text


def _offer(
    item: Item, units_per_order: Decimal | None, vendor: str | None = "Acme"
) -> None:
    """A vendor offer of `units_per_order` in the item's unit, from
    `vendor`, by name, or naming none."""
    session = object_session(item)
    assert session is not None
    vendor_id = add(session, Vendor(name=vendor)).id if vendor else None
    add(
        session,
        VendorOffer(
            item_id=item.id,
            vendor_id=vendor_id,
            url="https://example.org",
            units_per_order=units_per_order,
            unit=item.unit,
        ),
    )


def _stored(item: Item, stored: str, amount: str) -> None:
    """The item's Reorder at, on order or a vendor offer's units per
    order (`stored`) set to `amount`, and written."""
    if stored == "offer":
        _offer(item, Decimal(amount))
        return
    setattr(item, stored, Decimal(amount))
    session = object_session(item)
    assert session is not None
    session.flush()


# Each stored amount's field, its name bold in the note.
FIELD = {"minimum": "Reorder at", "on_order": "On order", "offer": "Units per order"}

# Each stored amount the note gives as its example, from m to ft and from
# ft to m: the units, what is stored, its amount and the example's words.
STORED_EXAMPLES = [
    (
        "m",
        "ft",
        "minimum",
        "100",
        "Its Reorder at would change from 100 m to 328.084 ft",
    ),
    ("m", "ft", "on_order", "50", "Its On order would change from 50 m to 164.042 ft"),
    (
        "m",
        "ft",
        "offer",
        "305",
        "The Units per order from Acme would change from 305 m to 1000.656 ft",
    ),
    (
        "ft",
        "m",
        "minimum",
        "1000",
        "Its Reorder at would change from 1000 ft to 304.8 m",
    ),
    ("ft", "m", "on_order", "50", "Its On order would change from 50 ft to 15.24 m"),
    (
        "ft",
        "m",
        "offer",
        "1000.656",
        "The Units per order from Acme would change from 1000.656 ft to 305 m",
    ),
]


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
    labelled(session, cable, 1)
    page = _declined(admin_client, cable, into)
    assert problem(page, "unit") == (
        f"The unit is still {unit}. Changing it to {into} recalculates every "
        f"recorded amount from {unit}. The stock at {place.store.name} would "
        f"change from {held} {unit} to {becomes} {into}. If the {held} was "
        f"really {into}, "
        f"recount it in {into}: its records are set to nothing, its unit "
        f"becomes {into}, and you count each place in {into}."
    )
    note = read(page).one("span", id="unit-problem")
    note.one("a", f"recount it in {into}", href=unit_recount(cable.id, into))
    assert note.all("a", href=item_labels(cable.id)) == []
    # Nothing typed was refused: the unit kept is not marked invalid
    # (inventory-tng-qwjj).
    unit_box = read(page).one("select", name="unit")
    assert described_and_invalid(unit_box) == (None, None)


def _installed(session: Session, unit: str = "m") -> Item:
    """A cable in `unit` received and all of it installed: no place holds
    any."""
    place = Place(session, cable_unit=unit)
    cable = place.cable
    lines: list[dict[str, object]] = [
        {"item_id": cable.id, "quantity": "305", "unit": unit}
    ]
    received(session, place, lines)
    write(session, place, kind="install", to_location_id=None, lines=lines)
    return cable


def test_a_label_revoked_while_the_note_is_drawn_changes_no_count(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its one label revoked after the note counted it, as a request at
    once can, before the page is drawn: the page links to revoke the
    label the note counted, never "revoke its 0 labels"."""
    cable = Place(session).cable
    labelled(session, cable, 0)
    reads = []
    current_of = labels.current_of

    def revoked_after_the_first(
        session: Session, names: ColumnElement[bool]
    ) -> list[labels.Named]:
        reads.append(names)
        return current_of(session, names) if len(reads) == 1 else []

    monkeypatch.setattr(labels, "current_of", revoked_after_the_first)
    page = _declined(admin_client, cable, "ft")
    assert [a.text for a in read(page).all("a") if "revoke its" in a.text] == [
        "revoke its label"
    ]


@pytest.mark.parametrize(
    ("unit", "into", "stored", "amount", "example"), STORED_EXAMPLES
)
def test_stock_moved_none_left_gives_a_stored_amount(
    admin_client: TestClient,
    session: Session,
    unit: str,
    into: str,
    stored: str,
    amount: str,
    example: str,
) -> None:
    """No place holds any to give: a stored amount instead, as the
    labels-only note gives (Ali, 2026-11-25, inventory-tng-aknv). The
    link says "they", as the example is no stock to recount."""
    cable = _installed(session, unit)
    _stored(cable, stored, amount)
    page = _declined(admin_client, cable, into)
    assert problem(page, "unit") == (
        f"The unit is still {unit}. Changing it to {into} recalculates every "
        f"recorded amount from {unit}. {example}. If they were really in "
        f"{into}, recount it in {into}: its records are set to nothing, its "
        f"unit becomes {into}, and you count each place in {into}."
    )
    note = read(page).one("span", id="unit-problem")
    assert [each.text for each in note.all("strong")] == [FIELD[stored]]
    note.one("a", f"recount it in {into}", href=unit_recount(cable.id, into))


def test_stock_moved_none_left_nor_stored_leaves_the_example_out(
    admin_client: TestClient, session: Session
) -> None:
    """All of it installed, and no amount stored: nothing to give."""
    cable = _installed(session)
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
        f"amount from m. The stock at {place.van.name} would change from 5 m "
        "to 16.404 ft. "
        "If the 5 was really ft,"
    )


def test_stock_in_sealed_packages_changes_by_how_many(
    admin_client: TestClient, session: Session
) -> None:
    """20 sealed spools of 305 m become 20 of 1000.656 ft, as the ledger
    reads a sealed side once the item is in ft: 20013.12 ft, not the
    20013.123 ft of 6100 m converted exactly."""
    spools = Spools(session)
    spools.receive(uuid.uuid4(), spools=18)
    page = _declined(admin_client, spools.cable, "ft")
    assert (
        f"The stock at {spools.store.name} would change from 6100 m to "
        "20013.12 ft." in problem(page, "unit")
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


def test_no_with_a_field_wrong_from_a_page_changed_since_says_so_first(
    admin_client: TestClient, session: Session
) -> None:
    """No sent with Reorder at wrong, from a page drawn before another
    administrator renamed the item: refused as changed since, the page as
    it is now, not the field's problem first (inventory-tng-ryjv). Not
    changed, it says the field's problem."""
    cable = _moved(session, "m", "300", "5").cable
    answered = unit_answered(cable, "no", unit="ft", minimum="-1")
    wrong = admin_client.post(f"{ITEMS}/{cable.id}", data=answered)
    assert wrong.status_code == 400
    assert problem(wrong.text, "minimum")
    cable.name = "Patch cable"
    session.flush()
    page = admin_client.post(f"{ITEMS}/{cable.id}", data=answered)
    assert page.status_code == 409
    assert str(RULES[CHANGED]) in text(page)
    assert read(page.text).all("span", class_="problem") == []


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
    ("unit", "into", "stored", "amount", "example"), STORED_EXAMPLES
)
def test_labels_only_give_a_stored_amount_and_link_to_revoke_the_label(
    admin_client: TestClient,
    session: Session,
    unit: str,
    into: str,
    stored: str,
    amount: str,
    example: str,
) -> None:
    """The field's name bold, as its label (Ali, 2026-11-25,
    inventory-tng-aknv)."""
    cable = Place(session, cable_unit=unit).cable
    _stored(cable, stored, amount)
    labelled(session, cable, 0)
    page = _declined(admin_client, cable, into)
    assert problem(page, "unit") == (
        f"The unit is still {unit}. Until its printed label is revoked, "
        f"changing it recalculates its amounts from {unit}. {example}. If "
        f"they were really in {into}, revoke its label, then change its unit "
        f"to {into}; the amounts stay as they are."
    )
    note = read(page).one("span", id="unit-problem")
    assert [each.text for each in note.all("strong")] == [FIELD[stored]]
    note.one("a", "revoke its label", href=item_labels(cable.id))


def test_the_example_is_the_first_amount_stored(
    admin_client: TestClient, session: Session
) -> None:
    """Nothing on order is skipped, as is an offer saying no units per
    order: the first offer saying some is given, not a later one."""
    cable = Place(session).cable
    cable.on_order = Decimal(0)
    _offer(cable, None, vendor="Unsaid")
    _offer(cable, Decimal(305))
    _offer(cable, Decimal(100), vendor="Later")
    labelled(session, cable, 0)
    page = _declined(admin_client, cable, "ft")
    assert "The Units per order from Acme would change from 305 m to " in problem(
        page, "unit"
    )


@pytest.mark.parametrize(
    ("vendor", "named"),
    [("<em>Acme</em> & Co", "<em>Acme</em> & Co"), (None, "https://example.org")],
)
def test_an_offer_names_its_vendor_as_typed_or_its_address(
    admin_client: TestClient, session: Session, vendor: str | None, named: str
) -> None:
    """A vendor's name is never read as markup: only the field's name is
    bold. An offer naming no vendor gives its address, as the recount's
    page does."""
    cable = Place(session).cable
    _offer(cable, Decimal(305), vendor=vendor)
    labelled(session, cable, 0)
    page = _declined(admin_client, cable, "ft")
    assert f"The Units per order from {named} would change" in problem(page, "unit")
    assert read(page).one("span", id="unit-problem").all("em") == []


def test_two_labels_and_no_stored_amount_leave_the_example_out(
    admin_client: TestClient, session: Session
) -> None:
    """The revoke link once on the page, not in the unit's hint as well
    (inventory-tng-62qs)."""
    cable = Place(session).cable
    labelled(session, cable, 1)
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
    labelled(session, cable, 4)
    page = _declined(admin_client, cable, "m")
    assert problem(page, "unit") == (
        "The unit is still ft. Until all 5 of its printed labels are revoked, "
        "changing it recalculates its amounts from ft. Its Reorder at would "
        "change from 1000 ft to 304.8 m. If they were really in m, revoke its "
        "5 labels, "
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
        "ft. The Units per order from Acme would change from 1000.656 ft to "
        "305 m. If they were really in m, recount it in m: you give its "
        "amounts and package sizes again in m, and its unit becomes m."
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


def _retired_declined(client: TestClient, item: Item, unit: str) -> str:
    """The page answering No to changing the item to `unit`, from a page
    drawn before it was retired, as a retired item's form is read only;
    checked to link neither the recount nor its labels."""
    answered = unit_answered(item, "no", unit=unit)
    session = object_session(item)
    assert session is not None
    retire(session, item)
    page = client.post(f"{ITEMS}/{item.id}", data=answered)
    assert page.status_code == 400
    note = read(page.text).one("span", id="unit-problem")
    assert note.all("a") == []
    return page.text


@pytest.mark.parametrize(
    ("unit", "into", "becomes"), [("m", "ft", "1000.656"), ("ft", "m", "301.752")]
)
def test_a_retired_item_whose_stock_moved_says_only_the_unit_and_example(
    admin_client: TestClient, session: Session, unit: str, into: str, becomes: str
) -> None:
    """It can't be changed until it is brought back, so no "recalculates"
    sentence and no recount link: as a retired labelled item (Ali,
    2026-11-25, inventory-tng-aknv)."""
    held = "305" if unit == "m" else "990"
    place = _moved(session, unit, str(Decimal(held) + 5), "5")
    page = _retired_declined(admin_client, place.cable, into)
    assert problem(page, "unit") == (
        f"The unit is still {unit}. The stock at {place.store.name} would "
        f"change from {held} {unit} to {becomes} {into}."
    )


@pytest.mark.parametrize(("unit", "into"), [("m", "ft"), ("ft", "m")])
def test_a_retired_item_whose_stock_moved_and_no_example_says_only_the_unit(
    admin_client: TestClient, session: Session, unit: str, into: str
) -> None:
    """All of it installed and no amount stored: nothing to give."""
    cable = _installed(session, unit)
    page = _retired_declined(admin_client, cable, into)
    assert problem(page, "unit") == f"The unit is still {unit}."


def _converted(session: Session, unit: str) -> Item:
    """A cable in `unit` converted before, so fixed by that alone."""
    cable = Place(session, cable_unit=unit).cable
    cable.unit_converted = True
    session.flush()
    return cable


@pytest.mark.parametrize(
    ("unit", "into", "stored", "amount", "example"), STORED_EXAMPLES
)
def test_a_retired_converted_item_says_only_the_unit_and_example(
    admin_client: TestClient,
    session: Session,
    unit: str,
    into: str,
    stored: str,
    amount: str,
    example: str,
) -> None:
    """As a retired item whose stock moved (Ali, 2026-11-25,
    inventory-tng-aknv)."""
    cable = _converted(session, unit)
    _stored(cable, stored, amount)
    page = _retired_declined(admin_client, cable, into)
    assert problem(page, "unit") == f"The unit is still {unit}. {example}."
    note = read(page).one("span", id="unit-problem")
    assert [each.text for each in note.all("strong")] == [FIELD[stored]]


@pytest.mark.parametrize(("unit", "into"), [("m", "ft"), ("ft", "m")])
def test_a_retired_converted_item_with_no_example_says_only_the_unit(
    admin_client: TestClient, session: Session, unit: str, into: str
) -> None:
    """No amount stored: nothing to give."""
    cable = _converted(session, unit)
    page = _retired_declined(admin_client, cable, into)
    assert problem(page, "unit") == f"The unit is still {unit}."


@pytest.mark.parametrize(
    ("unit", "into", "minimum", "example"),
    [
        (
            "m",
            "ft",
            Decimal(100),
            " Its Reorder at would change from 100 m to 328.084 ft.",
        ),
        ("m", "ft", None, ""),
        (
            "ft",
            "m",
            Decimal(1000),
            " Its Reorder at would change from 1000 ft to 304.8 m.",
        ),
        ("ft", "m", None, ""),
    ],
)
def test_a_retired_labelled_item_ends_with_no_link(
    admin_client: TestClient,
    session: Session,
    unit: str,
    into: str,
    minimum: Decimal | None,
    example: str,
) -> None:
    """Its labels are inactive, so neither a link to revoke them nor
    "Until … revoked": the page's top says to bring it back first (Ali,
    2026-11-25, inventory-tng-aknv)."""
    cable = Place(session, cable_unit=unit).cable
    cable.minimum = minimum
    labelled(session, cable, 0)
    page = _retired_declined(admin_client, cable, into)
    assert problem(page, "unit") == f"The unit is still {unit}.{example}"
    assert read(page).all("a", href=item_labels(cable.id)) == []
