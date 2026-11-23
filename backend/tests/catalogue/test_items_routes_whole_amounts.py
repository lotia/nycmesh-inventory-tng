"""An item counted each has whole amounts on its page: its Reorder at
refuses a decimal, and its amount boxes show the number keypad, while an
item in another unit keeps decimals and the decimal keypad (Ali,
2026-11-22; inventory-tng-qt6z). Nor is an item changed to each while a
current package of it holds, or is too short to use below, a fraction
(Ali, 2026-11-23; inventory-tng-fa5i)."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue import items
from inventory_tng.catalogue.forms import package_fraction
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.web.forms import WHOLE_UNITS

from helpers.items import NOW, new_item, shown_item_form
from helpers.markup import problem, read
from helpers.rows import add, item_fields, shown


@pytest.mark.parametrize(("unit", "keyboard"), [("each", "numeric"), ("m", "decimal")])
def test_its_amount_boxes_show_the_keypad_its_unit_takes(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    unit: str,
    keyboard: str,
) -> None:
    item = new_item(session, radios, unit=unit)
    add(session, ItemPackage(item_id=item.id, name="Box", size=Decimal(10)))
    boxes = read(admin_client.get(f"{ITEMS}/{item.id}").text)
    assert boxes.one("input", name="minimum").attrs["inputmode"] == keyboard
    assert boxes.one("input", name="short_below").attrs["inputmode"] == keyboard
    # The package's Holds, and the new package's.
    sizes = boxes.all("input", name="size")
    assert [box.attrs["inputmode"] for box in sizes] == [keyboard, keyboard]


@pytest.mark.parametrize(
    ("unit", "status", "kept"), [("each", 400, None), ("m", 303, Decimal("2.5"))]
)
def test_reorder_at_is_whole_for_an_item_counted_each(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    unit: str,
    status: int,
    kept: Decimal | None,
) -> None:
    item = new_item(session, radios, unit=unit)
    sent = admin_client.post(
        f"{ITEMS}/{item.id}", data=shown_item_form(item, minimum="2.5")
    )
    session.refresh(item)
    assert sent.status_code == status
    assert item.minimum == kept


def test_reorder_at_is_checked_against_the_unit_sent(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A change to each with a decimal Reorder at is refused, and the box
    comes back with the keypad of the unit it was checked against."""
    item = new_item(session, radios, unit="m")
    form = shown_item_form(item, unit="each", minimum="2.5")
    refused = admin_client.post(f"{ITEMS}/{item.id}", data=form)
    session.refresh(item)
    assert refused.status_code == 400
    assert problem(refused.text, "minimum") == str(WHOLE_UNITS)
    assert (item.unit, item.minimum) == ("m", None)
    box = read(refused.text).one("input", name="minimum")
    assert box.attrs["inputmode"] == "numeric"


def _packaged(
    session: Session, radios: Category, **package: object
) -> tuple[Item, ItemPackage]:
    """A cable in m with a Reel package, as `package` says."""
    cable = new_item(session, radios, name="Cable", unit="m")
    reel = ItemPackage(item_id=cable.id, name="Reel", size=Decimal(305))
    for column, value in package.items():
        setattr(reel, column, value)
    return cable, add(session, reel)


@pytest.mark.parametrize(
    ("package", "amount"),
    [
        ({"size": Decimal("30.5")}, "30.5"),
        ({"short_below": Decimal("1.5")}, "1.5"),
    ],
)
def test_a_change_to_each_is_refused_while_a_package_has_a_fraction(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    package: dict[str, object],
    amount: str,
) -> None:
    """Said beside the unit, naming the package and the amount, with a
    decimal Reorder at refused beside it too."""
    cable, _ = _packaged(session, radios, **package)
    form = shown_item_form(cable, unit="each", minimum="2.5")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=form)
    session.refresh(cable)
    assert refused.status_code == 400
    said = problem(refused.text, "unit")
    assert said == package_fraction(session, cable.id)
    assert "Reel" in said
    assert amount in said
    assert problem(refused.text, "minimum") == str(WHOLE_UNITS)
    assert cable.unit == "m"


@pytest.mark.parametrize(
    ("unit", "package"),
    [
        # A retired package may keep its fraction.
        ("each", {"size": Decimal("30.5"), "retired_at": NOW}),
        ("each", {"short_below": Decimal(2)}),
        # Only each is counted whole.
        ("ft", {"size": Decimal("30.5"), "short_below": Decimal("1.5")}),
    ],
)
def test_a_unit_change_is_saved_while_no_current_package_breaks_it(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    unit: str,
    package: dict[str, object],
) -> None:
    cable, _ = _packaged(session, radios, **package)
    form = shown_item_form(cable, unit=unit)
    assert admin_client.post(f"{ITEMS}/{cable.id}", data=form).status_code == 303
    session.refresh(cable)
    assert cable.unit == unit


def _first(
    monkeypatch: pytest.MonkeyPatch, name: str, meanwhile: Callable[[Session], object]
) -> None:
    """items' `name` does `meanwhile` first, as another request committed
    just before it took the catalogue lock."""
    real = getattr(items, name)

    def after(session: Session, *args: object, **kwargs: object) -> object:
        meanwhile(session)
        return real(session, *args, **kwargs)

    monkeypatch.setattr(items, name, after)


def test_a_fraction_on_a_package_meanwhile_refuses_a_change_to_each(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked again under the lock: a package given a fraction after the
    form was checked."""
    cable, reel = _packaged(session, radios)

    def short(s: Session) -> None:
        items.package_settings(s, s.get_one(ItemPackage, reel.id), Decimal("1.5"), None)

    _first(monkeypatch, "save", short)
    form = shown_item_form(cable, unit="each")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=form)
    session.refresh(cable)
    assert refused.status_code == 400
    # The savepoint undid that too, so none is named now.
    assert problem(refused.text, "unit") == str(WHOLE_UNITS)
    assert cable.unit == "m"


@pytest.mark.parametrize(
    ("name", "path", "sent", "field"),
    [
        ("add_package", "packages", {"package_name": "Drum", "size": "30.5"}, "size"),
        (
            "save_package",
            "packages/{id}",
            {"package_name": "Reel", "size": "30.5"},
            "size",
        ),
        (
            "package_settings",
            "packages/{id}/settings",
            {"short_below": "1.5", "minimum_sealed": ""},
            "short_below",
        ),
    ],
)
def test_a_package_fraction_is_refused_once_its_item_is_each_meanwhile(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    path: str,
    sent: dict[str, str],
    field: str,
) -> None:
    """Its form checked the amount against the unit before the lock: the
    unit is read again under it."""
    cable, reel = _packaged(session, radios)

    def to_each(s: Session) -> None:
        item = s.get_one(Item, cable.id)
        items.save(s, item, item_fields(item, unit="each"), seen=None)

    _first(monkeypatch, name, to_each)
    data = sent | {"package_version": shown(reel)}
    refused = admin_client.post(
        f"{ITEMS}/{cable.id}/" + path.format(id=reel.id), data=data
    )
    session.refresh(reel)
    assert refused.status_code == 400
    assert problem(refused.text, field) == str(WHOLE_UNITS)
    assert (reel.size, reel.short_below) == (Decimal(305), None)
