DELETE FROM google_email_status WHERE status = 'accepted';
ALTER TABLE google_email_status DROP CONSTRAINT google_email_status_status_check;
ALTER TABLE google_email_status ADD CONSTRAINT unmirrorable_addresses_reason_check
    CHECK (status IN ('no_google_account', 'address_not_found'));
ALTER TABLE google_email_status
    RENAME CONSTRAINT google_email_status_address_check
    TO unmirrorable_addresses_address_check;
ALTER INDEX google_email_status_pkey RENAME TO unmirrorable_addresses_pkey;
ALTER TABLE google_email_status RENAME COLUMN status TO reason;
ALTER TABLE google_email_status RENAME TO unmirrorable_addresses;
