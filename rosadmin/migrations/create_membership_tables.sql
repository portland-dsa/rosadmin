-- The domain tables: members and the leadership bodies they belong to. UUID primary keys
-- minted by this system
-- The Solidarity Tech id is only as a private correlation column. Turns out a table for members
-- instead of a leader and member table is simpler than the prototype, esp with potential later extension.
-- Role is a bad enum since it's very subject to change, but standing is a good first-class enum because it's
-- fossilized.
-- The infrastructure tables live in the sibling `create_infra_tables` migration.

CREATE TYPE member_standing AS ENUM ('good_standing', 'lapsed');

CREATE TABLE members (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    st_id            bigint NOT NULL UNIQUE,
    first_name       text,
    last_name        text,
    email            text NOT NULL UNIQUE,
    discord_user_id  bigint UNIQUE,
    standing         member_standing NOT NULL
);

CREATE TABLE leadership_bodies (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL,
    body_type  text NOT NULL,
    UNIQUE (name, body_type)
);

CREATE TABLE body_memberships (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    member_id  uuid NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    body_id    uuid NOT NULL REFERENCES leadership_bodies (id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('leader', 'member')),
    UNIQUE (member_id, body_id)
);
