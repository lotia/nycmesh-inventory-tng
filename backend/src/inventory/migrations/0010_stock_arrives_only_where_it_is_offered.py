"""Stock may leave a retired location and may not arrive at one.

The write half of decision 0019, whose statement of what retirement means is
the argument for it. A room keeps whatever it holds and that stock stays
movable *out* -- emptying it is how it gets decommissioned -- but nothing new
arrives, because a balance under a row no collection offers is stock nobody
can find.

A trigger rather than a check constraint: whether the row is active lives in
another table, which a constraint cannot see. Decision 0016 is why that makes
it the database's rule and not the serializer's, so the admin, a fixture load
and the sheet importer meet it too.

NOT backward compatible with the previous release, unlike 0008. That release's
serializers accept any to_location and any item, so after a `helm rollback` it
would write movements this trigger refuses. Rolling back means reversing this
migration too:

    manage.py migrate inventory 0009

This is the explicit plan docs/deployment.md#rolling-back asks for.
"""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION inventory_to_location_is_offered()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    still_offered boolean;
    parent_kind text;
BEGIN
    SELECT t.kind INTO parent_kind
      FROM inventory_stocktransaction t
     WHERE t.id = NEW.transaction_id;

    -- Adjustments and counts arrive anywhere, deliberately. They are how
    -- somebody says the shelf disagrees with the system, and decision 0011
    -- section 6 makes that the one claim this must never argue with: finding
    -- three of a retired item on a shelf has to be recordable, or the stock
    -- this rule cares about is exactly the stock nobody can reconcile.
    IF parent_kind IN ('adjustment', 'count') THEN
        RETURN NEW;
    END IF;

    -- IS FALSE, not NOT: no row at all leaves these NULL, and that is the
    -- foreign key's complaint to make, not this one's. The same reasoning as
    -- inventory_require_selectable_volunteer in 0008, and the same reason --
    -- the foreign key is deferrable, so loaddata can reach here before the
    -- row it names has been inserted.
    IF NEW.to_location_id IS NOT NULL THEN
        SELECT l.active INTO still_offered
          FROM inventory_location l
         WHERE l.id = NEW.to_location_id;

        IF still_offered IS FALSE THEN
            RAISE EXCEPTION
                'to_location % is retired, so stock cannot arrive there. Stock may still leave it.',
                NEW.to_location_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- The same rule for the item. A receipt is pure arrival with no from-side,
    -- so "draining is legitimate, filling is not" does not distinguish an item
    -- from a location: stock received against a retired item is a balance no
    -- collection offers, which is what this rule exists to prevent.
    SELECT i.active INTO still_offered
      FROM inventory_item i
     WHERE i.id = NEW.item_id;

    IF still_offered IS FALSE AND NEW.to_location_id IS NOT NULL THEN
        RAISE EXCEPTION
            'item % is retired, so stock cannot arrive under it. Stock may still leave, and a count may correct it.',
            NEW.item_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

-- BEFORE INSERT only, like its peers in 0008: the ledger is append-only, so a
-- movement's destination is decided once and an UPDATE branch would be dead.
CREATE TRIGGER stock_movement_to_location_is_active
    BEFORE INSERT ON inventory_stockmovement
    FOR EACH ROW EXECUTE FUNCTION inventory_to_location_is_offered();
"""

REVERSE = """
DROP TRIGGER IF EXISTS stock_movement_to_location_is_active ON inventory_stockmovement;
DROP FUNCTION IF EXISTS inventory_to_location_is_offered();
"""


class Migration(migrations.Migration):
    dependencies = [("inventory", "0009_remove_label_label_quantity_positive_and_more")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
