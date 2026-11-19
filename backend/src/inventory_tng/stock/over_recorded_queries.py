"""What the administrators' pages read and show of the lines recorded with
more than was moved (stock/over_recorded.py): those waiting (shown()), how
many (how_many()), those settled (settled()) and how that list sorts
(SETTLED_ORDER), and the words for each."""

from collections.abc import Collection, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Select,
    SQLColumnExpression,
    and_,
    case,
    func,
    select,
)
from sqlalchemy.orm import Session, aliased

from inventory_tng.core.local_time import show_day
from inventory_tng.core.phrases import gettext, in_query, lazy_gettext, number
from inventory_tng.models.admins import Admin
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import (
    LedgerTransaction,
    Movement,
    OpenPackage,
    OverRecorded,
)
from inventory_tng.stock import balance
from inventory_tng.stock.over_recorded import (
    CHECKED_REASONS,
    OPENED_INTO,
    WAITING,
    correction_key,
)
from inventory_tng.web.formatting import amount, plain_amount

# Where stock left, and where it arrived, and the counts that put each
# right.
FROM, TO = aliased(Location), aliased(Location)
LEFT_COUNT, ARRIVED_COUNT = aliased(LedgerTransaction), aliased(LedgerTransaction)


def within(name: str) -> str:
    """`name` as it reads after a sentence's first word: its first letter
    lower-cased when the rest of its first word is lower case already
    (str.islower: every cased letter lower, one at least), so "Spool"
    reads "spool" and "Cat6 cable" "cat6 cable", but "PoE injector",
    "SFP+ module", "UPS" and a one-letter word stay as given (Ali,
    2026-11-15, inventory-tng-r5z). A combining mark is not cased, so it
    changes nothing. Only shown, never stored."""
    return name[:1].lower() + name[1:] if name.partition(" ")[0][1:].islower() else name


class OneCounted(NamedTuple):
    """A check one of whose two ends a count put right: the end counted
    and how ("counted", or "closed" for an opening's open package closed
    since, inventory-tng-nyl) and when, the end still to put right, as a
    sentence starts with each, and as its Correct button names it; and the
    place to count, by id and name, and whether it may be counted
    (balance.countable). A move's ends are its places; an opening's, both
    at one place, its forms: "Sealed spool" where it left, "Open spool" or
    "Loose cat6 cable" where it arrived, the names as within() shows them
    (Ali, 2026-11-15, inventory-tng-r5z)."""

    counted: str
    how: Literal["counted", "closed"]
    on: datetime
    still: str
    correct: str
    place_id: int
    place: str
    countable: bool


class BothRight(NamedTuple):
    """A check both of whose ends are put right while it still waits, as
    when a close skipped it, its row held meanwhile (open_package_closed):
    each end as the row's other wording names it, within a sentence (a
    move's places as named, an opening's forms as within() shows them),
    how it was put right ("counted", or "closed" for an opening's open
    package) and when. Close it settles it as counted (Ali, 2026-11-15,
    inventory-tng-dro)."""

    left: str
    left_on: datetime
    arrived: str
    arrived_how: Literal["counted", "closed"]
    arrived_on: datetime


class MovedWhole(NamedTuple):
    """What an open package moved whole held when it moved: as its count
    box is filled, and in words."""

    count: str
    words: str


class Shown(NamedTuple):
    """A check waiting, as the administrators' pages show it."""

    row: OverRecorded
    item_id: int
    item: str
    unit: str
    # How much more was recorded: whole packages, as times() words them, or
    # so much in the item's unit.
    amount: str
    transaction_id: int
    kind: str
    recorded_at: datetime
    from_place: str | None
    to_place: str | None
    # A move of an open package whole, which is now where it arrived, so
    # cannot be found again where it left: what it held then, for its count
    # to be confirmed or corrected there (confirm_count). None for others,
    # which Correct undoes.
    moved_whole: MovedWhole | None
    # One end of two put right by a count; None if neither is, or it has
    # one end only.
    one_counted: OneCounted | None
    # Both ends of two put right; None unless both are.
    both_right: BothRight | None


def times(num: int | Decimal, package: str) -> str:
    """So many of a package: 2, a multiplication sign, "Spool 305 m"."""
    return gettext(
        "%(num)s \N{MULTIPLICATION SIGN} %(package)s",
        num=number(num),
        package=package,
    )


def amount_words(
    quantity: Decimal, unit: str, package: str | None, size: Decimal | None
) -> str:
    """`quantity` of an item as a volunteer counted it: whole packages of
    one, or so much in its unit."""
    if package is not None and size and quantity % size == 0:
        return times(quantity // size, package)
    after = "" if unit == "each" else f" {unit}"
    return f"{amount(quantity)}{after}"


def from_an_open_package(amount: str) -> str:
    """So much cut from an open package."""
    return gettext("%(amount)s from an open package", amount=amount)


def _amount(
    quantity: Decimal,
    unit: str,
    package: str | None,
    size: Decimal | None,
    from_open: int | None,
    kind: str,
) -> str:
    """How much more was recorded, as a row says it: amount_words(), and
    whether a cut, not an opening, took it from an open package."""
    words = amount_words(quantity, unit, package, size)
    if from_open is not None and kind != "open":
        return from_an_open_package(words)
    return words


def sealed(package: str) -> str:
    """Where an opening left: its package, sealed."""
    return gettext("Sealed %(package)s", package=within(package))


def opened(package: str) -> str:
    """Where an opening arrived, its item followed once opened."""
    return gettext("Open %(package)s", package=within(package))


def loose(item: str) -> str:
    """Where an opening arrived, its item not followed once opened."""
    return gettext("Loose %(item)s", item=within(item))


def _end_names(
    kind: str,
    item: str,
    package: str | None,
    to_open: int | None,
    from_place: str | None,
    to_place: str | None,
) -> tuple[str, str]:
    """A check's two ends, where it left and where it arrived, as a
    sentence starts with each: a move's, its places; an opening's, both at
    one place, its forms."""
    if kind != "open":
        return from_place or "", to_place or ""
    # An opening leaves sealed, and arrives open if its item tracks opened,
    # else loose (ledger._opening_breaks).
    arrived = opened(package or "") if to_open is not None else loose(item)
    return sealed(package or ""), arrived


def _checks(*columns: SQLColumnExpression[Any]) -> Select:
    """A check with its movement's item, form and transaction kind, then
    `columns`, of its places (FROM, TO), the counts that put each end right
    (LEFT_COUNT, ARRIVED_COUNT) and an opening's open package (OPENED_INTO),
    as shown() and settled() read them."""
    package_id = func.coalesce(Movement.from_package_id, Movement.to_package_id)
    return (
        select(
            OverRecorded,
            Item.id,
            Item.name_shown,
            Item.unit,
            ItemPackage.name_shown,
            ItemPackage.size,
            Movement.from_open_package_id,
            Movement.to_open_package_id,
            Movement.quantity,
            LedgerTransaction.kind,
            *columns,
        )
        .join(Movement, Movement.id == OverRecorded.movement_id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .join(Item, Item.id == Movement.item_id)
        .outerjoin(ItemPackage, ItemPackage.id == package_id)
        .outerjoin(FROM, FROM.id == LedgerTransaction.from_location_id)
        .outerjoin(TO, TO.id == LedgerTransaction.to_location_id)
        .outerjoin(LEFT_COUNT, LEFT_COUNT.id == OverRecorded.left_count_id)
        .outerjoin(ARRIVED_COUNT, ARRIVED_COUNT.id == OverRecorded.arrived_count_id)
        .outerjoin(OpenPackage, OPENED_INTO)
    )


def _countable(session: Session, place_ids: Collection[int | None]) -> set[int]:
    """Which of these places may be counted (balance.countable): one query,
    however many, or none for none."""
    ids = {each for each in place_ids if each is not None}
    if not ids:
        return set()
    return set(
        session.scalars(
            select(Location.id).where(Location.id.in_(ids), balance.countable())
        )
    )


def shown(session: Session, *where: ColumnElement[bool]) -> list[Shown]:
    """The checks waiting that `where` picks, by item name, oldest first:
    one query, however many."""
    found = session.execute(
        _checks(
            LedgerTransaction.id,
            LedgerTransaction.created_at,
            FROM.name,
            TO.name,
            LedgerTransaction.from_location_id,
            LedgerTransaction.to_location_id,
            LEFT_COUNT.created_at,
            ARRIVED_COUNT.created_at,
            OpenPackage.closed_at,
        )
        .where(WAITING, *where)
        .order_by(func.lower(Item.name), Item.id, OverRecorded.id)
    ).all()
    # Only a place that may be counted is offered for counting: the end
    # still to put right of one with one end counted, or an opening's open
    # package closed.
    countable = _countable(
        session,
        {
            to_id if left_on else from_id
            for *_, from_id, to_id, left_on, arrived_on, closed_on in found
            if (left_on is None) != (arrived_on is None and closed_on is None)
        },
    )
    lines = []
    for (
        row,
        item_id,
        item,
        unit,
        package,
        size,
        from_open,
        to_open,
        held,
        kind,
        transaction_id,
        at,
        from_place,
        to_place,
        from_id,
        to_id,
        left_on,
        arrived_on,
        closed_on,
    ) in found:
        words = _amount(row.quantity, unit, package, size, from_open, kind)
        whole = from_open is not None and from_open == to_open
        # An opening's open package closed since: Correct skips that end
        # (correct()), so the row says it is closed.
        arrived_how: Literal["counted", "closed"] = "counted"
        if arrived_on is None and closed_on is not None:
            arrived_on, arrived_how = closed_on, "closed"
        one = both = None
        # Moved whole, it has one end only: where it arrived.
        two = from_id is not None and to_id is not None and not whole
        left, arrived = _end_names(kind, item, package, to_open, from_place, to_place)
        # Within a sentence: a form's name lower-cased ("open spool"), a
        # place's as named.
        name = within if kind == "open" else str
        if two and left_on is not None and arrived_on is not None:
            both = BothRight(
                name(left), left_on, name(arrived), arrived_how, arrived_on
            )
        elif two and (left_on is None) != (arrived_on is None):
            # The other end's words, and the button's ("Correct open spool").
            counted_end, how, on, other, still_id, still_place = (
                (left, "counted", left_on, arrived, to_id, to_place)
                if left_on is not None
                else (arrived, arrived_how, arrived_on, left, from_id, from_place)
            )
            one = OneCounted(
                counted_end,
                how,
                on,
                other,
                name(other),
                still_id,
                still_place or "",
                still_id in countable,
            )
        lines.append(
            Shown(
                row,
                item_id,
                item,
                unit,
                words,
                transaction_id,
                kind,
                at,
                from_place,
                to_place if kind != "open" else None,
                MovedWhole(plain_amount(held), amount_words(held, unit, None, None))
                if whole
                else None,
                one,
                both,
            )
        )
    return lines


class Spot(NamedTuple):
    """One of a settled check's places: by id and name, and whether it may
    be counted (balance.countable), for a link to count its item there."""

    id: int
    name: str
    countable: bool


class WasSettled(NamedTuple):
    """A check settled, as the administrators' list of those settled shows
    it (Ali, 2026-11-15, inventory-tng-73p)."""

    at: datetime
    item_id: int
    item: str
    amount: str
    # An opening's both, one place.
    opening: bool
    from_place: Spot | None
    to_place: Spot | None
    # How, as SETTLED_HOW words it, and why, in words.
    how: str
    why: str
    by: str


def held_words(amount: str) -> str:
    """Why a count of one moved whole was confirmed: what it held."""
    return gettext("Held %(amount)s", amount=amount)


def adjusted_at(place: str) -> str:
    """Why a count of one moved whole was corrected: where."""
    return gettext("Adjusted at %(place)s", place=place)


def closed_on_day(end: str, day: str) -> str:
    """An opening's open end, closed since, and the day."""
    return gettext("%(end)s closed %(day)s", end=end, day=day)


def end_only(end: str) -> str:
    """Correct wrote one end only."""
    return gettext("%(end)s only", end=end)


def counted_on(end: str, day: str) -> str:
    """An end of a settled check, and the day a count put it right."""
    # Translators: a place or a form, then the day it was counted ("14 Nov").
    return gettext("%(end)s %(day)s", end=end, day=day)


# How one was settled, in words. Confirm count settles one as checked
# with no reason, or as corrected, an open package moved whole, which
# Correct cannot be (its open package is no longer where it left).
SETTLED_HOW = {
    "corrected": lazy_gettext("Corrected"),
    "confirmed": lazy_gettext("Count confirmed"),
    "count corrected": lazy_gettext("Count corrected"),
    "checked": lazy_gettext("Checked"),
    "counted": lazy_gettext("Counted"),
}
TWO_ADJUSTMENTS = lazy_gettext("Two adjustments")
NO_ADJUSTMENT = lazy_gettext("No adjustment")

SETTLED = OverRecorded.settled_at.is_not(None)

# What the list of those settled sorts by, a column each, as settled()
# reads them (Ali, 2026-11-15, inventory-tng-73p): How by its words, as
# settled() words it, in the language spoken when the list is asked for;
# Where by where it left, then where it arrived.
SETTLED_ORDER: dict[str, tuple[Any, ...]] = {
    "settled": (OverRecorded.settled_at,),
    "item": (func.lower(Item.name_shown),),
    "amount": (OverRecorded.quantity,),
    "where": (func.lower(FROM.name), func.lower(TO.name)),
    "how": (
        case(
            (
                and_(
                    OverRecorded.settled_how == "checked",
                    OverRecorded.checked_reason.is_(None),
                ),
                in_query(SETTLED_HOW["confirmed"]),
            ),
            (
                and_(
                    OverRecorded.settled_how == "corrected",
                    Movement.from_open_package_id == Movement.to_open_package_id,
                ),
                in_query(SETTLED_HOW["count corrected"]),
            ),
            *(
                (OverRecorded.settled_how == how, in_query(SETTLED_HOW[how]))
                for how in ("corrected", "checked", "counted")
            ),
        ),
    ),
    "by": (func.lower(Admin.username),),
}


def _spot(
    place_id: int | None, name: str | None, countable: Collection[int]
) -> Spot | None:
    return (
        None if place_id is None else Spot(place_id, name or "", place_id in countable)
    )


def how_many(session: Session, *where: ColumnElement[bool]) -> int:
    """How many checks `where` picks (of OverRecorded and Movement), such
    as WAITING or SETTLED."""
    return (
        session.scalar(
            select(func.count())
            .select_from(OverRecorded)
            .join(Movement, Movement.id == OverRecorded.movement_id)
            .where(*where)
        )
        or 0
    )


def settled(
    session: Session,
    zone: ZoneInfo,
    *where: ColumnElement[bool],
    order: Sequence[Any] = (),
    limit: int | None,
    offset: int = 0,
) -> list[WasSettled]:
    """Those settled that `where` picks (of Movement), in `order` (of
    SETTLED_ORDER, then OverRecorded.id), else newest first; `limit` of
    them, None for all: the same few queries, however many. Days in
    `zone`."""
    found = session.execute(
        _checks(
            LedgerTransaction.idempotency_key,
            LedgerTransaction.from_location_id,
            FROM.name,
            LedgerTransaction.to_location_id,
            TO.name,
            LEFT_COUNT.created_at,
            ARRIVED_COUNT.created_at,
            OpenPackage.closed_at,
            Admin.username,
        )
        .join(Admin, Admin.id == OverRecorded.settled_by_admin_id)
        .where(SETTLED, *where)
        .order_by(*(order or (OverRecorded.settled_at.desc(), OverRecorded.id.desc())))
        .limit(limit)
        .offset(offset)
    ).all()
    # An opening corrected, its open package closed: whether Correct wrote
    # its open end off, or found it closed and skipped it, by the key it
    # writes under, as no time tells (a close's is the server's clock as it
    # began, perhaps before Correct's, and waited for its place).
    off = {
        correction_key(each.idempotency_key, check, found=False): check.id
        for each in found
        # closed_at: an opening's only (OPENED_INTO).
        if (check := each.OverRecorded).settled_how == "corrected"
        and check.arrived_count_id is None
        and each.closed_at is not None
    }
    written_off = (
        {
            off[key]
            for key in session.scalars(
                select(LedgerTransaction.idempotency_key).where(
                    LedgerTransaction.idempotency_key.in_(off)
                )
            )
        }
        if off
        else set()
    )
    # Each place a link to count its item there, if it may be counted.
    countable = _countable(
        session,
        {
            place
            for each in found
            for place in (each.from_location_id, each.to_location_id)
        },
    )
    lines = []
    for (
        row,
        item_id,
        item,
        unit,
        package,
        size,
        from_open,
        to_open,
        held,
        kind,
        _,
        from_id,
        from_place,
        to_id,
        to_place,
        left_on,
        arrived_on,
        closed_on,
        by,
    ) in found:
        whole = from_open is not None and from_open == to_open
        left, arrived = _end_names(kind, item, package, to_open, from_place, to_place)
        at, how, reason = row.settled_at, row.settled_how, row.checked_reason
        # Settled: the CHECK sets all three or none.
        assert at is not None
        assert how is not None
        if how == "checked" and reason is None:
            how = "confirmed"
            why = held_words(amount_words(held, unit, None, None))
        elif how == "checked" and reason is not None:
            why = str(CHECKED_REASONS[reason])
            if row.checked_words:
                why += f" {row.checked_words}"
        elif how == "corrected" and whole:
            how = "count corrected"
            why = adjusted_at(to_place or "")
        elif how == "corrected":
            # The ends Correct wrote (correct()): each it has but one a
            # count put right, or an opening's open package closed before
            # (written_off).
            written = [
                end
                for end, there in (
                    (left, from_id is not None and row.left_count_id is None),
                    (
                        arrived,
                        to_id is not None
                        and row.arrived_count_id is None
                        and (closed_on is None or row.id in written_off),
                    ),
                )
                if there
            ]
            why = (
                str(TWO_ADJUSTMENTS)
                if len(written) > 1
                else end_only(written[0])
                if written
                else str(NO_ADJUSTMENT)
            )
        else:
            # Each end it has by the count that put it right, or an
            # opening's open package closed (counted(), open_package_closed()).
            said = []
            if left_on is not None:
                said.append(counted_on(left, show_day(left_on, zone)))
            if arrived_on is not None:
                said.append(counted_on(arrived, show_day(arrived_on, zone)))
            elif closed_on is not None:
                said.append(closed_on_day(arrived, show_day(closed_on, zone)))
            if kind == "open":
                said[1:] = [within(each) for each in said[1:]]
            why = ", ".join(said)
        lines.append(
            WasSettled(
                at,
                item_id,
                item,
                _amount(row.quantity, unit, package, size, from_open, kind),
                kind == "open",
                _spot(from_id, from_place, countable),
                _spot(to_id, to_place, countable),
                str(SETTLED_HOW[how]),
                why,
                by,
            )
        )
    return lines
