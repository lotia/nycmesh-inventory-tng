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

A place's count can show only some items: one (`?item=`, as the pages of
what needs an administrator's action take it), those whose names match a
search (`?q=`, as the items list searches), or both. Only those are
counted; the rest stay as they are, as any item left uncounted does. The
filter rides on the form's action, so a form shown again keeps it.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Any, NamedTuple
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import ColumnElement, case, func
from sqlalchemy.orm import Session

from inventory_tng.admin.common import (
    ADMIN,
    SECTION_COUNTS,
    admin_page,
    current_rows,
    now,
    text_form,
)
from inventory_tng.admin.listing import UNASKED, AskedView, Sort, Sorting, View, order
from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue.items_routes import SEARCHED
from inventory_tng.core.db import DbSession, by_name, refusal
from inventory_tng.core.phrases import Words, gettext, in_query, lazy_gettext, ngettext
from inventory_tng.models import (
    INTEGER_MAX,
    AdjustmentReason,
    Admin,
    Item,
    ItemPackage,
    LedgerTransaction,
    Location,
    OpenPackage,
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
from inventory_tng.site_settings.routes import SETTINGS
from inventory_tng.stock import needs_action, open_packages, over_recorded, queries
from inventory_tng.stock.balance import countable, retired_holding_stock_count, takeable
from inventory_tng.stock.forms import (
    CLOSED_MEANWHILE,
    LOOSE_FIELD,
    ON_THE_FORM_TWICE,
    OPENED,
    SEALED,
    adjustment,
    by_form,
    item_counts,
    lines,
    named_twice,
)
from inventory_tng.stock.ledger import (
    LOOSE,
    Counted,
    Form,
    corrected,
    expected,
    expected_forms,
    hold,
    lock_place,
    moment,
    record_adjustment,
    record_count,
)
from inventory_tng.stock.needs_action import OnlyItem
from inventory_tng.stock.open_packages import tracks_opened
from inventory_tng.web.forms import RULES, row_id

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

COUNTS = f"{ADMIN}/counts"
ADJUSTMENTS = f"{COUNTS}/adjustments"
# The ledger rules a race can break past the page's checks (module
# docstring).
WHOLE = "movement_whole_units"
COUNTABLE = "location_countable"
# An open package found that its item does not follow once opened, and a
# form counted twice: said as rules are.
UNTRACKED = "open_package_untracked"
ONCE = "movement_item_once"
TWICE = lazy_gettext(
    "An item is on this form more than once. Each item is counted once, so "
    "it isn't clear which number is right: nothing was saved."
)
COUNTS_TITLE = lazy_gettext("Counts and adjustments")


def count_title(place: str) -> str:
    return gettext("Count %(place)s", place=place)


def moved_meanwhile(saved: int) -> str:
    """A count refused as some of it moved while it was counted, the lines
    saved besides, if any."""
    if not saved:
        return gettext("These moved while you counted, so count them again.")
    return ngettext(
        "Saved %(num)d line. These moved while you counted, so count them again.",
        "Saved %(num)d lines. These moved while you counted, so count them again.",
        saved,
    )


COUNTED_NOTICE = lazy_gettext("Counted. The records now match what you found.")
COUNTED_SAME = lazy_gettext("Counted: everything was as the records say.")
ADJUSTED = lazy_gettext("Recorded.")
NOTICES.update(
    {
        "counted": COUNTED_NOTICE,
        "counted_same": COUNTED_SAME,
        "adjusted": ADJUSTED,
    }
)

TextForm = Annotated[dict[str, str], Depends(text_form)]


def badge(session: Session) -> Badge | None:
    """Beside Counts: how many retired places hold stock, to count
    (balance.retired_holding_stock)."""
    many = retired_holding_stock_count(session)
    return Badge(many, retired_holding(many)) if many else None


def _items(session: Session) -> list[Item]:
    """What can be counted or adjusted: current items, and retired ones with
    stock left, which may still be written off."""
    return by_name(session, Item, takeable())


def _places(session: Session) -> list[Location]:
    """Where can be counted or adjusted (balance.countable)."""
    return by_name(session, Location, countable())


# Translators: a count, as a kind of save, before what it saved.
COUNT_KIND = lazy_gettext("Count")
# How it sorts: newest first, as it always has, or by a heading; What by
# its words, Count or the reason (Ali, 2026-11-15, inventory-tng-qwb), in
# the language spoken.
SORTING = Sorting(
    "when",
    {
        "when": Sort.of(LedgerTransaction.id, first="desc"),
        "what": Sort.of(
            case(
                (LedgerTransaction.kind == "count", func.lower(in_query(COUNT_KIND))),
                else_=func.lower(AdjustmentReason.name),
            )
        ),
        "where": Sort.of(func.lower(Location.name)),
        "by": Sort.of(func.lower(Admin.username)),
    },
)


def _view(
    asked: AskedView,
    place: str = "",
    kind: str = "",
    admin: str = "",
) -> View:
    """The list's view as its query string asks: a place or an admin not a
    number, or a kind not offered, is no filter (Ali, 2026-11-15)."""
    place_id = whole(place, 1, INTEGER_MAX)
    admin_id = whole(admin, 1, INTEGER_MAX)
    filters = {
        "place": str(place_id or ""),
        "kind": kind if kind in queries.KINDS else "",
        "admin": str(admin_id or ""),
    }
    return View.read(COUNTS, SORTING, asked, filters=filters)


def _recent(session: Session, view: View) -> tuple[list[dict[str, Any]], View]:
    """The counts and adjustments the view picks, in its order, one page
    of them, with their lines; and the view counted."""
    where: list[ColumnElement[bool]] = []
    if place := view.filtered("place"):
        where.append(queries.count_place == int(place))
    if kind := view.filtered("kind"):
        where.append(LedgerTransaction.kind == kind)
    if admin := view.filtered("admin"):
        where.append(LedgerTransaction.admin_id == int(admin))
    view = view.counted(queries.counts_and_adjustments_found(session, *where))
    rows = queries.counts_and_adjustments(
        session,
        *where,
        order=order(view, LedgerTransaction.id),
        limit=view.per_page,
        offset=view.offset,
    )
    log.debug("counts listed", found=view.found, sort=view.by, page=view.page)
    lines: dict[int, list[tuple[Item, Decimal]]] = {}
    for transaction_id, item, amount in queries.count_lines(
        session, [row[0].id for row in rows]
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
    ], view


def _counts_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    view: View | None = None,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    recent, view = _recent(session, view or _view(UNASKED))
    # The filters offer where and by whom there are any.
    return admin_page(
        request,
        signed,
        "counts.html",
        {
            "title": COUNTS_TITLE,
            "here": COUNTS,
            "settings_path": SETTINGS,
            "places": _places(session),
            "items": _items(session),
            "reasons": current_rows(session, AdjustmentReason),
            "recent": recent,
            "view": view,
            "kinds": queries.KINDS,
            "count_kind": COUNT_KIND,
            "counted_places": queries.places_counted(session),
            "counters": queries.counters(session),
            "key": uuid.uuid4(),
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


@router.get(COUNTS)
def counts(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    view: Annotated[View, Depends(_view)],
) -> Response:
    return _counts_page(request, session, signed, view=view)


@router.get(f"{COUNTS}/place")
def choose_place(location_id: str = "") -> Response:
    """The "Count a place" form, sent as a GET, goes to that place's count."""
    if not location_id.isdigit():
        return redirect(COUNTS)
    return redirect(f"{COUNTS}/{location_id}")


class Shown(NamedTuple):
    """Which items a place's count shows (module docstring): one, those
    matching a search, or both; neither for all."""

    only: Item | None = None
    search: str = ""

    def ids(self, session: Session) -> set[int] | None:
        """The items shown, of any at all; None for every one."""
        if not self.search:
            return None if self.only is None else {self.only.id}
        return queries.item_ids_matching(
            session,
            SEARCHED.matching(self.search, SEARCHED.rules(session)),
            None if self.only is None else self.only.id,
        )

    def query(self) -> str:
        """The query string that shows the count so, for links and the
        form's action."""
        query = needs_action.query(self.only) | (
            {"q": self.search} if self.search else {}
        )
        return f"?{urlencode(query)}" if query else ""


def shown_items(only: OnlyItem, q: str = "") -> Shown:
    """The filter a count's query string asks for: an odd search is none,
    as the items list reads it."""
    return Shown(only, SEARCHED.read(q=q).search)


ShownItems = Annotated[Shown, Depends(shown_items)]


def _count_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    place: Location,
    as_of: int,
    *,
    shown: Shown,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
    items: Collection[int] = (),
    recount: Mapping[int, tuple[Decimal, Decimal, Decimal]] | None = None,
    alert: Words | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """The count form, with the records as of `as_of`: of everything there,
    or a recount of `items` only, which the form carries. With `recount`, a
    recount of its items: what was typed for each and what came in and went
    out while it was counted. Of those, only the items `shown`, but a
    recount's every one."""
    items = items or (recount or {}).keys()
    was = expected(session, place.id, as_of=as_of, items=items or None)
    if items:
        was = {item_id: was.get(item_id, Decimal(0)) for item_id in items}
    listed = _items(session)
    named = {item.id: item for item in listed}
    if shown.only is not None and not items and shown.only.id in named:
        # The item asked for, to count, though the records say none is here.
        was.setdefault(shown.only.id, Decimal(0))
    openings = over_recorded.openings_waiting(session, place.id, as_of)
    if not items:
        # An opening recorded more than opened, waiting here: its item, to
        # count, though the records say none is here.
        for item_id in openings:
            was.setdefault(item_id, Decimal(0))
    if (ids := shown.ids(session)) is not None:
        if not items:
            # A recount's items were shown when counted: each is asked for
            # again, though the search may no longer find it.
            was = {item_id: n for item_id, n in was.items() if item_id in ids}
        listed = [item for item in listed if item.id in ids]
    if missing := was.keys() - named.keys():
        # What the records had then but cannot be counted now, or what the
        # form's items, sent back, name that is no item at all.
        missed = queries.items_with_ids(session, missing)
        named |= {item.id: item for item in missed}
    rows = sorted(
        (
            (named[item_id], amount)
            for item_id, amount in was.items()
            if item_id in named
        ),
        key=lambda row: row[0].name.lower(),
    )
    forms = _forms(session, place.id, as_of, [item.id for item, _ in rows], openings)
    return admin_page(
        request,
        signed,
        "count.html",
        {
            "title": count_title(place.name),
            "here": f"{COUNTS}/{place.id}",
            "back_link": (COUNTS, SECTION_COUNTS),
            "place": place,
            "rows": rows,
            "forms": forms,
            "found_packages": [
                found
                for found in _followed(session)
                if ids is None or found[0].item_id in ids
            ],
            "recount": recount,
            "items": sorted(items),
            "others": [item for item in listed if item.id not in was],
            "key": (values or {}).get("key") or uuid.uuid4(),
            "as_of": as_of,
            "shown": shown,
            "alert": alert,
            "problems": problems,
            "values": values,
        },
        400 if problems else status_code,
    )


@router.get(f"{COUNTS}/{{location_id:int}}")
def count_form(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    location_id: int,
    shown: ShownItems,
) -> Response:
    place = queries.countable_place(session, location_id)
    if place is None:
        return not_found()
    return _count_page(
        request, session, signed, place, moment(session, place.id), shown=shown
    )


# How the count form asks for each form of an item.
LOOSE_WORDS = lazy_gettext("Loose")


def sealed_how_many(package: str) -> str:
    return gettext("%(package)s, sealed: how many", package=package)


def opened_left(package: str, day: str) -> str:
    return gettext("%(package)s, opened %(day)s: left", package=package, day=day)


class FormRow(NamedTuple):
    """One form of an item at a place, as the count form asks for it
    (docs/decisions/0020): its field, words, and the records as of the
    form; for an open package, its id."""

    field: str
    words: Words
    amount: Decimal
    open_id: int | None = None


def _forms(
    session: Session,
    place_id: int,
    as_of: int,
    item_ids: Sequence[int],
    openings: Mapping[int, Collection[int]],
) -> dict[int, list[FormRow]]:
    """The items at the place whose stock was in more than loose units, as
    of the form, each form a row: sealed packages, by how many; open
    packages; loose. Others are counted by their total, as before, but an
    item of an opening waiting here, recorded more than opened (`openings`,
    over_recorded.openings_waiting): it is counted by form, with a row of
    each package it opened sealed, though the records hold none, as a total
    puts neither of its ends right (docs/decisions/0020)."""
    found = expected_forms(session, place_id, as_of, item_ids)
    packages = {
        package.id: package for package in queries.packages_of_items(session, item_ids)
    }
    opened = {row.id: row for row in queries.open_at(session, place_id, item_ids)}
    held: dict[int, dict[Form, Decimal]] = {}
    for (item_id, form), amount in found.items():
        # One closed since is not counted: its row is gone from the place.
        if form.open_package_id is None or form.open_package_id in opened:
            held.setdefault(item_id, {})[form] = amount
    for row in opened.values():
        held.setdefault(row.item_id, {}).setdefault(
            Form(open_package_id=row.id), Decimal(0)
        )
    for item_id in item_ids:
        for package_id in openings.get(item_id, ()):
            held.setdefault(item_id, {}).setdefault(
                Form(package_id=package_id), Decimal(0)
            )
    rows: dict[int, list[FormRow]] = {}
    for item_id, forms in held.items():
        if set(forms) <= {LOOSE}:
            continue
        each = rows.setdefault(item_id, [])

        # Sealed by package, then open ones oldest first, then loose.
        def order(pair: tuple[Form, Decimal]) -> tuple[int, int]:
            form = pair[0]
            if form.package_id is not None:
                return 0, form.package_id
            return (1, form.open_package_id) if form.open_package_id else (2, 0)

        for form, amount in sorted(forms.items(), key=order):
            if form.package_id is not None:
                package = packages[form.package_id]
                each.append(
                    FormRow(
                        f"{SEALED}{item_id}_{package.id}",
                        sealed_how_many(package.name_shown),
                        amount / package.size,
                    )
                )
            elif form.open_package_id is not None:
                row = opened[form.open_package_id]
                name = packages[row.package_id].name_shown
                each.append(
                    FormRow(
                        f"{OPENED}{row.id}",
                        opened_left(name, f"{row.opened_at:%Y-%m-%d}"),
                        amount,
                        row.id,
                    )
                )
            else:
                each.append(FormRow(f"{LOOSE_FIELD}{item_id}", LOOSE_WORDS, amount))
        if not any(row.field.startswith(LOOSE_FIELD) for row in each):
            each.append(FormRow(f"{LOOSE_FIELD}{item_id}", LOOSE_WORDS, Decimal(0)))
    return rows


def _followed(session: Session) -> list[tuple[ItemPackage, str, str]]:
    """Current packages of current items followed once opened, with their
    item's name and unit: what an open package nobody recorded may be."""
    return [
        (package, name, unit)
        for package, name, unit, item_tracks in queries.current_packages(session)
        if tracks_opened(item_tracks, package.track_opened)
    ]


@router.post(f"{COUNTS}/{{location_id:int}}")
def count(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    location_id: int,
    form: TextForm,
    shown: ShownItems,
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
    counted, problems = item_counts(session, found)
    forms, form_problems = by_form(session, form, place.id)
    problems |= form_problems
    if repeats := named_twice(found):
        problems |= dict.fromkeys(repeats, ON_THE_FORM_TWICE)
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
            alert=TWICE if repeats else problems.get(CLOSED_MEANWHILE),
            items=sent_items,
            shown=shown,
        )
    closed: list[open_packages.Closed | None] = []
    # Recorded more than moved, closed by this count at each end.
    settled = 0

    # An item counted by its total holds loose units alone (_forms): with
    # an open package of it found, its total is its loose units.
    found_item = forms.found[0].item_id if forms.found else None
    totals = [(item_id, amount) for item_id, amount in counted if item_id != found_item]
    loose = [
        (item_id, LOOSE, amount) for item_id, amount in counted if item_id == found_item
    ]

    def put_right() -> Counted:
        nonlocal settled
        # The place, then the items, as every count and save takes them
        # (stock/ledger.py), before a package found is held.
        lock_place(session, place.id)
        if forms.found is not None:
            hold(session, {"to_location_id": place.id}, [forms.found[0].item_id])
        lines = [*forms.lines, *loose]
        if forms.found is not None:
            package, left = forms.found
            with session.begin_nested() as finding:
                made = open_packages.make(
                    session, uuid.uuid5(key, "found"), package.id, place.id
                )
                found = (package.item_id, Form(open_package_id=made.id), left)
                counted_now = record_count(
                    session,
                    key,
                    signed.admin.id,
                    place.id,
                    totals,
                    as_of,
                    by_form=[*lines, found],
                )
                if package.item_id in counted_now.moved:
                    # Its item moved meanwhile: asked again, found again then.
                    finding.rollback()
        if forms.found is None or package.item_id in counted_now.moved:
            counted_now = record_count(
                session, key, signed.admin.id, place.id, totals, as_of, by_form=lines
            )
        # Closed after the count: one not there, or used up, as said. Each
        # closed is at 0, as counted so, for an opening that arrived in it.
        emptied: list[tuple[int, Form]] = []
        for open_id, reason in forms.close:
            done = open_packages.close(
                session, open_id, signed.admin.id, now(), reason=reason
            )
            closed.append(done)
            if done is not None:
                item_id = session.get_one(OpenPackage, open_id).item_id
                emptied.append((item_id, Form(open_package_id=open_id)))
        settled = over_recorded.counted(
            session,
            signed.admin.id,
            place.id,
            as_of,
            counted_now.written,
            counted_now.put_right,
            emptied,
        )
        return counted_now

    result, broken = refusal(
        session, put_right, WHOLE, COUNTABLE, UNTRACKED, ONCE, CLOSED_MEANWHILE
    )
    if broken is not None:
        log.info("count refused by a rule", location_id=place.id, rule=broken)
        if broken == COUNTABLE:
            # As for a place there is not, as the form's own page answers.
            return not_found()
        # Checked again, the units as they are now mark the lines it broke.
        _, problems = item_counts(session, found)
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
            shown=shown,
            status_code=400,
        )
    assert result is not None
    session.commit()
    corrected(signed.admin.id, place.id, result.written, result.withheld)
    open_packages.measure_closing("count", closed)
    over_recorded.measure_counted(settled)
    if not result.moved:
        return redirect(
            COUNTS,
            notice="counted" if result.differed else "counted_same",
            section="count",
        )
    # What was typed of each item withheld: its total, or its forms'.
    amounts = dict(counted)
    for item_id, _, amount in forms.lines:
        amounts[item_id] = amounts.get(item_id, Decimal(0)) + amount
    saved = len(amounts) - len(result.moved)
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
        alert=moved_meanwhile(saved),
        shown=shown,
        status_code=409,
    )


@router.post(ADJUSTMENTS)
def adjust(
    request: Request, signed: AdminSignedIn, session: DbSession, form: TextForm
) -> Response:
    key = form_key(form.get("key", ""))
    asked, problems = adjustment(session, form, key)
    if asked is None:
        return _counts_page(request, session, signed, problems=problems, values=form)
    place, item, amount, direction, reason, key = asked
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
