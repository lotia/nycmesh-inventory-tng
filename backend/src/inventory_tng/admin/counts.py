"""Counts and adjustments: the pages an administrator puts the records right
with (docs/decisions/0003). The writing is the ledger's (ledger.record_count,
ledger.record_adjustment); these pages check what was typed and say what is
wrong.

Each form carries a key made when it was shown, so sending it twice writes
it once. A count also carries the place's latest ledger entry as it was
shown: the count is true for that moment, so a save made at the place
meanwhile still applies, and an item it moved comes back to count again.

The pages check what was typed before writing, but an item's unit may
change to each between that check and the write: the ledger then refuses
the line (movement_whole_units), and the form is shown again with that
line marked, and nothing written. So too a place retired meanwhile, which
the ledger refuses (location_countable) unless the form was written there
before.

A count names each item once: a form naming one on two fields (counted_5
and counted_05, or the select at the foot choosing an item listed above) is
refused and shown again saying why, as it isn't clear which number is
right. The page itself never sends one. The ledger refuses a repeat too
(movement_item_once), but the page checks first, to say which fields.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.admin.common import (
    ADMIN,
    RULES,
    admin_page,
    chosen,
    current_rows,
    quantity,
    row_id,
    text_form,
)
from inventory_tng.admin.settings import SETTINGS
from inventory_tng.db import DbSession, by_name, refusal
from inventory_tng.ledger import (
    ItemLines,
    corrected,
    countable,
    expected,
    moment,
    record_adjustment,
    record_count,
    repeated,
    retired_holding_stock_count,
    takeable,
)
from inventory_tng.models import (
    INTEGER_MAX,
    AdjustmentReason,
    Admin,
    Item,
    LedgerTransaction,
    Location,
    Movement,
    whole,
)
from inventory_tng.once import form_key
from inventory_tng.pages import (
    NOTICES,
    Badge,
    not_found,
    redirect,
    retired_holding,
)
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

COUNTS = f"{ADMIN}/counts"
ADJUSTMENTS = f"{COUNTS}/adjustments"
RECENT = 30
COUNTED = "counted_"
EXTRA = "extra_quantity"
# The ledger rules a race can break past the page's checks (module
# docstring).
WHOLE = "movement_whole_units"
COUNTABLE = "location_countable"
TWICE = (
    "An item is on this form more than once. Each item is counted once, so "
    "it isn't clear which number is right: nothing was saved."
)

NOTICES.update(
    {
        "counted": "Counted. The records now match what you found.",
        "counted_same": "Counted: everything was as the records say.",
        "adjusted": "Recorded.",
    }
)

TextForm = Annotated[dict[str, str], Depends(text_form)]


def badge(session: Session) -> Badge | None:
    """Beside Counts: how many retired places hold stock, to count
    (ledger.retired_holding_stock)."""
    many = retired_holding_stock_count(session)
    return Badge(many, retired_holding(many)) if many else None


def _items(session: Session) -> list[Item]:
    """What can be counted or adjusted: current items, and retired ones with
    stock left, which may still be written off."""
    return by_name(session, Item, takeable())


def _places(session: Session) -> list[Location]:
    """Where can be counted or adjusted (ledger.countable)."""
    return by_name(session, Location, countable())


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
            "places": _places(session),
            "items": _items(session),
            "reasons": current_rows(session, AdjustmentReason),
            "recent": _recent(session),
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
    items: Collection[int] = (),
    recount: Mapping[int, tuple[Decimal, Decimal, Decimal]] | None = None,
    alert: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """The count form, with the records as of `as_of`: of everything there,
    or a recount of `items` only, which the form carries. With `recount`, a
    recount of its items: what was typed for each and what came in and went
    out while it was counted."""
    items = items or (recount or {}).keys()
    was = expected(session, place.id, as_of=as_of, items=items or None)
    if items:
        was = {item_id: was.get(item_id, Decimal(0)) for item_id in items}
    listed = _items(session)
    named = {item.id: item for item in listed}
    if missing := was.keys() - named.keys():
        # What the records had then but cannot be counted now, or what the
        # form's items, sent back, name that is no item at all.
        missed = session.scalars(select(Item).where(Item.id.in_(missing)))
        named |= {item.id: item for item in missed}
    rows = sorted(
        (
            (named[item_id], amount)
            for item_id, amount in was.items()
            if item_id in named
        ),
        key=lambda row: row[0].name.lower(),
    )
    return admin_page(
        request,
        signed,
        "count.html",
        {
            "title": f"Count {place.name}",
            "here": f"{COUNTS}/{place.id}",
            "back_link": (COUNTS, "Counts"),
            "place": place,
            "rows": rows,
            "recount": recount,
            "items": sorted(items),
            "others": [item for item in listed if item.id not in was],
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
    place = session.scalar(
        select(Location).where(Location.id == location_id, countable())
    )
    if place is None:
        return not_found()
    return _count_page(request, session, signed, place, moment(session, place.id))


# A field of the count form: its name, the item it names (None for no
# item), and what was typed for it.
Line = tuple[str, int | None, str]


def _item_id(typed: str) -> int | None:
    """The item named by digits, however many zeros lead them."""
    return row_id(typed.lstrip("0"))


def lines(form: Mapping[str, str]) -> list[Line]:
    """The count form's lines. An item chosen at the foot with an amount is
    one more, shown at its amount; one with none is not counted."""
    found = [
        (field, _item_id(field.removeprefix(COUNTED)), typed)
        for field, typed in form.items()
        if field.startswith(COUNTED)
    ]
    extra, typed = form.get("extra_item_id", ""), form.get(EXTRA, "")
    if extra and typed.strip():
        found.append((EXTRA, _item_id(extra), typed))
    return found


def named_twice(found: Sequence[Line]) -> dict[str, int | None]:
    """The fields naming an item another field names too (ledger.repeated),
    and that item."""
    return {
        found[number][0]: found[number][1]
        for number in repeated([item_id for _, item_id, _ in found])
    }


def _counted(
    session: Session, found: Collection[Line]
) -> tuple[ItemLines, dict[str, str]]:
    """What was typed, as lines of an item and an amount, and what is wrong
    with it."""
    units = dict(
        session.execute(
            select(Item.id, Item.unit).where(
                Item.id.in_([item_id for _, item_id, _ in found if item_id])
            )
        ).all()
    )
    counted: list[tuple[int, Decimal]] = []
    problems: dict[str, str] = {}
    for field, item_id, typed in found:
        if item_id is None or (unit := units.get(item_id)) is None:
            problems[field] = "No such item."
            continue
        amount = quantity(typed, positive=False, unit=unit)
        if isinstance(amount, str):
            problems[field] = amount
        elif amount is not None:
            counted.append((item_id, amount))
    return counted, problems


@router.post(f"{COUNTS}/{{location_id:int}}")
def count(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    location_id: int,
    form: TextForm,
) -> Response:
    key = form_key(form.get("key", ""))
    place = session.get(Location, location_id)
    as_of = whole(form.get("as_of", ""), 0, INTEGER_MAX)
    if place is None or key is None or as_of is None:
        return not_found()
    found = lines(form)
    sent_items = {
        item_id
        for typed in form.get("items", "").split()
        if (item_id := row_id(typed)) is not None
    }
    counted, problems = _counted(session, found)
    if repeats := named_twice(found):
        problems |= dict.fromkeys(repeats, "This item is on the form more than once.")
        log.info(
            "count refused: an item named twice",
            location_id=place.id,
            item_ids=sorted({item_id for item_id in repeats.values() if item_id}),
        )
    if problems:
        # What was typed is as of when the form was shown; a recount asks
        # again for its own items only.
        return _count_page(
            request,
            session,
            signed,
            place,
            as_of,
            problems=problems,
            values=form,
            alert=TWICE if repeats else None,
            items=sent_items,
        )
    result, broken = refusal(
        session,
        lambda: record_count(session, key, signed.admin.id, place.id, counted, as_of),
        WHOLE,
        COUNTABLE,
    )
    if broken is not None:
        log.info("count refused by a rule", location_id=place.id, rule=broken)
        if broken == COUNTABLE:
            # As for a place there is not, as the form's own page answers.
            return not_found()
        # Checked again, the units as they are now mark the lines it broke.
        _, problems = _counted(session, found)
        return _count_page(
            request,
            session,
            signed,
            place,
            as_of,
            problems=problems,
            values=form,
            alert=None if problems else RULES[broken],
            items=sent_items,
            status_code=400,
        )
    assert result is not None
    session.commit()
    corrected(signed.admin.id, place.id, result.written, result.withheld)
    if not result.moved:
        return redirect(
            COUNTS,
            notice="counted" if result.differed else "counted_same",
            section="count",
        )
    saved = len(counted) - len(result.moved)
    amounts = dict(counted)
    return _count_page(
        request,
        session,
        signed,
        place,
        moment(session, place.id),
        recount={
            item_id: (amounts[item_id], came, went)
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
    key = form_key(form.get("key", ""))
    location_id = row_id(form.get("location_id", ""))
    place = session.get(Location, location_id) if location_id is not None else None
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
    if key is None:
        problems["amount"] = "This form is out of date: try again."
    if (
        problems
        or not (place and item and reason and key)
        or not isinstance(amount, Decimal)
    ):
        return _counts_page(request, session, signed, problems=problems, values=form)
    written, broken = refusal(
        session,
        lambda: record_adjustment(
            session,
            key,
            signed.admin.id,
            place.id,
            item.id,
            amount,
            found=direction == "found",
            reason_id=reason.id,
        ),
        WHOLE,
        COUNTABLE,
    )
    if broken is not None:
        log.info("adjustment refused by a rule", item_id=item.id, rule=broken)
        field = "location_id" if broken == COUNTABLE else "amount"
        return _counts_page(
            request, session, signed, problems={field: RULES[broken]}, values=form
        )
    assert written is not None
    session.commit()
    corrected(signed.admin.id, place.id, written)
    return redirect(COUNTS, notice="adjusted", section="adjust")
