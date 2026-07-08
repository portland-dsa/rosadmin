ALTER TABLE members
    DROP COLUMN alternate_email;

ALTER TABLE body_memberships
    DROP COLUMN added_by,
    DROP COLUMN manually_added_at;

ALTER TABLE leadership_bodies
    DROP COLUMN leader_google_group_email,
    DROP COLUMN member_google_group_email;
