-- The two login roles (DEPLOYMENT.md, Database roles), with development's
-- passwords, each its own name. Compose runs this on a new database volume;
-- the tests run it on their server before each run. Roles belong to the
-- whole server, so it makes them only if missing, and two runs at once may
-- both try.
DO $$
DECLARE
    role text;
BEGIN
    FOREACH role IN ARRAY ARRAY['inventory_owner', 'inventory_app'] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role) THEN
            BEGIN
                EXECUTE 'CREATE ROLE ' || quote_ident(role)
                    || ' LOGIN PASSWORD ' || quote_literal(role);
            EXCEPTION WHEN duplicate_object OR unique_violation THEN
                NULL;
            END;
        END IF;
    END LOOP;
END
$$;
