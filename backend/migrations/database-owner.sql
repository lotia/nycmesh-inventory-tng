-- Compose's first database belongs to the owner, as a CloudNativePG
-- cluster's does, so migrations can create tables in its public schema.
-- Compose's db service runs this, after roles.sql, on a new volume only
-- (compose.yaml, configs).
ALTER DATABASE inventory OWNER TO inventory_owner;
