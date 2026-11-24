"""A count form against the units and package sizes it was shown in
(units_shown): what it shows otherwise now, and the words it is shown
again with, beside each box of one changed since and above the form, so
what was typed in the old unit or size is not saved in the new one
unawares (Ali, 2026-11-23, inventory-tng-57u8). The routes are
stock/count_routes.py's."""

import uuid
from collections.abc import Collection, Mapping

import structlog
from sqlalchemy.orm import Session

from inventory_tng.core.phrases import Words
from inventory_tng.core.sent_forms import digest_spent
from inventory_tng.stock.count_forms import PlaceCount, changed_words, typed_named
from inventory_tng.stock.forms import now_counted_in, now_holds, shown_in
from inventory_tng.stock.store_counts import amounts_named
from inventory_tng.stock.units_shown import Now, changed_since

log = structlog.get_logger(__name__)


def asked_again(
    session: Session,
    form: Mapping[str, str],
    root_id: int,
    place_ids: Collection[int],
    now: Now,
) -> tuple[dict[str, Words], str]:
    """A count form shown again as one of its items or packages changed
    since it was shown: the words beside each box of one
    (count_forms.changed_words), and above the form what one is now, as a
    sealed row of a package held nowhere has no box to say it beside."""
    if now.units:
        alert = now_counted_in(min(now.units.items())[1])
    else:
        alert = now_holds(*min(now.sizes.items())[1])
    return changed_words(session, form, root_id, place_ids, now), alert


def changed_since_sent(
    session: Session,
    form: Mapping[str, str],
    key: uuid.UUID,
    counts: Collection[PlaceCount],
    root_id: int,
    place_ids: Collection[int],
) -> Now:
    """What a count form, of `counts` as read, shows in a unit or package
    size changed since (units_shown.changed_since), as it is now, a box
    typed in but not read too (count_forms.typed_named), as it is shown
    again with its new unit; nothing for a form saved before, which is
    answered as it was (store_counts.record_store_count)."""
    if digest_spent(session, key) is not None:
        return Now({}, {})
    items, packages = amounts_named(counts, {})
    typed_items, typed_packages = typed_named(session, form, root_id, place_ids)
    return changed_since(
        session, shown_in(form), items | typed_items, packages | typed_packages
    )


def kept_as_now(
    session: Session,
    form: Mapping[str, str],
    key: uuid.UUID,
    counts: Collection[PlaceCount],
    root_id: int,
    place_ids: Collection[int],
) -> tuple[dict[str, Words], Words | None]:
    """A count form shown again keeping what was typed, though not saved
    for it (Bring it back, a save refused for another reason): the words
    of asked_again for each item or package shown otherwise since, as it
    shows them as they are now. None for none."""
    now = changed_since_sent(session, form, key, counts, root_id, place_ids)
    if not now:
        return {}, None
    log.info(
        "count shown again in units changed since",
        location_id=root_id,
        item_ids=sorted(now.units),
        item_package_ids=sorted(now.sizes),
    )
    return asked_again(session, form, root_id, place_ids, now)
