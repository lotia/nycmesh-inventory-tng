"""Reading one column of rows by another, for the rows a request names."""

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import QueryableAttribute, Session


def values_by[K, V](
    session: Session,
    key: QueryableAttribute[K],
    value: QueryableAttribute[V],
    keys: Collection[K],
) -> dict[K, V]:
    """`value` of the rows whose `key` is one of `keys`, by key:
    `values_by(session, Item.id, Item.unit, item_ids)`. Keys no row has
    are left out; no keys, no query."""
    if not keys:
        return {}
    rows = session.execute(select(key, value).where(key.in_(list(keys))))
    return {found: each for found, each in rows}
