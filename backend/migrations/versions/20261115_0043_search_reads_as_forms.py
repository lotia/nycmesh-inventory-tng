"""search reads units as the forms do: what is searched folded again
(inventory-tng-c0c.3, Ali, 2026-11-15)

Search now reads a unit after a number as an item's form does
(units.respell, docs/decisions/0021): by the words near it, then its
reading rule, then the profile (`10G` as 10 Gbps, `4 K` as 4 kbps), and
never in a product's name (`Cat6a`). So every row is folded again, as the
app folds it (models.Searchable, Item.refold), an item's current aliases
too, items' and packages' by the reading rules there are, and items' and
aliases' name_folded, importing the app's fold as 0036 does. And every
item's, package's and alias's unit_key read again so (models.unit_key),
naming by id first any names that would then be one, as 0042 does.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-15 05:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng import units
from inventory_tng.models import FOLDED_WITH, fold, held, unit_key

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The units.VERSION this folds again for (0036 says how they follow).
FOLDS_UNITS = 3

# Each table's searched fields, in order, as models' SEARCHED at 0043, and
# whether reading rules read them (models.Searchable.READS_RULES).
SEARCHED: dict[str, tuple[tuple[str, ...], bool]] = {
    "item": (("name", "manufacturer", "part_number", "old_code"), True),
    "volunteer": (("name",), False),
    "location": (("name",), False),
    "item_package": (("name",), True),
}


def upgrade() -> None:
    bind = op.get_bind()
    rules = {
        token: units.Rule(reading, respell)
        for token, reading, respell in bind.execute(
            sa.text("SELECT token, reading, respell FROM reading_rule")
        )
    }
    _rekey(rules)
    aliases: dict[int, list[str]] = {}
    folded_aliases = []
    for alias_id, item_id, name, current in bind.execute(
        sa.text(
            "SELECT id, item_id, name, retired_at IS NULL FROM item_alias ORDER BY id"
        )
    ):
        folded_aliases.append({"id": alias_id, "name_folded": fold(name)})
        if current:
            aliases.setdefault(item_id, []).append(name)
    if folded_aliases:
        bind.execute(
            sa.text("UPDATE item_alias SET name_folded = :name_folded WHERE id = :id"),
            folded_aliases,
        )
    for table, (fields, reads_rules) in SEARCHED.items():
        read = rules if reads_rules else None
        rows = bind.execute(sa.text(f"SELECT id, {', '.join(fields)} FROM {table}"))
        also = aliases if table == "item" else {}
        folded = [
            {
                "id": row["id"],
                "folded_with": FOLDED_WITH,
                "search_text": " ".join(
                    [held(row[field] or "", read) for field in fields]
                    + [held(alias, read) for alias in also.get(row["id"], [])]
                ),
            }
            | ({"name_folded": fold(row["name"])} if table == "item" else {})
            for row in rows.mappings().all()
        ]
        if folded:
            named = ", name_folded = :name_folded" if table == "item" else ""
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET search_text = :search_text,"
                    f" folded_with = :folded_with{named} WHERE id = :id"
                ),
                folded,
            )


def _rekey(rules: units.Rules) -> None:
    """Each item's, package's and alias's unit_key as the app now reads it,
    unless names would then be one: RuntimeError naming them by id."""
    bind = op.get_bind()
    for table in ("item", "item_package", "item_alias"):
        scope = "item_id" if table == "item_package" else "NULL"
        current = "retired_at IS NULL" if table == "item_alias" else "true"
        rows = bind.execute(
            sa.text(f"SELECT id, name, {scope}, {current} FROM {table} ORDER BY id")
        ).all()
        keyed = [{"id": i, "unit_key": unit_key(name, rules)} for i, name, _, _ in rows]
        # Of an item's packages, or current aliases, or items.
        groups: dict[tuple[object, str], list[int]] = {}
        for (row_id, _, scoped, counted), key in zip(rows, keyed, strict=True):
            if counted:
                groups.setdefault((scoped, str(key["unit_key"])), []).append(row_id)
        clashing = [ids for ids in groups.values() if len(ids) > 1]
        if clashing:
            said = "; ".join(f"{table} {', '.join(map(str, ids))}" for ids in clashing)
            raise RuntimeError(
                "Names differing only in how their units are spelt would be one"
                f" name, by id: {said}. Rename all but one in each, then migrate"
                " again."
            )
        if keyed:
            # Each row's key set apart first, as two may swap keys.
            bind.execute(sa.text(f"UPDATE {table} SET unit_key = '#' || id"))
            bind.execute(
                sa.text(f"UPDATE {table} SET unit_key = :unit_key WHERE id = :id"),
                keyed,
            )
    clashing = bind.execute(
        sa.text(
            "SELECT a.id, i.id FROM item_alias a JOIN item i"
            " ON a.unit_key = i.unit_key WHERE a.retired_at IS NULL ORDER BY a.id, i.id"
        )
    ).all()
    if clashing:
        said = "; ".join(f"item_alias {a} and item {i}" for a, i in clashing)
        raise RuntimeError(
            "Names differing only in how their units are spelt would be one"
            f" name, by id: {said}. Rename all but one in each, then migrate again."
        )


def downgrade() -> None:
    # Folded as the app now folds, and saying so: left as they are.
    pass
