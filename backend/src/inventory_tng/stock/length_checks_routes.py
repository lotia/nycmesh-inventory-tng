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
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Annotated, NamedTuple

import structlog
from babel.support import LazyProxy
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import Session

from inventory_tng.admin.common import (
    SECTION_STOCK,
    TICK_OPEN_PACKAGES,
    admin_page,
    now,
)
from inventory_tng.admin.listing import AskedView, Sort, Sorting, View, arranged
from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.core.db import DbSession, RuleBroken, attempt
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.models import (
    INTEGER_MAX,
    Item,
    OpenPackage,
    whole,
)
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.stock import forms, ledger, open_packages, over_recorded, queries
from inventory_tng.stock.needs_action import (
    Kind,
    OnlyItem,
    of_only,
    query,
)
from inventory_tng.stock.stock_routes import STOCK
from inventory_tng.stock.used_up_routes import BY_PACKAGE, BY_SHOWS

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

LENGTHS = f"{STOCK}/length-checks"

LENGTH_SET = lazy_gettext(
    "Set: a count of each writes the difference from what it showed then; "
    "cuts since stay."
)
LENGTH_LEFT = lazy_gettext("Left as it shows.")
LENGTH_UNCHANGED = lazy_gettext(
    "Nothing changed: it was checked meanwhile, or its place can't be counted. "
    "Look again."
)
LENGTH_BAD = lazy_gettext("Type what is on it: a number, 0 or more.")
LENGTH_RELOAD = lazy_gettext(
    "This page is from before the last change to how lengths are set: load it "
    "again, then set it."
)
LENGTH_NONE_SAID = lazy_gettext(
    "Nobody said what is on those: type what is on each, and set it on its own."
)
NOTICES.update(
    {
        "length_set": LENGTH_SET,
        "length_left": LENGTH_LEFT,
        "length_none": TICK_OPEN_PACKAGES,
        "length_unchanged": LENGTH_UNCHANGED,
        "length_bad": LENGTH_BAD,
        "length_reload": LENGTH_RELOAD,
        "length_none_said": LENGTH_NONE_SAID,
    }
)
LENGTHS_TITLE = lazy_gettext("Lengths to check")

# Waiting for a check, not closed: reported by a volunteer, or gone below
# zero.
OPEN = OpenPackage.closed_at.is_(None)
REPORTED = OpenPackage.length_reported_at.is_not(None)
BELOW = OpenPackage.below_zero_at.is_not(None)
# Gone below zero and reported used up: the confirm page shows its
# shortfall already (stock/used_up_routes.py).
BELOW_ONLY = (BELOW, ~REPORTED, OpenPackage.reported_at.is_(None))
CHECKED = (or_(REPORTED, and_(*BELOW_ONLY)), OPEN)


# The kinds "Needs your action" shows, each with its own page: a package a
# volunteer reported is under the first, whether or not it went below zero
# too.
KINDS = (
    Kind(
        lazy_gettext("Shows the wrong length"),
        f"{LENGTHS}/reported",
        (REPORTED, OPEN),
    ),
    Kind(
        lazy_gettext("Went below zero"),
        f"{LENGTHS}/below-zero",
        (*BELOW_ONLY, OPEN),
    ),
)

# Cuts shown beside each, newest first.
RECENT = 5


# A cut's transaction kind (models.TRANSACTION_KINDS), in words, where no
# volunteer made it: the kind of save that took some of an open package.
CUT_KINDS = {
    # Translators: a kind of save, a noun.
    "move": lazy_gettext("move"),
    # Translators: a kind of save, a noun.
    "receipt": lazy_gettext("receipt"),
    # Translators: a kind of save, a noun.
    "install": lazy_gettext("install"),
    # Translators: a kind of save, a noun.
    "count": lazy_gettext("count"),
    # Translators: a kind of save, a noun.
    "adjustment": lazy_gettext("adjustment"),
    # Translators: a kind of save, a noun.
    "open": lazy_gettext("open"),
    # Translators: a kind of save, a noun.
    "report": lazy_gettext("report"),
}


class Cut(NamedTuple):
    at: datetime
    quantity: Decimal
    kind: str
    volunteer: str | None

    @property
    def by(self) -> Words:
        """Who made it, or, made by no volunteer, its kind in words."""
        return self.volunteer or CUT_KINDS[self.kind]


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
    for open_id, _, *cut in queries.recent_cuts(session, list(cuts), RECENT):
        cuts[open_id].append(Cut(*cut))
    return cuts


# How those to check (open_packages.Shown) sort: as they always have, as
# open_packages.shown() lists them, the first reported used up, then the
# first opened; or by a heading (Ali, 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "waiting",
    {
        "waiting": Sort.keyed(
            lambda each: (
                each.row.reported_at is None,
                each.row.reported_at or each.row.opened_at,
                each.row.opened_at,
                each.row.id,
            )
        ),
        "package": BY_PACKAGE,
        "shows": BY_SHOWS,
    },
)


def _checks_page(
    request: Request,
    signed: SignedIn,
    session: Session,
    only: Item | None,
    title: LazyProxy,
    where: Sequence[ColumnElement[bool]],
    view: View,
) -> Response:
    # Read first: a count or adjustment since is added to what the page
    # showed when it is set (_set).
    moment = queries.latest_transaction_id(session)
    found, view = arranged(open_packages.shown(session, *of_only(where, only)), view)
    # Only the page's own cuts and reporters.
    cuts = recent_cuts(session, [each.row.id for each in found])
    reporters = {each.row.length_reported_by_volunteer_id for each in found}
    volunteers: dict[int | None, str] = {
        volunteer.id: volunteer.name
        for volunteer in queries.volunteers_with_ids(session, reporters)
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
    log.debug("length checks listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "length_checks.html",
        {
            "title": title,
            "here": LENGTHS,
            "all_path": request.url.path,
            "back_link": (STOCK, SECTION_STOCK),
            "only": only,
            "checked": checked,
            "view": view,
            "moment": moment,
            "zone": site_zone(session),
        },
    )


def _view(request: Request, only: OnlyItem, asked: AskedView) -> View:
    """The list's item, sort, page size and page, as its query string
    asks: the page's, and each answer's, which comes back to it so
    (_back)."""
    return View.read(request.url.path, SORTING, asked, kept=query(only))


Viewed = Annotated[View, Depends(_view)]


@router.get(LENGTHS)
def length_checks_page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    view: Viewed,
) -> Response:
    """All those to check, or only one item's."""
    return _checks_page(request, signed, session, only, LENGTHS_TITLE, CHECKED, view)


@router.get(f"{LENGTHS}/{{which}}")
def kind_page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    which: str,
    view: Viewed,
) -> Response:
    """One kind's, as "Needs your action" links to it."""
    kind = next((each for each in KINDS if each.path == f"{LENGTHS}/{which}"), None)
    if kind is None:
        return not_found()
    return _checks_page(request, signed, session, only, kind.title, kind.where, view)


def _set(
    session: Session, admin_id: int, answer: Answer, places: Collection[int]
) -> tuple[bool, bool, int]:
    """A count of the open package by the difference between what it really
    had and what it showed then (docs/decisions/0020, amended by
    inventory-tng-b1z), so a cut since stays as saved: then is the report,
    for the figure a volunteer said, else the page (set_one). Under a key of
    its check, so sending it twice writes it once, and the check settled:
    both, if the check is still as `seen` and the package still at one of
    `places`, locked already; else neither. A count of it puts right the
    end of one recorded more than moved it is in there (over_recorded.counted).
    Whether it was set, whether that left it below zero, asked about again,
    and how many recorded more than moved it closed."""
    open_id, seen, left, showed, as_of = answer
    # A Set's, never a Leave's.
    assert left is not None
    row = session.get_one(OpenPackage, open_id)
    key = uuid.uuid5(row.key, f"length {seen}")

    def both() -> tuple[bool, int]:
        if not open_packages.settle_check(session, open_id, seen):
            raise RuleBroken("length_unchanged")
        # Moved since its place was locked: its place is not, so nothing
        # more is locked holding it (stock/ledger.py's lock order).
        if row.location_id not in places:
            raise RuleBroken("length_unchanged")
        # Read holding its place: a count or adjustment of it meanwhile is
        # in, never taken off twice.
        then = (
            ledger.left_at(session, open_id, as_of)
            if showed is None
            else showed + ledger.corrected_since(session, open_id, as_of)
        )
        counted = ledger.count_open_package(session, key, admin_id, row, left, then)
        if counted is None:
            return False, 0
        # A report is as of its own batch, which may be the one that
        # recorded the move: only a move before that batch came before it.
        closed = over_recorded.counted(
            session,
            admin_id,
            row.location_id,
            as_of if showed is not None else as_of - 1,
            counted.written,
            [(row.item_id, ledger.Form(open_package_id=open_id))],
        )
        return open_packages.left_below_zero(row, counted.shows, now()), closed

    done, broken = attempt(session, both)
    below, closed = done or (False, 0)
    return broken is None, below, closed


def _back(view: View, sent: str) -> View:
    """The page an answer came from, to go back to, this one or a kind's
    (`sent`), as it was: its item, sort, page size and page."""
    return replace(
        view, here=sent if sent in {kind.path for kind in KINDS} else LENGTHS
    )


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
    back: View,
    *,
    unsaid: int = 0,
) -> Response:
    """Set or leave each of `answers` still as seen, and say so. Their
    places locked first, by id, as saves take places: only here, never as
    the page is shown."""
    places = queries.places_of_open(session, [each.open_id for each in answers])
    ledger.lock_places(session, places)
    set_, left_, below, closed = 0, 0, 0, 0
    for each in sorted(answers):
        if each.left is None:
            left_ += open_packages.settle_check(session, each.open_id, each.seen)
        else:
            done, still_below, counted = _set(session, signed.admin.id, each, places)
            set_ += done
            below += still_below
            closed += counted
    session.commit()
    open_packages.measure_checks_settled("set", set_)
    open_packages.measure_checks_settled("left", left_)
    open_packages.measure_checks_raised("below_zero", below)
    over_recorded.measure_counted(closed)
    log.info("length checks answered", set=set_, left=left_, admin_id=signed.admin.id)
    if set_:
        notice = "length_set"
    elif left_:
        notice = "length_left"
    elif unsaid:
        notice = "length_none_said"
    else:
        notice = "length_unchanged"
    return redirect(back.link(), notice=notice)


@router.post(f"{LENGTHS}/{{open_id}}/set")
def set_one(
    signed: AdminSignedIn,
    session: DbSession,
    open_id: int,
    view: Viewed,
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
    back = _back(view, page)
    length = forms.length(session, row.item_id, left)
    if not isinstance(length, Decimal):
        return redirect(back.link(), notice="length_bad")
    # The volunteer's figure, as the box is filled, is set against what it
    # showed at the report; any other, against what the page showed.
    if row.length_reported_in_id is not None and length == row.length_reported:
        answer = Answer(open_id, seen, length, as_of=row.length_reported_in_id)
    else:
        shown, moment = forms.showed(showed), whole(as_of, 0, INTEGER_MAX)
        if shown is None or moment is None:
            log.info(
                "length not set: the form said not what it showed",
                open_package_id=open_id,
            )
            return redirect(back.link(), notice="length_reload")
        answer = Answer(open_id, seen, length, shown, moment)
    return _answer(session, signed, [answer], back)


@router.post(f"{LENGTHS}/{{open_id}}/leave")
def leave_one(
    signed: AdminSignedIn,
    session: DbSession,
    open_id: int,
    view: Viewed,
    seen: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
) -> Response:
    """Leave one as it shows."""
    if session.get(OpenPackage, open_id) is None:
        return not_found()
    answer = [Answer(open_id, seen)]
    return _answer(session, signed, answer, _back(view, page))


@router.post(LENGTHS)
def answer_ticked(
    signed: AdminSignedIn,
    session: DbSession,
    view: Viewed,
    open_package: Annotated[list[str], Form()] = [],  # noqa: B006  FastAPI copies it.
    do: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
) -> Response:
    """Set each ticked to what its volunteer reported, against what it
    showed at the report, or leave each. A box sends its package's id and
    what was seen of its check, "id seen"."""
    if do not in ("set", "leave"):
        return not_found()
    ticked = forms.ticked(open_package)
    back = _back(view, page)
    if not ticked:
        return redirect(back.link(), notice="length_none")
    rows = queries.open_with_ids(session, list(ticked), *CHECKED)
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
    return _answer(session, signed, answers, back, unsaid=unsaid)
