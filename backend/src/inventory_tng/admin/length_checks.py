"""Checks of what an open package really has (docs/decisions/0020,
2026-11-12): raised by a volunteer's report of what is on it now, or by a
save that took it below zero. "Needs your action" on the home page leads
here, for one item or all. Set to N writes a count of the open package,
under the administrator's name, of the difference between N and what it
showed then (at the report, or on the page, with any count or adjustment
of it since), so a cut since stays; Leave it clears the check
(inventory-tng-b1z)."""

import uuid
from collections.abc import Collection, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, NamedTuple

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from sqlalchemy import ColumnElement, and_, func, or_, select, true
from sqlalchemy.orm import Session

from inventory_tng import ledger, open_packages
from inventory_tng.admin.common import admin_page, now, quantity, row_id
from inventory_tng.admin.needs_action import (
    Kind,
    OnlyItem,
    of_only,
    query,
    query_string,
)
from inventory_tng.admin.stock import STOCK
from inventory_tng.db import DbSession, RuleBroken, attempt
from inventory_tng.local_time import site_zone
from inventory_tng.models import (
    INTEGER_MAX,
    Item,
    LedgerTransaction,
    Movement,
    OpenPackage,
    Volunteer,
    whole,
)
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

LENGTHS = f"{STOCK}/length-checks"

NOTICES.update(
    {
        "length_set": "Set: a count of each writes the difference from what "
        "it showed then; cuts since stay.",
        "length_left": "Left as it shows.",
        "length_none": "Tick the open packages first.",
        "length_unchanged": "Nothing changed: it was checked meanwhile, or "
        "its place can't be counted. Look again.",
        "length_bad": "Type what is on it: a number, 0 or more.",
        "length_reload": "This page is from before the last change to how "
        "lengths are set: load it again, then set it.",
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
    """An open package to check, as the page shows it, and what the
    administrator sees of its check (open_packages.check_seen)."""

    shown: open_packages.Shown
    # Who said what is really on it, if a volunteer did.
    reported_by: str | None
    cuts: list[Cut]
    seen: str


def recent_cuts(session: Session, open_ids: Collection[int]) -> dict[int, list[Cut]]:
    """What was last taken from each of these open packages, newest first,
    RECENT of each, a count's or an adjustment's too, named by its kind,
    never a move of it whole: one query, however many."""
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
        .where(
            Movement.from_open_package_id == OpenPackage.id,
            # Not a move of it whole, which names it on both sides.
            Movement.to_open_package_id.is_distinct_from(OpenPackage.id),
        )
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
    # Read first: a count or adjustment since is added to what the page
    # showed when it is set (_set).
    moment = session.scalar(select(func.max(LedgerTransaction.id))) or 0
    found = open_packages.shown(session, *of_only(where, only))
    cuts = recent_cuts(session, [each.row.id for each in found])
    reporters = {each.row.length_reported_by_volunteer_id for each in found}
    volunteers: dict[int | None, str] = {
        volunteer.id: volunteer.name
        for volunteer in session.scalars(
            select(Volunteer).where(Volunteer.id.in_(reporters))
        )
    }
    checked = [
        Checked(
            each,
            volunteers.get(each.row.length_reported_by_volunteer_id),
            cuts[each.row.id],
            open_packages.check_seen(each.row),
        )
        for each in found
    ]
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
            "moment": moment,
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


def _set(
    session: Session, admin_id: int, answer: Answer, places: Collection[int]
) -> tuple[bool, bool]:
    """A count of the open package by the difference between what it really
    had and what it showed then (docs/decisions/0020, amended by
    inventory-tng-b1z), so a cut since stays as saved: then is the report,
    for the figure a volunteer said, else the page (set_one). Under a key of
    its check, so sending it twice writes it once, and the check settled:
    both, if the check is still as `seen` and the package still at one of
    `places`, locked already; else neither. Whether it was set, and whether
    that left it below zero, asked about again."""
    open_id, seen, left, showed, as_of = answer
    # A Set's, never a Leave's.
    assert left is not None
    row = session.get_one(OpenPackage, open_id)
    key = uuid.uuid5(row.key, f"length {seen}")

    def both() -> bool:
        if not open_packages.settle_check(session, open_id, seen):
            raise RuleBroken("length_unchanged")
        # Moved since its place was locked: its place is not, so nothing
        # more is locked holding it (ledger.py's lock order).
        if row.location_id not in places:
            raise RuleBroken("length_unchanged")
        # Read holding its place: a count or adjustment of it meanwhile is
        # in, never taken off twice.
        then = (
            ledger.left_at(session, open_id, as_of)
            if showed is None
            else showed + ledger.corrected_since(session, open_id, as_of)
        )
        shows = ledger.count_open_package(session, key, admin_id, row, left, then)
        return shows is not None and open_packages.left_below_zero(row, shows, now())

    below, broken = attempt(session, both)
    return broken is None, bool(below)


def _showed(typed: str) -> Decimal | None:
    """What a package showed on the page, as it sent it back, below zero
    too; None if it sent none it could have."""
    try:
        found = Decimal(typed)
    except InvalidOperation:
        return None
    return found if found.is_finite() else None


def _page(sent: str) -> str:
    """The page an answer came from, to go back to: this one or a kind's."""
    return sent if sent in {kind.path for kind in KINDS} else LENGTHS


class Answer(NamedTuple):
    """What to do with a check: set it to a length, or leave it (no
    length); what was seen of it; and, for a Set, what it is set against:
    what the package showed at the ledger transaction `as_of`, its report's
    (ledger.left_at), or, from the page's own form (set_one), what the page
    showed, `showed`, read just after `as_of`, the page's moment."""

    open_id: int
    seen: str
    left: Decimal | None = None
    showed: Decimal | None = None
    as_of: int = 0


def _answer(
    session: Session,
    signed: SignedIn,
    answers: Sequence[Answer],
    only: Item | None,
    *,
    unsaid: int = 0,
    page: str = LENGTHS,
) -> Response:
    """Set or leave each of `answers` still as seen, and say so. Their
    places locked first, by id, as saves take places: only here, never as
    the page is shown."""
    named = OpenPackage.id.in_([each.open_id for each in answers])
    places = set(session.scalars(select(OpenPackage.location_id).where(named)))
    ledger.lock_places(session, places)
    set_, left_, below = 0, 0, 0
    for each in sorted(answers):
        if each.left is None:
            left_ += open_packages.settle_check(session, each.open_id, each.seen)
        else:
            done, still_below = _set(session, signed.admin.id, each, places)
            set_ += done
            below += still_below
    session.commit()
    open_packages.measure_checks_settled("set", set_)
    open_packages.measure_checks_settled("left", left_)
    open_packages.measure_checks_raised("below_zero", below)
    log.info("length checks answered", set=set_, left=left_, admin_id=signed.admin.id)
    if set_:
        notice = "length_set"
    elif left_:
        notice = "length_left"
    elif unsaid:
        notice = "length_none_said"
    else:
        notice = "length_unchanged"
    return redirect(page, notice=notice, **query(only))


@router.post(f"{LENGTHS}/{{open_id}}/set")
def set_one(
    signed: AdminSignedIn,
    session: DbSession,
    open_id: int,
    only: OnlyItem,
    left: Annotated[str, Form()] = "",
    seen: Annotated[str, Form()] = "",
    showed: Annotated[str, Form()] = "",
    as_of: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
) -> Response:
    """Set one to what is typed."""
    row = session.get(OpenPackage, open_id)
    if row is None:
        return not_found()
    back = _page(page)
    unit = session.get_one(Item, row.item_id).unit
    length = quantity(left, positive=False, unit=unit)
    if not isinstance(length, Decimal):
        return redirect(back, notice="length_bad", **query(only))
    # The volunteer's figure, as the box is filled, is set against what it
    # showed at the report; any other, against what the page showed.
    if row.length_reported_in_id is not None and length == row.length_reported:
        answer = Answer(open_id, seen, length, as_of=row.length_reported_in_id)
    else:
        shown, moment = _showed(showed), whole(as_of, 0, INTEGER_MAX)
        if shown is None or moment is None:
            log.info(
                "length not set: the form said not what it showed",
                open_package_id=open_id,
            )
            return redirect(back, notice="length_reload", **query(only))
        answer = Answer(open_id, seen, length, shown, moment)
    return _answer(session, signed, [answer], only, page=back)


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
    return _answer(session, signed, answer, only, page=_page(page))


@router.post(LENGTHS)
def answer_ticked(
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    open_package: Annotated[list[str], Form()] = [],  # noqa: B006  FastAPI copies it.
    do: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
) -> Response:
    """Set each ticked to what its volunteer reported, against what it
    showed at the report, or leave each. A box sends its package's id and
    what was seen of its check, "id seen"."""
    if do not in ("set", "leave"):
        return not_found()
    ticked = {
        open_id: seen
        for each in open_package
        for typed, _, seen in [each.partition(" ")]
        if (open_id := row_id(typed)) is not None
    }
    back = _page(page)
    if not ticked:
        return redirect(back, notice="length_none", **query(only))
    rows = session.scalars(
        select(OpenPackage).where(OpenPackage.id.in_(list(ticked)), *CHECKED)
    ).all()
    answers: list[Answer] = []
    unsaid = 0
    for row in rows:
        seen = ticked[row.id]
        if do == "leave":
            answers.append(Answer(row.id, seen))
        elif row.length_reported is not None:
            # Against what it showed at the report.
            assert row.length_reported_in_id is not None  # Reported with it.
            report = row.length_reported_in_id
            answers.append(Answer(row.id, seen, row.length_reported, as_of=report))
        else:
            unsaid += 1
    return _answer(session, signed, answers, only, unsaid=unsaid, page=back)
