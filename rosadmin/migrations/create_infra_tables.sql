-- The infrastructure tables the service leans on, keeping all transient state in
-- one store: server-side sessions, the single-use jti
-- replay cache, windowed rate-limit counters, and the append-only audit log. A
-- sibling to the domain migration with no cross-references, so apply order is
-- immaterial.

CREATE TABLE sessions (
    token_hash        bytea PRIMARY KEY,
    member_id         uuid NOT NULL,
    display_name      text NOT NULL,
    managed_group_ids uuid[] NOT NULL DEFAULT '{}',
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_seen_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE jti_replay (
    jti         text PRIMARY KEY,
    seen_at     timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);

CREATE TABLE rate_limit_counters (
    bucket        text NOT NULL,
    window_start  timestamptz NOT NULL,
    count         integer NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, window_start)
);

CREATE TABLE audit_log (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_hmac    text NOT NULL,
    subject_hmac  text,
    action        text NOT NULL,
    detail        jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now()
);
