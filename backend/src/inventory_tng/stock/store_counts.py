"""A count form saved (stock/counts_routes.py): a place and every area in
it on the form, each counted at its own place (inventory-tng-8i4.51.4).
Everything the places' counts hold is held for them all before any is
written, in the order stock/ledger.py gives (inventory-tng-nfia). A place
with no areas is a form of one place, saved the same way.
"""

import uuid
from collections.abc import Collection, Mapping
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import open_packages, over_recorded
from inventory_tng.stock.counts import Counted, FormLines, record_count
from inventory_tng.stock.forms import PlaceCount
from inventory_tng.stock.ledger import LOOSE, Form, hold_across, lock_places
from inventory_tng.stock.moments import Moment


def record_store_count(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    places: Collection[int],
    at: Moment,
    counts: Mapping[int, PlaceCount],
    when: datetime,
) -> dict[int, Counted]:
    """Put the records right at each place of `counts`, of a form showing
    `places`, as of `at`, read across them all (moments.moment): every
    place held, then the rows they name (_hold_named), then each written
    (_count_at). NotCountable names the place that may not be counted."""
    lock_places(session, places)
    _hold_named(session, counts.keys(), at, counts.values())
    return {
        place_id: _count_at(session, key, admin_id, place_id, count, at, places, when)
        for place_id, count in counts.items()
    }


def _hold_named(
    session: Session,
    places: Collection[int],
    at: Moment,
    counts: Collection[PlaceCount],
) -> None:
    """Hold the items, packages and open packages the places' counts name
    (ledger.hold_across), then the checks waiting there they may settle
    (over_recorded.hold_waiting), so writing them one place after another
    never takes a row of a lower id after a higher (inventory-tng-nfia)."""
    forms = [form for count in counts for _, form, _ in count.forms.lines]
    found = [count.forms.found[0] for count in counts if count.forms.found]
    items = hold_across(
        session,
        item_ids=[item_id for count in counts for item_id, _ in count.counted]
        + [item_id for count in counts for item_id, _, _ in count.forms.lines]
        + [package.item_id for package in found],
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
) -> Counted:
    """Put the records right at one place of a count form, holding every
    place on it (`across`) and the rows they name: its count, under a key
    of its own made from the form's (counts.record_count), an open package
    nobody recorded found there, the open packages closed there, and the
    ends there of lines recorded more than moved that it puts right."""
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
    place_key = uuid.uuid5(key, str(place_id))

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
        )

    # Its item's figures, if a package found was not kept (below).
    withheld: dict[int, tuple[Decimal, Decimal]] = {}
    if forms.found is not None:
        package, left = forms.found
        with session.begin_nested() as finding:
            made = open_packages.make(
                session, uuid.uuid5(key, "found"), package.id, place_id
            )
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
