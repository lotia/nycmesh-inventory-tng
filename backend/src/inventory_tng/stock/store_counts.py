"""A count form saved (stock/counts_routes.py): a place and every area in
it on the form, each counted at its own place (inventory-tng-8i4.51.4),
and the moves to them from areas gone from it whose amounts were fixed,
after every count (stock/count_arrivals.py, inventory-tng-ygk5).
Everything the places' counts and fixes hold is held for them all before
any is written, in the order stock/ledger.py gives (inventory-tng-nfia). A
place with no areas is a form of one place, saved the same way.

A form saved keeps a digest of what it said under its key
(core/sent_forms.py). Sent again under that key, as the browser's Back
sends it, saying anything otherwise, it is refused whole
(count_sent_otherwise), nothing written; sent as it was, it is answered as
before, each write under its key once (Ali, 2026-11-22,
inventory-tng-kro9).
"""

import hashlib
import json
import uuid
from collections.abc import Collection, Mapping
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import enforce
from inventory_tng.core.sent_forms import digest_spent, spend
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import open_packages, over_recorded
from inventory_tng.stock.counts import (
    Counted,
    FormLines,
    Written,
    record_arrivals,
    record_count,
)
from inventory_tng.stock.forms import PlaceCount
from inventory_tng.stock.ledger import LOOSE, Form, hold_across, lock_places
from inventory_tng.stock.moments import Moment


class StoreCounted(NamedTuple):
    """A count form saved: each place's count, by id, and what the moves
    fixed on it wrote."""

    places: dict[int, Counted]
    fixed: list[Written]


def record_store_count(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    places: Collection[int],
    at: Moment,
    counts: Mapping[int, PlaceCount],
    when: datetime,
    *,
    aside: Mapping[int, Mapping[tuple[int, Form], Decimal]],
    fixed: Mapping[int, FormLines],
) -> StoreCounted:
    """Put the records right at each place of `counts`, of a form showing
    `places`, as of `at`, read across them all (moments.moment): every
    place held, then the rows they name (_hold_named), then each written
    (_count_at), then, by place, the moves to it `fixed`, under a key of
    their own (counts.record_arrivals). `aside`, by place, what came from
    the areas gone from the form, which its lines count apart
    (counts.record_count). NotCountable names the place that may not be
    counted; RuleBroken (count_sent_otherwise) for a form saved before
    that says otherwise now (module docstring)."""
    lock_places(session, places)
    _hold_named(session, counts.keys(), at, counts.values(), fixed)
    said = _digest(places, at, counts, aside, fixed)
    if not spend(session, key, said):
        enforce("count_sent_otherwise", when=digest_spent(session, key) != said)
    counted = {
        place_id: _count_at(
            session,
            key,
            admin_id,
            place_id,
            count,
            at,
            places,
            when,
            aside=aside.get(place_id, {}),
        )
        for place_id, count in counts.items()
    }
    # After every count, which would take them for moves since its moment.
    written = [
        each
        for place_id, lines in sorted(fixed.items())
        for each in record_arrivals(
            session, uuid.uuid5(key, f"arrived {place_id}"), admin_id, place_id, lines
        )
    ]
    return StoreCounted(counted, written)


def _digest(
    places: Collection[int],
    at: Moment,
    counts: Mapping[int, PlaceCount],
    aside: Mapping[int, Mapping[tuple[int, Form], Decimal]],
    fixed: Mapping[int, FormLines],
) -> bytes:
    """A digest (hashlib's SHA-256) of all a count form's save writes from:
    the same for one sent again unchanged, whatever order its lines came
    in or however its amounts were written, as 5 or 5.0. Of `aside`, only
    the places counted: what went elsewhere is read up to no moment
    (count_arrivals.gone_moves), so it may grow after the save."""
    said = (
        places,
        at,
        {
            place_id: (
                count.counted,
                count.forms.lines,
                count.forms.close,
                count.forms.found and (count.forms.found[0].id, count.forms.found[1]),
                aside.get(place_id, {}),
            )
            for place_id, count in counts.items()
        },
        fixed,
    )
    return hashlib.sha256(json.dumps(_plain(said)).encode()).digest()


def _plain(value: object) -> object:
    """`value` as JSON says it, put together one way: a mapping's pairs and
    a list's members sorted, amounts normalized; a tuple, a record, kept
    in its order."""
    match value:
        case Decimal():
            return str(value.normalize())
        case tuple():
            return [_plain(each) for each in value]
        case Mapping():
            return _plain(list(value.items()))
        case list():
            return sorted((_plain(each) for each in value), key=json.dumps)
        case _:
            return value


def _place_key(key: uuid.UUID, place_id: int) -> uuid.UUID:
    """The key a form's count at one of its places is written under."""
    return uuid.uuid5(key, str(place_id))


def _hold_named(
    session: Session,
    places: Collection[int],
    at: Moment,
    counts: Collection[PlaceCount],
    fixed: Mapping[int, FormLines],
) -> None:
    """Hold the items, packages and open packages the places' counts and
    the moves `fixed` name (ledger.hold_across), then the checks waiting
    there they may settle (over_recorded.hold_waiting), so writing them one
    place after another never takes a row of a lower id after a higher
    (inventory-tng-nfia)."""
    fixes = [line for lines in fixed.values() for line in lines]
    forms = [form for count in counts for _, form, _ in count.forms.lines] + [
        form for _, form, _ in fixes
    ]
    found = [count.forms.found[0] for count in counts if count.forms.found]
    items = hold_across(
        session,
        item_ids=[item_id for count in counts for item_id, _ in count.counted]
        + [item_id for count in counts for item_id, _, _ in count.forms.lines]
        + [package.item_id for package in found]
        + [item_id for item_id, _, _ in fixes],
        package_ids=[form.package_id for form in forms if form.package_id is not None]
        + [package.id for package in found],
        open_ids=[
            form.open_package_id for form in forms if form.open_package_id is not None
        ]
        + [open_id for count in counts for open_id, _ in count.forms.close],
    )
    over_recorded.hold_waiting(session, places, at.as_of, items)


def _count_at(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    place_id: int,
    count: PlaceCount,
    at: Moment,
    across: Collection[int],
    when: datetime,
    *,
    aside: Mapping[tuple[int, Form], Decimal],
) -> Counted:
    """Put the records right at one place of a count form, holding every
    place on it (`across`) and the rows they name: its count, under a key
    of its own made from the form's (counts.record_count), an open package
    nobody recorded found there, the open packages closed there, and the
    ends there of lines recorded more than moved that it puts right.
    `aside`, what came there from the areas gone from the form, counted
    apart (counts.record_count)."""
    forms = count.forms
    # An item counted by its total holds loose units alone (the count
    # form's rows): with an open package of it found, its total is its
    # loose units.
    found_item = forms.found[0].item_id if forms.found else None
    totals = [(item_id, n) for item_id, n in count.counted if item_id != found_item]
    lines = [
        *forms.lines,
        *((item_id, LOOSE, n) for item_id, n in count.counted if item_id == found_item),
    ]
    place_key = _place_key(key, place_id)

    def counted_by(by_form: FormLines) -> Counted:
        return record_count(
            session,
            place_key,
            admin_id,
            place_id,
            totals,
            at,
            by_form=by_form,
            across=across,
            aside=aside,
        )

    # Its item's figures, if a package found was not kept (below).
    withheld: dict[int, tuple[Decimal, Decimal]] = {}
    if forms.found is not None:
        package, left = forms.found
        found_key = uuid.uuid5(key, "found")
        with session.begin_nested() as finding:
            made = open_packages.make(session, found_key, package.id, place_id)
            found = (package.item_id, Form(open_package_id=made.id), left)
            counted_now = counted_by([*lines, found])
            if package.item_id in counted_now.moved:
                # Its item moved meanwhile: asked again, found again then,
                # though no line but the package counted it.
                withheld = counted_now.moved
                finding.rollback()
    if forms.found is None or withheld:
        counted_now = counted_by(lines)
        counted_now = counted_now._replace(moved=withheld | counted_now.moved)
    # Closed after the count: one not there, or used up, as said. Each
    # closed is at 0, as counted so, for an opening that arrived in it.
    emptied: list[tuple[int, Form]] = []
    for open_id, reason in forms.close:
        if open_packages.close(
            session, open_id, admin_id, when, how="count", reason=reason
        ):
            item_id = session.get_one(OpenPackage, open_id).item_id
            emptied.append((item_id, Form(open_package_id=open_id)))
    over_recorded.counted(
        session,
        admin_id,
        place_id,
        at.as_of,
        counted_now.written,
        counted_now.put_right,
        emptied,
    )
    return counted_now
