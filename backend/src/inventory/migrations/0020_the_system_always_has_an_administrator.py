# Hand-written: a trigger, not model state. See
# docs/decisions/0016-invariants-for-every-writer.md for why an invariant that
# must hold whichever client wrote the row belongs in the database, and
# migration 0008 for the house pattern this follows.
#
# WHY THIS ONE IS IN THE DATABASE, specifically. The writers that can remove an
# administrator are the Django admin, a shell, a fixture load, `manage.py`
# commands and any future API. Five, and only some of them are code this
# project reviews -- so a rule enforced in a serializer is a rule the admin
# walks past, and the admin is exactly where somebody edits a user.
#
# THE OWNER'S REQUIREMENT, from `inventory-tng-s8dk`: the account made at
# deploy time is an ordinary administrator and must be deletable like any
# other, but the system MUST NOT be left with none.
#
# DELETION IS ONE OF THREE DOORS, and this is what makes it real work rather
# than a DELETE guard. An administrator also disappears by having `is_staff`
# cleared or `is_active` set false, and demoting yourself is the likelier
# accident. All three are held.
#
# WHAT IT MUST NOT DO IS OUTRANK STRIPPING A PRIVILEGE NOBODY GRANTED. A social
# account arriving with `is_staff` in its payload is demoted by
# `SocialAccountAdapter`, and if this refused that write the account would KEEP
# what the payload claimed. So the adapter clears the flags before the row is
# written rather than after -- a system with no administrator is recoverable and
# one where a stranger holds the flag is not.
#
# IT DOES NOT BLOCK A HANDOVER. Add the incoming administrator before removing
# the outgoing one and the invariant holds at every step. What it refuses is
# the last one going, which is the state nobody can recover from through the
# application -- there would be no account able to reach the admin at all.
#
# NOT A CheckConstraint, because the rule is about the table rather than about
# a row: "at least one row satisfies this" cannot be written as a per-row
# check.

from django.db import migrations

FUNCTION = """
CREATE OR REPLACE FUNCTION inventory_require_an_administrator()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- ONLY WHEN THIS WRITE TOOK AN ADMINISTRATOR AWAY. Without this the rule
    -- becomes "every transaction touching auth_user must leave an
    -- administrator", which is a different and much larger claim: it would
    -- refuse creating an ordinary account, or deleting one, in any system that
    -- happens to have no administrator yet -- including every test that never
    -- makes one. Two hundred and forty-three of them said so at once.
    IF NOT (OLD.is_staff AND OLD.is_active) THEN
        RETURN NULL;
    END IF;

    -- Asked of the table as it will be COMMITTED, which is why the rest is so
    -- short. A deferred constraint trigger runs when the transaction ends, so
    -- there is no arithmetic about what the write was going to do: the answer
    -- is simply whether anybody is left.
    IF NOT EXISTS (SELECT 1 FROM auth_user WHERE is_staff AND is_active) THEN
        RAISE EXCEPTION
            'This would leave the system with no administrator, so nobody could reach the admin '
            'and nobody could be invited. Add another administrator in the same change, or before '
            'removing this one.'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NULL;
END;
$$;
"""

# DEFERRED TO COMMIT, and that is the whole design rather than a detail.
#
# The invariant is about the state a transaction LEAVES, not about the state
# between two of its statements. Checked per statement it refuses things that
# are plainly correct: `seed_integration_data` deletes the login and recreates
# it inside one transaction, which is a pattern with its own argument written
# above it, and an immediate trigger fails on the DELETE having never seen the
# INSERT that follows it a line later.
#
# So the rule is "no transaction may end with no administrator". That admits
# delete-and-recreate, admits swapping one administrator for another in a
# single change, and still refuses the thing it exists to refuse.
#
# A CONSTRAINT TRIGGER is what can be deferred; an ordinary one cannot. It is
# necessarily AFTER and FOR EACH ROW, which suits: raising still rolls back the
# whole transaction, and the row is only the occasion to look rather than the
# subject of the question.
TRIGGERS = """
DROP TRIGGER IF EXISTS require_an_administrator_on_change ON auth_user;
CREATE CONSTRAINT TRIGGER require_an_administrator_on_change
    AFTER UPDATE ON auth_user
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION inventory_require_an_administrator();

DROP TRIGGER IF EXISTS require_an_administrator_on_delete ON auth_user;
CREATE CONSTRAINT TRIGGER require_an_administrator_on_delete
    AFTER DELETE ON auth_user
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION inventory_require_an_administrator();
"""

UNDO = """
DROP TRIGGER IF EXISTS require_an_administrator_on_change ON auth_user;
DROP TRIGGER IF EXISTS require_an_administrator_on_delete ON auth_user;
DROP FUNCTION IF EXISTS inventory_require_an_administrator();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0019_an_index_a_prefix_search_can_use"),
        # The table this guards is `auth`'s, not this application's, so the
        # dependency is named rather than assumed: without it the trigger could
        # be created before `auth_user` exists.
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunSQL(sql=FUNCTION + TRIGGERS, reverse_sql=UNDO),
    ]
