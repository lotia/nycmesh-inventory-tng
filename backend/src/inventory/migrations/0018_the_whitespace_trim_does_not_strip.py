"""Widen what counts as one identifier, after checking that nothing already is.

`value_normalised` was `Lower(Trim(value))`, and `TRIM` strips U+0020 alone.
The wider rule is `inventory.identifiers.Canonical`, and every duplicate it
newly recognises was legal before it -- so this migration can turn a table that
satisfies its unique index into one that does not.

The check below runs first and raises, which is the whole point of it. Django
runs a migration inside a transaction on PostgreSQL, so raising here leaves the
column exactly as it was and names every row an administrator has to look at,
rather than stopping half way through a rewrite with nothing to act on.

It is a check and not merely an early warning. Re-adding the unique constraint
at the end would fail on the same rows anyway -- but it would fail with one
pair of values out of PostgreSQL and no way to see the rest, which is the
difference between a report and a symptom.

The column is dropped and re-added rather than altered because Django refuses
to modify a `GeneratedField` in place, and the constraint comes off first
because PostgreSQL would otherwise take it down with the column and Django's
recorded state would stop matching the database.

The reasoning is
[decision 0026](../../../../docs/decisions/0026-what-makes-two-strings-one-identifier.md).
"""

from typing import Any

from django.contrib.postgres.aggregates import ArrayAgg
from django.db import migrations, models
from django.db.models import Count

import inventory.identifiers
from inventory.identifiers import Canonical


def report_collisions(apps: Any, schema_editor: Any) -> None:
    """Refuse, listing the rows, if the wider rule would make two rows one.

    Only the live table. `HistoricalItemIdentifier` repeats a value once per
    revision by design and carries no unique index, so grouping it would report
    every corrected identifier as a collision.
    """
    identifiers = apps.get_model("inventory", "ItemIdentifier")
    clashes = (
        identifiers.objects.annotate(folded=Canonical("value"))
        .values("folded")
        .annotate(
            rows=Count("pk"),
            ids=ArrayAgg("pk", ordering="pk"),
            spellings=ArrayAgg("value", ordering="pk"),
        )
        .filter(rows__gt=1)
        .order_by("folded")
    )

    found = list(clashes)
    if not found:
        return

    lines = [
        f"{len(found)} group(s) of ItemIdentifier rows are separate today and "
        f"are one identifier under the wider rule. Nothing has been changed.",
        "",
    ]
    # `!a` and not `!r`. The whole point of this report is that somebody
    # can tell the rows apart, and the pairs hardest to tell apart by eye are
    # exactly the ones it exists for: `repr` escapes a no-break space, so the
    # whitespace duplicates are legible either way, but it leaves a composed
    # and a decomposed accent as two lines that render identically. An
    # administrator would be asked which of two apparently identical values to
    # correct. `!a` escapes every non-ASCII codepoint, so any two strings
    # that differ at all are shown differing.
    for clash in found:
        lines.append(f"  {clash['folded']!a}")
        for pk, spelling in zip(clash["ids"], clash["spellings"], strict=True):
            lines.append(f"    id={pk} value={spelling!a}")
    lines += [
        "",
        "Each group has to become one row before this migration can run. An "
        "identifier is corrected rather than removed -- guides/administrator.md "
        "says why, and what it costs to free a string -- so move whatever the "
        "duplicate names onto the row being kept, then correct that row.",
    ]
    raise RuntimeError("\n".join(lines))


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0017_a_device_that_can_be_cut_off"),
    ]

    operations = [
        migrations.RunPython(report_collisions, migrations.RunPython.noop, elidable=False),
        migrations.RemoveConstraint(
            model_name="itemidentifier",
            name="item_identifier_unique_normalised_value",
        ),
        migrations.RemoveField(model_name="historicalitemidentifier", name="value_normalised"),
        migrations.RemoveField(model_name="itemidentifier", name="value_normalised"),
        migrations.AddField(
            model_name="historicalitemidentifier",
            name="value_normalised",
            field=models.GeneratedField(
                db_persist=True,
                expression=inventory.identifiers.Canonical("value"),
                output_field=models.CharField(max_length=600),
            ),
        ),
        migrations.AddField(
            model_name="itemidentifier",
            name="value_normalised",
            field=models.GeneratedField(
                db_persist=True,
                expression=inventory.identifiers.Canonical("value"),
                output_field=models.CharField(max_length=600),
            ),
        ),
        migrations.AddConstraint(
            model_name="itemidentifier",
            constraint=models.UniqueConstraint(
                fields=("value_normalised",), name="item_identifier_unique_normalised_value"
            ),
        ),
    ]
