"""Categories: the simplest catalogue page, and the pattern the others
follow. A category with current items cannot be retired: they would be
left in a category nobody can pick."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.admin.common import ADMIN, name_problem, now, section
from inventory_tng.db import DbSession, unique
from inventory_tng.models import Category, Item
from inventory_tng.pages import NOTICES, not_found, page, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

router = APIRouter(include_in_schema=False)

CATEGORIES = section(f"{ADMIN}/categories", "Categories")
TAKEN = "There is already a category by that name."

NOTICES.update(
    {
        "category_added": "Category added.",
        "category_renamed": "Renamed.",
        "category_retired": "Retired. Bring it back from the list if you need it.",
        "category_restored": "Brought back.",
    }
)


def _items(session: Session) -> dict[int, int]:
    """Each category's current items, by category id."""
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
    *,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    rows = session.scalars(select(Category).order_by(func.lower(Category.name))).all()
    return page(
        request,
        "categories.html",
        {
            "title": "Categories",
            "admin": signed.admin,
            "current": [c for c in rows if c.retired_at is None],
            "retired": [c for c in rows if c.retired_at is not None],
            "items": _items(session),
            "problems": problems or {},
            "values": values or {},
        },
        status_code,
    )


def _category_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    category: Category,
    *,
    problem: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    items = session.scalars(
        select(Item)
        .where(Item.category_id == category.id, Item.retired_at.is_(None))
        .order_by(func.lower(Item.name))
    ).all()
    return page(
        request,
        "category.html",
        {
            "title": category.name,
            "admin": signed.admin,
            "category": category,
            "items": items,
            "problems": {"name": problem} if problem else {},
        },
        status_code,
    )


def _find(session: Session, category_id: int) -> Category | None:
    return session.get(Category, category_id, with_for_update=True)


@router.get(CATEGORIES)
def categories(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _list_page(request, session, signed)


@router.post(CATEGORIES)
def add_category(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name: Annotated[str, Form()] = "",
) -> Response:
    name = name.strip()
    problem = name_problem(name)
    if problem is None and not unique(
        session, lambda: session.add(Category(name=name))
    ):
        problem = TAKEN
    if problem is not None:
        session.rollback()
        return _list_page(
            request,
            session,
            signed,
            problems={"name": problem},
            values={"name": name},
            status_code=400,
        )
    session.commit()
    return redirect(CATEGORIES, notice="category_added")


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
) -> Response:
    found = _find(session, category_id)
    if found is None:
        return not_found()
    name = name.strip()

    def rename() -> None:
        found.name = name

    problem = name_problem(name)
    if problem is None and not unique(session, rename):
        problem = TAKEN
    if problem is not None:
        session.rollback()
        return _category_page(
            request, session, signed, found, problem=problem, status_code=400
        )
    session.commit()
    return redirect(f"{CATEGORIES}/{category_id}", notice="category_renamed")


@router.post(f"{CATEGORIES}/{{category_id}}/retire")
def retire_category(
    request: Request, signed: AdminSignedIn, session: DbSession, category_id: int
) -> Response:
    found = _find(session, category_id)
    if found is None or found.retired_at is not None:
        return not_found()
    if _items(session).get(found.id):
        session.rollback()
        problem = "Move or retire its items first."
        return _category_page(
            request, session, signed, found, problem=problem, status_code=400
        )
    found.retired_at = now()
    session.commit()
    return redirect(CATEGORIES, notice="category_retired")


@router.post(f"{CATEGORIES}/{{category_id}}/restore")
def restore_category(
    request: Request, signed: AdminSignedIn, session: DbSession, category_id: int
) -> Response:
    found = _find(session, category_id)
    if found is None or found.retired_at is None:
        return not_found()

    def restore() -> None:
        found.retired_at = None

    if not unique(session, restore):
        session.rollback()
        problem = (
            f"A current category is already called {found.name}: rename one first."
        )
        return _category_page(
            request, session, signed, found, problem=problem, status_code=400
        )
    session.commit()
    return redirect(f"{CATEGORIES}/{category_id}", notice="category_restored")
