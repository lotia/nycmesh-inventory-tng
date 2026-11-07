"""Items and the packages they come in. An item's unit is fixed once stock
has moved in it: the ledger's quantities are in that unit. A retired item
with stock left may still be taken, never received (docs/decisions/0003)."""

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.admin.categories import CATEGORIES
from inventory_tng.admin.common import (
    ADMIN,
    admin_page,
    chosen,
    locked_row,
    name_problem,
    now,
    quantity,
    rule_words,
    text_form,
)
from inventory_tng.db import DbSession, exists_where, get_locked, refusal, unique
from inventory_tng.ledger import balance
from inventory_tng.models import UNITS, Category, Item, ItemPackage, Movement
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


def _change_or_note(
    session: Session, change: Callable[[], object], problems: dict[str, str]
) -> None:
    """Makes the change, unless its name is taken or its category retired:
    then says so in `problems`, beside that field."""
    broken = refusal(session, change, "unique", "item_category_current")
    if broken is not None:
        field = "name" if broken == "unique" else "category_id"
        problems[field] = rule_words(broken, TAKEN)


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
            "list": ITEMS,
            "item": item,
            "packages": packages,
            "categories": [c for c in _categories(session) if c.retired_at is None],
            "units": UNITS,
            "unit_fixed": _moved(session, item.id),
            "in_stock": _in_stock(session, item.id),
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


def _fields(
    session: Session, form: Mapping[str, str], *, unit_fixed: bool = False
) -> tuple[dict[str, object], dict[str, str]]:
    """What to set, and what is wrong."""
    values: dict[str, object] = {"name": form.get("name", "").strip()}
    problems: dict[str, str] = {}
    if problem := name_problem(str(values["name"])):
        problems["name"] = problem
    for field in TEXT_FIELDS:
        values[field] = form.get(field, "").strip() or None
    category = chosen(session, Category, form.get("category_id", ""))
    if category is None:
        problems["category_id"] = "Pick a category."
    else:
        values["category_id"] = category.id
    if not unit_fixed:
        if form.get("unit") not in UNITS:
            problems["unit"] = "Pick a unit."
        else:
            values["unit"] = form["unit"]
    minimum = quantity(form.get("minimum", ""), positive=False)
    if isinstance(minimum, str):
        problems["minimum"] = minimum
    else:
        values["minimum"] = minimum
    return values, problems


@router.get(ITEMS)
def items(
    request: Request, signed: AdminSignedIn, session: DbSession, q: str = ""
) -> Response:
    return _list_page(request, session, signed, search=q.strip())


@router.post(ITEMS)
def add_item(
    request: Request, signed: AdminSignedIn, session: DbSession, form: TextForm
) -> Response:
    values, problems = _fields(session, form)
    item = Item(**values)
    if not problems:
        _change_or_note(session, lambda: session.add(item), problems)
    if problems:
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
    found = locked_row(session, Item, item_id)
    if found is None:
        return not_found()
    values, problems = _fields(session, form, unit_fixed=_moved(session, item_id))

    def save() -> None:
        for field, value in values.items():
            setattr(found, field, value)

    if not problems:
        _change_or_note(session, save, problems)
    if problems:
        return _item_page(
            request, session, signed, found, problems=problems, values=form
        )
    session.commit()
    log.info("item saved", item_id=item_id)
    return redirect(f"{ITEMS}/{item_id}", notice="saved")


@router.post(f"{ITEMS}/{{item_id}}/retire")
def retire_item(signed: AdminSignedIn, session: DbSession, item_id: int) -> Response:
    found = locked_row(session, Item, item_id)
    if found is None or found.retired_at is not None:
        return not_found()
    found.retired_at = now()
    session.commit()
    log.info("item retired", item_id=item_id)
    return redirect(ITEMS, notice="retired")


@router.post(f"{ITEMS}/{{item_id}}/restore")
def restore_item(
    request: Request, signed: AdminSignedIn, session: DbSession, item_id: int
) -> Response:
    found = locked_row(session, Item, item_id)
    if found is None or found.retired_at is None:
        return not_found()

    def restore() -> None:
        found.retired_at = None

    # Beside the name: the category list shows only current categories.
    if broken := refusal(session, restore, "unique", "item_category_current"):
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
    found = locked_row(session, Item, item_id)
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
        package = ItemPackage(item_id=item_id, name=name, size=parsed)
        if unique(session, lambda: session.add(package)):
            session.commit()
            log.info("package added", item_package_id=package.id)
            return redirect(f"{ITEMS}/{item_id}", notice="package_added")
        problems["package_name"] = "It already has a package by that name."
    values = {"package_name": name, "size": size}
    return _item_page(request, session, signed, found, problems=problems, values=values)


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}/retire")
def retire_package(
    signed: AdminSignedIn, session: DbSession, item_id: int, package_id: int
) -> Response:
    package = get_locked(session, ItemPackage, package_id)
    if package is None or package.item_id != item_id or package.retired_at:
        return not_found()
    package.retired_at = now()
    session.commit()
    log.info("package retired", item_package_id=package_id)
    return redirect(f"{ITEMS}/{item_id}", notice="package_retired")
