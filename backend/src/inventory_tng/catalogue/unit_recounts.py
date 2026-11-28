"""The guided recount: an item whose unit was wrong from the start,
corrected on the same item in one transaction, keeping its history,
labels' records and links, nothing converted (Ali, 2026-11-24,
inventory-tng-qety). Offered for an item in a length whose unit stock
having moved, or a conversion before, fixes, into another length, as
answering No to a conversion points here.

In the ledger's lock order (stock/ledger.py): the catalogue lock, then
every place it has been at, then the item; the places read again, and
refused if it has since reached another, to start over from its page;
then, in order:

1. Every place holding any of it brought to zero, form by form, in the old
   unit, by adjustments with the reason Records were wrong.
2. Its unit switched; any part of a thousandth an earlier conversion left
   on a form (inventory-tng-8s2c) brought to zero in the new one alike,
   loose for an open package closed or moved whole since (_held).
3. Its open packages closed, showing nothing.
4. Its packages in the old unit retired, retired ones too, each renamed
   with that unit after its name, so one of the same name can be added in
   the new unit (names are unique, retired ones too).
5. Its Reorder at, what is on order and its current vendor offers' units
   per order set as the administrator typed them, in the new unit.
6. Its last ledger transaction marked (Item.unit_recounted_after): what
   left a place since a count form was first shown (stock/moved_out.py)
   starts after it, as use summed over a time window must
   (inventory-tng-ueg2).

An item converted before whose stock never moved (inventory-tng-l26i)
has nothing to write off, close or count, and retires no package: each
in its old unit, retired ones too, is given its size again in the new
unit instead (_resize), and only those packages' labels are revoked.
Which cases skip which steps, and why:
docs/decisions/0003-transfer-ledger-over-nested-locations.md, its guided
recount amendment."""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal, NamedTuple, Unpack

import structlog
from opentelemetry import metrics
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from inventory_tng.catalogue import unit_changes, units
from inventory_tng.catalogue.name_keys import name_key
from inventory_tng.catalogue.queries import Offer, current_offers
from inventory_tng.core.db_errors import enforce
from inventory_tng.core.db_locking import lock_catalogue, locked, relock
from inventory_tng.core.refusals import Fields, refusals
from inventory_tng.core.write_metrics import WriteMetric
from inventory_tng.labels import labels
from inventory_tng.models.catalogue import (
    Item,
    ItemPackage,
    VendorOffer,
    reading_rules_held,
)
from inventory_tng.models.labels import Label
from inventory_tng.models.names import unit_key
from inventory_tng.models.places import Location
from inventory_tng.models.stock import Movement, OpenPackage
from inventory_tng.stock import adjustment_reasons, counts, open_packages
from inventory_tng.stock.balance import by_form
from inventory_tng.stock.ledger import LOOSE, Form, lock_places
from inventory_tng.web.forms import CHANGED, RETIRED

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

recounted = WriteMetric(
    log,
    meter.create_counter(
        "inventory.unit_recounts",
        unit="{recount}",
        description="Items whose unit, wrong from the start, a guided recount "
        "corrected, by the unit it was in and the one it is in now",
    ),
)


# A recount refused before its save as its records changed since its page
# (stale_as_read); one a rule refuses as it is saved, core/db_errors.py
# logs and counts. Named apart from inventory.unit_recounts, which is a
# metric, not a namespace (OpenTelemetry's naming conventions).
@refusals(
    log,
    meter.create_counter(
        "inventory.unit_recount.refused",
        unit="{recount}",
        description="Guided recounts of an item's unit refused before they "
        "were saved, nothing written, by reason: the records changed since "
        "the page",
    ),
)
def refused(
    event: str, *, reason: Literal["changed"], **fields: Unpack[Fields]
) -> None: ...


def units_open(session: Session, item: Item) -> tuple[str, ...]:
    """The units the item may be recounted into: another length, while it
    is current and stock having moved, or a conversion before, fixes its
    unit. None, otherwise: a free change, or revoking its labels first,
    corrects it then."""
    if item.retired_at is not None:
        return ()
    if unit_changes.unit_fixed(session, item) not in ("moved", "converted"):
        return ()
    return unit_changes.units_open(item.unit, "moved")[1:]


class Held(NamedTuple):
    """What the records hold of the item at a place, by form."""

    place: Location
    forms: dict[Form, Decimal]

    @property
    def total(self) -> Decimal:
        return sum(self.forms.values(), Decimal(0))


class Renamed(NamedTuple):
    """A package in the old unit and the name it is given as retired: as
    stored, and as shown (units.shown_name)."""

    package: ItemPackage
    name: str
    shown: str


class Planned(NamedTuple):
    """What a recount of the item, as it is now, does: the places it
    brings to zero, the open packages it closes, its packages in its unit
    and the name each is given as retired, or, `converted` before with no
    stock moved, those it asks the size of again, current ones first, and
    their labels it revokes, by id, retired ones' apart, as those can't
    be printed again while they stay retired; the
    vendor offers it asks for again; whether a line of it recorded with
    more than was moved waits, which refuses it; and `seen`, all that as
    one string, which a recount sent from a page showing it compares: a
    package retired or brought back since has another version."""

    held: list[Held]
    opened: list[OpenPackage]
    packages: list[Renamed]
    converted: bool
    resized: list[ItemPackage]
    revoking_current: list[int]
    revoking_retired: list[int]
    offers: list[Offer]
    waiting: bool
    seen: str

    @property
    def revoking(self) -> list[int]:
        """Every label it revokes, by id, in order."""
        return sorted(self.revoking_current + self.revoking_retired)


def _held(session: Session, item_id: int) -> list[Held]:
    """What the records hold of the item at each place, not nothing, in
    its unit as it is now, by place name. What an open package closed, or
    moved whole elsewhere, holds there, such as a part of a thousandth an
    earlier conversion left (inventory-tng-8s2c), no line may name: it is
    held loose, as a count by total finds it."""
    there = and_(
        OpenPackage.closed_at.is_(None),
        OpenPackage.location_id == by_form.c.location_id,
    )
    rows = session.execute(
        select(
            Location,
            by_form.c.package_id,
            by_form.c.open_package_id,
            there,
            by_form.c.quantity,
        )
        .join(Location, Location.id == by_form.c.location_id)
        .outerjoin(OpenPackage, OpenPackage.id == by_form.c.open_package_id)
        .where(by_form.c.item_id == item_id, by_form.c.quantity != 0)
        .order_by(func.lower(Location.name), Location.id)
    )
    held: dict[int, Held] = {}
    for place, package_id, open_id, open_there, quantity in rows:
        form = (
            LOOSE
            if open_id is not None and not open_there
            else Form(package_id, open_id)
        )
        forms = held.setdefault(place.id, Held(place, {})).forms
        forms[form] = forms.get(form, Decimal(0)) + quantity
        if not forms[form]:
            del forms[form]
    return [each for each in held.values() if each.forms]


def planned(session: Session, item: Item) -> Planned:
    """What a recount of the item does, as the records are now: read
    without locks for its page, and again under them as it is recounted."""
    held = _held(session, item.id)
    opened = list(
        session.scalars(
            select(OpenPackage)
            .where(OpenPackage.item_id == item.id, OpenPackage.closed_at.is_(None))
            .order_by(OpenPackage.id)
        )
    )
    # Its packages read again as they are now, not as the session may hold
    # them from the route's read before the locks (stale_as_read), so one
    # renamed or resized in between is changed since (inventory-tng-0w7o;
    # db_locking, on the identity map).
    every = list(
        session.scalars(
            select(ItemPackage)
            .where(ItemPackage.item_id == item.id)
            .order_by(ItemPackage.id)
            .execution_options(populate_existing=True)
        )
    )
    # Converted before, no stock moved: asked again, not retired.
    converted = unit_changes.unit_fixed(session, item) == "converted"
    old = [] if converted else [each for each in every if each.unit == item.unit]
    renamed = _renamed(session, old, every, item.unit)
    resized = _asked_again(every, item.unit) if converted else []
    current = [each.id for each in resized if each.retired_at is None]
    retired = [each.id for each in resized if each.retired_at is not None]
    revoking_current = _labels_of(session, current)
    revoking_retired = _labels_of(session, retired)
    offers = current_offers(session, item.id)
    waiting = unit_changes.checks_waiting(session, item.id)
    last = _last_transaction(session, item.id)
    seen = "/".join(
        [
            item.unit,
            str(item.edit_version),
            str(last or 0),
            _ids(each.place.id for each in held),
            _ids(row.id for row in opened),
            # Each with its version: one renamed since is named otherwise.
            ",".join(f"{package.id}.{package.edit_version}" for package in every),
            _ids(each.offer.id for each in offers),
            _ids(revoking_current + revoking_retired),
        ]
    )
    return Planned(
        held,
        opened,
        renamed,
        converted,
        resized,
        revoking_current,
        revoking_retired,
        offers,
        waiting,
        seen,
    )


def stale_as_read(item_id: int, plan: Planned, seen: str) -> bool:
    """Whether a recount of the item, as `plan` (planned) reads the
    records now, does otherwise than its page said (`seen`,
    Planned.seen), so a stale form says so before what is wrong with its
    amounts (inventory-tng-0w7o). `plan` is read without locks, as its
    page is: recount takes them in the ledger's order, which a lock taken
    first could break, and holds none while a page of problems is shown.
    Safe: read changed, the records changed after the page showed `seen`
    (or no page did); read unchanged, recount reads the plan again under
    its locks, so a change in between is refused then."""
    if plan.seen == seen:
        return False
    refused("unit recount refused", item_id=item_id, reason="changed")
    return True


def _asked_again(packages: Iterable[ItemPackage], unit: str) -> list[ItemPackage]:
    """Those of an item's packages whose size a recount asks again: the
    ones in its `unit`, current ones first, then retired ones (Ali,
    2026-11-25), each in the order given."""
    asked = [each for each in packages if each.unit == unit]
    return sorted(asked, key=lambda each: each.retired_at is not None)


def _labels_of(session: Session, package_ids: Sequence[int]) -> list[int]:
    """The labels of these packages not revoked, by id, in order."""
    if not package_ids:
        return []
    return list(
        session.scalars(
            select(Label.id)
            .where(Label.package_id.in_(package_ids), Label.revoked_at.is_(None))
            .order_by(Label.id)
        )
    )


def _ids(ids: Iterable[int]) -> str:
    """Ids, as `seen` says them: in order, by commas."""
    return ",".join(str(each) for each in sorted(ids))


def _last_transaction(session: Session, item_id: int) -> int | None:
    """The last ledger transaction with a line of the item."""
    return session.scalar(
        select(func.max(Movement.transaction_id)).where(Movement.item_id == item_id)
    )


def _renamed(
    session: Session,
    old: Sequence[ItemPackage],
    every: Sequence[ItemPackage],
    unit: str,
) -> list[Renamed]:
    """For each package `old`, a name with `unit` after it, as "Reel
    (m)", then "Reel (m) 2" and on, whose keys no package of the item's,
    `every`, has, nor another of these: each key taken rules out one name
    at most, so one more than they number is enough."""
    rules = reading_rules_held(session)
    taken = {key for package in every for key in (package.name_key, package.unit_key)}
    names = []
    for package in old:
        base = f"{package.name} ({unit})"
        tried = (base if n == 1 else f"{base} {n}" for n in range(1, len(taken) + 2))
        name = next(each for each in tried if not _keys(each, rules) & taken)
        taken |= _keys(name, rules)
        names.append(Renamed(package, name, units.shown_name(name, rules)))
    return names


def _keys(name: str, rules: units.Rules) -> set[str]:
    """A name's keys, as a package's unique indexes compare them."""
    return {name_key(name), unit_key(name, rules)}


class Sized(NamedTuple):
    """A package's size and too-short length given again in the new unit;
    None for no length."""

    size: Decimal
    short_below: Decimal | None


class Amounts(NamedTuple):
    """What the administrator typed in the new unit: Reorder at, what is on
    order, and each current vendor offer's units per order, by its id,
    None for blank; and each package asked again (Planned.resized), by its
    id."""

    minimum: Decimal | None
    on_order: Decimal | None
    per_order: Mapping[int, Decimal | None]
    sizes: Mapping[int, Sized]


def recount(
    session: Session,
    item: Item,
    unit: str,
    amounts: Amounts,
    *,
    seen: str,
    key: uuid.UUID,
    admin_id: int,
    at: datetime,
) -> Recounted:
    """The item recounted into `unit` (above), as the page that showed
    `seen` (Planned.seen) said it would be, its adjustments written under
    keys made from `key`; what is left to do after it.
    RuleBroken: CHANGED if anything it does changed since, or it may no
    longer be recounted into `unit`; RETIRED for a retired item;
    unit_changes.UNIT_CHECKS_WAITING while a check of it waits."""
    lock_catalogue(session)
    places = _places_of(session, item.id)
    lock_places(session, places)
    relock(session, item)
    # A place it reached since was not held first: started over from its
    # page, which the stock moved since shows otherwise anyway (`seen`).
    if not _places_of(session, item.id) <= places:
        # Detail of the refusal db_errors logs and counts, by its rule.
        log.debug("recount found places added since", item_id=item.id)
        enforce(CHANGED)
    plan = planned(session, item)
    return _recounted(session, item, unit, amounts, plan, seen, key, admin_id, at)


class Recounted(NamedTuple):
    """What is left to do after a recount: count the item again at the
    places it brought to zero from what they were shown to hold, by id;
    print again the labels it revoked, by id (queries.revoked_packages
    names their packages); and add its packages in the new unit, as it
    retired them, unless the item was converted before
    (Planned.converted) and they were given new sizes."""

    places: list[int]
    revoked: list[int]
    add_packages: bool


def _places_of(session: Session, item_id: int) -> set[int]:
    """Every place the item has been at, so any whose forms read otherwise
    in the new unit (_recounted), and those of its open packages."""
    been = select(by_form.c.location_id).where(by_form.c.item_id == item_id)
    opened = select(OpenPackage.location_id).where(
        OpenPackage.item_id == item_id, OpenPackage.closed_at.is_(None)
    )
    return set(session.scalars(been.union(opened)))


def _recounted(
    session: Session,
    item: Item,
    unit: str,
    amounts: Amounts,
    plan: Planned,
    seen: str,
    key: uuid.UUID,
    admin_id: int,
    at: datetime,
) -> Recounted:
    """recount's writes, holding the places and the item."""
    _refuse_unless_as_seen(session, item, unit, amounts, plan, seen)
    old = item.unit
    reason = adjustment_reasons.seeded(session, adjustment_reasons.RECORDS_WRONG).id
    for each in plan.held:
        sent = uuid.uuid5(key, str(each.place.id))
        _write_off(session, each, sent, admin_id, item.id, reason)
    item.unit = unit
    session.flush()
    # Read in the new unit: a part of a thousandth left by lines converted
    # before (inventory-tng-8s2c), at any place it has been, all held. A
    # place holding only that is not counted again (Ali, 2026-11-25,
    # inventory-tng-86uq): it held nothing anyone could see or count,
    # before the recount and after, so the places returned are plan.held's.
    # The log names such places apart, as residue_location_ids.
    residue = _held(session, item.id)
    for each in residue:
        sent = uuid.uuid5(key, f"{each.place.id} {unit}")
        _write_off(session, each, sent, admin_id, item.id, reason)
    for row in plan.opened:
        open_packages.close(
            session,
            row.id,
            admin_id,
            at,
            how="unit_recount",
            reason=adjustment_reasons.RECORDS_WRONG,
        )
    _retire(session, plan, at)
    _resize(session, plan, amounts.sizes, unit, at)
    offers = select(VendorOffer).where(
        VendorOffer.id.in_([each.offer.id for each in plan.offers])
    )
    for offer in session.scalars(locked(offers.order_by(VendorOffer.id))):
        offer.units_per_order = amounts.per_order.get(offer.id)
        offer.unit = unit
    item.minimum, item.on_order = amounts.minimum, amounts.on_order
    item.unit_recounted_after = _last_transaction(session, item.id)
    places = sorted(each.place.id for each in plan.held)
    residue_places = sorted({each.place.id for each in residue} - set(places))
    recounted.on_commit(
        session,
        "item unit recounted",
        attributes={"from": old, "to": unit},
        item_id=item.id,
        admin_id=admin_id,
        from_unit=old,
        to_unit=unit,
        location_ids=places,
        residue_location_ids=residue_places,
        open_package_ids=[row.id for row in plan.opened],
        item_package_ids=sorted(each.package.id for each in plan.packages),
        resized_package_ids=[package.id for package in plan.resized],
        label_ids=plan.revoking,
        vendor_offer_ids=[each.offer.id for each in plan.offers],
    )
    return Recounted(places, plan.revoking, not plan.converted)


def _refuse_unless_as_seen(
    session: Session,
    item: Item,
    unit: str,
    amounts: Amounts,
    plan: Planned,
    seen: str,
) -> None:
    """RuleBroken for a retired item, for anything changed since
    its page showed `seen`, or while a check of it waits."""
    if item.retired_at is not None:
        enforce(RETIRED)
    # A package asked again whose size was not sent: never left in the old
    # unit, nor a server error.
    unsized = {package.id for package in plan.resized} - amounts.sizes.keys()
    if plan.seen != seen or unsized or unit not in units_open(session, item):
        enforce(CHANGED)
    if plan.waiting:
        enforce(unit_changes.UNIT_CHECKS_WAITING)


def _retire(session: Session, plan: Planned, at: datetime) -> None:
    """The item's packages in the old unit retired, retired ones too, each
    renamed as planned (Planned.packages)."""
    names = {each.package.id: each.name for each in plan.packages}
    retiring = select(ItemPackage).where(ItemPackage.id.in_(names))
    for package in session.scalars(locked(retiring.order_by(ItemPackage.id))):
        package.name = names[package.id]
        package.retired_at = package.retired_at or at


def _resize(
    session: Session,
    plan: Planned,
    sizes: Mapping[int, Sized],
    unit: str,
    at: datetime,
) -> None:
    """The packages the recount asks the size of again given `sizes` in
    `unit`, retired ones staying retired, and their labels revoked, as
    each prints its package's size as it was (labels.package_label)."""
    resizing = select(ItemPackage).where(
        ItemPackage.id.in_([package.id for package in plan.resized])
    )
    for package in session.scalars(locked(resizing.order_by(ItemPackage.id))):
        package.size, package.short_below = sizes[package.id]
        package.unit = unit
    for label_id in plan.revoking:
        labels.revoke(session, label_id, at)


def _write_off(
    session: Session,
    held: Held,
    key: uuid.UUID,
    admin_id: int,
    item_id: int,
    reason_id: int,
) -> None:
    """What the records hold of the item at a place, as `held`, brought to
    zero under `key` (counts.write_off)."""
    counts.write_off(
        session,
        key,
        admin_id,
        held.place.id,
        item_id,
        held.forms,
        reason_id=reason_id,
    )
