ALTER TABLE leadership_bodies
    DROP CONSTRAINT IF EXISTS leader_group_email_unique,
    DROP CONSTRAINT IF EXISTS member_group_email_unique;
DROP TABLE IF EXISTS bootstrap_state;
