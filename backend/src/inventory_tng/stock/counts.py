"""Counts and adjustments: an administrator's, from stock/count_routes.py
(through stock/store_counts.py) and stock/counts_routes.py, a length check
(stock/length_checks_routes.py), a package closed
(open_packages.close) or a check settled (over_recorded)
(docs/decisions/0003, 0020). They put the records right for what was
found at a place, as of when its form was shown (moments.Moment), and
write through the ledger's one writer (ledger.insert_transaction).
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import Literal, NamedTuple

import structlog
from opentelemetry import metrics
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import RuleBroken, enforce
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import exists_where
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, OpenPackage
from inventory_tng.stock import moments
from inventory_tng.stock.balance import countable, left_on
from inventory_tng.stock.ledger import (
    LOOSE,
    Entry,
    Form,
    Lines,
    by_item,
    each_once,
    entries,
    insert_transaction,
    lock_place,
    repeated,
)

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)


class NotCountable(RuleBroken):
    """location_countable, with the place that refused it, so a page names
    that one, not one it reads again after the place may have changed."""

    def __init__(self, location_id: int) -> None:
        super().__init__("location_countable")
        self.location_id = location_id


def _require_countable(session: Session, location_id: int) -> None:
    """NotCountable unless the place may be counted or adjusted, read with
    it locked (lock_place): a retire waits for that lock, and it for a
    retire, so a place retired since the page checked is refused."""
    if not exists_where(session, Location.id == location_id, countable()):
        log.debug("rule broken", rule="location_countable", location_id=location_id)
        raise NotCountable(location_id)


# Lines of an item and an amount alone, loose: a count's.
ItemLines = Sequence[tuple[int, Decimal]]


recorded = meter.create_counter(
    "inventory.counts.recorded",
    unit="{transaction}",
    description="Counts and adjustments written, by kind",
)
withheld = meter.create_counter(
    "inventory.count.lines.withheld",
    unit="{line}",
    description="Count lines not written because the item moved at the place "
    "while it was counted, so it is counted again",
)


class Written(NamedTuple):
    """A count's or an adjustment's transaction, as written."""

    id: int
    kind: str
    lines: int


def _correct(
    session: Session,
    key: uuid.UUID,
    kind: Literal["count", "adjustment"],
    admin_id: int,
    header: Mapping[str, object],
    lines: Lines,
) -> list[Written]:
    """A count's or an adjustment's transaction, as written: none if its
    key is written already. The one place either is written, so each is
    measured and logged here, once committed (_recorded), whichever page
    wrote it, and never twice (inventory-tng-vvp0)."""
    full = {"kind": kind, "admin_id": admin_id, **header}
    transaction = insert_transaction(session, key, full, lines)
    if transaction is None:
        return []
    written = Written(transaction.id, kind, len(lines))
    once_committed(session, _recorded, admin_id, written)
    return [written]


class Counted(NamedTuple):
    differed: bool
    # Items counted that moved at the place since the form was shown, with
    # what came in and what went out since. Not written: count them again.
    moved: dict[int, tuple[Decimal, Decimal]]
    # What this send wrote: nothing if an earlier send of it did.
    written: list[Written]
    # What this send put right, as of its moment: each item counted by its
    # total (no form), and each form of one counted by form. Nothing if an
    # earlier send of it did.
    put_right: list[tuple[int, Form | None]]


def _recorded(admin_id: int, transaction: Written) -> None:
    """Measure and log a count or an adjustment committed."""
    recorded.add(1, {"kind": transaction.kind})
    log.info(
        f"{transaction.kind} recorded",
        ledger_transaction_id=transaction.id,
        admin_id=admin_id,
        lines=transaction.lines,
    )


def _withheld(admin_id: int, location_id: int, lines: int) -> None:
    """Measure and log a count's lines withheld as moved, once committed."""
    withheld.add(lines)
    log.info(
        "count lines withheld",
        location_id=location_id,
        admin_id=admin_id,
        lines=lines,
    )


def _hold_place(session: Session, location_id: int, *keys: uuid.UUID) -> int | None:
    """Lock the place to count or adjust it, and return the first
    transaction there written under any of `keys` (the form was sent
    before), or None. Only when None, NotCountable unless the place may be
    counted (_require_countable)."""
    lock_place(session, location_id)
    sent = session.scalar(
        select(func.min(LedgerTransaction.id)).where(
            LedgerTransaction.idempotency_key.in_(keys), moments.at_place(location_id)
        )
    )
    if sent is None:
        _require_countable(session, location_id)
    return sent


def _write_count(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    location_id: int,
    into: list[Entry],
    out: list[Entry],
    *,
    when_unchanged: bool = False,
) -> list[Written]:
    """A count's corrections written: what it found, into the place under
    `key`, and what it lost, out of it under a key made from it (_lost_key).
    `when_unchanged`, a count that found nothing either way is written into
    the place all the same, so it still says when the place was counted."""
    written: list[Written] = []
    if into or (when_unchanged and not out):
        header = {"to_location_id": location_id}
        written += _correct(session, key, "count", admin_id, header, into)
    if out:
        header = {"from_location_id": location_id}
        written += _correct(session, _lost_key(key), "count", admin_id, header, out)
    return written


def _lost_key(key: uuid.UUID) -> uuid.UUID:
    """The key a count's losses are written under, made from its own, so
    sending it twice writes them once."""
    return uuid.uuid5(key, "lost")


# A count's line of a form (docs/decisions/0020): an item, the form it is
# in at the place, and how much was counted of it.
FormLines = Sequence[tuple[int, Form, Decimal]]


def count_corrections(
    counted: FormLines, then: Mapping[tuple[int, Form], Decimal]
) -> tuple[list[Entry], list[Entry]]:
    """What a count by form finds and loses: for each form counted, the
    difference from what the records said (`then`), into the place in that
    form, or out of it."""
    found: list[Entry] = []
    lost: list[Entry] = []
    for item_id, form, amount in counted:
        difference = amount - then.get((item_id, form), Decimal(0))
        if difference > 0:
            found.append(Entry(item_id, difference, LOOSE, form))
        elif difference < 0:
            lost.append(Entry(item_id, -difference, form, LOOSE))
    return found, lost


def record_count(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    location_id: int,
    counted: ItemLines,
    shown: moments.Moment,
    *,
    by_form: FormLines = (),
    across: Collection[int] = (),
    aside: Mapping[tuple[int, Form], Decimal] | None = None,
) -> Counted:
    """Put the records right for what was counted at a place, as they stood
    when the form was shown: as of `shown`, the place's latest ledger
    transaction then (docs/decisions/0003). Items not counted are left as
    they are.

    The form's moment was read without the place's lock: a save under way
    there then, committed since, has an id up to it that the form never
    saw. Holding the place, every such save has committed, so one is
    found by counting the place's transactions up to the moment: then the
    figures shown are not the records as of it, and the count is refused
    (changed_since_shown).

    A count of a store and its areas on one form (inventory-tng-8i4.51.4)
    is one record_count for each place, each under a key of its own, with
    `across` every place the form's moment was read across (moments.moment),
    each checking the moment across them all. The caller holds them all
    first, then the rows they all name (stock/store_counts.py).

    An item that moved there since may have been counted before the move or
    after it, so its line is not written: it comes back to count again. The
    rest are written against the records as of `shown.as_of`, so a move saved
    meanwhile still applies on top.

    Found more, or nothing differed, is a count into the place under the key;
    found less, a count out of it under a key made from it, so sending it
    twice writes it once, and finds what moved as the first send did. A
    count whose every line moved writes nothing.

    Items counted `by_form` (docs/decisions/0020) are put right form by
    form: sealed packages of each, each open package, loose; and withheld,
    as moved, if anything of them moved there, an opening too.

    `aside`, by item and form, is what the records there held as of
    `shown` that the form counts apart, as moves from areas gone from it
    (stock/count_arrivals.py): the lines count what was there without it,
    so it is taken from what the records said, and stays.

    NotCountable (location_countable) at a place that may not be counted,
    unless the form was sent there before, so it gets its first answer;
    RuleBroken (movement_item_once) for an item counted on two lines, or
    both by total and by form."""
    each_once(entries(counted))
    forms_counted = [(item_id, form) for item_id, form, _ in by_form]
    enforce("movement_item_once", when=bool(repeated(forms_counted)))
    enforce(
        "movement_item_once",
        when=bool({item_id for item_id, _ in counted} & {i for i, _ in forms_counted}),
    )
    counted_at = dict(counted)
    places = {location_id, *across}
    sent = _hold_place(session, location_id, key, _lost_key(key))
    as_of = shown.as_of
    after = LedgerTransaction.id > as_of
    if sent is not None:
        after &= LedgerTransaction.id < sent
    enforce(
        "changed_since_shown",
        when=moments.seen_up_to(session, places, as_of) != shown.seen,
    )
    moved, flows = moments.flows(
        session, location_id, after, LedgerTransaction.id <= as_of, items=counted_at
    )
    aside = aside or {}
    aside_total = by_item(aside)
    then = {
        item_id: came - went - aside_total.get(item_id, Decimal(0))
        for item_id, (came, went) in flows.items()
    }
    form_items = {item_id for item_id, _ in forms_counted}
    form_moved, form_then = moments.form_flows(
        session, location_id, (after, LedgerTransaction.id <= as_of), form_items
    )
    for item_id, _ in form_moved:
        came, went = moved.get(item_id, (Decimal(0), Decimal(0)))
        flow = form_moved[(item_id, _)]
        moved[item_id] = (came + flow[0], went + flow[1])
    kept_forms = [line for line in by_form if line[0] not in moved]
    kept = counted_at.keys() - moved.keys()
    # Counted by total, loose; never also by form (movement_item_once).
    into, out = count_corrections(
        [(item_id, LOOSE, counted_at[item_id]) for item_id in kept] + kept_forms,
        {(item_id, LOOSE): amount for item_id, amount in then.items()}
        | {
            form: came - went - aside.get(form, Decimal(0))
            for form, (came, went) in form_then.items()
        },
    )
    # A count of nothing at all still says when the place was counted.
    anything = counted_at or by_form
    written = _write_count(
        session,
        key,
        admin_id,
        location_id,
        into,
        out,
        when_unchanged=bool(kept or kept_forms or not anything),
    )
    put_right: list[tuple[int, Form | None]] = []
    if sent is None:
        put_right = [(item_id, None) for item_id in sorted(kept)]
        put_right += [(item_id, form) for item_id, form, _ in kept_forms]
        # Withheld by no earlier send of it.
        if moved:
            once_committed(session, _withheld, admin_id, location_id, len(moved))
    return Counted(bool(into or out), moved, written, put_right)


def record_arrivals(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    location_id: int,
    lines: FormLines,
) -> list[Written]:
    """Moves to a place fixed on a count form (stock/count_arrivals.py):
    each line's difference, in its form there, found if more, lost if
    less, as a count's corrections are. What it wrote: nothing if sent
    before under its key. NotCountable (location_countable), as
    record_count."""
    if _hold_place(session, location_id, key, _lost_key(key)) is not None:
        return []
    found, lost = count_corrections(lines, {})
    return _write_count(session, key, admin_id, location_id, found, lost)


class OpenCounted(NamedTuple):
    """An open package counted: what it shows then, and what was written,
    nothing if it already showed that."""

    shows: Decimal
    written: list[Written]


def count_open_package(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    row: OpenPackage,
    left: Decimal,
    showed: Decimal,
) -> OpenCounted | None:
    """Put an open package right (docs/decisions/0020, amended by
    inventory-tng-b1z): `left` is what it really had when it `showed`
    this, so what it shows now moves by the difference, and a cut since
    stays as saved. A count of only this package, at its place, so a cut of
    another does not stop it. What it shows then, and what this send
    wrote; None if sent before under its key, and so written once, then.
    NotCountable (location_countable), as record_count."""
    place = row.location_id
    if _hold_place(session, place, key, _lost_key(key)) is not None:
        return None
    shows = left_on(session, [row])[row.id]
    now = shows + left - showed
    form = Form(open_package_id=row.id)
    found, lost = count_corrections(
        [(row.item_id, form, now)], {(row.item_id, form): shows}
    )
    return OpenCounted(now, _write_count(session, key, admin_id, place, found, lost))


def record_adjustment(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    location_id: int,
    item_id: int,
    amount: Decimal,
    *,
    found: bool,
    reason_id: int,
    form: Form = LOOSE,
) -> list[Written]:
    """Stock found at a place, or written off from it, for a reason, in
    `form` there (docs/decisions/0020). What it wrote: nothing if its key
    was written already. NotCountable (location_countable), as record_count."""
    if _hold_place(session, location_id, key) is not None:
        return []
    side = "to_location_id" if found else "from_location_id"
    header = {"reason_id": reason_id, side: location_id}
    line = (
        Entry(item_id, amount, LOOSE, form) if found else Entry(item_id, amount, form)
    )
    return _correct(session, key, "adjustment", admin_id, header, [line])
