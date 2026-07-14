-- depends: grant_app_role create_bootstrap_state

CREATE TABLE unmirrorable_addresses (
    address      text PRIMARY KEY CHECK (address = lower(address)),
    reason       text NOT NULL CHECK (reason IN ('no_google_account', 'address_not_found')),
    observed_at  timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE ON unmirrorable_addresses TO rosadmin_app;

ALTER TABLE bootstrap_state
    ADD COLUMN bootstrapped_refusal_learning boolean NOT NULL DEFAULT false;
