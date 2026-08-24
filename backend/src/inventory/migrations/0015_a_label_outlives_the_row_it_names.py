"""CASCADE to PROTECT on both of a label's rows.

WHAT POSTGRES DOES ABOUT THIS: nothing. `sqlmigrate` emits the same two
constraints migration 0001 already created -- Django has never asked the
database to cascade these, it does the cascading itself in its own collector,
so the whole of this change lives in Python and this migration only records
it. `models.py` says what the change is for.

That matters twice. A `dbshell` DELETE, or anything else reaching the rows
without going through Django, is unaffected and still orphans nothing because
the database was never enforcing it either way -- the guard is the ORM's.
And re-adding a byte-identical constraint still takes ACCESS EXCLUSIVE on
`inventory_label` for as long as it takes, which on a pre-upgrade hook against
a large table is a lock nobody expected from a no-op.

Safe to apply against a database that already holds labels: no row is read,
written or invalidated.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0014_an_item_the_sheet_import_cannot_settle"),
    ]

    operations = [
        migrations.AlterField(
            model_name="label",
            name="item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="labels",
                to="inventory.item",
            ),
        ),
        migrations.AlterField(
            model_name="label",
            name="location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="labels",
                to="inventory.location",
            ),
        ),
    ]
