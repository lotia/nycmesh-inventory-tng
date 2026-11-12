-- Development's database belongs to the owner, as a CloudNativePG cluster's
-- does, so migrations can create tables in its public schema. Compose runs
-- this on a new volume only; mise run dev-database makes each checkout's
-- the owner's too (dev_database.py), and the tests make databases of their
-- own.
ALTER DATABASE inventory OWNER TO inventory_owner;
