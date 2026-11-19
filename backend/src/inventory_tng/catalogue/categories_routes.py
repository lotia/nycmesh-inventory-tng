"""Categories: the simplest catalogue page, and the pattern the others
follow. A category with current items cannot be retired: they would be
left in a category nobody can pick."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn, admin_signed_in
from inventory_tng.catalogue import categories, queries
from inventory_tng.catalogue.forms import CATEGORY_TAKEN
from inventory_tng.catalogue.paths import CATEGORIES
from inventory_tng.core.db_errors import refusal
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.phrases import Words
from inventory_tng.models.catalogue import Category
from inventory_tng.web.admin_pages import admin_page, now, retirement, undo_retire
from inventory_tng.web.forms import ALERTS, RULES, form_version, name_problem
from inventory_tng.web.listing import AskedView, Sort, Sorting, View, arranged
from inventory_tng.web.navigation import SECTION_CATEGORIES
from inventory_tng.web.pages import not_found, redirect

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)


# How the categories list sorts, each row a category and how many current
# items it has: by name, as it always has, or by a heading (Ali,
# 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "name",
    {
        "name": Sort.keyed(lambda row: row[0].name.lower()),
        "items": Sort.keyed(lambda row: row[1]),
    },
)


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    problem: Words | None = None,
    name: str | None = None,
    *,
    view: View | None = None,
    undo: str | None = None,
) -> HTMLResponse:
    rows = queries.categories_by_name(session)
    items = queries.current_items_by_category(session)
    current, view = arranged(
        ((c, items.get(c.id, 0)) for c in rows if c.retired_at is None),
        view or View.read(CATEGORIES, SORTING),
    )
    log.debug("categories listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "catalogue/categories.html",
        {
            "title": SECTION_CATEGORIES,
            "undo": undo,
            "here": CATEGORIES,
            "view": view,
            "current": current,
            "retired": [c for c in rows if c.retired_at is not None],
            "problems": {"name": problem} if problem else None,
            "values": {"name": name} if name is not None else None,
        },
        400 if problem else 200,
    )


def _category_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    category: Category,
    problem: Words | None = None,
    alert: Words | None = None,
) -> HTMLResponse:
    items = queries.current_items_of(session, category.id)
    return admin_page(
        request,
        signed,
        "catalogue/category.html",
        {
            "title": category.name,
            "here": f"{CATEGORIES}/{category.id}",
            "back_link": (CATEGORIES, SECTION_CATEGORIES),
            "category": category,
            **retirement(session, category),
            "items": items,
            "problems": {"name": problem} if problem else None,
            "alert": alert,
        },
    )


@router.get(CATEGORIES)
def list_categories(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    asked: AskedView,
    undo: str = "",
) -> Response:
    back = undo_retire(session, Category, undo, CATEGORIES)
    view = View.read(CATEGORIES, SORTING, asked)
    return _list_page(request, session, signed, view=view, undo=back)


@router.post(CATEGORIES)
def add_category(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name: Annotated[str, Form()] = "",
) -> Response:
    name = name.strip()
    problem = name_problem(name)
    added = None
    if problem is None:
        added, _ = refusal(session, lambda: categories.add(session, name), "unique")
        problem = None if added else CATEGORY_TAKEN
    if added is None:
        return _list_page(request, session, signed, problem, name)
    session.commit()
    log.info("category added", category_id=added.id)
    return redirect(CATEGORIES, notice="added")


@router.get(f"{CATEGORIES}/{{category_id}}")
def category(
    request: Request, signed: AdminSignedIn, session: DbSession, category_id: int
) -> Response:
    found = session.get(Category, category_id)
    if found is None:
        return not_found()
    return _category_page(request, session, signed, found)


@router.post(f"{CATEGORIES}/{{category_id}}/name")
def rename_category(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    category_id: int,
    name: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> Response:
    found = session.get(Category, category_id)
    if found is None:
        return not_found()
    name = name.strip()
    problem = name_problem(name)
    if problem is None:
        seen = form_version(version)
        _, broken = refusal(
            session,
            lambda: categories.rename(session, found, name, seen=seen),
            "unique",
            *ALERTS,
        )
        if broken in ALERTS:
            return _category_page(request, session, signed, found, alert=RULES[broken])
        problem = broken and CATEGORY_TAKEN
    if problem is not None:
        return _category_page(request, session, signed, found, problem)
    session.commit()
    log.info("category renamed", category_id=category_id)
    return redirect(f"{CATEGORIES}/{category_id}", notice="saved")


@router.post(f"{CATEGORIES}/{{category_id}}/retire")
def retire_category(
    request: Request, signed: AdminSignedIn, session: DbSession, category_id: int
) -> Response:
    found = session.get(Category, category_id)
    if found is None:
        return not_found()
    # Under the catalogue lock: items added or brought back meanwhile too.
    _, broken = refusal(
        session, lambda: categories.retire(session, found, now()), "category_has_items"
    )
    if broken:
        return _category_page(
            request, session, signed, found, RULES["category_has_items"]
        )
    session.commit()
    log.info("category retired", category_id=category_id)
    return redirect(CATEGORIES, notice="retired", undo=str(category_id))


@router.post(
    f"{CATEGORIES}/{{category_id}}/restore", dependencies=[Depends(admin_signed_in)]
)
def restore_category(session: DbSession, category_id: int) -> Response:
    found = session.get(Category, category_id)
    if found is None:
        return not_found()
    # Its name is its own still: names are unique, retired ones too.
    categories.restore(session, found)
    session.commit()
    log.info("category restored", category_id=category_id)
    return redirect(f"{CATEGORIES}/{category_id}", notice="restored")
