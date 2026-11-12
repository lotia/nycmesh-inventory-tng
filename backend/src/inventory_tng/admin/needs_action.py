"""What waits for an administrator's decision, on the home page as "Needs
your action (N)" (docs/decisions/0020, 2026-11-12): by kind, each kind's
open packages one line per item, linking to its page showing only that
item. Each kind's page takes the item as `?item=` (OnlyItem)."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, NamedTuple
from urllib.parse import urlencode

from fastapi import Depends
from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.admin.common import row_id
from inventory_tng.db import DbSession
from inventory_tng.models import Item, OpenPackage

# Between how many and a package's name, as an administrator reads it.
TIMES = "\N{MULTIPLICATION SIGN}"


class ItemWaiting(NamedTuple):
    """One item's open packages waiting for one kind of decision, as
    "Needs your action" lists them (docs/decisions/0020)."""

    item_id: int
    item: str
    # How many of each package, by its name: 3 TIMES "Spool 305 m".
    packages: str
    count: int
    # How far below zero those showing less than nothing are, together, in
    # the item's unit, so none is confirmed unseen; 0 if none is.
    below: Decimal
    unit: str


def by_item(session: Session, *where: ColumnElement[bool]) -> list[ItemWaiting]:
    """The open packages `where` picks, one line per item, by its name."""
    grouped: defaultdict[int, list[open_packages.Shown]] = defaultdict(list)
    for each in open_packages.shown(session, *where):
        grouped[each.row.item_id].append(each)
    lines = []
    for item_id, found in grouped.items():
        # Each package as named, never a word made from its name
        # (inventory-tng-b1z): "2 TIMES Spool 305 m, 1 TIMES Box 100 m".
        names = Counter(each.package for each in found)
        lines.append(
            ItemWaiting(
                item_id,
                found[0].item,
                ", ".join(f"{n} {TIMES} {name}" for name, n in sorted(names.items())),
                len(found),
                -sum((each.left for each in found if each.left < 0), Decimal(0)),
                found[0].unit,
            )
        )
    return sorted(lines, key=lambda line: (line.item.lower(), line.item_id))


class Kind(NamedTuple):
    """A kind of decision, its page, and which open packages wait for it."""

    title: str
    path: str
    where: tuple[ColumnElement[bool], ...]


class Waiting(NamedTuple):
    """A kind of decision waiting: each item's waiting, and how many in
    all."""

    kind: Kind
    items: list[ItemWaiting]
    total: int


def waiting(session: Session, kind: Kind) -> Waiting:
    """What waits for a kind of decision."""
    items = by_item(session, *kind.where)
    return Waiting(kind, items, sum(line.count for line in items))


def only_item(session: DbSession, item: str = "") -> Item | None:
    """The item a kind's page shows alone (`?item=`); None for all, as for
    one that is no id or no item."""
    ident = row_id(item)
    return session.get(Item, ident) if ident else None


OnlyItem = Annotated[Item | None, Depends(only_item)]


def of_only(
    where: Sequence[ColumnElement[bool]], only: Item | None
) -> list[ColumnElement[bool]]:
    """A kind's open packages, `where`, of only one item, if one."""
    return [*where, OpenPackage.item_id == only.id] if only else list(where)


def query(only: Item | None) -> dict[str, str]:
    """The query that shows a kind's page as it was: one item, or all."""
    return {"item": str(only.id)} if only else {}


def query_string(only: Item | None) -> str:
    """query(), for a form's action."""
    return f"?{urlencode(query(only))}" if only else ""


def needs_action(session: Session) -> list[Waiting]:
    """What waits for an administrator's decision, by kind, those with
    something waiting: shown on the home page until each is made."""
    # They import this.
    from inventory_tng.admin import length_checks, used_up

    kinds = [used_up.KIND, *length_checks.KINDS]
    return [found for kind in kinds if (found := waiting(session, kind)).items]
