"""Items and the packages they come in. An item's unit is fixed once stock
has moved in it (items.save): the form shows it, and does not offer to
change it. A retired item with stock left may still be taken, never
received (docs/decisions/0003)."""

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
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
    text_form,
    undo_retire,
)
from inventory_tng.admin.listing import Listing, Searched
from inventory_tng.db import DbSession, exists_where, refusal
from inventory_tng.ledger import balance
from inventory_tng.models import UNITS, Category, Item, ItemPackage, Label, Movement
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

ITEMS = f"{ADMIN}/items"
TEXT_FIELDS = ("manufacturer", "part_number", "old_code", "notes", "ordering_notes")
# Said when another item had what was asked for, but no longer does.
TAKEN = (
    "Another item, perhaps a retired one, already has that name, that "
    "manufacturer and part number, or that old code."
)
NO_LETTERS = "Give it a name with more than spaces and punctuation in it."
# The form's field holding the name an administrator was shown close names
# for, and sent again: a different item's, so not asked about again.
DIFFERENT = "different"
# Refused by _change_or_note, as a rule would be, to ask about close names.
CLOSE = "close_names"
# After another item's name, what of it is taken (items.taken_by).
TAKEN_FIELDS = {
    "name": "already has that name, or one differing from it only in case, "
    "spaces or punctuation.",
    "part_number": "already has that manufacturer and part number.",
    "old_code": "already has that old code.",
}


# The rules a change to an item may break, besides "unique", and the field
# each is shown by.
REFUSED_FIELDS = {
    "item_category_current": "category_id",
    "item_unit_fixed": "unit",
}


def _taken(
    session: Session, fields: items.Fields, item_id: int | None
) -> tuple[str, str]:
    """The field another item already has, and words naming that item."""
    found = items.taken_by(session, fields, item_id)
    field, other = found or ("name", None)
    log.debug(
        "item refused",
        rule="unique",
        field=field,
        item_id=item_id,
        other_item_id=other and other.id,
    )
    if other is None:
        return field, TAKEN
    retired = " It is retired." if other.retired_at is not None else ""
    return field, f"{other.name} {TAKEN_FIELDS[field]}{retired}"


class _AskFirst(Exception):
    """A change whose name is close to other items': undone, to ask."""


def _change_or_note[T](
    session: Session,
    change: Callable[[], T],
    problems: dict[str, str],
    fields: items.Fields,
    item_id: int | None = None,
    *,
    asked: list[Item] | None = None,
) -> tuple[T | None, str | None]:
    """Makes the change to `fields`: what it did, and None; or, if it
    breaks a unique index or one of REFUSED_FIELDS' rules, None, and the
    rule, which `problems` then says beside its field; or one of ALERTS;
    or, given `asked` and every rule allowing it, CLOSE when other items'
    names are close to its (docs/decisions/0021), those items put in
    `asked`, and nothing of it kept."""

    def asking() -> T:
        done = change()
        if asked is not None:
            session.flush()
            # An add returns its item; a save is of item_id's.
            made = done.id if isinstance(done, Item) else item_id or 0
            if near := items.similar(session, fields.name, made):
                asked.extend(near)
                raise _AskFirst
        return done

    try:
        done, broken = refusal(session, asking, "unique", *REFUSED_FIELDS, *ALERTS)
    except _AskFirst:
        log.debug("close names asked about", item_id=item_id, found=len(asked or ()))
        return None, CLOSE
    if broken == "unique":
        field, words = _taken(session, fields, item_id)
        problems[field] = words
    elif broken in REFUSED_FIELDS:
        problems[REFUSED_FIELDS[broken]] = RULES[broken]
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


# How the items list finds an item: by anything a volunteer might type.
SEARCHED = Searched(ITEMS, Item, Item.search_text, Item.name)


def _name_field(similar: Sequence[Item]) -> dict[str, object]:
    """What _item_name.html needs besides the form's values."""
    return {"similar": similar, "items_path": ITEMS, "different": DIFFERENT}


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    listing: Listing[Item] | None = None,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    undo: str | None = None,
    similar: Sequence[Item] = (),
) -> HTMLResponse:
    listing = (listing or SEARCHED.read()).find(session)
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
            "listing": listing,
            "categories": [c for c in categories if c.retired_at is None],
            "category_names": {c.id: c.name for c in categories},
            "units": UNITS,
            "problems": problems,
            "values": values,
            **_name_field(similar),
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
    similar: Sequence[Item] = (),
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
            "back_link": (ITEMS, "Items"),
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
            **_name_field(similar),
        },
    )


def _confirmed(form: Mapping[str, str], fields: items.Fields) -> bool:
    """Whether the name was sent before and asked about, then sent again
    unchanged: a different item's, whatever is close."""
    return form.get(DIFFERENT, "").strip() == fields.name


def _fields(
    session: Session, form: Mapping[str, str]
) -> tuple[items.Fields | None, dict[str, str]]:
    """What to set, unless something is wrong, and what is."""
    name = form.get("name", "").strip()
    problems: dict[str, str] = {}
    if problem := name_problem(name):
        problems["name"] = problem
    # A letter is never punctuation: only a name without one asks.
    elif not any(c.isalpha() for c in name) and items.nameless(session, name):
        problems["name"] = NO_LETTERS
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
    show: str = "",
    page: str = "",
    pick: str = "",
    undo: str = "",
) -> Response:
    listing = SEARCHED.read(q, show, page, pick)
    back = undo_retire(session, Item, undo, ITEMS)
    return _list_page(request, session, signed, listing=listing, undo=back)


@router.get(f"{ITEMS}/suggest")
def suggest_items(
    session: DbSession, signed: AdminSignedIn, q: str = "", show: str = ""
) -> JSONResponse:
    """What the search box offers as one types (_listing.html)."""
    listing = SEARCHED.read(q, show)
    names = {c.id: c.name for c in _categories(session)}
    offered = listing.suggest(session, lambda item: names.get(item.category_id, ""))
    return JSONResponse(offered, headers={"Cache-Control": "no-store"})


@router.post(ITEMS)
def add_item(
    request: Request, signed: AdminSignedIn, session: DbSession, form: TextForm
) -> Response:
    fields, problems = _fields(session, form)
    item = None
    if fields is not None:
        asked = None if _confirmed(form, fields) else []
        item, broken = _change_or_note(
            session, lambda: items.add(session, fields), problems, fields, asked=asked
        )
        if broken == CLOSE:
            return _list_page(
                request, session, signed, values=form, similar=asked or ()
            )
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
        # Its own name, or a respelling of it, asks nothing: it is the same.
        same = fields.name == found.name or _confirmed(form, fields)
        asked = (
            None if same or items.same_name(session, fields.name, found.name) else []
        )
        _, broken = _change_or_note(
            session,
            lambda: items.save(session, found, fields, seen=seen),
            problems,
            fields,
            item_id,
            asked=asked,
        )
        if broken == CLOSE:
            return _item_page(
                request, session, signed, found, values=form, similar=asked or ()
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
    # Its name and codes are its own still: unique, retired ones too.
    _, broken = refusal(
        session, lambda: items.restore(session, found), "item_category_current"
    )
    # Beside the name: the category list shows only current categories.
    if broken:
        problem = RULES[broken]
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
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    package_id: int,
) -> Response:
    package = session.get(ItemPackage, package_id)
    if package is None or package.item_id != item_id:
        return not_found()
    _, broken = refusal(
        session, lambda: items.retire_package(session, package, now()), RETIRED
    )
    if broken:
        found = session.get_one(Item, item_id)
        return _item_page(request, session, signed, found, alert=RULES[RETIRED])
    session.commit()
    log.info("package retired", item_package_id=package_id)
    return redirect(f"{ITEMS}/{item_id}", notice="package_retired", section="packages")
