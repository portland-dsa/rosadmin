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
- `add_leadership_assessment.sql` - adds the leadership cross-check to `members`:
  the raw `is_chapter_leader` flag Solidarity Tech serves, the `alternate_name`
  display override, and the derived `leadership_assessment` enum that materializes
  the flag-versus-derived-bodies verdict so the login gate reads it in one lookup.
- `add_mutation_columns.sql` - the columns the group-mutation write path needs:
  the leader/member Google group emails on `leadership_bodies` (both-or-neither),
  manual-add provenance on `body_memberships` (attribution requires a timestamp),
  and `members.alternate_email` for the Solidarity Tech custom property.
- `narrow_body_update_grant.sql` - restates `rosadmin_app`'s UPDATE on
  `leadership_bodies` as column-scoped. `grant_app_role.sql` was edited in
  place after some databases had already applied it with a broader UPDATE;
  since yoyo never re-runs an applied migration, this carries the narrower
  grant forward everywhere.
- `create_unmirrorable_addresses.sql` - the addresses Google has refused to hold
  as group members, with how it refused and when. The timestamp is a retry clock:
  a refusal older than the sweep's window stops filtering, so an address that has
  since gained a Google account is offered again on its own.

Roles are not here: they are cluster-global while migrations run per-database, so
the roles themselves (the group `rosadmin_app`, each stage's login role, and the
migration role) are created during provisioning. Grants, however, do live here.
They target the cluster-global group role `rosadmin_app`, which each stage's login
role joins, so the grant is stage-agnostic - and that is what lets it sit with the
versioned schema it protects rather than in a separate file substituted per
environment.

Later migrations that build on these declare `-- depends: <migration-id>` so
yoyo applies them after their prerequisites regardless of filename.
