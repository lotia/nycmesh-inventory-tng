"""The guided recount of an item whose unit was wrong from the start (Ali,
2026-11-24 and 2026-11-25, inventory-tng-qety): every place brought to
zero in the old unit, its open packages closed, its packages in the old
unit retired and renamed, its unit switched, and Reorder at, what is on
order and its vendor offers given again in the new unit; then a count at
each place. Nothing converts, and no label is revoked (Ali, 2026-11-25,
inventory-tng-8ema). An item converted before whose stock never moved is
given its packages' sizes in its old unit again instead, their labels
revoked (Ali, 2026-11-25, inventory-tng-l26i)."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.catalogue import items, unit_recounts
from inventory_tng.catalogue.paths import ITEMS, UNIT_RECOUNTED, unit_recount
from inventory_tng.labels import labels
from inventory_tng.models.catalogue import Item, ItemPackage, VendorOffer
from inventory_tng.models.labels import Label
from inventory_tng.models.stock import AdjustmentReason, Movement, OpenPackage
from inventory_tng.stock import open_packages
from inventory_tng.stock.ledger import Entry, Form
from inventory_tng.stock.moments import expected
from inventory_tng.stock.paths import COUNTS
from inventory_tng.web.forms import RULES

from helpers.markup import (
    box_values,
    hidden_fields,
    problem,
    read,
    table_rows,
    words_on,
)
from helpers.rows import add
from helpers.sign_in import new_admin_id
from helpers.stock import (
    Place,
    Spools,
    converted_reel,
    counts_written,
    held_by_form,
    raise_check,
    received,
    retire,
)


def _page(client: TestClient, item_id: int, unit: str = "ft") -> Response:
    page = client.get(unit_recount(item_id, unit))
    assert page.status_code == 200
    return page


def _sent(page: Response, **boxes: str) -> dict[str, str]:
    """The recount's form as its page shows it, its boxes as shown but for
    `boxes`."""
    form = dict(hidden_fields(page.text))
    for box in read(page.text).all("input"):
        if box.attrs.get("type") != "hidden":
            form[box.attrs["name"]] = box.attrs.get("value", "")
    return form | boxes


def _recount(client: TestClient, item_id: int, **boxes: str) -> Response:
    """The recount sent from its page as shown now."""
    page = _page(client, item_id)
    return client.post(unit_recount(item_id, "ft"), data=_sent(page, **boxes))


def _spools(session: Session) -> Spools:
    """The bead's example, Outdoor Cat6 set to m when it should be ft: two
    spools of 305 received into the store, one opened there; 5 cut loose
    from the van, which never had any, so less than none there; a label of
    the spool; Reorder at 100, 50 on order, and an offer of 305 per order."""
    spools = Spools(session)
    spools.opened()
    cable = spools.cable
    spools.write(
        {"kind": "install", "from_location_id": spools.van.id},
        Entry(cable.id, Decimal(5)),
    )
    labels.make_for_item(session, cable.id, spools.spool.id)
    cable.minimum, cable.on_order = Decimal(100), Decimal(50)
    add(
        session,
        VendorOffer(item_id=cable.id, url="https://example.org", units_per_order=305),
    )
    return spools


def test_the_page_says_beforehand_all_it_will_do(
    admin_client: TestClient, session: Session
) -> None:
    spools = _spools(session)
    cable = spools.cable
    page = _page(admin_client, cable.id)
    said = words_on(page.text)
    assert f"Recount {cable.name} in ft" in said
    assert "was never really in m: nothing is recalculated" in said
    assert table_rows(page.text) == [
        [spools.store.name, "610 m"],
        [spools.van.name, "-5 m"],
    ]
    assert "closes its open package;" in said
    assert "Spool becomes Spool (m)" in said
    assert "revoke" not in said
    assert "changes its unit to ft" in said
    assert "Then count it at each place in ft, and add its packages in ft." in said
    assert box_values(page.text, "minimum") == ["100"]
    assert box_values(page.text, "on_order") == ["50"]
    assert box_values(page.text, f"per_order_{_offer(session, cable.id).id}") == ["305"]
    assert "Units per order from https://example.org" in said


def _offer(session: Session, item_id: int) -> VendorOffer:
    return session.scalars(
        select(VendorOffer).where(VendorOffer.item_id == item_id)
    ).one()


def test_recount_brings_each_place_to_zero_and_switches_the_unit(
    admin_client: TestClient, session: Session
) -> None:
    """Written off in m as Records were wrong, found where less than none;
    nothing converted; its label left current, as it names no unit; then a
    count asked at each place in ft."""
    spools = _spools(session)
    cable, store, van = spools.cable, spools.store, spools.van
    opened = session.scalars(
        select(OpenPackage).where(OpenPackage.item_id == cable.id)
    ).one()
    offer = _offer(session, cable.id)
    done = _recount(
        admin_client,
        cable.id,
        minimum="300",
        on_order="150",
        **{f"per_order_{offer.id}": "1000"},
    )
    assert done.status_code == 303
    session.expire_all()
    assert cable.unit == "ft"
    assert (cable.minimum, cable.on_order) == (Decimal(300), Decimal(150))
    assert (offer.units_per_order, offer.unit) == (Decimal(1000), "ft")
    # Each form at each place, not only each total.
    for place in (store, van):
        assert held_by_form(session, place.id, cable.id) == {}
    records_wrong = session.scalars(
        select(AdjustmentReason.id).where(AdjustmentReason.name == "Records were wrong")
    ).one()
    written = counts_written(session)
    assert [(t.reason_id, t.from_location_id, t.to_location_id) for t in written] == [
        (records_wrong, store.id, None),
        (records_wrong, None, van.id),
    ]
    lines = session.execute(
        select(Movement.quantity, Movement.unit)
        .where(Movement.transaction_id.in_([t.id for t in written]))
        .order_by(Movement.id)
    ).all()
    assert sorted(lines) == [
        (Decimal(5), "m"),
        (Decimal(305), "m"),
        (Decimal(305), "m"),
    ]
    assert opened.closed_at is not None
    assert (spools.spool.name, spools.spool.unit) == ("Spool (m)", "m")
    assert spools.spool.retired_at is not None
    (label,) = session.scalars(select(Label).where(Label.item_id == cable.id))
    assert label.revoked_at is None
    assert cable.unit_recounted_after == written[-1].id
    # A spool in ft takes the old one's name.
    items.add_package(session, cable, "Spool", Decimal(1000), shown_unit="ft")
    after = admin_client.get(done.headers["location"])
    assert after.status_code == 200
    page = read(after.text)
    page.one(
        "a", f"Count it at {store.name}", href=f"{COUNTS}/{store.id}?item={cable.id}"
    )
    page.one("a", f"Count it at {van.name}", href=f"{COUNTS}/{van.id}?item={cable.id}")
    page.one("a", "Add its packages in ft", href=f"{ITEMS}/{cable.id}#add-package")
    assert "label" not in words_on(page)


def test_a_place_retired_since_is_counted_no_more(
    admin_client: TestClient, session: Session
) -> None:
    """The page after lists only current places; with none, it says so."""
    spools = Spools(session)
    retire(session, spools.store)
    path = UNIT_RECOUNTED.format(item_id=spools.cable.id)
    after = admin_client.get(f"{path}?places={spools.store.id},x")
    assert "nothing to count" in words_on(after.text)


def test_an_item_that_holds_nothing_changes_only_its_amounts(
    admin_client: TestClient, session: Session
) -> None:
    """Received and all installed: nothing to write off or close; its
    packages in m retired all the same, as their sizes were typed in m."""
    spools = Spools(session)
    spools.install(spools.store.id, 610)
    before = len(counts_written(session))
    page = _page(admin_client, spools.cable.id)
    assert "writes off nothing: the records hold none of it at any place" in (
        words_on(page.text)
    )
    done = admin_client.post(
        unit_recount(spools.cable.id, "ft"), data=_sent(page, minimum="")
    )
    assert done.status_code == 303
    session.expire_all()
    assert len(counts_written(session)) == before
    assert (spools.cable.unit, spools.cable.minimum) == ("ft", None)
    assert "nothing to count" in words_on(
        admin_client.get(done.headers["location"]).text
    )


def test_a_part_of_a_thousandth_left_by_a_conversion_is_brought_to_zero(
    admin_client: TestClient, session: Session
) -> None:
    """1 ft received, then the cable set to m (as converted): 0.3048 m
    reads 0.305 m, written off so; in ft the line and that differ by
    0.000656 ft, which reads 0.001 ft, so it is found in ft too."""
    place = Place(session, cable_unit="ft")
    cable = place.cable
    received(session, place, [{"item_id": cable.id, "quantity": "1", "unit": "ft"}])
    cable.unit = "m"
    session.flush()
    assert _recount(admin_client, cable.id).status_code == 303
    session.expire_all()
    written = counts_written(session)
    lines = session.execute(
        select(Movement.quantity, Movement.unit)
        .where(Movement.transaction_id.in_([t.id for t in written]))
        .order_by(Movement.id)
    ).all()
    assert lines == [(Decimal("0.305"), "m"), (Decimal("0.001"), "ft")]
    assert expected(session, place.store.id) == {}


@pytest.mark.parametrize("closed", [True, False])
def test_a_part_of_a_thousandth_on_an_open_package_gone_is_found_loose(
    admin_client: TestClient, session: Session, closed: bool
) -> None:
    """A spool of 1 ft opened in ft; the cable set to m (as converted);
    the 0.305 m on it, as read, cut and the spool closed, or moved whole
    to the van. Its form at the store, 1 ft less 0.305 m, reads nothing
    in m but -0.001 ft once the cable is in ft again: no line may name it
    there, so it is found loose, and the store holds nothing."""
    spools = Spools(session, cable_unit="ft")
    cable = spools.cable
    spools.spool.size = Decimal(1)
    opened = open_packages.make(session, uuid.uuid4(), spools.spool.id, spools.store.id)
    spools.write(
        {
            "kind": "open",
            "from_location_id": spools.store.id,
            "to_location_id": spools.store.id,
        },
        Entry(cable.id, Decimal(1), spools.sealed, Form(open_package_id=opened.id)),
    )
    cable.unit = "m"
    session.flush()
    on_it = Form(open_package_id=opened.id)
    if closed:
        spools.write(
            {"kind": "install", "from_location_id": spools.store.id},
            Entry(cable.id, Decimal("0.305"), on_it),
        )
        closer = new_admin_id(session)
        assert open_packages.close(
            session, opened.id, closer, datetime.now(UTC), how="direct"
        )
    else:
        spools.move(Entry(cable.id, Decimal("0.305"), on_it, on_it))
    session.flush()
    page = _page(admin_client, cable.id)
    done = admin_client.post(unit_recount(cable.id, "ft"), data=_sent(page))
    assert done.status_code == 303
    session.expire_all()
    assert cable.unit == "ft"
    assert expected(session, spools.store.id) == {}


def test_a_name_taken_takes_the_next_number(
    admin_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    add(session, ItemPackage(item_id=spools.cable.id, name="Spool (m)", size=10))
    page = words_on(_page(admin_client, spools.cable.id).text)
    assert "Spool becomes Spool (m) 2" in page
    assert "Spool (m) becomes Spool (m) (m)" in page


def test_refused_if_anything_changed_since_shown(
    admin_client: TestClient, session: Session
) -> None:
    """A package added since would be retired unsaid: nothing is done, and
    the page is shown again as it is now, with it. Logged once, by id."""
    spools = Spools(session)
    page = _page(admin_client, spools.cable.id)
    items.add_package(session, spools.cable, "Reel", Decimal(100), shown_unit="m")
    with capture_logs() as logs:
        refused = admin_client.post(
            unit_recount(spools.cable.id, "ft"), data=_sent(page)
        )
    assert refused.status_code == 409
    assert [each for each in logs if each["event"] == "unit recount refused"] == [
        {
            "event": "unit recount refused",
            "item_id": spools.cable.id,
            "reason": "changed",
            "log_level": "info",
        }
    ]
    assert str(RULES["changed_since_shown"]) in words_on(refused.text)
    assert "Reel becomes Reel (m)" in words_on(refused.text)
    assert spools.cable.unit == "m"


def test_a_label_printed_since_shown_changes_nothing(
    admin_client: TestClient, session: Session
) -> None:
    """The recount revokes no labels, so one printed after its page was
    shown is no reason to refuse it, and stays current."""
    spools = Spools(session)
    page = _page(admin_client, spools.cable.id)
    label = labels.make_for_item(session, spools.cable.id)
    assert label is not None
    done = admin_client.post(unit_recount(spools.cable.id, "ft"), data=_sent(page))
    assert done.status_code == 303
    session.expire_all()
    assert (spools.cable.unit, label.revoked_at) == ("ft", None)


def test_refused_while_a_line_recorded_with_more_than_moved_waits(
    admin_client: TestClient, session: Session
) -> None:
    """As a conversion is (unit_changes.convert): its page says so, with
    nothing to send, and a recount sent all the same is refused."""
    spools = Spools(session)
    raise_check(session, spools.move(Entry(spools.cable.id, Decimal(1))))
    waiting = str(RULES["item_unit_checks_waiting"])
    shown = read(_page(admin_client, spools.cable.id).text)
    assert shown.one("p", id="waiting").text == waiting
    assert shown.all("button", "Recount in ft") == []
    sent = {
        "unit": "ft",
        "seen": unit_recounts.planned(session, spools.cable).seen,
        "key": str(uuid.uuid4()),
    }
    refused = admin_client.post(unit_recount(spools.cable.id, "ft"), data=sent)
    assert refused.status_code == 409
    assert waiting in words_on(refused.text)
    assert spools.cable.unit == "m"


def test_amounts_are_checked(admin_client: TestClient, session: Session) -> None:
    spools = Spools(session)
    add(session, VendorOffer(item_id=spools.cable.id, url="https://example.org"))
    offer = _offer(session, spools.cable.id)
    refused = _recount(
        admin_client,
        spools.cable.id,
        minimum="-1",
        **{f"per_order_{offer.id}": "0"},
    )
    assert refused.status_code == 400
    assert problem(refused.text, "minimum") == "Use 0 or more."
    assert problem(refused.text, f"per_order_{offer.id}") == "Use a number above 0."
    assert spools.cable.unit == "m"


@pytest.mark.parametrize("unit", ["each", "m", "kg"])
def test_only_another_length_is_offered(
    admin_client: TestClient, session: Session, unit: str
) -> None:
    """Into its own unit, each or no unit at all, as after a recount sent
    twice: the item's page, nothing done."""
    spools = Spools(session)
    path = unit_recount(spools.cable.id, unit)
    assert admin_client.get(path).headers["location"] == f"{ITEMS}/{spools.cable.id}"
    sent = admin_client.post(path, data={"unit": unit})
    assert sent.headers["location"] == f"{ITEMS}/{spools.cable.id}"


def _labelled(session: Session, place: Place) -> None:
    """Only labelled: revoking its labels, then a free change, does it."""
    labels.make_for_item(session, place.cable.id)


def _retired(session: Session, place: Place) -> None:
    """Moved, then retired: read only."""
    received(
        session, place, [{"item_id": place.cable.id, "quantity": "1", "unit": "m"}]
    )
    retire(session, place.cable)


@pytest.mark.parametrize("fixed", [_labelled, _retired])
def test_only_a_current_item_whose_stock_moved_is_recounted(
    admin_client: TestClient,
    session: Session,
    fixed: Callable[[Session, Place], None],
) -> None:
    place = Place(session)
    fixed(session, place)
    path = unit_recount(place.cable.id, "ft")
    assert admin_client.get(path).headers["location"] == f"{ITEMS}/{place.cable.id}"


def test_no_such_item(admin_client: TestClient) -> None:
    assert admin_client.get(unit_recount(0, "ft")).status_code == 404
    assert admin_client.post(unit_recount(0, "ft"), data={}).status_code == 404
    path = UNIT_RECOUNTED.format(item_id=0)
    assert admin_client.get(path).status_code == 404


def test_a_recount_sent_twice_is_done_once(
    admin_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    page = _page(admin_client, spools.cable.id)
    form = _sent(page)
    path = unit_recount(spools.cable.id, "ft")
    assert admin_client.post(path, data=form).status_code == 303
    again = admin_client.post(path, data=form)
    assert again.headers["location"] == f"{ITEMS}/{spools.cable.id}"
    assert len(counts_written(session)) == 1


def test_refused_if_it_reached_a_place_since_its_places_were_read(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """That place was not held before the item: nothing is written, and the
    page is shown again (test_requests_at_once has it at once)."""
    spools = Spools(session)
    page = _page(admin_client, spools.cable.id)
    read_places = unit_recounts._places_of
    reads: list[int] = []

    def stale_first(session: Session, item_id: int) -> set[int]:
        reads.append(item_id)
        return set() if len(reads) == 1 else read_places(session, item_id)

    monkeypatch.setattr(unit_recounts, "_places_of", stale_first)
    refused = admin_client.post(unit_recount(spools.cable.id, "ft"), data=_sent(page))
    assert refused.status_code == 409
    assert spools.cable.unit == "m"


def test_one_no_longer_to_be_recounted_once_held_goes_to_its_page(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changed between the page's checks and its lock, as by a conversion
    meanwhile: refused, then the item's page, as there is no recount of it
    into that unit to show."""
    spools = Spools(session)
    page = _page(admin_client, spools.cable.id)
    open_before = unit_recounts.units_open
    calls: list[int] = []

    def closed_once_held(session: Session, item: Item) -> tuple[str, ...]:
        calls.append(item.id)
        return open_before(session, item) if len(calls) == 1 else ()

    monkeypatch.setattr(unit_recounts, "units_open", closed_once_held)
    refused = admin_client.post(unit_recount(spools.cable.id, "ft"), data=_sent(page))
    assert refused.headers["location"] == f"{ITEMS}/{spools.cable.id}"
    assert spools.cable.unit == "m"


def _converted(session: Session) -> tuple[Item, ItemPackage, ItemPackage, Label]:
    """The cable converted to ft with no stock moved (converted_reel), its
    Reel, its Spool still in m, and each labelled, the cable too: the Reel's
    label, the one a recount into m revokes."""
    place, spool, reel = converted_reel(session)
    cable = place.cable
    for package_id in (None, spool.id):
        assert labels.make_for_item(session, cable.id, package_id) is not None
    on_reel = labels.make_for_item(session, cable.id, reel.id)
    assert on_reel is not None
    return cable, spool, reel, on_reel


def test_a_converted_item_is_recounted_by_its_amounts_and_package_sizes(
    admin_client: TestClient, session: Session
) -> None:
    """Converted to ft, no stock moved, and wrong: its Reel, typed in ft,
    is given its size again in m and keeps its name, its label revoked as
    it prints 1000 ft; the Spool, in m already, is not asked, and its
    label and the cable's stay; nothing is written off, retired or
    converted (Ali, 2026-11-25, inventory-tng-l26i)."""
    cable, spool, reel, on_reel = _converted(session)
    offer = add(session, VendorOffer(item_id=cable.id, url="https://example.org"))
    offer.units_per_order = Decimal(1000)
    cable.on_order = Decimal(2000)
    session.flush()
    kept = [
        each.label
        for each in labels.current_of(session, Label.item_id == cable.id)
        if each.label.id != on_reel.id
    ]
    page = _page(admin_client, cable.id, "m")
    said = words_on(page.text)
    # Its stock never moved: nothing to write off, so not said (Ali,
    # 2026-11-25).
    assert "writes off" not in said
    assert "becomes" not in said
    # Nothing to count, and its packages kept (Ali, 2026-11-25).
    assert "Then count it" not in said
    assert (
        "gives the size of each of its packages in ft again, below, in m, each "
        "keeping its name and links; revokes the printed label of its current "
        "package, as it shows the size as it was;"
    ) in said
    assert "retired package" not in said
    fieldset = read(page.text).one("fieldset")
    assert fieldset.one("legend").text == "Reel"
    assert "Holds in m; was 1000 ft" in words_on(fieldset)
    assert box_values(page.text, f"size_{reel.id}") == ["1000"]
    assert box_values(page.text, f"short_below_{reel.id}") == ["10"]
    assert box_values(page.text, f"size_{spool.id}") == []
    with capture_logs() as logs:
        done = admin_client.post(
            unit_recount(cable.id, "m"),
            data=_sent(
                page,
                **{
                    f"size_{reel.id}": "300",
                    f"short_below_{reel.id}": "3",
                    f"per_order_{offer.id}": "305",
                    "on_order": "610",
                },
            ),
        )
    assert done.status_code == 303
    session.expire_all()
    assert (cable.unit, cable.on_order, cable.unit_converted) == ("m", 610, True)
    assert (offer.units_per_order, offer.unit) == (305, "m")
    assert (reel.name, reel.size, reel.short_below, reel.unit) == (
        "Reel",
        300,
        3,
        "m",
    )
    assert (spool.size, spool.unit, spool.retired_at) == (305, "m", None)
    assert reel.retired_at is None
    assert on_reel.revoked_at is not None
    assert [each.revoked_at for each in kept] == [None, None]
    assert counts_written(session) == []
    (logged,) = [each for each in logs if each["event"] == "item unit recounted"]
    assert (logged["resized_package_ids"], logged["label_ids"]) == (
        [reel.id],
        [on_reel.id],
    )
    after = read(admin_client.get(done.headers["location"]).text)
    assert "nothing to count" in words_on(after)
    assert "Add its packages" not in words_on(after)
    assert "The printed label of Reel was revoked: print a new one." in words_on(after)
    assert "retired package" not in words_on(after)
    after.one("a", "print a new one", href=f"{ITEMS}/{cable.id}#labels")


def test_a_converted_item_with_no_labels_revokes_none(
    admin_client: TestClient, session: Session
) -> None:
    """Nothing said of labels before or after."""
    place, _, reel = converted_reel(session)
    page = _page(admin_client, place.cable.id, "m")
    assert "revoke" not in words_on(page.text)
    done = admin_client.post(
        unit_recount(place.cable.id, "m"),
        data=_sent(page, **{f"size_{reel.id}": "300"}),
    )
    assert done.status_code == 303
    assert "labels=" not in done.headers["location"]
    after = admin_client.get(done.headers["location"])
    assert "revoked" not in words_on(after.text)


def test_a_converted_items_package_sizes_are_checked(
    admin_client: TestClient, session: Session
) -> None:
    place, _, reel = converted_reel(session)
    page = _page(admin_client, place.cable.id, "m")
    path = unit_recount(place.cable.id, "m")
    blank = admin_client.post(
        path,
        data=_sent(page, **{f"size_{reel.id}": "", f"short_below_{reel.id}": "0"}),
    )
    assert blank.status_code == 400
    assert problem(blank.text, f"size_{reel.id}") == (
        "How many of the item's unit it holds."
    )
    assert problem(blank.text, f"short_below_{reel.id}") == "Use a number above 0."
    longer = admin_client.post(
        path,
        data=_sent(page, **{f"size_{reel.id}": "5", f"short_below_{reel.id}": "5"}),
    )
    assert problem(longer.text, f"short_below_{reel.id}") == (
        "Shorter than a whole one, or blank."
    )
    session.expire_all()
    assert (place.cable.unit, reel.size) == ("ft", 1000)


def test_a_converted_items_package_added_since_shown_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """Its size was not asked: refused as changed, and asked once shown
    again."""
    place, _, _ = converted_reel(session)
    cable = place.cable
    page = _page(admin_client, cable.id, "m")
    box = items.add_package(session, cable, "Box", Decimal(500), shown_unit="ft")
    refused = admin_client.post(unit_recount(cable.id, "m"), data=_sent(page))
    assert refused.status_code == 409
    assert str(RULES["changed_since_shown"]) in words_on(refused.text)
    assert box_values(refused.text, f"size_{box.id}") == ["500"]
    assert cable.unit == "ft"


def test_a_label_printed_on_a_package_to_resize_since_shown_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """The page did not say it would be revoked."""
    place, _, reel = converted_reel(session)
    cable = place.cable
    page = _page(admin_client, cable.id, "m")
    label = labels.make_for_item(session, cable.id, reel.id)
    assert label is not None
    refused = admin_client.post(unit_recount(cable.id, "m"), data=_sent(page))
    assert refused.status_code == 409
    session.expire_all()
    assert (cable.unit, label.revoked_at) == ("ft", None)


def test_a_converted_items_package_size_not_sent_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """A form without a package's size, as one sent by hand: refused as
    changed, the package never left in the old unit."""
    place, _, reel = converted_reel(session)
    cable = place.cable
    page = _page(admin_client, cable.id, "m")
    form = _sent(page)
    del form[f"size_{reel.id}"]
    refused = admin_client.post(unit_recount(cable.id, "m"), data=form)
    assert refused.status_code == 409
    session.expire_all()
    assert (cable.unit, reel.unit) == ("ft", "ft")


def _retired_box(session: Session) -> tuple[Item, ItemPackage, ItemPackage, Label]:
    """The cable converted to ft with no stock moved (converted_reel), its
    Reel, and a Box of 1000 ft, labelled, then retired: its label, the one
    a recount into m revokes."""
    place, _, reel = converted_reel(session)
    cable = place.cable
    box = items.add_package(session, cable, "Box", Decimal(1000), shown_unit="ft")
    on_box = labels.make_for_item(session, cable.id, box.id)
    assert on_box is not None
    items.retire_package(session, box, datetime.now(UTC))
    session.flush()
    return cable, reel, box, on_box


def test_a_converted_items_retired_package_is_resized_and_stays_retired(
    admin_client: TestClient, session: Session
) -> None:
    """Its retired Box of 1000 ft is asked below its current Reel, marked
    retired, and given 304.8 m; it stays retired, its label revoked, to
    print once it is brought back, when it has the new size; the Reel's
    label, revoked too, is to print now (Ali, 2026-11-25,
    inventory-tng-l26i)."""
    cable, reel, box, on_box = _retired_box(session)
    on_reel = labels.make_for_item(session, cable.id, reel.id)
    assert on_reel is not None
    retired_at = box.retired_at
    page = _page(admin_client, cable.id, "m")
    said = words_on(page.text)
    assert (
        "revokes the printed label of its current package, as it shows the size "
        "as it was; revokes the printed label of its retired package too: a new "
        "one can be printed once it is brought back;"
    ) in said
    asked = read(page.text).all("fieldset")
    assert [words_on(each.one("legend")) for each in asked] == ["Reel", "Box Retired"]
    assert box_values(page.text, f"size_{box.id}") == ["1000"]
    done = admin_client.post(
        unit_recount(cable.id, "m"),
        data=_sent(page, **{f"size_{reel.id}": "300", f"size_{box.id}": "304.8"}),
    )
    assert done.status_code == 303
    session.expire_all()
    assert (reel.size, reel.unit, reel.retired_at) == (300, "m", None)
    assert (box.name, box.size, box.unit, box.retired_at) == (
        "Box",
        Decimal("304.8"),
        "m",
        retired_at,
    )
    assert (on_box.revoked_at is not None, on_reel.revoked_at is not None) == (
        True,
        True,
    )
    after = words_on(admin_client.get(done.headers["location"]).text)
    assert "The printed label of Reel was revoked: print a new one." in after
    assert (
        "The printed label of the retired package Box was revoked: print a new "
        "one if it is brought back."
    ) in after
    items.restore_package(session, box)
    session.flush()
    session.expire_all()
    assert (box.size, box.unit, box.retired_at) == (Decimal("304.8"), "m", None)


@pytest.mark.parametrize(
    ("more", "before", "said"),
    [
        (
            0,
            (
                "revokes the printed label of its retired package: a new one "
                "can be printed once it is brought back;"
            ),
            (
                "The printed label of the retired package Box was revoked: "
                "print a new one if it is brought back."
            ),
        ),
        (
            1,
            (
                "revokes the 2 printed labels of its retired packages: new "
                "ones can be printed once they are brought back;"
            ),
            (
                "The printed labels of the retired packages Box and Spool 0 "
                "were revoked: print new ones if they are brought back."
            ),
        ),
    ],
)
def test_only_a_retired_packages_labels_revoked_are_said_alone(
    admin_client: TestClient, session: Session, more: int, before: str, said: str
) -> None:
    """With the current Reel unlabelled, both pages say only that the
    retired packages' labels are revoked, without "too" before (Ali,
    2026-11-25), and the page after asks to print none now. A package has
    one label at most (labels.make_for_item), so as many labels as
    packages."""
    cable, reel, box, _ = _retired_box(session)
    sizes = {f"size_{reel.id}": "300", f"size_{box.id}": "304.8"}
    for n in range(more):
        spool = items.add_package(
            session, cable, f"Spool {n}", Decimal(500), shown_unit="ft"
        )
        assert labels.make_for_item(session, cable.id, spool.id) is not None
        items.retire_package(session, spool, datetime.now(UTC))
        sizes[f"size_{spool.id}"] = "152.4"
    session.flush()
    page = _page(admin_client, cable.id, "m")
    assert before in words_on(page.text)
    assert "current package" not in words_on(page.text)
    done = admin_client.post(unit_recount(cable.id, "m"), data=_sent(page, **sizes))
    assert done.status_code == 303
    after = words_on(admin_client.get(done.headers["location"]).text)
    assert said in after
    assert "was revoked: print a new one." not in after
    assert "print new ones." not in after
    assert "print a new one." not in after


def test_several_packages_labels_revoked_are_said_by_name(
    admin_client: TestClient, session: Session
) -> None:
    """Two current packages labelled and two retired: each pair said with
    its count before, "too" after the current ones, and named, joined as
    a list, after (Ali, 2026-11-25)."""
    cable, reel, box, _ = _retired_box(session)
    sizes = {f"size_{reel.id}": "300", f"size_{box.id}": "304.8"}
    assert labels.make_for_item(session, cable.id, reel.id) is not None
    for name, retired in (("Drum", False), ("Crate", True)):
        added = items.add_package(session, cable, name, Decimal(500), shown_unit="ft")
        assert labels.make_for_item(session, cable.id, added.id) is not None
        if retired:
            items.retire_package(session, added, datetime.now(UTC))
        sizes[f"size_{added.id}"] = "152.4"
    session.flush()
    page = _page(admin_client, cable.id, "m")
    assert (
        "revokes the 2 printed labels of its current packages, as they show the "
        "sizes as they were; revokes the 2 printed labels of its retired "
        "packages too: new ones can be printed once they are brought back;"
    ) in words_on(page.text)
    done = admin_client.post(unit_recount(cable.id, "m"), data=_sent(page, **sizes))
    assert done.status_code == 303
    after = read(admin_client.get(done.headers["location"]).text)
    assert (
        "The printed labels of Drum and Reel were revoked: print new ones."
    ) in words_on(after)
    assert (
        "The printed labels of the retired packages Box and Crate were "
        "revoked: print new ones if they are brought back."
    ) in words_on(after)
    after.one("a", "print new ones", href=f"{ITEMS}/{cable.id}#labels")


def test_the_page_after_names_only_the_items_packages(
    admin_client: TestClient, session: Session
) -> None:
    """A label id in its address not revoked, of another item's package,
    or none at all, names nothing."""
    cable, _, _, on_box = _retired_box(session)
    path = UNIT_RECOUNTED.format(item_id=cable.id)
    assert "revoked" not in words_on(
        admin_client.get(f"{path}?labels={on_box.id}").text
    )
    assert labels.revoke(session, on_box.id, datetime.now(UTC))
    assert "revoked" in words_on(admin_client.get(f"{path}?labels={on_box.id}").text)
    other = add(session, Item(name="Other", category_id=cable.category_id))
    elsewhere = UNIT_RECOUNTED.format(item_id=other.id)
    after = admin_client.get(f"{elsewhere}?labels={on_box.id},x")
    assert "revoked" not in words_on(after.text)


@pytest.mark.parametrize("bringing_back", [False, True])
def test_a_package_retired_or_brought_back_since_shown_is_refused(
    admin_client: TestClient, session: Session, bringing_back: bool
) -> None:
    """Either changes what the page showed of it: refused as changed,
    nothing resized."""
    cable, reel, box, _ = _retired_box(session)
    if not bringing_back:
        items.restore_package(session, box)
        session.flush()
    page = _page(admin_client, cable.id, "m")
    if bringing_back:
        items.restore_package(session, box)
    else:
        items.retire_package(session, box, datetime.now(UTC))
    session.flush()
    refused = admin_client.post(unit_recount(cable.id, "m"), data=_sent(page))
    assert refused.status_code == 409
    assert str(RULES["changed_since_shown"]) in words_on(refused.text)
    session.expire_all()
    assert (cable.unit, reel.unit, box.unit) == ("ft", "ft", "ft")
