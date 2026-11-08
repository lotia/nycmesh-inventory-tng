-- Development's database belongs to the owner, as a CloudNativePG cluster's
-- does, so migrations can create tables in its public schema. Compose runs
-- this on a new volume only; the tests make databases of their own.
ALTER DATABASE inventory OWNER TO inventory_owner;
