"""What waits for an administrator's decision, on the home page as "Needs
your action (N)" (docs/decisions/0020, 2026-11-12): by kind, each kind's
open packages one line per item, linking to its page showing only that
item. Each kind's page takes the item as `?item=` (OnlyItem)."""

from collections import defaultdict
from decimal import Decimal
from typing import Annotated, NamedTuple
from urllib.parse import urlencode

from fastapi import Depends
from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.admin.common import row_id
from inventory_tng.db import DbSession
from inventory_tng.models import Item


def plural(word: str, count: int) -> str:
    """`count` of a package's word, made plural by English's usual rules,
    its first letter small unless it is a name of its own, such as RJ45:
    "3 spools", "2 boxes"."""
    if word[1:] == word[1:].lower():
        word = word[:1].lower() + word[1:]
    if count != 1:
        if word.endswith(("s", "x", "z", "ch", "sh")):
            word += "es"
        elif len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou":
            word = word[:-1] + "ies"
        else:
            word += "s"
    return f"{count} {word}"


class ItemWaiting(NamedTuple):
    """One item's open packages waiting for one kind of decision, as
    "Needs your action" lists them (docs/decisions/0020)."""

    item_id: int
    item: str
    # How many, in their package's word: "3 spools".
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
        # A package's word is the first of its name: "Spool 305 m" is a
        # spool. Of packages of several words, none's is theirs.
        words = {each.package.split(" ", 1)[0] for each in found}
        count = len(found)
        lines.append(
            ItemWaiting(
                item_id,
                found[0].item,
                plural(words.pop(), count)
                if len(words) == 1
                else f"{count} open packages",
                count,
                -sum((each.left for each in found if each.left < 0), Decimal(0)),
                found[0].unit,
            )
        )
    return sorted(lines, key=lambda line: (line.item.lower(), line.item_id))


class Kind(NamedTuple):
    """A kind of decision waiting, its page, each item's waiting, and how
    many in all."""

    title: str
    path: str
    items: list[ItemWaiting]
    total: int


def kind(session: Session, title: str, path: str, *where: ColumnElement[bool]) -> Kind:
    """The kind whose open packages `where` picks."""
    items = by_item(session, *where)
    return Kind(title, path, items, sum(line.count for line in items))


def only_item(session: DbSession, item: str = "") -> Item | None:
    """The item a kind's page shows alone (`?item=`); None for all, as for
    one that is no id or no item."""
    ident = row_id(item)
    return session.get(Item, ident) if ident else None


OnlyItem = Annotated[Item | None, Depends(only_item)]


def query(only: Item | None) -> dict[str, str]:
    """The query that shows a kind's page as it was: one item, or all."""
    return {"item": str(only.id)} if only else {}


def query_string(only: Item | None) -> str:
    """query(), for a form's action."""
    return f"?{urlencode(query(only))}" if only else ""


def needs_action(session: Session) -> list[Kind]:
    """What waits for an administrator's decision, by kind, those with
    something waiting: shown on the home page until each is made."""
    from inventory_tng.admin import used_up

    kinds = [kind(session, used_up.TITLE, used_up.USED_UP, *used_up.REPORTED)]
    return [each for each in kinds if each.items]
