"""Items and the packages they come in. An item's unit is fixed once stock
has moved in it: the ledger's quantities are in that unit."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from inventory_tng.admin.categories import CATEGORIES
from inventory_tng.admin.common import ADMIN, name_problem, now, quantity, section
from inventory_tng.db import DbSession, unique
from inventory_tng.ledger import balance
from inventory_tng.models import UNITS, Category, Item, ItemPackage, Movement
from inventory_tng.pages import NOTICES, not_found, page, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

router = APIRouter(include_in_schema=False)

ITEMS = section(f"{ADMIN}/items", "Items")
# The fields an item's form sends, beyond its name.
TEXT_FIELDS = ("manufacturer", "part_number", "old_code", "notes", "ordering_notes")
TAKEN = (
    "Another item already has that name, that manufacturer and part number, "
    "or that old code."
)

NOTICES.update(
    {
        "item_added": "Item added.",
        "item_saved": "Saved.",
        "item_retired": "Retired. Bring it back from the list if you need it.",
        "item_restored": "Brought back.",
        "package_added": "Package added.",
        "package_retired": "Package retired.",
    }
)


def _categories(session: Session) -> list[Category]:
    return list(
        session.scalars(
            select(Category)
            .where(Category.retired_at.is_(None))
            .order_by(func.lower(Category.name))
        )
    )


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    search: str = "",
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    query = select(Item).order_by(func.lower(Item.name))
    if search:
        query = query.where(Item.matches(search))
    rows = session.scalars(query).all()
    return page(
        request,
        "items.html",
        {
            "title": "Items",
            "admin": signed.admin,
            "search": search,
            "current": [i for i in rows if i.retired_at is None],
            "retired": [i for i in rows if i.retired_at is not None],
            "categories": _categories(session),
            "categories_path": CATEGORIES,
            "category_names": {c.id: c.name for c in session.scalars(select(Category))},
            "units": UNITS,
            "problems": problems or {},
            "values": values or {},
        },
        status_code,
    )


def _in_stock(session: Session, item_id: int) -> Decimal:
    """How much of it there is, everywhere together."""
    total = session.scalar(
        select(func.sum(balance.c.quantity)).where(balance.c.item_id == item_id)
    )
    return Decimal(total or 0)


def _moved(session: Session, item_id: int) -> bool:
    return bool(session.scalar(select(exists().where(Movement.item_id == item_id))))


def _item_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    item: Item,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    packages = session.scalars(
        select(ItemPackage)
        .where(ItemPackage.item_id == item.id)
        .order_by(ItemPackage.retired_at.is_not(None), ItemPackage.size)
    ).all()
    return page(
        request,
        "item.html",
        {
            "title": item.name,
            "admin": signed.admin,
            "item": item,
            "packages": packages,
            "categories": _categories(session),
            "units": UNITS,
            "unit_fixed": _moved(session, item.id),
            "in_stock": _in_stock(session, item.id),
            "problems": problems or {},
            "values": values or {},
        },
        status_code,
    )


def _find(session: Session, item_id: int) -> Item | None:
    return session.get(Item, item_id, with_for_update=True)


def _fields(
    session: Session, form: Mapping[str, str], *, unit_fixed: bool = False
) -> tuple[dict[str, object], dict[str, str]]:
    """An item form's values, checked: what to set, and what is wrong."""
    values: dict[str, object] = {"name": form.get("name", "").strip()}
    problems: dict[str, str] = {}
    if problem := name_problem(str(values["name"])):
        problems["name"] = problem
    for field in TEXT_FIELDS:
        values[field] = form.get(field, "").strip() or None
    category_id = form.get("category_id", "")
    category = (
        session.get(Category, int(category_id)) if category_id.isdigit() else None
    )
    if category is None or category.retired_at is not None:
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


ItemForm = Annotated[str, Form()]


@router.get(ITEMS)
def items(
    request: Request, signed: AdminSignedIn, session: DbSession, q: str = ""
) -> Response:
    return _list_page(request, session, signed, search=q.strip())


@router.post(ITEMS)
def add_item(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name: ItemForm = "",
    category_id: ItemForm = "",
    unit: ItemForm = "each",
    manufacturer: ItemForm = "",
    part_number: ItemForm = "",
) -> Response:
    form = {
        "name": name,
        "category_id": category_id,
        "unit": unit,
        "manufacturer": manufacturer,
        "part_number": part_number,
    }
    values, problems = _fields(session, form)
    item = Item(**values)
    if not problems and not unique(session, lambda: session.add(item)):
        problems["name"] = TAKEN
    if problems:
        return _list_page(
            request, session, signed, problems=problems, values=form, status_code=400
        )
    session.commit()
    return redirect(f"{ITEMS}/{item.id}", notice="item_added")


@router.get(f"{ITEMS}/{{item_id}}")
def item(
    request: Request, signed: AdminSignedIn, session: DbSession, item_id: int
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    return _item_page(request, session, signed, found)


@router.post(f"{ITEMS}/{{item_id}}")
async def save_item(
    request: Request, signed: AdminSignedIn, session: DbSession, item_id: int
) -> Response:
    # Every field at once; the form is small and all of it is text.
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    return _save_item(request, signed, session, item_id, form)


def _save_item(
    request: Request,
    signed: SignedIn,
    session: Session,
    item_id: int,
    form: dict[str, str],
) -> Response:
    found = _find(session, item_id)
    if found is None:
        return not_found()
    values, problems = _fields(session, form, unit_fixed=_moved(session, item_id))

    def save() -> None:
        for field, value in values.items():
            setattr(found, field, value)

    if not problems and not unique(session, save):
        problems["name"] = TAKEN
    if problems:
        return _item_page(
            request,
            session,
            signed,
            found,
            problems=problems,
            values=form,
            status_code=400,
        )
    session.commit()
    return redirect(f"{ITEMS}/{item_id}", notice="item_saved")


@router.post(f"{ITEMS}/{{item_id}}/retire")
def retire_item(signed: AdminSignedIn, session: DbSession, item_id: int) -> Response:
    found = _find(session, item_id)
    if found is None or found.retired_at is not None:
        return not_found()
    found.retired_at = now()
    session.commit()
    return redirect(ITEMS, notice="item_retired")


@router.post(f"{ITEMS}/{{item_id}}/restore")
def restore_item(
    request: Request, signed: AdminSignedIn, session: DbSession, item_id: int
) -> Response:
    found = _find(session, item_id)
    if found is None or found.retired_at is None:
        return not_found()

    def restore() -> None:
        found.retired_at = None

    if not unique(session, restore):
        problem = (
            "A current item already has its name or part number: change one first."
        )
        return _item_page(
            request, session, signed, found, problems={"name": problem}, status_code=400
        )
    session.commit()
    return redirect(f"{ITEMS}/{item_id}", notice="item_restored")


@router.post(f"{ITEMS}/{{item_id}}/packages")
def add_package(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_name: ItemForm = "",
    size: ItemForm = "",
) -> Response:
    found = _find(session, item_id)
    if found is None:
        return not_found()
    name = package_name.strip()
    problems: dict[str, str] = {}
    if problem := name_problem(name):
        problems["package_name"] = problem
    parsed = quantity(size, positive=True)
    if parsed is None or isinstance(parsed, str):
        problems["size"] = parsed or "How many of the item's unit it holds."
    package = ItemPackage(
        item_id=item_id, name=name, size=parsed if isinstance(parsed, Decimal) else 1
    )
    if not problems and not unique(session, lambda: session.add(package)):
        problems["package_name"] = "It already has a package by that name."
    if problems:
        values = {"package_name": name, "size": size}
        return _item_page(
            request,
            session,
            signed,
            found,
            problems=problems,
            values=values,
            status_code=400,
        )
    session.commit()
    return redirect(f"{ITEMS}/{item_id}", notice="package_added")


@router.post(f"{ITEMS}/{{item_id}}/packages/{{package_id}}/retire")
def retire_package(
    signed: AdminSignedIn, session: DbSession, item_id: int, package_id: int
) -> Response:
    package = session.get(ItemPackage, package_id, with_for_update=True)
    if package is None or package.item_id != item_id or package.retired_at:
        return not_found()
    package.retired_at = now()
    session.commit()
    return redirect(f"{ITEMS}/{item_id}", notice="package_retired")
