-- The session's subject becomes the authenticated Discord identity. 
-- The table is transient state, so
-- truncating it (logging everyone out) is acceptable and lets the new NOT NULL
-- column land with no backfill. Table-level grants survive an ALTER.
-- depends: create_infra_tables

TRUNCATE sessions;

ALTER TABLE sessions
    DROP COLUMN member_id,
    DROP COLUMN display_name,
    DROP COLUMN managed_group_ids,
    ADD COLUMN discord_id text NOT NULL;
