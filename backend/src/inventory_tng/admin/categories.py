"""Categories: the simplest catalogue page, and the pattern the others
follow. A category with current items cannot be retired: they would be
left in a category nobody can pick."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import categories
from inventory_tng.admin.common import (
    ADMIN,
    CHANGED,
    RULES,
    admin_page,
    form_version,
    name_problem,
    now,
    undo_retire,
)
from inventory_tng.db import DbSession, refusal, unique
from inventory_tng.models import Category, Item
from inventory_tng.pages import not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

CATEGORIES = f"{ADMIN}/categories"
TAKEN = "There is already a category by that name."


def _items(session: Session) -> dict[int, int]:
    """How many current items each category has."""
    rows = session.execute(
        select(Item.category_id, func.count())
        .where(Item.retired_at.is_(None))
        .group_by(Item.category_id)
    )
    return {category_id: count for category_id, count in rows}


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    problem: str | None = None,
    name: str | None = None,
    *,
    undo: str | None = None,
) -> HTMLResponse:
    rows = session.scalars(select(Category).order_by(func.lower(Category.name))).all()
    return admin_page(
        request,
        signed,
        "categories.html",
        {
            "title": "Categories",
            "undo": undo,
            "here": CATEGORIES,
            "current": [c for c in rows if c.retired_at is None],
            "retired": [c for c in rows if c.retired_at is not None],
            "items": _items(session),
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
    problem: str | None = None,
    alert: str | None = None,
) -> HTMLResponse:
    items = session.scalars(
        select(Item)
        .where(Item.category_id == category.id, Item.retired_at.is_(None))
        .order_by(func.lower(Item.name))
    ).all()
    return admin_page(
        request,
        signed,
        "category.html",
        {
            "title": category.name,
            "here": f"{CATEGORIES}/{category.id}",
            "back": (CATEGORIES, "Categories"),
            "category": category,
            "items": items,
            "problems": {"name": problem} if problem else None,
            "alert": alert,
        },
    )


@router.get(CATEGORIES)
def list_categories(
    request: Request, signed: AdminSignedIn, session: DbSession, undo: str = ""
) -> Response:
    back = undo_retire(session, Category, undo, CATEGORIES)
    return _list_page(request, session, signed, undo=back)


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
        problem = None if added else TAKEN
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
            CHANGED,
        )
        if broken == CHANGED:
            return _category_page(request, session, signed, found, alert=RULES[CHANGED])
        problem = broken and TAKEN
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


@router.post(f"{CATEGORIES}/{{category_id}}/restore")
def restore_category(
    request: Request, signed: AdminSignedIn, session: DbSession, category_id: int
) -> Response:
    found = session.get(Category, category_id)
    if found is None:
        return not_found()
    if not unique(session, lambda: categories.restore(session, found)):
        problem = (
            f"A current category is already called {found.name}: rename one first."
        )
        return _category_page(request, session, signed, found, problem)
    session.commit()
    log.info("category restored", category_id=category_id)
    return redirect(f"{CATEGORIES}/{category_id}", notice="restored")
