TRUNCATE sessions;

ALTER TABLE sessions
    DROP COLUMN discord_id,
    ADD COLUMN member_id uuid NOT NULL,
    ADD COLUMN display_name text NOT NULL,
    ADD COLUMN managed_group_ids uuid[] NOT NULL DEFAULT '{}';
