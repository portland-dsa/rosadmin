-- depends: add_leadership_assessment

-- Google linkage: every body owns two remote groups (leaders and members carry
-- different Drive capabilities). Both-or-neither, so a body is exactly linked
-- or unlinked and the sync gate never sees a half state.
ALTER TABLE leadership_bodies
    ADD COLUMN leader_google_group_email text,
    ADD COLUMN member_google_group_email text,
    ADD CONSTRAINT linked_pair CHECK (
        (leader_google_group_email IS NULL) = (member_google_group_email IS NULL)
    );

-- Manual-add provenance: manually_added_at is the durable marker the pull and
-- the sweep honor; added_by is best-effort attribution that survives only as
-- long as the adding leader's record does, hence SET NULL and the
-- one-directional CHECK.
ALTER TABLE body_memberships
    ADD COLUMN added_by uuid REFERENCES members (id) ON DELETE SET NULL,
    ADD COLUMN manually_added_at timestamptz,
    ADD CONSTRAINT manual_provenance CHECK (
        added_by IS NULL OR manually_added_at IS NOT NULL
    );

-- The Solidarity Tech alternate-email custom property: a secondary Gmail some
-- members keep for Drive access when their primary has no Google account.
ALTER TABLE members
    ADD COLUMN alternate_email text;
