-- depends: create_membership_tables

-- The exact-email member search compares lower(email) = lower(%s), which the
-- plain-email unique index cannot serve, so each search was a sequential scan - a
-- per-request cost an unthrottled search could amplify. This functional index makes
-- the lookup an index probe.
CREATE INDEX members_email_lower_idx ON members (lower(email));
