-- Resource bounds on the runtime and migration roles, for both stages, applied as
-- the postgres superuser against the 5433 cluster. A one-time provision step,
-- re-runnable and non-destructive: ALTER ROLE ... SET is idempotent and role config
-- lives in the cluster catalog, so re-applying changes nothing.
--
-- The runtime role gets tight bounds; the migration role gets more room for DDL and
-- the first big populate transaction. idle_in_transaction_session_timeout only kills
-- an IDLE open transaction, so the long active bootstrap pull is unaffected. One
-- parameter per statement - ALTER ROLE ... SET does not chain.

ALTER ROLE rosadmin_staging_app        SET statement_timeout = '30s';
ALTER ROLE rosadmin_staging_app        SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE rosadmin_staging_app        SET lock_timeout = '10s';
ALTER ROLE rosadmin_staging_migrate    SET statement_timeout = '300s';
ALTER ROLE rosadmin_staging_migrate    SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE rosadmin_staging_migrate    SET lock_timeout = '30s';

ALTER ROLE rosadmin_production_app     SET statement_timeout = '30s';
ALTER ROLE rosadmin_production_app     SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE rosadmin_production_app     SET lock_timeout = '10s';
ALTER ROLE rosadmin_production_migrate SET statement_timeout = '300s';
ALTER ROLE rosadmin_production_migrate SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE rosadmin_production_migrate SET lock_timeout = '30s';
