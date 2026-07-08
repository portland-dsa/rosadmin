-- `grant_app_role.sql` was edited in place after it had already run on some
-- databases, narrowing its UPDATE grant on `leadership_bodies` from the whole
-- row to just the two linkage columns. yoyo never re-applies an already-applied
-- migration, so on a database that ran the earlier, broader version this
-- restates the narrowed privilege explicitly. On a database that only ever saw
-- the already-narrow `grant_app_role.sql`, the REVOKE has nothing to remove and
-- the GRANT re-states what is already there, so this is a harmless no-op there.
-- depends: grant_app_role add_mutation_columns

REVOKE UPDATE ON leadership_bodies FROM rosadmin_app;
GRANT UPDATE (leader_google_group_email, member_google_group_email)
    ON leadership_bodies TO rosadmin_app;
