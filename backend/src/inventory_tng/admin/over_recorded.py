"""Lines recorded with more than was moved (over_recorded.py,
inventory-tng-0gg): "Needs your action" on the home page leads here, for
one item or all. Correct writes adjustments that undo the difference on
each side of its transaction, with the reason Recorded more than moved;
Mark checked settles it as it is, for one corrected another way, saying
why it is right as it is (Ali, 2026-11-15, inventory-tng-zgy). An open
package moved whole is now where it arrived: Confirm count settles it with
the count it held then, as it was, or corrected there by an adjustment
(Ali, 2026-11-15). One a count put right at one end says so, and offers
Correct, Count or Mark checked for the other (inventory-tng-qs8); one
with both ends right says so, and offers Close it, which settles it as
counted (Ali, 2026-11-15, inventory-tng-dro)."""

from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import ledger, locations, over_recorded
from inventory_tng.admin.common import admin_page, quantity, row_id, rule_words
from inventory_tng.admin.counts import COUNTS
from inventory_tng.admin.listing import Sort, Sorting, View, arranged, paged
from inventory_tng.admin.listing import order as order_of
from inventory_tng.admin.locations import changeable
from inventory_tng.admin.needs_action import (
    ItemWaiting,
    Kind,
    OnlyItem,
    query,
    query_string,
)
from inventory_tng.admin.stock import RECORDED_MORE as OVER_RECORDED
from inventory_tng.admin.stock import RECORDED_MORE_SETTLED, STOCK
from inventory_tng.db import DbSession, Gone, attempt, exists_where
from inventory_tng.local_time import site_zone
from inventory_tng.models import (
    OVER_RECORDED_WORDS_MAX,
    Item,
    Location,
    Movement,
    OverRecorded,
    canonical,
)
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

TITLE = "Recorded more than moved"
SETTLED_TITLE = "Recorded more than moved, settled"
# A count left blank, in quantity()'s words for one not a number.
TYPE_A_NUMBER = "Type a number, such as 12 or 0.5."
# Mark checked with no reason, or put right another way with no words.
SAY_WHY = "Say why it is right as it is."
# Words typed with a reason other than another way: asked, never dropped
# (Ali, 2026-11-15, inventory-tng-zgy).
WORDS_FOR_ANOTHER = (
    "You wrote why, but picked another reason. Pick Put right another way "
    "to keep your words, or clear them."
)
# As "Needs your action" lists it (needs_action.py): its own rows, not open
# packages, so no open packages' where.
KIND = Kind(TITLE, OVER_RECORDED, ())

NOTICES.update(
    {
        "over_recorded_corrected": "Corrected.",
        "over_recorded_checked": "Marked checked.",
        "over_recorded_counted": "Closed.",
        "over_recorded_confirmed": "Confirmed.",
        "over_recorded_count_corrected": "Count corrected.",
        "over_recorded_unchanged": "Not changed: someone answered or changed "
        "it meanwhile. Reload to see it as it is.",
        # The page names the place brought back (?brought=) when it can.
        "over_recorded_brought_back": "Brought back. Correct it now.",
    }
)
# Refusals said as one answered or changed meanwhile: settled or said again
# (_settle), not an open package moved whole any more, or its adjustment's
# key written by another (unique).
MEANWHILE = frozenset({"over_recorded_unchanged", "over_recorded_not_whole", "unique"})


def by_item(session: Session) -> list[ItemWaiting]:
    """Those waiting, one line per item, as "Needs your action" lists
    them: each amount, said once with how many."""
    grouped: dict[int, list[over_recorded.Shown]] = {}
    for each in over_recorded.shown(session):
        grouped.setdefault(each.item_id, []).append(each)
    return [
        ItemWaiting(
            item_id,
            found[0].item,
            ", ".join(each.amount for each in found),
            len(found),
            Decimal(0),
            found[0].unit,
        )
        for item_id, found in grouped.items()
    ]


# How those waiting sort: by item, oldest first, as they always have, or
# by a heading; the newest transaction first by Transaction (Ali,
# 2026-11-15, inventory-tng-qwb).
WAITING_SORTING = Sorting(
    "item",
    {
        "item": Sort.keyed(lambda each: each.item.lower()),
        "amount": Sort.keyed(lambda each: each.row.quantity),
        "where": Sort.keyed(
            lambda each: (
                (each.from_place or "").lower(),
                (each.to_place or "").lower(),
            )
        ),
        "transaction": Sort.keyed(lambda each: each.transaction_id, first="desc"),
    },
)


@router.get(OVER_RECORDED)
def over_recorded_page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    notice: str = "",
    brought: str = "",
    sort: str = "",
    order: str = "",
    per: str = "",
    page: str = "",
) -> Response:
    """All those waiting, or only one item's, sorted and in pages as asked;
    after a place is brought back, its name in the notice."""
    place = None
    if notice == "over_recorded_brought_back" and (ident := row_id(brought)):
        place = session.get(Location, ident)
    said = f"{place.name} brought back. Correct it now." if place else None
    view = View.read(
        OVER_RECORDED,
        WAITING_SORTING,
        sort=sort,
        order=order,
        per=per,
        page=page,
        kept=query(only),
    )
    return _page(request, signed, session, only, notice=said, view=view)


def _views(waiting: int, only: Item | None) -> list[tuple[str, str, str]]:
    """The links between those waiting and those settled, for _views.html,
    each of one item still if one (Ali, 2026-11-15, inventory-tng-73p)."""
    kept = query_string(only)
    return [
        (f"{OVER_RECORDED}{kept}", f"Waiting ({waiting})", "waiting"),
        (f"{RECORDED_MORE_SETTLED}{kept}", "Settled", "settled"),
    ]


def _why(each: over_recorded.WasSettled) -> str:
    return each.why.lower()


# Every column sorts, either way; newest first unless asked (Ali,
# 2026-11-15, inventory-tng-73p). Why by its words as shown, which
# settled() words in Python, so sorted there (settled_page).
SETTLED_SORTING = Sorting(
    "settled",
    {
        **{
            name: Sort.of(*by, first="desc" if name == "settled" else "asc")
            for name, by in over_recorded.SETTLED_ORDER.items()
        },
        "why": Sort.keyed(_why),
    },
)


@router.get(RECORDED_MORE_SETTLED)
def settled_page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    page: str = "",
    sort: str = "",
    order: str = "",
    per: str = "",
) -> Response:
    """Those settled, newest first unless a heading sorts them, in pages,
    all or one item's: how each was settled, why, by whom and when (Ali,
    2026-11-15, inventory-tng-73p). Read only."""
    where = [Movement.item_id == only.id] if only else []
    view = View.read(
        RECORDED_MORE_SETTLED,
        SETTLED_SORTING,
        sort=sort,
        order=order,
        per=per,
        page=page,
        kept=query(only),
    ).counted(over_recorded.how_many(session, over_recorded.SETTLED, *where))
    zone = site_zone(session)
    if view.by == "why":
        # Every one, newest first, worded, then sorted by its words: the
        # same few queries.
        every = over_recorded.settled(session, zone, *where, limit=None)
        every.sort(key=_why, reverse=view.desc)
        rows, view = paged(every, view)
    else:
        rows = over_recorded.settled(
            session,
            zone,
            *where,
            order=order_of(view, OverRecorded.id),
            limit=view.per_page,
            offset=view.offset,
        )
    log.debug(
        "over recorded settled listed",
        found=view.found,
        page=view.page,
        sort=view.by,
        per_page=view.per_page,
    )
    return admin_page(
        request,
        signed,
        "over_recorded_settled.html",
        {
            "title": SETTLED_TITLE,
            "here": RECORDED_MORE_SETTLED,
            "back_link": (STOCK, "Stock"),
            "only": only,
            "recorded_views": _views(
                over_recorded.how_many(session, over_recorded.WAITING, *where), only
            ),
            "rows": rows,
            "view": view,
            "zone": zone,
            "counts_path": COUNTS,
            "stock_path": STOCK,
        },
    )


def _page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
    *,
    alert: str | None = None,
    refused: dict[str, Any] | None = None,
    notice: str | None = None,
    checking: int | None = None,
    view: View | None = None,
) -> Response:
    """The page, with the problems of a count or a Mark checked sent, and
    what was typed; a refusal's alert; or a notice in words of its own.
    Mark checked sent for an open package moved whole (`checking`), whose
    row offers only Confirm count, is asked again above the list, as a
    refusal offers it; one no longer listed is told it changed meanwhile,
    as it would be with a reason. A Mark checked refused for its reason
    from a refusal's alert draws that alert again, with the problem
    under it: 400, as any problem typed, not 409, as the refusal was."""
    where = [Movement.item_id == only.id] if only else []
    waiting = over_recorded.shown(session, *where)
    asking = None
    if checking is not None:
        found = next((each for each in waiting if each.row.id == checking), None)
        if found is None:
            return redirect(
                OVER_RECORDED, notice="over_recorded_unchanged", **query(only)
            )
        if found.moved_whole:
            asking = found
    shown, view = arranged(
        waiting, view or View.read(OVER_RECORDED, WAITING_SORTING, kept=query(only))
    )
    log.debug("over recorded listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "over_recorded.html",
        {
            "title": TITLE,
            "here": OVER_RECORDED,
            "back_link": (STOCK, "Stock"),
            "only": only,
            "query": query_string(only),
            "waiting": shown,
            "view": view,
            "recorded_views": _views(len(waiting), only),
            "reasons": over_recorded.CHECKED_REASONS,
            "words_max": OVER_RECORDED_WORDS_MAX,
            "seen": over_recorded.seen,
            "zone": site_zone(session),
            "counts_path": COUNTS,
            "problems": problems,
            "values": values,
            "alert": alert,
            "refused": refused,
            "asking": asking,
            "notice": notice,
        },
        400 if problems else 409 if refused else None,
    )


def _refusal(
    session: Session, check_id: int, do: str, broken: str, at: int | None
) -> dict[str, Any] | None:
    """What the page says of a correction or count refused by a rule, and
    offers under it: Mark checked, and, refused at a retired place that
    holds nothing (ledger.NotCountable, `at`), Bring back that place, which
    the places page would (not a volunteer's custody, which comes back with
    them). The place as refused, whatever has changed since. None if it is
    settled meanwhile. Drawn again for Mark checked sent from it (`do`
    "checked"), it says what its row offers: Confirm count for an open
    package moved whole, else Correct."""
    found = over_recorded.shown(session, OverRecorded.id == check_id)
    if not found:
        return None
    (each,) = found
    if at is None:
        words = f"Not changed: {rule_words(broken)}"
        return {"shown": each, "words": words, "place": None, "at": None}
    if do == "checked":
        do = "count" if each.moved_whole else "correct"
    place = session.get_one(Location, at)
    # correct() finds it again where it left, writes it off where it arrived.
    if do != "correct":
        so = "its count can't be corrected there"
    elif at == over_recorded.ends(session, check_id)[0]:
        so = f"the {each.amount} can't be found there again"
    else:
        so = f"the {each.amount} can't be written off there"
    return {
        "shown": each,
        "words": f"Not changed: {place.name} is retired and holds nothing, so {so}.",
        "place": place if place.kind != "custody" else None,
        "at": at,
    }


def _refused_again(
    session: Session, check_id: int, marker: str
) -> dict[str, Any] | None:
    """The refusal a Mark checked was sent from, its alert's hidden marker
    the place that refused it (_refusal), read again, not trusted: None if
    it names none of the check's places, or one that may be counted now,
    as once brought back, so the problem is shown in the row alone."""
    at = row_id(marker)
    if at is None or at not in over_recorded.places_of(session, check_id):
        return None
    if exists_where(session, Location.id == at, ledger.countable()):
        return None
    return _refusal(session, check_id, "checked", "location_countable", at)


@router.post(f"{OVER_RECORDED}/{{check_id}}/bring-back")
def bring_back(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    check_id: int,
    only: OnlyItem,
    seen: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
) -> Response:
    """Bring back a retired place one's correction was refused at, as the
    places page does (locations.restore, under the same locks), so it can
    be corrected there: one of its transaction's places, not a volunteer's
    custody, while it waits as seen. Brought back already, as after a
    second tap, it says so the same way. Before answer(), whose path
    would take it."""
    ident = row_id(place)
    location = changeable(session, ident) if ident else None
    # A check that isn't there has no places.
    if location is None or location.id not in over_recorded.places_of(
        session, check_id
    ):
        return not_found()

    def restore() -> bool:
        try:
            locations.restore(session, location)
            brought = True
        except Gone:  # Current already.
            brought = False
        # After the place, as answer() takes them: one settled meanwhile
        # brings nothing back.
        over_recorded.waiting(session, check_id, seen)
        return brought

    brought, broken = attempt(session, restore)
    session.commit()
    if broken is not None:
        log.info(
            "over recorded place not brought back",
            over_recorded_id=check_id,
            location_id=location.id,
            rule=broken,
        )
        if broken == "over_recorded_unchanged":
            return redirect(
                OVER_RECORDED, notice="over_recorded_unchanged", **query(only)
            )
        alert = f"Not changed: {rule_words(broken)}"
        return _page(request, signed, session, only, alert=alert)
    if brought:
        over_recorded.measure_brought_back()
        log.info(
            "over recorded place brought back",
            over_recorded_id=check_id,
            location_id=location.id,
            admin_id=signed.admin.id,
        )
    return redirect(
        OVER_RECORDED,
        notice="over_recorded_brought_back",
        brought=str(location.id),
        **query(only),
    )


@router.post(f"{OVER_RECORDED}/{{check_id}}/{{do}}")
def answer(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    check_id: int,
    do: str,
    only: OnlyItem,
    seen: Annotated[str, Form()] = "",
    typed: Annotated[str, Form(alias="count")] = "",
    reason: Annotated[str, Form()] = "",
    typed_words: Annotated[str, Form(alias="words")] = "",
    marker: Annotated[str, Form(alias="refused")] = "",
) -> Response:
    """Correct one, close one with both ends right, mark it checked, saying
    why, or confirm the count of an open package moved whole, if still as
    seen. Its places locked first, by id, as saves take places."""
    check = session.get(OverRecorded, check_id)
    if do not in ("correct", "close", "checked", "count") or check is None:
        return not_found()
    count = None
    if do == "count":
        unit = session.scalars(
            select(Item.unit)
            .join(Movement, Movement.item_id == Item.id)
            .where(Movement.id == check.movement_id)
        ).one()
        count = quantity(typed, positive=False, unit=unit, required=TYPE_A_NUMBER)
        if not isinstance(count, Decimal):
            field = f"count-{check_id}"
            log.debug("over recorded count refused", over_recorded_id=check_id)
            return _page(
                request, signed, session, only, {field: str(count)}, {field: typed}
            )
    words = None
    if do == "checked":
        stripped = canonical(typed_words.strip())
        problem = _why_problem(reason, stripped)
        if problem is not None:
            # Never the words: free text.
            log.debug("over recorded checked refused", over_recorded_id=check_id)
            values = {f"reason-{check_id}": reason, f"words-{check_id}": typed_words}
            return _page(
                request,
                signed,
                session,
                only,
                {f"reason-{check_id}": problem},
                values,
                refused=_refused_again(session, check_id, marker),
                checking=check_id,
            )
        # Only another way has words: _why_problem refuses them otherwise.
        words = stripped or None
    ledger.lock_places(session, over_recorded.places_of(session, check_id))
    admin_id = signed.admin.id
    # The place that refused it (ledger.NotCountable), as it was then.
    refused_at: int | None = None

    def settle() -> tuple[over_recorded.Settled, list[ledger.Written]]:
        nonlocal refused_at
        try:
            if do == "correct":
                return over_recorded.correct(session, check_id, seen, admin_id)
            if do == "close":
                over_recorded.close(session, check_id, seen, admin_id)
                return "counted", []
            if count is not None:
                return over_recorded.confirm_count(
                    session, check_id, seen, admin_id, count
                )
            over_recorded.mark_checked(session, check_id, seen, admin_id, reason, words)
            return "checked", []
        except ledger.NotCountable as broken:
            refused_at = broken.location_id
            raise

    settled, broken = attempt(session, settle)
    session.commit()
    if settled is None:
        log.info("over recorded not settled", over_recorded_id=check_id, rule=broken)
        refused = None
        if broken is not None and broken not in MEANWHILE:
            refused = _refusal(session, check_id, do, broken, refused_at)
        if refused is None:
            return redirect(
                OVER_RECORDED, notice="over_recorded_unchanged", **query(only)
            )
        values = {f"count-{check_id}": typed} if count is not None else None
        return _page(request, signed, session, only, values=values, refused=refused)
    how, written = settled
    ledger.corrected(admin_id, 0, written)
    over_recorded.measure_settled(how, reason if do == "checked" else None)
    notice = f"over_recorded_{how}"
    if do == "count":
        # By how it was settled, not by what was written: an adjustment
        # whose key was written already writes nothing, and still corrects.
        notice = (
            "over_recorded_count_corrected"
            if how == "corrected"
            else "over_recorded_confirmed"
        )
    return redirect(OVER_RECORDED, notice=notice, **query(only))


def _why_problem(reason: str, words: str) -> str | None:
    """What is wrong with why one is marked checked, `words` stripped: no
    reason picked, put right another way with no words or too many, or
    words with another reason."""
    if reason not in over_recorded.CHECKED_REASONS:
        return SAY_WHY
    if reason != "other":
        return WORDS_FOR_ANOTHER if words else None
    if not words:
        return SAY_WHY
    if len(words) > OVER_RECORDED_WORDS_MAX:
        return f"Up to {OVER_RECORDED_WORDS_MAX} characters."
    return None
