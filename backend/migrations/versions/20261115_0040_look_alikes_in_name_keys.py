"""name keys read look-alike spaces, quotes and dashes as ASCII's
(inventory-tng-6sb, Ali, 2026-11-15)

A non-breaking or other Unicode space, a curly quote or apostrophe, and an
en or em dash, a minus sign or another hyphen are each read as the ASCII
one before the key leaves spaces and punctuation out (names.LOOK_ALIKES,
docs/decisions/0021), so a name pasted with one clashes with the name
typed plainly. Names are as they were. Items', packages' and aliases'
CHECKs are written again with the new key and every key refilled; names
that would then be one, among items, among an item's packages, among
current aliases, or an alias and an item's name, are named by id first,
to rename, and nothing is changed until none are. Going back cannot
clash: the old key keeps apart all the new one does.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-15 02:00:00
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# names.name_key_sql's, written out: migrations do not import the app.
# names.LOOK_ALIKES's characters.
LOOK_ALIKES = "".join(
    map(
        chr,
        (
            *(0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000),
            *(0x00B4, 0x02BC, 0x2018, 0x2019, 0x201A, 0x201B, 0x2032, 0x2035),
            0xFF07,
            *(0x201C, 0x201D, 0x201E, 0x201F, 0x2033, 0x2036, 0xFF02),
            *(0x00AD, *range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D),
        ),
    )
)
# Each one's ASCII counterpart, quoted for SQL (each ' doubled).
PLAIN = " " * 16 + "''" * 9 + '"' * 7 + "-" * 11
IGNORED = "'(?!(?<=[0-9])[.][0-9])[\\t-\\r -/:-@[-`{-~]'"


def key(name: str) -> str:
    """The new key of the column `name`."""
    return (
        f'lower(regexp_replace(translate({name} COLLATE "C",'
        f" '{LOOK_ALIKES}', '{PLAIN}'), {IGNORED}, '', 'g'))"
    )


KEY = key("name")
# 0037's.
BEFORE = f"lower(regexp_replace(name COLLATE \"C\", {IGNORED}, '', 'g'))"
TABLES = ("item", "item_package", "item_alias")


def upgrade() -> None:
    _refuse_clashes()
    _rekey(KEY)


def downgrade() -> None:
    _rekey(BEFORE)


def _rekey(new: str) -> None:
    """Each table's CHECK written again with the key `new`, and its keys
    refilled so."""
    for table in TABLES:
        check = op.f(f"ck_{table}_name_key")
        op.drop_constraint(check, table, type_="check")
        op.execute(f"UPDATE {table} SET name_key = {new}")
        op.create_check_constraint(check, table, f"name_key = {new}")


def _refuse_clashes() -> None:
    """RuntimeError naming by id the rows the new key would make one name,
    if any."""
    current = "WHERE retired_at IS NULL"
    clashing = [
        *(f"item {ids}" for ids in _clashing(f"FROM item GROUP BY {KEY}")),
        *(
            f"item_package {ids}"
            for ids in _clashing(f"FROM item_package GROUP BY item_id, {KEY}")
        ),
        *(
            f"item_alias {ids}"
            for ids in _clashing(f"FROM item_alias {current} GROUP BY {KEY}")
        ),
        *(
            f"item_alias {alias} and item {item}"
            for alias, item in op.get_bind().execute(
                text(
                    "SELECT a.id, i.id FROM item_alias a JOIN item i"
                    f" ON {key('a.name')} = {key('i.name')}"
                    " WHERE a.retired_at IS NULL ORDER BY a.id, i.id"
                )
            )
        ),
    ]
    if clashing:
        raise RuntimeError(
            "Names differing only by look-alike spaces, quotes or dashes would"
            f" be one name, by id: {'; '.join(clashing)}. Rename all but one in"
            " each, then migrate again."
        )


def _clashing(rows: str) -> list[str]:
    """Ids of `rows`, by group, that the new key makes one name."""
    groups = op.get_bind().execute(
        text(f"SELECT array_agg(id ORDER BY id) {rows} HAVING count(*) > 1")
    )
    return [", ".join(map(str, ids)) for ids in groups.scalars()]
