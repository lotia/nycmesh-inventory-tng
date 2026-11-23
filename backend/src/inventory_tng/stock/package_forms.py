"""An item's stock at a place split by form (docs/decisions/0020): sealed
packages, open packages, loose units and short ends, read from the ledger
by form (balance.by_form), and the orders each form is listed in.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from inventory_tng.core.db_lookups import values_by
from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import balance


@dataclass
class Forms:
    """An item's stock at a place, form by form (docs/decisions/0020):
    sealed packages of each, by name, how many and how much; each open
    package's left; loose units; short ends, open packages left below
    their package's too-short-to-use length; and what is usable, all but
    short ends."""

    sealed: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)
    # How many sealed of each package, by id.
    sealed_by_package: dict[int, Decimal] = field(default_factory=dict)
    open: list[Decimal] = field(default_factory=list)
    loose: Decimal = Decimal(0)
    short_ends: list[Decimal] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return (
            sum((amount for _, _, amount in self.sealed), Decimal(0))
            + sum(self.open, Decimal(0))
            + self.loose
            + sum(self.short_ends, Decimal(0))
        )

    @property
    def negative(self) -> bool:
        """Less than nothing in some form."""
        amounts = [amount for _, _, amount in self.sealed]
        return any(each < 0 for each in [*amounts, *self.open, self.loose])

    @property
    def usable(self) -> Decimal:
        return self.total - sum(self.short_ends, Decimal(0))


def is_short_end(left: Decimal, short_below: Decimal | None) -> bool:
    """Whether an open package with `left` on it is a short end: some
    left, but less than its package's too-short-to-use length. Less than
    nothing is the records', not a short end. The scan page tells one the
    same way (frontend/src/scan/offered.ts, isShortEnd)."""
    return short_below is not None and 0 < left < short_below


def fewest_left_first(open_id: int, left: Decimal) -> tuple[bool, Decimal, int]:
    """How an item's open packages at a place are listed, by open package
    id and what is left on it: fewest left first, those showing nothing or
    less last, ties by id (docs/decisions/0020). It does not set short
    ends apart: split lists them on their own. The scan page lists its
    cards the same way, but puts short ends after the usable ones and
    before those showing nothing (frontend/src/scan/offered.ts,
    offeredAt)."""
    return (left <= 0, left, open_id)


def largest_first(package_id: int, size: Decimal) -> tuple[Decimal, int]:
    """How an item's sealed packages are listed, by package id and size:
    largest size first, ties by id (Ali, 2026-11-18, docs/decisions/0020)."""
    return (-size, package_id)


class PackageSize(NamedTuple):
    name: str
    size: Decimal
    short_below: Decimal | None


def split(
    rows: Iterable[tuple[int | None, int | None, Decimal]],
    packages: Mapping[int, PackageSize],
    opened_from: Mapping[int, int],
) -> Forms:
    """An item's balances at a place, each of a package (sealed), an open
    package, or neither (loose), as Forms: `packages` by id, and each open
    package's package by its id, to tell a short end. Sealed packages are
    listed largest_first, and open packages and short ends
    fewest_left_first, whatever order `rows` come in."""
    forms = Forms()
    sealed: list[tuple[int, Decimal]] = []
    opened: list[tuple[int, Decimal]] = []
    for package_id, open_id, amount in rows:
        if package_id is not None:
            sealed.append((package_id, amount))
        elif open_id is not None:
            opened.append((open_id, amount))
        else:
            forms.loose += amount
    for package_id, amount in sorted(
        sealed, key=lambda each: largest_first(each[0], packages[each[0]].size)
    ):
        package = packages[package_id]
        count = amount / package.size
        forms.sealed.append((package.name, count, amount))
        forms.sealed_by_package[package_id] = count
    for open_id, amount in sorted(opened, key=lambda each: fewest_left_first(*each)):
        short = packages[opened_from[open_id]].short_below
        short_end = is_short_end(amount, short)
        (forms.short_ends if short_end else forms.open).append(amount)
    return forms


def forms_of(
    session: Session, where: ColumnElement[bool]
) -> dict[tuple[int, int], Forms]:
    """Stock by item and place, form by form, of the balances `where`
    picks (on balance.by_form): those of nothing left are left out."""
    rows = session.execute(
        select(
            balance.by_form.c.item_id,
            balance.by_form.c.location_id,
            balance.by_form.c.package_id,
            balance.by_form.c.open_package_id,
            balance.by_form.c.quantity,
        ).where(where, balance.by_form.c.quantity != 0)
    ).all()
    item_ids = {row[0] for row in rows}
    packages = {
        package.id: PackageSize(package.name_shown, package.size, package.short_below)
        for package in session.scalars(
            select(ItemPackage).where(ItemPackage.item_id.in_(item_ids))
        )
    }
    open_ids = {row[3] for row in rows if row[3] is not None}
    opened_from = values_by(session, OpenPackage.id, OpenPackage.package_id, open_ids)
    grouped: dict[tuple[int, int], list[tuple[int | None, int | None, Decimal]]] = {}
    for item_id, place, package_id, open_id, amount in rows:
        grouped.setdefault((item_id, place), []).append((package_id, open_id, amount))
    return {key: split(found, packages, opened_from) for key, found in grouped.items()}
