-- Least-privilege grants for the runtime app role. They target the
-- cluster-global group role `rosadmin_app`, which each stage's login role
-- (rosadmin_staging_app, later rosadmin_production_app) is a member of and
-- inherits from. Granting to the group makes this stage-agnostic, which is what
-- lets the grants live here in the versioned schema instead of a separate file
-- substituted per environment. Applied by the migration role, which owns the
-- tables; re-runnable, so a later migration that adds a table extends this one.
-- depends: create_membership_tables create_infra_tables

GRANT SELECT, INSERT, UPDATE, DELETE ON
    members, leadership_bodies, body_memberships,
    sessions, jti_replay, rate_limit_counters
  TO rosadmin_app;

-- The audit log is append-only from the app's side: it may add rows but never
-- read, rewrite, or erase them. Reads happen out of band as the postgres role.
GRANT INSERT ON audit_log TO rosadmin_app;
