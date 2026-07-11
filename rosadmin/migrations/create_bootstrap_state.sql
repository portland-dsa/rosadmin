-- depends: grant_app_role

-- A one-row table of "have we run bootstrap X yet" markers. Named by concern,
-- not a bare `bootstrapped`, so a future bootstrap is a sibling column and
-- re-opening one never disturbs another. The `only_row` primary key with its
-- CHECK keeps the table to a single row.
CREATE TABLE bootstrap_state (
    only_row boolean PRIMARY KEY DEFAULT true CHECK (only_row),
    bootstrapped_group_provisioning boolean NOT NULL DEFAULT false
);
INSERT INTO bootstrap_state (only_row) VALUES (true);

-- An address backs exactly one body: the sweep keys on it, so a shared address
-- is corruption. The `-leaders`/`-editors` suffix means a leader address and an
-- editor address can never be equal, so one UNIQUE per column suffices.
ALTER TABLE leadership_bodies
    ADD CONSTRAINT leader_group_email_unique UNIQUE (leader_google_group_email),
    ADD CONSTRAINT member_group_email_unique UNIQUE (member_google_group_email);

-- The app role reads and flips the marker; it never deletes it.
GRANT SELECT, UPDATE ON bootstrap_state TO rosadmin_app;
