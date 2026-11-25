"""The item page where opened packages are not followed
(catalogue/items_routes.py): Too short to use shown only where they are,
and turning following off, on the item, a package or the items ticked,
says how many open packages it closed (Ali, 2026-11-23,
inventory-tng-j89e)."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from inventory_tng.catalogue import forms, reading_rules
from inventory_tng.catalogue.items_routes import closed_loose
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.core.db_errors import RuleBroken
from inventory_tng.models.catalogue import Category, ItemPackage
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import ledger, open_packages
from inventory_tng.stock.ledger import Entry, Form
from inventory_tng.web.forms import RULES

from helpers.items import new_item, shown_item_form
from helpers.markup import box_values, words_on
from helpers.rows import add
from helpers.stock import Spools


def then[**P, R](
    call: Callable[P, R], also: Callable[[], object], *, first: bool = False
) -> Callable[P, R]:
    """`call`, and `also` done after it, or before it if `first`."""

    def both(*args: P.args, **kwargs: P.kwargs) -> R:
        if first:
            also()
        done = call(*args, **kwargs)
        if not first:
            also()
        return done

    return both


def test_too_short_to_use_is_shown_only_where_opened_ones_are_followed(
    admin_client: TestClient, session: Session
) -> None:
    """Hidden, its value is kept, and comes back with the box."""
    spools = Spools(session, tracked=False)
    spools.spool.short_below = Decimal(5)
    session.flush()
    page = f"{ITEMS}/{spools.cable.id}"
    assert box_values(admin_client.get(page).text, "short_below") == []
    settings = f"{page}/packages/{spools.spool.id}/settings"
    assert admin_client.post(settings, data={"minimum_sealed": "2"}).status_code == 303
    session.refresh(spools.spool)
    assert (spools.spool.short_below, spools.spool.minimum_sealed) == (Decimal(5), 2)
    tracking = f"{page}/packages/{spools.spool.id}/tracking"
    admin_client.post(tracking, data={"track": "on"})
    assert box_values(admin_client.get(page).text, "short_below") == ["5"]


def test_a_hidden_too_short_to_use_keeps_what_was_set_meanwhile(
    admin_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kept as it is once the package is locked, not as the page's
    request first read it: set meanwhile, it is not undone."""
    spools = Spools(session, tracked=False)
    spools.spool.short_below = Decimal(5)
    session.flush()

    def set_meanwhile() -> None:
        session.execute(
            update(ItemPackage)
            .where(ItemPackage.id == spools.spool.id)
            .values(short_below=Decimal(7))
        )

    monkeypatch.setattr(
        forms,
        "package_settings_fields",
        then(forms.package_settings_fields, set_meanwhile),
    )
    page = f"{ITEMS}/{spools.cable.id}/packages/{spools.spool.id}/settings"
    assert admin_client.post(page, data={"minimum_sealed": "2"}).status_code == 303
    session.refresh(spools.spool)
    assert (spools.spool.short_below, spools.spool.minimum_sealed) == (Decimal(7), 2)


def test_a_box_to_set_whole_is_shown_where_opened_ones_are_not_followed(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A switch to each refused for its fraction outlines it, so it can be
    set whole (inventory-tng-fa5i)."""
    cable = new_item(session, radios, name="Cable", unit="m")
    add(
        session,
        ItemPackage(
            item_id=cable.id, name="Reel", size=Decimal(305), short_below=Decimal("2.5")
        ),
    )
    page = f"{ITEMS}/{cable.id}"
    assert box_values(admin_client.get(page).text, "short_below") == []
    refused = admin_client.post(page, data=shown_item_form(cable, unit="each"))
    assert refused.status_code == 400
    assert box_values(refused.text, "short_below") == ["2.5"]


def test_a_resize_refused_for_a_hidden_too_short_to_use_shows_its_box(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Refused as at or below it, its box is shown, so it can be changed
    first."""
    cable = new_item(session, radios, name="Cable", unit="m")
    reel = add(
        session,
        ItemPackage(
            item_id=cable.id, name="Reel", size=Decimal(50), short_below=Decimal(20)
        ),
    )
    page = f"{ITEMS}/{cable.id}"
    refused = admin_client.post(
        f"{page}/packages/{reel.id}",
        data={"package_name": "Reel", "size": "20", "package_version": "1"},
    )
    assert refused.status_code == 400
    assert box_values(refused.text, "short_below") == ["20"]


def test_turning_an_item_off_says_how_many_open_packages_closed(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """At two places: a spool cut in the store, another moved whole to the
    van."""
    cable = spools.cable
    in_store, in_van = spools.opened(), spools.opened()
    spools.move(Entry(cable.id, Decimal(55), Form(open_package_id=in_store.id)))
    whole = Form(open_package_id=in_van.id)
    spools.move(Entry(cable.id, Decimal(305), whole, whole))
    page = f"{ITEMS}/{cable.id}"
    # Unticked, the box is not sent: the form says off.
    done = admin_client.post(page, data=shown_item_form(cable))
    assert done.headers["location"] == f"{page}?closed=2&notice=saved"
    said = words_on(admin_client.get(done.headers["location"]).text)
    assert "2 open packages closed; what was left is now loose." in said
    session.refresh(in_store)
    session.refresh(in_van)
    assert in_store.closed_at is not None
    assert in_van.closed_at is not None
    # Saved again, none is closed, and it says only that it saved.
    session.refresh(cable)
    again = admin_client.post(page, data=shown_item_form(cable))
    assert again.headers["location"] == f"{page}?notice=saved"


def test_a_package_or_the_items_ticked_turned_off_say_so_too(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    cable = spools.cable
    first = spools.opened()
    page = f"{ITEMS}/{cable.id}"
    tracking = f"{page}/packages/{spools.spool.id}/tracking"
    done = admin_client.post(tracking, data={"track": "off"})
    assert done.headers["location"] == (
        f"{page}?closed=1&notice=package_tracking&section=packages#packages"
    )
    said = words_on(admin_client.get(done.headers["location"]).text)
    assert str(closed_loose(1)) in said
    assert "1 open package closed; what was left is now loose." in said
    session.refresh(first)
    assert first.closed_at is not None

    admin_client.post(tracking, data={"track": ""})
    second = spools.opened()
    ticked = admin_client.post(
        f"{ITEMS}/tracking?q=cable",
        data={"item": [str(cable.id)], "track": "off"},
    )
    assert ticked.headers["location"] == (
        f"{ITEMS}?q=cable&closed=1&notice=tracking_off"
    )
    assert "1 open package closed" in words_on(
        admin_client.get(ticked.headers["location"]).text
    )
    session.refresh(second)
    assert second.closed_at is not None
    assert open_packages.shown(session, OpenPackage.item_id == cable.id) == []


def test_items_ticked_with_an_open_package_moved_meanwhile_are_refused(
    admin_client: TestClient,
    session: Session,
    spools: Spools,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moved whole to a place not locked first (open_packages.
    close_unfollowed): nothing changes, and the list says so."""

    def moved_meanwhile(*_: object, **__: object) -> int:
        raise RuleBroken("changed_since_shown")

    monkeypatch.setattr(open_packages, "close_unfollowed", moved_meanwhile)
    refused = admin_client.post(
        f"{ITEMS}/tracking", data={"item": [str(spools.cable.id)], "track": "off"}
    )
    assert refused.headers["location"] == f"{ITEMS}?notice=tracking_changed"
    assert str(RULES["changed_since_shown"]) in words_on(
        admin_client.get(refused.headers["location"]).text
    )
    session.refresh(spools.cable)
    assert spools.cable.track_opened is True


def test_an_item_saved_with_answers_locks_its_places_before_reading_again(
    admin_client: TestClient,
    session: Session,
    spools: Spools,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answers' reading rules lock the items they read again FOR
    UPDATE (items.reread), which a save at their open packages' places
    holds after those places: so the places are taken first, or the two
    could deadlock."""
    taken: list[str] = []
    places = then(ledger.lock_places, lambda: taken.append("places"), first=True)
    rules = then(reading_rules.decide, lambda: taken.append("rules"), first=True)
    monkeypatch.setattr(ledger, "lock_places", places)
    monkeypatch.setattr(reading_rules, "decide", rules)
    row = spools.opened()
    cable = spools.cable
    form = shown_item_form(cable, name="Cable 10g") | {"unit:g": "Gbit/s"}
    assert admin_client.post(f"{ITEMS}/{cable.id}", data=form).status_code == 303
    assert taken == ["places", "rules"]
    session.refresh(row)
    assert row.closed_at is not None


def test_items_ticked_set_so_already_say_the_open_packages_closed(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """Already off, as an open package made before following was turned
    off is left: closed, so said, not "nothing changed"."""
    row = spools.opened()
    spools.cable.track_opened = False
    session.flush()
    ticked = admin_client.post(
        f"{ITEMS}/tracking", data={"item": [str(spools.cable.id)], "track": "off"}
    )
    assert ticked.headers["location"] == f"{ITEMS}?closed=1&notice=tracking_off"
    session.refresh(row)
    assert row.closed_at is not None


def test_a_link_cannot_say_open_packages_closed_after_another_notice(
    admin_client: TestClient, spools: Spools
) -> None:
    page = f"{ITEMS}/{spools.cable.id}?notice=package_added&closed=40"
    assert "open packages closed" not in words_on(admin_client.get(page).text)
