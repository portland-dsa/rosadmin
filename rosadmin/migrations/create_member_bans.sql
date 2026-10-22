-- depends: grant_app_role

-- Members the chapter has banned. A ban keeps a member out of every Google group
-- the sweep fills - and so out of the Drive those groups grant - and out of the
-- leader panel. It lives in its own table rather than on `members` because the
-- roster pull rewrites a member's row from Solidarity Tech on every run, so a
-- renewal or an email change upstream can never lift it.
--
-- Bans are set and lifted by hand as the postgres role. The app may read a ban
-- and copy it onto the surviving row of an account merge, but never change or
-- remove one.

CREATE TABLE member_bans (
    member_id  uuid PRIMARY KEY REFERENCES members (id) ON DELETE CASCADE,
    banned_at  timestamptz NOT NULL DEFAULT now(),
    note       text NOT NULL CHECK (note <> '')
);

GRANT SELECT, INSERT ON member_bans TO rosadmin_app;
