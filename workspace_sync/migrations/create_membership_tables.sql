----
--
-- Grant for membership_sync schema db objects.
--
----

GRANT pg_read_all_data TO ${MEMBER_SYNC_USER}
GRANT pg_write_all_data TO ${MEMBER_SYNC_USER}

CREATE TYPE preferred_phone AS ENUM ('HOME', 'MOBILE');
CREATE TYPE group_type AS ENUM ('WORKING_GROUP', 'CAUCAS', );
CREATE TYPE leadership_role AS ENUM ('COCHAIR', '')



CREATE TABLE IF NOT EXISTS members
(
    member_id               BIGINT             PRIMARY KEY
  , email                   TEXT               UNIQUE
  , alt_email               TEXT
  , first_name              TEXT               NOT NULL
  , middle_name             TEXT
  , last_name               TEXT               NOT NULL
  , full_name               TEXT
  , best_phone              preferred_phone    NOT NULL
  , phone_number            TEXT               NOT NULL
  , active_member           BOOLEAN            NOT NULL
  , discord_username        TEXT
  , discord_user_id         BIGINT 
  , date_joined             DATE NOT NULL

);

CREATE TABLE IF NOT EXISTS groups
(
    group_id                BIGINT             PRIMARY KEY
  , group_name              TEXT               UNIQUE
  , group_type              group_type         NOT NULL

);

CREATE TABLE IF NOT EXISTS group_memberships
(
    group_membership_id     BIGINT          PRIMARY KEY
  , member_id               BIGINT          NOT NULL REFERENCES members
  , group_id                BIGINT          NOT NULL REFERENCES groups

    -- Synthetic key table
  , UNIQUE(member_id, group_id)
);

CREATE TABLE IF NOT EXISTS group_leaders
(
    group_leader_id        BIGINT           PRIMARY KEY
  , member_id              BIGINT           NOT NULL REFERENCES members
  , group_id               BIGINT           NOT NULL REFERENCES groups
  , leadership_role        leadership_role  NOT NULL

    -- Synthetic key table
  , UNIQUE(member_id, group_id, leadership_role)
);