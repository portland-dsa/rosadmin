--
-- Basic role for read-write access to membership data.
--

DO
$$
BEGIN
    IF NOT EXISTS
        (
            SELECT * FROM pg_roles WHERE rolname = 'member_sync_role'
        )
    THEN
        CREATE ROLE member_sync_role;
    END IF;
END
$$
;

----
--
-- Immediately remove all permissions to the public schema on this DB.
-- Users granted this role are only meant to read from the specific membership schema.
--
----

REVOKE ALL ON DATABASE ${DB_NAME} FROM member_sync_role;

GRANT CONNECT ON DATABASE ${DB_NAME} TO member_sync_role;

--
-- 
--

DO
$$
BEGIN
    IF NOT EXISTS
        (
            SELECT * FROM pg_user WHERE usename = '${MEMBER_SYNC_USER}'
        )
    THEN
        CREATE USER ${MEMBER_SYNC_USER} WITH PASSWORD '${MEMBER_SYNC_USER_PASS}';
    END IF;
END
$$
;

----
--
-- Make sure this user doesn't accidentally inherit any unwanted permissions.
--
----

REVOKE ALL ON DATABASE ${DB_NAME} FROM ${MEMBER_SYNC_USER};

GRANT member_sync_role TO ${MEMBER_SYNC_USER};
