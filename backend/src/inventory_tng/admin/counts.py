"""Counts and adjustments: the pages an administrator puts the records right
with (docs/decisions/0003). The writing is the ledger's (ledger.record_count,
ledger.record_adjustment); these pages check what was typed and say what is
wrong.

Each form carries a key made when it was shown, so sending it twice writes
it once. A count also carries the place's latest ledger entry as it was
shown: the count is true for that moment, so a save made at the place
meanwhile still applies, and an item it moved comes back to count again.
"""

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from inventory_tng.admin.common import (
    ADMIN,
    admin_page,
    chosen,
    current_rows,
    quantity,
    text_form,
    whole,
)
from inventory_tng.admin.settings import SETTINGS
from inventory_tng.db import DbSession
from inventory_tng.ledger import (
    expected,
    moment,
    record_adjustment,
    record_count,
    retired_in_stock,
)
from inventory_tng.local_time import site_zone
from inventory_tng.models import (
    INTEGER_MAX,
    AdjustmentReason,
    Admin,
    Item,
    LedgerTransaction,
    Location,
    Movement,
)
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

router = APIRouter(include_in_schema=False)

COUNTS = f"{ADMIN}/counts"
ADJUSTMENTS = f"{COUNTS}/adjustments"
RECENT = 30
COUNTED = "counted_"

NOTICES.update(
    {
        "counted": "Counted. The records now match what you found.",
        "counted_same": "Counted: everything was as the records say.",
        "adjusted": "Recorded.",
    }
)

TextForm = Annotated[dict[str, str], Depends(text_form)]


def _key(typed: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(typed)
    except ValueError:
        return None


def _items(session: Session) -> list[Item]:
    """What can be counted or adjusted: current items, and retired ones with
    stock left, which may still be written off."""
    return list(
        session.scalars(
            select(Item)
            .where(or_(Item.retired_at.is_(None), Item.id.in_(retired_in_stock())))
            .order_by(func.lower(Item.name))
        )
    )


def _recent(session: Session) -> list[dict[str, Any]]:
    """The latest counts and adjustments, newest first, with their lines."""
    rows = session.execute(
        select(LedgerTransaction, Admin.username, Location.name, AdjustmentReason.name)
        .join(Admin, Admin.id == LedgerTransaction.admin_id)
        .join(
            Location,
            Location.id
            == func.coalesce(
                LedgerTransaction.to_location_id, LedgerTransaction.from_location_id
            ),
        )
        .outerjoin(AdjustmentReason, AdjustmentReason.id == LedgerTransaction.reason_id)
        .where(LedgerTransaction.kind.in_(("count", "adjustment")))
        .order_by(LedgerTransaction.id.desc())
        .limit(RECENT)
    ).all()
    lines: dict[int, list[tuple[Item, Decimal]]] = {}
    for transaction_id, item, amount in session.execute(
        select(Movement.transaction_id, Item, Movement.quantity)
        .join(Item, Item.id == Movement.item_id)
        .where(Movement.transaction_id.in_([row[0].id for row in rows]))
    ):
        lines.setdefault(transaction_id, []).append((item, amount))
    return [
        {
            "transaction": transaction,
            "admin": admin,
            "place": place,
            "found": transaction.to_location_id is not None,
            "reason": reason or "",
            "lines": lines.get(transaction.id, []),
        }
        for transaction, admin, place, reason in rows
    ]


def _counts_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    return admin_page(
        request,
        signed,
        "counts.html",
        {
            "title": "Counts and adjustments",
            "here": COUNTS,
            "settings_path": SETTINGS,
            "places": current_rows(session, Location),
            "items": _items(session),
            "reasons": current_rows(session, AdjustmentReason),
            "recent": _recent(session),
            "zone": site_zone(session),
            "key": uuid.uuid4(),
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


@router.get(COUNTS)
def counts(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _counts_page(request, session, signed)


@router.get(f"{COUNTS}/place")
def choose_place(location_id: str = "") -> Response:
    """The "Count a place" form, sent as a GET, goes to that place's count."""
    if not location_id.isdigit():
        return redirect(COUNTS)
    return redirect(f"{COUNTS}/{location_id}")


def _count_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    place: Location,
    as_of: int,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    recount: Mapping[int, tuple[Decimal, Decimal, Decimal]] | None = None,
    alert: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """The count form, with the records as of `as_of`. With recount, only the
    items that moved while they were counted: what was typed for each, and
    what came in and went out."""
    was = expected(session, place.id, as_of=as_of, items=recount)
    if recount:
        was = {item_id: was.get(item_id, Decimal(0)) for item_id in recount}
    items = {i.id: i for i in session.scalars(select(Item).where(Item.id.in_(was)))}
    rows = sorted(
        ((items[item_id], amount) for item_id, amount in was.items()),
        key=lambda row: row[0].name.lower(),
    )
    return admin_page(
        request,
        signed,
        "count.html",
        {
            "title": f"Count {place.name}",
            "here": f"{COUNTS}/{place.id}",
            "list": COUNTS,
            "place": place,
            "rows": rows,
            "recount": recount,
            "others": [i for i in _items(session) if i.id not in was],
            "key": (values or {}).get("key") or uuid.uuid4(),
            "as_of": as_of,
            "alert": alert,
            "problems": problems,
            "values": values,
        },
        400 if problems else status_code,
    )


@router.get(f"{COUNTS}/{{location_id:int}}")
def count_form(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    place = chosen(session, Location, str(location_id))
    if place is None:
        return not_found()
    return _count_page(request, session, signed, place, moment(session, place.id))


def _counted(
    session: Session, form: Mapping[str, str]
) -> tuple[dict[int, Decimal], dict[str, str]]:
    """What was typed, by item, and what is wrong with it. An item added at
    the foot counts as one more line."""
    lines = {
        field.removeprefix(COUNTED): typed
        for field, typed in form.items()
        if field.startswith(COUNTED)
    }
    extra = form.get("extra_item_id", "")
    if extra.isdigit() and form.get("extra_quantity", "").strip():
        lines[extra] = form["extra_quantity"]
    units = dict(
        session.execute(
            select(Item.id, Item.unit).where(
                Item.id.in_([int(i) for i in lines if i.isdigit()])
            )
        ).all()
    )
    counted: dict[int, Decimal] = {}
    problems: dict[str, str] = {}
    for item_id, typed in lines.items():
        field = (
            "extra_quantity"
            if item_id == extra and f"{COUNTED}{extra}" not in form
            else f"{COUNTED}{item_id}"
        )
        unit = units.get(int(item_id)) if item_id.isdigit() else None
        if unit is None:
            problems[field] = "No such item."
            continue
        amount = quantity(typed, positive=False, unit=unit)
        if isinstance(amount, str):
            problems[field] = amount
        elif amount is not None:
            counted[int(item_id)] = amount
    return counted, problems


@router.post(f"{COUNTS}/{{location_id:int}}")
def count(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    location_id: int,
    form: TextForm,
) -> Response:
    place = chosen(session, Location, str(location_id))
    key = _key(form.get("key", ""))
    as_of = whole(form.get("as_of", ""), 0, INTEGER_MAX)
    if place is None or key is None or as_of is None:
        return not_found()
    counted, problems = _counted(session, form)
    if problems:
        # What was typed is as of when the form was shown.
        return _count_page(
            request, session, signed, place, as_of, problems=problems, values=form
        )
    result = record_count(session, key, signed.admin.id, place.id, counted, as_of)
    session.commit()
    if not result.moved:
        return redirect(COUNTS, notice="counted" if result.differed else "counted_same")
    saved = len(counted) - len(result.moved)
    return _count_page(
        request,
        session,
        signed,
        place,
        moment(session, place.id),
        recount={
            item_id: (counted[item_id], came, went)
            for item_id, (came, went) in result.moved.items()
        },
        alert=(f"Saved {saved} line{'s' if saved != 1 else ''}. " if saved else "")
        + "These moved while you counted, so count them again.",
        status_code=409,
    )


@router.post(ADJUSTMENTS)
def adjust(
    request: Request, signed: AdminSignedIn, session: DbSession, form: TextForm
) -> Response:
    problems: dict[str, str] = {}
    place = chosen(session, Location, form.get("location_id", ""))
    if place is None:
        problems["location_id"] = "Pick a place."
    item = chosen(session, Item, form.get("item_id", ""), current=False)
    if item is None:
        problems["item_id"] = "Pick an item."
    amount = quantity(
        form.get("amount", ""),
        positive=True,
        unit=item.unit if item else None,
        required="How much.",
    )
    if isinstance(amount, str):
        problems["amount"] = amount
    direction = form.get("direction")
    if direction not in ("found", "off"):
        problems["direction"] = "Found or written off?"
    reason = chosen(session, AdjustmentReason, form.get("reason_id", ""))
    if reason is None:
        problems["reason_id"] = "Pick a reason."
    key = _key(form.get("key", ""))
    if key is None:
        problems["amount"] = "This form is out of date: try again."
    if (
        problems
        or not (place and item and reason and key)
        or not isinstance(amount, Decimal)
    ):
        return _counts_page(request, session, signed, problems=problems, values=form)
    record_adjustment(
        session,
        key,
        signed.admin.id,
        place.id,
        item.id,
        amount,
        found=direction == "found",
        reason_id=reason.id,
    )
    session.commit()
    return redirect(COUNTS, notice="adjusted")
