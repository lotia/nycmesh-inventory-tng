"""CASCADE to PROTECT on the two other things recorded about an item.

The same shape as migration 0015 and the same non-event in the database: these
constraints were never `ON DELETE CASCADE` in PostgreSQL, because Django does
the cascading itself in its collector, so `sqlmigrate` emits what migration
0001 already created and the whole of the change lives in Python. `models.py`
says what it is for, on each of the two keys.

Both consequences migration 0015 sets out apply here unchanged, against
`inventory_itemidentifier` and `inventory_vendoroffer` instead of
`inventory_label`: what the guard therefore does not reach, and what this
otherwise-empty migration still locks while it applies. Read them there.

Safe to apply against a database that already holds either: no row is read,
written or invalidated.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0015_a_label_outlives_the_row_it_names"),
    ]

    operations = [
        migrations.AlterField(
            model_name="itemidentifier",
            name="item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="identifiers",
                to="inventory.item",
            ),
        ),
        migrations.AlterField(
            model_name="vendoroffer",
            name="item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="offers",
                to="inventory.item",
            ),
        ),
    ]
