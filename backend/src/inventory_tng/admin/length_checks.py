"""Checks of what an open package really has (docs/decisions/0020,
2026-11-12): raised by a volunteer's report of what is on it now, or by a
save that took it below zero. "Needs your action" on the home page leads
here, for one item or all. Set to N writes a count of the open package,
under the administrator's name, as a count does; Leave it clears the
check."""

import uuid
from collections.abc import Collection, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, NamedTuple

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from sqlalchemy import ColumnElement, and_, or_, select, true
from sqlalchemy.orm import Session

from inventory_tng import ledger, open_packages
from inventory_tng.admin.common import admin_page, quantity, row_id
from inventory_tng.admin.needs_action import Kind, OnlyItem, query, query_string
from inventory_tng.admin.stock import STOCK
from inventory_tng.db import DbSession, RuleBroken, attempt
from inventory_tng.local_time import site_zone
from inventory_tng.models import (
    Item,
    LedgerTransaction,
    Movement,
    OpenPackage,
    Volunteer,
)
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

LENGTHS = f"{STOCK}/length-checks"

NOTICES.update(
    {
        "length_set": "Set: a count of each writes what it really has.",
        "length_left": "Left as it shows.",
        "length_none": "Tick the open packages first.",
        "length_unchanged": "Nothing changed: it was checked meanwhile, moved "
        "or was cut from since this page was shown, or its place can't be "
        "counted. Look again.",
        "length_bad": "Type what is on it: a number, 0 or more.",
        "length_none_said": "Nobody said what is on those: type what is on "
        "each, and set it on its own.",
    }
)

# Waiting for a check, not closed: reported by a volunteer, or gone below
# zero.
OPEN = OpenPackage.closed_at.is_(None)
REPORTED = OpenPackage.length_reported_at.is_not(None)
BELOW = OpenPackage.below_zero_at.is_not(None)
# Gone below zero and reported used up: the confirm page shows its
# shortfall already (used_up.py).
BELOW_ONLY = (BELOW, ~REPORTED, OpenPackage.reported_at.is_(None))
CHECKED = (or_(REPORTED, and_(*BELOW_ONLY)), OPEN)


# The kinds "Needs your action" shows, each with its own page: a package a
# volunteer reported is under the first, whether or not it went below zero
# too.
KINDS = (
    Kind("Shows the wrong length", f"{LENGTHS}/reported", (REPORTED, OPEN)),
    Kind("Went below zero", f"{LENGTHS}/below-zero", (*BELOW_ONLY, OPEN)),
)

# Cuts shown beside each, newest first.
RECENT = 5


class Cut(NamedTuple):
    at: datetime
    quantity: Decimal
    kind: str
    volunteer: str | None


class Checked(NamedTuple):
    """An open package to check, as the page shows it: what the
    administrator sees of its check (open_packages.check_seen), and its
    place's latest transaction then (ledger.latest_at), so Set to N counts
    as of then."""

    shown: open_packages.Shown
    # Who said what is really on it, if a volunteer did.
    reported_by: str | None
    cuts: list[Cut]
    as_of: int
    seen: str


def recent_cuts(session: Session, open_ids: Collection[int]) -> dict[int, list[Cut]]:
    """What was last taken from each of these open packages, newest first,
    RECENT of each: one query, however many."""
    cuts: dict[int, list[Cut]] = {open_id: [] for open_id in open_ids}
    if not cuts:
        return cuts
    newest = (
        select(
            LedgerTransaction.id,
            LedgerTransaction.created_at,
            Movement.quantity,
            LedgerTransaction.kind,
            Volunteer.name,
        )
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .outerjoin(Volunteer, Volunteer.id == LedgerTransaction.volunteer_id)
        .where(Movement.from_open_package_id == OpenPackage.id)
        .order_by(LedgerTransaction.id.desc())
        .limit(RECENT)
        .lateral()
    )
    for open_id, _, *cut in session.execute(
        select(OpenPackage.id, *newest.c)
        .join(newest, true())
        .where(OpenPackage.id.in_(list(cuts)))
        .order_by(OpenPackage.id, newest.c.id.desc())
    ):
        cuts[open_id].append(Cut(*cut))
    return cuts


def _checks_page(
    request: Request,
    signed: SignedIn,
    session: Session,
    only: Item | None,
    title: str,
    where: Sequence[ColumnElement[bool]],
) -> Response:
    if only:
        where = [*where, OpenPackage.item_id == only.id]
    # Read, not locked, and first, so a save between the two reads is after
    # it: an answer locks the place, and refuses a package that changed
    # since it was shown (_set).
    latest = ledger.latest_at(session, _places(session, where))
    found = open_packages.shown(session, *where)
    cuts = recent_cuts(session, [each.row.id for each in found])
    reporters = {each.row.length_reported_by_volunteer_id for each in found}
    volunteers: dict[int | None, str] = {
        volunteer.id: volunteer.name
        for volunteer in session.scalars(
            select(Volunteer).where(Volunteer.id.in_(reporters))
        )
    }
    checked = []
    for each in found:
        # Set to what was said is as of the report, so a cut since is never
        # undone; else as of the page.
        as_of = each.row.length_reported_in_id or latest[each.row.location_id]
        checked.append(
            Checked(
                each,
                volunteers.get(each.row.length_reported_by_volunteer_id),
                cuts[each.row.id],
                as_of,
                open_packages.check_seen(each.row),
            )
        )
    return admin_page(
        request,
        signed,
        "length_checks.html",
        {
            "title": title,
            "here": LENGTHS,
            "all_path": request.url.path,
            "back_link": (STOCK, "Stock"),
            "only": only,
            "query": query_string(only),
            "checked": checked,
            "zone": site_zone(session),
        },
    )


@router.get(LENGTHS)
def length_checks_page(
    request: Request, signed: AdminSignedIn, session: DbSession, only: OnlyItem
) -> Response:
    """All those to check, or only one item's."""
    return _checks_page(request, signed, session, only, "Lengths to check", CHECKED)


@router.get(f"{LENGTHS}/{{which}}")
def kind_page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    which: str,
) -> Response:
    """One kind's, as "Needs your action" links to it."""
    kind = next((each for each in KINDS if each.path == f"{LENGTHS}/{which}"), None)
    if kind is None:
        return not_found()
    return _checks_page(request, signed, session, only, kind.title, kind.where)


def _places(session: Session, where: Sequence[ColumnElement[bool]]) -> set[int]:
    """Where the open packages `where` names are."""
    return set(
        session.scalars(select(OpenPackage.location_id).where(*where).distinct())
    )


def _set(session: Session, signed: SignedIn, answer: Answer) -> bool:
    """A count of the open package at what it really has, as of `as_of`,
    under a key of its check, so sending it twice writes it once, and the
    check settled: both, if the check is still as `seen` and the package
    was not moved or cut from since, nor shows other than it `showed` on
    the page; else neither."""
    open_id, seen, left, as_of, showed = answer
    assert left is not None  # A Set's, never a Leave's.
    row = session.get_one(OpenPackage, open_id)
    key = uuid.uuid5(row.key, f"length {seen}")

    def both() -> bool:
        if open_packages.settle_check(
            session, open_id, seen
        ) and ledger.count_open_package(
            session, key, signed.admin.id, row, left, as_of, showed=showed
        ):
            return True
        # Neither: undone with the savepoint.
        raise RuleBroken("length_unchanged")

    done, broken = attempt(session, both)
    return bool(done) and broken is None


def _showed(typed: str) -> tuple[int, Decimal] | None:
    """Where a package was and what it showed, "place left", as the page
    sent it back; None if it sent none it could have."""
    place, _, left = typed.partition(" ")
    try:
        found = Decimal(left)
    except InvalidOperation:
        return None
    place_id = row_id(place)
    if place_id is None or not found.is_finite():
        return None
    return place_id, found


def _page(sent: str) -> str:
    """The page an answer came from, to go back to: this one or a kind's."""
    return sent if sent in {kind.path for kind in KINDS} else LENGTHS


class Answer(NamedTuple):
    """What to do with a check: set it to a length, as of a moment, or leave
    it (no length); what was seen of it; and, for a Set from the page's own
    form (set_one), where the package was and what it showed there."""

    open_id: int
    seen: str
    left: Decimal | None = None
    as_of: int = 0
    showed: tuple[int, Decimal] | None = None


def _answer(
    session: Session,
    signed: SignedIn,
    answers: Sequence[Answer],
    back: dict[str, str],
    *,
    unsaid: int = 0,
    page: str = LENGTHS,
) -> Response:
    """Set or leave each of `answers` still as seen, and say so. Their
    places locked first, by id, as saves take places: only here, never as
    the page is shown."""
    named = OpenPackage.id.in_([each.open_id for each in answers])
    for place in sorted(_places(session, [named])):
        ledger.lock_place(session, place)
    set_, left_ = 0, 0
    for each in sorted(answers):
        if each.left is None:
            left_ += open_packages.settle_check(session, each.open_id, each.seen)
        else:
            set_ += _set(session, signed, each)
    session.commit()
    open_packages.measure_checks_settled("set", set_)
    open_packages.measure_checks_settled("left", left_)
    log.info("length checks answered", set=set_, left=left_, admin_id=signed.admin.id)
    if set_:
        notice = "length_set"
    elif left_:
        notice = "length_left"
    elif unsaid:
        notice = "length_none_said"
    else:
        notice = "length_unchanged"
    return redirect(page, notice=notice, **back)


@router.post(f"{LENGTHS}/{{open_id}}/set")
def set_one(
    signed: AdminSignedIn,
    session: DbSession,
    open_id: int,
    only: OnlyItem,
    left: Annotated[str, Form()] = "",
    as_of: Annotated[str, Form()] = "",
    seen: Annotated[str, Form()] = "",
    showed: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
) -> Response:
    """Set one to what is typed."""
    row = session.get(OpenPackage, open_id)
    if row is None:
        return not_found()
    back = _page(page)
    unit = session.get_one(Item, row.item_id).unit
    length = quantity(left, positive=False, unit=unit)
    moment = row_id(as_of)
    shown = _showed(showed)
    if not isinstance(length, Decimal) or moment is None:
        return redirect(back, notice="length_bad", **query(only))
    if shown is None:
        # A form from before it said: its moment cannot be trusted.
        return redirect(back, notice="length_unchanged", **query(only))
    answer = [Answer(open_id, seen, length, moment, shown)]
    return _answer(session, signed, answer, query(only), page=back)


@router.post(f"{LENGTHS}/{{open_id}}/leave")
def leave_one(
    signed: AdminSignedIn,
    session: DbSession,
    open_id: int,
    only: OnlyItem,
    seen: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
) -> Response:
    """Leave one as it shows."""
    if session.get(OpenPackage, open_id) is None:
        return not_found()
    answer = [Answer(open_id, seen)]
    return _answer(session, signed, answer, query(only), page=_page(page))


@router.post(LENGTHS)
def answer_ticked(
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    open_package: Annotated[list[str], Form()] = [],  # noqa: B006  FastAPI copies it.
    do: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
) -> Response:
    """Set each ticked to what its volunteer reported, as of the report, or
    leave each. A box sends its package's id and what was seen of its
    check, "id seen"."""
    if do not in ("set", "leave"):
        return not_found()
    ticked = {
        open_id: seen
        for each in open_package
        for open_id, _, seen in [each.partition(" ")]
        if open_id.isdigit()
    }
    back = _page(page)
    if not ticked:
        return redirect(back, notice="length_none", **query(only))
    rows = session.scalars(
        select(OpenPackage).where(
            OpenPackage.id.in_([int(open_id) for open_id in ticked]), *CHECKED
        )
    ).all()
    answers: list[Answer] = []
    unsaid = 0
    for row in rows:
        seen = ticked[str(row.id)]
        if do == "leave":
            answers.append(Answer(row.id, seen))
        elif row.length_reported is not None:
            # As of the report: a cut since leaves it to check again.
            # Set with it, reported with it (ck_open_package_length_reported_by).
            assert row.length_reported_in_id is not None
            answers.append(
                Answer(row.id, seen, row.length_reported, row.length_reported_in_id)
            )
        else:
            unsaid += 1
    return _answer(session, signed, answers, query(only), unsaid=unsaid, page=back)
