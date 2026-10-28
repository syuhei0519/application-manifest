-- ACL remediation v1, NOT an application schema migration. No credentials.
-- Run only against the reviewed, already-bootstrapped fresh Account database,
-- through an approved administrator session, after inspecting this transaction.
-- Does not edit history rows or reapply migration 1. Fail closed on drift.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
SELECT pg_advisory_xact_lock(7742340081);
DO $preflight$
BEGIN
  IF current_database() <> 'account' OR current_user <> 'postgres'
     OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()) <> 'postgres'
     OR (SELECT nspowner FROM pg_namespace WHERE nspname = 'account') IS DISTINCT FROM 'account_migrator'::regrole
     OR (SELECT relowner FROM pg_class WHERE oid = 'account.accounts'::regclass) <> 'account_migrator'::regrole
     OR (SELECT relowner FROM pg_class WHERE oid = 'account.schema_migrations'::regclass) <> 'account_migrator'::regrole
     OR (SELECT array_agg(version ORDER BY version) FROM account.schema_migrations) IS DISTINCT FROM ARRAY[1]
  THEN RAISE EXCEPTION 'ACL v1: database/owner/history drift'; END IF;
  IF NOT EXISTS (SELECT FROM pg_attribute WHERE attrelid = 'account.accounts'::regclass
                 AND attname = 'id' AND attidentity = 'a' AND NOT attisdropped)
     OR pg_get_serial_sequence('account.accounts', 'id') IS NULL
     OR (SELECT relowner FROM pg_class WHERE oid = pg_get_serial_sequence('account.accounts', 'id')::regclass) <> 'account_migrator'::regrole
  THEN RAISE EXCEPTION 'ACL v1: identity sequence drift'; END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname IN ('account_runtime', 'account_migrator')
             AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls OR rolinherit OR NOT rolcanlogin))
     OR EXISTS (SELECT FROM pg_auth_members WHERE member IN ('account_runtime'::regrole, 'account_migrator'::regrole))
     OR EXISTS (SELECT FROM pg_class WHERE relowner = 'account_runtime'::regrole)
     OR EXISTS (SELECT FROM pg_namespace WHERE nspowner = 'account_runtime'::regrole)
     OR EXISTS (SELECT FROM pg_database WHERE datdba = 'account_runtime'::regrole)
  THEN RAISE EXCEPTION 'ACL v1: role/ownership drift'; END IF;
  -- Per-schema REVOKE cannot cancel a global default or a PUBLIC grant.
  IF EXISTS (SELECT FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a
             WHERE d.defaclrole = 'account_migrator'::regrole
               AND d.defaclobjtype IN ('r', 'S')
               AND d.defaclnamespace IN (0, 'account'::regnamespace)
               AND (a.grantee = 0 OR (d.defaclnamespace = 0 AND a.grantee = 'account_runtime'::regrole)))
  THEN RAISE EXCEPTION 'ACL v1: global/PUBLIC default privilege drift'; END IF;
END
$preflight$;

-- Superuser-issued grants on these owner-created objects are attributed to
-- account_migrator. Use that owner explicitly, including its default ACLs.
SET LOCAL ROLE account_migrator;
REVOKE INSERT, UPDATE, DELETE ON TABLE account.schema_migrations FROM account_runtime;
GRANT SELECT ON TABLE account.schema_migrations TO account_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE account.accounts TO account_runtime;
SELECT format('REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM account_runtime',
              pg_get_serial_sequence('account.accounts', 'id')::regclass) \gexec
ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator IN SCHEMA account
  REVOKE ALL PRIVILEGES ON TABLES FROM account_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator IN SCHEMA account
  REVOKE ALL PRIVILEGES ON SEQUENCES FROM account_runtime;
RESET ROLE;

DO $postflight$
DECLARE
  privilege text;
BEGIN
  FOREACH privilege IN ARRAY ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE'] LOOP
    IF NOT has_table_privilege('account_runtime', 'account.accounts', privilege)
    THEN RAISE EXCEPTION 'ACL v1: accounts CRUD missing'; END IF;
  END LOOP;
  IF NOT has_table_privilege('account_runtime', 'account.schema_migrations', 'SELECT')
     OR has_table_privilege('account_runtime', 'account.schema_migrations', 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
     OR has_any_column_privilege('account_runtime', 'account.schema_migrations', 'INSERT,UPDATE,REFERENCES')
     OR has_table_privilege('account_runtime', 'account.accounts', 'TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
     OR has_schema_privilege('account_runtime', 'account', 'CREATE')
     OR has_schema_privilege('account_runtime', 'public', 'CREATE')
     OR has_database_privilege('account_runtime', current_database(), 'CREATE,TEMP')
     OR NOT has_schema_privilege('account_runtime', 'account', 'USAGE')
     OR EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'account' AND c.relname NOT IN ('accounts', 'schema_migrations')
                  AND CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
                    has_table_privilege('account_runtime', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                    ELSE false END)
     OR EXISTS (SELECT FROM pg_class c WHERE c.relnamespace = 'account'::regnamespace
                -- CASE prevents predicate reordering from passing an index/table
                -- OID to has_sequence_privilege (which raises an error).
                AND CASE WHEN c.relkind = 'S' THEN
                  has_sequence_privilege('account_runtime', c.oid, 'USAGE,SELECT,UPDATE')
                  ELSE false END)
     OR EXISTS (SELECT FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a
                WHERE d.defaclrole = 'account_migrator'::regrole AND d.defaclobjtype IN ('r', 'S')
                  AND d.defaclnamespace IN (0, 'account'::regnamespace)
                  AND a.grantee IN (0, 'account_runtime'::regrole))
  THEN RAISE EXCEPTION 'ACL v1: effective privileges outside reviewed scope; transaction rolled back'; END IF;
END
$postflight$;
COMMIT;
