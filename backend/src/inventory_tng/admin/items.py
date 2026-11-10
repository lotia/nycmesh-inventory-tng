"""Items and the packages they come in. An item's unit is fixed once stock
has moved in it (items.save): the form shows it, and does not offer to
change it. A retired item with stock left may still be taken, never
received (docs/decisions/0003)."""

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import items, labels
from inventory_tng.admin.categories import CATEGORIES
from inventory_tng.admin.common import (
    ADMIN,
    ALERTS,
    RETIRED,
    RULES,
    admin_page,
    chosen,
    form_version,
    name_problem,
    now,
    quantity,
    retirement,
    rule_words,
    text_form,
    undo_retire,
)
from inventory_tng.db import DbSession, exists_where, refusal
from inventory_tng.ledger import balance
from inventory_tng.models import UNITS, Category, Item, ItemPackage, Label, Movement
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

ITEMS = f"{ADMIN}/items"
TEXT_FIELDS = ("manufacturer", "part_number", "old_code", "notes", "ordering_notes")
TAKEN = (
    "Another item already has that name, that manufacturer and part number, "
    "or that old code."
)
RESTORE_TAKEN = "A current item already has its name or part number: change one first."


# The rules a change to an item may break, and the field each is shown by.
REFUSED_FIELDS = {
    "unique": "name",
    "item_category_current": "category_id",
    "item_unit_fixed": "unit",
}


def _change_or_note[T](
    session: Session, change: Callable[[], T], problems: dict[str, str]
) -> tuple[T | None, str | None]:
    """Makes the change: what it did, and None; or, if it breaks one of
    REFUSED_FIELDS' rules, None, and the rule, which `problems` then says
    beside its field, or one of ALERTS."""
    done, broken = refusal(session, change, *REFUSED_FIELDS, *ALERTS)
    if broken in REFUSED_FIELDS:
        problems[REFUSED_FIELDS[broken]] = rule_words(broken, TAKEN)
    return done, broken


NOTICES.update(
    {
        "package_added": "Package added.",
        "package_retired": "Package retired.",
    }
)

TextForm = Annotated[dict[str, str], Depends(text_form)]


def _categories(session: Session) -> list[Category]:
    """Every category, by name: the current ones are offered."""
    return list(session.scalars(select(Category).order_by(func.lower(Category.name))))


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    search: str = "",
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    undo: str | None = None,
) -> HTMLResponse:
    query = select(Item).order_by(func.lower(Item.name))
    if search:
        query = query.where(Item.matches(search))
    rows = session.scalars(query).all()
    categories = _categories(session)
    return admin_page(
        request,
        signed,
        "items.html",
        {
            "title": "Items",
            "undo": undo,
            "here": ITEMS,
            "categories_path": CATEGORIES,
            "search": search,
            "current": [i for i in rows if i.retired_at is None],
            "retired": [i for i in rows if i.retired_at is not None],
            "categories": [c for c in categories if c.retired_at is None],
            "category_names": {c.id: c.name for c in categories},
            "units": UNITS,
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


def _in_stock(session: Session, item_id: int) -> Decimal:
    """How much of it there is, everywhere together."""
    total = session.scalar(
        select(func.sum(balance.c.quantity)).where(balance.c.item_id == item_id)
    )
    return Decimal(total or 0)


def _moved(session: Session, item_id: int) -> bool:
    return exists_where(session, Movement.item_id == item_id)


def _item_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    item: Item,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    alert: str | None = None,
) -> HTMLResponse:
    packages = session.scalars(
        select(ItemPackage)
        .where(ItemPackage.item_id == item.id)
        .order_by(ItemPackage.retired_at.is_not(None), ItemPackage.size)
    ).all()
    return admin_page(
        request,
        signed,
        "item.html",
        {
            "title": item.name,
            "here": f"{ITEMS}/{item.id}",
            "back": (ITEMS, "Items"),
            "item": item,
            **retirement(session, item),
            "packages": packages,
            "categories": [c for c in _categories(session) if c.retired_at is None],
            "units": UNITS,
            "unit_fixed": _moved(session, item.id),
            "in_stock": _in_stock(session, item.id),
            "labels": labels.current_of(session, Label.item_id == item.id),
            "problems": problems,
            "values": values,
            "alert": alert,
        },
    )


def _fields(
    session: Session, form: Mapping[str, str]
) -> tuple[items.Fields | None, dict[str, str]]:
    """What to set, unless something is wrong, and what is."""
    name = form.get("name", "").strip()
    problems: dict[str, str] = {}
    if problem := name_problem(name):
        problems["name"] = problem
    category = chosen(session, Category, form.get("category_id", ""))
    if category is None:
        problems["category_id"] = "Pick a category."
    unit = form.get("unit", "")
    if unit not in UNITS:
        problems["unit"] = "Pick a unit."
    minimum = quantity(form.get("minimum", ""), positive=False)
    if isinstance(minimum, str):
        problems["minimum"] = minimum
    # Each None or str here is a problem already; checked again for the types.
    if problems or category is None or isinstance(minimum, str):
        return None, problems
    texts = {field: form.get(field, "").strip() or None for field in TEXT_FIELDS}
    fields = items.Fields(
        name=name, category_id=category.id, unit=unit, minimum=minimum, **texts
    )
    return fields, problems


@router.get(ITEMS)
def list_items(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    q: str = "",
    undo: str = "",
) -> Response:
    back = undo_retire(session, Item, undo, ITEMS)
    return _list_page(request, session, signed, search=q.strip(), undo=back)


@router.post(ITEMS)
def add_item(
    request: Request, signed: AdminSignedIn, session: DbSession, form: TextForm
) -> Response:
    fields, problems = _fields(session, form)
    item = None
    if fields is not None:
        item, _ = _change_or_note(session, lambda: items.add(session, fields), problems)
    if item is None:
        return _list_page(request, session, signed, problems=problems, values=form)
    session.commit()
    log.info("item added", item_id=item.id)
    return redirect(f"{ITEMS}/{item.id}", notice="added")


@router.get(f"{ITEMS}/{{item_id}}")
def item(
    request: Request, signed: AdminSignedIn, session: DbSession, item_id: int
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    return _item_page(request, session, signed, found)


@router.post(f"{ITEMS}/{{item_id}}")
def save_item(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    form: TextForm,
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    fields, problems = _fields(session, form)
    if fields is not None:
        seen = form_version(form.get("version", ""))
        _, broken = _change_or_note(
            session, lambda: items.save(session, found, fields, seen=seen), problems
        )
        if broken in ALERTS:
            alert = RULES[broken]
            return _item_page(request, session, signed, found, alert=alert)
    if problems:
        return _item_page(
            request, session, signed, found, problems=problems, values=form
        )
    session.commit()
    log.info("item saved", item_id=item_id)
    return redirect(f"{ITEMS}/{item_id}", notice="saved")


@router.post(f"{ITEMS}/{{item_id}}/retire")
def retire_item(signed: AdminSignedIn, session: DbSession, item_id: int) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    items.retire(session, found, now())
    session.commit()
    log.info("item retired", item_id=item_id)
    return redirect(ITEMS, notice="retired", undo=str(item_id))


@router.post(f"{ITEMS}/{{item_id}}/restore")
def restore_item(
    request: Request, signed: AdminSignedIn, session: DbSession, item_id: int
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    _, broken = refusal(
        session,
        lambda: items.restore(session, found),
        "unique",
        "item_category_current",
    )
    # Beside the name: the category list shows only current categories.
    if broken:
        problem = rule_words(broken, RESTORE_TAKEN)
        return _item_page(request, session, signed, found, problems={"name": problem})
    session.commit()
    log.info("item restored", item_id=item_id)
    return redirect(f"{ITEMS}/{item_id}", notice="restored")


@router.post(f"{ITEMS}/{{item_id}}/packages")
def add_package(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_name: Annotated[str, Form()] = "",
    size: Annotated[str, Form()] = "",
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    name = package_name.strip()
    problems: dict[str, str] = {}
    if problem := name_problem(name):
        problems["package_name"] = problem
    parsed = quantity(
        size, positive=True, required="How many of the item's unit it holds."
    )
    if isinstance(parsed, str):
        problems["size"] = parsed
    if not problems:
        assert isinstance(parsed, Decimal)  # Checked above; narrows the type.
        package, broken = refusal(
            session,
            lambda: items.add_package(session, found, name, parsed),
            "unique",
            RETIRED,
        )
        if broken == RETIRED:
            return _item_page(request, session, signed, found, alert=RULES[RETIRED])
        if package is not None:
            session.commit()
            log.info("package added", item_package_id=package.id)
            return redirect(
                f"{ITEMS}/{item_id}", notice="package_added", section="packages"
            )
        problems["package_name"] = "It already has a package by that name."
    values = {"package_name": name, "size": size}
    return _item_page(request, session, signed, found, problems=problems, values=values)


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}/retire")
def retire_package(
    signed: AdminSignedIn, session: DbSession, item_id: int, package_id: int
) -> Response:
    package = session.get(ItemPackage, package_id)
    if package is None or package.item_id != item_id:
        return not_found()
    items.retire_package(session, package, now())
    session.commit()
    log.info("package retired", item_package_id=package_id)
    return redirect(f"{ITEMS}/{item_id}", notice="package_retired", section="packages")
