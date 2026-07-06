# Database migrations

Schema for the rosadmin database, applied with yoyo-migrations. Each `.sql` file
is one forward migration with an optional `.rollback.sql` companion.

- `create_membership_tables.sql` - the domain tables: members, the leadership
  bodies they belong to, and their memberships.
- `create_infra_tables.sql` - the infrastructure tables the service leans on:
  sessions, the jti replay cache, rate-limit counters, and the audit log.
- `grant_app_role.sql` - the runtime role's least-privilege grants, to the
  `rosadmin_app` group role.
- `reshape_sessions.sql` - reshapes `sessions` to carry the authenticated
  Discord identity instead of the persona-era member/display-name/managed-group
  columns.

Roles are not here: they are cluster-global while migrations run per-database, so
the roles themselves (the group `rosadmin_app`, each stage's login role, and the
migration role) are created during provisioning. Grants, however, do live here.
They target the cluster-global group role `rosadmin_app`, which each stage's login
role joins, so the grant is stage-agnostic - and that is what lets it sit with the
versioned schema it protects rather than in a separate file substituted per
environment.

Later migrations that build on these declare `-- depends: <migration-id>` so
yoyo applies them after their prerequisites regardless of filename.
