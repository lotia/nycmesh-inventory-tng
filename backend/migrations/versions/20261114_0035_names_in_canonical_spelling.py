"""names in canonical spelling: NFC, with the micro sign as the Greek mu and
℃ and ℉ as °C and °F (inventory-tng-c0c.1)

The app stores names so from now on (models.CanonicalName,
docs/decisions/0021, 2026-11-14); the names there are are rewritten here,
in place. Two names that would then clash, such as "10µF" and "10μF", are
named by id, to rename first: nothing is rewritten until none would.
What searches compare is left as it is: fold() makes the same of either
spelling, as its NFKC does all of this and more.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-14 14:00:00
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# models.name_key's (0031's), written out: migrations do not import the app.
KEY = (
    "lower(regexp_replace({name},"
    " '(?!(?<=[0-9])[.][0-9])[[:space:][:punct:]]', '', 'g'))"
)
LOWER = "lower({name})"
# Each table's unique names, as models' indexes compare them: what a name is
# unique within, how it is compared, and among which rows.
UNIQUE: dict[str, tuple[tuple[str, str, str], ...]] = {
    "category": (("", LOWER, "true"),),
    "item": (("", KEY, "true"),),
    "item_package": (("item_id", KEY, "true"),),
    "volunteer": (("", LOWER, "retired_at IS NULL"),),
    "location": (
        ("kind, coalesce(parent_id, 0)", LOWER, "kind <> 'custody'"),
        ("", LOWER, "kind = 'custody' AND retired_at IS NULL"),
    ),
    "adjustment_reason": (("", LOWER, "retired_at IS NULL"),),
}
# models.CANONICAL_SIGNS's, written out.
SIGNS = str.maketrans({"\u00b5": "\u03bc", "\u2103": "°C", "\u2109": "°F"})


def _canonical(name: str) -> str:
    """models.canonical's, written out."""
    return unicodedata.normalize("NFC", name.translate(SIGNS))


def upgrade() -> None:
    bind = op.get_bind()
    renamed: dict[str, list[dict[str, object]]] = {}
    clashes: list[str] = []
    for table, uniques in UNIQUE.items():
        rows = bind.execute(sa.text(f"SELECT id, name FROM {table}")).all()
        renamed[table] = [
            {"id": id_, "name": _canonical(name)}
            for id_, name in rows
            if _canonical(name) != name
        ]
        if renamed[table]:
            clashes += _clashes(table, uniques, renamed[table])
    if clashes:
        raise RuntimeError(
            "Names that would be one name once in canonical spelling (such as "
            f"10\u00b5F and 10\u03bcF), by id: {'; '.join(clashes)}. Rename all but one "
            "in each, then upgrade again."
        )
    for table, rows in renamed.items():
        if rows:
            bind.execute(
                sa.text(f"UPDATE {table} SET name = :name WHERE id = :id"), rows
            )


def _clashes(
    table: str, uniques: tuple[tuple[str, str, str], ...], rows: list[dict[str, object]]
) -> list[str]:
    """Each group of `table`'s rows whose names would clash with `rows`'
    names in place of theirs, as "table ids"."""
    new = {"ids": [row["id"] for row in rows], "names": [row["name"] for row in rows]}
    # The table's rows, each with its new name, if it has one, as canon.
    renamed = (
        f"SELECT t.*, coalesce(r.new, t.name) AS canon FROM {table} t"
        " LEFT JOIN unnest(CAST(:ids AS integer[]), CAST(:names AS text[]))"
        " AS r(id, new) USING (id)"
    )
    found: list[str] = []
    for scope, key, where in uniques:
        group = ", ".join(filter(None, (scope, key.format(name="canon"))))
        query = (
            f"SELECT array_agg(id ORDER BY id) FROM ({renamed}) AS t"
            f" WHERE {where} GROUP BY {group} HAVING count(*) > 1"
        )
        groups = op.get_bind().execute(sa.text(query), new).scalars()
        found += [f"{table} {', '.join(map(str, ids))}" for ids in groups]
    return found


def downgrade() -> None:
    # The spellings typed are not kept: the names stay canonical.
    pass
