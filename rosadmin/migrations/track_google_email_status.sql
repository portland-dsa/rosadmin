-- depends: create_unmirrorable_addresses add_mutation_columns

-- The refusal table becomes a record of what Google last said about each address:
-- `accepted` joins the two refusals, so an address that later gains (or loses) a
-- Google account has its row overwritten rather than kept stale.
ALTER TABLE unmirrorable_addresses RENAME TO google_email_status;
ALTER TABLE google_email_status RENAME COLUMN reason TO status;
ALTER INDEX unmirrorable_addresses_pkey RENAME TO google_email_status_pkey;
ALTER TABLE google_email_status
    RENAME CONSTRAINT unmirrorable_addresses_address_check
    TO google_email_status_address_check;
ALTER TABLE google_email_status DROP CONSTRAINT unmirrorable_addresses_reason_check;
ALTER TABLE google_email_status ADD CONSTRAINT google_email_status_status_check
    CHECK (status IN ('accepted', 'no_google_account', 'address_not_found'));

-- Every good-standing member's address under the rule the sweep has mirrored by
-- until now (a gmail primary, else a gmail alternate, else the primary) is one
-- Google has held, unless it refused it. Seeding those as accepted keeps current
-- members on the address they already have.
INSERT INTO google_email_status (address, status)
SELECT DISTINCT lower(
    CASE
        WHEN lower(email) LIKE '%@gmail.com' THEN email
        WHEN lower(alternate_email) LIKE '%@gmail.com' THEN alternate_email
        ELSE email
    END
), 'accepted'
FROM members
WHERE standing = 'good_standing'
  AND lower(email) NOT LIKE '%@example.com'
ON CONFLICT (address) DO NOTHING;
