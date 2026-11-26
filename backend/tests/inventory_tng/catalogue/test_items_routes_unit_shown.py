"""A package's amount on the item page, its size added or its too-short
length set, typed in the unit the form showed: an item switched to another
unit since, the save is refused and the form asks again in the new one,
nothing stored, never the number kept in the new unit (inventory-tng-74f3);
anything else wrong with the number in any unit said with it
(inventory-tng-hhlr)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.catalogue import items
from inventory_tng.catalogue.forms import SIZE_REQUIRED
from inventory_tng.catalogue.items_routes import UNIT_SHOWN
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.stock.forms import now_counted_in
from inventory_tng.web.forms import ABOVE_ZERO

from helpers.items import new_item
from helpers.markup import problem, read
from helpers.rows import item_fields
from helpers.stock import Spools


def _drawn(admin_client: TestClient, item: Item, action: str) -> dict[str, str]:
    """The hidden fields of the item page's form sent to `action`, as the
    page draws it now."""
    return _hidden(admin_client.get(f"{ITEMS}/{item.id}").text, action)


def _hidden(html: str, action: str) -> dict[str, str]:
    """The hidden fields of the form sent to `action` on the page `html`."""
    (form,) = [f for f in read(html).all("form") if f.attrs.get("action") == action]
    return {
        box.attrs["name"]: box.attrs.get("value", "")
        for box in form.all("input", type="hidden")
    }


def _cable(session: Session, radios: Category) -> tuple[Item, ItemPackage]:
    """A cable in m, nothing moved in it, its opened ones followed, so its
    Reel package has a Too short to use box."""
    cable = new_item(session, radios, name="Cable", unit="m")
    cable.track_opened = True
    reel = ItemPackage(item_id=cable.id, name="Reel", size=Decimal(305))
    session.add(reel)
    session.flush()
    return cable, reel


def _to_ft(session: Session, cable: Item, *, recorded_in: str | None = None) -> None:
    """Another administrator's switch to ft, saved: free while nothing
    fixes its unit, else converting, answered Yes (`recorded_in`)."""
    _switch(session, cable, "ft", recorded_in=recorded_in)


def _switch(
    session: Session, cable: Item, unit: str, *, recorded_in: str | None = None
) -> None:
    """Another administrator's switch to `unit`, saved."""
    fields = item_fields(cable, unit=unit)
    items.save(session, cable, fields, seen=None, recorded_in=recorded_in)
    session.flush()
    assert cable.unit == unit


@pytest.mark.parametrize("converting", [False, True])
def test_a_package_added_in_a_unit_left_since_is_asked_again(
    admin_client: TestClient, session: Session, radios: Category, converting: bool
) -> None:
    """ "Reel" holding 300 typed in m, the cable in ft since: refused beside
    Holds, nothing added; sent again from the page as drawn now, in ft, it
    is added."""
    if converting:
        cable = Spools(session).cable
    else:
        cable, _ = _cable(session, radios)
    action = f"{ITEMS}/{cable.id}/packages"
    drawn = _drawn(admin_client, cable, action)
    assert drawn[UNIT_SHOWN] == "m"
    _to_ft(session, cable, recorded_in="m" if converting else None)
    typed = {"package_name": "Drum", "size": "300"}
    with capture_logs() as logs:
        refused = admin_client.post(action, data=drawn | typed)
    assert refused.status_code == 400
    assert problem(refused.text, "size") == now_counted_in("ft")
    assert _hidden(refused.text, action)[UNIT_SHOWN] == "ft"
    assert [
        (line["form"], line["table"], line["row_id"])
        for line in logs
        if line["event"] == "package form refused: unit changed since shown"
    ] == [("add_package", "item", cable.id)]
    drum = select(ItemPackage).where(ItemPackage.name == "Drum")
    assert session.scalars(drum).all() == []
    again = _drawn(admin_client, cable, action)
    assert again[UNIT_SHOWN] == "ft"
    assert admin_client.post(action, data=again | typed).status_code == 303
    (added,) = session.scalars(drum).all()
    assert (added.size, added.unit) == (Decimal(300), "ft")


def test_a_too_short_length_in_a_unit_left_since_is_asked_again(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """The switch, nothing fixing its unit, took the package into ft too:
    5 typed in m is refused beside its box, nothing stored; the Keep sealed
    sent with it is not kept either."""
    cable, reel = _cable(session, radios)
    action = f"{ITEMS}/{cable.id}/packages/{reel.id}/settings"
    drawn = _drawn(admin_client, cable, action)
    assert drawn[UNIT_SHOWN] == "m"
    _to_ft(session, cable)
    typed = {"short_below": "5", "minimum_sealed": "2"}
    refused = admin_client.post(action, data=drawn | typed)
    assert refused.status_code == 400
    assert problem(refused.text, "short_below") == now_counted_in("ft")
    assert _hidden(refused.text, action)[UNIT_SHOWN] == "ft"
    session.refresh(reel)
    assert (reel.unit, reel.short_below, reel.minimum_sealed) == ("ft", None, None)
    again = _drawn(admin_client, cable, action)
    assert admin_client.post(action, data=again | typed).status_code == 303
    session.refresh(reel)
    assert (reel.short_below, reel.minimum_sealed) == (Decimal(5), 2)


def test_a_too_short_length_in_its_packages_own_unit_is_kept_once_converted(
    admin_client: TestClient, session: Session
) -> None:
    """A converting switch leaves each package in its own unit, m, as the
    page still shows its length's box: 15 typed in m is the package's,
    right as typed, so it is saved."""
    spools = Spools(session)
    cable, spool = spools.cable, spools.spool
    action = f"{ITEMS}/{cable.id}/packages/{spool.id}/settings"
    drawn = _drawn(admin_client, cable, action)
    _to_ft(session, cable, recorded_in="m")
    assert _drawn(admin_client, cable, action)[UNIT_SHOWN] == "m"
    typed = {"short_below": "15", "minimum_sealed": ""}
    assert admin_client.post(action, data=drawn | typed).status_code == 303
    session.refresh(spool)
    assert (spool.unit, spool.short_below) == ("m", Decimal(15))


def test_a_cleared_or_unshown_length_needs_no_unit(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Nothing typed in a unit is kept: a length cleared, or none sent as
    the page shows no box for it, saves whatever the form showed."""
    cable, reel = _cable(session, radios)
    reel.short_below = Decimal(5)
    action = f"{ITEMS}/{cable.id}/packages/{reel.id}/settings"
    stale = {UNIT_SHOWN: "each"}
    kept = admin_client.post(action, data=stale | {"minimum_sealed": "1"})
    assert kept.status_code == 303
    session.refresh(reel)
    assert (reel.short_below, reel.minimum_sealed) == (Decimal(5), 1)
    cleared = stale | {"short_below": "", "minimum_sealed": ""}
    assert admin_client.post(action, data=cleared).status_code == 303
    session.refresh(reel)
    assert (reel.short_below, reel.minimum_sealed) == (None, None)


def test_a_package_form_refused_otherwise_says_its_unit_changed_too(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Refused for something else, its name taken or its Keep sealed not
    whole, the page drawn again in ft keeps the number typed in m: said
    beside it, so it is not sent again unchecked and kept in ft."""
    cable, reel = _cable(session, radios)
    add = f"{ITEMS}/{cable.id}/packages"
    settings = f"{add}/{reel.id}/settings"
    drawn_add = _drawn(admin_client, cable, add)
    drawn_settings = _drawn(admin_client, cable, settings)
    _to_ft(session, cable)
    taken = admin_client.post(
        add, data=drawn_add | {"package_name": "Reel", "size": "300"}
    )
    assert taken.status_code == 400
    assert problem(taken.text, "size") == now_counted_in("ft")
    not_whole = {"short_below": "5", "minimum_sealed": "1.5"}
    refused = admin_client.post(settings, data=drawn_settings | not_whole)
    assert refused.status_code == 400
    assert problem(refused.text, "short_below") == now_counted_in("ft")


@pytest.mark.parametrize("box", ["size", "short_below"])
def test_a_package_amount_wrong_in_any_unit_says_so_with_its_unit_changed(
    admin_client: TestClient, session: Session, radios: Category, box: str
) -> None:
    """-5 typed in m, the cable in ft since: beside Holds on the add form,
    or Too short to use on the package's own, the unit changed is said
    first and the number's own problem after, so both are fixed at once;
    typed again in ft, only the number's problem is left."""
    cable, reel = _cable(session, radios)
    add = f"{ITEMS}/{cable.id}/packages"
    action = add if box == "size" else f"{add}/{reel.id}/settings"
    drawn = _drawn(admin_client, cable, action)
    _to_ft(session, cable)
    others = {"package_name": "Drum"} if box == "size" else {"minimum_sealed": ""}
    typed = others | {box: "-5"}
    refused = admin_client.post(action, data=drawn | typed)
    assert refused.status_code == 400
    assert problem(refused.text, box) == f"{now_counted_in('ft')} {ABOVE_ZERO}"
    again = admin_client.post(action, data=_hidden(refused.text, action) | typed)
    assert again.status_code == 400
    assert problem(again.text, box) == str(ABOVE_ZERO)


def test_a_package_added_with_no_size_says_so_with_its_unit_changed(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Holds left blank on a form drawn in m, the cable in ft since: a size
    is asked for as well as the unit changed said."""
    cable, _ = _cable(session, radios)
    add = f"{ITEMS}/{cable.id}/packages"
    drawn = _drawn(admin_client, cable, add)
    _to_ft(session, cable)
    typed = {"package_name": "Drum", "size": ""}
    refused = admin_client.post(add, data=drawn | typed)
    assert refused.status_code == 400
    assert problem(refused.text, "size") == f"{now_counted_in('ft')} {SIZE_REQUIRED}"


def test_a_package_amount_wrong_only_in_the_unit_left_says_just_its_unit_changed(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """What is wrong only as read in the new unit is not said with it, as
    the number is to be typed again in that unit: 2.5 typed in m, the cable
    counted each since, is not said to be not whole; 400 typed in m beside
    a Reel of 305 ft is not said to be longer than a whole one."""
    cable, reel = _cable(session, radios)
    add = f"{ITEMS}/{cable.id}/packages"
    settings = f"{add}/{reel.id}/settings"
    drawn_add = _drawn(admin_client, cable, add)
    drawn_settings = _drawn(admin_client, cable, settings)
    _to_ft(session, cable)
    long = {"short_below": "400", "minimum_sealed": ""}
    refused = admin_client.post(settings, data=drawn_settings | long)
    assert refused.status_code == 400
    assert problem(refused.text, "short_below") == now_counted_in("ft")
    _switch(session, cable, "each")
    fraction = {"package_name": "Drum", "size": "2.5"}
    refused = admin_client.post(add, data=drawn_add | fraction)
    assert refused.status_code == 400
    assert problem(refused.text, "size") == now_counted_in("each")
