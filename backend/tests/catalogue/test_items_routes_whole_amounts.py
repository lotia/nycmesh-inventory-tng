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
from httpx2 import Response
from sqlalchemy.orm import Session

from inventory_tng.catalogue import items
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.web.forms import WHOLE_UNITS

from helpers.items import NOW, new_item, shown_item_form
from helpers.markup import problem, read
from helpers.rows import add, history, item_fields, shown


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


# What a refused switch to each says of the Reel package as each case
# leaves it (Ali, 2026-11-23).
FRACTIONS = [
    pytest.param(
        {"size": Decimal("30.5"), "short_below": Decimal("1.5")},
        "The Reel package holds 30.5: change that to a whole number, or retire "
        "the package, before switching the item count to each.",
        id="holds",
    ),
    pytest.param(
        {"short_below": Decimal("1.5")},
        "The Reel package has a fraction (1.5): set it to a whole number from 1 "
        "to 304 before switching the item count to each.",
        id="too-short",
    ),
]


@pytest.mark.parametrize(("package", "switching"), FRACTIONS)
def test_a_change_to_each_is_refused_while_a_package_has_a_fraction(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    package: dict[str, object],
    switching: str,
) -> None:
    """Said beside the unit, naming the package and the amount, with a
    decimal Reorder at refused beside it too."""
    cable, _reel = _packaged(session, radios, **package)
    form = shown_item_form(cable, unit="each", minimum="2.5")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=form)
    session.refresh(cable)
    assert refused.status_code == 400
    assert problem(refused.text, "unit") == switching
    assert problem(refused.text, "minimum") == str(WHOLE_UNITS)
    assert cable.unit == "m"


def _outlined(page: str) -> list[dict[str, str]]:
    """The Too short to use boxes outlined on the page, as their
    attributes."""
    boxes = read(page).all("input", name="short_below")
    return [box.attrs for box in boxes if box.attrs.get("aria-invalid")]


@pytest.mark.parametrize(
    ("package", "outlined"),
    [
        ({"short_below": Decimal("1.5")}, True),
        # Its Holds is said instead.
        ({"size": Decimal("30.5"), "short_below": Decimal("1.5")}, False),
    ],
)
def test_the_too_short_box_a_refused_switch_means_is_outlined(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    package: dict[str, object],
    outlined: bool,
) -> None:
    """Only that package's, tied to the words beside the unit; none on
    the page as first shown."""
    cable, _reel = _packaged(session, radios, **package)
    add(session, ItemPackage(item_id=cable.id, name="Drum", size=Decimal(500)))
    assert _outlined(admin_client.get(f"{ITEMS}/{cable.id}").text) == []
    form = shown_item_form(cable, unit="each")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=form)
    boxes = _outlined(refused.text)
    if not outlined:
        assert boxes == []
        return
    [box] = boxes
    assert box["aria-invalid"] == "true"
    assert box["aria-describedby"] == "unit-problem"
    assert box["value"] == str(package["short_below"])


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


def test_a_switch_to_each_clears_a_too_short_length_no_whole_number_fits(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A package holding 1 can only be too short to use below a fraction,
    which catches only an empty one: cleared by itself as the item saves,
    with nothing said, its history keeping it (Ali, 2026-11-23)."""
    cable, reel = _packaged(
        session, radios, size=Decimal(1), short_below=Decimal("0.5")
    )
    form = shown_item_form(cable, unit="each")
    done = admin_client.post(f"{ITEMS}/{cable.id}", data=form)
    session.refresh(cable)
    session.refresh(reel)
    assert done.headers["location"] == f"{ITEMS}/{cable.id}?notice=saved"
    assert (cable.unit, reel.short_below) == ("each", None)
    cleared = history(session, "item_package", reel.id)[-1]
    assert cleared.old_row is not None
    assert cleared.new_row is not None
    assert (cleared.old_row["short_below"], cleared.new_row["short_below"]) == (
        0.5,
        None,
    )


def _restore(
    admin_client: TestClient, session: Session, radios: Category, **package: object
) -> tuple[Response, ItemPackage]:
    """The retired Reel package, as `package` says, brought back to a cable
    counted each."""
    cable, reel = _packaged(session, radios, retired_at=NOW, **package)
    cable.unit = "each"
    session.flush()
    sent = admin_client.post(f"{ITEMS}/{cable.id}/packages/{reel.id}/restore")
    session.refresh(reel)
    return sent, reel


def test_a_retired_package_too_short_to_use_below_a_fraction_stays_retired(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Said above the page, naming it and the whole numbers it may be."""
    refused, reel = _restore(admin_client, session, radios, short_below=Decimal("1.5"))
    assert refused.status_code == 409
    assert read(refused.text).one("p", role="alert").text == (
        "The Reel package has a fraction (1.5): set it to a whole number from 1 "
        "to 304 before bringing it back."
    )
    assert reel.retired_at == NOW


def test_a_retired_package_holding_a_fraction_is_added_again_instead(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """It has no Holds box while retired: the alert links to the Add
    package form, whose Holds box is outlined and focused (Ali,
    2026-11-23). Its name is still its own, retired ones' too, so the
    form's Package box is left empty."""
    refused, reel = _restore(
        admin_client, session, radios, size=Decimal("30.5"), short_below=Decimal("1.5")
    )
    page = read(refused.text)
    alert = page.one("p", role="alert")
    assert refused.status_code == 409
    assert alert.text == (
        "The Reel package holds 30.5, which an item counted in each can't hold: "
        "add it again with a whole number."
    )
    assert alert.attrs["id"] == "held-back"
    link = alert.one("a")
    assert (link.text, link.attrs["href"]) == (
        "add it again with a whole number",
        "#add-package",
    )
    form = page.one("form", id="add-package")
    assert form.one("input", name="package_name").attrs["value"] == ""
    holds = form.one("input", name="size").attrs
    assert holds["aria-invalid"] == "true"
    assert holds["aria-describedby"] == "held-back"
    assert "autofocus" in holds
    assert (reel.retired_at, reel.size) == (NOW, Decimal("30.5"))


def test_a_retired_package_holding_1_comes_back_without_its_too_short_length(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """As a switch to each clears it: nothing said, its history keeping
    it."""
    done, reel = _restore(
        admin_client, session, radios, size=Decimal(1), short_below=Decimal("0.5")
    )
    assert done.status_code == 303
    assert (reel.retired_at, reel.short_below) == (None, None)
    brought_back = history(session, "item_package", reel.id)[-1]
    assert brought_back.old_row is not None
    assert brought_back.old_row["short_below"] == 0.5


def test_the_add_package_forms_holds_box_is_not_outlined_on_first_show(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    cable = new_item(session, radios, unit="each")
    form = read(admin_client.get(f"{ITEMS}/{cable.id}").text).one(
        "form", id="add-package"
    )
    attrs = form.one("input", name="size").attrs
    assert not {"aria-invalid", "autofocus"} & attrs.keys()
