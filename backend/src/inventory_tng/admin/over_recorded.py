"""Lines recorded with more than was moved (over_recorded.py,
inventory-tng-0gg): "Needs your action" on the home page leads here, for
one item or all. Correct writes adjustments that undo the difference on
each side of its transaction, with the reason Recorded more than moved;
Mark checked settles it as it is, for one corrected another way, saying
why it is right as it is (Ali, 2026-11-15, inventory-tng-zgy). An open
package moved whole is now where it arrived: Confirm count settles it with
the count it held then, as it was, or corrected there by an adjustment
(Ali, 2026-11-15). One a count put right at one end says so, and offers
Correct, Count or Mark checked for the other (inventory-tng-qs8)."""

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
from inventory_tng.admin.locations import changeable
from inventory_tng.admin.needs_action import (
    ItemWaiting,
    Kind,
    OnlyItem,
    query,
    query_string,
)
from inventory_tng.admin.stock import STOCK
from inventory_tng.db import DbSession, Gone, attempt
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

OVER_RECORDED = f"{STOCK}/recorded-more"

TITLE = "Recorded more than moved"
# A count left blank, in quantity()'s words for one not a number.
TYPE_A_NUMBER = "Type a number, such as 12 or 0.5."
# Mark checked with no reason, or put right another way with no words.
SAY_WHY = "Say why it is right as it is."
# As "Needs your action" lists it (needs_action.py): its own rows, not open
# packages, so no open packages' where.
KIND = Kind(TITLE, OVER_RECORDED, ())

NOTICES.update(
    {
        "over_recorded_corrected": "Corrected.",
        "over_recorded_checked": "Marked checked.",
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


@router.get(OVER_RECORDED)
def over_recorded_page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    notice: str = "",
    brought: str = "",
) -> Response:
    """All those waiting, or only one item's; after a place is brought
    back, its name in the notice."""
    place = None
    if notice == "over_recorded_brought_back" and (ident := row_id(brought)):
        place = session.get(Location, ident)
    said = f"{place.name} brought back. Correct it now." if place else None
    return _page(request, signed, session, only, notice=said)


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
) -> Response:
    """The page, with the problems of a count or a Mark checked sent, and
    what was typed; a refusal's alert; or a notice in words of its own.
    Mark checked sent for an open package moved whole (`checking`), whose
    row offers only Confirm count, is asked again above the list, as a
    refusal offers it; one no longer listed is told it changed meanwhile,
    as it would be with a reason."""
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
            "waiting": waiting,
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
        409 if refused else None,
    )


def _refusal(
    session: Session, check_id: int, do: str, broken: str, at: int | None
) -> dict[str, Any] | None:
    """What the page says of a correction or count refused by a rule, and
    offers under it: Mark checked, and, refused at a retired place that
    holds nothing (ledger.NotCountable, `at`), Bring back that place, which
    the places page would (not a volunteer's custody, which comes back with
    them). The place as refused, whatever has changed since. None if it is
    settled meanwhile."""
    found = over_recorded.shown(session, OverRecorded.id == check_id)
    if not found:
        return None
    (each,) = found
    if at is None:
        words = f"Not changed: {rule_words(broken)}"
        return {"shown": each, "words": words, "place": None}
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
    }


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
) -> Response:
    """Correct one, mark it checked, saying why, or confirm the count of an
    open package moved whole, if still as seen. Its places locked first, by
    id, as saves take places."""
    check = session.get(OverRecorded, check_id)
    if do not in ("correct", "checked", "count") or check is None:
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
        words = canonical(typed_words.strip()) if reason == "other" else None
        problem = _why_problem(reason, words)
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
                checking=check_id,
            )
    ledger.lock_places(session, over_recorded.places_of(session, check_id))
    admin_id = signed.admin.id
    # The place that refused it (ledger.NotCountable), as it was then.
    refused_at: int | None = None

    def settle() -> tuple[over_recorded.Settled, list[ledger.Written]]:
        nonlocal refused_at
        try:
            if do == "correct":
                done = over_recorded.correct(session, check_id, seen, admin_id)
                return "corrected", done
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


def _why_problem(reason: str, words: str | None) -> str | None:
    """What is wrong with why one is marked checked: no reason picked, or
    put right another way with no words, or too many."""
    if reason not in over_recorded.CHECKED_REASONS or words == "":
        return SAY_WHY
    if words is not None and len(words) > OVER_RECORDED_WORDS_MAX:
        return f"Up to {OVER_RECORDED_WORDS_MAX} characters."
    return None
