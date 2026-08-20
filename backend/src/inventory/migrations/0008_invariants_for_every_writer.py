# Hand-written: triggers, not model state. See docs/decisions/0016-invariants-for-every-writer.md
# for which rules are here and which deliberately stayed above the database.
#
# Every rule below was previously enforced only by the serializers and the
# batch view, so the admin, a fixture load and the planned sheet importer were
# not held to any of them. Each needs something a CheckConstraint cannot see --
# another table, the current time, or the row's own previous value -- which is
# why each is a trigger. The house pattern is migration 0001
# (inventory_reject_tree_cycle) and 0002 (the append-only ledger).
#
# Backward compatible with the previous release, which is what a rollback
# leaves running: every rule below is already enforced by that release's
# serializers, so nothing it writes is refused. Existing rows are not examined
# either -- a trigger fires on writes, so a database that already holds a row
# these rules would refuse keeps it until somebody writes it again.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0007_label_label_code_is_crockford_base32"),
    ]

    operations = [
        # ------------------------------------------------------------------
        # "A volunteer the list still offers." One rule, three columns: the
        # actor of a transaction, the holder of a custody location, and the
        # survivor a merge points at. Generic over the column, the way
        # inventory_reject_tree_cycle is generic over the table, because three
        # copies of it would be three chances to fix two of them.
        #
        # Checked when the column is written, not continuously, so an ordinary
        # UPDATE that leaves the column alone -- renaming a shelf, retiring an
        # item -- is not an occasion to re-litigate a choice made earlier.
        #
        # That leaves two gaps the API closes rather than the database:
        # withdrawing somebody who already holds a custody location, and
        # bringing such a location back once its holder has been withdrawn.
        # Both are in VolunteerDetailSerializer.validate and
        # LocationSerializer.validate; neither is a value this can see from
        # the row being written.
        # ------------------------------------------------------------------
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION inventory_require_selectable_volunteer()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                column_name text := TG_ARGV[0];
                chosen text;
                selectable boolean;
            BEGIN
                chosen := to_jsonb(NEW) ->> column_name;
                IF chosen IS NULL THEN
                    RETURN NEW;
                END IF;
                IF TG_OP = 'UPDATE' AND chosen IS NOT DISTINCT FROM (to_jsonb(OLD) ->> column_name) THEN
                    RETURN NEW;
                END IF;

                SELECT v.merged_into_id IS NULL AND v.active
                  INTO selectable
                  FROM inventory_volunteer v
                 WHERE v.id = chosen::bigint;

                -- IS FALSE, not NOT: no row at all leaves this NULL, and that
                -- is the foreign key's complaint to make, not this one's.
                IF selectable IS FALSE THEN
                    RAISE EXCEPTION
                        '%.% names a volunteer the list no longer offers: '
                        'volunteer % has been merged or retired',
                        TG_TABLE_NAME, column_name, chosen
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END;
            $$;

            CREATE TRIGGER stock_transaction_actor_selectable
                BEFORE INSERT ON inventory_stocktransaction
                FOR EACH ROW EXECUTE FUNCTION inventory_require_selectable_volunteer('actor_id');

            CREATE TRIGGER location_held_by_selectable
                BEFORE INSERT OR UPDATE ON inventory_location
                FOR EACH ROW EXECUTE FUNCTION inventory_require_selectable_volunteer('held_by_id');

            CREATE TRIGGER volunteer_merged_into_selectable
                BEFORE INSERT OR UPDATE ON inventory_volunteer
                FOR EACH ROW EXECUTE FUNCTION inventory_require_selectable_volunteer('merged_into_id');
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS volunteer_merged_into_selectable ON inventory_volunteer;
            DROP TRIGGER IF EXISTS location_held_by_selectable ON inventory_location;
            DROP TRIGGER IF EXISTS stock_transaction_actor_selectable ON inventory_stocktransaction;
            DROP FUNCTION IF EXISTS inventory_require_selectable_volunteer();
            """,
        ),
        # ------------------------------------------------------------------
        # Nothing happened in the future. Two columns, one rule.
        #
        # clock_timestamp() rather than now(): now() is the transaction's start
        # time, and a server writing a moment it computed itself would be
        # comparing its own clock against a reading taken before the request
        # began. The five minutes on top are the same allowance the API gives a
        # client's clock (CLOCK_SKEW in serializers.py) -- the database must
        # not refuse what the API accepted a moment earlier, and this is far
        # below the scale of what it is here to catch: a phone whose clock is
        # set to next year.
        # ------------------------------------------------------------------
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION inventory_reject_future_timestamp()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                column_name text := TG_ARGV[0];
                claimed text;
            BEGIN
                claimed := to_jsonb(NEW) ->> column_name;
                IF claimed IS NULL THEN
                    RETURN NEW;
                END IF;
                IF TG_OP = 'UPDATE' AND claimed IS NOT DISTINCT FROM (to_jsonb(OLD) ->> column_name) THEN
                    RETURN NEW;
                END IF;
                IF claimed::timestamptz > clock_timestamp() + interval '5 minutes' THEN
                    RAISE EXCEPTION
                        '%.% is in the future: % has not happened yet',
                        TG_TABLE_NAME, column_name, claimed
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END;
            $$;

            CREATE TRIGGER stock_transaction_occurred_at_not_in_the_future
                BEFORE INSERT ON inventory_stocktransaction
                FOR EACH ROW EXECUTE FUNCTION inventory_reject_future_timestamp('occurred_at');

            CREATE TRIGGER label_revoked_at_not_in_the_future
                BEFORE INSERT OR UPDATE ON inventory_label
                FOR EACH ROW EXECUTE FUNCTION inventory_reject_future_timestamp('revoked_at');
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS label_revoked_at_not_in_the_future ON inventory_label;
            DROP TRIGGER IF EXISTS stock_transaction_occurred_at_not_in_the_future ON inventory_stocktransaction;
            DROP FUNCTION IF EXISTS inventory_reject_future_timestamp();
            """,
        ),
        # ------------------------------------------------------------------
        # A movement has the shape its transaction's kind says it has. Decision
        # 0011 section 6 is the argument; KIND_SIDES in views.py is the same
        # table in Python, and test_ledger.py walks that table against this
        # trigger so the two cannot drift apart unnoticed.
        #
        # A receipt that carries a from_location drains a warehouse nobody
        # shipped from, and the ledger is append-only, so it stands until
        # somebody works out what happened and compensates it.
        #
        # INSERT only: an UPDATE of either table is already refused outright by
        # the append-only triggers in 0002, so a movement's kind is decided
        # once and a second check would be dead code.
        #
        # Adjustments and counts are absent on purpose: a count reconciles the
        # shelf against the system and may push stock in either direction, so
        # neither side is required and neither is forbidden.
        # ------------------------------------------------------------------
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION inventory_movement_matches_kind()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                kind text;
            BEGIN
                SELECT t.kind INTO kind
                  FROM inventory_stocktransaction t
                 WHERE t.id = NEW.transaction_id;

                -- Refused rather than waved through. The foreign key is
                -- deferrable, so a writer that does not go through the API --
                -- loaddata defers constraint checks for the whole load -- can
                -- insert a movement before its transaction exists. Leaving
                -- `kind` NULL here would make every test below false and the
                -- row would be accepted with any shape at all, which is the
                -- one thing this trigger exists to stop.
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'a movement''s transaction must exist before it does, '
                        'because its kind decides which sides the movement may carry'
                        USING ERRCODE = 'check_violation';
                END IF;

                IF kind IN ('checkout', 'consumption', 'transfer') AND NEW.from_location_id IS NULL THEN
                    RAISE EXCEPTION
                        'a % takes stock out of somewhere, so its movements have a from_location', kind
                        USING ERRCODE = 'check_violation';
                END IF;
                IF kind IN ('checkin', 'receipt', 'transfer') AND NEW.to_location_id IS NULL THEN
                    RAISE EXCEPTION
                        'a % brings stock somewhere, so its movements have a to_location', kind
                        USING ERRCODE = 'check_violation';
                END IF;
                IF kind = 'receipt' AND NEW.from_location_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'a receipt brings stock in from outside, so its movements leave nowhere'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF kind = 'consumption' AND NEW.to_location_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'stock used at a job does not arrive anywhere, so its movements have no to_location'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END;
            $$;

            CREATE TRIGGER stock_movement_matches_kind
                BEFORE INSERT ON inventory_stockmovement
                FOR EACH ROW EXECUTE FUNCTION inventory_movement_matches_kind();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS stock_movement_matches_kind ON inventory_stockmovement;
            DROP FUNCTION IF EXISTS inventory_movement_matches_kind();
            """,
        ),
        # ------------------------------------------------------------------
        # A printed code is not the database's to change either. The code is on
        # a sticker on a shelf; changing it 404s that sticker for the life of
        # the object carrying it, and the sticker cannot be reprinted by the
        # row that just stopped matching it. A reprint is a new label and a
        # revocation of this one.
        #
        # Only the code is frozen, not the row: what a label points at, how
        # much one scan of it means, and whether it is revoked all stay
        # editable, which is what makes a correction cheaper than a reprint.
        # ------------------------------------------------------------------
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION inventory_label_code_is_printed()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.code IS DISTINCT FROM OLD.code THEN
                    RAISE EXCEPTION
                        'a label''s code is printed on it and cannot be changed: '
                        'revoke % and print another',
                        OLD.code
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END;
            $$;

            CREATE TRIGGER label_code_is_printed
                BEFORE UPDATE ON inventory_label
                FOR EACH ROW EXECUTE FUNCTION inventory_label_code_is_printed();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS label_code_is_printed ON inventory_label;
            DROP FUNCTION IF EXISTS inventory_label_code_is_printed();
            """,
        ),
    ]
